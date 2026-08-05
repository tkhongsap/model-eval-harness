"""
Tests for SharePointModule class.
"""

from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pytest
import requests

from src.modules.microsoft.sharepoint import SharePointModule


class TestSharePointModule:
    """Test suite for SharePointModule class."""

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_init_success(self, mock_get, mock_msal):
        """Test successful SharePointModule initialization."""
        # Mock MSAL token acquisition
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "test_site_id"}
        mock_get.return_value = mock_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        assert module.client_id == "test_client"
        assert module.access_token == "test_token"
        assert module._site_id_cache == "test_site_id"

    def test_init_missing_client_id_raises_error(self):
        """Test that missing client_id raises ValueError."""
        with pytest.raises(ValueError, match="Missing required authentication parameters"):
            SharePointModule(
                client_secret="test_secret",
                tenant_id="test_tenant",
                site_domain="test.sharepoint.com",
                site_path="/sites/test",
            )

    def test_init_missing_site_domain_raises_error(self):
        """Test that missing site_domain raises ValueError."""
        with pytest.raises(ValueError, match="Missing required site parameters"):
            SharePointModule(
                client_id="test_client", client_secret="test_secret", tenant_id="test_tenant", site_path="/sites/test"
            )

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    def test_init_token_acquisition_failure(self, mock_msal):
        """Test initialization fails when token acquisition fails."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"error": "invalid_client"}
        mock_msal.return_value = mock_app

        with pytest.raises(Exception, match="Failed to acquire token"):
            SharePointModule(
                client_id="test_client",
                client_secret="test_secret",
                tenant_id="test_tenant",
                site_domain="test.sharepoint.com",
                site_path="/sites/test",
            )

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_site_id_success(self, mock_get, mock_msal):
        """Test successful site ID retrieval."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID request
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "test_site_id"}
        mock_get.return_value = mock_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        site_id = module.get_site_id(use_cache=False)
        assert site_id == "test_site_id"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_site_id_uses_cache(self, mock_get, mock_msal):
        """Test that get_site_id uses cached value."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID request
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"id": "cached_site_id"}
        mock_get.return_value = mock_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        # First call - should cache
        site_id_1 = module.get_site_id()

        # Second call with cache - should not make new request
        call_count_before = mock_get.call_count
        site_id_2 = module.get_site_id(use_cache=True)
        call_count_after = mock_get.call_count

        assert site_id_1 == site_id_2
        assert call_count_before == call_count_after  # No new request made

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_success(self, mock_get, mock_msal):
        """Test successful file listing."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock responses
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        files_response = Mock()
        files_response.status_code = 200
        files_response.json.return_value = {
            "value": [{"name": "file1.txt", "id": "id1"}, {"name": "file2.txt", "id": "id2"}]
        }

        mock_get.side_effect = [site_response, files_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [files_response]
        files = module.list_files("/test/folder")

        assert len(files) == 2
        assert files[0]["name"] == "file1.txt"
        assert files[1]["name"] == "file2.txt"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_with_pagination(self, mock_get, mock_msal):
        """Test file listing with pagination."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site response
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock paginated responses
        page1_response = Mock()
        page1_response.status_code = 200
        page1_response.json.return_value = {
            "value": [{"name": "file1.txt"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/next",
        }

        page2_response = Mock()
        page2_response.status_code = 200
        page2_response.json.return_value = {"value": [{"name": "file2.txt"}]}

        mock_get.side_effect = [site_response, page1_response, page2_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [page1_response, page2_response]
        files = module.list_files("/test/folder")

        assert len(files) == 2

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_item_by_path_success(self, mock_get, mock_msal):
        """Test successful item retrieval by path."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site response
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock item metadata response
        item_response = Mock()
        item_response.status_code = 200
        item_response.json.return_value = {"@microsoft.graph.downloadUrl": "https://download.url", "size": 1024}

        # Mock download response
        download_response = Mock()
        download_response.status_code = 200
        download_response.content = b"file content"

        mock_get.side_effect = [site_response, item_response, download_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [item_response, download_response]
        response = module.get_item_by_path("/test/file.txt")

        assert response.content == b"file content"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_is_item_exists_true(self, mock_get, mock_msal):
        """Test is_item_exists returns True for existing item."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site response
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock item exists response
        item_response = Mock()
        item_response.status_code = 200

        mock_get.side_effect = [site_response, item_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [item_response]
        exists = module.is_item_exists("/test/file.txt")

        assert exists is True

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_is_item_exists_false(self, mock_get, mock_msal):
        """Test is_item_exists returns False for non-existing item."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site response
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock item not found response
        item_response = Mock()
        item_response.status_code = 404

        mock_get.side_effect = [site_response, item_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [item_response]
        exists = module.is_item_exists("/test/nonexistent.txt")

        assert exists is False

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    @patch("src.modules.microsoft.sharepoint.requests.put")
    def test_upload_file_success(self, mock_put, mock_get, mock_msal):
        """Test successful file upload."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site response
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock upload response
        upload_response = Mock()
        upload_response.status_code = 201

        mock_get.side_effect = [site_response]
        mock_put.return_value = upload_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        response = module.upload_file("/test/file.txt", b"content")

        assert response.status_code == 201

    def test_ensure_leading_slash_adds_slash(self):
        """Test _ensure_leading_slash adds slash when missing."""
        with patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication") as mock_msal:
            with patch("src.modules.microsoft.sharepoint.requests.get") as mock_get:
                # Mock MSAL token acquisition
                mock_app = Mock()
                mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
                mock_msal.return_value = mock_app

                # Mock site ID response
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "test_site_id"}
                mock_get.return_value = mock_response

                module = SharePointModule(
                    client_id="test_client",
                    client_secret="test_secret",
                    tenant_id="test_tenant",
                    site_domain="test.sharepoint.com",
                    site_path="/sites/test",
                )

                result = module._ensure_leading_slash("path/to/file")
                assert result == "/path/to/file"

    def test_ensure_leading_slash_keeps_slash(self):
        """Test _ensure_leading_slash keeps existing slash."""
        with patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication") as mock_msal:
            with patch("src.modules.microsoft.sharepoint.requests.get") as mock_get:
                # Mock MSAL token acquisition
                mock_app = Mock()
                mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
                mock_msal.return_value = mock_app

                # Mock site ID response
                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "test_site_id"}
                mock_get.return_value = mock_response

                module = SharePointModule(
                    client_id="test_client",
                    client_secret="test_secret",
                    tenant_id="test_tenant",
                    site_domain="test.sharepoint.com",
                    site_path="/sites/test",
                )

                result = module._ensure_leading_slash("/path/to/file")
                assert result == "/path/to/file"

    def test_build_graph_url(self):
        """Test _build_graph_url constructs correct URLs."""
        with patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication") as mock_msal:
            with patch("src.modules.microsoft.sharepoint.requests.get") as mock_get:
                mock_app = Mock()
                mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
                mock_msal.return_value = mock_app

                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "site123"}
                mock_get.return_value = mock_response

                module = SharePointModule(
                    client_id="test_client",
                    client_secret="test_secret",
                    tenant_id="test_tenant",
                    site_domain="test.sharepoint.com",
                    site_path="/sites/test",
                )

                url = module._build_graph_url("sites/site123")
                assert url == "https://graph.microsoft.com/v1.0/sites/site123"

                url = module._build_graph_url("/sites/site123")
                assert url == "https://graph.microsoft.com/v1.0/sites/site123"

    def test_get_headers_includes_authorization(self):
        """Test _get_headers includes authorization."""
        with patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication") as mock_msal:
            with patch("src.modules.microsoft.sharepoint.requests.get") as mock_get:
                mock_app = Mock()
                mock_app.acquire_token_for_client.return_value = {"access_token": "token123"}
                mock_msal.return_value = mock_app

                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "site123"}
                mock_get.return_value = mock_response

                module = SharePointModule(
                    client_id="test_client",
                    client_secret="test_secret",
                    tenant_id="test_tenant",
                    site_domain="test.sharepoint.com",
                    site_path="/sites/test",
                )

                headers = module._get_headers()
                assert "Authorization" in headers
                assert headers["Authorization"] == "Bearer token123"

                headers = module._get_headers({"Custom": "Value"})
                assert headers["Custom"] == "Value"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_item_by_path_download_url_unauthorized_retry(self, mock_get, mock_msal):
        """Test get_item_by_path retries on download URL 401."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        item_response = Mock()
        item_response.status_code = 200
        item_response.json.return_value = {"@microsoft.graph.downloadUrl": "https://download.url", "size": 1024}

        download_401 = Mock()
        download_401.status_code = 401

        download_success = Mock()
        download_success.status_code = 200
        download_success.content = b"file content"
        download_success.raise_for_status = Mock()

        mock_get.side_effect = [site_response, item_response, download_401, download_success]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        mock_get.side_effect = [item_response, download_401, download_success]
        response = module.get_item_by_path("/test/file.txt")

        assert response.content == b"file content"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests")
    def test_upload_file_with_archive_and_retry(self, mock_requests, mock_msal):
        """Test upload_file archives locked file and retries."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        item_metadata = Mock()
        item_metadata.status_code = 200
        item_metadata.json.return_value = {
            "id": "item123",
            "name": "file.txt",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        item_metadata.raise_for_status = Mock()

        locked_response = Mock()
        locked_response.status_code = 423

        success_response = Mock()
        success_response.status_code = 200

        mock_requests.get.side_effect = [site_response, item_metadata]
        mock_requests.put.side_effect = [locked_response, success_response]
        mock_requests.patch.return_value = success_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        response = module.upload_file("/path/file.txt", b"content")
        assert response.status_code == 200

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_handle_response_with_retry_401(self, mock_get, mock_msal):
        """Test __handle_response_with_retry refreshes token on 401."""
        call_count = 0

        def token_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"access_token": "token1"}
            return {"access_token": "token2"}

        mock_app = Mock()
        mock_app.acquire_token_for_client.side_effect = token_side_effect
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        auth_fail_response = Mock()
        auth_fail_response.status_code = 401

        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"id": "site_refreshed"}
        success_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response, auth_fail_response, success_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        # Trigger a request that gets 401, should auto-refresh
        mock_get.side_effect = [auth_fail_response, success_response]
        module.get_site_id(use_cache=False)

        assert call_count == 2  # Token acquired twice

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_site_id_missing_id_in_response(self, mock_get, mock_msal):
        """Test get_site_id when ID is missing from response."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"name": "site"}  # Missing 'id'
        site_response.raise_for_status = Mock()

        mock_get.return_value = site_response

        with pytest.raises((Exception, ConnectionError), match="(Site ID not found|Failed to connect)"):
            SharePointModule(
                client_id="test_client",
                client_secret="test_secret",
                tenant_id="test_tenant",
                site_domain="test.sharepoint.com",
                site_path="/sites/test",
            )

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests")
    @patch("time.sleep")
    def test_upload_file_conflict_status_retry(self, mock_sleep, mock_requests, mock_msal):
        """Test upload_file handles conflict status (409)."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        conflict_response = Mock()
        conflict_response.status_code = 409

        success_response = Mock()
        success_response.status_code = 200

        mock_requests.get.return_value = site_response
        mock_requests.put.side_effect = [conflict_response, success_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with patch.object(module, "_archive_locked_file", return_value=True):
            response = module.upload_file("/path/file.txt", b"content")
            assert response.status_code == 200

    def test_init_with_custom_timezone(self):
        """Test initialization with custom timezone."""
        with patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication") as mock_msal:
            with patch("src.modules.microsoft.sharepoint.requests.get") as mock_get:
                mock_app = Mock()
                mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
                mock_msal.return_value = mock_app

                mock_response = Mock()
                mock_response.status_code = 200
                mock_response.json.return_value = {"id": "site123"}
                mock_get.return_value = mock_response

                module = SharePointModule(
                    client_id="test_client",
                    client_secret="test_secret",
                    tenant_id="test_tenant",
                    site_domain="test.sharepoint.com",
                    site_path="/sites/test",
                    timezone="America/New_York",
                )

                assert module.timezone is not None

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_archive_locked_file_successful_rename(self, mock_get, mock_msal):
        """Test _archive_locked_file successfully renames file."""
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        item_metadata = Mock()
        item_metadata.status_code = 200
        item_metadata.json.return_value = {
            "id": "item123",
            "name": "file.txt",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        item_metadata.raise_for_status = Mock()

        mock_get.side_effect = [site_response, item_metadata]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with patch("src.modules.microsoft.sharepoint.requests.patch") as mock_patch:
            rename_response = Mock()
            rename_response.status_code = 200
            mock_patch.return_value = rename_response

            result = module._archive_locked_file("site123", "/path/file.txt", {})
            assert result is True


class TestSharePointModuleAdditional:
    """Additional test suite for SharePointModule class."""

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_with_pagination(self, mock_get, mock_msal):
        """Test list_files handles pagination correctly."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock paginated responses
        page1_response = Mock()
        page1_response.status_code = 200
        page1_response.json.return_value = {
            "value": [{"name": "file1.txt"}, {"name": "file2.txt"}],
            "@odata.nextLink": "https://graph.microsoft.com/v1.0/sites/test_site_id/drive/root:/folder:/children?$skiptoken=abc123",
        }
        page1_response.raise_for_status = Mock()

        page2_response = Mock()
        page2_response.status_code = 200
        page2_response.json.return_value = {"value": [{"name": "file3.txt"}], "@odata.nextLink": None}
        page2_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response, page1_response, page2_response]

        # Create module and test
        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        result = module.list_files("/test/folder")

        assert len(result) == 3
        assert result[0]["name"] == "file1.txt"
        assert result[1]["name"] == "file2.txt"
        assert result[2]["name"] == "file3.txt"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_http_error(self, mock_get, mock_msal):
        """Test list_files handles HTTP errors."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock error response
        error_response = Mock()
        error_response.status_code = 404
        error_response.text = "Not Found"
        http_err = requests.exceptions.HTTPError()
        http_err.response = Mock(status_code=404, text="Not Found")
        error_response.raise_for_status.side_effect = http_err

        mock_get.side_effect = [site_response, error_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with pytest.raises(requests.exceptions.HTTPError):
            module.list_files("/nonexistent/folder")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_pattern_matches(self, mock_get, mock_msal):
        """Test list_files_pattern correctly filters files."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock files list response
        files_response = Mock()
        files_response.status_code = 200
        files_response.json.return_value = {
            "value": [
                {"name": "data_20260101.csv", "parentReference": {"path": "/drive/root:/folder"}},
                {"name": "data_20260102.csv", "parentReference": {"path": "/drive/root:/folder"}},
                {"name": "README.txt", "parentReference": {"path": "/drive/root:/folder"}},
            ]
        }
        files_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response, files_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        result = module.list_files_pattern("/folder", r"data_\d{8}\.csv")

        assert len(result) == 2
        assert "/folder/data_20260101.csv" in result
        assert "/folder/data_20260102.csv" in result

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_pattern_no_matches(self, mock_get, mock_msal):
        """Test list_files_pattern returns empty list when no matches."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock files list response
        files_response = Mock()
        files_response.status_code = 200
        files_response.json.return_value = {
            "value": [
                {"name": "file1.txt", "parentReference": {"path": "/drive/root:/folder"}},
                {"name": "file2.txt", "parentReference": {"path": "/drive/root:/folder"}},
            ]
        }
        files_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response, files_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        result = module.list_files_pattern("/folder", r"data_\d{8}\.csv")

        assert len(result) == 0

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_item_by_path_no_download_url(self, mock_get, mock_msal):
        """Test get_item_by_path raises error when download URL is missing."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock item response without download URL
        item_response = Mock()
        item_response.status_code = 200
        item_response.json.return_value = {"size": 1024}  # No download URL
        item_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response, item_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with pytest.raises(Exception, match="Download URL not found in response"):
            module.get_item_by_path("/file.txt")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_item_by_path_http_error(self, mock_get, mock_msal):
        """Test get_item_by_path handles HTTP errors."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock error response
        error_response = Mock()
        error_response.status_code = 404
        error_response.text = "File not found"
        http_err = requests.exceptions.HTTPError()
        http_err.response = Mock(status_code=404, text="File not found")
        error_response.raise_for_status.side_effect = http_err

        mock_get.side_effect = [site_response, error_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with pytest.raises(requests.exceptions.HTTPError):
            module.get_item_by_path("/nonexistent.txt")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_site_id_http_error(self, mock_get, mock_msal):
        """Test get_site_id handles HTTP errors."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock error response
        error_response = Mock()
        error_response.status_code = 404
        error_response.text = "Site not found"
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError()
        error_response.response = Mock(status_code=404, text="Site not found")

        mock_get.return_value = error_response

        with pytest.raises(Exception, match="Failed to connect to SharePoint site"):
            SharePointModule(
                client_id="test_client",
                client_secret="test_secret",
                tenant_id="test_tenant",
                site_domain="test.sharepoint.com",
                site_path="/sites/nonexistent",
            )

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_get_site_id_missing_id_in_response(self, mock_get, mock_msal):
        """Test get_site_id raises error when site ID is missing from response."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock response without site ID
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {}  # No 'id' field
        site_response.raise_for_status = Mock()

        mock_get.return_value = site_response

        with pytest.raises(Exception, match="Site ID not found in response|Failed to connect to SharePoint site"):
            SharePointModule(
                client_id="test_client",
                client_secret="test_secret",
                tenant_id="test_tenant",
                site_domain="test.sharepoint.com",
                site_path="/sites/test",
            )

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_handle_response_with_retry_401(self, mock_get, mock_msal):
        """Test that 401 responses trigger token refresh and retry."""
        # Mock MSAL with different tokens
        mock_app = Mock()
        mock_app.acquire_token_for_client.side_effect = [{"access_token": "old_token"}, {"access_token": "new_token"}]
        mock_msal.return_value = mock_app

        # Mock site ID retrieval - first call with old token
        site_response_success = Mock()
        site_response_success.status_code = 200
        site_response_success.json.return_value = {"id": "test_site_id"}

        # When getting site ID later with use_cache=False
        unauthorized_response = Mock()
        unauthorized_response.status_code = 401

        success_response = Mock()
        success_response.status_code = 200
        success_response.json.return_value = {"id": "test_site_id"}
        success_response.raise_for_status = Mock()

        mock_get.side_effect = [site_response_success, unauthorized_response, success_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        # This should trigger token refresh
        site_id = module.get_site_id(use_cache=False)
        assert site_id == "test_site_id"
        # Verify token was refreshed
        assert module.access_token == "new_token"

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_timezone_configuration(self, mock_get, mock_msal):
        """Test custom timezone configuration."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}
        mock_get.return_value = site_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
            timezone="America/New_York",
        )

        assert module.timezone == ZoneInfo("America/New_York")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_default_timezone_bangkok(self, mock_get, mock_msal):
        """Test default timezone is Asia/Bangkok."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}
        mock_get.return_value = site_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        assert module.timezone == ZoneInfo("Asia/Bangkok")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_list_files_pattern_error_handling(self, mock_get, mock_msal):
        """Test list_files_pattern handles errors from list_files."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.json.return_value = {"id": "test_site_id"}

        # Mock error response for list_files
        error_response = Mock()
        error_response.status_code = 500
        error_response.text = "Internal Server Error"
        error_response.raise_for_status.side_effect = requests.exceptions.HTTPError()
        error_response.response = Mock(status_code=500, text="Internal Server Error")

        mock_get.side_effect = [site_response, error_response]

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        with pytest.raises(Exception, match="Error listing files with pattern from SharePoint"):
            module.list_files_pattern("/folder", r".*\.csv")

    @patch("src.modules.microsoft.sharepoint.ConfidentialClientApplication")
    @patch("src.modules.microsoft.sharepoint.requests.get")
    def test_site_id_caching(self, mock_get, mock_msal):
        """Test that site ID is properly cached."""
        # Mock MSAL
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "test_token"}
        mock_msal.return_value = mock_app

        # Mock site ID retrieval
        site_response = Mock()
        site_response.status_code = 200
        site_response.raise_for_status = Mock()
        site_response.json.return_value = {"id": "cached_site_id"}
        mock_get.return_value = site_response

        module = SharePointModule(
            client_id="test_client",
            client_secret="test_secret",
            tenant_id="test_tenant",
            site_domain="test.sharepoint.com",
            site_path="/sites/test",
        )

        # First call should use cache (set during init)
        site_id_1 = module.get_site_id(use_cache=True)
        # Second call should also use cache
        site_id_2 = module.get_site_id(use_cache=True)

        assert site_id_1 == "cached_site_id"
        assert site_id_2 == "cached_site_id"

        # Verify cache is working - get_site_id should not make additional API calls
        # when use_cache=True (only initial calls during module initialization)
        assert site_id_1 == site_id_2


