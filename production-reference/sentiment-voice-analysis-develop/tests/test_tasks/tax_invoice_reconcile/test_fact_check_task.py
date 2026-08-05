"""Tests for :class:`TaxInvoiceFactCheckTask` orchestration and error handling."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult
from tasks.tax_invoice_reconcile.fact_check_task import TaxInvoiceFactCheckTask

_TASK_MODULE = "tasks.tax_invoice_reconcile.fact_check_task"


def _task_param() -> dict:
    return {
        "gcp": {"project_id": "proj-1", "project_name": "name-1"},
        "sharepoint": {
            "control_site": {
                "ground_truth_file": "/ctrl/fact_check/ground_truth.xlsx",
                "master_buyer_path": "/ctrl/fact_check/master_file",
                "master_buyer_file": r"Master Buyer Company_\d{8}.xlsx",
                "transaction_log_file": "/ctrl/fact_check/transaction_log/transaction_log_202607.csv",
                "performance_log_file": "/ctrl/fact_check/performance_log/performance_log_202607.csv",
            }
        },
        "framework": {
            "email_template_dir": "config/tax_invoice_extraction/email_template",
            "notifications": {
                "system_exception": {
                    "sender_email": "${BOT_EMAIL}",
                    "receiver_email": "${DEVELOPER_EMAIL}",
                    "cc_email": "${OPER_EMAIL}",
                },
                "fact_check_result": {
                    "sender_email": "${BOT_EMAIL}",
                    "receiver_email": "${DEVELOPER_EMAIL}",
                    "cc_email": "${OPER_EMAIL}",
                    "baseline_path": "config/tax_invoice_extraction/fact_check_uat_baseline.yml",
                },
            },
        },
    }


def _make_task() -> TaxInvoiceFactCheckTask:
    return TaxInvoiceFactCheckTask(
        task_param=_task_param(),
        packages={"execution_dt": datetime(2026, 7, 6, 10, 0, 0)},
    )


def _ocr_result(final_df: pd.DataFrame) -> OCRResult:
    return OCRResult(
        final_df=final_df,
        file_statuses={"a.pdf": "SUCCESS"},
        pre_processing_log=pd.DataFrame(),
    )


class TestValidate:
    def test_missing_required_key_fails_validation(self):
        # Arrange — drop the ground-truth path.
        param = _task_param()
        del param["sharepoint"]["control_site"]["ground_truth_file"]
        task = TaxInvoiceFactCheckTask(task_param=param, packages={"execution_dt": datetime(2026, 7, 6)})

        # Act / Assert
        assert task.validate() is False

    def test_missing_result_email_baseline_path_fails_validation(self):
        # Arrange — the UAT-baseline path is required (a blank one silently kills the result email).
        param = _task_param()
        del param["framework"]["notifications"]["fact_check_result"]["baseline_path"]
        task = TaxInvoiceFactCheckTask(task_param=param, packages={"execution_dt": datetime(2026, 7, 6)})

        # Act / Assert
        assert task.validate() is False

    def test_complete_config_passes_validation(self):
        assert _make_task().validate() is True


class TestExecuteTask:
    def test_scores_and_emits_then_returns_pre_result_unchanged(self, mocker):
        # Arrange
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        metric_rows = [{"label": "overall", "accuracy": 100.0, "precision": 100.0, "recall": 100.0, "f1_score": 100.0}]
        evaluator = mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        evaluator.return_value.evaluate.return_value = metric_rows
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")

        pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))
        task.pre_result = pre_result

        # Act
        result = task.execute_task()

        # Assert — upstream OCRResult returned unchanged and one emit call with the metric rows.
        assert result is pre_result
        emit.assert_called_once()
        assert emit.call_args.args[0] == metric_rows

    def test_emit_datetimes_follow_telesale_convention(self, mocker):
        # Arrange — two rows share the same Gemini END_TIME (UTC), one differs: mode wins.
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        common = datetime(2026, 7, 6, 3, 30, 0, tzinfo=UTC)
        other = datetime(2026, 7, 6, 4, 0, 0, tzinfo=UTC)
        task.pre_result = _ocr_result(
            pd.DataFrame(
                [
                    {"FILE_NAME": "a.pdf", "END_TIME": common},
                    {"FILE_NAME": "a.pdf", "END_TIME": common},
                    {"FILE_NAME": "a.pdf", "END_TIME": other},
                ]
            )
        )

        # Act
        task.execute_task()

        # Assert — created = execution_dt; processed = mode END_TIME in Asia/Bangkok (UTC+7).
        kwargs = emit.call_args.kwargs
        assert kwargs["created_datetime"] == "2026-07-06 10:00:00"
        assert kwargs["processed_datetime"] == "2026-07-06 10:30:00"

    def test_emit_processed_datetime_handles_tz_naive_end_time(self, mocker):
        # Arrange — production shape: the finalizer's DuckDB + pandera round-trip strips the
        # tzinfo, leaving a naive datetime64[ns] column whose wall-clock is UTC. Regression for
        # "TypeError: Cannot convert tz-naive Timestamp, use tz_localize to localize".
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        final_df = pd.DataFrame({"FILE_NAME": ["a.pdf", "a.pdf"]})
        final_df["END_TIME"] = pd.to_datetime(["2026-07-06 03:30:00", "2026-07-06 03:30:00"])
        assert final_df["END_TIME"].dt.tz is None  # guard: this test must exercise the naive path
        task.pre_result = _ocr_result(final_df)

        # Act
        task.execute_task()

        # Assert — naive UTC wall-clock localized to UTC, then converted to Asia/Bangkok (UTC+7).
        assert emit.call_args.kwargs["processed_datetime"] == "2026-07-06 10:30:00"

    def test_emit_processed_datetime_none_when_no_end_time(self, mocker):
        # Arrange — no END_TIME column at all (e.g. every prediction line failed to carry one).
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))

        # Act
        task.execute_task()

        # Assert
        assert emit.call_args.kwargs["processed_datetime"] is None

    def test_emit_processed_datetime_none_when_end_time_all_nat(self, mocker):
        # Arrange — END_TIME column present but all null.
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf", "END_TIME": pd.NaT}]))

        # Act
        task.execute_task()

        # Assert
        assert emit.call_args.kwargs["processed_datetime"] is None

    def test_empty_ocr_result_passes_through_without_scoring(self, mocker):
        # Arrange
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        pre_result = _ocr_result(pd.DataFrame())
        task.pre_result = pre_result

        # Act
        result = task.execute_task()

        # Assert — nothing scored or emitted.
        assert result is pre_result
        builder.assert_not_called()
        emit.assert_not_called()

    def test_sends_result_email_with_counts_model_and_accuracy_table(self, mocker, monkeypatch):
        # Arrange
        monkeypatch.setenv("BOT_EMAIL", "bot@example.com")
        monkeypatch.setenv("DEVELOPER_EMAIL", "dev@example.com")
        monkeypatch.setenv("OPER_EMAIL", "oper@example.com")
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        loader = mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        loader.return_value.load_ground_truth.return_value = pd.DataFrame({"file_name": ["a.pdf", "b.pdf"]})
        metric_rows = [{"label": "overall", "accuracy": 88.5, "precision": 88.5, "recall": 100.0, "f1_score": 93.9}]
        evaluator = mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        evaluator.return_value.evaluate.return_value = metric_rows
        mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        # The run log must carry the latest_status_per_file required columns or it is dropped.
        task.pre_result = OCRResult(
            final_df=pd.DataFrame([{"FILE_NAME": "a.pdf"}]),
            file_statuses={"a.pdf": "SUCCESS"},
            pre_processing_log=pd.DataFrame(
                {
                    "sharepoint_input_path": ["/in/a.pdf", "/in/b.pdf"],
                    "update_dt": ["2026-07-06 09:00:00", "2026-07-06 09:00:00"],
                    "status": ["PENDING", "PENDING"],
                    "batch_inference_model_name": ["gemini-2.5-pro", "gemini-2.5-pro"],
                }
            ),
        )

        # Act
        task.execute_task()

        # Assert — template + subject positionally, recipients from env, all five placeholders.
        args, kwargs = task._notifier.send_template.call_args
        assert args == ("fact_check_result.txt", "[AI-Tax Invoice][Fact Check][Result] on 2026-07-06")
        assert kwargs["sender_email"] == "bot@example.com"
        assert kwargs["receiver_email"] == "dev@example.com"
        assert kwargs["cc_email"] == "oper@example.com"
        assert kwargs["REPORT_DATETIME"] == "2026-07-06 10:00:00"
        assert kwargs["MODEL_NAME"] == "gemini-2.5-pro"
        assert kwargs["GT_NO"] == 2
        assert kwargs["PRED_NO"] == 1
        # Table content is covered by test_email_notifier; here only the wiring is asserted.
        task._notifier.build_fact_check_table.assert_called_once_with(
            metric_rows, "config/tax_invoice_extraction/fact_check_uat_baseline.yml"
        )
        assert kwargs["FACT_CHECK_TABLE"] is task._notifier.build_fact_check_table.return_value

    def test_result_email_model_name_is_na_when_run_log_lacks_column(self, mocker):
        # Arrange — the fixture's pre_processing_log is an empty frame (no model column).
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        evaluator = mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        evaluator.return_value.evaluate.return_value = [
            {"label": "overall", "accuracy": 100.0, "precision": 100.0, "recall": 100.0, "f1_score": 100.0}
        ]
        mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))

        # Act
        task.execute_task()

        # Assert
        assert task._notifier.send_template.call_args.kwargs["MODEL_NAME"] == "N/A"

    def test_result_email_failure_is_swallowed(self, mocker):
        # Arrange — the Graph send raises; the run (and OCRFinalizeTask) must not be blocked.
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        task._notifier.send_template.side_effect = RuntimeError("graph down")
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        evaluator = mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        evaluator.return_value.evaluate.return_value = [
            {"label": "overall", "accuracy": 100.0, "precision": 100.0, "recall": 100.0, "f1_score": 100.0}
        ]
        mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))
        task.pre_result = pre_result

        # Act
        result = task.execute_task()

        # Assert
        assert result is pre_result

    def test_no_result_email_when_evaluator_returns_no_metric_rows(self, mocker):
        # Arrange — no ground-truth match: nothing to report, so no email.
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        builder = mocker.patch(f"{_TASK_MODULE}.ExtractionReportBuilder")
        builder.return_value.build.return_value = pd.DataFrame([{"FILE_NAME": "a.pdf"}])
        mocker.patch(f"{_TASK_MODULE}.FactCheckSourceLoader")
        evaluator = mocker.patch(f"{_TASK_MODULE}.FactCheckEvaluator")
        evaluator.return_value.evaluate.return_value = []
        mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))

        # Act
        task.execute_task()

        # Assert
        task._notifier.send_template.assert_not_called()

    def test_non_ocr_result_passes_through(self, mocker):
        # Arrange
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        emit = mocker.patch(f"{_TASK_MODULE}.emit_fact_check_logs")
        task.pre_result = None

        # Act
        result = task.execute_task()

        # Assert
        assert result is None
        emit.assert_not_called()


class TestOnError:
    def test_sends_system_exception_email_bot_to_dev_cc_oper(self, mocker, monkeypatch):
        # Arrange
        monkeypatch.setenv("BOT_EMAIL", "bot@example.com")
        monkeypatch.setenv("DEVELOPER_EMAIL", "dev@example.com")
        monkeypatch.setenv("OPER_EMAIL", "oper@example.com")
        task = _make_task()
        task._notifier = mocker.MagicMock()

        # Act
        task.on_error(RuntimeError("boom"))

        # Assert
        _, kwargs = task._notifier.send_template.call_args
        assert kwargs["sender_email"] == "bot@example.com"
        assert kwargs["receiver_email"] == "dev@example.com"
        assert kwargs["cc_email"] == "oper@example.com"

    def test_no_notifier_is_safe(self):
        # Arrange — pre_execute never ran, so no _notifier attribute.
        task = _make_task()

        # Act / Assert — must not raise.
        task.on_error(RuntimeError("boom"))


class TestPostExecute:
    def test_exports_audit_logs_when_results_present(self, mocker):
        # Arrange
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        export = mocker.patch(f"{_TASK_MODULE}.ExportLogging")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))

        # Act
        result = task.post_execute("sentinel")

        # Assert
        assert result == "sentinel"
        export.return_value.export_logs.assert_called_once()

    def test_audit_log_failure_is_swallowed(self, mocker):
        # Arrange — ExportLogging raises; post_execute must not propagate.
        task = _make_task()
        task._sp_control = mocker.MagicMock()
        task._notifier = mocker.MagicMock()
        export = mocker.patch(f"{_TASK_MODULE}.ExportLogging")
        export.return_value.export_logs.side_effect = RuntimeError("sp down")
        task.pre_result = _ocr_result(pd.DataFrame([{"FILE_NAME": "a.pdf"}]))

        # Act
        result = task.post_execute("sentinel")

        # Assert
        assert result == "sentinel"
