"""Pre-reconcile dependency check for the tax-invoice pipeline (OCR-pipeline v2).

Registered as ``ReconcilePrecheckTask``. Verifies that the Master-Buyer and SAP ZAPRPT45 (Z45)
source files both exist on the source SharePoint site *before* the reconcile task runs. If any
are missing it emails a single consolidated notification listing the missing file(s) and halts
the pipeline (raising :class:`DependencyMissingError`); otherwise it logs and returns ``None``.

``execute_task`` always returns ``None`` — it runs *before* any ``OCRResult`` exists, so there is
nothing to pass through. This is only safe because this task is the **first** key in
``ocr_pipeline_post_tasks.yml`` (an order pinned by
``tests/test_tasks/tax_invoice_reconcile/test_pipeline_config.py``), where the engine's
``pre_result`` chain value is already ``None``.

.. warning::
   Do **not** move this task after ``OCRRetrieveTask``. The engine overwrites ``pre_result`` with
   each task's return value, so a ``None`` return placed after ``OCRRetrieveTask`` would wipe the
   ``OCRResult`` out of the chain — ``ReconcileTask`` would then see no upstream result,
   ``OCRFinalizeTask`` would stamp nothing, and every file would stay ``PENDING``/``PARTIAL``
   forever even though Gemini was already paid. See DEVELOPER_GUIDE.md § 2.
"""

from __future__ import annotations

from src.core.task_interface import TaskInterface
from src.core.task_registry import task_registry
from src.utils.common import missing_string_errors, resolve_env
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper.init_conn import init_email_notifier, init_sharepoint
from tasks.tax_invoice_reconcile.helper.task_context import ReconcileTaskContext

logger = Logger(__name__)

# Notification templates + subjects (``{date}`` filled with the run date YYYY-MM-DD).
# Each case key selects a per-case from/to/cc set from ``framework.notifications``.
_DEPENDENCY_MISSING_TEMPLATE = "dependency_missing.txt"
_PROCESSING_FAILED_TEMPLATE = "processing_failed.txt"
_SUBJECT_DEPENDENCY_MISSING = "[AI-Tax Invoice][Business Exception] on {date}"
_SUBJECT_PROCESSING_FAILED = "[AI-Tax Invoice][System Exception] on {date}"
_CASE_BUSINESS = "business_exception"
_CASE_SYSTEM = "system_exception"


class DependencyMissingError(Exception):
    """Raised when a required source file is absent — halts the pipeline (already notified)."""


@task_registry.register("ReconcilePrecheckTask")
class ReconcilePrecheckTask(TaskInterface):
    """Verify Master-Buyer + Z45 exist on SharePoint before reconcile; notify + halt if not."""

    # (folder_key, pattern_key, human label) for each required source file.
    _DEPENDENCIES = (
        ("master_buyer_path", "master_buyer_file", "Master Buyer file"),
        ("z45_report_path", "z45_report_file", "Z45 report file"),
        ("master_vendor_path", "master_vendor_file", "Master Vendor file"),
    )

    # Dotted config paths that must each resolve to a non-empty string.
    REQUIRED_STRING_KEYS: tuple[str, ...] = (
        "sharepoint.source_site.site_domain",
        "sharepoint.source_site.site_path",
        "sharepoint.source_site.client_id",
        "sharepoint.source_site.client_secret",
        "sharepoint.source_site.tenant_id",
        "sharepoint.source_site.master_buyer_path",
        "sharepoint.source_site.master_buyer_file",
        "sharepoint.source_site.z45_report_path",
        "sharepoint.source_site.z45_report_file",
        "sharepoint.source_site.master_vendor_path",
        "sharepoint.source_site.master_vendor_file",
        "framework.email_template_dir",
        "framework.notifications.business_exception.sender_email",
        "framework.notifications.business_exception.receiver_email",
        "framework.notifications.system_exception.sender_email",
        "framework.notifications.system_exception.receiver_email",
    )

    def __init__(self, **kwargs) -> None:
        """Build the immutable :class:`ReconcileTaskContext` from config + packages."""
        super().__init__(**kwargs)
        self.ctx = ReconcileTaskContext.from_task(self)

    def validate(self) -> bool:
        """Collect required-key errors, log each at ERROR, and halt on any."""
        errors = missing_string_errors(self.config, self.REQUIRED_STRING_KEYS)
        for error in errors:
            logger.error(f"Config validation error [{self.task_name}]: {error}")
        return not errors

    def pre_execute(self) -> None:
        """Initialise the source SharePoint connection and the email notifier."""
        logger.info("Initializing dependency-check modules")
        self._sp_source = init_sharepoint("Source", self.ctx.source_site)
        self._notifier = init_email_notifier(self.ctx.framework, self.ctx.msgraph_access)

    def execute_task(self) -> None:
        """Verify the required source files exist; notify + halt on any missing.

        Returns:
            None

        Raises:
            DependencyMissingError: When one or more required source files are missing.
        """
        missing = self._missing_dependencies()
        if missing:
            self._notify_missing(missing)
            raise DependencyMissingError(f"Missing required source file(s): {', '.join(missing)}")
        logger.info("All required source files present; proceeding to reconciliation.")

    def on_error(self, error: Exception) -> None:
        """Email a system-error alert; a business 'missing file' was already notified."""
        if isinstance(error, DependencyMissingError):
            logger.error(f"Dependency check halted the pipeline: {error}")
            return
        logger.error(f"Dependency check failed with a system error: {error}", exc_info=True)
        try:
            self._notifier.send_template(
                _PROCESSING_FAILED_TEMPLATE,
                self.ctx.subject(_SUBJECT_PROCESSING_FAILED),
                **self.ctx.recipients(_CASE_SYSTEM),
            )
        except Exception as exc:
            logger.error(f"Failed to send processing-failed notification: {exc}", exc_info=True)

    def _missing_dependencies(self) -> list[str]:
        """Return labels of every required source file with no SharePoint match."""
        missing = []
        for folder_key, pattern_key, label in self._DEPENDENCIES:
            folder_path = resolve_env(self.ctx.source_site.get(folder_key))
            pattern = self.ctx.source_site.get(pattern_key)
            matches = self._sp_source.list_files_pattern(folder_path=folder_path, pattern=pattern)
            if not matches:
                logger.warning(f"{label} not found in SharePoint at {folder_path} ({pattern})")
                missing.append(label)
        return missing

    def _notify_missing(self, missing: list[str]) -> None:
        """Send the consolidated missing-file email listing each label (best-effort)."""
        missing_files = "\n".join(f"- {label}" for label in missing)
        try:
            self._notifier.send_template(
                _DEPENDENCY_MISSING_TEMPLATE,
                self.ctx.subject(_SUBJECT_DEPENDENCY_MISSING),
                **self.ctx.recipients(_CASE_BUSINESS),
                MISSING_FILES=missing_files,
            )
        except Exception as exc:
            logger.error(f"Failed to send dependency-missing notification: {exc}", exc_info=True)
