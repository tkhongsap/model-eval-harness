"""Tests for tax_invoice_reconcile.helper.init_conn — the package's connection factories.

Covers the SharePoint/GCS factories (happy-path construction + labelled log-then-reraise
error path) and the :class:`EmailNotifier` factory. Each factory resolves ``${ENV_VAR}``
placeholders, constructs its module, and on failure logs the error then re-raises so the
caller's ``pre_execute`` still fails loudly. Modules are patched at the ``init_conn``
module namespace (where they are imported/used), per the mocking rule.
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from tasks.tax_invoice_reconcile.helper import init_conn
from tasks.tax_invoice_reconcile.helper.init_conn import init_email_notifier, init_gcs, init_sharepoint

_LOGGER_NAME = "app"  # src.utils.logger.Logger defaults to the "app" logger name in non-local ENVIRONMENT

_SP_CONFIG = {
    "client_id": "cid",
    "client_secret": "${SP_TEST_SECRET}",
    "tenant_id": "tid",
    "site_domain": "dom",
    "site_path": "/path",
}


class TestInitSharepoint:
    def test_init_sharepoint_happy_path_resolves_env_and_logs_debug(self, monkeypatch, mocker, caplog):
        # Arrange
        monkeypatch.setenv("SP_TEST_SECRET", "s3cr3t")
        mock_cls = mocker.patch.object(init_conn, "SharePointModule")

        # Act
        with caplog.at_level("DEBUG", logger=_LOGGER_NAME):
            module = init_sharepoint("source_site", _SP_CONFIG)

        # Assert
        mock_cls.assert_called_once_with(
            client_id="cid", client_secret="s3cr3t", tenant_id="tid", site_domain="dom", site_path="/path"
        )
        assert module is mock_cls.return_value
        assert any(
            "source_site" in rec.message and "dom" in rec.message for rec in caplog.records if rec.levelname == "DEBUG"
        )

    def test_init_sharepoint_error_path_logs_and_reraises(self, mocker, caplog):
        # Arrange
        mocker.patch.object(init_conn, "SharePointModule", side_effect=Exception("boom"))

        # Act / Assert
        with caplog.at_level("ERROR"):
            with pytest.raises(Exception, match="boom"):
                init_sharepoint("source_site", _SP_CONFIG)
        assert any(
            "Failed to initialize SharePoint" in rec.message and "source_site" in rec.message
            for rec in caplog.records
            if rec.levelname == "ERROR"
        )


class TestInitGcs:
    def test_init_gcs_happy_path_returns_module_and_logs_debug(self, mocker, caplog):
        # Arrange
        gcs_config = {"project_id": "proj", "bucket_name": "bkt"}
        mock_cls = mocker.patch.object(init_conn, "GCSModule")

        # Act
        with caplog.at_level("DEBUG", logger=_LOGGER_NAME):
            result = init_gcs(gcs_config)

        # Assert
        mock_cls.assert_called_once_with(project_id="proj", bucket_name="bkt")
        assert result is mock_cls.return_value
        assert any("proj/bkt" in rec.message for rec in caplog.records if rec.levelname == "DEBUG")

    def test_init_gcs_error_path_logs_and_reraises(self, mocker, caplog):
        # Arrange
        gcs_config = {"project_id": "proj", "bucket_name": "bkt"}
        mocker.patch.object(init_conn, "GCSModule", side_effect=Exception("gcs boom"))

        # Act / Assert
        with caplog.at_level("ERROR"):
            with pytest.raises(Exception, match="gcs boom"):
                init_gcs(gcs_config)
        assert any(
            "Failed to initialize GCS module" in rec.message for rec in caplog.records if rec.levelname == "ERROR"
        )


def test_init_email_notifier_builds_msgraph_and_notifier(mocker):
    # Arrange
    mock_msgraph_cls = mocker.patch.object(init_conn, "MSGraphModule")
    mock_msgraph_instance = MagicMock()
    mock_msgraph_cls.return_value = mock_msgraph_instance
    mock_notifier_cls = mocker.patch.object(init_conn, "EmailNotifier")
    mock_notifier_instance = MagicMock()
    mock_notifier_cls.return_value = mock_notifier_instance
    framework = {"email_template_dir": "/templates"}
    msgraph_access = {"client_id": "c", "client_secret": "s", "tenant_id": "t"}

    # Act
    result = init_email_notifier(framework, msgraph_access)

    # Assert
    mock_msgraph_cls.assert_called_once_with(client_id="c", client_secret="s", tenant_id="t")
    mock_notifier_cls.assert_called_once_with(msgraph=mock_msgraph_instance, template_dir="/templates")
    assert result is mock_notifier_instance


def test_init_email_notifier_resolves_env_placeholders(monkeypatch, mocker):
    # Arrange
    monkeypatch.setenv("GRAPH_SECRET", "s3cr3t")
    mock_msgraph_cls = mocker.patch.object(init_conn, "MSGraphModule")
    mocker.patch.object(init_conn, "EmailNotifier")
    framework = {"email_template_dir": "/templates"}
    msgraph_access = {"client_id": "c", "client_secret": "${GRAPH_SECRET}", "tenant_id": "t"}

    # Act
    init_email_notifier(framework, msgraph_access)

    # Assert
    mock_msgraph_cls.assert_called_once_with(client_id="c", client_secret="s3cr3t", tenant_id="t")


def test_init_email_notifier_error_is_logged_then_reraised(mocker, caplog):
    # Arrange
    mocker.patch.object(init_conn, "MSGraphModule", side_effect=Exception("graph boom"))
    framework = {"email_template_dir": "/templates"}
    msgraph_access = {"client_id": "c", "client_secret": "s", "tenant_id": "t"}

    # Act / Assert
    with caplog.at_level(logging.ERROR):
        with pytest.raises(Exception, match="graph boom"):
            init_email_notifier(framework, msgraph_access)
    assert "Failed to initialize email notifier" in caplog.text
