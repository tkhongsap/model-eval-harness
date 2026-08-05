"""Unit tests for EmailService."""
from __future__ import annotations

import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.email_service import EmailService


@pytest.fixture()
def svc() -> EmailService:
    return EmailService("tenant_id", "client_id", "client_secret", "sender@example.com")


def _make_graph_client() -> MagicMock:
    """Return a mock GraphServiceClient with an awaitable send_mail.post."""
    mock_graph = MagicMock()
    mock_graph.users.by_user_id.return_value.send_mail.post = AsyncMock()
    return mock_graph


class TestSend:
    async def test_sends_email_with_no_attachments(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["recipient@example.com"],
                    subject="Test Subject",
                    body_html="<p>Hello</p>",
                )
        mock_graph.users.by_user_id.assert_called_once_with("sender@example.com")
        mock_graph.users.by_user_id.return_value.send_mail.post.assert_awaited_once()

    async def test_includes_file_attachment_with_known_mime(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        file_b64 = base64.b64encode(b"file content").decode()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>body</p>",
                    attachments={"report.xlsx": file_b64},
                )
        post_call = mock_graph.users.by_user_id.return_value.send_mail.post
        post_call.assert_awaited_once()
        request_body = post_call.call_args[0][0]
        assert request_body.message.attachments is not None
        assert len(request_body.message.attachments) == 1
        att = request_body.message.attachments[0]
        assert att.name == "report.xlsx"
        assert att.content_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

    async def test_attachment_with_unknown_mime_uses_octet_stream(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        file_b64 = base64.b64encode(b"data").decode()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                    attachments={"unknown.xyz": file_b64},
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        att = request_body.message.attachments[0]
        assert att.content_type == "application/octet-stream"

    async def test_includes_inline_image(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        img_b64 = base64.b64encode(b"png data").decode()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                    inline_images={"photo.png": img_b64},
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        att = request_body.message.attachments[0]
        assert att.is_inline is True
        assert att.content_id == "photo.png"

    async def test_inline_image_with_unknown_mime_uses_image_png(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        img_b64 = base64.b64encode(b"data").decode()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                    inline_images={"noext": img_b64},
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        att = request_body.message.attachments[0]
        assert att.content_type == "image/png"

    async def test_combined_attachment_and_inline_image(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        b64 = base64.b64encode(b"data").decode()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                    attachments={"file.pdf": b64},
                    inline_images={"img.png": b64},
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        assert len(request_body.message.attachments) == 2

    async def test_no_attachments_field_when_both_none(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["r@example.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                    attachments=None,
                    inline_images=None,
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        # attachments not set means it stays as the Message default (None)
        assert request_body.message.attachments is None

    async def test_bcc_recipients_set_correctly(self, svc: EmailService) -> None:
        mock_graph = _make_graph_client()
        with patch("app.services.email_service.ClientSecretCredential"):
            with patch("app.services.email_service.GraphServiceClient", return_value=mock_graph):
                await svc.send(
                    recipients=["a@x.com", "b@x.com"],
                    subject="Sub",
                    body_html="<p>b</p>",
                )
        request_body = mock_graph.users.by_user_id.return_value.send_mail.post.call_args[0][0]
        addresses = [r.email_address.address for r in request_body.message.bcc_recipients]
        assert addresses == ["a@x.com", "b@x.com"]
