from __future__ import annotations

import io

# Library imports
import mimetypes
import re
import time
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import google.auth
from google.api_core.exceptions import GoogleAPICallError
from google.auth.transport.requests import AuthorizedSession
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.cloud import storage

# Source code imports
from src.hook.tls import TlsPolicy
from src.logger import Logger

logger = Logger.get_logger(__name__)


class GCSError(Exception):
    """Base for every Google Cloud Storage failure raised by this module."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status that produced it, when the failure came from the API.
        """
        super().__init__(message)
        self.status_code = status_code


class GCSAuthError(GCSError):
    """Credentials were rejected (401) or lack the required IAM role (403)."""


class GCSNotFoundError(GCSError):
    """The bucket or object does not exist (404)."""


class GCSConflictError(GCSError):
    """A generation/metageneration precondition failed (412)."""


# Dispatch on the HTTP status rather than on exception class. google-api-core's hierarchy nests
# (PreconditionFailed subclasses ClientError subclasses GoogleAPICallError), so an except-chain
# has to be ordered most-specific-first -- correct but fragile, and silently broken by a later
# edit that appends a clause. A status lookup has no ordering to get wrong.
_ERROR_BY_STATUS: dict[int, type[GCSError]] = {
    401: GCSAuthError,
    403: GCSAuthError,
    404: GCSNotFoundError,
    412: GCSConflictError,
}


