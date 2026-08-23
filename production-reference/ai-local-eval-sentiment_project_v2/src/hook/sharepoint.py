# Library imports
import posixpath
import re
import time
from datetime import datetime
from http import HTTPStatus
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests
from msal import ConfidentialClientApplication

# Source code imports
from src.hook.tls import TlsPolicy
from src.logger import Logger

logger = Logger.get_logger(__name__)


class SharePointError(Exception):
    """Base for every SharePoint/Graph failure raised by this module."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """Initialize the error.

        Args:
            message: Human-readable description of the failure.
            status_code: HTTP status that produced it, when the failure came from Graph.
        """
        super().__init__(message)
        self.status_code = status_code


class SharePointAuthError(SharePointError):
    """Token acquisition failed, or a 401/403 persisted after refresh."""


class SharePointNotFoundError(SharePointError):
    """Graph returned 404 for the addressed item."""


class SharePointConflictError(SharePointError):
    """Graph returned 409 Conflict or 423 Locked after retries."""


class SharePointModule:
    """
    SharePoint module for interacting with Microsoft Graph API.
    Handles authentication, file operations, and SharePoint site management.
    """

    # API Configuration Constants
    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    GRAPH_SCOPE = "https://graph.microsoft.com/.default"
    LOGIN_AUTHORITY_BASE = "https://login.microsoftonline.com"

    # Retry Configuration
    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 10
    RETRY_CONNECTION_ERRORS_SECONDS = 5
    RETRY_STATUS_CODES = (HTTPStatus.LOCKED, HTTPStatus.CONFLICT)

    # Throttling / transient unavailability. Graph sends Retry-After with both.
    THROTTLE_STATUS_CODES = (HTTPStatus.TOO_MANY_REQUESTS, HTTPStatus.SERVICE_UNAVAILABLE)
    MAX_RETRY_AFTER_SECONDS = 120

    # (connect, read) seconds. requests defaults to None, so an unbounded read against a
    # half-open connection blocks the calling thread forever with nothing in the log.
    DEFAULT_TIMEOUT = (10.0, 60.0)

    # Errors worth repeating the whole request for, as opposed to failing fast.
    NETWORK_ERRORS = (
        requests.exceptions.ConnectionError,
        requests.exceptions.Timeout,
        requests.exceptions.ChunkedEncodingError,
    )

    def __init__(self, **kwargs):
        """
        Initialize SharePoint module with authentication and site configuration.

        Parameters:
            client_id (str): Azure AD application client ID
            client_secret (str): Azure AD application client secret
            tenant_id (str): Azure AD tenant ID
            site_domain (str): SharePoint site domain
            site_path (str): SharePoint site path
            timezone (str, optional): Timezone for datetime operations. Defaults to 'UTC'
            timeout (tuple, optional): (connect, read) seconds. Defaults to DEFAULT_TIMEOUT

        Raises:
            SharePointError: If required parameters are missing or the site is unreachable
        """
        started = time.monotonic()

        # Authentication configuration
        self.client_id = kwargs.get("client_id")
        self.client_secret = kwargs.get("client_secret")
        self.tenant_id = kwargs.get("tenant_id")

        if not all([self.client_id, self.client_secret, self.tenant_id]):
            message = "Missing required authentication parameters: client_id, client_secret, or tenant_id"
            logger.error("sharepoint.config.invalid", reason=message)
            raise SharePointError(message)

        # Site configuration
        self.site_domain = kwargs.get("site_domain")
        self.site_path = kwargs.get("site_path")

        if not all([self.site_domain, self.site_path]):
            message = "Missing required site parameters: site_domain or site_path"
            logger.error("sharepoint.config.invalid", reason=message)
            raise SharePointError(message)

        # Timezone configuration
        timezone_str = kwargs.get("timezone", "UTC")
        self.timezone = ZoneInfo(timezone_str)

        self._timeout = kwargs.get("timeout", self.DEFAULT_TIMEOUT)

        # Scopes for authentication
        self.scope = [self.GRAPH_SCOPE]

        # Cache for site_id to avoid repeated API calls
        self._site_id_cache: str | None = None

        # TLS 1.2+ pinned session, reused for all Graph calls and handed to MSAL token requests
        self._session = TlsPolicy().session()

        # One MSAL application for the module's lifetime. MSAL caches tokens on the instance,
        # so rebuilding it per acquisition made every refresh a guaranteed round-trip to AAD.
        self._msal_app = ConfidentialClientApplication(
            self.client_id,
            authority=f"{self.LOGIN_AUTHORITY_BASE}/{self.tenant_id}",
            client_credential=self.client_secret,
            http_client=self._session,
        )

        logger.debug(
            "sharepoint.config.resolved",
            site_domain=self.site_domain,
            site_path=self.site_path,
            timezone=timezone_str,
            timeout=self._timeout,
            max_retries=self.MAX_RETRIES,
        )

        # Obtain access token
        self.access_token = self.__get_access_token()

        # Verify the site is reachable before handing the module to a caller
        site_id = self._test_connection()

        logger.info(
            "sharepoint.connected",
            site_domain=self.site_domain,
            site_path=self.site_path,
            site_id=site_id,
            elapsed_ms=self._elapsed_ms(started),
        )

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        """Milliseconds since a ``time.monotonic()`` reading, rounded for logging."""
        return round((time.monotonic() - started) * 1000, 1)

    def _get_headers(self, additional_headers: dict[str, str] | None = None) -> dict[str, str]:
        """
        Get HTTP headers with current access token.

        Parameters:
            additional_headers (dict, optional): Additional headers to include

        Returns:
            dict: HTTP headers with Authorization bearer token
        """
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if additional_headers:
            headers.update(additional_headers)
        return headers

    def _ensure_leading_slash(self, path: str) -> str:
        """
        Ensure a path starts with a forward slash.

        Parameters:
            path (str): The path to check

        Returns:
            str: Path with leading slash
        """
        return path if path.startswith("/") else f"/{path}"

    @staticmethod
    def _encode_path(path: str) -> str:
        """
        Percent-encode a drive path for Graph's ``/drive/root:{path}`` addressing.

        ``safe="/"`` keeps separators intact while escaping the characters that silently
        retarget a request: ``#`` opens a URL fragment (taking any trailing ``:/content``
        with it, so the request lands on a different item), ``?`` opens a query string, and
        a bare ``%`` is otherwise re-read as an escape sequence. Spaces, ``+``, and
        non-ASCII are already handled correctly by requests' own ``requote_uri`` -- do not
        double-encode them here.

        Parameters:
            path (str): The drive path to encode

        Returns:
            str: Percent-encoded path, separators preserved
        """
        return quote(path, safe="/")

    @staticmethod
    def _safe_url(url: str) -> str:
        """
        Strip the query string from a URL before it reaches a log line.

        Graph's pre-authenticated download URLs carry a bearer credential in their query
        parameters, so the raw URL is itself a secret.

        Parameters:
            url (str): The URL to redact

        Returns:
            str: The URL without its query string
        """
        return url.split("?", 1)[0]

    def _build_graph_url(self, endpoint: str) -> str:
        """
        Build a complete Microsoft Graph API URL.

        Parameters:
            endpoint (str): The API endpoint path

        Returns:
            str: Complete Graph API URL
        """
        return f"{self.GRAPH_API_BASE}/{endpoint.lstrip('/')}"

    def _item_endpoint(self, site_id: str, item_path: str) -> str:
        """
        Build the driveItem endpoint for a path.

        Graph addresses the drive root as ``/drive/root`` and anything below it as
        ``/drive/root:{path}``; the two forms are not interchangeable.

        Parameters:
            site_id (str): The SharePoint site ID
            item_path (str): Absolute drive path of the item

        Returns:
            str: Endpoint fragment for :meth:`_build_graph_url`
        """
        if item_path == "/":
            return f"sites/{site_id}/drive/root"
        return f"sites/{site_id}/drive/root:{self._encode_path(item_path)}"

    def _test_connection(self) -> str:
        """
        Verify the connection to the SharePoint site by retrieving the site ID.

        Raises rather than returning a bool: collapsing every distinct cause -- expired
        secret, missing Sites.ReadWrite.All consent, typo'd site_path, DNS failure -- into
        False left the caller with an error carrying no diagnostic detail.

        Returns:
            str: The resolved site ID

        Raises:
            SharePointError: If the site cannot be reached
        """
        logger.debug(
            "sharepoint.connection.testing", site_domain=self.site_domain, site_path=self.site_path
        )

        try:
            return self.get_site_id(use_cache=False)
        except SharePointError:
            raise
        except Exception as exc:
            message = (
                f"Failed to connect to SharePoint site {self.site_domain}{self.site_path}: {exc}"
            )
            logger.error("sharepoint.connection.failed", reason=str(exc), exc_info=True)
            raise SharePointError(message) from exc

    def __get_access_token(self) -> str:
        """
        Obtain an access token for SharePoint using MSAL.

        Returns:
            str: Access token for SharePoint

        Raises:
            SharePointAuthError: If token acquisition fails
        """
        logger.debug("sharepoint.token.acquiring", tenant_id=self.tenant_id)

        try:
            access_result = self._msal_app.acquire_token_for_client(scopes=self.scope)
        except Exception as exc:
            logger.error("sharepoint.token.failed", reason=str(exc), exc_info=True)
            raise SharePointAuthError(f"Error obtaining access token: {exc}") from exc

        if "access_token" not in access_result:
            description = access_result.get(
                "error_description", access_result.get("error", "Unknown error")
            )
            logger.error("sharepoint.token.failed", reason=description)
            raise SharePointAuthError(f"Failed to acquire token: {description}")

        logger.debug("sharepoint.token.acquired", expires_in=access_result.get("expires_in"))
        return access_result["access_token"]

    def __refresh_access_token(self) -> None:
        """
        Refresh the access token.

        Logged at WARNING: a reactive refresh means a request was already rejected.
        """
        logger.warning("sharepoint.token.refreshed", reason="401")
        self.access_token = self.__get_access_token()

    def _log_response(self, response: requests.Response) -> None:
        """
        Emit one DEBUG record per HTTP response.

        Every session call in this module passes through :meth:`__handle_response_with_retry`,
        so instrumenting here covers the whole module. ``elapsed`` comes free from requests.

        Parameters:
            response: The response to record
        """
        logger.debug(
            "sharepoint.request.completed",
            method=response.request.method if response.request is not None else None,
            url=self._safe_url(response.url),
            status=response.status_code,
            elapsed_ms=round(response.elapsed.total_seconds() * 1000, 1),
        )

    def _retry_delay(self, response: requests.Response) -> float:
        """
        Seconds to wait before retrying a throttled request.

        Graph sends ``Retry-After`` on 429 and 503; honouring it is what stops a retry storm
        from extending the throttle window. Clamped so an outsized value cannot stall the
        process, and falls back to the fixed delay when the header is absent or expressed as
        an HTTP-date.

        Parameters:
            response: The throttled response

        Returns:
            float: Delay in seconds
        """
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), self.MAX_RETRY_AFTER_SECONDS)
            except ValueError:
                logger.debug("sharepoint.request.retry_after_unparsed", value=retry_after)
        return float(self.RETRY_DELAY_SECONDS)

    def _raise_for_status(self, response: requests.Response, context: str) -> None:
        """
        Map a failed Graph response onto this module's exception hierarchy.

        Replaces ``raise_for_status()``: callers were re-raising ``requests.HTTPError`` and
        then dereferencing ``http_err.response.status_code``, which is Optional, so the error
        handler itself could raise while logging and destroy the original traceback. This is
        the single ERROR site for HTTP faults -- outer handlers must not log again. The body
        is truncated because Graph error payloads are unbounded and end up in log lines.

        Parameters:
            response: The response to inspect
            context (str): What was being attempted, for the message

        Raises:
            SharePointAuthError: On 401 or 403
            SharePointNotFoundError: On 404
            SharePointConflictError: On 409 or 423
            SharePointError: On any other non-success status
        """
        if response.ok:
            return

        status = response.status_code
        message = f"{context} failed: {status} {response.text[:500]}"
        logger.error(
            "sharepoint.request.failed",
            context=context,
            status=status,
            url=self._safe_url(response.url),
            body=response.text[:500],
        )

        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            raise SharePointAuthError(message, status_code=status)
        if status == HTTPStatus.NOT_FOUND:
            raise SharePointNotFoundError(message, status_code=status)
        if status in (HTTPStatus.CONFLICT, HTTPStatus.LOCKED):
            raise SharePointConflictError(message, status_code=status)
        raise SharePointError(message, status_code=status)

    def __handle_response_with_retry(self, response: requests.Response, retry_func, *args, **kwargs):
        """
        Handle a response, retrying once after a token refresh or a throttling delay.

        ``retry_func`` must rebuild its headers from :meth:`_get_headers` internally. Capturing
        a header dict in the lambda re-sent the token that had just been rejected, which made
        the 401 path a guaranteed second 401.

        Parameters:
            response: Initial HTTP response
            retry_func: Callable that re-issues the request
            *args: Arguments to pass to retry function
            **kwargs: Keyword arguments to pass to retry function

        Returns:
            requests.Response: The response to act on
        """
        self._log_response(response)

        if response.status_code == HTTPStatus.UNAUTHORIZED:
            self.__refresh_access_token()
            retried = retry_func(*args, **kwargs)
            self._log_response(retried)
            return retried

        if response.status_code in self.THROTTLE_STATUS_CODES:
            delay = self._retry_delay(response)
            logger.warning(
                "sharepoint.request.retry",
                url=self._safe_url(response.url),
                status=response.status_code,
                delay_s=delay,
                retry_after=response.headers.get("Retry-After"),
            )
            time.sleep(delay)
            retried = retry_func(*args, **kwargs)
            self._log_response(retried)
            return retried

        return response

    def get_site_id(self, use_cache: bool = True) -> str:
        """
        Retrieve the SharePoint site ID with caching support.

        Parameters:
            use_cache (bool): Whether to use cached site_id if available

        Returns:
            str: The site ID

        Raises:
            SharePointError: If the site ID cannot be retrieved
        """
        if use_cache and self._site_id_cache:
            logger.debug("sharepoint.site.cache_hit", site_id=self._site_id_cache)
            return self._site_id_cache

        site_endpoint = f"sites/{self.site_domain}:{self._encode_path(self.site_path)}"
        site_url = self._build_graph_url(site_endpoint)

        response = self._session.get(site_url, headers=self._get_headers(), timeout=self._timeout)
        response = self.__handle_response_with_retry(
            response,
            lambda: self._session.get(site_url, headers=self._get_headers(), timeout=self._timeout),
        )
        self._raise_for_status(response, f"Resolving site {self.site_domain}{self.site_path}")

        site_id = response.json().get("id")
        if not site_id:
            logger.error(
                "sharepoint.site.id_missing", site_domain=self.site_domain, site_path=self.site_path
            )
            raise SharePointError(
                f"Site ID missing from response for {self.site_domain}{self.site_path}"
            )

        self._site_id_cache = site_id
        logger.debug("sharepoint.site.resolved", site_id=site_id)
        return site_id

    def _get_item(self, item_path: str, context: str) -> dict[str, Any]:
        """
        Fetch driveItem metadata addressed by path.

        Shared by every method that needs an item's id, name, parent, or driveId before
        acting on it.

        Parameters:
            item_path (str): The path of the item
            context (str): What the metadata is for, used in the error message

        Returns:
            dict: The driveItem payload

        Raises:
            SharePointNotFoundError: If the item does not exist
            SharePointError: If the request fails for any other reason
        """
        item_path = self._ensure_leading_slash(item_path)
        site_id = self.get_site_id()
        item_url = self._build_graph_url(self._item_endpoint(site_id, item_path))

        response = self._session.get(item_url, headers=self._get_headers(), timeout=self._timeout)
        response = self.__handle_response_with_retry(
            response,
            lambda: self._session.get(item_url, headers=self._get_headers(), timeout=self._timeout),
        )
        self._raise_for_status(response, context)
        return response.json()

    def _fetch_all_pages(self, list_url: str, dir_label: str) -> list[dict[str, Any]]:
        """
        Follow ``@odata.nextLink`` until the collection is exhausted.

        Shared by the root and sub-directory listings, which differ only in the first URL.
        nextLink is absolute and already encoded, so it is passed back through untouched.

        Parameters:
            list_url (str): First page URL
            dir_label (str): Directory label for log records

        Returns:
            list: Every item across all pages

        Raises:
            SharePointError: If any page fails
        """
        all_files: list[dict[str, Any]] = []
        page_count = 0

        while list_url:
            page_count += 1
            response = self._session.get(
                list_url, headers=self._get_headers(), timeout=self._timeout
            )
            response = self.__handle_response_with_retry(
                response,
                lambda url=list_url: self._session.get(
                    url, headers=self._get_headers(), timeout=self._timeout
                ),
            )
            self._raise_for_status(response, f"Listing children of {dir_label}")

            data = response.json()
            page_items = data.get("value", [])
            all_files.extend(page_items)
            logger.debug(
                "sharepoint.children.page",
                directory=dir_label,
                page=page_count,
                items=len(page_items),
            )

            list_url = data.get("@odata.nextLink")

        logger.debug(
            "sharepoint.children.listed",
            directory=dir_label,
            total=len(all_files),
            pages=page_count,
        )
        return all_files

    def _list_files_current_directory(self, dir_path: str) -> list[dict[str, Any]]:
        """
        List all files in a SharePoint folder with pagination support.

        Parameters:
            dir_path (str): The path of the folder to list files from

        Returns:
            list: List of file/folder metadata dictionaries

        Raises:
            SharePointError: If files cannot be listed
        """
        dir_path = self._ensure_leading_slash(dir_path)
        site_id = self.get_site_id()
        list_endpoint = f"{self._item_endpoint(site_id, dir_path)}:/children"
        return self._fetch_all_pages(self._build_graph_url(list_endpoint), dir_path)

    def _list_files_root(self) -> list[dict[str, Any]]:
        """
        List all files in the root directory of the SharePoint site.

        Returns:
            list: List of file/folder metadata dictionaries

        Raises:
            SharePointError: If files cannot be listed
        """
        site_id = self.get_site_id()
        list_endpoint = f"sites/{site_id}/drive/root/children"
        return self._fetch_all_pages(self._build_graph_url(list_endpoint), "/")

    def _collect_paths(
        self,
        dir_path: str,
        *,
        recursive: bool,
        regex: re.Pattern[str] | None,
        want_folders: bool,
    ) -> tuple[list[str], int]:
        """
        Walk a folder and collect the paths of the items that match.

        Child paths are composed from the directory being walked plus the item name rather
        than read back out of ``parentReference.path``: Graph omits that field on some items,
        and reading it produced a bogus root-level path that then 404'd and aborted the walk.

        Parameters:
            dir_path (str): The folder to walk
            recursive (bool): Whether to descend into subfolders
            regex: Compiled pattern to filter paths, or None for no filtering
            want_folders (bool): Collect folders when True, files when False

        Returns:
            tuple: (matching paths, number of folders visited)
        """
        base = self._ensure_leading_slash(dir_path).rstrip("/")
        items = self._list_files_current_directory(base) if base else self._list_files_root()

        matched: list[str] = []
        folders_walked = 1

        for item in items:
            name = item.get("name", "")
            if not name:
                continue

            item_path = f"{base}/{name}"
            # Presence, not truthiness: an empty folder can serialize as "folder": {}, which
            # the old `if item.get("folder")` check read as a file.
            is_folder = "folder" in item

            # Matched against the full path, not the bare name: a pattern like r".*/\d{8}$"
            # carries a separator and can only ever match a path. `search`, not `match`, so an
            # unanchored pattern behaves the way callers expect.
            if is_folder is want_folders and (regex is None or regex.search(item_path)):
                logger.debug("sharepoint.listing.matched", path=item_path, folder=is_folder)
                matched.append(item_path)

            if recursive and is_folder:
                child_paths, child_folders = self._collect_paths(
                    item_path, recursive=True, regex=regex, want_folders=want_folders
                )
                matched.extend(child_paths)
                folders_walked += child_folders

        return matched, folders_walked

    def list_files(
        self, dir_path: str, recursive: bool = False, pattern: str | None = None
    ) -> list[str]:
        """
        List files in a SharePoint folder, optionally recursively.

        Parameters:
            dir_path (str): The path of the folder to list files from
            recursive (bool): Whether to list files recursively from subfolders
            pattern (str, optional): Regex pattern matched against each file's full path

        Returns:
            list: Full paths of the matching files

        Raises:
            SharePointError: If the listing fails
        """
        started = time.monotonic()
        regex = re.compile(pattern) if pattern else None
        paths, folders_walked = self._collect_paths(
            dir_path, recursive=recursive, regex=regex, want_folders=False
        )

        logger.info(
            "sharepoint.listing.completed",
            directory=dir_path,
            kind="files",
            recursive=recursive,
            pattern=pattern,
            folders_walked=folders_walked,
            items=len(paths),
            elapsed_ms=self._elapsed_ms(started),
        )
        return paths

    def list_dirs(
        self, dir_path: str, recursive: bool = False, pattern: str | None = None
    ) -> list[str]:
        """
        List directories in a SharePoint folder, optionally recursively.

        Parameters:
            dir_path (str): The path of the folder to list directories from
            recursive (bool): Whether to list directories recursively from subfolders
            pattern (str, optional): Regex pattern matched against each directory's full path

        Returns:
            list: Full paths of the matching directories

        Raises:
            SharePointError: If the listing fails
        """
        started = time.monotonic()
        regex = re.compile(pattern) if pattern else None
        paths, folders_walked = self._collect_paths(
            dir_path, recursive=recursive, regex=regex, want_folders=True
        )

        logger.info(
            "sharepoint.listing.completed",
            directory=dir_path,
            kind="dirs",
            recursive=recursive,
            pattern=pattern,
            folders_walked=folders_walked,
            items=len(paths),
            elapsed_ms=self._elapsed_ms(started),
        )
        return paths

    def download_file(self, item_path: str) -> requests.Response:
        """
        Retrieve and download an item from SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to retrieve

        Returns:
            requests.Response: The response containing the downloaded file content

        Raises:
            SharePointNotFoundError: If the item does not exist
            SharePointError: If the item cannot be retrieved or downloaded
        """
        started = time.monotonic()
        item_path = self._ensure_leading_slash(item_path)

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                item_data = self._get_item(item_path, f"Retrieving item {item_path}")

                download_url = item_data.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    logger.error("sharepoint.download.url_missing", path=item_path)
                    raise SharePointError(f"Download URL missing in response for {item_path}")

                logger.debug(
                    "sharepoint.download.metadata", path=item_path, size=item_data.get("size")
                )

                # The download URL is pre-authorized and carries its credential in the query
                # string, so it goes out without our bearer header -- and never into a log.
                download_res = self._session.get(download_url, timeout=self._timeout)
                download_res = self.__handle_response_with_retry(
                    download_res,
                    lambda _u=download_url: self._session.get(_u, timeout=self._timeout),
                )
                self._raise_for_status(download_res, f"Downloading {item_path}")

                logger.info(
                    "sharepoint.download.completed",
                    path=item_path,
                    bytes=len(download_res.content),
                    attempts=attempt,
                    elapsed_ms=self._elapsed_ms(started),
                )
                return download_res

            except self.NETWORK_ERRORS as network_err:
                if attempt >= self.MAX_RETRIES:
                    logger.error(
                        "sharepoint.download.failed",
                        path=item_path,
                        error_type=type(network_err).__name__,
                        attempts=attempt,
                        exc_info=True,
                    )
                    raise SharePointError(
                        f"Network failure retrieving {item_path} after {attempt} attempts: "
                        f"{network_err}"
                    ) from network_err

                logger.warning(
                    "sharepoint.download.retry",
                    path=item_path,
                    attempt=attempt,
                    error_type=type(network_err).__name__,
                )
                time.sleep(self.RETRY_CONNECTION_ERRORS_SECONDS)

        # Unreachable while MAX_RETRIES >= 1, but the return annotation must hold if it changes.
        raise SharePointError(f"Retrieving {item_path} exhausted {self.MAX_RETRIES} attempts")

    def is_item_exists(self, item_path: str) -> bool:
        """
        Check if an item exists in SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to check

        Returns:
            bool: True if the item exists, False otherwise

        Raises:
            SharePointError: If the check fails for a reason other than 404
        """
        item_path = self._ensure_leading_slash(item_path)
        site_id = self.get_site_id()
        item_url = self._build_graph_url(self._item_endpoint(site_id, item_path))

        response = self._session.get(item_url, headers=self._get_headers(), timeout=self._timeout)
        response = self.__handle_response_with_retry(
            response,
            lambda: self._session.get(item_url, headers=self._get_headers(), timeout=self._timeout),
        )

        if response.status_code == HTTPStatus.NOT_FOUND:
            logger.debug("sharepoint.exists.checked", path=item_path, exists=False)
            return False

        self._raise_for_status(response, f"Checking existence of {item_path}")
        logger.debug("sharepoint.exists.checked", path=item_path, exists=True)
        return True

    def _archive_locked_file(self, item_path: str) -> str | None:
        """
        Archive a locked file by renaming it with a timestamp.

        Logged at WARNING rather than INFO: this mutates a user's file as a side effect of an
        upload, which is not routine.

        Parameters:
            item_path (str): The path of the item to archive

        Returns:
            str | None: The archived path on success, None if archiving was not possible
        """
        try:
            item_data = self._get_item(item_path, f"Resolving {item_path} for archiving")
        except SharePointError as exc:
            logger.warning("sharepoint.archive.metadata_failed", path=item_path, reason=str(exc))
            return None

        item_id = item_data.get("id")
        item_name = item_data.get("name")
        parent_folder_id = item_data.get("parentReference", {}).get("id")
        drive_id = item_data.get("parentReference", {}).get("driveId")

        if not all([item_id, item_name, parent_folder_id, drive_id]):
            logger.warning("sharepoint.archive.metadata_incomplete", path=item_path)
            return None

        # Generate archive name with timestamp
        timestamp = datetime.now(tz=self.timezone).strftime("%Y%m%d%H%M%S")
        archive_name = f"archive_{timestamp}_{item_name}"

        move_url = self._build_graph_url(f"drives/{drive_id}/items/{item_id}")
        move_payload = {"parentReference": {"id": parent_folder_id}, "name": archive_name}
        move_headers = {"Content-Type": "application/json", "Prefer": "bypass-shared-lock"}

        rename_response = self._session.patch(
            move_url,
            headers=self._get_headers(move_headers),
            json=move_payload,
            timeout=self._timeout,
        )
        rename_response = self.__handle_response_with_retry(
            rename_response,
            lambda: self._session.patch(
                move_url,
                headers=self._get_headers(move_headers),
                json=move_payload,
                timeout=self._timeout,
            ),
        )

        if not rename_response.ok:
            logger.warning(
                "sharepoint.archive.failed", path=item_path, status=rename_response.status_code
            )
            return None

        archived_path = f"{posixpath.dirname(item_path)}/{archive_name}".replace("//", "/")
        logger.warning(
            "sharepoint.archive.renamed",
            path=item_path,
            archive_name=archive_name,
            archived_path=archived_path,
            item_id=item_id,
        )
        return archived_path

    def _restore_archived_file(self, archived_path: str, original_path: str) -> None:
        """
        Undo an archive rename after the upload it made room for failed.

        Without this, a failed upload leaves nothing at the destination: the caller sees an
        exception and assumes nothing happened, while the file it was replacing has silently
        moved elsewhere.

        Parameters:
            archived_path (str): Where the original file was moved to
            original_path (str): Where it should be moved back to
        """
        try:
            restored = self.rename_file(archived_path, original_path)
        except SharePointError as exc:
            logger.error(
                "sharepoint.archive.restore_failed",
                path=original_path,
                archived_path=archived_path,
                reason=str(exc),
            )
            return

        if restored:
            logger.warning(
                "sharepoint.archive.restored", path=original_path, archived_path=archived_path
            )
        else:
            logger.error(
                "sharepoint.archive.restore_failed",
                path=original_path,
                archived_path=archived_path,
            )

    def upload_file(
        self, upload_path: str, content: bytes, *, archive_on_lock: bool = False
    ) -> requests.Response:
        """
        Upload a file to SharePoint at the specified path with retry logic.

        Parameters:
            upload_path (str): The path where the file will be uploaded
            content (bytes): The binary data of the file to upload
            archive_on_lock (bool): When True, a 423 Locked destination is archived once (its
                existing file renamed aside) and the upload retried. Off by default: this
                mutates a file the caller did not name. Never triggered by a 409, which is
                usually an eTag/nameAlreadyExists conflict rather than a lock.

        Returns:
            requests.Response: The response from the successful upload operation

        Raises:
            SharePointConflictError: If the destination stays locked or conflicted
            SharePointError: If the upload fails for any other reason
        """
        started = time.monotonic()
        upload_path = self._ensure_leading_slash(upload_path)
        site_id = self.get_site_id()
        upload_url = self._build_graph_url(f"{self._item_endpoint(site_id, upload_path)}:/content")

        # Tracked separately: a failed archive must not re-arm the attempt on the next pass,
        # so "did we try" cannot be inferred from "did we succeed".
        archived_path: str | None = None
        archive_attempted = False
        response: requests.Response | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.debug(
                "sharepoint.upload.attempt",
                path=upload_path,
                attempt=attempt,
                max_attempts=self.MAX_RETRIES,
            )

            response = self._session.put(
                upload_url, headers=self._get_headers(), data=content, timeout=self._timeout
            )
            response = self.__handle_response_with_retry(
                response,
                lambda: self._session.put(
                    upload_url, headers=self._get_headers(), data=content, timeout=self._timeout
                ),
            )

            if response.ok:
                logger.info(
                    "sharepoint.upload.completed",
                    path=upload_path,
                    bytes=len(content),
                    attempts=attempt,
                    archived=archived_path is not None,
                    elapsed_ms=self._elapsed_ms(started),
                )
                return response

            if response.status_code not in self.RETRY_STATUS_CODES:
                break

            logger.warning(
                "sharepoint.upload.conflict",
                path=upload_path,
                status=response.status_code,
                attempt=attempt,
            )

            # Archive at most once, and only on a genuine lock. Archiving on every attempt
            # left up to three archive_<ts>_<name> copies behind per upload.
            if (
                archive_on_lock
                and not archive_attempted
                and response.status_code == HTTPStatus.LOCKED
            ):
                archive_attempted = True
                archived_path = self._archive_locked_file(upload_path)
                if archived_path:
                    response = self._session.put(
                        upload_url,
                        headers=self._get_headers(),
                        data=content,
                        timeout=self._timeout,
                    )
                    response = self.__handle_response_with_retry(
                        response,
                        lambda: self._session.put(
                            upload_url,
                            headers=self._get_headers(),
                            data=content,
                            timeout=self._timeout,
                        ),
                    )
                    if response.ok:
                        logger.info(
                            "sharepoint.upload.completed",
                            path=upload_path,
                            bytes=len(content),
                            attempts=attempt,
                            archived=True,
                            elapsed_ms=self._elapsed_ms(started),
                        )
                        return response

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY_SECONDS)

        # Every attempt is spent. Put the archived file back first, so a failed upload does
        # not leave the destination path empty.
        if archived_path:
            self._restore_archived_file(archived_path, upload_path)

        if response is not None:
            self._raise_for_status(response, f"Uploading {upload_path}")
        raise SharePointError(f"Uploading {upload_path} exhausted {self.MAX_RETRIES} attempts")

    def copy_file(self, source_path: str, destination_path: str) -> bool:
        """
        Copy a file within SharePoint from source path to destination path.
        Uses download + upload approach for reliability.

        Parameters:
            source_path (str): The path of the source file to copy
            destination_path (str): The path where the file will be copied to

        Returns:
            bool: True if copy was successful, False otherwise

        Note:
            This implementation uses download + upload rather than the Graph API /copy endpoint
            because the /copy endpoint is asynchronous and has issues with same-folder copies.
        """
        started = time.monotonic()

        try:
            source_response = self.download_file(source_path)
            content = source_response.content
            logger.debug("sharepoint.copy.downloaded", source=source_path, bytes=len(content))
            self.upload_file(destination_path, content)
        except SharePointError as exc:
            # Deliberately narrow: the old blanket `except Exception` swallowed an
            # AttributeError from a misspelled method call, so copy_file returned False for
            # every input and the bug stayed invisible.
            logger.error(
                "sharepoint.copy.failed",
                source=source_path,
                destination=destination_path,
                reason=str(exc),
            )
            return False

        logger.info(
            "sharepoint.copy.completed",
            source=source_path,
            destination=destination_path,
            bytes=len(content),
            elapsed_ms=self._elapsed_ms(started),
        )
        return True

    def _resolve_folder_id(self, folder_path: str) -> str:
        """
        Resolve a folder's driveItem ID, for use as a move target.

        Parameters:
            folder_path (str): Path of the destination folder

        Returns:
            str: The folder's item ID

        Raises:
            SharePointNotFoundError: If the folder does not exist
            SharePointError: If the response carries no item ID
        """
        folder_data = self._get_item(folder_path, f"Resolving destination folder {folder_path}")
        folder_id = folder_data.get("id")
        if not folder_id:
            raise SharePointError(f"Destination folder {folder_path} has no item ID")
        return folder_id

    def rename_file(self, current_path: str, new_path: str) -> bool:
        """
        Rename or move an existing file on SharePoint.

        Honours the directory component of ``new_path``: when it differs from the source's
        folder the file is moved, not just renamed. The previous implementation always reused
        the source's parent, so a cross-directory rename silently became a no-op that still
        reported success.

        Includes specialized logic to handle files locked (423) by active users.

        Parameters:
            current_path (str): The current path of the file on SharePoint
            new_path (str): The new path for the file on SharePoint

        Returns:
            bool: True if renaming succeeded

        Raises:
            SharePointNotFoundError: If the source or destination folder does not exist
            SharePointError: If the rename fails
        """
        current_path = self._ensure_leading_slash(current_path)
        new_path = self._ensure_leading_slash(new_path)

        item_data = self._get_item(current_path, f"Resolving {current_path} for rename")
        item_id = item_data.get("id")
        parent_folder_id = item_data.get("parentReference", {}).get("id")
        drive_id = item_data.get("parentReference", {}).get("driveId")

        if not all([item_id, parent_folder_id, drive_id]):
            logger.error("sharepoint.rename.metadata_incomplete", path=current_path)
            raise SharePointError(f"Missing metadata required to rename {current_path}")

        source_dir = posixpath.dirname(current_path)
        destination_dir = posixpath.dirname(new_path)
        moved = destination_dir != source_dir
        target_parent_id = self._resolve_folder_id(destination_dir) if moved else parent_folder_id

        logger.debug(
            "sharepoint.rename.resolved",
            item_id=item_id,
            source_parent=source_dir,
            destination_parent=destination_dir,
            moved=moved,
        )

        move_url = self._build_graph_url(f"drives/{drive_id}/items/{item_id}")
        # posixpath, not os.path: on Windows os.path.basename also splits on "\", which is
        # wrong for a remote POSIX-style path.
        move_payload = {
            "parentReference": {"id": target_parent_id},
            "name": posixpath.basename(new_path),
        }
        patch_headers = {"Content-Type": "application/json"}
        bypass_headers = {"Content-Type": "application/json", "Prefer": "bypass-shared-lock"}
        rename_response: requests.Response | None = None

        for attempt in range(1, self.MAX_RETRIES + 1):
            logger.debug(
                "sharepoint.rename.attempt",
                path=current_path,
                attempt=attempt,
                max_attempts=self.MAX_RETRIES,
            )

            rename_response = self._session.patch(
                move_url,
                headers=self._get_headers(patch_headers),
                json=move_payload,
                timeout=self._timeout,
            )
            rename_response = self.__handle_response_with_retry(
                rename_response,
                lambda: self._session.patch(
                    move_url,
                    headers=self._get_headers(patch_headers),
                    json=move_payload,
                    timeout=self._timeout,
                ),
            )

            if rename_response.ok:
                logger.info(
                    "sharepoint.rename.completed",
                    source=current_path,
                    destination=new_path,
                    moved=moved,
                )
                return True

            # 400 (invalid name), 403 (no permission) and 404 are deterministic; the old loop
            # re-sent an identical PATCH three times with no delay before giving up.
            if rename_response.status_code not in self.RETRY_STATUS_CODES:
                break

            logger.warning(
                "sharepoint.rename.locked",
                path=current_path,
                status=rename_response.status_code,
                attempt=attempt,
            )

            rename_response = self._session.patch(
                move_url,
                headers=self._get_headers(bypass_headers),
                json=move_payload,
                timeout=self._timeout,
            )
            rename_response = self.__handle_response_with_retry(
                rename_response,
                lambda: self._session.patch(
                    move_url,
                    headers=self._get_headers(bypass_headers),
                    json=move_payload,
                    timeout=self._timeout,
                ),
            )

            if rename_response.ok:
                logger.info(
                    "sharepoint.rename.completed",
                    source=current_path,
                    destination=new_path,
                    moved=moved,
                    bypassed_lock=True,
                )
                return True

            if attempt < self.MAX_RETRIES:
                time.sleep(self.RETRY_DELAY_SECONDS)

        self._raise_for_status(rename_response, f"Renaming {current_path} to {new_path}")
        return False

    def delete_item(self, item_path: str) -> bool:
        """
        Delete an item from SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to delete.

        Returns:
            bool: True if the delete succeeded (Graph returns 204 No Content).

        Raises:
            SharePointNotFoundError: If the item does not exist.
            SharePointError: If the delete cannot be performed for any other reason.
        """
        item_path = self._ensure_leading_slash(item_path)

        # Resolve first so the audit record carries the item ID, not just a path string.
        item_data = self._get_item(item_path, f"Resolving {item_path} for delete")
        item_id = item_data.get("id")
        logger.debug("sharepoint.delete.resolved", path=item_path, item_id=item_id)

        site_id = self.get_site_id()
        item_url = self._build_graph_url(self._item_endpoint(site_id, item_path))

        # 'bypass-shared-lock' lets us delete a checked-out / locked file, mirroring upload.
        prefer = {"Prefer": "bypass-shared-lock"}
        response = self._session.delete(
            item_url, headers=self._get_headers(prefer), timeout=self._timeout
        )
        response = self.__handle_response_with_retry(
            response,
            lambda: self._session.delete(
                item_url, headers=self._get_headers(prefer), timeout=self._timeout
            ),
        )
        self._raise_for_status(response, f"Deleting {item_path}")

        logger.info("sharepoint.delete.completed", path=item_path, item_id=item_id)
        return True

    def get_web_url(self, item_path: str) -> str:
        """
        Get the web URL of an item in SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to get the web URL for

        Returns:
            str: The web URL of the item

        Raises:
            SharePointNotFoundError: If the item does not exist
            SharePointError: If the web URL is missing from the response
        """
        item_data = self._get_item(item_path, f"Getting web URL for {item_path}")
        web_url = item_data.get("webUrl")

        if not web_url:
            logger.error("sharepoint.web_url.missing", path=item_path)
            raise SharePointError(f"Web URL missing in response for {item_path}")

        logger.debug("sharepoint.web_url.resolved", path=item_path, url=web_url)
        return web_url
