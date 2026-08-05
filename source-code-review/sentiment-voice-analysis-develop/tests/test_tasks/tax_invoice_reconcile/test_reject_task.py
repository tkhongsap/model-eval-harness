"""Tests for TaxInvoiceRejectTask — IQS reject-move task for the tax-invoice pre-pipeline.

Covers: validate (required-key errors), pre_execute (SharePoint/GCS/LogExporter/IqsRejecter
init), and execute_task (job_id filtering, empty logs, and the best-effort exception swallow
around ``IqsRejecter.reject``).
"""

from __future__ import annotations

import copy
from datetime import datetime

import pandas as pd

from tasks.tax_invoice_reconcile.reject_task import TaxInvoiceRejectTask

_SOURCE_SITE = {
    "site_name": "s",
    "site_domain": "d",
    "site_path": "p",
    "client_id": "ci",
    "client_secret": "cs",
    "tenant_id": "ti",
    "reject_path": "/reject",
}


def _task_param():
    return {
        "gcp": {"project_id": "proj"},
        "gcs": {
            "project_id": "proj",
            "pre_processing_log_path": "gs://bucket-a/pre.csv",
            "page_manifest_log_path": "gs://bucket-a/manifest.csv",
        },
        "sharepoint": {"source_site": dict(_SOURCE_SITE)},
    }


def _make_task(task_param=None, packages=None):
    return TaxInvoiceRejectTask(
        task_param=task_param or _task_param(),
        packages=packages or {"execution_dt": datetime(2026, 6, 10), "job_id": "JOB1"},
    )


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_returns_true_when_all_required_keys_present():
    task = _make_task()

    assert task.validate() is True


def test_validate_returns_false_when_required_key_missing():
    param = copy.deepcopy(_task_param())
    del param["sharepoint"]["source_site"]["reject_path"]
    task = _make_task(task_param=param)

    assert task.validate() is False


# ---------------------------------------------------------------------------
# pre_execute
# ---------------------------------------------------------------------------


def test_pre_execute_initializes_collaborators(mocker):
    mock_sp = mocker.patch("tasks.tax_invoice_reconcile.reject_task.init_sharepoint", return_value=mocker.Mock())
    mock_gcs = mocker.patch("tasks.tax_invoice_reconcile.reject_task.init_gcs", return_value=mocker.Mock())
    mock_log_exporter = mocker.patch("tasks.tax_invoice_reconcile.reject_task.LogExporter", return_value=mocker.Mock())
    mock_rejecter = mocker.patch("tasks.tax_invoice_reconcile.reject_task.IqsRejecter", return_value=mocker.Mock())
    task = _make_task()

    task.pre_execute()

    mock_sp.assert_called_once_with("Source", _SOURCE_SITE)
    mock_gcs.assert_called_once_with({"project_id": "proj", "bucket_name": "bucket-a"})
    mock_log_exporter.assert_called_once_with(mock_gcs.return_value, mock_sp.return_value)
    mock_rejecter.assert_called_once_with(mock_sp.return_value, "/reject")
    assert task._pre_log_path == "gs://bucket-a/pre.csv"
    assert task._manifest_log_path == "gs://bucket-a/manifest.csv"
    assert task._log_exporter is mock_log_exporter.return_value
    assert task._rejecter is mock_rejecter.return_value


# ---------------------------------------------------------------------------
# execute_task
# ---------------------------------------------------------------------------


def _run_df(job_ids):
    return pd.DataFrame(
        {
            "job_id": job_ids,
            "sharepoint_input_path": [f"/f{i}" for i in range(len(job_ids))],
            "status": ["REJECTED"] * len(job_ids),
        }
    )


def test_execute_task_filters_rows_by_current_job_id(mocker):
    task = _make_task()
    task._pre_log_path = "gs://bucket-a/pre.csv"
    task._manifest_log_path = "gs://bucket-a/manifest.csv"
    task._log_exporter = mocker.Mock()
    task._log_exporter.load_log.side_effect = [_run_df(["JOB1", "JOB2"]), _run_df(["JOB1", "JOB2"])]
    task._rejecter = mocker.Mock()

    task.execute_task()

    task._rejecter.reject.assert_called_once()
    pre_run_df, manifest_run_df, datadate = task._rejecter.reject.call_args[0]
    assert list(pre_run_df["job_id"]) == ["JOB1"]
    assert list(manifest_run_df["job_id"]) == ["JOB1"]
    assert datadate == 20260610


def test_execute_task_passes_through_empty_logs(mocker):
    task = _make_task()
    task._pre_log_path = "gs://bucket-a/pre.csv"
    task._manifest_log_path = "gs://bucket-a/manifest.csv"
    task._log_exporter = mocker.Mock()
    task._log_exporter.load_log.side_effect = [pd.DataFrame(), pd.DataFrame()]
    task._rejecter = mocker.Mock()

    task.execute_task()

    task._rejecter.reject.assert_called_once()
    pre_run_df, manifest_run_df, _datadate = task._rejecter.reject.call_args[0]
    assert pre_run_df.empty
    assert manifest_run_df.empty


def test_execute_task_swallows_rejecter_exception(mocker):
    task = _make_task()
    task._pre_log_path = "gs://bucket-a/pre.csv"
    task._manifest_log_path = "gs://bucket-a/manifest.csv"
    task._log_exporter = mocker.Mock()
    task._log_exporter.load_log.side_effect = [_run_df(["JOB1"]), _run_df(["JOB1"])]
    task._rejecter = mocker.Mock()
    task._rejecter.reject.side_effect = Exception("boom")

    task.execute_task()  # must not raise
