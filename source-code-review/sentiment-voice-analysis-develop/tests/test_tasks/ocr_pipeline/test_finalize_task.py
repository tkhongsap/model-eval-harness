"""Tests for OCRFinalizeTask — the terminal-status idempotency matrix.

Finalize stamps SUCCESS/FAILED only after business logic; it must tolerate a missing or
bare-DataFrame ``pre_result``, stamp exactly once on the first run, and no-op on a re-run
against an already-terminal log. The pre-processing-log snapshot is read from
``OCRResult.pre_processing_log`` (threaded from retrieve), never re-read from GCS here.
"""

from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.finalize_task import OCRFinalizeTask
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult

EXECUTION_DT = datetime(2026, 6, 5, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {"framework": {"timezone": "UTC"}, "control": {}}
GCS_CONFIG = {"project_id": "gcs-proj", "pre_processing_log_path": "gs://log-bucket/pre.csv"}

PRE_LOG_COLUMNS = [
    "job_id",
    "pipeline_name",
    "domain_name",
    "sharepoint_input_path",
    "gcp_project_id",
    "gcs_project_id",
    "gcs_landing_path",
    "gcs_payload_path",
    "vertexai_project_id",
    "batch_inference_location",
    "batch_inference_model_name",
    "batch_inference_job_name",
    "batch_inference_display_name",
    "batch_inference_output_path",
    "status",
    "load_dt",
    "update_dt",
    "datadate",
    "message",
]


def _pre_log(rows):
    records = []
    for i, (sp_path, status, job_name) in enumerate(rows):
        record = {col: f"v_{col}" for col in PRE_LOG_COLUMNS}
        record.update(
            sharepoint_input_path=sp_path,
            status=status,
            batch_inference_job_name=job_name,
            update_dt=f"2026-06-10T10:0{i}:00+00:00",
        )
        records.append(record)
    return pd.DataFrame(records, columns=PRE_LOG_COLUMNS)


def _make_task(pre_result=None):
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "gcp-proj"},
        "gcs": GCS_CONFIG,
        "vertexai": {},
        "sharepoint": {"control_site": {"pre_processing_log_path": "/ctrl/pre.csv"}},
        "framework": {},
    }
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        task = OCRFinalizeTask(task_param=task_param, packages=packages)
    task._router = GcsRouter(GCS_CONFIG, "JOB", EXECUTION_DT, gcs_factory=Mock(return_value=Mock()))
    task._sp_control = Mock()
    if pre_result is not None:
        task.pre_result = pre_result
    return task


def _run(task):
    """Execute the task with LogExporter patched (used only for the save); yield the exporter mock."""
    with patch("tasks.ocr_tax_invoice_pipeline.finalize_task.LogExporter") as mock_le:
        exporter = mock_le.return_value
        result = task.execute_task()
    return result, exporter


def test_pre_result_none_is_noop():
    task = _make_task(pre_result=None)
    result, exporter = _run(task)
    assert result is None
    exporter.save_log.assert_not_called()


def test_bare_dataframe_is_noop():
    task = _make_task(pre_result=pd.DataFrame({"x": [1]}))
    result, exporter = _run(task)
    assert result is None
    exporter.save_log.assert_not_called()


def test_empty_file_statuses_is_noop():
    ocr_result = OCRResult(
        final_df=pd.DataFrame(),
        file_statuses={},
        pre_processing_log=_pre_log([("/a", JobStatus.PENDING.value, "job1")]),
    )
    task = _make_task(pre_result=ocr_result)
    result, exporter = _run(task)
    assert result is None
    exporter.save_log.assert_not_called()


def test_normal_stamp_calls_save_log_once_and_returns_result():
    ocr_result = OCRResult(
        final_df=pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": [OCROutputStatus.SUCCESS.value]}),
        file_statuses={"/a": JobStatus.SUCCESS.value},
        pre_processing_log=_pre_log([("/a", JobStatus.PENDING.value, "job1")]),
    )
    task = _make_task(pre_result=ocr_result)
    result, exporter = _run(task)

    assert result is ocr_result  # upstream passed through unchanged
    # The log snapshot comes from OCRResult, so finalize must NOT re-read it from GCS.
    exporter.load_log.assert_not_called()
    exporter.save_log.assert_called_once()
    rows_df = exporter.save_log.call_args[0][0]
    assert rows_df.iloc[0]["status"] == JobStatus.SUCCESS.value


def test_second_run_against_terminal_log_is_noop():
    # The log already shows /a as terminal SUCCESS — nothing left to stamp.
    ocr_result = OCRResult(
        final_df=pd.DataFrame(),
        file_statuses={"/a": JobStatus.SUCCESS.value},
        pre_processing_log=_pre_log([("/a", JobStatus.SUCCESS.value, "job1")]),
    )
    task = _make_task(pre_result=ocr_result)
    result, exporter = _run(task)

    assert result is ocr_result
    exporter.save_log.assert_not_called()


def test_empty_final_df_with_statuses_stamps_failed():
    ocr_result = OCRResult(
        final_df=pd.DataFrame(),
        file_statuses={"/a": JobStatus.FAILED.value},
        pre_processing_log=_pre_log([("/a", JobStatus.PENDING.value, "job-dead")]),
    )
    task = _make_task(pre_result=ocr_result)
    result, exporter = _run(task)

    assert result is ocr_result
    exporter.save_log.assert_called_once()
    rows_df = exporter.save_log.call_args[0][0]
    assert rows_df.iloc[0]["status"] == JobStatus.FAILED.value


def test_validate_with_all_required_keys_returns_true():
    task = _make_task()

    assert task.validate() is True


def test_validate_missing_required_key_returns_false_and_logs(caplog):
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "gcp-proj"},
        "gcs": {"project_id": "gcs-proj"},  # gcs.pre_processing_log_path deliberately missing
        "vertexai": {},
        "sharepoint": {"control_site": {"pre_processing_log_path": "/ctrl/pre.csv"}},
        "framework": {},
    }
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        task = OCRFinalizeTask(task_param=task_param, packages=packages)

    with caplog.at_level("ERROR"):
        result = task.validate()

    assert result is False
    assert any("gcs.pre_processing_log_path" in rec.message for rec in caplog.records)


def test_pre_execute_initializes_sharepoint_and_router(mocker):
    mock_init_sp = mocker.patch("tasks.ocr_tax_invoice_pipeline.finalize_task.init_sharepoint")
    mock_gcs_router_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.finalize_task.GcsRouter")

    task = _make_task()  # overrides _router/_sp_control with stubs; pre_execute rebuilds them for real
    task.pre_execute()

    mock_init_sp.assert_called_once_with("Control", task.ctx.control_site_access)
    mock_gcs_router_cls.assert_called_once_with(task.ctx.gcs, task.ctx.job_id, task.ctx.execution_dt)
    assert task._sp_control is mock_init_sp.return_value
    assert task._router is mock_gcs_router_cls.return_value
