# Library imports
import base64
import time
from typing import Any

import requests
from msal import ConfidentialClientApplication

# Source code imports
from src.modules.security.tls import TlsPolicy
from src.utils.logger import Logger

logger = Logger(__name__)


class MSGraphModule:
    """
    Microsoft Graph module for interacting with Microsoft Graph API.
    Uses sync MSAL + requests — consistent with SharePointModule.
    Handles authentication (Client Credentials Flow) and email sending.
    """

    GRAPH_API_BASE = "https://graph.microsoft.com/v1.0"
    GRAPH_SCOPE = "https://graph.microsoft.com/.default"
    LOGIN_AUTHORITY_BASE = "https://login.microsoftonline.com"

    MAX_RETRIES = 3
    RETRY_DELAY_SECONDS = 10
    RETRY_STATUS_CODES = [429, 503, 504]

    STATUS_UNAUTHORIZED = 401

    def __init__(self, **kwargs):
        """
        Initialize Microsoft Graph module with authentication configuration.

        Parameters:
            client_id (str): Azure AD application client ID
            client_secret (str): Azure AD application client secret
            tenant_id (str): Azure AD tenant ID
            max_retries (int, optional): Max retry attempts for transient errors. Defaults to MAX_RETRIES (3).
            retry_delay_seconds (int, optional): Seconds to wait between retries. Defaults to RETRY_DELAY_SECONDS (10).
        """
        logger.info("Initializing Microsoft Graph Module...")

        self.client_id = kwargs.get("client_id")
        self.client_secret = kwargs.get("client_secret")
        self.tenant_id = kwargs.get("tenant_id")
        self.max_retries = kwargs.get("max_retries", self.MAX_RETRIES)
        self.retry_delay_seconds = kwargs.get("retry_delay_seconds", self.RETRY_DELAY_SECONDS)

        if not all([self.client_id, self.client_secret, self.tenant_id]):
            error_msg = "Missing required authentication parameters: client_id, client_secret, or tenant_id"
            logger.error(error_msg)
            raise ValueError(error_msg)

        # TLS 1.2+ pinned session, reused for Graph calls and handed to MSAL token requests
        self._session = TlsPolicy().session()

        self._msal_app = ConfidentialClientApplication(
            self.client_id,
            authority=f"{self.LOGIN_AUTHORITY_BASE}/{self.tenant_id}",
            client_credential=self.client_secret,
            http_client=self._session,
        )

        self.access_token = self._acquire_token()
        logger.info("Microsoft Graph Module initialized successfully")

    def _acquire_token(self) -> str:
        """Acquire an access token using MSAL Client Credentials Flow."""
        logger.debug(f"Acquiring access token for tenant: {self.tenant_id}")
        result = self._msal_app.acquire_token_for_client(scopes=[self.GRAPH_SCOPE])
        if "access_token" in result:
            logger.info("Access token acquired successfully")
            return result["access_token"]
        error = result.get("error_description", result.get("error", "Unknown error"))
        logger.error(f"Failed to acquire token: {error}")
        raise Exception(f"Failed to acquire token: {error}")

    def _refresh_token(self) -> None:
        logger.info("Refreshing access token...")
        self.access_token = self._acquire_token()

    def _get_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, **kwargs) -> requests.Response:
        """
        Execute an HTTP request against the Graph API with retry and token-refresh logic.

        - 401: refresh token, retry once immediately.
        - 429/503/504: retry with delay up to MAX_RETRIES times.
        - Other 4xx/5xx: raise immediately via raise_for_status().
        """
        url = f"{self.GRAPH_API_BASE}/{endpoint.lstrip('/')}"

        for attempt in range(1, self.max_retries + 1):
            response = self._session.request(method, url, headers=self._get_headers(), **kwargs)

            if response.status_code == self.STATUS_UNAUTHORIZED:
                logger.warning("Received 401 Unauthorized. Refreshing token and retrying...")
                self._refresh_token()
                response = self._session.request(method, url, headers=self._get_headers(), **kwargs)

            if response.ok:
                return response

            if response.status_code in self.RETRY_STATUS_CODES and attempt < self.max_retries:
                logger.warning(
                    f"Request failed with status {response.status_code} "
                    f"(attempt {attempt}/{self.max_retries}). "
                    f"Retrying in {self.retry_delay_seconds}s..."
                )
                time.sleep(self.retry_delay_seconds)
                continue

            response.raise_for_status()

        response.raise_for_status()
        return None

    def send_email(
        self,
        subject: str,
        body: str,
        sender_email: str,
        receiver_email: str | list[str],
        cc_email: str | list[str] | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> None:
        """
        Send an email via Microsoft Graph API (Client Credentials Flow).

        Parameters:
            subject (str): Email subject.
            body (str): Email body (HTML supported).
            sender_email (str): Sender's UPN or email (must be licensed in the tenant).
            receiver_email (str | list[str]): Recipient(s) — comma-separated string or list.
            cc_email (str | list[str], optional): CC recipient(s) — comma-separated string or list.
            attachments (list[dict], optional): List of attachments, each with keys:
                - name (str): File name (e.g. 'report.xlsx').
                - content_type (str): MIME type
                  (e.g. 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet').
                - data (bytes): Raw file bytes.

        Raises:
            requests.HTTPError: If the request fails after all retry attempts.
        """

        def _parse_emails(value: str | list[str] | None) -> list[str]:
            if not value:
                return []
            if isinstance(value, list):
                return [e.strip() for e in value if e.strip()]
            return [e.strip() for e in value.split(",") if e.strip()]

        to_list = _parse_emails(receiver_email)
        cc_list = _parse_emails(cc_email)

        payload: dict[str, Any] = {
            "message": {
                "subject": subject,
                "body": {"contentType": "HTML", "content": body},
                "toRecipients": [{"emailAddress": {"address": e}} for e in to_list],
                "ccRecipients": [{"emailAddress": {"address": e}} for e in cc_list],
            },
            "saveToSentItems": True,
        }

        if attachments:
            payload["message"]["attachments"] = [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": att["name"],
                    "contentType": att["content_type"],
                    "contentBytes": base64.b64encode(att["data"]).decode("utf-8"),
                }
                for att in attachments
            ]

        self._request("POST", f"/users/{sender_email}/sendMail", json=payload)
        logger.info(f"Email sent successfully: '{subject}' → {to_list}")
