"""OCR submit task — ingest, quality-gate, and submit source documents to Vertex AI Batch.

Registered as ``OCRSubmitTask``. Lists source files (optionally over a CLI date window),
uploads them to GCS landing, splits + IQS-scores each page, uploads accepted pages, builds
and submits Vertex AI Batch payloads, and appends the pre-processing + page-manifest logs.
This task starts its pipeline and returns ``None``.
"""

from __future__ import annotations

import asyncio

import pandas as pd
from pandera.errors import SchemaErrors

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.modules.google.gemini_batch import GeminiBatchModule
from src.utils.common import get_value_by_path, int_castable_errors, missing_string_errors, resolve_env
from src.utils.date_utils import has_data_date_placeholder, resolve_data_date_window
from src.utils.file_utils import load_yaml
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus
from tasks.ocr_tax_invoice_pipeline.helper.error_notify import notify_system_error
from tasks.ocr_tax_invoice_pipeline.helper.init_conn import init_sharepoint
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import latest_status_per_file
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import (
    DEFAULT_RETENTION_DAYS,
    expired_job_ids,
    resolve_retention_days,
    retention_cutoff,
)
from tasks.ocr_tax_invoice_pipeline.helper.task_context import OCRTaskContext
from tasks.ocr_tax_invoice_pipeline.module.batch_job_client import BatchJobClient
from tasks.ocr_tax_invoice_pipeline.module.batch_submitter import BatchSubmitter
from tasks.ocr_tax_invoice_pipeline.module.document_processor import DocumentProcessor
from tasks.ocr_tax_invoice_pipeline.module.gcs_router import GcsRouter
from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter
from tasks.ocr_tax_invoice_pipeline.module.page_processor import PageProcessor
from tasks.ocr_tax_invoice_pipeline.module.payload_builder import PayloadBuilder
from tasks.ocr_tax_invoice_pipeline.module.pre_log_builder import PreLogContext, PreLogRowBuilder
from tasks.ocr_tax_invoice_pipeline.module.source_loader import SourceFileLoader
from tasks.ocr_tax_invoice_pipeline.schema.pre_processing_log import PageManifestLogSchema, PreProcessingLogSchema

logger = Logger(__name__)


