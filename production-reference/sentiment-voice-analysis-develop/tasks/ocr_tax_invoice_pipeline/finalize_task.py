"""OCR finalize task — stamp terminal statuses AFTER business logic succeeds.

Registered as ``OCRFinalizeTask`` and always the LAST task in the pipeline. Consumes the
:class:`OCRResult` threaded through the business task(s) and appends the terminal
SUCCESS / SUCCESS_WITH_FAILURE / FAILED rows to the pre-processing log. Because it runs only
after business logic, a business-task exception leaves files PENDING/PARTIAL and the next run
re-collects the (already-completed) Vertex predictions from GCS at zero additional Gemini cost.
Idempotent: only still-in-flight files are stamped, so a re-run is a no-op.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from pandera.errors import SchemaErrors

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.utils.common import missing_string_errors
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.error_notify import notify_system_error
from tasks.ocr_tax_invoice_pipeline.helper.init_conn import init_sharepoint
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import DEFAULT_RETENTION_DAYS, resolve_retention_days
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter
from tasks.ocr_tax_invoice_pipeline.module.status_finalizer import aggregate_file_messages, build_terminal_log_rows
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult
from tasks.ocr_tax_invoice_pipeline.schema.pre_processing_log import PreProcessingLogSchema

logger = Logger(__name__)


@task_registry.register("OCRFinalizeTask")
class OCRFinalizeTask(TaskInterface):
    """Append terminal-status rows to the pre-processing log; always the pipeline's last task."""

    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "gcs.project_id",
        "gcs.pre_processing_log_path",
        "sharepoint.control_site.pre_processing_log_path",
    )

    def __init__(self, **kwargs) -> None:
        """Build the immutable :class:`OCRTaskContext` from config + packages."""
        super().__init__(**kwargs)
        self.ctx = OCRTaskContext.from_task(self)

    def validate(self) -> bool:
        """Collect required-key errors, log each at ERROR, and halt on any."""
        errors = missing_string_errors(self.config, self.REQUIRED_STRING_KEYS)
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def pre_execute(self) -> None:
        """Initialise the SharePoint control connection and the GCS router."""
        self._sp_control = init_sharepoint("Control", self.ctx.control_site_access)
        self._router = GcsRouter(self.ctx.gcs, self.ctx.job_id, self.ctx.execution_dt)

    def execute_task(self) -> OCRResult | None:
        """Stamp terminal statuses for files completed this run; pass the result through.

        Returns ``None`` when there is no upstream :class:`OCRResult` with statuses (None,
        a bare DataFrame from a legacy chain, or empty statuses). Otherwise returns the
        upstream result unchanged so the chain stays composable.
        """
        result = self.pre_result
        if not isinstance(result, OCRResult) or not result.file_statuses:
            logger.info("Nothing to finalize (no upstream OCRResult with file statuses)")
            return None

        # The pre-processing-log snapshot is threaded through OCRResult (loaded once in
        # retrieve), so no GCS re-read here — only the terminal-status append below writes.
        exporter = LogExporter(
            self._router.module_for("pre_processing_log_path"),
            self._sp_control,
            retention_days=self._retention_days(),
            timezone=self.ctx.timezone,
        )
        rows = build_terminal_log_rows(
            result.file_statuses,
            result.pre_processing_log,
            datetime.now(self.ctx.timezone).isoformat(),
            file_messages=aggregate_file_messages(result.final_df),
        )
        if not rows:
            logger.info("No in-flight files to stamp (already terminal or absent from the log)")
            return result

        rows_df = pd.DataFrame(rows)
        self._validate_soft(rows_df)
        exporter.save_log(
            rows_df,
            self._router.resolved_path("pre_processing_log_path"),
            self._router.resolve(self.ctx.control_site.get("pre_processing_log_path", "")),
            label="finalize status",
            sort_by="update_dt",
        )
        logger.info(f"Stamped {len(rows)} terminal status row(s) to the pre-processing log")
        return result

    def _retention_days(self) -> int:
        """Retention window for the pre-processing log — ``framework.log_retention_days``."""
        return resolve_retention_days(self.ctx.framework.get("log_retention_days", DEFAULT_RETENTION_DAYS))

    def on_error(self, error: Exception) -> None:
        """Log the failure and send the optional, config-gated system-error email."""
        super().on_error(error)
        notify_system_error(self.ctx, self.task_name, error)

    @staticmethod
    def _validate_soft(df: pd.DataFrame) -> None:
        """Validate against PreProcessingLogSchema without aborting (log-don't-crash)."""
        try:
            PreProcessingLogSchema.validate(df, lazy=True)
        except SchemaErrors as exc:
            logger.warning(f"finalize status rows failed schema validation (writing anyway): {exc}")