class GCSModule:
    """
    Module for interacting with Google Cloud Storage (GCS).
    Handles file uploads, downloads, and listing operations.

    Bucket names are per-call rather than constructor state, so one instance can span buckets
    within a project.
    """

    # Seconds. This is not hang-prevention -- unlike requests (which defaults to None), every
    # google-cloud-storage method already defaults to timeout=60, so omitting it would be safe.
    # It exists to make that 60 *reachable*: it is otherwise buried in the SDK with no knob, and
    # 60s is short for a large object. Callers raise it with GCSModule(..., timeout=300).
    DEFAULT_TIMEOUT = 60.0

    # Object names ending in "/" with zero bytes are directory placeholders created by GUI tools
    # and gsutil. GCS has no real directories, so these are ordinary objects that would otherwise
    # show up as files in a listing.
    MARKER_SUFFIX = "/"

    def __init__(self, **kwargs) -> None:
        """
        Initialize GCS module with project configuration.

        Args:
            project_id (str): GCP project ID
            timezone (str, optional): Timezone for datetime operations. Defaults to 'UTC'
            timeout (float, optional): Per-request timeout in seconds. Defaults to DEFAULT_TIMEOUT

        Raises:
            GCSError: If required parameters are missing
            google.auth.exceptions.DefaultCredentialsError: If no credentials can be resolved
        """
        started = time.monotonic()

        self.project_id = kwargs.get("project_id")

        if not self.project_id:
            message = "Missing required arguments: project_id."
            logger.error("gcs.config.invalid", reason=message)
            raise GCSError(message)

        timezone = kwargs.get("timezone", "UTC")
        self.timezone = ZoneInfo(timezone)

        self._timeout = kwargs.get("timeout", self.DEFAULT_TIMEOUT)

        # storage.Client resolves credentials itself when _http is absent, but we need them
        # first in order to build the session, so resolve them explicitly with the SDK's own
        # scopes. This is also the module's fail-fast check: google.auth.default() raises
        # DefaultCredentialsError immediately when ADC is unconfigured, which is why there is no
        # separate connection probe here (a list_buckets call would demand a storage.buckets.list
        # permission that a narrowly-scoped service account will not have).
        credentials, _ = google.auth.default(scopes=list(storage.Client.SCOPE))

        # AuthorizedSession keeps a *separate* internal session for token refresh, so mounting
        # the adapter below pins the storage calls but not the refresh leg; auth_request pins
        # that one too.
        tls = TlsPolicy()
        self._session = AuthorizedSession(
            credentials, auth_request=GoogleAuthRequest(session=tls.session())
        )
        self._session.mount("https://", tls.adapter())

        # _http must be an *authorized* session. google.cloud.client.Client._http only builds an
        # AuthorizedSession when its backing field is None, and this kwarg is what populates that
        # field -- so passing a bare requests.Session here sends every request with no
        # Authorization header and turns the whole module into a 401 generator. credentials= is
        # required alongside it because the client validates its universe domain against them.
        self.client = storage.Client(
            project=self.project_id, credentials=credentials, _http=self._session
        )

        logger.debug(
            "gcs.config.resolved",
            project_id=self.project_id,
            timezone=timezone,
            timeout=self._timeout,
        )

        logger.info(
            "gcs.connected",
            project_id=self.project_id,
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
        return round((time.monotonic() - started) * 1000, 1)

    @staticmethod
    def _normalize_blob_path(path: str | Path) -> str:
        """
        Normalize a caller-supplied path into a GCS object name.

        A GCS object name must not begin with ``/``: ``bucket.blob("/a/b.txt")`` addresses an
        object literally named ``/a/b.txt``, which is a different object from ``a/b.txt`` and is
        invisible to a listing of ``a/``. This module sits next to :class:`SharePointModule`,
        whose every path *does* start with ``/``, so callers bridging the two will pass one.

        Also accepts a ``Path`` (whose Windows separators become ``/``) and strips a leading
        ``gs://<bucket>/`` so a URI copied from the console can be passed through unchanged.

        Args:
            path: The path to normalize.

        Returns:
            str: A bare object name with no leading separator.
        """
        text = str(path).replace("\\", "/")

        if text.startswith("gs://"):
            # Drop the scheme and the bucket segment; what remains is the object name.
            text = text[len("gs://") :].split("/", 1)[1] if "/" in text[len("gs://") :] else ""

        return text.lstrip("/")

    @staticmethod
    def _normalize_prefix(prefix: str | Path) -> str:
        """
        Normalize a listing prefix.

        Like :meth:`_normalize_blob_path`, but a non-empty prefix is given a trailing ``/`` so it
        addresses a folder rather than every sibling sharing its name as a substring: without it,
        a prefix of ``report`` also matches ``reports_archive/x.txt``.

        Args:
            prefix: The prefix to normalize.

        Returns:
            str: Normalized prefix, empty string for the bucket root.
        """
        text = GCSModule._normalize_blob_path(prefix)
        if text and not text.endswith("/"):
            text = f"{text}/"
        return text

    @contextmanager
    def _translate_errors(self, context: str, **fields: Any) -> Generator[None]:
        """
        Map google-api-core exceptions onto this module's exception hierarchy.

        This is the single ERROR site for API faults -- outer handlers must not log again, or
        every fault produces two records. ``google.cloud.exceptions.GoogleCloudError`` is a
        subclass of ``GoogleAPICallError``, so one clause covers the SDK's whole error surface.

        Args:
            context (str): What was being attempted, for the message.
            **fields: Structured fields to attach to the error record.

        Yields:
            None

        Raises:
            GCSAuthError: On 401 or 403.
            GCSNotFoundError: On 404.
            GCSConflictError: On 412.
            GCSError: On any other API failure.
        """
        try:
            yield
        except GoogleAPICallError as exc:
            status = exc.code
            message = f"{context} failed: {status} {exc.message}"
            logger.error("gcs.request.failed", context=context, status=status, **fields)
            raise _ERROR_BY_STATUS.get(status, GCSError)(message, status_code=status) from exc

    def _bucket(self, bucket_name: str) -> storage.Bucket:
        """
        Get a bucket handle.

        Purely local -- no request is issued, so a nonexistent bucket surfaces on first use
        rather than here.

        Args:
            bucket_name (str): The name of the GCS bucket.

        Returns:
            storage.Bucket: The bucket handle.

        Raises:
            GCSError: If no bucket name was supplied.
        """
        if not bucket_name:
            message = "Missing required argument: bucket_name."
            logger.error("gcs.config.invalid", reason=message)
            raise GCSError(message)
        return self.client.bucket(bucket_name)

    def upload_file(
        self,
        bucket_name: str,
        upload_path: str | Path,
        content: bytes,
        *,
        mime_type: str | None = None,
        if_generation_match: int | None = None,
    ) -> None:
        """
        Upload a file to GCS.

        Args:
            bucket_name (str): The name of the GCS bucket
            upload_path (str | Path): The path in the bucket where the file will be uploaded
            content (bytes): The content of the file to upload
            mime_type (str | None): Optional MIME type. Guessed from the path when omitted
            if_generation_match (int | None): Optimistic-concurrency precondition. ``0`` requires
                that the object not already exist; any other value requires that generation.
                A mismatch raises :class:`GCSConflictError`

        Raises:
            GCSConflictError: If ``if_generation_match`` is set and does not match
            GCSError: If the upload fails for any other reason
        """
        started = time.monotonic()
        blob_path = self._normalize_blob_path(upload_path)
        blob = self._bucket(bucket_name).blob(blob_path)

        if mime_type is None:
            mime_type, _ = mimetypes.guess_type(blob_path)
            if mime_type is None:
                mime_type = "application/octet-stream"

        logger.debug(
            "gcs.upload.starting",
            bucket=bucket_name,
            path=blob_path,
            bytes=len(content),
            mime_type=mime_type,
        )

        with self._translate_errors(
            f"Uploading gs://{bucket_name}/{blob_path}", bucket=bucket_name, path=blob_path
        ):
            blob.upload_from_string(
                content,
                content_type=mime_type,
                if_generation_match=if_generation_match,
                timeout=self._timeout,
            )

        logger.info(
            "gcs.upload.completed",
            bucket=bucket_name,
            path=blob_path,
            bytes=len(content),
            mime_type=mime_type,
            elapsed_ms=self._elapsed_ms(started),
        )
    
    def upload_stream_file(self, bucket_name: str, upload_path: str | Path, stream: io.BytesIO, *, mime_type: str | None = None, if_generation_match: int | None = None) -> None:
        """
        Upload a file to GCS from a stream.

        Args:
            bucket_name (str): The name of the GCS bucket
            upload_path (str | Path): The path in the bucket where the file will be uploaded
            stream (io.BytesIO): The stream of the file to upload
            mime_type (str | None): Optional MIME type. Guessed from the path when omitted
            if_generation_match (int | None): Optimistic-concurrency precondition. ``0`` requires
                that the object not already exist; any other value requires that generation.
                A mismatch raises :class:`GCSConflictError`

        Raises:
            GCSConflictError: If ``if_generation_match`` is set and does not match
            GCSError: If the upload fails for any other reason
        """
        started = time.monotonic()
        blob_path = self._normalize_blob_path(upload_path)
        blob = self._bucket(bucket_name).blob(blob_path)

        if mime_type is None:
            mime_type, _ = mimetypes.guess_type(blob_path)
            if mime_type is None:
                mime_type = "application/octet-stream"

        logger.debug(
            "gcs.upload.starting",
            bucket=bucket_name,
            path=blob_path,
            mime_type=mime_type,
        )

        with self._translate_errors(
            f"Uploading gs://{bucket_name}/{blob_path}", bucket=bucket_name, path=blob_path
        ):
            # rewind=True: the usual caller hands over a BytesIO it just finished writing, whose
            # position is at the end -- uploading from there would silently store zero bytes.
            blob.upload_from_file(
                stream,
                rewind=True,
                content_type=mime_type,
                if_generation_match=if_generation_match,
                timeout=self._timeout,
            )

        logger.info(
            "gcs.upload.completed",
            bucket=bucket_name,
            path=blob_path,
            bytes=blob.size,
            mime_type=mime_type,
            elapsed_ms=self._elapsed_ms(started),
        )

    def download_file(self, bucket_name: str, file_path: str | Path) -> bytes:
        """
        Download a file from GCS.

        Args:
            bucket_name (str): The name of the GCS bucket
            file_path (str | Path): The path of the file in the bucket

        Returns:
            bytes: The content of the downloaded file

        Raises:
            GCSNotFoundError: If the object does not exist
            GCSError: If the download fails for any other reason
        """
        started = time.monotonic()
        blob_path = self._normalize_blob_path(file_path)
        blob = self._bucket(bucket_name).blob(blob_path)

        logger.debug("gcs.download.starting", bucket=bucket_name, path=blob_path)

        # No exists() pre-check: it costs a second round trip to answer a question the download
        # itself already answers, and the object can vanish between the two calls anyway.
        with self._translate_errors(
            f"Downloading gs://{bucket_name}/{blob_path}", bucket=bucket_name, path=blob_path
        ):
            content = blob.download_as_bytes(timeout=self._timeout)

        logger.info(
            "gcs.download.completed",
            bucket=bucket_name,
            path=blob_path,
            bytes=len(content),
            elapsed_ms=self._elapsed_ms(started),
        )
        return content

    def _iter_blobs(
        self, bucket_name: str, prefix: str, delimiter: str | None
    ) -> tuple[list[storage.Blob], tuple[str, ...]]:
        """
        List blobs under a prefix, returning both the objects and the common prefixes.

        The iterator must be fully consumed before ``.prefixes`` is read: the SDK fills that set
        page by page as the responses arrive, so reading it off a freshly constructed iterator
        always yields an empty set.

        Args:
            bucket_name (str): The name of the GCS bucket
            prefix (str): Normalized prefix to list under
            delimiter (str | None): ``"/"`` to list one level, ``None`` to list everything

        Returns:
            tuple: (blobs, common prefixes)

        Raises:
            GCSError: If the listing fails
        """
        with self._translate_errors(
            f"Listing gs://{bucket_name}/{prefix}", bucket=bucket_name, prefix=prefix
        ):
            iterator = self._bucket(bucket_name).list_blobs(
                prefix=prefix, delimiter=delimiter, timeout=self._timeout
            )
            blobs = list(iterator)
            prefixes = tuple(iterator.prefixes)

        logger.debug(
            "gcs.listing.fetched",
            bucket=bucket_name,
            prefix=prefix,
            delimiter=delimiter,
            blobs=len(blobs),
            prefixes=len(prefixes),
        )
        return blobs, prefixes

    def list_files(
        self,
        bucket_name: str,
        prefix: str | Path = "",
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[str]:
        """
        List files in a GCS bucket under an optional prefix.

        Args:
            bucket_name (str): The name of the GCS bucket
            prefix (str | Path): Only list objects under this prefix. Defaults to the bucket root
            recursive (bool): Descend into nested prefixes. When False only immediate children
                are returned
            pattern (str, optional): Regex matched against each object's full name

        Returns:
            list[str]: Names of the matching objects

        Raises:
            GCSError: If the listing fails
        """
        started = time.monotonic()
        normalized = self._normalize_prefix(prefix)

        # GCS is flat: a delimiter is what makes a listing look hierarchical. Without one the
        # response already contains the whole subtree, so recursion needs no extra request.
        delimiter = None if recursive else "/"
        blobs, _ = self._iter_blobs(bucket_name, normalized, delimiter)

        # Compiled once, not per item, and matched against the full object name -- a pattern like
        # r".*/\d{8}/" carries a separator and can only ever match a path. `search`, not `match`,
        # so an unanchored pattern behaves the way callers expect.
        regex = re.compile(pattern) if pattern else None

        paths = [
            blob.name
            for blob in blobs
            if not blob.name.endswith(self.MARKER_SUFFIX)
            and (regex is None or regex.search(blob.name))
        ]

        logger.info(
            "gcs.listing.completed",
            bucket=bucket_name,
            kind="files",
            prefix=normalized,
            recursive=recursive,
            pattern=pattern,
            items=len(paths),
            elapsed_ms=self._elapsed_ms(started),
        )
        return paths

    def list_dirs(
        self,
        bucket_name: str,
        prefix: str | Path = "",
        recursive: bool = False,
        pattern: str | None = None,
    ) -> list[str]:
        """
        List directories (common prefixes) in a GCS bucket.

        Args:
            bucket_name (str): The name of the GCS bucket
            prefix (str | Path): Only list directories under this prefix
            recursive (bool): Return every nested directory, not just immediate children
            pattern (str, optional): Regex matched against each directory's full path

        Returns:
            list[str]: Directory paths, each with a trailing ``/``

        Raises:
            GCSError: If the listing fails
        """
        started = time.monotonic()
        normalized = self._normalize_prefix(prefix)

        if recursive:
            # One unfiltered listing, then every intermediate prefix is derived from the object
            # names. The alternative -- recursing with delimiter="/" -- costs one request per
            # subtree to rediscover what this single response already contains.
            blobs, _ = self._iter_blobs(bucket_name, normalized, None)
            directories = self._derive_prefixes(blobs, normalized)
        else:
            _, prefixes = self._iter_blobs(bucket_name, normalized, "/")
            directories = sorted(prefixes)

        regex = re.compile(pattern) if pattern else None
        paths = [path for path in directories if regex is None or regex.search(path)]

        logger.info(
            "gcs.listing.completed",
            bucket=bucket_name,
            kind="dirs",
            prefix=normalized,
            recursive=recursive,
            pattern=pattern,
            items=len(paths),
            elapsed_ms=self._elapsed_ms(started),
        )
        return paths

    @staticmethod
    def _derive_prefixes(blobs: list[storage.Blob], base: str) -> list[str]:
        """
        Reconstruct every directory path implied by a flat list of object names.

        A directory in GCS exists only as a shared substring of object names, so
        ``a/b/c/file.txt`` implies ``a/``, ``a/b/`` and ``a/b/c/``. Marker objects are included
        by the same rule: a 0-byte ``a/b/`` is itself the evidence that ``a/b/`` exists.

        Args:
            blobs: The objects to derive directories from
            base (str): The prefix the listing was rooted at; not itself returned

        Returns:
            list[str]: Sorted directory paths, each with a trailing ``/``
        """
        directories: set[str] = set()

        for blob in blobs:
            # rsplit off the final segment: for a marker ("a/b/") that segment is empty, which
            # correctly leaves the marker's own directory in place.
            parent = blob.name.rsplit("/", 1)[0] if "/" in blob.name else ""
            while parent and f"{parent}/" != base:
                directories.add(f"{parent}/")
                parent = parent.rsplit("/", 1)[0] if "/" in parent else ""

        return sorted(directories)

    def move_file(
        self, bucket_name: str, source_path: str | Path, destination_path: str | Path
    ) -> None:
        """
        Move (rename) a file within the GCS bucket.

        Copy-then-delete, which is **not atomic**: GCS has no rename primitive for a
        flat-namespace bucket. The delete runs only after the copy returns, so a failed copy
        leaves the source untouched; a failure between the two leaves the object at both paths,
        which is the safe direction to fail in.

        Args:
            bucket_name (str): The name of the GCS bucket
            source_path (str | Path): The current path of the file
            destination_path (str | Path): The new path for the file

        Raises:
            GCSNotFoundError: If the source object does not exist
            GCSError: If the move fails for any other reason
        """
        started = time.monotonic()
        source = self._normalize_blob_path(source_path)
        destination = self._normalize_blob_path(destination_path)
        bucket = self._bucket(bucket_name)

        logger.debug("gcs.move.starting", bucket=bucket_name, source=source, destination=destination)

        with self._translate_errors(
            f"Moving gs://{bucket_name}/{source} to {destination}",
            bucket=bucket_name,
            source=source,
            destination=destination,
        ):
            source_blob = bucket.blob(source)
            bucket.copy_blob(source_blob, bucket, destination, timeout=self._timeout)
            source_blob.delete(timeout=self._timeout)

        logger.info(
            "gcs.move.completed",
            bucket=bucket_name,
            source=source,
            destination=destination,
            elapsed_ms=self._elapsed_ms(started),
        )

    def delete_file(self, bucket_name: str, file_path: str | Path) -> None:
        """
        Delete a file from the GCS bucket.

        Args:
            bucket_name (str): The name of the GCS bucket
            file_path (str | Path): The path of the file to delete

        Raises:
            GCSNotFoundError: If the object does not exist
            GCSError: If the delete fails for any other reason
        """
        blob_path = self._normalize_blob_path(file_path)

        with self._translate_errors(
            f"Deleting gs://{bucket_name}/{blob_path}", bucket=bucket_name, path=blob_path
        ):
            self._bucket(bucket_name).blob(blob_path).delete(timeout=self._timeout)

        logger.info("gcs.delete.completed", bucket=bucket_name, path=blob_path)

    def copy_file(
        self, bucket_name: str, source_path: str | Path, destination_path: str | Path
    ) -> None:
        """
        Copy a file within the GCS bucket.

        Args:
            bucket_name (str): The name of the GCS bucket
            source_path (str | Path): The path of the source file
            destination_path (str | Path): The path to copy the file to

        Raises:
            GCSNotFoundError: If the source object does not exist
            GCSError: If the copy fails for any other reason
        """
        started = time.monotonic()
        source = self._normalize_blob_path(source_path)
        destination = self._normalize_blob_path(destination_path)
        bucket = self._bucket(bucket_name)

        logger.debug("gcs.copy.starting", bucket=bucket_name, source=source, destination=destination)

        with self._translate_errors(
            f"Copying gs://{bucket_name}/{source} to {destination}",
            bucket=bucket_name,
            source=source,
            destination=destination,
        ):
            bucket.copy_blob(bucket.blob(source), bucket, destination, timeout=self._timeout)

        logger.info(
            "gcs.copy.completed",
            bucket=bucket_name,
            source=source,
            destination=destination,
            elapsed_ms=self._elapsed_ms(started),
        )

    def is_file_exists(self, bucket_name: str, file_path: str | Path) -> bool:
        """
        Check if a file exists in the GCS bucket.

        Only absence returns False. A 403 raises :class:`GCSAuthError` rather than reading as
        "does not exist" -- "you may not look" is not an answer to "is it there".

        Args:
            bucket_name (str): The name of the GCS bucket
            file_path (str | Path): The path of the file to check

        Returns:
            bool: True if the file exists, False otherwise

        Raises:
            GCSError: If the check fails for a reason other than absence
        """
        blob_path = self._normalize_blob_path(file_path)

        with self._translate_errors(
            f"Checking gs://{bucket_name}/{blob_path}", bucket=bucket_name, path=blob_path
        ):
            exists = self._bucket(bucket_name).blob(blob_path).exists(timeout=self._timeout)

        logger.debug("gcs.exists.checked", bucket=bucket_name, path=blob_path, exists=exists)
        return exists

    def is_dir_exists(self, bucket_name: str, dir_path: str | Path) -> bool:
        """
        Check if a "directory" (prefix) exists in the GCS bucket.

        Args:
            bucket_name (str): The name of the GCS bucket
            dir_path (str | Path): The directory path (prefix) to check

        Returns:
            bool: True if the directory exists (has at least one object), False otherwise

        Raises:
            GCSError: If the check fails
        """
        prefix = self._normalize_prefix(dir_path)

        # max_results=1 -- the question is "any?", so fetching a full listing to answer it wastes
        # a page of results and unbounded latency on a large prefix.
        with self._translate_errors(
            f"Checking gs://{bucket_name}/{prefix}", bucket=bucket_name, prefix=prefix
        ):
            iterator = self._bucket(bucket_name).list_blobs(
                prefix=prefix, max_results=1, timeout=self._timeout
            )
            exists = any(True for _ in iterator)

        logger.debug("gcs.exists.checked", bucket=bucket_name, prefix=prefix, exists=exists)
        return exists

    def cleanup_markers(self, bucket_name: str, prefix: str | Path = "") -> int:
        """
        Clean up empty directory marker objects (0-byte blobs ending with '/').
        These are often created by tools to represent directory structures.

        Both conditions are required: a non-empty object whose name ends in ``/`` is real data
        someone stored under a confusing name, not a placeholder, and deleting it would lose it.

        Args:
            bucket_name (str): The name of the GCS bucket
            prefix (str | Path): Only clean up markers under this prefix

        Returns:
            int: Number of directory markers deleted

        Raises:
            GCSError: If the listing or a delete fails
        """
        started = time.monotonic()
        normalized = self._normalize_prefix(prefix)
        blobs, _ = self._iter_blobs(bucket_name, normalized, None)

        markers = [b for b in blobs if b.name.endswith(self.MARKER_SUFFIX) and b.size == 0]

        for marker in markers:
            with self._translate_errors(
                f"Deleting marker gs://{bucket_name}/{marker.name}",
                bucket=bucket_name,
                path=marker.name,
            ):
                marker.delete(timeout=self._timeout)
            logger.debug("gcs.cleanup.deleted", bucket=bucket_name, path=marker.name)

        if markers:
            logger.warning(
                "gcs.cleanup.completed",
                bucket=bucket_name,
                prefix=normalized,
                deleted=len(markers),
                elapsed_ms=self._elapsed_ms(started),
            )
        else:
            logger.info(
                "gcs.cleanup.completed",
                bucket=bucket_name,
                prefix=normalized,
                deleted=0,
                elapsed_ms=self._elapsed_ms(started),
            )

        return len(markers)