@task_registry.register("OCRSubmitTask")
class OCRSubmitTask(TaskInterface):
    """Download, split, score, and submit source files to Vertex AI Batch (orchestration only)."""

    DEFAULT_BATCH_JOB_LIMIT = 100_000
    DEFAULT_CONCURRENCY = 5
    DEFAULT_EXT_FILTER = (".pdf", ".jpg", ".jpeg", ".png")
    DEFAULT_BATCH_POLL_DELAY = 2
    _WINDOW_FLAGS = ("rerun_data_dt", "start_data_dt", "end_data_dt")

    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "gcp.project_id",
        "gcs.project_id",
        "gcs.landing_path",
        "gcs.processing_path",
        "gcs.payload_landing_path",
        "gcs.output_path",
        "gcs.pre_processing_log_path",
        "gcs.page_manifest_log_path",
        "vertexai.project_id",
        "vertexai.location",
        "vertexai.model",
        "sharepoint.source_site.site_domain",
        "sharepoint.source_site.site_path",
        "sharepoint.source_site.client_id",
        "sharepoint.source_site.client_secret",
        "sharepoint.source_site.tenant_id",
        "sharepoint.source_site.src_path",
        "sharepoint.control_site.pre_processing_log_path",
        "sharepoint.control_site.page_manifest_log_path",
        "sharepoint.control_site.system_prompt_path",
        "framework.iqs_config_path",
    )

    def __init__(self, **kwargs) -> None:
        """Build the immutable :class:`OCRTaskContext` from config + packages."""
        super().__init__(**kwargs)
        self.ctx = OCRTaskContext.from_task(self)

    def validate(self) -> bool:
        """Collect all config/window errors, log each at ERROR, and halt on any."""
        errors = missing_string_errors(self.config, self.REQUIRED_STRING_KEYS)
        errors += int_castable_errors(self.config, ("framework.concurrency_upload",), required=True)
        # 'framework.log_retention_days' is deliberately NOT validated here: an unset
        # TAX_INVOICE_LOG_RETENTION_DAYS resolves to "" and would halt the task. resolve_retention_days
        # warns and falls back to the default instead — a retention setting must never block a run.
        errors += int_castable_errors(
            self.config, ("framework.batch_job_limit", "framework.batch_status_check_delay_seconds"), required=False
        )
        errors += self._shape_errors()
        errors += self._window_errors()
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def _shape_errors(self) -> list[str]:
        """Return errors for the non-string config rules (scheme, dict, list)."""
        errors = []
        landing = get_value_by_path(self.config, "gcs.landing_path")
        if isinstance(landing, str) and landing and not landing.startswith("gs://"):
            errors.append(f"'gcs.landing_path' must start with 'gs://' (got: {landing!r})")
        gen_config = get_value_by_path(self.config, "vertexai.generation_config")
        if gen_config is not None and not isinstance(gen_config, dict):
            errors.append(f"'vertexai.generation_config' must be a dict when set (got: {type(gen_config).__name__})")
        ext_filter = get_value_by_path(self.config, "framework.ext_filter")
        if ext_filter is not None and (not isinstance(ext_filter, list) or not ext_filter):
            errors.append(f"'framework.ext_filter' must be a non-empty list when set (got: {ext_filter!r})")
        return errors

    def _window_errors(self) -> list[str]:
        """Validate the CLI date-window flag combination (invalid combos halt before I/O)."""
        try:
            resolve_data_date_window(
                self.get_package("rerun_data_dt"),
                self.get_package("start_data_dt"),
                self.get_package("end_data_dt"),
                self.ctx.execution_dt,
            )
        except ValueError as exc:
            return [str(exc)]
        return []

    def pre_execute(self) -> None:
        """Initialise SharePoint, the GCS router, and the pipeline collaborator objects."""
        logger.info("Initializing modules")
        self._sp_control = init_sharepoint("Control", self.ctx.control_site_access)
        sp_source = init_sharepoint("Source", self.ctx.source_site)
        self._router = GcsRouter(self.ctx.gcs, self.ctx.job_id, self.ctx.execution_dt)
        landing_gcs = self._router.module_for("landing_path")
        self._source_loader = SourceFileLoader(sp_source, landing_gcs)
        self._page_processor = self._build_page_processor(landing_gcs)
        self._batch_submitter = self._build_batch_submitter()
        self._pre_log_builder = PreLogRowBuilder(PreLogContext.from_task_context(self.ctx), sp_source)

    def _build_page_processor(self, landing_gcs) -> PageProcessor:
        """Construct the :class:`PageProcessor` (loads the IQS config)."""
        iqs_config = load_yaml(
            self.ctx.framework.get("iqs_config_path", "config/ocr_tax_invoice_pipeline/iqs_config.yml")
        )
        return PageProcessor(
            landing_gcs=landing_gcs,
            processing_gcs=self._router.module_for("processing_path"),
            processing_prefix=self._router.prefix_for("processing_path"),
            doc_processor=DocumentProcessor(iqs_config),
            job_id=self.ctx.job_id,
            pipeline_name=self.ctx.pipeline_name,
        )

    def _load_system_prompt(self) -> str:
        """Download the system prompt from the control SharePoint site; halt when absent or blank.

        Blank is a hard failure too: an existence-only check would still submit a paid
        Gemini batch with an empty ``system_instruction`` if the file uploads empty.

        Returns:
            The decoded system prompt text.

        Raises:
            FileNotFoundError: When the prompt file does not exist on the control site.
            ValueError: When the prompt file exists but contains only whitespace.
        """
        path = self._router.resolve(self.ctx.control_site.get("system_prompt_path", ""))
        if not self._sp_control.is_item_exists(path):
            raise FileNotFoundError(f"System prompt not found on control site: {path}")
        prompt = self._sp_control.get_item_by_path(path).content.decode("utf-8")
        if not prompt.strip():
            raise ValueError(f"System prompt file is empty on control site: {path}")
        return prompt

    def _build_batch_submitter(self) -> BatchSubmitter:
        """Construct the :class:`BatchSubmitter` (downloads the system prompt + builds the Gemini client)."""
        system_prompt = self._load_system_prompt()
        model = resolve_env(self.ctx.vertexai.get("model", ""))
        payload_builder = PayloadBuilder(
            model=model,
            generation_config=self.ctx.vertexai.get("generation_config", {}),
            system_prompt=system_prompt,
            pipeline_prefix=self.ctx.pipeline_name,
            line_limit=int(self.ctx.framework.get("batch_job_limit", self.DEFAULT_BATCH_JOB_LIMIT)),
        )
        gemini = GeminiBatchModule(
            genai_project_id=resolve_env(self.ctx.gcp.get("project_id", "")),
            genai_location=resolve_env(self.ctx.vertexai.get("location", "")),
        )
        poll_delay = int(self.ctx.framework.get("batch_status_check_delay_seconds", self.DEFAULT_BATCH_POLL_DELAY))
        return BatchSubmitter(
            payload_builder=payload_builder,
            batch_client=BatchJobClient(gemini, poll_delay_seconds=poll_delay),
            payload_gcs=self._router.module_for("payload_landing_path"),
            output_bucket=self._router.extract_bucket(self._router.resolved_path("output_path")),
            payload_prefix=self._router.prefix_for("payload_landing_path"),
            output_prefix=self._router.prefix_for("output_path"),
            model=model,
            job_id=self.ctx.job_id,
            dt_suffix=self.ctx.execution_dt.strftime("%Y%m%d%H%M%S"),
        )

    def execute_task(self) -> None:
        """Run the full submit chain for the current run; returns ``None`` (start of pipeline).

        Unsupported-extension files short-circuit the page-processing/batch-submit steps
        (empty ``uploaded`` still runs safely through both) but still persist their terminal
        REJECTED pre-processing-log rows.
        """
        datadate = self.ctx.execution_dt.strftime("%Y%m%d")
        existing_log = self._log_exporter("pre_processing_log_path").load_log(
            self._router.resolved_path("pre_processing_log_path")
        )
        in_flight = self._in_flight(existing_log)

        uploaded, failed, unsupported = self._load_source_files(in_flight)
        if not uploaded and not failed and not unsupported:
            logger.info(f"No new files to process for {datadate}")
            return

        manifest_rows, chunks = self._page_processor.run(uploaded)
        submissions = self._batch_submitter.run(chunks)
        pre_rows = self._pre_log_builder.build(uploaded, failed, unsupported, manifest_rows, submissions, datadate)

        # Retention is applied on this run's writes; a run with no new files writes (and prunes) nothing.
        cutoff = retention_cutoff(self._retention_days(), tz=self.ctx.timezone)
        expired = expired_job_ids(existing_log, cutoff)
        self._persist_logs(pre_rows, manifest_rows, expired)

    def on_error(self, error: Exception) -> None:
        """Log the failure and send the optional, config-gated system-error email."""
        super().on_error(error)
        notify_system_error(self.ctx, self.task_name, error)

    def _load_source_files(self, in_flight: set[str]) -> tuple[list[dict], list[dict], list[dict]]:
        """List (over the date window), filter in-flight, and upload to GCS landing.

        Returns:
            An ``(uploaded, failed, unsupported)`` tuple. ``unsupported`` entries were
            listed but never uploaded (extension outside ``ext_filter``); they still flow
            into :meth:`~PreLogRowBuilder.build` as terminal REJECTED rows.
        """
        src_paths = self._resolve_src_paths()
        ext_filter = list(self.ctx.framework.get("ext_filter", self.DEFAULT_EXT_FILTER))
        concurrency = int(resolve_env(str(self.ctx.framework.get("concurrency_upload", self.DEFAULT_CONCURRENCY))))
        landing_prefix = self._router.prefix_for("landing_path")

        files, unsupported = self._source_loader.list_files_union(src_paths, ext_filter)
        new_files = self._source_loader.filter_new(files, in_flight)
        if not new_files:
            return [], [], unsupported
        uploaded, failed = asyncio.run(self._source_loader.upload_to_landing(new_files, landing_prefix, concurrency))
        if failed:
            logger.warning(f"{len(failed)} file(s) failed to upload to GCS landing")
        return uploaded, failed, unsupported

    def _resolve_src_paths(self) -> list[str]:
        """Resolve ``src_path`` once per data date in the window; single path when no placeholder."""
        template = self.ctx.source_site.get("src_path", "")
        window_given = any(self.get_package(flag) for flag in self._WINDOW_FLAGS)
        if not has_data_date_placeholder(template):
            if window_given:
                logger.warning(
                    "Date-window flags are set but src_path has no %{DATA_DATE} placeholder; "
                    "listing the single configured path"
                )
            return [self._router.resolve(template)]
        dates = resolve_data_date_window(
            self.get_package("rerun_data_dt"),
            self.get_package("start_data_dt"),
            self.get_package("end_data_dt"),
            self.ctx.execution_dt,
        )
        resolved = [self._router.resolve(template, data_dt=d) for d in dates]
        # Order-preserving dedupe: coarse formats (e.g. %{DATA_DATE_YYYYMM}) collide across days.
        return list(dict.fromkeys(resolved))

    def _in_flight(self, log_df: pd.DataFrame) -> set[str]:
        """Return SharePoint paths whose latest log status is PENDING or PARTIAL."""
        in_flight_statuses = {JobStatus.PENDING.value, JobStatus.PARTIAL.value}
        latest = latest_status_per_file(log_df)
        if latest.empty:
            return set()
        return set(latest.loc[latest["status"].isin(in_flight_statuses), "sharepoint_input_path"])

    def _retention_days(self) -> int:
        """Retention window for both logs — ``framework.log_retention_days`` (negative disables)."""
        return resolve_retention_days(self.ctx.framework.get("log_retention_days", DEFAULT_RETENTION_DAYS))

    def _persist_logs(self, pre_log_rows: list[dict], manifest_rows: list[dict], expired_ids: set[str]) -> None:
        """Write the pre-processing and page-manifest CSVs to GCS + SharePoint (soft-validated).

        Each write prunes rows that have aged past the retention window out of the merged frame —
        regardless of status — inside the same generation precondition.
        """
        if pre_log_rows:
            pre_df = pd.DataFrame(pre_log_rows)
            self._validate_soft(pre_df, PreProcessingLogSchema, "pre-processing log")
            self._log_exporter("pre_processing_log_path").save_log(
                pre_df,
                self._router.resolved_path("pre_processing_log_path"),
                self._router.resolve(self.ctx.control_site.get("pre_processing_log_path", "")),
                label="pre-processing log",
                sort_by="update_dt",
            )
        if manifest_rows:
            manifest_df = pd.DataFrame(manifest_rows)
            self._validate_soft(manifest_df, PageManifestLogSchema, "page manifest")
            self._log_exporter("page_manifest_log_path").save_log(
                manifest_df,
                self._router.resolved_path("page_manifest_log_path"),
                self._router.resolve(self.ctx.control_site.get("page_manifest_log_path", "")),
                label="page-manifest log",
                expired_ids=expired_ids,
            )

    def _log_exporter(self, gcs_key: str) -> LogExporter:
        """Construct a :class:`LogExporter` bound to ``gcs_key``'s bucket + the control site."""
        return LogExporter(
            self._router.module_for(gcs_key),
            self._sp_control,
            retention_days=self._retention_days(),
            timezone=self.ctx.timezone,
        )

    @staticmethod
    def _validate_soft(df: pd.DataFrame, schema: type, label: str) -> None:
        """Validate against a Pandera schema without aborting — batches already exist by now."""
        try:
            schema.validate(df, lazy=True)
        except SchemaErrors as exc:
            logger.warning(f"{label} failed schema validation (writing anyway): {exc}")
