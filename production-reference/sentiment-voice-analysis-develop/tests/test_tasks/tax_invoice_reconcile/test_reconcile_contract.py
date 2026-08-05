"""Tests for ReconcileTask's OCR-pipeline v2 contract (the OCRResult hand-off semantics).

ReconcileTask must unwrap ``OCRResult.final_df``, read the audit-log frame from
``OCRResult.pre_processing_log``, and — crucially — return the upstream ``OCRResult``
unchanged so the trailing ``OCRFinalizeTask`` can stamp terminal status. Missing/empty OCR
output is passed through (not dropped) so dead-job FAILED files can still be finalized.
"""

import copy
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.log_helper import unwrap_ocr_result
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import DEFAULT_RETENTION_DAYS
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult
from tasks.tax_invoice_reconcile.reconcile_task import _PROCESSING_FAILED_TEMPLATE, ReconcileTask

EXECUTION_DT = datetime(2026, 6, 10, tzinfo=ZoneInfo("UTC"))
COMMON_CONFIG = {
    "framework": {"timezone": "Asia/Bangkok"},
    "control": {"site_name": "ctrl"},
    "msgraph": {"client_id": "mg"},
}

_SITE = {
    "site_name": "s",
    "site_domain": "d",
    "site_path": "p",
    "client_id": "ci",
    "client_secret": "cs",
    "tenant_id": "ti",
}


def _pre_log(rows):
    """Build a minimal pre-processing-log frame (one row per (path, status, job))."""
    return pd.DataFrame(
        [
            {
                "sharepoint_input_path": sp,
                "status": st,
                "update_dt": f"2026-06-10T10:0{i}:00+00:00",
                "batch_inference_job_name": jn,
                "batch_inference_model_name": "gemini-x",
                "sharepoint_web_url": "http://x",
            }
            for i, (sp, st, jn) in enumerate(rows)
        ]
    )


def _task_param():
    source = {
        **_SITE,
        "master_buyer_path": "mb",
        "master_buyer_file": "MB.xlsx",
        "z45_report_path": "z",
        "z45_report_file": "Z.xlsx",
        "master_vendor_path": "mv",
        "master_vendor_file": "MV.xlsx",
    }
    dest = {**_SITE, "dest_path": "out", "archive_invoice_path": "arc_inv", "archive_vat_path": "arc_vat"}
    return {
        "domain": "treasury",
        "gcp": {"project_id": "proj", "project_name": "name"},
        "sharepoint": {
            "source_site": source,
            "destination_site": dest,
            "control_site": {
                "extraction_result_path": "ctrl/extr.csv",
                "transaction_log_file": "ctrl/txn.csv",
                "performance_log_file": "ctrl/perf.csv",
            },
        },
        "framework": {"email_template_dir": "tmpl", "notifications": {}},
    }


def _make_task(mocker, pre_result=None, task_param=None):
    """Construct a ReconcileTask with the context patched and SharePoint/notifier mocked out."""
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    mocker.patch("tasks.tax_invoice_reconcile.helper.task_context.load_yaml", return_value=COMMON_CONFIG)
    task = ReconcileTask(task_param=task_param or _task_param(), packages=packages)
    task._sp_source = mocker.Mock()
    task._sp_control = mocker.Mock()
    task._sp_dest = mocker.Mock()
    task._notifier = mocker.Mock()
    task._rejecter = mocker.Mock()  # normally built in pre_execute; bypassed here
    if pre_result is not None:
        task.pre_result = pre_result
    return task


def _patch_collaborators(mocker, processing_df):
    """Stub every business collaborator so execute_task runs without real I/O."""
    loader = mocker.Mock()
    loader.load_master_buyer.return_value = pd.DataFrame()
    loader.load_z45.return_value = pd.DataFrame({"path_file": ["/z45.xlsx"]})
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.ReportSourceLoader", return_value=loader)
    builder = mocker.Mock()
    builder.build.return_value = processing_df
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.ExtractionReportBuilder", return_value=builder)
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.to_extraction_output", return_value=pd.DataFrame())
    recon = mocker.Mock()
    recon.build.return_value = (
        pd.DataFrame({"r": [1]}),
        pd.DataFrame({"z": [1]}),
        pd.DataFrame({"_z_id": [0], "file_name": ["a.pdf"]}),
    )
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.ReconciliationBuilder", return_value=recon)
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.OutputExporter", return_value=mocker.Mock())
    mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.SourceArchiver", return_value=mocker.Mock())


