"""Unit tests for app/sharepoint.py (legacy module).

Bootstrap strategy
------------------
* ``app/sharepoint.py`` reads from ``os.environ`` at import time AND
  instantiates ``msal.ConfidentialClientApplication`` at module level.
* We set all required env vars and patch ``msal.ConfidentialClientApplication``
  *before* importing the module so no real MSAL / network calls happen.
"""
from __future__ import annotations

import io
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest
import requests

# ── bootstrap ────────────────────────────────────────────────────────────────
_SP_ENV: dict[str, str] = {
    "FRAUD_SITE_CLIENT_ID": "fraud-cid",
    "FRAUD_SITE_CLIENT_SECRET": "fraud-csec",
    "FRAUD_SITE_TENANT_ID": "fraud-tid",
    "FRAUD_SITE_SITE_DOMAIN": "contoso.sharepoint.com",
    "FRAUD_SITE_SITE_PATH": "/sites/fraud",
    "FRAUD_SITE_BASE_ROOT": "Shared Documents",
    "INPUT_FOLDER": "input",
    "BACKUP_FOLDER": "backup",
    "OUTPUT_FOLDER": "output",
    "ARCHIVE_FOLDER": "archive",
    "CONTROL_SITE_CLIENT_ID": "ctrl-cid",
    "CONTROL_SITE_CLIENT_SECRET": "ctrl-csec",
    "CONTROL_SITE_TENANT_ID": "ctrl-tid",
    "CONTROL_SITE_SITE_DOMAIN": "contoso.sharepoint.com",
    "CONTROL_SITE_SITE_PATH": "/sites/control",
    "CONTROL_SITE_BASE_ROOT": "Shared Documents",
    "CONTROL_SITE_PROMPTS_ROOT": "Prompts",
    "CONTROL_SITE_CONTROL_PATH": "Control",
    "CONTROL_SITE_TRANSACTION_LOG_PATH": "TransactionLog",
    "CONTROL_SITE_PERFORMANCE_LOG_PATH": "PerformanceLog",
    "BATCH_SIZE": "4",
    "RECIPIENT_EMAIL": "recipient@example.com",
    "TEAM_EMAIL": "team@example.com",
    "SENDER_EMAIL": "sender@example.com",
    "GCS_BUCKET_NAME": "my-gcs-bucket",
    "S3_AWS_ACCESS_KEY": "fake-key",
    "S3_AWS_SECRET_KEY": "fake-secret",
    "S3_BUCKET_NAME": "my-s3-bucket",
}
for _k, _v in _SP_ENV.items():
    os.environ.setdefault(_k, _v)

# Patch msal.ConfidentialClientApplication BEFORE app.sharepoint is imported
# so module-level instantiation gets MagicMock objects.
import msal as _msal

_ORIG_CCA = _msal.ConfidentialClientApplication
_msal.ConfidentialClientApplication = MagicMock(return_value=MagicMock())

sys.modules.pop("app.sharepoint", None)
import app.sharepoint as sp  # noqa: E402  (must follow bootstrap)

# Restore msal for other tests
_msal.ConfidentialClientApplication = _ORIG_CCA


# ── helpers ───────────────────────────────────────────────────────────────────