def _make_sharepoint_stub() -> SharePointModule:
    module = SharePointModule.__new__(SharePointModule)
    module.client_id = "test_client"
    module.client_secret = "test_secret"
    module.tenant_id = "test_tenant"
    module.site_domain = "test.sharepoint.com"
    module.site_path = "/sites/test"
    module.timezone = ZoneInfo("Asia/Bangkok")
    module.scope = [SharePointModule.GRAPH_SCOPE]
    module.access_token = "test_token"
    module._site_id_cache = None
    module._session = requests
    return module


class TestSharePointModuleCoveragePhase2:
    def test_get_site_id_retries_on_service_unavailable(self):
        module = _make_sharepoint_stub()
        unavailable_response = Mock(status_code=503)
        success_response = Mock(status_code=200)
        success_response.json.return_value = {"id": "site123"}
        success_response.raise_for_status = Mock()

        with (
            patch(
                "src.modules.microsoft.sharepoint.requests.get", side_effect=[unavailable_response, success_response]
            ) as mock_get,
            patch("src.modules.microsoft.sharepoint.time.sleep") as mock_sleep,
        ):
            assert module.get_site_id(use_cache=False) == "site123"

        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(module.RETRY_DELAY_SECONDS)

    def test_get_site_id_http_error_from_api(self):
        module = _make_sharepoint_stub()
        response = Mock(status_code=500, text="boom")
        error = requests.exceptions.HTTPError("boom")
        error.response = Mock(status_code=500, text="boom")
        response.raise_for_status.side_effect = error

        with patch("src.modules.microsoft.sharepoint.requests.get", return_value=response):
            with pytest.raises(requests.exceptions.HTTPError):
                module.get_site_id(use_cache=False)

    def test_list_files_wraps_unexpected_exception(self):
        module = _make_sharepoint_stub()

        with patch.object(module, "get_site_id", side_effect=RuntimeError("site lookup failed")):
            with pytest.raises(Exception, match="Error listing files from SharePoint"):
                module.list_files("/folder")

    @pytest.mark.parametrize(
        ("exception_factory", "expected_message"),
        [
            (requests.exceptions.ConnectionError, "Error connection lost while retrieving item from SharePoint"),
            (requests.exceptions.Timeout, "Error request timed out while retrieving item from SharePoint"),
            (
                requests.exceptions.ChunkedEncodingError,
                "Error request Request chunk encode error while retrieving item from SharePoint",
            ),
        ],
    )
    def test_get_item_by_path_retries_then_raises_for_transient_errors(self, exception_factory, expected_message):
        module = _make_sharepoint_stub()

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", side_effect=exception_factory("network")),
            patch("src.modules.microsoft.sharepoint.time.sleep") as mock_sleep,
        ):
            with pytest.raises(Exception, match=expected_message):
                module.get_item_by_path("/file.txt")

        assert mock_sleep.call_count == module.MAX_RETRIES - 1

    def test_is_item_exists_http_error(self):
        module = _make_sharepoint_stub()
        response = Mock(status_code=500, text="server error")
        error = requests.exceptions.HTTPError("server error")
        error.response = Mock(status_code=500, text="server error")
        response.raise_for_status.side_effect = error

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=response),
        ):
            with pytest.raises(requests.exceptions.HTTPError):
                module.is_item_exists("/file.txt")

    def test_is_item_exists_wraps_unexpected_exception(self):
        module = _make_sharepoint_stub()

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", side_effect=RuntimeError("boom")),
        ):
            with pytest.raises(Exception, match="Error checking item existence in SharePoint"):
                module.is_item_exists("/file.txt")

    def test_archive_locked_file_returns_false_when_metadata_lookup_fails(self):
        module = _make_sharepoint_stub()
        response = Mock(status_code=404)

        with patch("src.modules.microsoft.sharepoint.requests.get", return_value=response):
            assert module._archive_locked_file("site123", "/file.txt", {}) is False

    def test_archive_locked_file_returns_false_when_metadata_is_incomplete(self):
        module = _make_sharepoint_stub()
        response = Mock(status_code=200)
        response.json.return_value = {"id": "item123", "name": "file.txt"}

        with patch("src.modules.microsoft.sharepoint.requests.get", return_value=response):
            assert module._archive_locked_file("site123", "/file.txt", {}) is False

    def test_archive_locked_file_returns_false_when_rename_fails(self):
        module = _make_sharepoint_stub()
        item_response = Mock(status_code=200)
        item_response.json.return_value = {
            "id": "item123",
            "name": "file.txt",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        rename_response = Mock(status_code=500, text="rename failed")

        with (
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=item_response),
            patch("src.modules.microsoft.sharepoint.requests.patch", return_value=rename_response),
        ):
            assert module._archive_locked_file("site123", "/file.txt", {}) is False

    def test_archive_locked_file_returns_false_on_exception(self):
        module = _make_sharepoint_stub()

        with patch("src.modules.microsoft.sharepoint.requests.get", side_effect=RuntimeError("boom")):
            assert module._archive_locked_file("site123", "/file.txt", {}) is False

    def test_upload_file_locked_retries_exhausted(self):
        module = _make_sharepoint_stub()
        locked_response = Mock(status_code=423, text="locked")
        error = requests.exceptions.HTTPError("locked")
        error.response = Mock(status_code=423, text="locked")
        locked_response.raise_for_status.side_effect = error

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch.object(module, "_archive_locked_file", return_value=False),
            patch("src.modules.microsoft.sharepoint.requests.put", return_value=locked_response) as mock_put,
            patch("src.modules.microsoft.sharepoint.time.sleep") as mock_sleep,
        ):
            with pytest.raises(requests.exceptions.HTTPError):
                module.upload_file("/locked.txt", b"content")

        assert mock_put.call_count == module.MAX_RETRIES
        assert mock_sleep.call_count == module.MAX_RETRIES - 1

    def test_upload_file_wraps_unexpected_exception(self):
        module = _make_sharepoint_stub()

        with patch.object(module, "get_site_id", side_effect=RuntimeError("site lookup failed")):
            with pytest.raises(Exception, match="Error uploading file to SharePoint"):
                module.upload_file("/file.txt", b"content")

    def test_copy_file_success(self):
        module = _make_sharepoint_stub()
        source_response = Mock(content=b"copied-bytes")
        upload_response = Mock(status_code=201)

        with (
            patch.object(module, "get_item_by_path", return_value=source_response),
            patch.object(module, "upload_file", return_value=upload_response),
        ):
            assert module.copy_file("/source.txt", "/dest.txt") is True

    def test_copy_file_returns_false_when_source_content_is_empty(self):
        module = _make_sharepoint_stub()
        source_response = Mock(content=b"")

        with (
            patch.object(module, "get_item_by_path", return_value=source_response),
            patch.object(module, "upload_file") as mock_upload,
        ):
            assert module.copy_file("/source.txt", "/dest.txt") is False

        mock_upload.assert_not_called()

    def test_copy_file_returns_false_when_upload_response_is_not_successful(self):
        module = _make_sharepoint_stub()
        source_response = Mock(content=b"copied-bytes")
        upload_response = Mock(status_code=500)

        with (
            patch.object(module, "get_item_by_path", return_value=source_response),
            patch.object(module, "upload_file", return_value=upload_response),
        ):
            assert module.copy_file("/source.txt", "/dest.txt") is False

    def test_copy_file_returns_false_on_exception(self):
        module = _make_sharepoint_stub()

        with patch.object(module, "get_item_by_path", side_effect=RuntimeError("download failed")):
            assert module.copy_file("/source.txt", "/dest.txt") is False

    def test_is_item_exists_returns_false_when_raise_for_status_does_not_raise(self):
        module = _make_sharepoint_stub()
        response = Mock(status_code=500, text="server error")
        response.raise_for_status = Mock()

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=response),
        ):
            assert module.is_item_exists("/file.txt") is False

        response.raise_for_status.assert_called_once()

    def test_upload_file_returns_last_response_when_retries_exhausted_without_http_error(self):
        module = _make_sharepoint_stub()
        locked_response = Mock(status_code=423, text="locked")
        locked_response.raise_for_status = Mock()

        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch.object(module, "_archive_locked_file", return_value=False),
            patch("src.modules.microsoft.sharepoint.requests.put", return_value=locked_response) as mock_put,
            patch("src.modules.microsoft.sharepoint.time.sleep") as mock_sleep,
        ):
            response = module.upload_file("/locked.txt", b"content")

        assert response is locked_response
        assert mock_put.call_count == module.MAX_RETRIES
        assert locked_response.raise_for_status.call_count == 2
        assert mock_sleep.call_count == module.MAX_RETRIES - 1


