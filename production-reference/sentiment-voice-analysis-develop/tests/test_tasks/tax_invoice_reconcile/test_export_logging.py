"""Tests for :class:`ExportLogging` (tax_invoice_reconcile).

Covers: constructor validation (DataFrame type + required-field guard), per-page dedup of
line-item rows (usage/timestamps as the group key, ``PAGE_STATUS`` folding any FAILED line
item), transaction-log building with real token usage + Gemini cost, storage-path derivation
from the pre-processing log, performance-log aggregation, the AI-operation summary frame,
append-and-upload against SharePoint (existing-CSV merge vs fresh-file branches), and the
static helpers (usage JSON key, page status/usage, cost computation, ``_to_str``, the
environment label). SharePoint and ``gemini_cost`` (which reads pricing from SharePoint) are
mocked at the boundary; everything else runs through the real dataclasses/schemas.
"""

from __future__ import annotations

import datetime as dt
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.tax_invoice_reconcile.module.export_logging import ExportLogging

_EXECUTION_DT = dt.datetime(2026, 6, 10, 8, 0, 0)
_USAGE = {
    "promptTokensDetails": [{"modality": "TEXT", "tokenCount": 1000}],
    "candidatesTokensDetails": [{"modality": "TEXT", "tokenCount": 500}],
    "cachedContentTokenCount": 0,
    "thoughtsTokenCount": 0,
}
_COST_CONFIG = [
    {"model": "gemini-2.5-flash", "pricing_type": "input", "input_type": "text", "cost_per_token": 0.00000015},
    {"model": "gemini-2.5-flash", "pricing_type": "output", "input_type": "all", "cost_per_token": 0.00000125},
]
_CFG = {
    "project_id": "proj1",
    "project_name": "Project One",
    "transaction_log_path": "/control/transaction_log.csv",
    "performance_log_path": "/control/performance_log.csv",
}


def _row(**overrides) -> dict:
    """One OCR-output line item carrying the columns ``ExportLogging`` requires."""
    row = {
        "FILE_PATH": "/sp/doc1.pdf",
        "PAGE_NO": 1,
        "START_TIME": dt.datetime(2026, 6, 10, 1, 0, 0),
        "END_TIME": dt.datetime(2026, 6, 10, 1, 0, 5),
        "STATUS": "SUCCESS",
        "MESSAGE": None,
        "USAGE_METADATA": _USAGE,
        "DATADATE": "20260610",
    }
    row.update(overrides)
    return row


