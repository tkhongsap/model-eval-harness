"""Unit tests for app/main.py — pipeline entry-point factory."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_secret_service(overrides: dict | None = None) -> MagicMock:
    """Return a MagicMock SecretService whose .get() returns sensible defaults."""
    defaults = {
        "BATCH_SIZE": "5",
        "S3_BUCKET_NAME": "my-bucket",
        "GCS_BUCKET_NAME": "gcs-bucket",
        "PROJECT_NAME": "rtr-test",
        "FRAUD_SITE_BASE_ROOT": "/fraud",
        "INPUT_FOLDER": "/input",
        "OUTPUT_FOLDER": "/output",
        "BACKUP_FOLDER": "/backup",
        "ARCHIVE_FOLDER": "/archive",
        "CONTROL_SITE_BASE_ROOT": "/control",
        "CONTROL_SITE_PROMPTS_ROOT": "/prompts",
        "CONTROL_SITE_TRANSACTION_LOG_PATH": "/txn.csv",
        "CONTROL_SITE_PERFORMANCE_LOG_PATH": "/perf.csv",
        "RECIPIENT_EMAIL": "a@b.com,c@d.com",
        "RSA_PRIVATE_KEY": "RSA_KEY",
        "FRAUD_SITE_CLIENT_ID": "fcid",
        "FRAUD_SITE_CLIENT_SECRET": "fcsec",
        "FRAUD_SITE_TENANT_ID": "ftid",
        "FRAUD_SITE_SITE_DOMAIN": "fraud.sharepoint.com",
        "FRAUD_SITE_SITE_PATH": "/sites/fraud",
        "CONTROL_SITE_CLIENT_ID": "ccid",
        "CONTROL_SITE_CLIENT_SECRET": "ccsec",
        "CONTROL_SITE_TENANT_ID": "ctid",
        "CONTROL_SITE_SITE_DOMAIN": "control.sharepoint.com",
        "CONTROL_SITE_SITE_PATH": "/sites/control",
        "S3_AWS_ACCESS_KEY": "AKID",
        "S3_AWS_SECRET_KEY": "secret",
        "SENDER_EMAIL": "sender@x.com",
    }
    if overrides:
        defaults.update(overrides)

    mock = MagicMock()
    mock.get.side_effect = lambda key: defaults[key]
    mock.get_optional.side_effect = lambda key, default="": defaults.get(key, default)
    return mock


# ---------------------------------------------------------------------------
# main() happy-path test
# ---------------------------------------------------------------------------


class TestMain:
    def test_main_happy_path(self) -> None:
        """main() wires all services and calls pipeline.run() exactly once."""
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock()

        mock_secrets = _make_secret_service()

        sample_config = {
            "Output": {
                "sharepoint": {
                    "output_user_schema_sheet1": ["col1", "col2"],
                    "output_user_schema_sheet2": ["col3"],
                }
            }
        }

        with (
            patch("app.main.SecretService", return_value=mock_secrets),
            patch("app.main.google.auth.default", return_value=(None, "test-project")),
            patch("app.main.read_file", return_value="yaml: content"),
            patch("app.main.resolve_env", return_value="yaml: content"),
            patch("app.main.load_yaml_string", return_value=sample_config),
            patch("app.main.SharePointService"),
            patch("app.main.S3Service"),
            patch("app.main.GCSService"),
            patch("app.main.EmailService"),
            patch("app.main.genai.Client"),
            patch("app.main.GeminiService"),
            patch("app.main.ImageProcessor"),
            patch("app.main.ShopProcessor"),
            patch("app.main.ReportBuilder"),
            patch("app.main.EmailComposer") as mock_composer_cls,
            patch("app.main.FraudValidationPipeline", return_value=mock_pipeline_instance),
        ):
            mock_composer_cls.from_image_dir.return_value = MagicMock()

            import asyncio
            from app.main import main

            asyncio.run(main())

        mock_pipeline_instance.run.assert_called_once()

    def test_main_builds_pipeline_config_with_recipient_list(self) -> None:
        """Recipient emails are split on comma and stripped."""
        captured_config = {}

        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock()

        mock_secrets = _make_secret_service({"RECIPIENT_EMAIL": " a@b.com , c@d.com "})

        sample_config = {
            "Output": {
                "sharepoint": {
                    "output_user_schema_sheet1": [],
                    "output_user_schema_sheet2": [],
                }
            }
        }

        def _capture_pipeline(**kwargs):
            captured_config.update(kwargs)
            return mock_pipeline_instance

        with (
            patch("app.main.SecretService", return_value=mock_secrets),
            patch("app.main.google.auth.default", return_value=(None, "proj")),
            patch("app.main.read_file", return_value=""),
            patch("app.main.resolve_env", return_value=""),
            patch("app.main.load_yaml_string", return_value=sample_config),
            patch("app.main.SharePointService"),
            patch("app.main.S3Service"),
            patch("app.main.GCSService"),
            patch("app.main.EmailService"),
            patch("app.main.genai.Client"),
            patch("app.main.GeminiService"),
            patch("app.main.ImageProcessor"),
            patch("app.main.ShopProcessor"),
            patch("app.main.ReportBuilder"),
            patch("app.main.EmailComposer") as mock_composer_cls,
            patch("app.main.FraudValidationPipeline", side_effect=_capture_pipeline),
        ):
            mock_composer_cls.from_image_dir.return_value = MagicMock()

            import asyncio
            from app.main import main

            asyncio.run(main())

        config = captured_config["config"]
        assert config.recipient_emails == ["a@b.com", "c@d.com"]

    def test_main_passes_project_id_to_gcs(self) -> None:
        """project_id from google.auth.default() is forwarded to GCSService."""
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock()

        mock_secrets = _make_secret_service()

        sample_config = {
            "Output": {
                "sharepoint": {
                    "output_user_schema_sheet1": [],
                    "output_user_schema_sheet2": [],
                }
            }
        }

        gcs_calls = []

        def _capture_gcs(**kwargs):
            gcs_calls.append(kwargs)
            return MagicMock()

        with (
            patch("app.main.SecretService", return_value=mock_secrets),
            patch("app.main.google.auth.default", return_value=(None, "my-gcp-project")),
            patch("app.main.read_file", return_value=""),
            patch("app.main.resolve_env", return_value=""),
            patch("app.main.load_yaml_string", return_value=sample_config),
            patch("app.main.SharePointService"),
            patch("app.main.S3Service"),
            patch("app.main.GCSService", side_effect=_capture_gcs),
            patch("app.main.EmailService"),
            patch("app.main.genai.Client"),
            patch("app.main.GeminiService"),
            patch("app.main.ImageProcessor"),
            patch("app.main.ShopProcessor"),
            patch("app.main.ReportBuilder"),
            patch("app.main.EmailComposer") as mock_composer_cls,
            patch("app.main.FraudValidationPipeline", return_value=mock_pipeline_instance),
        ):
            mock_composer_cls.from_image_dir.return_value = MagicMock()

            import asyncio
            from app.main import main

            asyncio.run(main())

        assert gcs_calls[0]["project_id"] == "my-gcp-project"

    def test_main_sharepoint_service_called_twice(self) -> None:
        """Two SharePointService instances are created (fraud + control sites)."""
        mock_pipeline_instance = MagicMock()
        mock_pipeline_instance.run = AsyncMock()

        mock_secrets = _make_secret_service()

        sample_config = {
            "Output": {
                "sharepoint": {
                    "output_user_schema_sheet1": [],
                    "output_user_schema_sheet2": [],
                }
            }
        }

        with (
            patch("app.main.SecretService", return_value=mock_secrets),
            patch("app.main.google.auth.default", return_value=(None, "proj")),
            patch("app.main.read_file", return_value=""),
            patch("app.main.resolve_env", return_value=""),
            patch("app.main.load_yaml_string", return_value=sample_config),
            patch("app.main.SharePointService") as mock_sp_cls,
            patch("app.main.S3Service"),
            patch("app.main.GCSService"),
            patch("app.main.EmailService"),
            patch("app.main.genai.Client"),
            patch("app.main.GeminiService"),
            patch("app.main.ImageProcessor"),
            patch("app.main.ShopProcessor"),
            patch("app.main.ReportBuilder"),
            patch("app.main.EmailComposer") as mock_composer_cls,
            patch("app.main.FraudValidationPipeline", return_value=mock_pipeline_instance),
        ):
            mock_composer_cls.from_image_dir.return_value = MagicMock()

            import asyncio
            from app.main import main

            asyncio.run(main())

        assert mock_sp_cls.call_count == 2
