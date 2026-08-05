import base64
from unittest.mock import Mock, patch

import pytest
import requests

from src.modules.microsoft.msgraph import MSGraphModule


def _make_response(status_code: int, ok: bool) -> Mock:
    response = Mock()
    response.status_code = status_code
    response.ok = ok
    response.raise_for_status = Mock()
    return response


class TestMSGraphModule:
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_init_success(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        assert module.access_token == "token-1"
        assert module.max_retries == MSGraphModule.MAX_RETRIES
        assert module.retry_delay_seconds == MSGraphModule.RETRY_DELAY_SECONDS
        mock_msal.assert_called_once_with(
            "client-id",
            authority="https://login.microsoftonline.com/tenant-id",
            client_credential="client-secret",
            http_client=module._session,
        )

    def test_init_missing_authentication_params(self):
        with pytest.raises(ValueError, match="Missing required authentication parameters"):
            MSGraphModule(client_id="client-id", tenant_id="tenant-id")

    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_init_raises_when_token_acquisition_fails(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"error_description": "bad credentials"}
        mock_msal.return_value = mock_app

        with pytest.raises(Exception, match="Failed to acquire token: bad credentials"):
            MSGraphModule(
                client_id="client-id",
                client_secret="client-secret",
                tenant_id="tenant-id",
            )

    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_refresh_token_updates_access_token(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.side_effect = [
            {"access_token": "token-1"},
            {"access_token": "token-2"},
        ]
        mock_msal.return_value = mock_app

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )
        module._refresh_token()

        assert module.access_token == "token-2"

    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_get_headers_includes_auth_and_content_type(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        assert module._get_headers() == {
            "Authorization": "Bearer token-1",
            "Content-Type": "application/json",
        }

    @patch("src.modules.microsoft.msgraph.requests.request")
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_request_success(self, mock_msal, mock_request):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app
        mock_request.return_value = _make_response(200, True)

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        response = module._request("GET", "/me")

        assert response.status_code == 200
        mock_request.assert_called_once_with(
            "GET",
            "https://graph.microsoft.com/v1.0/me",
            headers=module._get_headers(),
        )

    @patch("src.modules.microsoft.msgraph.requests.request")
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_request_refreshes_token_after_401(self, mock_msal, mock_request):
        mock_app = Mock()
        mock_app.acquire_token_for_client.side_effect = [
            {"access_token": "token-1"},
            {"access_token": "token-2"},
        ]
        mock_msal.return_value = mock_app

        unauthorized = _make_response(401, False)
        success = _make_response(200, True)
        mock_request.side_effect = [unauthorized, success]

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        response = module._request("GET", "/me")

        assert response is success
        assert module.access_token == "token-2"
        assert mock_request.call_count == 2

    @patch("src.modules.microsoft.msgraph.time.sleep")
    @patch("src.modules.microsoft.msgraph.requests.request")
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_request_retries_transient_failure_then_succeeds(self, mock_msal, mock_request, mock_sleep):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        transient = _make_response(429, False)
        success = _make_response(200, True)
        mock_request.side_effect = [transient, success]

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        response = module._request("GET", "/me")

        assert response is success
        mock_sleep.assert_called_once_with(module.retry_delay_seconds)
        assert mock_request.call_count == 2

    @patch("src.modules.microsoft.msgraph.time.sleep")
    @patch("src.modules.microsoft.msgraph.requests.request")
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_request_raises_after_retry_limit(self, mock_msal, mock_request, mock_sleep):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        retry_response = _make_response(503, False)
        failure_response = _make_response(503, False)
        failure_response.raise_for_status.side_effect = requests.HTTPError("service unavailable")
        mock_request.side_effect = [retry_response, failure_response]

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
            max_retries=2,
        )

        with pytest.raises(requests.HTTPError, match="service unavailable"):
            module._request("GET", "/me")

        mock_sleep.assert_called_once_with(module.retry_delay_seconds)
        assert mock_request.call_count == 2

    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_send_email_builds_payload_with_string_recipients_and_attachments(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        with patch.object(module, "_request") as mock_request:
            module.send_email(
                subject="Subject",
                body="<b>Hello</b>",
                sender_email="sender@example.com",
                receiver_email="a@example.com, b@example.com ",
                cc_email=["c@example.com", ""],
                attachments=[
                    {
                        "name": "report.txt",
                        "content_type": "text/plain",
                        "data": b"hello",
                    }
                ],
            )

        args, kwargs = mock_request.call_args
        payload = kwargs["json"]
        assert args == ("POST", "/users/sender@example.com/sendMail")
        assert payload["message"]["toRecipients"] == [
            {"emailAddress": {"address": "a@example.com"}},
            {"emailAddress": {"address": "b@example.com"}},
        ]
        assert payload["message"]["ccRecipients"] == [
            {"emailAddress": {"address": "c@example.com"}},
        ]
        assert payload["message"]["attachments"][0]["contentBytes"] == base64.b64encode(b"hello").decode("utf-8")
        assert payload["saveToSentItems"] is True

    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_send_email_filters_empty_list_entries_without_attachments(self, mock_msal):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
        )

        with patch.object(module, "_request") as mock_request:
            module.send_email(
                subject="Subject",
                body="Hello",
                sender_email="sender@example.com",
                receiver_email=["a@example.com", "", "b@example.com"],
            )

        payload = mock_request.call_args.kwargs["json"]
        assert payload["message"]["toRecipients"] == [
            {"emailAddress": {"address": "a@example.com"}},
            {"emailAddress": {"address": "b@example.com"}},
        ]
        assert payload["message"]["ccRecipients"] == []
        assert "attachments" not in payload["message"]

    @patch("src.modules.microsoft.msgraph.time.sleep")
    @patch("src.modules.microsoft.msgraph.requests.request")
    @patch("src.modules.microsoft.msgraph.ConfidentialClientApplication")
    def test_request_calls_raise_for_status_again_after_retry_loop(self, mock_msal, mock_request, mock_sleep):
        mock_app = Mock()
        mock_app.acquire_token_for_client.return_value = {"access_token": "token-1"}
        mock_msal.return_value = mock_app

        retry_response = _make_response(503, False)
        final_response = _make_response(503, False)
        mock_request.side_effect = [retry_response, final_response]

        module = MSGraphModule(
            client_id="client-id",
            client_secret="client-secret",
            tenant_id="tenant-id",
            max_retries=2,
        )

        response = module._request("GET", "/me")

        assert response is None
        assert final_response.raise_for_status.call_count == 2
        mock_sleep.assert_called_once_with(module.retry_delay_seconds)
