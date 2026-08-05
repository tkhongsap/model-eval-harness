"""Source file loader — SharePoint → GCS landing upload."""

from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import requests

from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger

logger = Logger(__name__)

_DRIVE_ROOT_PREFIX = "/drive/root:"


class SourceFileLoader:
    """Lists, filters, and uploads source files from SharePoint to GCS landing.

    Deduplication against in-flight files is the caller's responsibility
    via :meth:`filter_new` before calling :meth:`upload_to_landing`.
    """

    def __init__(self, sp_conn: SharePointModule, gcs_conn: GCSModule) -> None:
        """Initialise with injected SharePoint and GCS connections.

        Args:
            sp_conn: Authenticated SharePoint module.
            gcs_conn: Initialised GCS module pointing at the processing bucket.
        """
        self._sp = sp_conn
        self._gcs = gcs_conn

    def list_files(self, src_path: str, ext_filter: list[str]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """List source files from SharePoint, partitioned by extension.

        Args:
            src_path: SharePoint folder path to list recursively.
            ext_filter: Allowed lowercase extensions (e.g. ``['.pdf', '.jpg']``).

        Returns:
            A ``(supported, unsupported)`` tuple. Each element is a list of file entry
            dicts with ``name``, ``sp_path``, and ``mime_type`` keys; ``supported``
            entries match ``ext_filter``, ``unsupported`` entries do not.
        """
        raw_items = self._sp.list_files(src_path, recursive=True)
        supported: list[dict[str, Any]] = []
        unsupported: list[dict[str, Any]] = []
        for item in raw_items:
            if item.get("folder"):
                continue
            entry = self._build_file_entry(item)
            if entry is None:
                continue
            if Path(entry["name"]).suffix.lower() in ext_filter:
                supported.append(entry)
            else:
                unsupported.append(entry)
                logger.warning(f"Unsupported file type (will be logged REJECTED): {entry['sp_path']}")
        logger.info(f"Listed {len(supported)} supported / {len(unsupported)} unsupported file(s) at {src_path}")
        return supported, unsupported

    def list_files_union(
        self, src_paths: list[str], ext_filter: list[str]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """List every path recursively and union the results, deduped by ``sp_path`` (first wins).

        A path that fails to list (e.g. a backfill date whose folder does not exist, surfaced
        as a Graph 404 ``requests.exceptions.HTTPError``) is logged at WARNING and skipped. If
        EVERY path fails, raise ``RuntimeError`` — a systemic SharePoint outage must not
        masquerade as "no new files to process". A path classifies identically on every
        listing, so both the supported and unsupported lists are deduped against one shared
        ``seen`` set of ``sp_path``s.

        Args:
            src_paths: SharePoint folder paths to list (already date-resolved).
            ext_filter: Allowed lowercase extensions (e.g. ``['.pdf', '.jpg']``).

        Returns:
            A ``(supported, unsupported)`` tuple, each a deduplicated union of file entries
            across all listable paths.

        Raises:
            RuntimeError: When every path in ``src_paths`` fails to list.
        """
        seen: set[str] = set()
        supported_union: list[dict[str, Any]] = []
        unsupported_union: list[dict[str, Any]] = []
        failures = 0
        for path in src_paths:
            try:
                supported, unsupported = self.list_files(path, ext_filter)
            except requests.exceptions.HTTPError as exc:
                failures += 1
                logger.warning(f"Failed to list source path {path} (skipping): {exc}")
                continue
            for entry in supported:
                if entry["sp_path"] not in seen:
                    seen.add(entry["sp_path"])
                    supported_union.append(entry)
            for entry in unsupported:
                if entry["sp_path"] not in seen:
                    seen.add(entry["sp_path"])
                    unsupported_union.append(entry)
        if src_paths and failures == len(src_paths):
            raise RuntimeError(
                f"All {len(src_paths)} source path(s) failed to list; aborting (possible SharePoint outage)"
            )
        return supported_union, unsupported_union

    def filter_new(
        self,
        files: list[dict[str, Any]],
        in_flight: set[str],
    ) -> list[dict[str, Any]]:
        """Exclude files whose SharePoint path is in the in-flight set.

        Args:
            files: File entries from :meth:`list_files`.
            in_flight: Set of SharePoint paths currently PENDING or PARTIAL.

        Returns:
            Filtered list of file entries not currently being processed.
        """
        new = [f for f in files if f["sp_path"] not in in_flight]
        skipped = len(files) - len(new)
        if skipped:
            logger.info(f"Skipped {skipped} in-flight file(s)")
        return new

    async def upload_to_landing(
        self,
        files: list[dict[str, Any]],
        landing_gcs_prefix: str,
        max_concurrent: int,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Upload files from SharePoint to GCS landing asynchronously.

        Args:
            files: File entries from :meth:`list_files`.
            landing_gcs_prefix: GCS prefix (without ``gs://bucket/``) for uploaded files.
            max_concurrent: Maximum concurrent SharePoint→GCS uploads.

        Returns:
            A ``(uploaded, failed)`` tuple. Each element is a list of dicts
            with ``name``, ``sp_path``, and ``gcs_path`` keys; ``failed``
            entries also include an ``error`` key.
        """
        if not files:
            return [], []

        stream_list = [
            {
                "download": f["sp_path"],
                "upload": f"{landing_gcs_prefix.rstrip('/')}/{f['name']}",
                "mime_type": f["mime_type"],
            }
            for f in files
        ]

        res = await self._gcs.upload_sharepoint_to_gcs(
            sharepoint_object=self._sp,
            stream_list=stream_list,
            max_concurrent_uploads=max_concurrent,
        )

        errors_by_path = {e.get("download_path"): e.get("error", "upload failed") for e in res.get("errors", [])}
        failed = [
            {"name": f["name"], "sp_path": f["sp_path"], "error": errors_by_path[f["sp_path"]]}
            for f in files
            if f["sp_path"] in errors_by_path
        ]
        uploaded = [
            {
                "name": f["name"],
                "sp_path": f["sp_path"],
                "gcs_path": f"gs://{self._gcs.bucket_name}/{landing_gcs_prefix.rstrip('/')}/{f['name']}",
            }
            for f in files
            if f["sp_path"] not in errors_by_path
        ]
        return uploaded, failed

    def _build_file_entry(self, item: dict[str, Any]) -> dict[str, Any] | None:
        """Build a normalised file-entry dict from a raw SharePoint item.

        Extension filtering is the caller's responsibility (:meth:`list_files` partitions
        by ``ext_filter`` after building the entry). Returns ``None`` only when the item
        should be skipped outright (missing parent path).
        """
        name = item.get("name", "")
        parent_path = item.get("parentReference", {}).get("path", "")
        if not parent_path:
            logger.warning(f"Skipping {name}: missing parentReference.path")
            return None

        sp_path = parent_path.replace(_DRIVE_ROOT_PREFIX, "") + "/" + name
        mime_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
        return {"name": name, "sp_path": sp_path, "mime_type": mime_type}
