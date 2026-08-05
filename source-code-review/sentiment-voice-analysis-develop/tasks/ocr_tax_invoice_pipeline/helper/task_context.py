"""Immutable per-run task context shared by all OCR-pipeline tasks."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from src.core.task_interface import TaskInterface
from src.utils.common import get_value_by_path
from src.utils.file_utils import load_yaml

_COMMON_CONFIG_PATH = "config/common.yml"


@dataclass(frozen=True)
class OCRTaskContext:
    """Immutable per-run context derived from task config + engine packages.

    Fields a given task does not use stay as empty dicts — the dataclass is shared across
    the submit/retrieve/finalize tasks and the YAML decides which blocks are populated.
    ``frozen=True`` is shallow: the dict fields are still mutable, so treat them as read-only.
    """

    job_id: str
    pipeline_name: str
    execution_dt: datetime
    timezone: ZoneInfo
    domain: str
    gcp: dict[str, Any]
    gcs: dict[str, Any]
    vertexai: dict[str, Any]
    framework: dict[str, Any]
    notifications: dict[str, Any]
    control_site_access: dict[str, Any]
    source_site: dict[str, Any]
    control_site: dict[str, Any]
    msgraph_access: dict[str, Any]

    @classmethod
    def from_task(cls, task: TaskInterface) -> OCRTaskContext:
        """Build the context from a task's config blocks and engine packages.

        Reads ``task.get_config(...)`` / ``task.get_package(...)`` and ``config/common.yml``
        exactly as the legacy tasks' ``__init__`` did — including the common.yml ``control:``
        block being the source of control-site credentials and the ``msgraph:`` block being the
        source of Microsoft Graph credentials for the optional system-error email.

        Args:
            task: The task instance whose config and packages seed the context.

        Returns:
            A fully populated, immutable :class:`OCRTaskContext`.

        Raises:
            ValueError: when ``framework.timezone`` is absent from ``config/common.yml``.
        """
        sharepoint_cfg = task.get_config("sharepoint", {})
        source_site = sharepoint_cfg.get("source_site", {})
        control_site = sharepoint_cfg.get("control_site", {})
        framework = task.get_config("framework", {})
        common_config = load_yaml(_COMMON_CONFIG_PATH)
        timezone = get_value_by_path(common_config, "framework.timezone")
        if not timezone:
            raise ValueError(f"Timezone not set in {_COMMON_CONFIG_PATH}")
        return cls(
            job_id=task.get_package("job_id"),
            pipeline_name=task.get_package("pipeline_name"),
            execution_dt=task.get_package("execution_dt"),
            timezone=ZoneInfo(timezone),
            domain=task.get_config("domain", ""),
            gcp=task.get_config("gcp", {}),
            gcs=task.get_config("gcs", {}),
            vertexai=task.get_config("vertexai", {}),
            framework=framework,
            notifications=framework.get("notifications", {}),
            control_site_access=common_config.get("control", {}),
            source_site=source_site,
            control_site=control_site,
            msgraph_access=common_config.get("msgraph", {}),
        )
