"""EmailService — Microsoft Graph API email sender.

Extracts ``send_outlook_graph_api()`` from ``utility.py`` into a class so
credentials are injected once at construction time rather than fetched inside
every call.
"""
from __future__ import annotations

import base64
import mimetypes

from azure.identity.aio import ClientSecretCredential
from msgraph import GraphServiceClient
from msgraph.generated.models.body_type import BodyType
from msgraph.generated.models.email_address import EmailAddress
from msgraph.generated.models.file_attachment import FileAttachment
from msgraph.generated.models.item_body import ItemBody
from msgraph.generated.models.message import Message
from msgraph.generated.models.recipient import Recipient
from msgraph.generated.users.item.send_mail.send_mail_post_request_body import (
    SendMailPostRequestBody,
)

from app.core.exceptions import EmailServiceError
from app.share_log import get_logger

logger = get_logger(__name__)


class EmailService:
    """Sends email via Microsoft Graph API using Client Credentials flow."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        sender_email: str,
    ) -> None:
        self._tenant_id = tenant_id
        self._client_id = client_id
        self._client_secret = client_secret
        self._sender_email = sender_email

    async def send(
        self,
        to_recipients: list[str] | None = None,
        cc_recipients: list[str] | None = None,
        bcc_recipients: list[str] | None = None,
        subject: str = "",
        body_html: str = "",
        attachments: dict[str, str] | None = None,
        inline_images: dict[str, str] | None = None,
    ) -> None:
        """Send an HTML email via MS Graph API.

        Args:
            to_recipients: To email addresses.
            cc_recipients: CC email addresses.
            bcc_recipients: BCC email addresses.
            subject: Email subject line.
            body_html: Full HTML body content.
            attachments: ``{filename: base64_string}`` file attachments.
            inline_images: ``{cid_name: base64_string}`` inline images.

        Raises:
            EmailServiceError: If email sending fails.
        """
        try:
            credential = ClientSecretCredential(
                tenant_id=self._tenant_id,
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            graph_client = GraphServiceClient(credentials=credential)

            message = Message()
            message.subject = subject
            message.body = ItemBody()
            message.body.content_type = BodyType.Html
            message.body.content = body_html
            if to_recipients is not None:
                message.to_recipients = [
                    Recipient(email_address=EmailAddress(address=addr))
                    for addr in to_recipients
                ]
            if cc_recipients is not None:
                message.cc_recipients = [
                    Recipient(email_address=EmailAddress(address=addr))
                    for addr in cc_recipients
                ]
            if bcc_recipients is not None:
                message.bcc_recipients = [
                    Recipient(email_address=EmailAddress(address=addr))
                    for addr in bcc_recipients
                ]

            all_attachments: list[FileAttachment] = []

            if attachments:
                for filename, file_b64 in attachments.items():
                    att = FileAttachment()
                    att.odata_type = "#microsoft.graph.fileAttachment"
                    att.name = filename
                    mime_type, _ = mimetypes.guess_type(filename)
                    att.content_type = mime_type or "application/octet-stream"
                    att.content_bytes = base64.b64decode(file_b64)
                    all_attachments.append(att)

            if inline_images:
                for cid, file_b64 in inline_images.items():
                    att = FileAttachment()
                    att.odata_type = "#microsoft.graph.fileAttachment"
                    att.name = cid
                    mime_type, _ = mimetypes.guess_type(cid)
                    att.content_type = mime_type or "image/png"
                    att.content_bytes = base64.b64decode(file_b64)
                    att.content_id = cid
                    att.is_inline = True
                    all_attachments.append(att)

            if all_attachments:
                message.attachments = all_attachments

            request_body = SendMailPostRequestBody(message=message, save_to_sent_items=True)
            await graph_client.users.by_user_id(self._sender_email).send_mail.post(request_body)

            logger.info(f"Email sent to {to_recipients}, {cc_recipients}, and {bcc_recipients} from {self._sender_email}")
        except EmailServiceError:
            raise
        except Exception as exc:
            logger.error(f"Failed to send email from {self._sender_email}: {exc}")
            raise EmailServiceError(
                f"Failed to send email from {self._sender_email}: {exc}",
                details={
                    "sender_email": self._sender_email,
                    "to_recipients": to_recipients,
                    "subject": subject,
                }
            ) from exc
