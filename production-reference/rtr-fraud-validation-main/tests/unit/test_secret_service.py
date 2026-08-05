"""Unit tests for SecretService."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from app.services.secret_service import SecretService


class TestSecretServiceGet:
    def test_returns_cached_value(self) -> None:
        svc = SecretService()
        svc._cache["MY_KEY"] = "cached_val"
        assert svc.get("MY_KEY") == "cached_val"

    def test_second_call_uses_cache(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "env_val")
        svc = SecretService()
        svc.get("MY_KEY")  # first call — populates cache
        monkeypatch.delenv("MY_KEY")
        # second call should hit cache, not env
        assert svc.get("MY_KEY") == "env_val"

    def test_returns_env_var_and_caches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "env_val")
        svc = SecretService()
        result = svc.get("MY_KEY")
        assert result == "env_val"
        assert svc._cache["MY_KEY"] == "env_val"

    def test_calls_gcp_when_no_cache_or_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_KEY", raising=False)
        svc = SecretService(project_id="my-project")
        with patch.object(svc, "_fetch_from_gcp", return_value="gcp_val") as mock_gcp:
            result = svc.get("MY_KEY")
        assert result == "gcp_val"
        mock_gcp.assert_called_once_with("MY_KEY")
        assert svc._cache["MY_KEY"] == "gcp_val"

    def test_raises_when_gcp_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_KEY", raising=False)
        svc = SecretService(project_id="my-project")
        with patch.object(svc, "_fetch_from_gcp", side_effect=ValueError("not found")):
            with pytest.raises(ValueError):
                svc.get("MY_KEY")


class TestSecretServiceGetOptional:
    def test_returns_value_when_key_exists(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MY_KEY", "val")
        svc = SecretService()
        assert svc.get_optional("MY_KEY") == "val"

    def test_returns_default_on_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_KEY", raising=False)
        svc = SecretService()
        with patch.object(svc, "_fetch_from_gcp", side_effect=Exception("gcp error")):
            result = svc.get_optional("MY_KEY", default="default_val")
        assert result == "default_val"

    def test_returns_empty_string_as_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MY_KEY", raising=False)
        svc = SecretService()
        with patch.object(svc, "_fetch_from_gcp", side_effect=Exception("gcp error")):
            result = svc.get_optional("MY_KEY")
        assert result == ""


class TestFetchFromGcp:
    def test_fetches_from_gcp_secret_manager(self) -> None:
        svc = SecretService(project_id="test-project")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "secret_value"
        mock_client.access_secret_version.return_value = mock_response

        with patch("google.auth.default", return_value=(None, "test-project")):
            with patch(
                "google.cloud.secretmanager.SecretManagerServiceClient",
                return_value=mock_client,
            ):
                result = svc._fetch_from_gcp("MY_SECRET")
        assert result == "secret_value"

    def test_raises_value_error_on_gcp_exception(self) -> None:
        svc = SecretService(project_id="test-project")
        with patch("google.auth.default", side_effect=Exception("auth failed")):
            with pytest.raises(ValueError, match="not found in env or GCP"):
                svc._fetch_from_gcp("MY_SECRET")

    def test_uses_constructor_project_id(self) -> None:
        svc = SecretService(project_id="explicit-project")
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.payload.data.decode.return_value = "val"
        mock_client.access_secret_version.return_value = mock_response

        with patch("google.auth.default", return_value=(None, "default-project")):
            with patch(
                "google.cloud.secretmanager.SecretManagerServiceClient",
                return_value=mock_client,
            ):
                svc._fetch_from_gcp("MY_SECRET")

        # name should contain explicit-project, not default-project
        call_args = mock_client.access_secret_version.call_args
        assert "explicit-project" in call_args[1]["request"]["name"]
