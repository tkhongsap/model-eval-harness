"""Unified SharePoint service — one class, one site per instance.

Replaces both ``app/sharepoint.py`` (procedural, two-site string dispatch) and
``app/modules/sharepoint.py`` (class-based, single site).  Create two instances
in ``main.py`` — one for the Fraud site, one for the Control site — instead of
passing ``site_name="fraud"/"control"`` strings.
"""
from __future__ import annotations
import polars as pl
from datetime import datetime
from zoneinfo import ZoneInfo
from io import BytesIO
import io
import requests
from msal import ConfidentialClientApplication

from app.share_log import get_logger
from app.crypto.decrypt_batchRTR import decrypt_hybrid

logger = get_logger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_SCOPE = ["https://graph.microsoft.com/.default"]


class SharePointService:
    """Microsoft Graph API client scoped to a single SharePoint site."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        tenant_id: str,
        site_domain: str,
        site_path: str,
        base_root: str = "",
        timezone: ZoneInfo | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.tenant_id = tenant_id
        self.site_domain = site_domain
        self.site_path = site_path
        self.base_root = base_root
        self._timezone = timezone or ZoneInfo("Asia/Bangkok")
        self._access_token: str = self._acquire_token()
        logger.info(f"SharePointService initialised for {site_domain}{site_path}")

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _acquire_token(self) -> str:
        app = ConfidentialClientApplication(
            self.client_id,
            authority=f"https://login.microsoftonline.com/{self.tenant_id}",
            client_credential=self.client_secret,
        )
        result = app.acquire_token_for_client(scopes=_SCOPE)
        if "access_token" not in result:
            raise RuntimeError(
                f"Failed to acquire token: {result.get('error_description', result.get('error'))}"
            )
        return result["access_token"]

    def _refresh_if_401(self, response: requests.Response) -> bool:
        """Refresh the access token when a 401 is returned."""
        if response.status_code == 401:
            logger.info("Access token expired — refreshing…")
            self._access_token = self._acquire_token()
            return True
        return False

    @property
    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._access_token}"}

    # ------------------------------------------------------------------
    # Site / drive resolution (cached lazily)
    # ------------------------------------------------------------------

    def _get_site_id(self) -> str:
        url = f"{_GRAPH_BASE}/sites/{self.site_domain}:{self.site_path}"
        resp = requests.get(url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(url, headers=self._auth_headers)
        resp.raise_for_status()
        site_id = resp.json().get("id")
        if not site_id:
            raise RuntimeError("site_id not found in Graph API response")
        return site_id

    def _get_drive_id(self, site_id: str) -> str:
        url = f"{_GRAPH_BASE}/sites/{site_id}/drive"
        resp = requests.get(url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(url, headers=self._auth_headers)
        resp.raise_for_status()
        return resp.json()["id"]

    # ------------------------------------------------------------------
    # Listing
    # ------------------------------------------------------------------

    def list_items(self, folder_path: str) -> list[dict]:
        """Return all items (files + folders) under *folder_path* with pagination."""
        site_id = self._get_site_id()
        if not folder_path.startswith("/"):
            folder_path = f"/{folder_path}"
        url: str | None = (
            f"{_GRAPH_BASE}/sites/{site_id}/drive/root:{folder_path}:/children"
        )
        items: list[dict] = []
        while url:
            resp = requests.get(url, headers=self._auth_headers)
            if self._refresh_if_401(resp):
                resp = requests.get(url, headers=self._auth_headers)
            resp.raise_for_status()
            data = resp.json()
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    def list_files(self, folder_path: str) -> list[dict]:
        return [i for i in self.list_items(folder_path) if "folder" not in i]

    def list_folders(self, folder_path: str) -> list[dict]:
        return [i for i in self.list_items(folder_path) if "folder" in i]

    def list_folder_names(self, folder_path: str) -> list[str]:
        return [f["name"] for f in self.list_folders(folder_path)]

    # ------------------------------------------------------------------
    # Item resolution
    # ------------------------------------------------------------------

    def get_item_metadata(self, item_path: str) -> dict:
        """Return raw Graph API item dict for *item_path*."""
        site_id = self._get_site_id()
        if not item_path.startswith("/"):
            item_path = f"/{item_path}"
        url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:{item_path}"
        resp = requests.get(url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(url, headers=self._auth_headers)
        resp.raise_for_status()
        return resp.json()

    def get_item_id(self, item_path: str) -> str | None:
        """Return item ID or ``None`` if not found (404)."""
        try:
            return self.get_item_metadata(item_path).get("id")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def get_download_url(self, item_path: str) -> str | None:
        """Return the direct download URL or ``None`` on 404."""
        try:
            return self.get_item_metadata(item_path).get("@microsoft.graph.downloadUrl")
        except requests.exceptions.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def item_exists(self, item_path: str) -> bool:
        return self.get_item_id(item_path) is not None

    # ------------------------------------------------------------------
    # Download
    # ------------------------------------------------------------------

    def download_file(self, item_path: str) -> bytes:
        """Download *item_path* and return its raw bytes."""
        site_id = self._get_site_id()
        if not item_path.startswith("/"):
            item_path = f"/{item_path}"
        url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:{item_path}:/content"
        resp = requests.get(url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(url, headers=self._auth_headers)
        resp.raise_for_status()
        return resp.content

    def download_file_by_id(self, item_id: str) -> bytes:
        site_id = self._get_site_id()
        drive_id = self._get_drive_id(site_id)
        meta_url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}"
        meta_resp = requests.get(meta_url, headers=self._auth_headers)
        if self._refresh_if_401(meta_resp):
            meta_resp = requests.get(meta_url, headers=self._auth_headers)
        meta_resp.raise_for_status()
        download_url = meta_resp.json().get("@microsoft.graph.downloadUrl")
        if not download_url:
            raise RuntimeError(f"Download URL not found for item {item_id}")
        content_resp = requests.get(download_url)
        content_resp.raise_for_status()
        return content_resp.content

    # ------------------------------------------------------------------
    # Upload
    # ------------------------------------------------------------------

    def upload_file(self, upload_path: str, content: bytes, max_retries: int = 3) -> None:
        """Upload *content* to *upload_path*.

        On 423 (file locked) the existing file is archived and the upload is retried.
        """
        site_id = self._get_site_id()
        if not upload_path.startswith("/"):
            upload_path = f"/{upload_path}"

        url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:{upload_path}:/content"
        headers = {**self._auth_headers, "Content-Type": "application/octet-stream"}

        for _attempt in range(max_retries):
            resp = requests.put(url, headers=headers, data=content)
            if self._refresh_if_401(resp):
                headers["Authorization"] = f"Bearer {self._access_token}"
                resp = requests.put(url, headers=headers, data=content) 

            if resp.status_code in (200, 201):
                logger.info(f"Uploaded: {upload_path}")
                return

            if resp.status_code in (423, 409):
                logger.warning(f"File locked ({resp.status_code}) — attempting archive then retry.")
                self._archive_locked_file(site_id, upload_path)
                continue

            resp.raise_for_status()

        raise RuntimeError(f"Upload failed after {max_retries} attempts: {upload_path}")

    def _archive_locked_file(self, site_id: str, item_path: str) -> None:
        """Rename a locked file in-place so we can upload a fresh copy."""
        try:
            meta = self.get_item_metadata(item_path)
            item_id = meta.get("id")
            item_name = meta.get("name", "file")
            parent_id = meta.get("parentReference", {}).get("id")
            drive_id = meta.get("parentReference", {}).get("driveId")
            if not (item_id and parent_id and drive_id):
                return
            timestamp = datetime.now(tz=self._timezone).strftime("%Y%m%d%H%M%S")
            archive_name = f"archive_{timestamp}_{item_name}"
            move_url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{item_id}"
            move_headers = {
                **self._auth_headers,
                "Content-Type": "application/json",
                "Prefer": "bypass-shared-lock",
            }
            payload = {"parentReference": {"id": parent_id}, "name": archive_name}
            move_resp = requests.patch(move_url, headers=move_headers, json=payload)
            if self._refresh_if_401(move_resp):
                move_headers["Authorization"] = f"Bearer {self._access_token}"
                move_resp = requests.patch(move_url, headers=move_headers, json=payload)
            if move_resp.status_code == 200:
                logger.info(f"Archived locked file to: {archive_name}")
            else:
                logger.error(f"Failed to archive locked file: {move_resp.status_code}")
        except Exception as exc:
            logger.error(f"Error archiving locked file: {exc}")

    # ------------------------------------------------------------------
    # Move / archive
    # ------------------------------------------------------------------

    def move_file(
        self,
        file_id: str,
        destination_folder_id: str,
        new_name: str | None = None,
    ) -> bool:
        site_id = self._get_site_id()
        drive_id = self._get_drive_id(site_id)
        move_url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{file_id}"
        headers = {
            **self._auth_headers,
            "Content-Type": "application/json",
            "Prefer": "bypass-shared-lock",
        }
        payload: dict = {"parentReference": {"id": destination_folder_id}}
        if new_name:
            payload["name"] = new_name
        resp = requests.patch(move_url, headers=headers, json=payload)
        if self._refresh_if_401(resp):
            resp = requests.patch(move_url, headers=headers, json=payload)
        resp.raise_for_status()
        return True

    def ensure_folder(self, parent_path: str, folder_name: str) -> str:
        """Create *folder_name* under *parent_path* if it does not exist.

        Returns the folder item ID.
        """
        full_path = f"{parent_path}/{folder_name}"
        folder_id = self.get_item_id(full_path)
        if folder_id:
            return folder_id

        # Create it
        site_id = self._get_site_id()
        drive_id = self._get_drive_id(site_id)
        parent_id = self.get_item_id(parent_path)
        if not parent_id:
            raise RuntimeError(f"Parent folder not found: {parent_path}")

        create_url = f"{_GRAPH_BASE}/drives/{drive_id}/items/{parent_id}/children"
        headers = {**self._auth_headers, "Content-Type": "application/json"}
        resp = requests.post(create_url, headers=headers, json={"name": folder_name, "folder": {}})
        if self._refresh_if_401(resp):
            resp = requests.post(create_url, headers=headers, json={"name": folder_name, "folder": {}})
        resp.raise_for_status()
        logger.info(f"Created folder: {full_path}")
        return resp.json()["id"]

    def move_to_archive(
        self, file_name: str, file_folder_path: str, archive_parent_path: str
    ) -> bool:
        """Move *file_name* from *file_folder_path* into a monthly archive subfolder."""
        try:
            monthly_name = datetime.now().strftime("%Y%m")
            archive_folder_id = self.ensure_folder(archive_parent_path, monthly_name)
            file_id = self.get_item_id(f"{file_folder_path}/{file_name}")
            if not file_id:
                logger.warning(f"File not found for archive move: {file_folder_path}/{file_name}")
                return False
            return self.move_file(file_id, archive_folder_id, new_name=file_name)
        except Exception as exc:
            logger.error(f"move_to_archive failed: {exc}")
            return False

    # ------------------------------------------------------------------
    # GCS bridge (ingest helper)
    # ------------------------------------------------------------------

    def download_with_backup(
        self,
        file_folder_path: str,
        file_name: str,
        backup_folder_path: str,
    ) -> bytes:
        """Download *file_name*, create a timestamped backup copy, and return the bytes."""
        site_id = self._get_site_id()
        # Build the full path including base_root
        full_path = f"{self.base_root}/{file_folder_path}/{file_name}" if self.base_root else f"{file_folder_path}/{file_name}"
        item_url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:/{full_path}:/content"
        resp = requests.get(item_url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(item_url, headers=self._auth_headers)
        resp.raise_for_status()
        item_bytes = resp.content
        logger.info(f"Downloaded from SharePoint: {file_name}")

        # Backup copy with datestamp
        bk_name = f"{file_name.rsplit('.', 1)[0]}_bk_{datetime.now().strftime('%m%d')}.{file_name.rsplit('.', 1)[-1]}"
        bk_full = f"{self.base_root}/{backup_folder_path}/{bk_name}" if self.base_root else f"{backup_folder_path}/{bk_name}"
        bk_url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:/{bk_full}:/content"
        bk_resp = requests.put(bk_url, data=item_bytes, headers=self._auth_headers)
        bk_resp.raise_for_status()
        logger.info(f"Backed up to SharePoint: {bk_name}")

        return item_bytes

    # ------------------------------------------------------------------
    # decrypted
    # ------------------------------------------------------------------
    def decrypt(
        self,
        initial_headers: list,
        file_folder_path: str,
        file_name: str,
        rsa_private_key: str,
        ) -> None:
        "perform decryption of encrypted file"
        site_id = self._get_site_id()

        # Build the full path including base_root
        full_path = f"{self.base_root}/{file_folder_path}/{file_name}" if self.base_root else f"{file_folder_path}/{file_name}"
        item_url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:/{full_path}:/content"
        resp = requests.get(item_url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(item_url, headers=self._auth_headers)
        resp.raise_for_status()
        item_bytes = resp.content
        file_stream = BytesIO(item_bytes)
        content_byte = decrypt_hybrid(file_stream, rsa_private_key)

        initial_df = pl.read_csv(
            io.StringIO(content_byte.decode("utf-8")),
            separator="|",     
            has_header=True,
            new_columns=initial_headers,           
            ignore_errors=True,
            truncate_ragged_lines=True,
            infer_schema_length=1000
        )

        csv_text = initial_df.write_csv()
        csv_bytes = csv_text.encode("utf-8-sig")
        csv_buffer = io.BytesIO(csv_bytes)
        csv_buffer.seek(0)
        return csv_buffer

    def read_bytes(
        self,
        headers: list,
        file_folder_path: str,
        file_name: str,
    ) -> bytes:

        "perform decryption of encrypted file"
        site_id = self._get_site_id()

        full_path = f"{self.base_root}/{file_folder_path}/{file_name}" if self.base_root else f"{file_folder_path}/{file_name}"
        item_url = f"{_GRAPH_BASE}/sites/{site_id}/drive/root:/{full_path}:/content"
        resp = requests.get(item_url, headers=self._auth_headers)
        if self._refresh_if_401(resp):
            resp = requests.get(item_url, headers=self._auth_headers)
        resp.raise_for_status()

        return resp.content