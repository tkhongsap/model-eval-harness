"""Tests for OCRRetrieveTask's pipeline contract (the OCRResult hand-off semantics).

Covers the deliberate behavior changes: all-jobs-dead still returns an OCRResult with FAILED
statuses (not None); all-running / nothing-in-flight return None; the result CSV is written
once as utf-8-sig; empty frames are dropped before concat (no FutureWarning).
"""

import warnings
from copy import deepcopy
from datetime import datetime
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.retrieve_task import OCRRetrieveTask
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult

EXECUTION_DT = datetime(2026, 6, 5, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {"framework": {"timezone": "UTC"}, "control": {}}
GCS_CONFIG = {
    "project_id": "gcs-proj",
    "dest_path": "gs://result-bucket/result/out_${JOB_ID}.csv",
    "pre_processing_log_path": "gs://log-bucket/pre.csv",
    "page_manifest_log_path": "gs://log-bucket/manifest.csv",
}

PRE_LOG_COLUMNS = [
    "sharepoint_input_path",
    "status",
    "update_dt",
    "batch_inference_job_name",
]


def _pre_log(rows):
    return pd.DataFrame(
        [
            {
                "sharepoint_input_path": sp,
                "status": st,
                "update_dt": "2026-06-10T10:00:00+00:00",
                "batch_inference_job_name": jn,
            }
            for sp, st, jn in rows
        ],
        columns=PRE_LOG_COLUMNS,
    )


def _task(pre_log, manifest=None, gcs_module=None):
    """Construct an OCRRetrieveTask with mocked collaborators (no real GCP/SharePoint)."""
    gcs_module = gcs_module or Mock()
    manifest = manifest if manifest is not None else pd.DataFrame()
    task_param = {
        "domain": "treasury",
        "gcp": {"project_id": "gcp-proj"},
        "gcs": GCS_CONFIG,
        "vertexai": {"location": "us-central1"},
        "sharepoint": {"control_site": {"pre_processing_log_path": "/ctrl/pre.csv"}},
        "framework": {},
    }
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        task = OCRRetrieveTask(task_param=task_param, packages=packages)

    task._router = GcsRouter(GCS_CONFIG, "JOB", EXECUTION_DT, gcs_factory=Mock(return_value=gcs_module))
    exporter = Mock()
    exporter.load_log.side_effect = [pre_log, manifest]
    task._log_exporter = Mock(return_value=exporter)
    task._sp_control = Mock()
    task._batch_client = Mock()
    task._retriever = Mock()
    task._tracing_builder = Mock()
    task._tracing_exporter = Mock()
    task._result_finalizer = Mock()
    return task, gcs_module


def _job(state, name):
    # ``name`` is a reserved Mock constructor kwarg, so set ``.name`` after construction.
    job = Mock(state=state, display_name=name)
    job.name = name
    return job


def test_nothing_in_flight_returns_none():
    task, _ = _task(_pre_log([("/a", JobStatus.SUCCESS.value, "job1")]))
    assert task.execute_task() is None


def test_all_jobs_running_returns_none():
    task, _ = _task(_pre_log([("/a", JobStatus.PENDING.value, "job-run")]))
    task._batch_client.pull_job_detail.return_value = _job("JOB_STATE_RUNNING", "job-run")

    assert task.execute_task() is None


def test_all_jobs_dead_returns_ocrresult_with_failed_statuses():
    # The deliberate bug fix: dead jobs with zero predictions must NOT return None.
    pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-dead")])
    task, gcs_module = _task(pre_log)
    task._batch_client.pull_job_detail.return_value = _job("JOB_STATE_FAILED", "job-dead")
    # Retriever returns (frame, tracing_rows); both empty for a dead job with zero predictions.
    task._retriever.retrieve_succeeded.return_value = (pd.DataFrame(), [])
    task._retriever.retrieve_failed.return_value = (pd.DataFrame(), [])

    result = task.execute_task()

    assert isinstance(result, OCRResult)
    assert result.final_df.empty
    assert result.file_statuses == {"/a": JobStatus.FAILED.value}
    # The pre-processing-log snapshot is threaded forward for finalize/business tasks.
    assert result.pre_processing_log is pre_log
    gcs_module.update_content_to_gcs.assert_not_called()  # nothing to write


def test_succeeded_threads_final_df_forward_and_drops_empty_frames(recwarn):
    pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-ok")])
    task, gcs_module = _task(pre_log)
    task._batch_client.pull_job_detail.return_value = _job("JOB_STATE_SUCCEEDED", "job-ok")
    # Succeeded frame carries the time columns the task coerces; failed frame is empty (dropped).
    succeeded_df = pd.DataFrame(
        {"start_time": [datetime(2026, 6, 5, 1)], "end_time": [datetime(2026, 6, 5, 2)], "x": [1]}
    )
    task._retriever.retrieve_succeeded.return_value = (succeeded_df, [{"job_id": "JOB"}])
    task._retriever.retrieve_failed.return_value = (pd.DataFrame(), [])
    final_df = pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": [OCROutputStatus.SUCCESS.value]})
    task._result_finalizer.run.return_value = final_df

    result = task.execute_task()

    assert isinstance(result, OCRResult)
    assert result.file_statuses == {"/a": JobStatus.SUCCESS.value}
    assert result.pre_processing_log is pre_log
    # The result frame is threaded forward via OCRResult — NOT written to GCS here.
    assert result.final_df is final_df
    gcs_module.update_content_to_gcs.assert_not_called()
    # The tracing rows are exported once to SharePoint (the only side-effecting write here).
    task._tracing_exporter.save.assert_called_once()
    assert not any(issubclass(w.category, FutureWarning) for w in recwarn.list)


def test_empty_frames_dropped_before_concat_no_futurewarning():
    pre_log = _pre_log([("/a", JobStatus.PENDING.value, "job-ok")])
    task, _ = _task(pre_log)
    task._batch_client.pull_job_detail.return_value = _job("JOB_STATE_SUCCEEDED", "job-ok")
    task._retriever.retrieve_succeeded.return_value = (pd.DataFrame(), [])
    task._retriever.retrieve_failed.return_value = (pd.DataFrame(), [])

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = task.execute_task()

    # No succeeded/failed rows → empty final frame, finalizer never invoked.
    assert isinstance(result, OCRResult)
    assert result.final_df.empty
    task._result_finalizer.run.assert_not_called()


VALID_TASK_PARAM = {
    "domain": "treasury",
    "gcp": {"project_id": "gcp-proj"},
    "gcs": {
        "project_id": "gcs-proj",
        "pre_processing_log_path": "gs://log-bucket/pre.csv",
        "page_manifest_log_path": "gs://log-bucket/manifest.csv",
    },
    "vertexai": {"location": "us-central1"},
    "sharepoint": {
        "control_site": {
            "pre_processing_log_path": "/ctrl/pre.csv",
            "tracing_log_path": "/ctrl/tracing.csv",
        }
    },
    "framework": {},
}


def _validate_task(task_param):
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    with patch("tasks.ocr_tax_invoice_pipeline.helper.task_context.load_yaml", return_value=COMMON_CONFIG):
        return OCRRetrieveTask(task_param=task_param, packages=packages)


def test_validate_with_all_required_keys_returns_true():
    task = _validate_task(VALID_TASK_PARAM)

    assert task.validate() is True


def test_validate_missing_required_key_returns_false_and_logs(caplog):
    param = deepcopy(VALID_TASK_PARAM)
    del param["gcp"]["project_id"]
    task = _validate_task(param)

    with caplog.at_level("ERROR"):
        result = task.validate()

    assert result is False
    assert any("gcp.project_id" in rec.message for rec in caplog.records)


def test_pre_execute_wires_all_collaborators(mocker):
    mock_init_sp = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.init_sharepoint")
    mock_gemini_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.GeminiBatchModule")
    mock_batch_client_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.BatchJobClient")
    mock_gcs_router_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.GcsRouter")
    mock_tracing_builder_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.TracingLogBuilder")
    mock_tracing_ctx_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.TracingLogContext")
    mock_tracing_exporter_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.TracingLogExporter")
    mock_retriever_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.BatchResultRetriever")
    mock_result_finalizer_cls = mocker.patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.ResultFinalizer")

    task = _validate_task(VALID_TASK_PARAM)
    task.pre_execute()

    mock_init_sp.assert_called_once_with("Control", task.ctx.control_site_access)
    mock_gemini_cls.assert_called_once()
    mock_batch_client_cls.assert_called_once()
    mock_gcs_router_cls.assert_called_once_with(task.ctx.gcs, task.ctx.job_id, task.ctx.execution_dt)
    mock_tracing_ctx_cls.from_task_context.assert_called_once_with(task.ctx)
    mock_tracing_builder_cls.assert_called_once_with(mock_tracing_ctx_cls.from_task_context.return_value)
    mock_tracing_exporter_cls.assert_called_once_with(mock_init_sp.return_value)
    mock_retriever_cls.assert_called_once()
    mock_result_finalizer_cls.assert_called_once()

    assert task._sp_control is mock_init_sp.return_value
    assert task._batch_client is mock_batch_client_cls.return_value
    assert task._router is mock_gcs_router_cls.return_value
    assert task._tracing_builder is mock_tracing_builder_cls.return_value
    assert task._tracing_exporter is mock_tracing_exporter_cls.return_value
    assert task._retriever is mock_retriever_cls.return_value
    assert task._result_finalizer is mock_result_finalizer_cls.return_value


def test_in_flight_jobs_empty_log_returns_empty_list():
    task, _ = _task(_pre_log([]))

    assert task._in_flight_jobs(_pre_log([])) == []


def test_normalize_job_state_missing_state_returns_unknown_state():
    job = Mock(spec=[])  # no attributes at all -> getattr(job, "state", None) is None

    assert OCRRetrieveTask._normalize_job_state(job) == "UNKNOWN_STATE"


def test_log_exporter_builds_logexporter_from_router_bucket_and_control():
    with patch("tasks.ocr_tax_invoice_pipeline.retrieve_task.LogExporter") as mock_log_exporter_cls:
        task, _ = _task(_pre_log([]))
        del task._log_exporter  # the fixture stubs this; restore the real bound method
        bucket_module = Mock()
        task._router.module_for = Mock(return_value=bucket_module)

        exporter = task._log_exporter("pre_processing_log_path")

        task._router.module_for.assert_called_once_with("pre_processing_log_path")
        # The configured window must be passed even though this exporter is load-only:
        # the ctor logs the "log retention active" line, which must state the real value.
        mock_log_exporter_cls.assert_called_once_with(
            bucket_module,
            task._sp_control,
            retention_days=task._retention_days(),
            timezone=task.ctx.timezone,
        )
        assert exporter is mock_log_exporter_cls.return_value
