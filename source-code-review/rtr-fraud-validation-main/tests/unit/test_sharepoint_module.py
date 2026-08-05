"""Unit tests for app/modules/sharepoint.py — SharePointModule shim."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.modules.sharepoint import SharePointModule


# ---------------------------------------------------------------------------
# Fixture: patch SharePointService so no real HTTP is made
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_sp_service() -> MagicMock:
    """Return a MagicMock SharePointService and patch the constructor."""
    mock_svc = MagicMock()
    mock_svc._access_token = "test_token"
    return mock_svc


@pytest.fixture()
def module(mock_sp_service: MagicMock) -> SharePointModule:
    with patch("app.modules.sharepoint.SharePointService", return_value=mock_sp_service):
        return SharePointModule(
            client_id="cid",
            client_secret="csec",
            tenant_id="tid",
            site_domain="example.sharepoint.com",
            site_path="/sites/test",
        )


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_stores_credentials(self, mock_sp_service: MagicMock) -> None:
        with patch("app.modules.sharepoint.SharePointService", return_value=mock_sp_service):
            m = SharePointModule(
                client_id="cid",
                client_secret="csec",
                tenant_id="tid",
                site_domain="example.sharepoint.com",
                site_path="/sites/test",
            )
        assert m.client_id == "cid"
        assert m.tenant_id == "tid"
        assert m.site_domain == "example.sharepoint.com"

    def test_access_token_exposed(self, module: SharePointModule) -> None:
        assert module.access_token == "test_token"

    def test_scope_hardcoded(self, module: SharePointModule) -> None:
        assert module.scope == ["https://graph.microsoft.com/.default"]

    def test_sharepoint_service_constructed_with_kwargs(self, mock_sp_service: MagicMock) -> None:
        with patch(
            "app.modules.sharepoint.SharePointService", return_value=mock_sp_service
        ) as mock_cls:
            SharePointModule(
                client_id="cid",
                client_secret="csec",
                tenant_id="tid",
                site_domain="domain.com",
                site_path="/s/p",
            )
        mock_cls.assert_called_once_with(
            client_id="cid",
            client_secret="csec",
            tenant_id="tid",
            site_domain="domain.com",
            site_path="/s/p",
        )

    def test_default_empty_kwargs(self, mock_sp_service: MagicMock) -> None:
        """Missing kwargs default to empty strings."""
        with patch("app.modules.sharepoint.SharePointService", return_value=mock_sp_service):
            m = SharePointModule()
        assert m.client_id == ""
        assert m.client_secret == ""


# ---------------------------------------------------------------------------
# Delegated methods
# ---------------------------------------------------------------------------

class TestGetSiteId:
    def test_delegates_to_service(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service._get_site_id.return_value = "site-abc"
        result = module.get_site_id()
        mock_sp_service._get_site_id.assert_called_once()
        assert result == "site-abc"


class TestListFiles:
    def test_delegates_to_list_items(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.list_items.return_value = [{"name": "file.csv"}]
        result = module.list_files("some/folder")
        mock_sp_service.list_items.assert_called_once_with("some/folder")
        assert result == [{"name": "file.csv"}]


class TestListFolders:
    def test_delegates_to_list_folders(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.list_folders.return_value = [{"name": "subdir", "folder": {}}]
        result = module.list_folders("some/folder")
        mock_sp_service.list_folders.assert_called_once_with("some/folder")
        assert len(result) == 1


class TestGetItemByPath:
    def test_returns_response_with_content(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.download_file.return_value = b"file bytes"
        resp = module.get_item_by_path("path/to/file.csv")
        assert resp.content == b"file bytes"
        assert resp.status_code == 200

    def test_delegates_download_call(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.download_file.return_value = b"data"
        module.get_item_by_path("path/file.xlsx")
        mock_sp_service.download_file.assert_called_once_with("path/file.xlsx")


class TestCheckItemExists:
    def test_returns_true_when_exists(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.item_exists.return_value = True
        assert module.check_item_exists("/some/path") is True

    def test_returns_false_when_missing(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        mock_sp_service.item_exists.return_value = False
        assert module.check_item_exists("/missing") is False


class TestUploadFile:
    def test_calls_service_upload(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        module.upload_file("path/file.csv", b"content")
        mock_sp_service.upload_file.assert_called_once_with("path/file.csv", b"content")

    def test_returns_response_200(self, module: SharePointModule, mock_sp_service: MagicMock) -> None:
        resp = module.upload_file("path/file.csv", b"content")
        assert resp.status_code == 200
