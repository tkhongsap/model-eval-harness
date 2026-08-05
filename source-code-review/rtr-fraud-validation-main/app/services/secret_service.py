"""SecretService — centralised secret retrieval with in-process cache.

Resolution order:
1. In-process cache (avoids repeated calls inside one container run)
2. ``os.environ`` (local dev / env-injected secrets)
3. GCP Secret Manager (production)

Injecting this class instead of calling ``os.environ["KEY"]`` directly means
unit tests can pass in a dict-backed stub with no environment pollution.
"""
from __future__ import annotations

import os


from app.core.exceptions import SecretServiceError


class SecretService:
    """Retrieve secrets from env or GCP Secret Manager."""

    def __init__(self, project_id: str | None = None) -> None:
        self._project_id = project_id
        self._cache: dict[str, str] = {}

    def get(self, key: str) -> str:
        """Return the secret value for *key*.

        Raises ``ValueError`` if the secret cannot be found anywhere.
        Raises ``SecretServiceError`` on unexpected failures.
        """
        try:
            if key in self._cache:
                return self._cache[key]

            value = os.environ.get(key)
            if value is not None:
                self._cache[key] = value
                return value

            value = self._fetch_from_gcp(key)
            self._cache[key] = value
            return value
        except SecretServiceError:
            raise
        except Exception as exc:
            raise SecretServiceError(
                f"Unexpected error retrieving secret: {key}",
                details={"key": key}
            ) from exc

    def get_optional(self, key: str, default: str = "") -> str:
        """Like ``get`` but returns *default* instead of raising."""
        try:
            return self.get(key)
        except SecretServiceError:
            return default
        except Exception:
            return default

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _fetch_from_gcp(self, secret_id: str) -> str:
        import google.auth
        from google.cloud import secretmanager

        try:
            _, project_id = google.auth.default()
            project_id = self._project_id or project_id
            client = secretmanager.SecretManagerServiceClient()
            name = f"projects/{project_id}/secrets/{secret_id}/versions/latest"
            response = client.access_secret_version(request={"name": name})
            return response.payload.data.decode("UTF-8")
        except Exception as exc:
            raise SecretServiceError(
                f"Secret '{secret_id}' not found in env or GCP Secret Manager",
                details={"secret_id": secret_id, "error": str(exc)}
            ) from exc
