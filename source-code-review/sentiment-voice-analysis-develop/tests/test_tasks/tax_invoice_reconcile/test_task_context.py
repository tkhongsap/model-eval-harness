"""Tests for ReconcileTaskContext.from_task (the shared per-run context builder)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from tasks.tax_invoice_reconcile.helper.task_context import ReconcileTaskContext
from tasks.tax_invoice_reconcile.precheck_task import ReconcilePrecheckTask

EXECUTION_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {
    "framework": {"timezone": "Asia/Bangkok"},
    "control": {"site_name": "ctrl", "client_id": "cc"},
    "msgraph": {"client_id": "mg"},
}

_SOURCE = {
    "site_name": "s",
    "site_domain": "d",
    "site_path": "p",
    "client_id": "ci",
    "client_secret": "cs",
    "tenant_id": "ti",
    "master_buyer_path": "mb",
    "master_buyer_file": "MB.xlsx",
    "z45_report_path": "z",
    "z45_report_file": "Z.xlsx",
}


def _task(mocker, common_config=COMMON_CONFIG):
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "proj"},
        "sharepoint": {"source_site": _SOURCE},
        "framework": {"email_template_dir": "tmpl", "notifications": {"system_exception": {"sender_email": "x"}}},
    }
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    mocker.patch("tasks.tax_invoice_reconcile.helper.task_context.load_yaml", return_value=common_config)
    return ReconcilePrecheckTask(task_param=task_param, packages=packages)


def test_from_task_populates_blocks_and_resolves_timezone(mocker):
    task = _task(mocker)
    ctx = task.ctx

    assert isinstance(ctx, ReconcileTaskContext)
    assert ctx.timezone == ZoneInfo("Asia/Bangkok")
    assert ctx.execution_dt == EXECUTION_DT
    assert ctx.source_site == _SOURCE
    assert ctx.control_site_access == COMMON_CONFIG["control"]
    assert ctx.msgraph_access == COMMON_CONFIG["msgraph"]
    assert ctx.notifications == {"system_exception": {"sender_email": "x"}}


def test_from_task_missing_timezone_raises(mocker):
    with pytest.raises(ValueError, match="Timezone not set"):
        _task(mocker, common_config={"control": {}, "msgraph": {}})
