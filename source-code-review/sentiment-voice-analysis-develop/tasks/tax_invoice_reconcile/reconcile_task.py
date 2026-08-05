"""Reconcile OCR output with the Master Buyer and SAP ZAPRPT45 (Z45) report (OCR-pipeline v2).

Registered as ``ReconcileTask``. Consumes the upstream :class:`OCRResult` from ``OCRRetrieveTask``,
reconciles its ``final_df`` against the Master Buyer and Z45 sources, exports the per-document
Output workbooks + archives, then **returns the upstream ``OCRResult`` unchanged** so the trailing
``OCRFinalizeTask`` can stamp terminal status only after this business logic succeeds. The
pre-processing-log snapshot needed for audit logs is read off ``OCRResult.pre_processing_log`` —
no GCS re-read here.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.modules.google.gcs import GCSModule
from src.utils.common import missing_string_errors, resolve_env
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import unwrap_ocr_result
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult
from tasks.tax_invoice_reconcile.helper.constant import ExtractionStatus
from tasks.tax_invoice_reconcile.helper.init_conn import init_email_notifier, init_gcs, init_sharepoint
from tasks.tax_invoice_reconcile.helper.task_context import ReconcileTaskContext
from tasks.tax_invoice_reconcile.module import (
    ExportLogging,
    ExtractionReportBuilder,
    OutputExporter,
    ReconciliationBuilder,
    ReportSourceLoader,
    SourceArchiver,
    SourceRejecter,
)
from tasks.tax_invoice_reconcile.schema.extraction_output import to_extraction_output

logger = Logger(__name__)

_CSV_ENCODING = "utf-8-sig"  # BOM so Thai characters open correctly in Excel

# Stage notification templates + subjects (``{date}`` filled with the run date YYYY-MM-DD).
# Each case key selects a per-case from/to/cc set from ``framework.notifications``.
_EXTRACTION_TEMPLATE = "extraction.txt"
_REPORT_TEMPLATE = "report.txt"
_PROCESSING_FAILED_TEMPLATE = "processing_failed.txt"
_SUBJECT_EXTRACTION = "[AI-Tax Invoice][Extraction_Success] on {date}"
_SUBJECT_REPORT = "[AI-Tax Invoice][Mapping_Success] on {date}"
_SUBJECT_PROCESSING_FAILED = "[AI-Tax Invoice][System Exception] on {date}"
_CASE_EXTRACTION = "extraction_success"
_CASE_REPORT = "mapping_success"
_CASE_SYSTEM = "system_exception"


@task_registry.register("ReconcileTask")
class ReconcileTask(TaskInterface):
    """Reconcile the upstream OCR output against the Master Buyer and SAP Z45 report.

    Orchestration only: it loads the source files, builds the per-document extraction report
    (exported to the control site as a dated CSV), reconciles it against Z45, then exports one
    Extract&Mapping + one VAT Report workbook per source document to the destination site and
    archives the source invoices + the Z45 report. Returns the upstream :class:`OCRResult`
    unchanged so the pipeline's finalize task can stamp terminal status afterwards.
    """

    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "gcp.project_id",
        "gcp.project_name",
        "sharepoint.source_site.site_domain",
        "sharepoint.source_site.site_path",
        "sharepoint.source_site.client_id",
        "sharepoint.source_site.client_secret",
        "sharepoint.source_site.tenant_id",
        "sharepoint.source_site.master_buyer_path",
        "sharepoint.source_site.master_buyer_file",
        "sharepoint.source_site.master_vendor_path",
        "sharepoint.source_site.master_vendor_file",
        "sharepoint.source_site.z45_report_path",
        "sharepoint.source_site.z45_report_file",
        "sharepoint.destination_site.site_domain",
        "sharepoint.destination_site.site_path",
        "sharepoint.destination_site.client_id",
        "sharepoint.destination_site.client_secret",
        "sharepoint.destination_site.tenant_id",
        "sharepoint.destination_site.dest_path",
        "sharepoint.destination_site.archive_invoice_path",
        "sharepoint.destination_site.archive_vat_path",
        "sharepoint.control_site.extraction_result_path",
        "sharepoint.control_site.transaction_log_file",
        "sharepoint.control_site.performance_log_file",
        "framework.email_template_dir",
    )

    def __init__(self, **kwargs) -> None:
        """Build the immutable :class:`ReconcileTaskContext` and resolve destination paths."""
        super().__init__(**kwargs)
        self.ctx = ReconcileTaskContext.from_task(self)
        dest = self.ctx.destination_site
        self._dest_path: str = resolve_env(dest.get("dest_path", ""))
        self._archive_invoice_path: str = resolve_env(dest.get("archive_invoice_path", ""))
        self._archive_vat_path: str = resolve_env(dest.get("archive_vat_path", ""))
        self._reject_path: str = resolve_env(dest.get("reject_path", ""))
        # Per-bucket GCS modules, cached: a Suspicious page's child_path may live in any bucket.
        self._gcs_cache: dict[str, GCSModule] = {}

    def validate(self) -> bool:
        """Collect required-key errors, log each at ERROR, and halt on any."""
        errors = missing_string_errors(self.config, self.REQUIRED_STRING_KEYS)
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def pre_execute(self) -> None:
        """Initialise the source/control/destination SharePoint connections and notifier."""
        logger.info("Initializing modules")
        self._sp_source = init_sharepoint("Source", self.ctx.source_site)
        self._sp_control = init_sharepoint("Control", self.ctx.control_site_access)
        self._sp_dest = init_sharepoint("Destination", self.ctx.destination_site)
        self._notifier = init_email_notifier(self.ctx.framework, self.ctx.msgraph_access)
        self._rejecter = SourceRejecter(self._sp_dest, self._gcs_for_bucket, self._reject_path)

    def _gcs_for_bucket(self, bucket: str) -> GCSModule:
        """Return a cached :class:`GCSModule` for *bucket* (Suspicious-page ``child_path`` reads)."""
        if bucket not in self._gcs_cache:
            self._gcs_cache[bucket] = init_gcs({"project_id": self.ctx.gcp.get("project_id"), "bucket_name": bucket})
        return self._gcs_cache[bucket]

    def execute_task(self) -> Any:
        """Reconcile the upstream OCR output; return the upstream :class:`OCRResult` unchanged.

        Returns:
            ``self.pre_result`` (the upstream ``OCRResult``) unchanged once reconciled, or
            unchanged without work when there is no OCR result to reconcile — so the finalize
            task can still stamp dead-job FAILED files.
        """
        ocr_results_df, _ = unwrap_ocr_result(self.pre_result)
        if not isinstance(ocr_results_df, pd.DataFrame):
            logger.warning("Result from OCR task not found or not a DataFrame; passing upstream result through.")
            return self.pre_result
        if ocr_results_df.empty:
            logger.warning("Result from OCR task is empty; passing upstream result through.")
            return self.pre_result

        loader = ReportSourceLoader(self._sp_source, self.ctx.source_site)
        master_buyer_df = loader.load_master_buyer()
        processing_df = ExtractionReportBuilder().build(ocr_results_df, master_buyer_df)
        # Persist the export projection of the extraction report (dated CSV) to the control
        # site — drops the internal match inputs that reconciliation needs but the CSV omits.
        # A SharePoint failure is logged and swallowed (the in-memory frames still flow on).
        extraction_output_df = to_extraction_output(processing_df)
        extraction_result_path = self.ctx.resolve_path(self.ctx.control_site.get("extraction_result_path", ""))
        try:
            csv_bytes = extraction_output_df.to_csv(index=False, encoding=_CSV_ENCODING).encode(_CSV_ENCODING)
            self._sp_control.upload_file(extraction_result_path, csv_bytes)
            logger.info(
                f"extraction report ({len(extraction_output_df)} rows) saved to SharePoint: {extraction_result_path}"
            )
        except Exception as exc:
            logger.warning(f"SharePoint upload failed for extraction report at {extraction_result_path}: {exc}")
        # Stage 2: extraction landed on the control site — notify with per-file counts.
        self._notify(
            _CASE_EXTRACTION,
            _EXTRACTION_TEMPLATE,
            self.ctx.subject(_SUBJECT_EXTRACTION),
            **self._extraction_counts(processing_df),
        )
        master_vendor_df = loader.load_master_vendor()
        z45_report_df = loader.load_z45()
        report_df, z45_enriched_df, z45_link_df = ReconciliationBuilder().build(
            processing_df, z45_report_df, master_vendor_df
        )

        # Export the merged Extract&Mapping + VAT Report workbooks, then archive the processed
        # source invoices and the Z45 source report. The folder date is the run's DATADATE value
        # so reruns of a past data date land in the right dated folder.
        datadate = self._datadate(processing_df)
        OutputExporter(self._sp_dest, self._dest_path).export(processing_df, report_df, z45_enriched_df, z45_link_df)
        archiver = SourceArchiver(self._sp_source, self._sp_dest, self._archive_invoice_path, self._archive_vat_path)
        archiver.archive_invoices(processing_df, datadate)
        archiver.archive_z45(self._z45_source_path(z45_report_df), datadate)
        # Copy each Suspicious page's immutable GCS chunk into the reject folder (original still archived).
        if isinstance(self.pre_result, OCRResult):
            self._rejecter.reject_suspicious(
                ocr_results_df, self.pre_result.pre_processing_log, self.pre_result.page_manifest_log, datadate
            )

        logger.info(
            f"Exported Output Report ({len(report_df)} rows) and enriched Z45 "
            f"({len(z45_enriched_df)} rows) to SharePoint."
        )
        # Stage 3: the mapping report reached the output path — notify completion.
        self._notify(_CASE_REPORT, _REPORT_TEMPLATE, self.ctx.subject(_SUBJECT_REPORT))
        return self.pre_result

    def post_execute(self, result: Any) -> Any:
        """Export transaction / performance / AI-operation logs for the OCR run.

        Best-effort: this runs after the reconcile output has already been written to
        SharePoint, so a logging failure is logged and swallowed rather than raised — an
        audit-log problem must not undo the delivered Output Report.

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
            ).export_logs()
        except Exception as exc:
            logger.error(f"Audit-log export failed (reconcile output already delivered): {exc}", exc_info=True)
        return result

    def on_error(self, error: Exception) -> None:
        """Email a failure alert (best-effort) when the reconcile run errors out.

        Args:
            error: The exception that aborted the task (re-raised by the framework after this).
        """
        logger.error(f"Reconcile task failed: {error}", exc_info=True)
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
            logger.error(f"Failed to send processing-failed notification: {exc}", exc_info=True)

    def _notify(self, case: str, template_name: str, subject: str, **placeholders: object) -> None:
        """Send a stage notification, swallowing errors so a mail failure never undoes delivery."""
        try:
            self._notifier.send_template(template_name, subject, **self.ctx.recipients(case), **placeholders)
        except Exception as exc:
            logger.error(f"Notification '{template_name}' failed to send: {exc}", exc_info=True)

    @staticmethod
    def _extraction_counts(processing_df: pd.DataFrame) -> dict[str, int]:
        """Return per-input-file extraction counts keyed for the ``extraction.txt`` template.

        A file is counted as success only when *every* row for that ``FILE_NAME`` has
        ``DOC_STATUS == ExtractionStatus.COMPLETED``; any "Human in the loop" row marks the
        whole file failed.

        Args:
            processing_df: The per-document extraction report.

        Returns:
            ``{"PROCESSING_NO": ..., "SUCCESS_NO": ..., "FAILED_NO": ...}`` (distinct files).
        """
        completed = ExtractionStatus.COMPLETED.value
        per_file = processing_df.groupby("FILE_NAME")["DOC_STATUS"].apply(lambda s: bool((s == completed).all()))
        processing_no = int(per_file.size)
        success_no = int(per_file.sum())
        return {
            "PROCESSING_NO": processing_no,
            "SUCCESS_NO": success_no,
            "FAILED_NO": processing_no - success_no,
        }

    def _datadate(self, processing_df: pd.DataFrame) -> int:
        """Return the run's ``DATADATE`` value (``YYYYMMDD`` int) from ``processing_df``.

        Falls back to the execution date (in the configured timezone) as ``YYYYMMDD`` when no
        row carries a ``DATADATE`` value.
        """
        values = processing_df["DATADATE"].dropna()
        if values.empty:
            dt = self.ctx.execution_dt
            local_dt = dt.astimezone(self.ctx.timezone) if dt.tzinfo else dt
            return int(local_dt.strftime("%Y%m%d"))
        return int(values.iloc[0])

    @staticmethod
    def _z45_source_path(z45_report_df: pd.DataFrame) -> str:
        """Return the SharePoint source path of the loaded Z45 report (or ``''``)."""
        if "path_file" in z45_report_df.columns and not z45_report_df.empty:
            return str(z45_report_df["path_file"].iloc[0])
        return ""