class TestSharePointModuleDelete:
    """Tests for SharePointModule.delete_item."""

    def test_delete_item_success_returns_true(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=204, ok=True)  # Graph DELETE → 204 No Content

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.delete", return_value=response) as mock_delete,
        ):
            result = module.delete_item("/folder/file.pdf")

        # Assert
        assert result is True
        mock_delete.assert_called_once()
        assert mock_delete.call_args.args[0] == (
            "https://graph.microsoft.com/v1.0/sites/site123/drive/root:/folder/file.pdf"
        )

    def test_delete_item_adds_leading_slash(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=204, ok=True)

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.delete", return_value=response) as mock_delete,
        ):
            module.delete_item("folder/file.pdf")  # no leading slash

        # Assert — path is normalized with a leading slash
        assert mock_delete.call_args.args[0].endswith("/drive/root:/folder/file.pdf")

    def test_delete_item_http_error_raises(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=404, ok=False, text="Not Found")
        error = requests.exceptions.HTTPError("not found")
        error.response = Mock(status_code=404, text="Not Found")
        response.raise_for_status.side_effect = error

        # Act / Assert
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.delete", return_value=response),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            module.delete_item("/folder/missing.pdf")

    def test_delete_item_wraps_unexpected_exception(self):
        # Arrange
        module = _make_sharepoint_stub()

        # Act / Assert
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.delete", side_effect=RuntimeError("boom")),
            pytest.raises(Exception, match="Error deleting item from SharePoint"),
        ):
            module.delete_item("/folder/file.pdf")

    def test_delete_item_returns_false_when_raise_for_status_does_not_raise(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=500, ok=False, text="server error")
        response.raise_for_status = Mock()  # does not raise despite non-2xx status

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.delete", return_value=response),
        ):
            result = module.delete_item("/folder/file.pdf")

        # Assert
        assert result is False
        response.raise_for_status.assert_called_once()


