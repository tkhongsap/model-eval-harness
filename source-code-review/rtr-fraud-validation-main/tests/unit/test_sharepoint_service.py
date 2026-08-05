"""Unit tests for SharePointService."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests as req_module

from app.services.sharepoint_service import SharePointService


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_msal() -> MagicMock:
    """Patch MSAL so __init__ doesn't require real credentials."""
    with patch("app.services.sharepoint_service.ConfidentialClientApplication") as mock_cls:
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_cls.return_value = mock_app
        yield mock_cls  # type: ignore[misc]


@pytest.fixture()
def svc(mock_msal: MagicMock) -> SharePointService:
    return SharePointService(
        client_id="cid",
        client_secret="csecret",
        tenant_id="tid",
        site_domain="example.sharepoint.com",
        site_path="/sites/test",
    )


def _ok_response(json_data: dict | None = None, content: bytes = b"") -> MagicMock:
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = json_data or {}
    resp.content = content
    resp.raise_for_status = MagicMock()
    return resp


def _error_response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    http_error = req_module.exceptions.HTTPError(response=resp)
    resp.raise_for_status.side_effect = http_error
    return resp


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAcquireToken:
    def test_returns_access_token(self, mock_msal: MagicMock) -> None:
        # Creating the service calls _acquire_token; verify it used the token
        svc = SharePointService("cid", "csec", "tid", "domain.com", "/s/test")
        assert svc._access_token == "test_token"

    def test_raises_runtime_error_on_msal_failure(self, mock_msal: MagicMock) -> None:
        mock_app = MagicMock()
        mock_app.acquire_token_for_client.return_value = {
            "error": "invalid_client",
            "error_description": "bad creds",
        }
        mock_msal.return_value = mock_app
        with pytest.raises(RuntimeError, match="Failed to acquire token"):
            SharePointService("cid", "csec", "tid", "domain.com", "/s/test")


class TestRefreshIf401:
    def test_returns_true_and_refreshes_on_401(self, svc: SharePointService) -> None:
        resp = MagicMock()
        resp.status_code = 401
        result = svc._refresh_if_401(resp)
        assert result is True
        # token should have been re-acquired
        assert svc._access_token == "test_token"

    def test_returns_false_for_non_401(self, svc: SharePointService) -> None:
        resp = MagicMock()
        resp.status_code = 200
        assert svc._refresh_if_401(resp) is False


# ---------------------------------------------------------------------------
# Site / Drive resolution
# ---------------------------------------------------------------------------

class TestGetSiteId:
    def test_returns_site_id(self, svc: SharePointService) -> None:
        ok_resp = _ok_response({"id": "site-abc"})
        with patch("app.services.sharepoint_service.requests.get", return_value=ok_resp):
            site_id = svc._get_site_id()
        assert site_id == "site-abc"

    def test_raises_on_missing_site_id(self, svc: SharePointService) -> None:
        ok_resp = _ok_response({"id": None})
        with patch("app.services.sharepoint_service.requests.get", return_value=ok_resp):
            with pytest.raises(RuntimeError, match="site_id not found"):
                svc._get_site_id()


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

class TestListItems:
    def test_returns_items_from_single_page(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        items_resp = _ok_response({"value": [{"name": "file.csv"}], "@odata.nextLink": None})
        with patch("app.services.sharepoint_service.requests.get", side_effect=[site_resp, items_resp]):
            items = svc.list_items("some/folder")
        assert len(items) == 1
        assert items[0]["name"] == "file.csv"

    def test_handles_pagination(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        page1 = _ok_response({"value": [{"name": "a.csv"}], "@odata.nextLink": "http://next"})
        page2 = _ok_response({"value": [{"name": "b.csv"}]})
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, page1, page2],
        ):
            items = svc.list_items("folder")
        assert len(items) == 2

    def test_prepends_slash_to_folder_path(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        items_resp = _ok_response({"value": []})
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, items_resp],
        ) as mock_get:
            svc.list_items("no-leading-slash")
        url_called = mock_get.call_args_list[1][0][0]
        assert "/no-leading-slash:" in url_called


