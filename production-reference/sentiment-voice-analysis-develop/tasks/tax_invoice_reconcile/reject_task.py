"""IQS reject-move task for the tax-invoice pre-pipeline.

Registered as ``TaxInvoiceRejectTask``. Runs after ``OCRSubmitTask`` in
``ocr_tax_invoice_pipeline_pre_tasks.yml``. Reads the stamped pre-processing and page-manifest logs
from GCS, filters to this run's ``job_id``, and delegates to :class:`IqsRejecter` to move
fully-rejected files and split bad pages from partial files.

This task is best-effort: per-file errors are logged and swallowed so the pre pipeline
never aborts. Returns the upstream ``pre_result`` unchanged (always ``None`` in the pre
pipeline, since ``OCRSubmitTask`` starts it).
"""

from __future__ import annotations

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.utils.common import missing_string_errors, resolve_env
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.module.log_exporter import LogExporter
from tasks.tax_invoice_reconcile.helper.init_conn import init_gcs, init_sharepoint
from tasks.tax_invoice_reconcile.module.iqs_rejecter import IqsRejecter

logger = Logger(__name__)

_REQUIRED_STRING_KEYS: tuple[str, ...] = (
    "gcp.project_id",
    "gcs.project_id",
    "gcs.pre_processing_log_path",
    "gcs.page_manifest_log_path",
    "sharepoint.source_site.site_domain",
    "sharepoint.source_site.site_path",
    "sharepoint.source_site.client_id",
    "sharepoint.source_site.client_secret",
    "sharepoint.source_site.tenant_id",
    "sharepoint.source_site.reject_path",
)


@task_registry.register("TaxInvoiceRejectTask")
class TaxInvoiceRejectTask(TaskInterface):
    """Move IQS-rejected files/pages from the source site to the reject folder."""

    def validate(self) -> bool:
        """Collect config errors and halt on any."""
        errors = missing_string_errors(self.config, _REQUIRED_STRING_KEYS)
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def pre_execute(self) -> None:
        """Initialise SharePoint, GCS log reader, and the IqsRejecter."""
        source_site = self.get_config("sharepoint", {}).get("source_site", {})
        self._sp_source = init_sharepoint("Source", source_site)

        gcs_cfg = self.get_config("gcs", {})
        self._pre_log_path = resolve_env(gcs_cfg.get("pre_processing_log_path", ""))
        self._manifest_log_path = resolve_env(gcs_cfg.get("page_manifest_log_path", ""))
        bucket = self._pre_log_path[len("gs://") :].split("/")[0]
        project_id = resolve_env(gcs_cfg.get("project_id", ""))
        gcs_module = init_gcs({"project_id": project_id, "bucket_name": bucket})
        self._log_exporter = LogExporter(gcs_module, self._sp_source)

        reject_root = resolve_env(source_site.get("reject_path", ""))
        self._rejecter = IqsRejecter(self._sp_source, reject_root)

    def execute_task(self) -> None:
        """Load this run's logs from GCS and move IQS rejects to the reject folder.

        Reads the pre-processing and page-manifest logs written by ``OCRSubmitTask`` earlier in
        the same pre pipeline, filters both to this run's ``job_id``, and delegates to
        :class:`IqsRejecter` to move fully-rejected files and split bad pages out of partial
        files.

        Returns:
            None. This task is best-effort: rejection failures are logged and swallowed rather
            than raised, so the pre pipeline never aborts on a reject-move error.

        Raises:
            AttributeError: If the ``execution_dt`` package is missing (``None`` has no
                ``strftime``).
        """
        execution_dt = self.get_package("execution_dt")
        job_id = self.get_package("job_id")
        datadate = int(execution_dt.strftime("%Y%m%d"))

        pre_log_df = self._log_exporter.load_log(self._pre_log_path)
        manifest_df = self._log_exporter.load_log(self._manifest_log_path)

        pre_run_df = pre_log_df[pre_log_df["job_id"] == job_id] if not pre_log_df.empty else pre_log_df
        manifest_run_df = manifest_df[manifest_df["job_id"] == job_id] if not manifest_df.empty else manifest_df

        logger.info(f"[{self.task_name}] Processing {len(pre_run_df)} log row(s) for job {job_id}")
        try:
            self._rejecter.reject(pre_run_df, manifest_run_df, datadate)
        except Exception as exc:
            logger.warning(f"[{self.task_name}] IQS reject handling encountered an error: {exc}")