def _pre_log_df(**overrides) -> pd.DataFrame:
    row = {
        "sharepoint_input_path": "/sp/doc1.pdf",
        "sharepoint_web_url": "https://sharepoint/doc1.pdf",
        "batch_inference_model_name": "gemini-2.5-flash",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def _make(
    ocr_rows: list[dict] | None = None,
    pre_log_df: pd.DataFrame | None = None,
    cfg: dict | None = None,
    sharepoint: MagicMock | None = None,
    execution_dt: dt.datetime | None = _EXECUTION_DT,
) -> ExportLogging:
    ocr_df = pd.DataFrame(ocr_rows if ocr_rows is not None else [_row()])
    return ExportLogging(
        execution_dt=execution_dt,
        ocr_df=ocr_df,
        pre_log_df=pre_log_df if pre_log_df is not None else _pre_log_df(),
        cfg=cfg if cfg is not None else dict(_CFG),
        sharepoint=sharepoint if sharepoint is not None else MagicMock(),
    )


# --- Constructor -------------------------------------------------------------------


def test_init_non_dataframe_ocr_df_raises_value_error():
    # Arrange / Act / Assert
    with pytest.raises(ValueError, match="ocr_df must be a pandas DataFrame"):
        ExportLogging(
            execution_dt=_EXECUTION_DT, ocr_df=[{"a": 1}], pre_log_df=pd.DataFrame(), cfg={}, sharepoint=MagicMock()
        )


def test_init_missing_required_fields_raises_value_error():
    # Arrange
    ocr_df = pd.DataFrame([{"FILE_PATH": "/sp/doc1.pdf"}])

    # Act / Assert
    with pytest.raises(ValueError, match="Missing required fields for export logging"):
        ExportLogging(
            execution_dt=_EXECUTION_DT, ocr_df=ocr_df, pre_log_df=pd.DataFrame(), cfg={}, sharepoint=MagicMock()
        )


def test_init_valid_df_sets_paths_from_cfg():
    # Arrange / Act
    exporter = _make()

    # Assert
    assert exporter.transaction_log_path == "/control/transaction_log.csv"
    assert exporter.performance_log_path == "/control/performance_log.csv"
    assert exporter.cfg == _CFG


def test_init_non_dataframe_pre_log_df_defaults_to_empty_frame():
    # Arrange / Act
    exporter = ExportLogging(
        execution_dt=_EXECUTION_DT,
        ocr_df=pd.DataFrame([_row()]),
        pre_log_df=None,
        cfg={},
        sharepoint=MagicMock(),
    )

    # Assert
    assert exporter.pre_log_df.empty


def test_init_blank_cfg_defaults_log_paths_to_empty_string():
    # Arrange / Act
    exporter = ExportLogging(
        execution_dt=_EXECUTION_DT,
        ocr_df=pd.DataFrame([_row()]),
        pre_log_df=pd.DataFrame(),
        cfg=None,
        sharepoint=MagicMock(),
    )

    # Assert
    assert exporter.transaction_log_path == ""
    assert exporter.performance_log_path == ""


# --- export_logs (integration) ------------------------------------------------------


def test_export_logs_happy_path_uploads_transaction_and_performance_logs(mocker):
    # Arrange
    mocker.patch("tasks.tax_invoice_reconcile.module.export_logging.gemini_cost", return_value=_COST_CONFIG)
    sharepoint = MagicMock()
    sharepoint.is_item_exists.return_value = False
    exporter = _make(sharepoint=sharepoint)

    # Act
    exporter.export_logs()

    # Assert — both monthly CSVs uploaded to their configured paths.
    uploaded_paths = [call.args[0] for call in sharepoint.upload_file.call_args_list]
    assert uploaded_paths == ["/control/transaction_log.csv", "/control/performance_log.csv"]


def test_export_logs_exception_is_logged_and_reraised(mocker):
    # Arrange — force a downstream failure inside the try block.
    mocker.patch("tasks.tax_invoice_reconcile.module.export_logging.gemini_cost", return_value=[])
    sharepoint = MagicMock()
    sharepoint.is_item_exists.side_effect = RuntimeError("sharepoint down")
    sharepoint.upload_file.side_effect = RuntimeError("sharepoint down")
    exporter = _make(sharepoint=sharepoint)

    # Act / Assert
    with pytest.raises(RuntimeError, match="sharepoint down"):
        exporter.export_logs()


# --- _dedup_pages --------------------------------------------------------------------


def test_dedup_pages_collapses_multiple_line_items_into_one_page_row():
    # Arrange — two line items sharing the same page identity (file/page/time/usage).
    exporter = _make(
        ocr_rows=[
            _row(TAX_INVOICE_NUMBER="INV-1"),
            _row(TAX_INVOICE_NUMBER="INV-2"),
        ]
    )

    # Act
    result = exporter._dedup_pages(exporter.ocr_df)

    # Assert — one row per page, not per line item.
    assert len(result) == 1
    assert result.iloc[0]["TAX_INVOICE_NUMBER"] == "INV-1"


def test_dedup_pages_two_distinct_pages_yield_two_rows():
    # Arrange
    exporter = _make(ocr_rows=[_row(PAGE_NO=1), _row(PAGE_NO=2)])

    # Act
    result = exporter._dedup_pages(exporter.ocr_df)

    # Assert
    assert len(result) == 2
    assert set(result["PAGE_NO"]) == {1, 2}


def test_dedup_pages_marks_page_failed_when_any_line_item_failed():
    # Arrange
    exporter = _make(ocr_rows=[_row(STATUS="SUCCESS"), _row(STATUS="FAILED")])

    # Act
    result = exporter._dedup_pages(exporter.ocr_df)

    # Assert
    assert len(result) == 1
    assert result.iloc[0]["PAGE_STATUS"] == OCROutputStatus.FAILED.value


def test_dedup_pages_marks_page_success_when_all_line_items_succeed():
    # Arrange
    exporter = _make(ocr_rows=[_row(STATUS="SUCCESS"), _row(STATUS="SUCCESS")])

    # Act
    result = exporter._dedup_pages(exporter.ocr_df)

    # Assert
    assert result.iloc[0]["PAGE_STATUS"] == OCROutputStatus.SUCCESS.value


# --- _build_transaction_log / _transaction_row ---------------------------------------


def test_build_transaction_log_empty_page_df_returns_empty_frame():
    # Arrange
    exporter = _make()

    # Act
    result = exporter._build_transaction_log(pd.DataFrame(), _COST_CONFIG)

    # Assert
    assert result.empty


def test_build_transaction_log_computes_real_token_usage_and_cost():
    # Arrange — one SUCCESS page whose model resolves via the pre-processing log.
    exporter = _make()
    page_df = pd.DataFrame([_row(PAGE_STATUS=OCROutputStatus.SUCCESS.value)])

    # Act
    result = exporter._build_transaction_log(page_df, _COST_CONFIG)

    # Assert — 1000 input / 500 output tokens; cost = 1000*0.00000015 + 500*0.00000125.
    assert len(result) == 1
    row = result.iloc[0]
    assert row["token_usage_input"] == 1000
    assert row["token_usage_output"] == 500
    assert row["total_cost_usd"] == pytest.approx(0.000775)
    assert row["status_pass_failed_retry"] == "Pass"
    assert row["storage_path"] == "https://sharepoint/doc1.pdf"
    assert row["folder"] == "/sp/doc1.pdf"
    assert row["filename"] == "doc1.pdf"
    assert row["data_date"] == "20260610"
    assert row["action"] == "Create Transaction Log"
    assert row["status"] == "SUCCESS"


def test_transaction_row_failed_page_marks_status_failed_and_stamps_error():
    # Arrange
    exporter = _make()
    row = _row(PAGE_STATUS=OCROutputStatus.FAILED.value, MESSAGE="page rejected by IQS gate")

    # Act
    result = exporter._transaction_row(row, {"token_input": {}, "token_output": {}}, {}, {})

    # Assert
    assert result["status_pass_failed_retry"] == "Failed"
    assert result["error_log_if"] == "page rejected by IQS gate"
    assert result["status"] == "FAILED"
    assert result["error_message"] == "page rejected by IQS gate"
    assert result["total_cost_usd"] == 0.0
    assert result["token_usage_input"] == 0
    assert result["token_usage_output"] == 0


def test_transaction_row_blank_file_path_yields_empty_folder_and_filename():
    # Arrange
    exporter = _make()
    row = _row(FILE_PATH="", PAGE_STATUS=OCROutputStatus.SUCCESS.value)

    # Act
    result = exporter._transaction_row(row, {"token_input": {}, "token_output": {}}, {}, {})

    # Assert
    assert result["folder"] == ""
    assert result["filename"] == ""
    assert result["storage_path"] == ""


def test_transaction_row_storage_path_looked_up_from_storage_map():
    # Arrange
    exporter = _make()
    row = _row(PAGE_STATUS=OCROutputStatus.SUCCESS.value)
    storage_map = {"/sp/doc1.pdf": "https://sharepoint/doc1.pdf"}

    # Act
    result = exporter._transaction_row(row, {"token_input": {}, "token_output": {}}, {}, storage_map)

    # Assert
    assert result["storage_path"] == "https://sharepoint/doc1.pdf"


# --- _build_performance_log / _performance_row ---------------------------------------


def test_build_performance_log_empty_transaction_df_returns_empty_frame():
    # Arrange
    exporter = _make()

    # Act
    result = exporter._build_performance_log(pd.DataFrame())

    # Assert
    assert result.empty


def test_build_performance_log_aggregates_counts_and_runtime():
    # Arrange — two transactions for the same project/date: one Pass, one Failed.
    exporter = _make()
    transaction_df = pd.DataFrame(
        [
            {
                "data_date": "20260610",
                "gcp_project_id": "proj1",
                "gcp_project_name": "Project One",
                "status_pass_failed_retry": "Pass",
                "latency_ms": 5000.0,
                "load_dt": "2026-06-10 08:00:00",
            },
            {
                "data_date": "20260610",
                "gcp_project_id": "proj1",
                "gcp_project_name": "Project One",
                "status_pass_failed_retry": "Failed",
                "latency_ms": 3000.0,
                "load_dt": "2026-06-10 08:05:00",
            },
        ]
    )

    # Act
    result = exporter._build_performance_log(transaction_df)

    # Assert
    assert len(result) == 1
    row = result.iloc[0]
    assert row["total_transaction"] == 2
    assert row["total_completed"] == 1
    assert row["total_failed"] == 1
    assert row["success_rate"] == 50.0
    assert row["total_runtime"] == "0.08"  # 8.0 seconds -> 0 min, 08 sec
    assert row["data_date"] == "20260610"
    assert row["run_date"] == "20260610"
    assert row["action"] == "Create Performance Log"
    assert row["status"] == "SUCCESS"


# --- _cost_config ----------------------------------------------------------------------


def test_cost_config_empty_pre_log_df_returns_empty_list():
    # Arrange
    exporter = _make(pre_log_df=pd.DataFrame())

    # Act
    result = exporter._cost_config()

    # Assert
    assert result == []


def test_cost_config_missing_model_column_returns_empty_list():
    # Arrange
    exporter = _make(pre_log_df=pd.DataFrame([{"sharepoint_input_path": "/sp/doc1.pdf"}]))

    # Act
    result = exporter._cost_config()

    # Assert
    assert result == []


def test_cost_config_no_models_present_returns_empty_list():
    # Arrange — a batch_inference_model_name column that is entirely null.
    exporter = _make(pre_log_df=pd.DataFrame([{"batch_inference_model_name": None}]))

    # Act
    result = exporter._cost_config()

    # Assert
    assert result == []


def test_cost_config_calls_gemini_cost_with_unique_models(mocker):
    # Arrange
    mock_gemini_cost = mocker.patch(
        "tasks.tax_invoice_reconcile.module.export_logging.gemini_cost", return_value=_COST_CONFIG
    )
    pre_log_df = pd.DataFrame(
        [{"batch_inference_model_name": "gemini-2.5-flash"}, {"batch_inference_model_name": "gemini-2.5-flash"}]
    )
    exporter = _make(pre_log_df=pre_log_df)

    # Act
    result = exporter._cost_config()

    # Assert
    mock_gemini_cost.assert_called_once_with("batch", ["gemini-2.5-flash"])
    assert result == _COST_CONFIG


def test_cost_config_gemini_cost_failure_returns_empty_list(mocker):
    # Arrange
    mocker.patch(
        "tasks.tax_invoice_reconcile.module.export_logging.gemini_cost", side_effect=RuntimeError("no pricing file")
    )
    pre_log_df = pd.DataFrame([{"batch_inference_model_name": "gemini-2.5-flash"}])
    exporter = _make(pre_log_df=pre_log_df)

    # Act
    result = exporter._cost_config()

    # Assert
    assert result == []


# --- _pre_log_map -----------------------------------------------------------------------


def test_pre_log_map_empty_pre_log_df_returns_empty_dict():
    # Arrange
    exporter = _make(pre_log_df=pd.DataFrame())

    # Act
    result = exporter._pre_log_map("batch_inference_model_name")

    # Assert
    assert result == {}


def test_pre_log_map_missing_column_returns_empty_dict():
    # Arrange — a pre-processing-log frame written before the requested column existed.
    exporter = _make(pre_log_df=pd.DataFrame([{"sharepoint_input_path": "/sp/doc1.pdf"}]))

    # Act
    result = exporter._pre_log_map("sharepoint_web_url")

    # Assert
    assert result == {}


def test_pre_log_map_builds_file_path_to_value_mapping_with_blank_for_nan():
    # Arrange
    pre_log_df = pd.DataFrame(
        [
            {"sharepoint_input_path": "/sp/doc1.pdf", "batch_inference_model_name": "gemini-2.5-flash"},
            {"sharepoint_input_path": "/sp/doc2.pdf", "batch_inference_model_name": None},
        ]
    )
    exporter = _make(pre_log_df=pre_log_df)

    # Act
    result = exporter._pre_log_map("batch_inference_model_name")

    # Assert
    assert result == {"/sp/doc1.pdf": "gemini-2.5-flash", "/sp/doc2.pdf": ""}


# --- _ai_operation_logging / _ai_operation_frame ---------------------------------------


def test_ai_operation_logging_empty_transaction_df_skips_logging(mocker):
    # Arrange
    mock_log = mocker.patch("tasks.tax_invoice_reconcile.module.export_logging.logging_ai_operation")
    exporter = _make()

    # Act
    exporter._ai_operation_logging(pd.DataFrame())

    # Assert
    mock_log.assert_not_called()


def test_ai_operation_logging_calls_logging_ai_operation_per_group(mocker, monkeypatch):
    # Arrange
    monkeypatch.setenv("ENVIRONMENT", "prod")
    mock_log = mocker.patch("tasks.tax_invoice_reconcile.module.export_logging.logging_ai_operation")
    exporter = _make()
    transaction_df = pd.DataFrame(
        [
            {
                "start_time": "2026-06-10T01:00:00+00:00",
                "end_time": "2026-06-10T01:00:05+00:00",
                "gcp_project_id": "proj1",
                "status_pass_failed_retry": "Pass",
                "latency_ms": 5000.0,
            }
        ]
    )

    # Act
    exporter._ai_operation_logging(transaction_df)

    # Assert
    mock_log.assert_called_once()
    kwargs = mock_log.call_args.kwargs
    assert kwargs["log_type"] == "batch"
    assert kwargs["message"] == "AI-Operation-Log"
    assert kwargs["log_obj"]["project_id"] == "proj1"
    assert kwargs["log_obj"]["total_transaction"] == 1
    assert kwargs["log_obj"]["environment"] == "production"


def test_ai_operation_logging_swallows_exception(mocker):
    # Arrange — logging_ai_operation blowing up must not propagate.
    mocker.patch(
        "tasks.tax_invoice_reconcile.module.export_logging.logging_ai_operation",
        side_effect=RuntimeError("logging backend down"),
    )
    exporter = _make()
    transaction_df = pd.DataFrame(
        [
            {
                "start_time": "2026-06-10T01:00:00+00:00",
                "end_time": "2026-06-10T01:00:05+00:00",
                "gcp_project_id": "proj1",
                "status_pass_failed_retry": "Pass",
                "latency_ms": 5000.0,
            }
        ]
    )

    # Act — must not raise.
    exporter._ai_operation_logging(transaction_df)


def test_ai_operation_frame_computes_aggregates_and_environment_label(monkeypatch):
    # Arrange
    monkeypatch.setenv("ENVIRONMENT", "prod")
    exporter = _make()
    transaction_df = pd.DataFrame(
        [
            {
                "start_time": "2026-06-10T01:00:00+00:00",
                "end_time": "2026-06-10T01:00:05+00:00",
                "gcp_project_id": "proj1",
                "status_pass_failed_retry": "Pass",
                "latency_ms": 5000.0,
            },
            {
                "start_time": "2026-06-10T01:00:10+00:00",
                "end_time": "2026-06-10T01:00:12+00:00",
                "gcp_project_id": "proj1",
                "status_pass_failed_retry": "Failed",
                "latency_ms": 2000.0,
            },
        ]
    )

    # Act
    result = exporter._ai_operation_frame(transaction_df)

    # Assert
    assert len(result) == 1
    row = result.iloc[0]
    assert row["total_transaction"] == 2
    assert row["total_success_transaction"] == 1
    assert row["total_failed_transaction"] == 1
    assert row["average_response_time_sec"] == 3.5
    assert row["total_runtime_sec"] == 12.0
    assert row["environment"] == "production"
    assert row["project_type"] == "batch"
    assert row["project_id"] == "proj1"


# --- _append_and_upload -----------------------------------------------------------------


def test_append_and_upload_empty_df_skips_upload():
    # Arrange
    sharepoint = MagicMock()
    exporter = _make(sharepoint=sharepoint)

    # Act
    exporter._append_and_upload(pd.DataFrame(), "/control/x.csv", ["load_dt"], "test log")

    # Assert
    sharepoint.upload_file.assert_not_called()


def test_append_and_upload_blank_path_skips_upload():
    # Arrange
    sharepoint = MagicMock()
    exporter = _make(sharepoint=sharepoint)
    df = pd.DataFrame([{"a": 1}])

    # Act
    exporter._append_and_upload(df, "", ["load_dt"], "test log")

    # Assert
    sharepoint.upload_file.assert_not_called()


def test_append_and_upload_uploads_csv_bytes_to_sharepoint():
    # Arrange
    sharepoint = MagicMock()
    sharepoint.is_item_exists.return_value = False
    exporter = _make(sharepoint=sharepoint)
    df = pd.DataFrame([{"data_date": "20260610", "load_dt": "2026-06-10 08:00:00"}])

    # Act
    exporter._append_and_upload(df, "/control/x.csv", ["load_dt"], "test log")

    # Assert
    sharepoint.upload_file.assert_called_once()
    path, content = sharepoint.upload_file.call_args.args
    assert path == "/control/x.csv"
    assert "20260610" in content.decode("utf-8-sig")


def test_append_and_upload_concats_existing_csv_content():
    # Arrange
    sharepoint = MagicMock()
    sharepoint.is_item_exists.return_value = True
    existing_csv = "data_date\n20260609\n".encode("utf-8-sig")
    sharepoint.get_item_by_path.return_value = MagicMock(content=existing_csv)
    exporter = _make(sharepoint=sharepoint)
    df = pd.DataFrame([{"data_date": "20260610"}])

    # Act
    exporter._append_and_upload(df, "/control/x.csv", [], "test log")

    # Assert
    _, content = sharepoint.upload_file.call_args.args
    assert content.decode("utf-8-sig").splitlines() == ["data_date", "20260609", "20260610"]


def test_append_and_upload_read_failure_writes_fresh_df():
    # Arrange
    sharepoint = MagicMock()
    sharepoint.is_item_exists.side_effect = RuntimeError("sharepoint timeout")
    exporter = _make(sharepoint=sharepoint)
    df = pd.DataFrame([{"data_date": "20260610"}])

    # Act
    exporter._append_and_upload(df, "/control/x.csv", [], "test log")

    # Assert
    _, content = sharepoint.upload_file.call_args.args
    assert content.decode("utf-8-sig").splitlines() == ["data_date", "20260610"]


def test_append_and_upload_stringifies_sorts_and_blanks_nan():
    # Arrange — unsorted rows and a NaN cell; no existing CSV.
    sharepoint = MagicMock()
    sharepoint.is_item_exists.return_value = False
    exporter = _make(sharepoint=sharepoint)
    df = pd.DataFrame(
        [
            {"data_date": 20260609, "load_dt": "2026-06-09 08:00:00", "note": None},
            {"data_date": 20260610, "load_dt": "2026-06-10 08:00:00", "note": "ok"},
        ]
    )

    # Act
    exporter._append_and_upload(df, "/control/x.csv", ["load_dt"], "test log")

    # Assert
    _, content = sharepoint.upload_file.call_args.args
    lines = content.decode("utf-8-sig").splitlines()
    assert lines[0] == "data_date,load_dt,note"
    assert lines[1] == "20260610,2026-06-10 08:00:00,ok"  # newest load_dt first
    assert lines[2] == "20260609,2026-06-09 08:00:00,"  # NaN note blanked


# --- Static helpers ------------------------------------------------------------------------


def test_usage_json_dict_returns_stable_sorted_json():
    # Arrange / Act
    result = ExportLogging._usage_json({"b": 1, "a": 2})

    # Assert
    assert result == '{"a": 2, "b": 1}'


def test_usage_json_non_dict_returns_empty_string():
    # Arrange / Act / Assert
    assert ExportLogging._usage_json(None) == ""
    assert ExportLogging._usage_json("not a dict") == ""


def test_page_usage_dict_summarizes_tokens():
    # Arrange / Act
    result = ExportLogging._page_usage(_USAGE)

    # Assert
    assert result["token_input"] == {"text": 1000}
    assert result["token_output"] == {"text": 500, "thoughts": 0}
    assert result["token_cached"] == 0


def test_page_usage_non_dict_returns_zeroed_summary():
    # Arrange / Act
    result = ExportLogging._page_usage(None)

    # Assert
    assert result == {"token_input": {}, "token_cached": 0, "token_output": {}}


def test_compute_costs_empty_usage_detail_returns_empty_dict():
    # Arrange / Act
    exporter = ExportLogging(
        execution_dt=_EXECUTION_DT,
        ocr_df=pd.DataFrame([_row()]),
        pre_log_df=pd.DataFrame(),
        cfg={},
        sharepoint=MagicMock(),
    )
    result = exporter._compute_costs({}, _COST_CONFIG)

    # Assert
    assert result == {}


def test_compute_costs_empty_cost_config_returns_empty_dict():
    # Arrange
    exporter = _make()
    usage_detail = {
        0: {"model": "gemini-2.5-flash", "token_input": {"text": 100}, "token_cached": 0, "token_output": {}}
    }

    # Act
    result = exporter._compute_costs(usage_detail, [])

    # Assert
    assert result == {}


def test_compute_costs_success_returns_real_cost_breakdown():
    # Arrange
    exporter = _make()
    usage_detail = {
        0: {
            "model": "gemini-2.5-flash",
            "token_input": {"text": 1000},
            "token_cached": 0,
            "token_output": {"text": 500},
        }
    }

    # Act
    result = exporter._compute_costs(usage_detail, _COST_CONFIG)

    # Assert
    assert result[0]["cost_input"] == pytest.approx(0.00015)
    assert result[0]["cost_output"] == pytest.approx(0.000625)


def test_compute_costs_exception_returns_empty_dict(mocker):
    # Arrange
    mocker.patch(
        "tasks.tax_invoice_reconcile.module.export_logging.GeminiBatchModule.cal_gemini_cost",
        side_effect=RuntimeError("bad usage shape"),
    )
    exporter = _make()
    usage_detail = {0: {"model": "gemini-2.5-flash", "token_input": {}, "token_cached": 0, "token_output": {}}}

    # Act
    result = exporter._compute_costs(usage_detail, _COST_CONFIG)

    # Assert
    assert result == {}


def test_load_dt_and_run_date_derive_from_execution_dt():
    # Arrange / Act
    exporter = _make(execution_dt=dt.datetime(2026, 6, 10, 8, 30, 15))

    # Assert
    assert exporter._load_dt == "2026-06-10 08:30:15"
    assert exporter._run_date == "2026-06-10"


def test_environment_label_prod_returns_production(monkeypatch):
    # Arrange
    monkeypatch.setenv("ENVIRONMENT", "prod")

    # Act / Assert
    assert ExportLogging._environment_label() == "production"


def test_environment_label_nprd_returns_non_production(monkeypatch):
    # Arrange
    monkeypatch.setenv("ENVIRONMENT", "nprd")

    # Act / Assert
    assert ExportLogging._environment_label() == "non-production"


def test_environment_label_unknown_value_returns_lowercased_value(monkeypatch):
    # Arrange
    monkeypatch.setenv("ENVIRONMENT", "STAGING")

    # Act / Assert
    assert ExportLogging._environment_label() == "staging"


def test_environment_label_unset_returns_unknown(monkeypatch):
    # Arrange
    monkeypatch.delenv("ENVIRONMENT", raising=False)

    # Act / Assert
    assert ExportLogging._environment_label() == "unknown"


def test_to_str_string_value_returned_as_is():
    assert ExportLogging._to_str("2026-06-10T01:00:00") == "2026-06-10T01:00:00"


def test_to_str_none_returns_empty_string():
    assert ExportLogging._to_str(None) == ""


def test_to_str_nat_returns_empty_string():
    assert ExportLogging._to_str(pd.NaT) == ""


def test_to_str_datetime_returns_isoformat():
    assert ExportLogging._to_str(dt.datetime(2026, 6, 10, 1, 0, 0)) == "2026-06-10T01:00:00"


def test_to_str_non_datetime_value_returns_str():
    assert ExportLogging._to_str(42) == "42"
