"""SharePointModule — compatibility shim for FactCheckerModule.

Delegates all operations to ``SharePointService``.
New code should use ``SharePointService`` directly.
"""
from __future__ import annotations

from zoneinfo import ZoneInfo

import requests as _requests

from app.services.sharepoint_service import SharePointService
from app.share_log import get_logger

logger = get_logger(__name__)


class SharePointModule:
    """Thin wrapper that delegates to SharePointService.

    Constructor kwargs are identical to the original class so FactCheckerModule
    requires no changes.
    """

    def __init__(self, **kwargs: str) -> None:
        self.scope = ["https://graph.microsoft.com/.default"]
        self.timezone = ZoneInfo("Asia/Bangkok")
        self.client_id: str = kwargs.get("client_id", "")
        self.client_secret: str = kwargs.get("client_secret", "")
        self.tenant_id: str = kwargs.get("tenant_id", "")
        self.site_domain: str = kwargs.get("site_domain", "")
        self.site_path: str = kwargs.get("site_path", "")

        self._service = SharePointService(
            client_id=self.client_id,
            client_secret=self.client_secret,
            tenant_id=self.tenant_id,
            site_domain=self.site_domain,
            site_path=self.site_path,
        )
        # Keep access_token accessible for code that reads it directly
        self.access_token: str = self._service._access_token
        logger.info(f"SharePointModule (shim) initialised for {self.site_domain}{self.site_path}")

    # ------------------------------------------------------------------
    # Delegated methods (same signatures as original)
    # ------------------------------------------------------------------

    def get_site_id(self) -> str:
        return self._service._get_site_id()

    def list_files(self, folder_path: str) -> list:
        return self._service.list_items(folder_path)

    def list_folders(self, folder_path: str) -> list:
        return self._service.list_folders(folder_path)

    def get_item_by_path(self, item_path: str) -> _requests.Response:
        """Download item at *item_path* and return a Response-like object.

        Returns a ``requests.Response`` whose ``.content`` is the raw file bytes,
        matching the original interface used by FactCheckerModule.
        """
        content = self._service.download_file(item_path)
        # Construct a minimal Response-compatible object
        mock = _requests.Response()
        mock._content = content
        mock.status_code = 200
        return mock

    def check_item_exists(self, item_path: str) -> bool:
        return self._service.item_exists(item_path)

    def upload_file(self, upload_path: str, content: bytes) -> _requests.Response:
        self._service.upload_file(upload_path, content)
        mock = _requests.Response()
        mock.status_code = 200
        return mock