def test_unwrap_ocr_result_returns_final_df_and_latest_run_log():
    pre_log = _pre_log([("/a", "PENDING", "job1"), ("/a", "SUCCESS", "job1")])
    final_df = pd.DataFrame({"FILE_PATH": ["/a"]})
    ocr_result = OCRResult(final_df=final_df, pre_processing_log=pre_log)

    out, run_log = unwrap_ocr_result(ocr_result)

    assert out is final_df
    # latest_status_per_file collapses the append-only log to one row per file.
    assert len(run_log) == 1


def test_empty_final_df_returns_upstream_unchanged(mocker):
    ocr_result = OCRResult(final_df=pd.DataFrame(), pre_processing_log=_pre_log([]))
    task = _make_task(mocker, pre_result=ocr_result)

    assert task.execute_task() is ocr_result


def test_none_pre_result_returns_none(mocker):
    task = _make_task(mocker, pre_result=None)

    assert task.execute_task() is None


def test_normal_path_returns_upstream_ocrresult_unchanged(mocker):
    pre_log = _pre_log([("/a", "PENDING", "job1")])
    final_df = pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": ["SUCCESS"]})
    ocr_result = OCRResult(final_df=final_df, file_statuses={"/a": "SUCCESS"}, pre_processing_log=pre_log)
    task = _make_task(mocker, pre_result=ocr_result)
    processing_df = pd.DataFrame({"FILE_NAME": ["a"], "DOC_STATUS": ["Completed"], "DATADATE": [20260610]})
    _patch_collaborators(mocker, processing_df)

    result = task.execute_task()

    assert result is ocr_result  # upstream passed through unchanged → finalize can stamp


def test_non_ocr_result_pre_result_passes_through_without_reconciling(mocker):
    # Only the OCRResult contract is reconciled; anything else passes through untouched.
    bare_df = pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": ["SUCCESS"]})
    task = _make_task(mocker, pre_result=bare_df)
    builder_cls = mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.ExtractionReportBuilder")

    result = task.execute_task()

    assert result is bare_df
    builder_cls.assert_not_called()


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------


def test_validate_returns_true_when_all_required_keys_present(mocker):
    task = _make_task(mocker)

    assert task.validate() is True


def test_validate_returns_false_when_required_key_missing(mocker):
    param = copy.deepcopy(_task_param())
    del param["sharepoint"]["source_site"]["master_buyer_file"]
    task = _make_task(mocker, task_param=param)

    assert task.validate() is False


# ---------------------------------------------------------------------------
# pre_execute / _gcs_for_bucket
# ---------------------------------------------------------------------------


def test_pre_execute_initializes_sharepoint_notifier_and_rejecter(mocker):
    mock_sp = mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.init_sharepoint", return_value=mocker.Mock())
    mock_notifier = mocker.patch(
        "tasks.tax_invoice_reconcile.reconcile_task.init_email_notifier", return_value=mocker.Mock()
    )
    mock_rejecter_cls = mocker.patch(
        "tasks.tax_invoice_reconcile.reconcile_task.SourceRejecter", return_value=mocker.Mock()
    )
    task = _make_task(mocker)

    task.pre_execute()

    assert mock_sp.call_count == 3
    labels = [call.args[0] for call in mock_sp.call_args_list]
    assert labels == ["Source", "Control", "Destination"]
    mock_notifier.assert_called_once_with(task.ctx.framework, task.ctx.msgraph_access)
    mock_rejecter_cls.assert_called_once_with(task._sp_dest, task._gcs_for_bucket, task._reject_path)
    assert task._notifier is mock_notifier.return_value
    assert task._rejecter is mock_rejecter_cls.return_value


def test_gcs_for_bucket_caches_module_per_bucket(mocker):
    mock_init_gcs = mocker.patch("tasks.tax_invoice_reconcile.reconcile_task.init_gcs", return_value=mocker.Mock())
    task = _make_task(mocker)

    first = task._gcs_for_bucket("bkt")
    second = task._gcs_for_bucket("bkt")

    mock_init_gcs.assert_called_once_with({"project_id": task.ctx.gcp.get("project_id"), "bucket_name": "bkt"})
    assert first is second


# ---------------------------------------------------------------------------
# post_execute
# ---------------------------------------------------------------------------