def _ok_response(json_data: dict | None = None, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    if json_data is not None:
        resp.json.return_value = json_data
    resp.raise_for_status.return_value = None
    return resp


def _http_error(status_code: int) -> requests.exceptions.HTTPError:
    resp = MagicMock()
    resp.status_code = status_code
    err = requests.exceptions.HTTPError(response=resp)
    err.response = resp
    return err


# ── get_access_token ──────────────────────────────────────────────────────────

class TestGetAccessToken:
    def test_fraud_site_returns_token(self) -> None:
        mock_fraud_app = MagicMock()
        mock_fraud_app.acquire_token_for_client.return_value = {
            "access_token": "tok-fraud",
            "expires_in": 7200,
        }
        with patch.object(sp, "fraud_app", mock_fraud_app):
            token = sp.get_access_token("fraud")
        assert token == "tok-fraud"

    def test_fraud_site_near_expiry_refreshes_app(self) -> None:
        mock_fraud_app = MagicMock()
        mock_fraud_app.acquire_token_for_client.return_value = {
            "access_token": "old-tok",
            "expires_in": 500,  # < 1800 → triggers refresh
        }
        new_app = MagicMock()
        new_app.acquire_token_for_client.return_value = {
            "access_token": "fresh-tok",
            "expires_in": 7200,
        }
        with (
            patch.object(sp, "fraud_app", mock_fraud_app),
            patch("app.sharepoint.ConfidentialClientApplication", return_value=new_app),
        ):
            token = sp.get_access_token("fraud")
        assert token == "fresh-tok"

    def test_control_site_returns_token(self) -> None:
        mock_ctrl_app = MagicMock()
        mock_ctrl_app.acquire_token_for_client.return_value = {
            "access_token": "tok-ctrl",
            "expires_in": 7200,
        }
        with patch.object(sp, "control_app", mock_ctrl_app):
            token = sp.get_access_token("control")
        assert token == "tok-ctrl"

    def test_control_site_near_expiry_refreshes_app(self) -> None:
        mock_ctrl_app = MagicMock()
        mock_ctrl_app.acquire_token_for_client.return_value = {
            "access_token": "old-ctrl-tok",
            "expires_in": 300,  # < 1800 → triggers refresh
        }
        new_app = MagicMock()
        new_app.acquire_token_for_client.return_value = {
            "access_token": "refreshed-ctrl-tok",
            "expires_in": 7200,
        }
        with (
            patch.object(sp, "control_app", mock_ctrl_app),
            patch("app.sharepoint.ConfidentialClientApplication", return_value=new_app),
        ):
            token = sp.get_access_token("control")
        assert token == "refreshed-ctrl-tok"

    def test_invalid_site_name_returns_empty_string(self) -> None:
        with patch.object(sp, "logger", MagicMock()):
            token = sp.get_access_token("unknown-site")
        assert token == ""

    def test_exception_returns_none(self) -> None:
        mock_fraud_app = MagicMock()
        mock_fraud_app.acquire_token_for_client.side_effect = Exception("auth fail")
        with (
            patch.object(sp, "fraud_app", mock_fraud_app),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_access_token("fraud")
        assert result is None


# ── get_site_id ───────────────────────────────────────────────────────────────

class TestGetSiteId:
    def test_fraud_site_returns_id(self) -> None:
        with patch("requests.get", return_value=_ok_response({"id": "site-abc"})):
            site_id = sp.get_site_id("fraud", "token-x")
        assert site_id == "site-abc"

    def test_control_site_returns_id(self) -> None:
        with patch("requests.get", return_value=_ok_response({"id": "site-ctrl"})):
            site_id = sp.get_site_id("control", "token-x")
        assert site_id == "site-ctrl"

    def test_invalid_site_logs_error(self) -> None:
        with (
            patch.object(sp, "logger", MagicMock()) as mock_log,
            patch("requests.get", return_value=_ok_response({"id": "x"})),
        ):
            sp.get_site_id("bad-site", "tok")
        mock_log.error.assert_called()

    def test_exception_logs_error(self) -> None:
        with (
            patch("requests.get", side_effect=Exception("net err")),
            patch.object(sp, "logger", MagicMock()),
        ):
            sp.get_site_id("fraud", "tok")


# ── get_drive_id ──────────────────────────────────────────────────────────────

class TestGetDriveId:
    def test_returns_drive_id(self) -> None:
        with patch("requests.get", return_value=_ok_response({"id": "drive-123"})):
            drive_id = sp.get_drive_id("tok", "site-x")
        assert drive_id == "drive-123"

    def test_raises_on_http_error(self) -> None:
        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = requests.exceptions.HTTPError("500")
        with patch("requests.get", return_value=err_resp):
            with pytest.raises(requests.exceptions.HTTPError):
                sp.get_drive_id("tok", "site-x")


# ── list_folders_in_folder ────────────────────────────────────────────────────

class TestListFoldersInFolder:
    def _patch_auth(self) -> tuple[MagicMock, MagicMock]:
        mock_get_token = MagicMock(return_value="token")
        mock_get_site = MagicMock(return_value="site-id")
        return mock_get_token, mock_get_site

    def test_returns_folder_names(self) -> None:
        items = [
            {"folder": True, "name": "FolderA"},
            {"folder": True, "name": "FolderB"},
            {"name": "file.xlsx"},  # no "folder" key
        ]
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="site-id"),
            patch("requests.get", return_value=_ok_response({"value": items})),
        ):
            result = sp.list_folders_in_folder("fraud", "input/202301")
        assert result == ["FolderA", "FolderB"]

    def test_exception_returns_empty_list(self) -> None:
        with (
            patch.object(sp, "get_access_token", side_effect=Exception("err")),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.list_folders_in_folder("fraud", "folder")
        assert result == []


# ── list_files_in_folder ──────────────────────────────────────────────────────

class TestListFilesInFolder:
    def test_returns_file_list(self) -> None:
        items = [
            {
                "name": "data.xlsx",
                "id": "file-id-1",
                "createdDateTime": "2024-01-01T00:00:00Z",
                "parentReference": {"path": "/sites/fraud/root:/input"},
            },
        ]
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch(
                "requests.get",
                return_value=_ok_response({"value": items}),
            ),
        ):
            files = sp.list_files_in_folder("fraud", "input/202301")
        assert len(files) == 1
        assert files[0]["file_name"] == "data.xlsx"

    def test_pagination_follows_next_link(self) -> None:
        page1 = {
            "value": [
                {
                    "name": "a.xlsx",
                    "id": "id1",
                    "createdDateTime": "2024-01-01T00:00:00Z",
                    "parentReference": {"path": "/p"},
                }
            ],
            "@odata.nextLink": "https://graph.microsoft.com/next",
        }
        page2 = {
            "value": [
                {
                    "name": "b.xlsx",
                    "id": "id2",
                    "createdDateTime": "2024-01-02T00:00:00Z",
                    "parentReference": {"path": "/p"},
                }
            ],
        }
        mock_resp1 = _ok_response()
        mock_resp1.json.return_value = page1
        mock_resp2 = _ok_response()
        mock_resp2.json.return_value = page2
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch("requests.get", side_effect=[mock_resp1, mock_resp2]),
        ):
            files = sp.list_files_in_folder("fraud", "input/202301")
        assert len(files) == 2

    def test_skips_folders_logs_warning(self) -> None:
        items = [
            {"folder": True, "name": "sub-folder"},
        ]
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch("requests.get", return_value=_ok_response({"value": items})),
            patch.object(sp, "logger", MagicMock()) as mock_log,
        ):
            files = sp.list_files_in_folder("fraud", "input/202301")
        assert files == []
        mock_log.warning.assert_called()

    def test_exception_returns_empty_list(self) -> None:
        with (
            patch.object(sp, "get_access_token", side_effect=Exception("net")),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.list_files_in_folder("fraud", "folder")
        assert result == []


# ── get_item_download_url_by_path ─────────────────────────────────────────────

class TestGetItemDownloadUrlByPath:
    def test_returns_download_url(self) -> None:
        data = {"@microsoft.graph.downloadUrl": "https://cdn.example.com/file.xlsx"}
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch("requests.get", return_value=_ok_response(data)),
        ):
            url = sp.get_item_download_url_by_path("fraud", "input/file.xlsx")
        assert url == "https://cdn.example.com/file.xlsx"

    def test_returns_none_on_404(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "logger", MagicMock()),
        ):
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = _http_error(404)
            with patch("requests.get", return_value=mock_resp):
                result = sp.get_item_download_url_by_path("fraud", "missing.xlsx")
        assert result is None

    def test_raises_on_non_404_http_error(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "logger", MagicMock()),
        ):
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = _http_error(500)
            with patch("requests.get", return_value=mock_resp):
                with pytest.raises(requests.exceptions.HTTPError):
                    sp.get_item_download_url_by_path("fraud", "file.xlsx")

    def test_raises_on_generic_exception(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch("requests.get", side_effect=ConnectionError("net")),
            patch.object(sp, "logger", MagicMock()),
        ):
            with pytest.raises(ConnectionError):
                sp.get_item_download_url_by_path("fraud", "file.xlsx")