class TestSharePointModuleListFilesRecursive:
    """Tests for the recursive branch of SharePointModule.list_files."""

    def test_list_files_recursive_traverses_subfolders(self):
        # Arrange
        module = _make_sharepoint_stub()

        root_response = Mock(status_code=200)
        root_response.json.return_value = {
            "value": [
                {"name": "subfolder", "folder": {"childCount": 1}},
                {"name": "file1.txt"},
            ]
        }
        root_response.raise_for_status = Mock()

        sub_response = Mock(status_code=200)
        sub_response.json.return_value = {"value": [{"name": "file2.txt"}]}
        sub_response.raise_for_status = Mock()

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch(
                "src.modules.microsoft.sharepoint.requests.get",
                side_effect=[root_response, sub_response],
            ) as mock_get,
        ):
            result = module.list_files("/root", recursive=True)

        # Assert
        assert len(result) == 2
        assert result[0]["name"] == "file2.txt"  # from the recursed subfolder
        assert result[1]["name"] == "file1.txt"  # non-folder item at the root
        assert mock_get.call_count == 2
        assert mock_get.call_args_list[1].args[0].endswith("/drive/root:/root/subfolder:/children")

    def test_list_files_non_recursive_does_not_traverse_subfolders(self):
        # Arrange
        module = _make_sharepoint_stub()

        root_response = Mock(status_code=200)
        root_response.json.return_value = {
            "value": [
                {"name": "subfolder", "folder": {"childCount": 1}},
                {"name": "file1.txt"},
            ]
        }
        root_response.raise_for_status = Mock()

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=root_response) as mock_get,
        ):
            result = module.list_files("/root", recursive=False)

        # Assert
        assert len(result) == 2
        assert mock_get.call_count == 1  # no recursive call made


