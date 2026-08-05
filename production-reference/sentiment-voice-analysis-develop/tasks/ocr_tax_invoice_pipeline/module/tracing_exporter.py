"""Tracing-log exporter — SharePoint-only, one CSV per month, with a retention window.

Unlike :class:`LogExporter` (GCS source-of-truth + SharePoint mirror), the tracing log lives
solely on SharePoint and is partitioned into one CSV per month (``tracing_log_YYYYMM.csv``, the
month resolved from ``%{DATA_DATE_YYYYMM}`` in the config path). Each run appends only to the
current month's file; month-files whose month lies entirely before the retention cutoff are deleted
on write. The window is the shared ``framework.log_retention_days``
(:mod:`~tasks.ocr_tax_invoice_pipeline.helper.log_retention`), so a negative value disables the
sweep along with every other log's retention.
"""

from __future__ import annotations

import io
from zoneinfo import ZoneInfo

import pandas as pd

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import (
    DEFAULT_RETENTION_DAYS,
    month_file_pattern,
    retention_cutoff,
    sweep_month_files,
)

logger = Logger(__name__)

_CSV_ENCODING = "utf-8-sig"  # BOM for Thai character compatibility in Excel
_MONTH_FILE_PATTERN = month_file_pattern("tracing_log")  # e.g. tracing_log_202607.csv


class TracingLogExporter:
    """Appends tracing rows to the current month's SharePoint CSV and prunes expired month-files."""

    def __init__(self, sp_conn: SharePointModule) -> None:
        """Initialise with an injected SharePoint connection."""
        self._sp = sp_conn

    def save(
        self,
        new_rows: pd.DataFrame,
        sp_path: str,
        *,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        timezone: ZoneInfo | None = None,
        label: str = "tracing log",
    ) -> None:
        """Append *new_rows* to the month-partitioned CSV at *sp_path* and prune expired months.

        Args:
            new_rows: Rows to append (append-only; existing rows are never modified).
            sp_path: SharePoint path of this month's tracing-log CSV (already resolved to
                ``tracing_log_YYYYMM.csv``).
            retention_days: Month-files lying entirely before this window are deleted. **Negative
                disables the sweep.**
            timezone: Timezone the cutoff is anchored to (``framework.timezone``).
            label: Human-readable label for log lines.
        """
        if new_rows.empty:
            logger.info(f"{label}: no new rows to append; skipping write")
            return
        existing = self._load_csv(sp_path)
        merged = pd.concat([existing, new_rows], ignore_index=True) if not existing.empty else new_rows
        csv_bytes = merged.to_csv(index=False, encoding=_CSV_ENCODING).encode(_CSV_ENCODING)
        self._sp.upload_file(sp_path, csv_bytes)
        logger.info(f"{label} saved to SharePoint: {sp_path} ({len(merged)} row(s))")
        sweep_month_files(self._sp, sp_path, _MONTH_FILE_PATTERN, retention_cutoff(retention_days, timezone), label)

    def _load_csv(self, sp_path: str) -> pd.DataFrame:
        """Download + parse the existing CSV; empty frame when absent or unparseable."""
        try:
            raw = self._sp.get_item_by_path(sp_path).content
            return pd.read_csv(io.BytesIO(raw), dtype=str)
        except Exception as exc:
            logger.info(f"No existing tracing log at {sp_path} (first write): {exc}")
            return pd.DataFrame()
