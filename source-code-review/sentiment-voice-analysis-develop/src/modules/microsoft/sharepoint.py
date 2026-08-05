# Library imports
import os
import re
import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from msal import ConfidentialClientApplication

# Source code imports
from src.modules.security.tls import TlsPolicy
from src.utils.logger import Logger

logger = Logger(__name__)


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
    RETRY_STATUS_CODES = [423, 409]  # Locked, Conflict
    SUCCESS_STATUS_CODES = [200, 201]

    # HTTP Status Codes
    STATUS_UNAUTHORIZED = 401
    STATUS_NOT_FOUND = 404
    STATUS_LOCKED = 423
    STATUS_CONFLICT = 409
    STATUS_SERVICE_UNAVAILABLE = 503

    def __init__(self, **kwargs):
        """
        Initialize SharePoint module with authentication and site configuration.

        Parameters:
            client_id (str): Azure AD application client ID
            client_secret (str): Azure AD application client secret
            tenant_id (str): Azure AD tenant ID
            site_domain (str): SharePoint site domain
            site_path (str): SharePoint site path
            timezone (str, optional): Timezone for datetime operations. Defaults to 'Asia/Bangkok'
        """
        logger.info("Initializing SharePointModule...")

        # Authentication configuration
        self.client_id = kwargs.get("client_id")
        self.client_secret = kwargs.get("client_secret")
        self.tenant_id = kwargs.get("tenant_id")

        if not all([self.client_id, self.client_secret, self.tenant_id]):
            error_msg = "Missing required authentication parameters: client_id, client_secret, or tenant_id"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Site configuration
        self.site_domain = kwargs.get("site_domain")
        self.site_path = kwargs.get("site_path")

        if not all([self.site_domain, self.site_path]):
            error_msg = "Missing required site parameters: site_domain or site_path"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # Timezone configuration
        timezone_str = kwargs.get("timezone", "Asia/Bangkok")
        self.timezone = ZoneInfo(timezone_str)
        logger.debug(f"Using timezone: {timezone_str}")

        # Scopes for authentication
        self.scope = [self.GRAPH_SCOPE]

        # Cache for site_id to avoid repeated API calls
        self._site_id_cache: str | None = None

        # TLS 1.2+ pinned session, reused for all Graph calls and handed to MSAL token requests
        self._session = TlsPolicy().session()

        # Obtain access token
        logger.info("Obtaining access token...")
        self.access_token = self.__get_access_token()

        # Test connection to SharePoint site
        if not self._test_connection():
            error_msg = "Failed to connect to SharePoint site with provided configuration"
            logger.error(error_msg)
            raise ConnectionError(error_msg)

        logger.info(f"SharePointModule initialized successfully for site: {self.site_domain}{self.site_path}")

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

    def _build_graph_url(self, endpoint: str) -> str:
        """
        Build a complete Microsoft Graph API URL.

        Parameters:
            endpoint (str): The API endpoint path

        Returns:
            str: Complete Graph API URL
        """
        return f"{self.GRAPH_API_BASE}/{endpoint.lstrip('/')}"

    def _test_connection(self) -> bool:
        """
        Test the connection to the SharePoint site by retrieving the site ID.

        Returns:
            bool: True if connection is successful, False otherwise
        """
        logger.info(f"Testing connection to SharePoint site: {self.site_domain}{self.site_path}")

        try:
            site_id = self.get_site_id(use_cache=False)
            logger.info(f"Connection test successful. Site ID: {site_id}")
            return True
        except Exception as e:
            logger.error(f"Connection test failed: {e}", exc_info=True)
            return False

    def __get_access_token(self) -> str:
        """
        Obtain an access token for SharePoint using MSAL.

        Returns:
            str: Access token for SharePoint

        Raises:
            Exception: If token acquisition fails
        """
        logger.debug(f"Acquiring access token for tenant: {self.tenant_id}")

        try:
            authority_url = f"{self.LOGIN_AUTHORITY_BASE}/{self.tenant_id}"
            sharepoint_app = ConfidentialClientApplication(
                self.client_id,
                authority=authority_url,
                client_credential=self.client_secret,
                http_client=self._session,
            )

            logger.debug("Requesting token from Azure AD...")
            access_result = sharepoint_app.acquire_token_for_client(scopes=self.scope)

            if "access_token" in access_result:
                logger.info("Access token acquired successfully")
                return access_result["access_token"]
            error_description = access_result.get("error_description", access_result.get("error", "Unknown error"))
            logger.error(f"Failed to acquire token: {error_description}")
            raise Exception(f"Failed to acquire token: {error_description}")

        except Exception as e:
            logger.error(f"Error obtaining access token: {e}", exc_info=True)
            raise Exception(f"Error obtaining access token: {e}") from e

    def __refresh_access_token(self) -> None:
        """
        Refresh the access token.
        """
        logger.info("Refreshing access token...")
        self.access_token = self.__get_access_token()
        logger.debug("Access token refreshed successfully")

    def __handle_response_with_retry(self, response: requests.Response, retry_func, *args, **kwargs):
        """
        Handle response and retry with new token if unauthorized.

        Parameters:
            response: Initial HTTP response
            retry_func: Function to retry if token is refreshed
            *args: Arguments to pass to retry function
            **kwargs: Keyword arguments to pass to retry function

        Returns:
            requests.Response: The successful response
        """
        if response.status_code == self.STATUS_UNAUTHORIZED:
            logger.warning("Received 401 Unauthorized. Attempting to refresh token...")
            self.__refresh_access_token()
            logger.debug("Retrying request with new token...")
            return retry_func(*args, **kwargs)
        if response.status_code == self.STATUS_SERVICE_UNAVAILABLE:
            logger.warning("Received 503 Service Unavailable. Retrying after delay...")
            time.sleep(self.RETRY_DELAY_SECONDS)
            return retry_func(*args, **kwargs)
        return response

    def get_site_id(self, use_cache: bool = True) -> str:
        """
        Retrieve the SharePoint site ID with caching support.

        Parameters:
            use_cache (bool): Whether to use cached site_id if available

        Returns:
            str: The site ID

        Raises:
            Exception: If site ID cannot be retrieved
        """
        if use_cache and self._site_id_cache:
            logger.debug(f"Using cached site ID: {self._site_id_cache}")
            return self._site_id_cache

        logger.info(f"Retrieving site ID for {self.site_domain}{self.site_path}")

        try:
            site_endpoint = f"sites/{self.site_domain}:{self.site_path}"
            site_url = self._build_graph_url(site_endpoint)

            logger.debug(f"GET {site_url}")
            response = self._session.get(site_url, headers=self._get_headers())
            response = self.__handle_response_with_retry(
                response, lambda: self._session.get(site_url, headers=self._get_headers())
            )

            response.raise_for_status()
            site_id = response.json().get("id")

            if not site_id:
                error_msg = "Site ID not found in response"
                logger.error(error_msg)
                raise Exception(error_msg)

            self._site_id_cache = site_id
            logger.info(f"Site ID retrieved successfully: {site_id}")
            return site_id

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error retrieving site ID: {http_err.response.status_code} - {http_err.response.text}")
            raise http_err
        except Exception as e:
            logger.error(f"Error retrieving site ID: {e}", exc_info=True)
            raise Exception(f"Error retrieving site ID from SharePoint: {e}") from e

    def _list_files_current_directory(self, folder_path: str) -> list[dict[str, Any]]:
        """
        List all files in a SharePoint folder with pagination support.

        Parameters:
            folder_path (str): The path of the folder to list files from

        Returns:
            list: List of file/folder metadata dictionaries

        Raises:
            Exception: If files cannot be listed
        """
        logger.info(f"Listing files from folder: {folder_path}")

        try:
            site_id = self.get_site_id()
            folder_path = self._ensure_leading_slash(folder_path)

            list_endpoint = f"sites/{site_id}/drive/root:{folder_path}:/children"
            list_url = self._build_graph_url(list_endpoint)

            all_files = []
            page_count = 0

            while list_url:
                page_count += 1
                logger.debug(f"Fetching page {page_count}: {list_url}")

                response = self._session.get(list_url, headers=self._get_headers())
                response = self.__handle_response_with_retry(
                    response, lambda url=list_url: self._session.get(url, headers=self._get_headers())
                )

                response.raise_for_status()
                data = response.json()

                page_items = data.get("value", [])
                all_files.extend(page_items)
                logger.debug(f"Page {page_count}: Found {len(page_items)} items")

                list_url = data.get("@odata.nextLink")
                if list_url:
                    logger.debug("More pages available, continuing pagination...")

            logger.info(f"Successfully listed {len(all_files)} item(s) from {folder_path} ({page_count} page(s))")
            return all_files

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error listing files: {http_err.response.status_code} - {http_err.response.text}")
            raise http_err
        except Exception as e:
            logger.error(f"Error listing files from folder '{folder_path}': {e}", exc_info=True)
            raise Exception(f"Error listing files from SharePoint: {e}") from e

    def list_files(self, folder_path: str, recursive: bool = False) -> list[dict[str, Any]]:
        """
        List files in a SharePoint folder, optionally recursively.

        Parameters:
            folder_path (str): The path of the folder to list files from
            recursive (bool): Whether to list files recursively from subfolders
        Returns:
            list: List of file/folder metadata dictionaries
        """
        items = self._list_files_current_directory(folder_path)
        if not recursive:
            return items

        all_items = []
        for item in items:
            if item.get("folder"):  # If the item is a folder, list its contents recursively
                subfolder_path = f"{folder_path.rstrip('/')}/{item['name']}"
                all_items.extend(self.list_files(subfolder_path, recursive=True))
            else:
                all_items.append(item)
        return all_items

    def list_files_pattern(self, folder_path: str, pattern: str) -> list[str]:
        """
        List files in a SharePoint folder that match a specific regex pattern.

        Parameters:
            folder_path (str): The path of the folder to list files from
            pattern (str): The regex pattern to match file names
        Returns:
            list: List of file paths matching the pattern
        """
        logger.info(f"Listing files from folder: {folder_path} with pattern: {pattern}")
        matched_files = []

        try:
            all_files = self.list_files(folder_path)
            regex = re.compile(pattern)

            for item in all_files:
                item_name = item.get("name", "")
                item_path = item.get("parentReference", {}).get("path", "") + "/" + item_name
                if regex.match(item_name):
                    matched_files.append(item_path.replace("/drive/root:", ""))
                    logger.debug(f"Matched file: {item_name} at path: {item_path}")

            logger.info(f"Total matched files: {len(matched_files)}")
            return matched_files

        except Exception as e:
            logger.error(f"Error listing files with pattern '{pattern}' in folder '{folder_path}': {e}", exc_info=True)
            raise Exception(f"Error listing files with pattern from SharePoint: {e}") from e

    def get_item_by_path(self, item_path: str) -> requests.Response:
        """
        Retrieve and download an item from SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to retrieve

        Returns:
            requests.Response: The response containing the downloaded file content

        Raises:
            Exception: If item cannot be retrieved or downloaded
        """
        logger.info(f"Retrieving item: {item_path}")

        for attempt in range(1, self.MAX_RETRIES + 1):
            try:
                site_id = self.get_site_id()
                item_path = self._ensure_leading_slash(item_path)

                item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
                item_url = self._build_graph_url(item_endpoint)

                logger.debug(f"GET {item_url}")
                response = self._session.get(item_url, headers=self._get_headers())
                response = self.__handle_response_with_retry(
                    response, lambda _u=item_url: self._session.get(_u, headers=self._get_headers())
                )

                response.raise_for_status()
                item_data = response.json()

                download_url = item_data.get("@microsoft.graph.downloadUrl")
                if not download_url:
                    error_msg = "Download URL not found in response"
                    logger.error(error_msg)
                    raise Exception(error_msg)

                file_size = item_data.get("size", "unknown")
                logger.debug(f"Downloading file (size: {file_size} bytes)...")

                download_res = self._session.get(download_url)
                download_res = self.__handle_response_with_retry(
                    download_res, lambda _u=download_url: self._session.get(_u)
                )

                download_res.raise_for_status()
                logger.info(f"Successfully retrieved item: {item_path} ({len(download_res.content)} bytes)")
                return download_res

            except requests.exceptions.ConnectionError as conn_err:
                if attempt < self.MAX_RETRIES:
                    logger.warning(
                        f"Connection lost while retrieving item: {item_path}. Retrying attempt {attempt + 1}..."
                    )
                    time.sleep(self.RETRY_CONNECTION_ERRORS_SECONDS)
                else:
                    logger.error(f"Error connection lost while retrieving item: {item_path}: {conn_err}", exc_info=True)
                    raise Exception(
                        f"Error connection lost while retrieving item from SharePoint: {conn_err}"
                    ) from conn_err
            except requests.exceptions.Timeout as timeout_err:
                if attempt < self.MAX_RETRIES:
                    logger.warning(
                        f"Request timed out while retrieving item: {item_path}. Retrying attempt {attempt + 1}..."
                    )
                    time.sleep(self.RETRY_CONNECTION_ERRORS_SECONDS)
                else:
                    logger.error(
                        f"Error request timed out while retrieving item: {item_path}: {timeout_err}", exc_info=True
                    )
                    raise Exception(
                        f"Error request timed out while retrieving item from SharePoint: {timeout_err}"
                    ) from timeout_err
            except requests.exceptions.ChunkedEncodingError as chunkencode_err:
                if attempt < self.MAX_RETRIES:
                    logger.warning(
                        f"Request chunk encode error while retrieving item: {item_path}. "
                        f"Retrying attempt {attempt + 1}..."
                    )
                    time.sleep(self.RETRY_CONNECTION_ERRORS_SECONDS)
                else:
                    logger.error(
                        f"Error request Request chunk encode error while retrieving item: {item_path}: "
                        f"{chunkencode_err}",
                        exc_info=True,
                    )
                    raise Exception(
                        f"Error request Request chunk encode error while retrieving item from SharePoint: "
                        f"{chunkencode_err}"
                    ) from chunkencode_err
            except requests.exceptions.HTTPError as http_err:
                logger.error(f"HTTP error retrieving item: {http_err.response.status_code} - {http_err.response.text}")
                raise http_err
            except Exception as e:
                logger.error(f"Error retrieving item '{item_path}': {e}", exc_info=True)
                raise Exception(f"Error retrieving item from SharePoint: {e}") from e

        return None

    def is_item_exists(self, item_path: str) -> bool:
        """
        Check if an item exists in SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to check

        Returns:
            bool: True if the item exists, False otherwise

        Raises:
            Exception: If the check fails (excluding 404 not found)
        """
        logger.debug(f"Checking if item exists: {item_path}")

        try:
            site_id = self.get_site_id()
            item_path = self._ensure_leading_slash(item_path)

            item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
            item_url = self._build_graph_url(item_endpoint)

            response = self._session.get(item_url, headers=self._get_headers())
            response = self.__handle_response_with_retry(
                response, lambda: self._session.get(item_url, headers=self._get_headers())
            )

            if response.status_code == self.SUCCESS_STATUS_CODES[0]:  # 200
                logger.debug(f"Item exists: {item_path}")
                return True
            if response.status_code == self.STATUS_NOT_FOUND:  # 404
                logger.debug(f"Item does not exist: {item_path}")
                return False
            response.raise_for_status()
            return False

        except requests.exceptions.HTTPError as http_err:
            logger.error(
                f"HTTP error checking item existence: {http_err.response.status_code} - {http_err.response.text}"
            )
            raise http_err
        except Exception as e:
            logger.error(f"Error checking if item exists '{item_path}': {e}", exc_info=True)
            raise Exception(f"Error checking item existence in SharePoint: {e}") from e

    def _archive_locked_file(self, site_id: str, item_path: str, headers: dict[str, str]) -> bool:
        """
        Archive a locked file by renaming it with a timestamp.

        Parameters:
            site_id (str): The SharePoint site ID
            item_path (str): The path of the item to archive
            headers (dict): HTTP headers for the request

        Returns:
            bool: True if archiving succeeded, False otherwise
        """
        logger.info(f"Attempting to archive locked file: {item_path}")

        try:
            item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
            item_url = self._build_graph_url(item_endpoint)

            item_response = self._session.get(item_url, headers=headers)
            item_response = self.__handle_response_with_retry(
                item_response, lambda: self._session.get(item_url, headers=headers)
            )

            if item_response.status_code != self.SUCCESS_STATUS_CODES[0]:
                logger.error(f"Failed to get item metadata. Status: {item_response.status_code}")
                return False

            item_data = item_response.json()
            item_id = item_data.get("id")
            item_name = item_data.get("name")
            parent_folder_id = item_data.get("parentReference", {}).get("id")
            drive_id = item_data.get("parentReference", {}).get("driveId")

            if not all([item_id, item_name, parent_folder_id, drive_id]):
                logger.error("Missing required metadata for archiving")
                return False

            # Generate archive name with timestamp
            timestamp = datetime.now(tz=self.timezone).strftime("%Y%m%d%H%M%S")
            archive_name = f"archive_{timestamp}_{item_name}"

            logger.debug(f"Renaming '{item_name}' to '{archive_name}'")

            move_endpoint = f"drives/{drive_id}/items/{item_id}"
            move_url = self._build_graph_url(move_endpoint)

            move_payload = {"parentReference": {"id": parent_folder_id}, "name": archive_name}

            move_headers = self._get_headers({"Content-Type": "application/json", "Prefer": "bypass-shared-lock"})

            rename_response = self._session.patch(move_url, headers=move_headers, json=move_payload)
            rename_response = self.__handle_response_with_retry(
                rename_response,
                lambda: self._session.patch(
                    move_url,
                    headers=self._get_headers({"Content-Type": "application/json", "Prefer": "bypass-shared-lock"}),
                    json=move_payload,
                ),
            )

            if rename_response.status_code == self.SUCCESS_STATUS_CODES[0]:
                logger.info(f"Successfully archived file to: {archive_name}")
                return True
            logger.error(
                f"Failed to archive file. Status: {rename_response.status_code}, Response: {rename_response.text}"
            )
            return False

        except Exception as e:
            logger.error(f"Error archiving locked file: {e}", exc_info=True)
            return False

    def upload_file(self, upload_path: str, content: bytes) -> requests.Response:
        """
        Upload a file to SharePoint at the specified path with retry logic.
        Handles locked files by archiving them before retrying.

        Parameters:
            upload_path (str): The path where the file will be uploaded
            content (bytes): The binary data of the file to upload

        Returns:
            requests.Response: The response from the successful upload operation

        Raises:
            Exception: If upload fails after all retry attempts
        """
        file_size_mb = len(content) / (1024 * 1024)
        logger.info(f"Uploading file to: {upload_path} (size: {file_size_mb:.2f} MB)")

        try:
            site_id = self.get_site_id()
            upload_path = self._ensure_leading_slash(upload_path)

            upload_endpoint = f"sites/{site_id}/drive/root:{upload_path}:/content"
            upload_url = self._build_graph_url(upload_endpoint)

            for attempt in range(1, self.MAX_RETRIES + 1):
                logger.debug(f"Upload attempt {attempt}/{self.MAX_RETRIES}")

                response = self._session.put(upload_url, headers=self._get_headers(), data=content)
                response = self.__handle_response_with_retry(
                    response, lambda: self._session.put(upload_url, headers=self._get_headers(), data=content)
                )

                # Success
                if response.status_code in self.SUCCESS_STATUS_CODES:
                    logger.info(f"File uploaded successfully: {upload_path}")
                    return response

                # Handle locked or conflict status
                if response.status_code in self.RETRY_STATUS_CODES:
                    logger.warning(
                        f"Upload failed with status {response.status_code} (attempt {attempt}/{self.MAX_RETRIES})"
                    )

                    # Try to archive the existing file
                    if self._archive_locked_file(site_id, upload_path, self._get_headers()):
                        logger.info("Retrying upload after archiving existing file...")
                        response = self._session.put(upload_url, headers=self._get_headers(), data=content)
                        response = self.__handle_response_with_retry(
                            response, lambda: self._session.put(upload_url, headers=self._get_headers(), data=content)
                        )

                        if response.status_code in self.SUCCESS_STATUS_CODES:
                            logger.info(f"File uploaded successfully after archiving: {upload_path}")
                            return response

                    # Wait before retry if not last attempt
                    if response.status_code == self.STATUS_LOCKED and attempt < self.MAX_RETRIES:
                        logger.warning(f"Resource locked. Waiting {self.RETRY_DELAY_SECONDS}s before retry...")
                        time.sleep(self.RETRY_DELAY_SECONDS)
                        continue

                # If we're here and not continuing, raise the error
                response.raise_for_status()

            # If all retries exhausted
            if response:
                logger.error(f"Upload failed after {self.MAX_RETRIES} attempts. Status: {response.status_code}")
                response.raise_for_status()

            return response

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error uploading file: {http_err.response.status_code} - {http_err.response.text}")
            raise http_err
        except Exception as e:
            logger.error(f"Error uploading file '{upload_path}': {e}", exc_info=True)
            raise Exception(f"Error uploading file to SharePoint: {e}") from e

    # noqa justification: `timeout` is kept for interface compatibility with callers; unused internally.
    def copy_file(self, source_path: str, destination_path: str, timeout: int = 60) -> bool:  # noqa: ARG002
        """
        Copy a file within SharePoint from source path to destination path.
        Uses download + upload approach for reliability.

        Parameters:
            source_path (str): The path of the source file to copy
            destination_path (str): The path where the file will be copied to
            timeout (int): Maximum time in seconds to wait for copy operation

        Returns:
            bool: True if copy was successful, False otherwise

        Note:
            This implementation uses download + upload rather than the Graph API /copy endpoint
            because the /copy endpoint is asynchronous and has issues with same-folder copies.
        """
        logger.info(f"Copying file from '{source_path}' to '{destination_path}'")

        try:
            # Download source file content
            logger.debug(f"Downloading source file: {source_path}")
            source_response = self.get_item_by_path(source_path)

            if not source_response or not source_response.content:
                logger.error(f"Failed to download source file: {source_path}")
                return False

            file_size_mb = len(source_response.content) / (1024 * 1024)
            logger.debug(f"Downloaded {file_size_mb:.2f} MB from source")

            # Upload to destination
            logger.debug(f"Uploading to destination: {destination_path}")
            upload_response = self.upload_file(destination_path, source_response.content)

            if upload_response and upload_response.status_code in self.SUCCESS_STATUS_CODES:
                logger.info(f"File copied successfully to: {destination_path}")
                return True
            logger.error(
                f"Upload to destination failed. Status: {upload_response.status_code if upload_response else 'None'}"
            )
            return False

        except Exception as e:
            logger.error(f"Error copying file from '{source_path}' to '{destination_path}': {e}", exc_info=True)
            return False

    def rename_file(self, current_path: str, new_path: str) -> bool:
        """
        Renames an existing file on SharePoint by fetching its metadata and
        updating its name via item ID. Includes specialized logic to handle
        files locked (423) by active users.

        Parameters:
            current_path (str): The current path of the file on SharePoint
            new_path (str): The new path for the file on SharePoint

        Returns:
            bool: True if renaming succeeded, False otherwise
        """
        logger.info(f"Attempting to rename file at '{current_path}' to '{new_path}'")
        headers = self._get_headers()

        try:
            # 1. Fetch item metadata via path
            site_id = self.get_site_id()
            item_path = self._ensure_leading_slash(current_path)
            item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
            item_url = self._build_graph_url(item_endpoint)

            item_response = requests.get(item_url, headers=headers)
            item_response = self.__handle_response_with_retry(
                item_response, lambda: requests.get(item_url, headers=headers)
            )

            if item_response.status_code != self.SUCCESS_STATUS_CODES[0]:
                logger.error(f"Failed to get item metadata for renaming. Status: {item_response.status_code}")
                return False

            item_data = item_response.json()
            item_id = item_data.get("id")
            parent_folder_id = item_data.get("parentReference", {}).get("id")
            drive_id = item_data.get("parentReference", {}).get("driveId")

            if not all([item_id, parent_folder_id, drive_id]):
                logger.error("Missing required metadata for renaming")
                return False

            # 2. Build the execution payload
            move_endpoint = f"drives/{drive_id}/items/{item_id}"
            move_url = self._build_graph_url(move_endpoint)

            move_payload = {"parentReference": {"id": parent_folder_id}, "name": os.path.basename(new_path)}

            # Define base configuration headers
            patch_headers = {"Content-Type": "application/json"}

            # Loop attempts for handling persistent locks/retries
            for attempt in range(1, self.MAX_RETRIES + 1):
                logger.debug(f"Rename attempt {attempt}/{self.MAX_RETRIES}")

                # Form standard headers first
                current_headers = self._get_headers(patch_headers)

                rename_response = requests.patch(move_url, headers=current_headers, json=move_payload)
                rename_response = self.__handle_response_with_retry(
                    rename_response,
                    lambda: requests.patch(move_url, headers=self._get_headers(patch_headers), json=move_payload),
                )

                # Success Case
                if rename_response.status_code == self.SUCCESS_STATUS_CODES[0]:
                    logger.info(f"Successfully renamed file to: {new_path}")
                    return True

                # Lock Handling Case (423 Locked or matching RETRY_STATUS_CODES)
                if rename_response.status_code == 423 or rename_response.status_code in self.RETRY_STATUS_CODES:
                    logger.warning(
                        f"File rename locked/conflicted with status {rename_response.status_code}. "
                        f"Injecting lock bypass..."
                    )

                    # Add lock bypass directive to the headers
                    lock_bypass_headers = self._get_headers(
                        {"Content-Type": "application/json", "Prefer": "bypass-shared-lock"}
                    )

                    # Execute lock-bypassed patching
                    rename_response = requests.patch(move_url, headers=lock_bypass_headers, json=move_payload)
                    rename_response = self.__handle_response_with_retry(
                        rename_response,
                        lambda: requests.patch(
                            move_url,
                            headers=self._get_headers(
                                {"Content-Type": "application/json", "Prefer": "bypass-shared-lock"}
                            ),
                            json=move_payload,
                        ),
                    )

                    if rename_response.status_code == self.SUCCESS_STATUS_CODES[0]:
                        logger.info(f"Successfully renamed locked file via bypass context: {new_path}")
                        return True

                    # If still locked (e.g., rigid exclusive checkout lock) and we have retries remaining, wait
                    if attempt < self.MAX_RETRIES:
                        logger.warning(
                            f"Resource persistently locked. Waiting {self.RETRY_DELAY_SECONDS}s "
                            f"before next execution pass..."
                        )
                        time.sleep(self.RETRY_DELAY_SECONDS)
                        continue

            # If loop exhausts without matching success criteria
            logger.error(
                f"Rename failed after {self.MAX_RETRIES} attempts. Final Status: {rename_response.status_code}"
            )
            return False

        except Exception as e:
            logger.error(f"Error executing rename layout on '{current_path}': {e}", exc_info=True)
            return False

    def delete_item(self, item_path: str) -> bool:
        """
        Delete an item from SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to delete.

        Returns:
            bool: True if the delete succeeded (Graph returns 204 No Content).

        Raises:
            requests.HTTPError: If the delete fails with a non-success status.
            Exception: If the delete cannot be performed for any other reason.
        """
        logger.info(f"Deleting item: {item_path}")

        try:
            site_id = self.get_site_id()
            item_path = self._ensure_leading_slash(item_path)

            item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
            item_url = self._build_graph_url(item_endpoint)

            # 'bypass-shared-lock' lets us delete a checked-out / locked file, mirroring upload.
            prefer = {"Prefer": "bypass-shared-lock"}
            response = self._session.delete(item_url, headers=self._get_headers(prefer))
            response = self.__handle_response_with_retry(
                response, lambda: self._session.delete(item_url, headers=self._get_headers(prefer))
            )

            if response.ok:  # Graph DELETE returns 204 No Content on success
                logger.info(f"Successfully deleted item: {item_path}")
                return True
            response.raise_for_status()
            return False

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error deleting item: {http_err.response.status_code} - {http_err.response.text}")
            raise http_err
        except Exception as e:
            logger.error(f"Error deleting item '{item_path}': {e}", exc_info=True)
            raise Exception(f"Error deleting item from SharePoint: {e}") from e

    def get_web_url(self, item_path: str) -> str:
        """
        Get the web URL of an item in SharePoint by specified path.

        Parameters:
            item_path (str): The path of the item to get the web URL for
        Returns:
            str: The web URL of the item
        Raises:
            Exception: If the web URL cannot be retrieved
        """
        logger.info(f"Getting web URL for item: {item_path}")

        try:
            site_id = self.get_site_id()
            item_path = self._ensure_leading_slash(item_path)

            item_endpoint = f"sites/{site_id}/drive/root:{item_path}"
            item_url = self._build_graph_url(item_endpoint)

            response = self._session.get(item_url, headers=self._get_headers())
            response = self.__handle_response_with_retry(
                response, lambda: self._session.get(item_url, headers=self._get_headers())
            )

            response.raise_for_status()
            item_data = response.json()
            web_url = item_data.get("webUrl")

            if not web_url:
                error_msg = "Web URL not found in response"
                logger.error(error_msg)
                raise Exception(error_msg)

            logger.info(f"Web URL retrieved successfully: {web_url}")
            return web_url

        except requests.exceptions.HTTPError as http_err:
            logger.error(f"HTTP error getting web URL: {http_err.response.status_code} - {http_err.response.text}")
            raise http_err
        except Exception as e:
            logger.error(f"Error getting web URL for item '{item_path}': {e}", exc_info=True)
            raise Exception(f"Error getting web URL from SharePoint: {e}") from e