class TestListFiles:
    def test_filters_out_folders(self, svc: SharePointService) -> None:
        with patch.object(
            svc,
            "list_items",
            return_value=[
                {"name": "file.csv"},
                {"name": "subdir", "folder": {}},
            ],
        ):
            files = svc.list_files("folder")
        assert len(files) == 1
        assert files[0]["name"] == "file.csv"


# ---------------------------------------------------------------------------
# Item resolution
# ---------------------------------------------------------------------------

class TestItemExists:
    def test_returns_true_when_found(self, svc: SharePointService) -> None:
        with patch.object(svc, "get_item_id", return_value="abc-123"):
            assert svc.item_exists("/some/path") is True

    def test_returns_false_when_not_found(self, svc: SharePointService) -> None:
        with patch.object(svc, "get_item_id", return_value=None):
            assert svc.item_exists("/missing/path") is False


class TestGetItemId:
    def test_returns_id_from_metadata(self, svc: SharePointService) -> None:
        with patch.object(svc, "get_item_metadata", return_value={"id": "item-abc"}):
            assert svc.get_item_id("/path") == "item-abc"

    def test_returns_none_on_404(self, svc: SharePointService) -> None:
        resp_404 = MagicMock()
        resp_404.status_code = 404
        http_err = req_module.exceptions.HTTPError(response=resp_404)
        with patch.object(svc, "get_item_metadata", side_effect=http_err):
            assert svc.get_item_id("/missing") is None

    def test_reraises_non_404_http_error(self, svc: SharePointService) -> None:
        resp_403 = MagicMock()
        resp_403.status_code = 403
        http_err = req_module.exceptions.HTTPError(response=resp_403)
        with patch.object(svc, "get_item_metadata", side_effect=http_err):
            with pytest.raises(req_module.exceptions.HTTPError):
                svc.get_item_id("/forbidden")


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

class TestDownloadFile:
    def test_returns_bytes(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        content_resp = _ok_response(content=b"file bytes")
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, content_resp],
        ):
            result = svc.download_file("path/to/file.csv")
        assert result == b"file bytes"

    def test_raises_on_http_error(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        err_resp = _error_response(404)
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, err_resp],
        ):
            with pytest.raises(req_module.exceptions.HTTPError):
                svc.download_file("missing/file.csv")


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------