# ── get_item_id_by_path ───────────────────────────────────────────────────────

class TestGetItemIdByPath:
    def test_returns_item_id(self) -> None:
        with patch("requests.get", return_value=_ok_response({"id": "item-id-x"})):
            item_id = sp.get_item_id_by_path("tok", "sid", "folder/file.xlsx")
        assert item_id == "item-id-x"

    def test_returns_none_on_404(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = _http_error(404)
        with (
            patch("requests.get", return_value=mock_resp),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_item_id_by_path("tok", "sid", "missing.xlsx")
        assert result is None

    def test_raises_on_non_404_http_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = _http_error(503)
        with (
            patch("requests.get", return_value=mock_resp),
            patch.object(sp, "logger", MagicMock()),
        ):
            with pytest.raises(requests.exceptions.HTTPError):
                sp.get_item_id_by_path("tok", "sid", "file.xlsx")

    def test_raises_on_generic_exception(self) -> None:
        with (
            patch("requests.get", side_effect=TimeoutError("timed out")),
            patch.object(sp, "logger", MagicMock()),
        ):
            with pytest.raises(TimeoutError):
                sp.get_item_id_by_path("tok", "sid", "file.xlsx")


# ── move_sharepoint_file ──────────────────────────────────────────────────────

class TestMoveSharepointFile:
    def test_success_returns_true(self) -> None:
        with patch("requests.patch", return_value=_ok_response()):
            result = sp.move_sharepoint_file(
                "tok", "drv", "file-id", "dest-id", "archive", "new_name.xlsx"
            )
        assert result is True

    def test_success_without_new_name(self) -> None:
        with patch("requests.patch", return_value=_ok_response()):
            result = sp.move_sharepoint_file(
                "tok", "drv", "file-id", "dest-id", "archive"
            )
        assert result is True

    def test_request_exception_logs_no_raise(self) -> None:
        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = requests.exceptions.RequestException(
            "err"
        )
        with (
            patch("requests.patch", return_value=err_resp),
            patch.object(sp, "logger", MagicMock()),
        ):
            # Returns None (no explicit return in except block)
            result = sp.move_sharepoint_file("t", "d", "f", "df", "folder", "f.xlsx")
        assert result is None

    def test_generic_exception_re_raises(self) -> None:
        with (
            patch("requests.patch", side_effect=Exception("unexpected")),
            patch.object(sp, "logger", MagicMock()),
        ):
            with pytest.raises(Exception, match="unexpected"):
                sp.move_sharepoint_file("t", "d", "f", "df", "folder", "f.xlsx")


# ── move_file_to_archive ──────────────────────────────────────────────────────

class TestMoveFileToArchive:
    def _stub_auth(self) -> None:
        """Common auth patches used by all sub-tests (applied manually)."""

    def test_success_archive_folder_exists(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
        ):
            # 1st GET: monthly archive folder found
            # 2nd GET: file item
            # PATCH: move file
            mock_archive = _ok_response({"id": "archive-folder-id"})
            mock_file = _ok_response({"id": "file-item-id"})
            mock_move = _ok_response()
            with (
                patch("requests.get", side_effect=[mock_archive, mock_file]),
                patch("requests.patch", return_value=mock_move),
            ):
                result = sp.move_file_to_archive("data.xlsx", "input/202301")
        assert result is True

    def test_success_archive_folder_created_when_missing(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
        ):
            # 1st GET: archive folder → 404
            archive_missing = MagicMock()
            archive_missing.raise_for_status.side_effect = _http_error(404)
            # 2nd GET: parent archive folder
            parent = _ok_response({"id": "parent-id"})
            # POST: create monthly folder
            created = _ok_response({"id": "new-monthly-id"}, 201)
            # 3rd GET: file item
            file_item = _ok_response({"id": "file-id"})
            # PATCH: move
            move_ok = _ok_response()
            with (
                patch(
                    "requests.get",
                    side_effect=[archive_missing, parent, file_item],
                ),
                patch(
                    "requests.post", return_value=created
                ),
                patch("requests.patch", return_value=move_ok),
            ):
                result = sp.move_file_to_archive("data.xlsx", "input/202301")
        assert result is True

    def test_failure_returns_false(self) -> None:
        # move_file_to_archive only catches requests.exceptions.RequestException
        err = requests.exceptions.RequestException("auth fail")
        with (
            patch.object(sp, "get_access_token", side_effect=err),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.move_file_to_archive("data.xlsx", "input/202301")
        assert result is False

    def test_non_404_archive_http_error_reraises_returns_false(self) -> None:
        """Archive folder request returns non-404 error → else: raise → outer catches → False."""
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
        ):
            archive_500 = MagicMock()
            archive_500.raise_for_status.side_effect = _http_error(500)
            with patch("requests.get", return_value=archive_500):
                result = sp.move_file_to_archive("data.xlsx", "input/202301")
        assert result is False


# ── upload_file_to_sharepoint ─────────────────────────────────────────────────

class TestUploadFileToSharepoint:
    def _buf(self) -> io.BytesIO:
        return io.BytesIO(b"xlsx-bytes")

    def test_upload_success_200(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.put", return_value=_ok_response({}, 200)),
        ):
            sp.upload_file_to_sharepoint("fraud", self._buf(), "output/file.xlsx")

    def test_upload_success_201(self) -> None:
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.put", return_value=_ok_response({}, 201)),
        ):
            sp.upload_file_to_sharepoint("fraud", self._buf(), "output/file.xlsx")

    def test_upload_locked_423_retries_and_succeeds(self) -> None:
        locked = MagicMock()
        locked.status_code = 423
        ok = _ok_response({}, 200)
        ok.raise_for_status.return_value = None
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "get_item_id_by_path", return_value="file-id"),
            patch.object(sp, "move_sharepoint_file", return_value=True),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.put", side_effect=[locked, ok]),
        ):
            # Should not raise
            sp.upload_file_to_sharepoint(
                "fraud", self._buf(), "output/folder/file.xlsx"
            )

    def test_upload_other_failure_logs(self) -> None:
        fail = MagicMock()
        fail.status_code = 500
        fail.text = "Internal Server Error"
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.put", return_value=fail),
        ):
            # Should not raise, just logs failure
            sp.upload_file_to_sharepoint("fraud", self._buf(), "output/file.xlsx")

    def test_upload_locked_423_retry_exception_reraises(self) -> None:
        """423 path: second inner operation raises → except handler logs and re-raises.

        The first get_item_id_by_path call must succeed so that url_split and
        destination_folder are defined before the exception occurs (the logger
        message in the except block references these names).
        """
        locked = MagicMock()
        locked.status_code = 423
        # First call (file_id lookup) succeeds; second call (folder lookup) fails.
        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "get_drive_id", return_value="drv"),
            patch.object(
                sp,
                "get_item_id_by_path",
                side_effect=["file-id", Exception("folder lookup failed")],
            ),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.put", return_value=locked),
        ):
            with pytest.raises(Exception, match="folder lookup failed"):
                sp.upload_file_to_sharepoint(
                    "fraud", self._buf(), "output/folder/file.xlsx"
                )


