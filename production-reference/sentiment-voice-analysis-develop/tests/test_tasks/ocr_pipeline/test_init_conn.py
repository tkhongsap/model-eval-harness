"""Tests for init_conn — SharePoint/GCS connection factories used in each task's pre_execute.

Patches the SharePointModule/GCSModule classes where init_conn imports them, and covers the
happy-path construction plus the labelled log-then-reraise error path for each factory.
"""

from unittest.mock import patch

import pytest

from tasks.ocr_tax_invoice_pipeline.helper.init_conn import init_gcs, init_sharepoint

_LOGGER_NAME = "app"  # src.utils.logger.Logger defaults to the "app" logger name in non-local ENVIRONMENT

SP_CONFIG = {
    "client_id": "cid",
    "client_secret": "${SP_TEST_SECRET}",
    "tenant_id": "tid",
    "site_domain": "dom",
    "site_path": "/path",
}


class TestInitSharepoint:
    def test_init_sharepoint_happy_path_resolves_env_and_logs_debug(self, monkeypatch, caplog):
        # Arrange
        monkeypatch.setenv("SP_TEST_SECRET", "s3cr3t")

        # Act
        with patch("tasks.ocr_tax_invoice_pipeline.helper.init_conn.SharePointModule") as mock_cls:
            with caplog.at_level("DEBUG", logger=_LOGGER_NAME):
                module = init_sharepoint("source_site", SP_CONFIG)

        # Assert
        mock_cls.assert_called_once_with(
            client_id="cid", client_secret="s3cr3t", tenant_id="tid", site_domain="dom", site_path="/path"
        )
        assert module is mock_cls.return_value
        assert any(
            "source_site" in rec.message and "dom" in rec.message for rec in caplog.records if rec.levelname == "DEBUG"
        )

    def test_init_sharepoint_error_path_logs_and_reraises(self, caplog):
        # Arrange
        with patch("tasks.ocr_tax_invoice_pipeline.helper.init_conn.SharePointModule") as mock_cls:
            mock_cls.side_effect = Exception("boom")

            # Act / Assert
            with caplog.at_level("ERROR"):
                with pytest.raises(Exception, match="boom"):
                    init_sharepoint("source_site", SP_CONFIG)

        assert any(
            "Failed to initialize SharePoint" in rec.message and "source_site" in rec.message
            for rec in caplog.records
            if rec.levelname == "ERROR"
        )


class TestInitGcs:
    def test_init_gcs_happy_path_returns_module_and_logs_debug(self, caplog):
        # Arrange
        gcs_config = {"project_id": "proj", "bucket_name": "bkt"}

        # Act
        with patch("tasks.ocr_tax_invoice_pipeline.helper.init_conn.GCSModule") as mock_cls:
            with caplog.at_level("DEBUG", logger=_LOGGER_NAME):
                result = init_gcs(gcs_config)

        # Assert
        mock_cls.assert_called_once_with(project_id="proj", bucket_name="bkt")
        assert result is mock_cls.return_value
        assert any("proj/bkt" in rec.message for rec in caplog.records if rec.levelname == "DEBUG")

    def test_init_gcs_error_path_logs_and_reraises(self, caplog):
        # Arrange
        gcs_config = {"project_id": "proj", "bucket_name": "bkt"}

        # Act / Assert
        with patch("tasks.ocr_tax_invoice_pipeline.helper.init_conn.GCSModule") as mock_cls:
            mock_cls.side_effect = Exception("gcs boom")
            with caplog.at_level("ERROR"):
                with pytest.raises(Exception, match="gcs boom"):
                    init_gcs(gcs_config)

        assert any(
            "Failed to initialize GCS module" in rec.message for rec in caplog.records if rec.levelname == "ERROR"
        )