def test_post_execute_exports_audit_logs_when_ocr_results_present(mocker):
    pre_log = _pre_log([("/a", "PENDING", "job1")])
    final_df = pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": ["SUCCESS"]})
    ocr_result = OCRResult(final_df=final_df, pre_processing_log=pre_log)
    task = _make_task(mocker, pre_result=ocr_result)
    mock_export_logging = mocker.patch(
        "tasks.tax_invoice_reconcile.reconcile_task.ExportLogging", return_value=mocker.Mock()
    )

    result = task.post_execute("RESULT_MARKER")

    assert result == "RESULT_MARKER"
    mock_export_logging.assert_called_once()
    _args, kwargs = mock_export_logging.call_args
    assert kwargs["ocr_df"] is final_df
    assert kwargs["sharepoint"] is task._sp_control
    mock_export_logging.return_value.export_logs.assert_called_once()


def test_post_execute_skips_export_when_no_ocr_results(mocker):
    task = _make_task(mocker, pre_result=None)
    mock_export_logging = mocker.patch(
        "tasks.tax_invoice_reconcile.reconcile_task.ExportLogging", return_value=mocker.Mock()
    )

    result = task.post_execute("RESULT_MARKER")

    assert result == "RESULT_MARKER"
    mock_export_logging.assert_not_called()


def test_post_execute_swallows_export_logging_failure(mocker):
    pre_log = _pre_log([("/a", "PENDING", "job1")])
    final_df = pd.DataFrame({"FILE_PATH": ["/a"], "STATUS": ["SUCCESS"]})
    ocr_result = OCRResult(final_df=final_df, pre_processing_log=pre_log)
    task = _make_task(mocker, pre_result=ocr_result)
    mock_export_logging = mocker.patch(
        "tasks.tax_invoice_reconcile.reconcile_task.ExportLogging", return_value=mocker.Mock()
    )
    mock_export_logging.return_value.export_logs.side_effect = Exception("gcs down")

    result = task.post_execute("RESULT_MARKER")  # must not raise

    assert result == "RESULT_MARKER"


# ---------------------------------------------------------------------------
# on_error / _notify
# ---------------------------------------------------------------------------


def test_on_error_without_notifier_does_not_raise(mocker):
    packages = {"execution_dt": EXECUTION_DT, "job_id": "JOB", "pipeline_name": "tax_invoice_extraction"}
    mocker.patch("tasks.tax_invoice_reconcile.helper.task_context.load_yaml", return_value=COMMON_CONFIG)
    task = ReconcileTask(task_param=_task_param(), packages=packages)  # pre_execute never ran

    task.on_error(RuntimeError("boom"))  # must not raise


def test_on_error_sends_processing_failed_notification(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()

    task.on_error(RuntimeError("boom"))

    task._notifier.send_template.assert_called_once()
    args, _kwargs = task._notifier.send_template.call_args
    assert args[0] == _PROCESSING_FAILED_TEMPLATE


def test_on_error_swallows_notifier_failure(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()
    task._notifier.send_template.side_effect = Exception("graph down")

    task.on_error(RuntimeError("boom"))  # must not raise


def test_notify_swallows_send_template_failure(mocker):
    task = _make_task(mocker)
    task._notifier = mocker.Mock()
    task._notifier.send_template.side_effect = Exception("graph down")

    task._notify("system_exception", "template.txt", "subject")  # must not raise


# ---------------------------------------------------------------------------
# ctx.logging_cfg / _datadate / _z45_source_path
# ---------------------------------------------------------------------------


def test_logging_cfg_resolves_project_and_log_paths(mocker):
    task = _make_task(mocker)

    cfg = task.ctx.logging_cfg()

    assert cfg == {
        "project_id": "proj",
        "project_name": "name",
        "transaction_log_path": "ctrl/txn.csv",
        "performance_log_path": "ctrl/perf.csv",
        "retention_days": DEFAULT_RETENTION_DAYS,  # raw; ExportLogging env-resolves it itself
    }


def test_datadate_falls_back_to_execution_dt_when_column_all_null(mocker):
    task = _make_task(mocker)
    processing_df = pd.DataFrame({"DATADATE": [None]})

    expected_dt = EXECUTION_DT.astimezone(ZoneInfo("Asia/Bangkok"))
    expected = int(expected_dt.strftime("%Y%m%d"))

    assert task._datadate(processing_df) == expected


def test_z45_source_path_returns_empty_string_when_column_missing():
    z45_report_df = pd.DataFrame({"other_col": [1]})

    assert ReconcileTask._z45_source_path(z45_report_df) == ""


def test_z45_source_path_returns_empty_string_when_dataframe_empty():
    z45_report_df = pd.DataFrame(columns=["path_file"])

    assert ReconcileTask._z45_source_path(z45_report_df) == ""