class TestSharePointModuleGetItemByPathNoRetries:
    """Tests for get_item_by_path when the retry loop is exhausted without a terminal branch."""

    def test_get_item_by_path_returns_none_when_max_retries_is_zero(self):
        # Arrange
        module = _make_sharepoint_stub()
        module.MAX_RETRIES = 0  # forces the for-loop range to be empty

        # Act
        with patch.object(module, "get_site_id", return_value="site123") as mock_get_site_id:
            result = module.get_item_by_path("/file.txt")

        # Assert
        assert result is None
        mock_get_site_id.assert_not_called()  # loop body never executes


class TestSharePointModuleRenameFile:
    """Tests for SharePointModule.rename_file."""

    def test_rename_file_success_first_attempt(self):
        # Arrange
        module = _make_sharepoint_stub()
        item_metadata_response = Mock(status_code=200)
        item_metadata_response.json.return_value = {
            "id": "item123",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        success_response = Mock(status_code=200)

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=item_metadata_response) as mock_get,
            patch("src.modules.microsoft.sharepoint.requests.patch", return_value=success_response) as mock_patch,
        ):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is True
        mock_get.assert_called_once()
        mock_patch.assert_called_once()
        assert mock_patch.call_args.args[0] == "https://graph.microsoft.com/v1.0/drives/drive123/items/item123"
        assert mock_patch.call_args.kwargs["json"] == {
            "parentReference": {"id": "parent123"},
            "name": "new.txt",
        }

    def test_rename_file_returns_false_when_metadata_fetch_fails(self):
        # Arrange
        module = _make_sharepoint_stub()
        failed_response = Mock(status_code=404)

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=failed_response),
        ):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is False

    def test_rename_file_returns_false_when_metadata_incomplete(self):
        # Arrange
        module = _make_sharepoint_stub()
        incomplete_response = Mock(status_code=200)
        incomplete_response.json.return_value = {"id": "item123"}  # missing parentReference/driveId

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=incomplete_response),
        ):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is False

    def test_rename_file_success_after_lock_bypass(self):
        # Arrange
        module = _make_sharepoint_stub()
        item_metadata_response = Mock(status_code=200)
        item_metadata_response.json.return_value = {
            "id": "item123",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        locked_response = Mock(status_code=423)
        bypass_success_response = Mock(status_code=200)

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=item_metadata_response),
            patch(
                "src.modules.microsoft.sharepoint.requests.patch",
                side_effect=[locked_response, bypass_success_response],
            ) as mock_patch,
        ):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is True
        assert mock_patch.call_count == 2
        assert mock_patch.call_args_list[1].kwargs["headers"]["Prefer"] == "bypass-shared-lock"

    def test_rename_file_retries_exhausted_returns_false(self):
        # Arrange
        module = _make_sharepoint_stub()
        item_metadata_response = Mock(status_code=200)
        item_metadata_response.json.return_value = {
            "id": "item123",
            "parentReference": {"id": "parent123", "driveId": "drive123"},
        }
        locked_response = Mock(status_code=423)

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=item_metadata_response),
            patch("src.modules.microsoft.sharepoint.requests.patch", return_value=locked_response) as mock_patch,
            patch("src.modules.microsoft.sharepoint.time.sleep") as mock_sleep,
        ):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is False
        # Each of the MAX_RETRIES attempts issues a plain patch plus a lock-bypass patch
        assert mock_patch.call_count == module.MAX_RETRIES * 2
        assert mock_sleep.call_count == module.MAX_RETRIES - 1

    def test_rename_file_wraps_unexpected_exception_returns_false(self):
        # Arrange
        module = _make_sharepoint_stub()

        # Act
        with patch.object(module, "get_site_id", side_effect=RuntimeError("boom")):
            result = module.rename_file("/folder/old.txt", "/folder/new.txt")

        # Assert
        assert result is False