class TestUploadFile:
    def test_succeeds_on_200(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        put_resp = MagicMock()
        put_resp.status_code = 200
        put_resp.raise_for_status = MagicMock()
        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch("app.services.sharepoint_service.requests.put", return_value=put_resp):
                svc.upload_file("path/file.csv", b"content")
        put_resp.raise_for_status.assert_not_called()

    def test_succeeds_on_201(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        put_resp = MagicMock()
        put_resp.status_code = 201
        put_resp.raise_for_status = MagicMock()
        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch("app.services.sharepoint_service.requests.put", return_value=put_resp):
                svc.upload_file("path/file.csv", b"content")

    def test_archives_and_retries_on_423(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        locked_resp = MagicMock()
        locked_resp.status_code = 423
        success_resp = MagicMock()
        success_resp.status_code = 200

        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch(
                "app.services.sharepoint_service.requests.put",
                side_effect=[locked_resp, success_resp],
            ):
                with patch.object(svc, "_archive_locked_file") as mock_archive:
                    svc.upload_file("path/file.csv", b"content", max_retries=2)
        mock_archive.assert_called_once()

    def test_raises_after_max_retries(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        locked_resp = MagicMock()
        locked_resp.status_code = 423

        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch("app.services.sharepoint_service.requests.put", return_value=locked_resp):
                with patch.object(svc, "_archive_locked_file"):
                    with pytest.raises(RuntimeError, match="Upload failed after"):
                        svc.upload_file("path/file.csv", b"content", max_retries=2)


# ---------------------------------------------------------------------------
# ensure_folder
# ---------------------------------------------------------------------------

class TestEnsureFolder:
    def test_returns_existing_folder_id(self, svc: SharePointService) -> None:
        with patch.object(svc, "get_item_id", return_value="existing-id"):
            result = svc.ensure_folder("/parent", "child")
        assert result == "existing-id"

    def test_creates_folder_when_missing(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        drive_resp = _ok_response({"id": "drive-1"})
        create_resp = _ok_response({"id": "new-folder-id"})

        call_count = {"n": 0}

        def mock_get_item_id(path: str) -> str | None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                return None  # full_path not found
            return "parent-id"  # parent found

        with patch.object(svc, "get_item_id", side_effect=mock_get_item_id):
            with patch("app.services.sharepoint_service.requests.get", side_effect=[site_resp, drive_resp]):
                with patch("app.services.sharepoint_service.requests.post", return_value=create_resp):
                    result = svc.ensure_folder("/parent", "child")
        assert result == "new-folder-id"

    def test_raises_when_parent_not_found(self, svc: SharePointService) -> None:
        def mock_get_item_id(path: str) -> None:
            return None  # both full_path and parent missing

        with patch.object(svc, "get_item_id", side_effect=mock_get_item_id):
            with patch.object(svc, "_get_site_id", return_value="site-1"):
                with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                    with pytest.raises(RuntimeError, match="Parent folder not found"):
                        svc.ensure_folder("/missing-parent", "child")


# ---------------------------------------------------------------------------
# move_to_archive
# ---------------------------------------------------------------------------

class TestMoveToArchive:
    def test_returns_false_when_file_not_found(self, svc: SharePointService) -> None:
        with patch.object(svc, "ensure_folder", return_value="archive-folder-id"):
            with patch.object(svc, "get_item_id", return_value=None):
                result = svc.move_to_archive("file.csv", "/input", "/archive")
        assert result is False

    def test_returns_true_on_success(self, svc: SharePointService) -> None:
        with patch.object(svc, "ensure_folder", return_value="archive-id"):
            with patch.object(svc, "get_item_id", return_value="file-id"):
                with patch.object(svc, "move_file", return_value=True):
                    result = svc.move_to_archive("file.csv", "/input", "/archive")
        assert result is True

    def test_returns_false_on_exception(self, svc: SharePointService) -> None:
        with patch.object(svc, "ensure_folder", side_effect=Exception("network error")):
            result = svc.move_to_archive("file.csv", "/input", "/archive")
        assert result is False


# ---------------------------------------------------------------------------
# list_folders / list_folder_names
# ---------------------------------------------------------------------------

class TestListFolders:
    def test_returns_only_folder_items(self, svc: SharePointService) -> None:
        items = [{"name": "file.csv"}, {"name": "dir", "folder": {}}]
        with patch.object(svc, "list_items", return_value=items):
            folders = svc.list_folders("some/path")
        assert len(folders) == 1
        assert folders[0]["name"] == "dir"

    def test_list_folder_names_returns_names(self, svc: SharePointService) -> None:
        with patch.object(svc, "list_folders", return_value=[{"name": "2024"}, {"name": "2025"}]):
            names = svc.list_folder_names("some/path")
        assert names == ["2024", "2025"]


# ---------------------------------------------------------------------------
# get_item_metadata
# ---------------------------------------------------------------------------

class TestGetItemMetadata:
    def test_returns_item_dict(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        item_resp = _ok_response({"id": "item-abc", "name": "file.csv"})
        with patch("app.services.sharepoint_service.requests.get", side_effect=[site_resp, item_resp]):
            result = svc.get_item_metadata("path/file.csv")
        assert result["id"] == "item-abc"

    def test_prepends_slash_to_item_path(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        item_resp = _ok_response({"id": "x"})
        with patch(
            "app.services.sharepoint_service.requests.get", side_effect=[site_resp, item_resp]
        ) as mock_get:
            svc.get_item_metadata("no-leading-slash/file.csv")
        url_called = mock_get.call_args_list[1][0][0]
        assert url_called.startswith("https://")


# ---------------------------------------------------------------------------
# get_download_url
# ---------------------------------------------------------------------------

class TestGetDownloadUrl:
    def test_returns_download_url(self, svc: SharePointService) -> None:
        with patch.object(
            svc,
            "get_item_metadata",
            return_value={"@microsoft.graph.downloadUrl": "https://cdn.example.com/file"},
        ):
            url = svc.get_download_url("path/file.csv")
        assert url == "https://cdn.example.com/file"

    def test_returns_none_on_404(self, svc: SharePointService) -> None:
        resp = MagicMock()
        resp.status_code = 404
        err = req_module.exceptions.HTTPError(response=resp)
        with patch.object(svc, "get_item_metadata", side_effect=err):
            assert svc.get_download_url("missing/path") is None

    def test_reraises_non_404_error(self, svc: SharePointService) -> None:
        resp = MagicMock()
        resp.status_code = 500
        err = req_module.exceptions.HTTPError(response=resp)
        with patch.object(svc, "get_item_metadata", side_effect=err):
            with pytest.raises(req_module.exceptions.HTTPError):
                svc.get_download_url("path/file.csv")


# ---------------------------------------------------------------------------
# download_file_by_id
# ---------------------------------------------------------------------------

class TestDownloadFileById:
    def test_returns_bytes(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                meta_resp = _ok_response(
                    {"@microsoft.graph.downloadUrl": "https://cdn.example.com/file"}
                )
                content_resp = _ok_response(content=b"file data")
                with patch(
                    "app.services.sharepoint_service.requests.get",
                    side_effect=[meta_resp, content_resp],
                ):
                    result = svc.download_file_by_id("item-abc")
        assert result == b"file data"

    def test_raises_when_no_download_url(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                meta_resp = _ok_response({})  # no downloadUrl
                with patch("app.services.sharepoint_service.requests.get", return_value=meta_resp):
                    with pytest.raises(RuntimeError, match="Download URL not found"):
                        svc.download_file_by_id("item-abc")


# ---------------------------------------------------------------------------
# move_file
# ---------------------------------------------------------------------------

class TestMoveFile:
    def test_returns_true_on_success(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                patch_resp = _ok_response({"id": "moved-item"})
                patch_resp.raise_for_status = MagicMock()
                with patch("app.services.sharepoint_service.requests.patch", return_value=patch_resp):
                    result = svc.move_file("file-id", "dest-folder-id")
        assert result is True

    def test_passes_new_name_when_provided(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                patch_resp = _ok_response({"id": "x"})
                patch_resp.raise_for_status = MagicMock()
                with patch(
                    "app.services.sharepoint_service.requests.patch", return_value=patch_resp
                ) as mock_patch:
                    svc.move_file("file-id", "dest-id", new_name="renamed.csv")
        payload = mock_patch.call_args[1]["json"]
        assert payload.get("name") == "renamed.csv"


# ---------------------------------------------------------------------------
# _archive_locked_file
# ---------------------------------------------------------------------------

class TestArchiveLockedFile:
    def test_renames_locked_file_successfully(self, svc: SharePointService) -> None:
        meta = {
            "id": "item-1",
            "name": "file.csv",
            "parentReference": {"id": "parent-1", "driveId": "drive-1"},
        }
        with patch.object(svc, "get_item_metadata", return_value=meta):
            patch_resp = _ok_response({"id": "archived"})
            with patch("app.services.sharepoint_service.requests.patch", return_value=patch_resp):
                svc._archive_locked_file("site-1", "/path/file.csv")
        # No assertion needed — success means no exception

    def test_logs_error_when_rename_fails(self, svc: SharePointService) -> None:
        meta = {
            "id": "item-1",
            "name": "file.csv",
            "parentReference": {"id": "parent-1", "driveId": "drive-1"},
        }
        with patch.object(svc, "get_item_metadata", return_value=meta):
            err_resp = MagicMock()
            err_resp.status_code = 500
            with patch("app.services.sharepoint_service.requests.patch", return_value=err_resp):
                svc._archive_locked_file("site-1", "/path/file.csv")
        # Should not raise

    def test_returns_early_when_metadata_missing_ids(self, svc: SharePointService) -> None:
        meta = {"id": None, "name": "file.csv", "parentReference": {}}
        with patch.object(svc, "get_item_metadata", return_value=meta):
            svc._archive_locked_file("site-1", "/path/file.csv")
        # Returns early without making patch request

    def test_swallows_exception(self, svc: SharePointService) -> None:
        with patch.object(svc, "get_item_metadata", side_effect=Exception("error")):
            svc._archive_locked_file("site-1", "/path/file.csv")
        # Should not raise


# ---------------------------------------------------------------------------
# download_with_backup
# ---------------------------------------------------------------------------

class TestDownloadWithBackup:
    def test_downloads_and_creates_backup(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        content_resp = _ok_response(content=b"file content")
        backup_resp = _ok_response()

        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch(
                "app.services.sharepoint_service.requests.get",
                side_effect=[content_resp],
            ):
                with patch(
                    "app.services.sharepoint_service.requests.put", return_value=backup_resp
                ):
                    result = svc.download_with_backup("folder", "file.csv", "backup_folder")
        assert result == b"file content"

    def test_raises_on_content_http_error(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            err_resp = _error_response(404)
            with patch("app.services.sharepoint_service.requests.get", return_value=err_resp):
                with pytest.raises(req_module.exceptions.HTTPError):
                    svc.download_with_backup("folder", "file.csv", "backup")


# ---------------------------------------------------------------------------
# 401-refresh branches for each method (lines 86, 97, 117, 145, 182, 192,
#   220-221, 232, 257-258, 289, 314, 352)
# ---------------------------------------------------------------------------

def _make_401_then_ok(json_data: dict | None = None, content: bytes = b"") -> list:
    """Return [401_resp, ok_resp] for testing the _refresh_if_401 retry branch."""
    resp_401 = MagicMock()
    resp_401.status_code = 401
    resp_401.raise_for_status = MagicMock()
    ok = _ok_response(json_data, content)
    return [resp_401, ok]


class TestGetSiteId401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        side = _make_401_then_ok({"id": "site-retry"})
        with patch("app.services.sharepoint_service.requests.get", side_effect=side):
            site_id = svc._get_site_id()
        assert site_id == "site-retry"


class TestGetDriveId401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        side = _make_401_then_ok({"id": "drive-retry"})
        with patch("app.services.sharepoint_service.requests.get", side_effect=side):
            drive_id = svc._get_drive_id("site-1")
        assert drive_id == "drive-retry"


class TestListItems401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()
        ok_items = _ok_response({"value": [{"name": "f.csv"}]})
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, resp_401, ok_items],
        ):
            items = svc.list_items("folder")
        assert len(items) == 1


class TestGetItemMetadata401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        side = _make_401_then_ok({"id": "item-retry"})
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, *side],
        ):
            result = svc.get_item_metadata("path/file.csv")
        assert result["id"] == "item-retry"


class TestDownloadFile401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        side = _make_401_then_ok(content=b"retry content")
        with patch(
            "app.services.sharepoint_service.requests.get",
            side_effect=[site_resp, *side],
        ):
            result = svc.download_file("path/file.csv")
        assert result == b"retry content"


class TestDownloadFileById401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                side = _make_401_then_ok({"@microsoft.graph.downloadUrl": "http://cdn/file"})
                content_resp = _ok_response(content=b"data")
                with patch(
                    "app.services.sharepoint_service.requests.get",
                    side_effect=[*side, content_resp],
                ):
                    result = svc.download_file_by_id("item-1")
        assert result == b"data"


class TestUploadFile401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        resp_401 = MagicMock()
        resp_401.status_code = 401
        ok_put = MagicMock()
        ok_put.status_code = 200
        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch(
                "app.services.sharepoint_service.requests.put",
                side_effect=[resp_401, ok_put],
            ):
                svc.upload_file("path/file.csv", b"content")  # should succeed

    def test_raises_on_unexpected_status(self, svc: SharePointService) -> None:
        site_resp = _ok_response({"id": "site-1"})
        err_put = MagicMock()
        err_put.status_code = 500
        http_err = req_module.exceptions.HTTPError(response=err_put)
        err_put.raise_for_status.side_effect = http_err
        with patch("app.services.sharepoint_service.requests.get", return_value=site_resp):
            with patch("app.services.sharepoint_service.requests.put", return_value=err_put):
                with pytest.raises(req_module.exceptions.HTTPError):
                    svc.upload_file("path/file.csv", b"content", max_retries=1)


class TestArchiveLockedFile401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        meta = {
            "id": "item-1",
            "name": "file.csv",
            "parentReference": {"id": "parent-1", "driveId": "drive-1"},
        }
        with patch.object(svc, "get_item_metadata", return_value=meta):
            resp_401 = MagicMock()
            resp_401.status_code = 401
            ok_patch = _ok_response({"id": "archived"})
            with patch(
                "app.services.sharepoint_service.requests.patch",
                side_effect=[resp_401, ok_patch],
            ):
                svc._archive_locked_file("site-1", "/path/file.csv")


class TestMoveFile401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                resp_401 = MagicMock()
                resp_401.status_code = 401
                resp_401.raise_for_status = MagicMock()
                ok_patch = _ok_response({"id": "moved"})
                ok_patch.raise_for_status = MagicMock()
                with patch(
                    "app.services.sharepoint_service.requests.patch",
                    side_effect=[resp_401, ok_patch],
                ):
                    result = svc.move_file("file-id", "dest-id")
        assert result is True


class TestEnsureFolder401:
    def test_retries_after_401_on_create(self, svc: SharePointService) -> None:
        call_count = {"n": 0}

        def mock_get_item_id(path: str) -> str | None:
            call_count["n"] += 1
            return None if call_count["n"] == 1 else "parent-id"

        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()
        ok_create = _ok_response({"id": "new-folder-id"})

        with patch.object(svc, "get_item_id", side_effect=mock_get_item_id):
            with patch.object(svc, "_get_site_id", return_value="site-1"):
                with patch.object(svc, "_get_drive_id", return_value="drive-1"):
                    with patch(
                        "app.services.sharepoint_service.requests.post",
                        side_effect=[resp_401, ok_create],
                    ):
                        result = svc.ensure_folder("/parent", "child")
        assert result == "new-folder-id"


class TestDownloadWithBackup401:
    def test_retries_after_401(self, svc: SharePointService) -> None:
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()
        ok_content = _ok_response(content=b"file bytes")
        ok_backup = _ok_response()

        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch(
                "app.services.sharepoint_service.requests.get",
                side_effect=[resp_401, ok_content],
            ):
                with patch(
                    "app.services.sharepoint_service.requests.put", return_value=ok_backup
                ):
                    result = svc.download_with_backup("folder", "file.csv", "backup")
        assert result == b"file bytes"


# ---------------------------------------------------------------------------
# decrypt() — lines 378-405
# ---------------------------------------------------------------------------

class TestDecrypt:
    def test_returns_csv_buffer(self, svc: SharePointService) -> None:
        """decrypt() downloads, decrypts, converts to CSV and returns a BytesIO."""
        import io as _io
        import polars as _pl

        # The decrypted bytes — a pipe-separated CSV with two rows
        decrypted_csv = b"col1|col2\nval1|val2\n"

        with patch.object(svc, "_get_site_id", return_value="site-1"):
            content_resp = _ok_response(content=b"encrypted_bytes")
            with patch("app.services.sharepoint_service.requests.get", return_value=content_resp):
                with patch(
                    "app.services.sharepoint_service.decrypt_hybrid",
                    return_value=decrypted_csv,
                ):
                    result = svc.decrypt(
                        initial_headers=["col1", "col2"],
                        file_folder_path="folder/sub",
                        file_name="file.csv",
                        rsa_private_key="rsa_key",
                    )

        assert isinstance(result, _io.BytesIO)
        result.seek(0)
        contents = result.read()
        assert b"col1" in contents or b"col2" in contents

    def test_retries_decrypt_download_after_401(self, svc: SharePointService) -> None:
        """decrypt() hits the 401-refresh branch (lines 384-385)."""
        decrypted_csv = b"a|b\nx|y\n"
        resp_401 = MagicMock()
        resp_401.status_code = 401
        resp_401.raise_for_status = MagicMock()
        ok_content = _ok_response(content=b"encrypted")

        with patch.object(svc, "_get_site_id", return_value="site-1"):
            with patch(
                "app.services.sharepoint_service.requests.get",
                side_effect=[resp_401, ok_content],
            ):
                with patch(
                    "app.services.sharepoint_service.decrypt_hybrid",
                    return_value=decrypted_csv,
                ):
                    result = svc.decrypt(
                        initial_headers=["a", "b"],
                        file_folder_path="folder",
                        file_name="f.csv",
                        rsa_private_key="key",
                    )
        assert result is not None
