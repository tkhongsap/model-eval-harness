"""OCR retrieve task — poll in-flight jobs, collect predictions, compute terminal statuses.

Registered as ``OCRRetrieveTask``. Reads the pre-processing log for in-flight (PENDING /
PARTIAL) jobs, polls each Vertex AI job once, retrieves every succeeded job's predictions,
finalizes them against the page manifest, exports the raw-Gemini tracing log to
SharePoint, and returns a typed :class:`OCRResult` (final frame + terminal statuses) for the
business task and finalize task. The result frame is threaded forward via ``OCRResult`` (not
written to GCS here). It does **not** stamp terminal statuses into the log.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.modules.google.gemini_batch import GeminiBatchModule
from src.utils.common import missing_string_errors, resolve_env
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus
from tasks.ocr_tax_invoice_pipeline.helper.error_notify import notify_system_error
from tasks.ocr_tax_invoice_pipeline.helper.init_conn import init_sharepoint
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import latest_status_per_file
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import DEFAULT_RETENTION_DAYS, resolve_retention_days
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.module.batch_job_client import BatchJobClient
from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter
from tasks.ocr_tax_invoice_pipeline.module.result_finalizer import ResultFinalizer
from tasks.ocr_tax_invoice_pipeline.module.result_retriever import BatchResultRetriever
from tasks.ocr_tax_invoice_pipeline.module.status_finalizer import resolve_terminal_statuses
from tasks.ocr_tax_invoice_pipeline.module.tracing_builder import TracingLogBuilder, TracingLogContext
from tasks.ocr_tax_invoice_pipeline.module.tracing_exporter import TracingLogExporter
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult

logger = Logger(__name__)

# Vertex AI JobState enum names. A partially-succeeded job still emits predictions.
TERMINAL_FAILED_STATES = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")
SUCCEEDED_STATES = ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED")
IN_FLIGHT_STATUSES = (JobStatus.PENDING.value, JobStatus.PARTIAL.value)


@task_registry.register("OCRRetrieveTask")
class OCRRetrieveTask(TaskInterface):
    """Retrieve and collect Vertex AI batch predictions into an ``OCRResult`` (no domain validation)."""

    DEFAULT_BATCH_POLL_DELAY = 2
    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "gcp.project_id",
        "gcs.project_id",
        "gcs.pre_processing_log_path",
        "gcs.page_manifest_log_path",
        "vertexai.location",
        "sharepoint.control_site.pre_processing_log_path",
        "sharepoint.control_site.tracing_log_path",
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
        """Initialise SharePoint control, the Gemini batch client, router, and retriever."""
        logger.info("Initializing modules")
        self._sp_control = init_sharepoint("Control", self.ctx.control_site_access)
        gemini = GeminiBatchModule(
            genai_project_id=resolve_env(self.ctx.gcp.get("project_id", "")),
            genai_location=resolve_env(self.ctx.vertexai.get("location", "")),
        )
        poll_delay = int(self.ctx.framework.get("batch_status_check_delay_seconds", self.DEFAULT_BATCH_POLL_DELAY))
        self._batch_client = BatchJobClient(gemini, poll_delay_seconds=poll_delay)
        self._router = GcsRouter(self.ctx.gcs, self.ctx.job_id, self.ctx.execution_dt)
        self._tracing_builder = TracingLogBuilder(TracingLogContext.from_task_context(self.ctx))
        self._tracing_exporter = TracingLogExporter(self._sp_control)
        self._retriever = BatchResultRetriever(gemini, self._router.module_for_bucket, self._tracing_builder)
        self._result_finalizer = ResultFinalizer()

    def execute_task(self) -> OCRResult | None:
        """Poll in-flight jobs, collect predictions, and return a typed :class:`OCRResult`.

        Returns ``None`` only when nothing is in-flight or every in-flight job is still
        running. When all polled jobs died with zero predictions, still returns an
        ``OCRResult`` (empty frame, FAILED statuses) so dead-job files do not leak as
        eternally PENDING.
        """
        pre_log = self._log_exporter("pre_processing_log_path").load_log(
            self._router.resolved_path("pre_processing_log_path")
        )
        manifest = self._log_exporter("page_manifest_log_path").load_log(
            self._router.resolved_path("page_manifest_log_path")
        )
        job_names = self._in_flight_jobs(pre_log)
        if not job_names:
            logger.info("No batch jobs in PENDING/PARTIAL status; nothing to retrieve")
            return None

        succeeded, failed, running = self._classify_jobs(job_names)
        if not succeeded and not failed:
            logger.info(f"All {len(running)} in-flight job(s) still running; nothing to retrieve yet")
            return None

        final_df = self._collect(succeeded, failed, pre_log, manifest)
        dead = tuple(job.name for job in failed if getattr(job, "name", ""))
        statuses = resolve_terminal_statuses(final_df, pre_log, dead, running_job_names=running)
        logger.info(f"Resolved {len(statuses)} terminal file status(es); {len(dead)} dead job(s)")
        return OCRResult(
            final_df=final_df,
            file_statuses=statuses,
            pre_processing_log=pre_log,
            page_manifest_log=manifest,
        )

    def on_error(self, error: Exception) -> None:
        """Log the failure and send the optional, config-gated system-error email."""
        super().on_error(error)
        notify_system_error(self.ctx, self.task_name, error)

    def _collect(
        self, succeeded: list[Any], failed: list[Any], pre_log: pd.DataFrame, manifest: pd.DataFrame
    ) -> pd.DataFrame:
        """Retrieve + finalize predictions; returns an EMPTY frame when nothing was collected."""
        # Drop empty frames before concat: an empty frame's columns are all object dtype and
        # would trigger a pandas FutureWarning about dtype coercion.
        retriever_succeeded = self._retriever.retrieve_succeeded(succeeded)
        retriever_failed = self._retriever.retrieve_failed(failed)
        # Export the raw-Gemini tracing log first so failed-only / blank runs still record traces.
        tracing_df = self._tracing_builder.build_tracing_log(retriever_succeeded[1] + retriever_failed[1])
        self._tracing_exporter.save(
            tracing_df,
            self._router.resolve(self.ctx.control_site.get("tracing_log_path", "")),
            retention_days=self._retention_days(),
            timezone=self.ctx.timezone,
        )
        frames = [df for df in (retriever_succeeded[0], retriever_failed[0]) if not df.empty]
        if not frames:
            logger.info("No predictions collected from succeeded/failed jobs")
            return pd.DataFrame()
        result_df = pd.concat(frames, ignore_index=True)
        for col in ("start_time", "end_time"):
            result_df[col] = pd.to_datetime(result_df[col], errors="coerce", utc=True).dt.tz_convert(self.ctx.timezone)
        return self._result_finalizer.run(result_df, pre_log, manifest)

    def _in_flight_jobs(self, log_df: pd.DataFrame) -> list[str]:
        """Return distinct batch job names whose latest per-file status is in-flight."""
        latest = latest_status_per_file(log_df)
        if latest.empty or "batch_inference_job_name" not in latest.columns:
            return []
        in_flight = latest.loc[latest["status"].isin(IN_FLIGHT_STATUSES), "batch_inference_job_name"]
        return [name for name in in_flight.dropna().unique().tolist() if name]

    def _classify_jobs(self, job_names: list[str]) -> tuple[list[Any], list[Any], list[str]]:
        """Poll each job once and partition into ``(succeeded, failed, still_running_names)``."""
        succeeded: list[Any] = []
        failed: list[Any] = []
        running: list[str] = []
        for job_name in job_names:
            job = self._batch_client.pull_job_detail(job_name=job_name)
            state = self._normalize_job_state(job)
            if state in SUCCEEDED_STATES:
                succeeded.append(job)
            elif state in TERMINAL_FAILED_STATES:
                failed.append(job)
                logger.error(f"Batch job {getattr(job, 'display_name', job_name)} ended in {state}")
            else:
                running.append(job_name)
                logger.info(f"Batch job {getattr(job, 'display_name', job_name)} still in progress: {state}")
        return succeeded, failed, running

    @staticmethod
    def _normalize_job_state(job: Any) -> str:
        """Best-effort string form of a ``BatchJob.state`` (enum or raw string)."""
        state = getattr(job, "state", None)
        if state is None:
            return "UNKNOWN_STATE"
        return getattr(state, "name", None) or str(state)

    def _retention_days(self) -> int:
        """Retention window for the tracing-log month sweep — ``framework.log_retention_days``."""
        return resolve_retention_days(self.ctx.framework.get("log_retention_days", DEFAULT_RETENTION_DAYS))

    def _log_exporter(self, gcs_key: str) -> LogExporter:
        """Construct a :class:`LogExporter` bound to ``gcs_key``'s bucket + the control site.

        Load-only here (this task stamps no statuses), but the configured window is passed
        anyway: the constructor logs a "log retention active" line, and it must state the
        real ``framework.log_retention_days`` value, not the 90-day default.
        """
        return LogExporter(
            self._router.module_for(gcs_key),
            self._sp_control,
            retention_days=self._retention_days(),
            timezone=self.ctx.timezone,
        )
