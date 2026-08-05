"""Fact-check the tax-invoice OCR extraction against a human-labelled ground truth.

Registered as ``TaxInvoiceFactCheckTask``. A post-processing business task that consumes the
upstream :class:`OCRResult` from ``OCRRetrieveTask``, runs its ``final_df`` through the reconcile
``ExtractionReportBuilder`` (Master-Buyer enrichment — same first step as ``ReconcileTask``),
compares the per-document fields against ``ground_truth.xlsx`` field-by-field, and emits per-field
+ overall accuracy metrics as ``AI-Operation Fact Check log`` JSON lines. It then emails the result
(accuracy-only HTML table, best-effort) per ``framework.notifications.fact_check_result``, writes
the transaction and performance logs exactly as ``ReconcileTask`` does, and **returns the upstream
``OCRResult`` unchanged** so the trailing ``OCRFinalizeTask`` can stamp terminal status afterwards.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pandas as pd

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.utils.common import get_value_by_path, missing_string_errors, resolve_env
from src.utils.date_utils import parse_datetime
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import unwrap_ocr_result
from tasks.tax_invoice_reconcile.helper.fact_check_log_emitter import emit_fact_check_logs
from tasks.tax_invoice_reconcile.helper.init_conn import init_email_notifier, init_sharepoint
from tasks.tax_invoice_reconcile.helper.task_context import ReconcileTaskContext
from tasks.tax_invoice_reconcile.module import (
    ExportLogging,
    ExtractionReportBuilder,
    FactCheckEvaluator,
    FactCheckSourceLoader,
)

logger = Logger(__name__)

_DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
# System-error email (on_error): BOT_EMAIL -> DEVELOPER_EMAIL, cc OPER_EMAIL (per framework.notifications).
_PROCESSING_FAILED_TEMPLATE = "processing_failed.txt"
_SUBJECT_PROCESSING_FAILED = "[AI-Tax Invoice][Fact Check][System Exception] on {date}"
_CASE_SYSTEM = "system_exception"
# Result email (execute_task, best-effort): recipients per framework.notifications.fact_check_result.
_FACT_CHECK_RESULT_TEMPLATE = "fact_check_result.txt"
_SUBJECT_FACT_CHECK_RESULT = "[AI-Tax Invoice][Fact Check][Result] on {date}"
_CASE_RESULT = "fact_check_result"
_MODEL_NAME_COLUMN = "batch_inference_model_name"
_UNKNOWN_MODEL = "N/A"


@task_registry.register("TaxInvoiceFactCheckTask")
class TaxInvoiceFactCheckTask(TaskInterface):
    """Compare OCR extraction against ground truth and emit per-field accuracy logs."""

    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "gcp.project_id",
        "gcp.project_name",
        "sharepoint.control_site.ground_truth_file",
        "sharepoint.control_site.master_buyer_path",
        "sharepoint.control_site.transaction_log_file",
        "sharepoint.control_site.performance_log_file",
        "framework.email_template_dir",
        "framework.notifications.fact_check_result.baseline_path",
    )

    def __init__(self, **kwargs) -> None:
        """Build the immutable :class:`ReconcileTaskContext` (source/destination blocks stay empty)."""
        super().__init__(**kwargs)
        self.ctx = ReconcileTaskContext.from_task(self)

    def validate(self) -> bool:
        """Collect required-key errors, log each at ERROR, and halt on any."""
        errors = missing_string_errors(self.config, self.REQUIRED_STRING_KEYS)
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def pre_execute(self) -> None:
        """Initialise the control-site SharePoint connection and the system-error notifier."""
        logger.info("Initializing modules")
        self._sp_control = init_sharepoint("Control", self.ctx.control_site_access)
        self._notifier = init_email_notifier(self.ctx.framework, self.ctx.msgraph_access)

    def execute_task(self) -> Any:
        """Score the OCR extraction against ground truth; return the upstream result unchanged.

        Returns:
            ``self.pre_result`` (the upstream ``OCRResult``) unchanged, so the finalize task can
            still stamp terminal status. Passes straight through when there is no OCR result.
        """
        ocr_results_df, _ = unwrap_ocr_result(self.pre_result)
        if not isinstance(ocr_results_df, pd.DataFrame) or ocr_results_df.empty:
            logger.warning("No OCR results to fact-check; passing upstream result through.")
            return self.pre_result

        loader = FactCheckSourceLoader(self._sp_control, self.ctx.control_site)
        ground_truth_df = loader.load_ground_truth()
        processing_df = ExtractionReportBuilder().build(ocr_results_df, loader.load_master_buyer())
        metric_rows = FactCheckEvaluator().evaluate(processing_df, ground_truth_df)
        emit_fact_check_logs(
            metric_rows,
            created_datetime=self.ctx.execution_dt.strftime(_DATETIME_FORMAT),
            processed_datetime=self._processed_datetime(ocr_results_df),
            gcp_project_id=resolve_env(self.ctx.gcp.get("project_id", "")),
        )
        self._send_result_email(metric_rows, gt_count=len(ground_truth_df), pred_count=len(processing_df))
        return self.pre_result

    def post_execute(self, result: Any) -> Any:
        """Export transaction / performance / AI-operation logs (best-effort), like reconcile.

        Args:
            result: The value returned by :meth:`execute_task` (passed straight through).

        Returns:
            ``result`` unchanged.
        """
        ocr_results_df, run_log_df = unwrap_ocr_result(self.pre_result)
        if not isinstance(ocr_results_df, pd.DataFrame) or ocr_results_df.empty:
            logger.info("No OCR results to log; skipping audit-log export.")
            return result
        try:
            ExportLogging(
                execution_dt=self.ctx.execution_dt,
                ocr_df=ocr_results_df,
                pre_log_df=run_log_df,
                cfg=self.ctx.logging_cfg(),
                sharepoint=self._sp_control,
            ).export_logs(enable_oper_log=False, p_type="AI Fact-Checker")
        except Exception as exc:
            logger.error(f"Audit-log export failed (fact-check logs already emitted): {exc}", exc_info=True)
        return result

    def on_error(self, error: Exception) -> None:
        """Email a system-error alert (best-effort) when the fact-check run errors out.

        Sends ``BOT_EMAIL`` -> ``DEVELOPER_EMAIL`` (cc ``OPER_EMAIL``) per the task's
        ``framework.notifications.system_exception`` block. Swallows mail failures so the
        original error still propagates.

        Args:
            error: The exception that aborted the task (re-raised by the framework after this).
        """
        logger.error(f"Fact-check task failed: {error}", exc_info=True)
        notifier = getattr(self, "_notifier", None)
        if notifier is None:
            return
        try:
            notifier.send_template(
                _PROCESSING_FAILED_TEMPLATE,
                self.ctx.subject(_SUBJECT_PROCESSING_FAILED),
                **self.ctx.recipients(_CASE_SYSTEM),
            )
        except Exception as exc:
            logger.error(f"Failed to send system-exception notification: {exc}", exc_info=True)

    def _send_result_email(self, metric_rows: list[dict], gt_count: int, pred_count: int) -> None:
        """Email the fact-check result table (best-effort; a mail failure never fails the run).

        Sends per the task's ``framework.notifications.fact_check_result`` block, filling the
        ``fact_check_result.txt`` template's placeholders. Skips (with a warning) when the
        evaluator produced no metric rows — there is nothing to report.

        Args:
            metric_rows: ``FactCheckEvaluator.evaluate()`` output (per-field + overall rows).
            gt_count: Number of ground-truth document lines loaded.
            pred_count: Number of extraction (prediction) document lines scored.
        """
        if not metric_rows:
            logger.warning("No fact-check metrics to report; skipping the result email.")
            return
        try:
            self._notifier.send_template(
                _FACT_CHECK_RESULT_TEMPLATE,
                self.ctx.subject(_SUBJECT_FACT_CHECK_RESULT),
                **self.ctx.recipients(_CASE_RESULT),
                REPORT_DATETIME=self.ctx.execution_dt.strftime(_DATETIME_FORMAT),
                MODEL_NAME=self._model_name(),
                GT_NO=gt_count,
                PRED_NO=pred_count,
                FACT_CHECK_TABLE=self._notifier.build_fact_check_table(
                    metric_rows, get_value_by_path(self.ctx.notifications, f"{_CASE_RESULT}.baseline_path", "")
                ),
            )
        except Exception as exc:
            logger.error(f"Failed to send fact-check result email: {exc}", exc_info=True)

    def _model_name(self) -> str:
        """Unique batch-inference model name(s) from the upstream run log; ``N/A`` when unknown."""
        _, run_log_df = unwrap_ocr_result(self.pre_result)
        if not isinstance(run_log_df, pd.DataFrame) or _MODEL_NAME_COLUMN not in run_log_df.columns:
            return _UNKNOWN_MODEL
        names = run_log_df[_MODEL_NAME_COLUMN].dropna().unique()
        return ", ".join(str(name) for name in names) if len(names) else _UNKNOWN_MODEL

    def _processed_datetime(self, ocr_results_df: pd.DataFrame) -> str | None:
        """Most common Gemini processed time (``END_TIME``) in the run timezone; None when absent.

        Mirrors the telesale/QA fact-check convention: ``processed_datetime`` is the batch
        response's prediction ``processed_time`` (mode across the run's rows, converted to the
        configured timezone), not the metric-emission time.
        """
        column = ocr_results_df.get("END_TIME", pd.Series(dtype=object))
        values = [value for value in column if pd.notna(value)]
        if not values:
            return None
        raw = max(set(values), key=values.count)
        if isinstance(raw, datetime) and raw.tzinfo is None:
            # END_TIME wall-clock is UTC (parsed as UTC by the retriever); the finalizer's
            # DuckDB + pandera round-trip drops the tzinfo, so re-attach it before converting.
            raw = raw.replace(tzinfo=UTC)
        parsed = parse_datetime(raw, self.ctx.timezone)
        return parsed.strftime(_DATETIME_FORMAT) if parsed else None