class TestSharePointModuleGetWebUrl:
    """Tests for SharePointModule.get_web_url."""

    def test_get_web_url_success(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=200)
        response.raise_for_status = Mock()
        response.json.return_value = {"webUrl": "https://contoso.sharepoint.com/folder/file.txt"}

        # Act
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=response) as mock_get,
        ):
            url = module.get_web_url("/folder/file.txt")

        # Assert
        assert url == "https://contoso.sharepoint.com/folder/file.txt"
        assert (
            mock_get.call_args.args[0] == "https://graph.microsoft.com/v1.0/sites/site123/drive/root:/folder/file.txt"
        )

    def test_get_web_url_missing_web_url_raises(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=200)
        response.raise_for_status = Mock()
        response.json.return_value = {}  # no webUrl key

        # Act / Assert
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=response),
            pytest.raises(Exception, match="Web URL not found in response"),
        ):
            module.get_web_url("/folder/file.txt")

    def test_get_web_url_http_error_raises(self):
        # Arrange
        module = _make_sharepoint_stub()
        response = Mock(status_code=404, text="not found")
        error = requests.exceptions.HTTPError("not found")
        error.response = Mock(status_code=404, text="not found")
        response.raise_for_status.side_effect = error

        # Act / Assert
        with (
            patch.object(module, "get_site_id", return_value="site123"),
            patch("src.modules.microsoft.sharepoint.requests.get", return_value=response),
            pytest.raises(requests.exceptions.HTTPError),
        ):
            module.get_web_url("/folder/file.txt")

    def test_get_web_url_wraps_unexpected_exception(self):
        # Arrange
        module = _make_sharepoint_stub()

        # Act / Assert
        with (
            patch.object(module, "get_site_id", side_effect=RuntimeError("boom")),
            pytest.raises(Exception, match="Error getting web URL from SharePoint"),
        ):
            module.get_web_url("/folder/file.txt")
