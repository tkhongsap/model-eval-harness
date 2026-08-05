"""Tests for OCRTaskContext.from_task — focused on the notification + Graph wiring.

The submit/retrieve/finalize tasks share this context; the system-error email reads
``framework.notifications`` (per-task) and the Microsoft Graph credentials from the shared
``common.yml msgraph`` block.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.retrieve_task import OCRRetrieveTask

_EXEC_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))
_COMMON = {
    "framework": {"timezone": "Asia/Bangkok"},
    "control": {"site_name": "ctrl"},
    "msgraph": {"client_id": "mg", "client_secret": "ms", "tenant_id": "mt"},
}
_PACKAGES = {"execution_dt": _EXEC_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}


def _task(mocker, task_param, common=_COMMON):
    mocker.patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=common)
    return OCRRetrieveTask(task_param=task_param, packages=_PACKAGES)


def test_from_task_populates_notifications_and_msgraph(mocker):
    # Arrange
    notifications = {"system_exception": {"enabled": True, "subject": "s"}}
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "p"},
        "framework": {"notifications": notifications},
        "sharepoint": {"control_site": {}},
    }

    # Act
    ctx = _task(mocker, task_param).ctx

    # Assert
    assert isinstance(ctx, OCRTaskContext)
    assert ctx.notifications == notifications
    assert ctx.msgraph_access == _COMMON["msgraph"]


def test_from_task_notifications_default_empty_when_absent(mocker):
    # Arrange — no framework block at all.
    task_param = {"domain": "treasury", "gcp": {"project_id": "p"}, "sharepoint": {"control_site": {}}}

    # Act
    ctx = _task(mocker, task_param).ctx

    # Assert
    assert ctx.notifications == {}
    assert ctx.msgraph_access == _COMMON["msgraph"]


def test_from_task_missing_timezone_raises(mocker):
    # Arrange
    task_param = {"domain": "treasury", "sharepoint": {}}

    # Act / Assert
    with pytest.raises(ValueError, match="Timezone not set"):
        _task(mocker, task_param, common={"control": {}, "msgraph": {}})