# ── get_file_content_by_id_csv ────────────────────────────────────────────────

class TestGetFileContentByIdCsv:
    def test_returns_file_bytes(self) -> None:
        item_json = {"@microsoft.graph.downloadUrl": "https://cdn.example.com/data.csv"}
        content_resp = MagicMock()
        content_resp.raise_for_status.return_value = None
        content_resp.content = b"col1,col2\n1,2\n"
        item_resp = _ok_response(item_json)
        with (
            patch("requests.get", side_effect=[item_resp, content_resp]),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_file_content_by_id_csv("tok", "drv", "item-id-1")
        assert result == b"col1,col2\n1,2\n"

    def test_returns_none_when_no_download_url(self) -> None:
        item_resp = _ok_response({})  # no downloadUrl
        with (
            patch("requests.get", return_value=item_resp),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_file_content_by_id_csv("tok", "drv", "item-id-1")
        assert result is None

    def test_request_exception_returns_none(self) -> None:
        err_resp = MagicMock()
        err_resp.raise_for_status.side_effect = requests.exceptions.RequestException(
            "net"
        )
        with (
            patch("requests.get", return_value=err_resp),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_file_content_by_id_csv("tok", "drv", "item-id-1")
        assert result is None

    def test_generic_exception_returns_none(self) -> None:
        with (
            patch("requests.get", side_effect=Exception("unexpected")),
            patch.object(sp, "logger", MagicMock()),
        ):
            result = sp.get_file_content_by_id_csv("tok", "drv", "item-id-1")
        assert result is None


# ── copy_sharepoint_file_to_gcs ───────────────────────────────────────────────

class TestCopySharepointFileToGcs:
    def test_copies_when_gcs_file_missing(self) -> None:
        item_resp = MagicMock()
        item_resp.raise_for_status.return_value = None
        item_resp.content = b"xlsx-content"
        upload_resp = MagicMock()
        upload_resp.raise_for_status.return_value = None

        fake_fs = MagicMock()
        fake_fs.exists.return_value = False
        fake_file = MagicMock()
        fake_fs.open.return_value.__enter__ = MagicMock(return_value=fake_file)
        fake_fs.open.return_value.__exit__ = MagicMock(return_value=False)

        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.get", return_value=item_resp),
            patch("requests.put", return_value=upload_resp),
        ):
            gcs_path, gcs_uri = sp.copy_sharepoint_file_to_gcs(
                "input/202301", "data.xlsx", "input/data.xlsx", fake_fs
            )
        assert gcs_path == "input/data.xlsx"
        assert gcs_uri.startswith("gs://")
        fake_fs.open.assert_called_once()

    def test_skips_write_when_gcs_file_exists(self) -> None:
        item_resp = MagicMock()
        item_resp.raise_for_status.return_value = None
        item_resp.content = b"xlsx-content"
        upload_resp = MagicMock()
        upload_resp.raise_for_status.return_value = None

        fake_fs = MagicMock()
        fake_fs.exists.return_value = True  # already in GCS → skip write

        with (
            patch.object(sp, "get_access_token", return_value="tok"),
            patch.object(sp, "get_site_id", return_value="sid"),
            patch.object(sp, "logger", MagicMock()),
            patch("requests.get", return_value=item_resp),
            patch("requests.put", return_value=upload_resp),
        ):
            gcs_path, gcs_uri = sp.copy_sharepoint_file_to_gcs(
                "input/202301", "data.xlsx", "input/data.xlsx", fake_fs
            )
        assert gcs_path == "input/data.xlsx"
        fake_fs.open.assert_not_called()
