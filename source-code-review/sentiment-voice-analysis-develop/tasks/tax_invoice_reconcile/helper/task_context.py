"""Immutable per-run task context for the tax-invoice reconcile + fact-check tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.core.task_interface import TaskInterface
from src.utils.common import get_value_by_path, resolve_date, resolve_env
from src.utils.file_utils import load_yaml
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import DEFAULT_RETENTION_DAYS

_COMMON_CONFIG_PATH = "config/common.yml"


@dataclass(frozen=True)
class ReconcileTaskContext:
    """Immutable per-run context derived from task config + engine packages.

    Shared by every task in this package (precheck / reconcile / fact-check); fields a given
    task does not use stay populated-but-ignored (the YAML decides which blocks are present),
    exactly like ``ocr_tax_invoice_pipeline``'s ``OCRTaskContext``. ``frozen=True`` is shallow:
    the dict fields are still mutable, so treat them as read-only.
    """

    execution_dt: datetime
    timezone: ZoneInfo
    gcp: dict[str, Any]
    framework: dict[str, Any]
    notifications: dict[str, Any]
    source_site: dict[str, Any]
    control_site: dict[str, Any]
    control_site_access: dict[str, Any]
    destination_site: dict[str, Any]
    msgraph_access: dict[str, Any]

    @classmethod
    def from_task(cls, task: TaskInterface) -> ReconcileTaskContext:
        """Build the context from a task's config blocks and engine packages.

        Reads ``task.get_config(...)`` / ``task.get_package(...)`` and ``config/common.yml``
        exactly as the legacy tasks' ``__init__`` did — including the common.yml ``control:``
        block being the source of control-site credentials and the ``msgraph:`` block being the
        source of Microsoft Graph credentials for email. The source/destination site blocks
        double as their own credential sets (``init_sharepoint`` reads the access keys off them).

        Args:
            task: The task instance whose config and packages seed the context.

        Returns:
            A fully populated, immutable :class:`ReconcileTaskContext`.

        Raises:
            ValueError: when ``framework.timezone`` is absent from ``config/common.yml``.
        """
        sharepoint_cfg = task.get_config("sharepoint", {})
        framework = task.get_config("framework", {})
        common_config = load_yaml(_COMMON_CONFIG_PATH)
        timezone = get_value_by_path(common_config, "framework.timezone")
        if not timezone:
            raise ValueError(f"Timezone not set in {_COMMON_CONFIG_PATH}")
        return cls(
            execution_dt=task.get_package("execution_dt"),
            timezone=ZoneInfo(timezone),
            gcp=task.get_config("gcp", {}),
            framework=framework,
            notifications=framework.get("notifications", {}),
            source_site=sharepoint_cfg.get("source_site", {}),
            control_site=sharepoint_cfg.get("control_site", {}),
            control_site_access=common_config.get("control", {}),
            destination_site=sharepoint_cfg.get("destination_site", {}),
            msgraph_access=common_config.get("msgraph", {}),
        )

    # --- Helpers shared verbatim by the precheck / reconcile / fact-check tasks -----------

    def recipients(self, case: str) -> dict[str, str | None]:
        """Resolve the from/to/cc address set for ``case`` from ``framework.notifications``."""
        cfg = self.notifications.get(case) or {}
        return {
            "sender_email": resolve_env(cfg.get("sender_email")),
            "receiver_email": resolve_env(cfg.get("receiver_email")),
            "cc_email": resolve_env(cfg.get("cc_email")),
        }

    def subject(self, template: str) -> str:
        """Fill a subject line's ``{date}`` token with the run date (configured tz, YYYY-MM-DD)."""
        dt = self.execution_dt or datetime.now(tz=self.timezone)
        local_dt = dt.astimezone(self.timezone) if dt.tzinfo else dt
        return template.format(date=local_dt.strftime("%Y-%m-%d"))

    def resolve_path(self, value: str) -> str:
        """Resolve ``${ENV}`` and ``%{DATA_DATE}`` placeholders in a path."""
        return resolve_date(resolve_env(value or ""), self.execution_dt)

    def logging_cfg(self) -> dict:
        """Resolved project info, monthly log paths, and retention window for ``ExportLogging``."""
        return {
            "project_id": resolve_env(self.gcp.get("project_id")),
            "project_name": resolve_env(self.gcp.get("project_name")),
            "transaction_log_path": self.resolve_path(self.control_site.get("transaction_log_file", "")),
            "performance_log_path": self.resolve_path(self.control_site.get("performance_log_file", "")),
            # Raw on purpose — resolve_retention_days (inside ExportLogging) env-resolves it itself.
            "retention_days": self.framework.get("log_retention_days", DEFAULT_RETENTION_DAYS),
        }
