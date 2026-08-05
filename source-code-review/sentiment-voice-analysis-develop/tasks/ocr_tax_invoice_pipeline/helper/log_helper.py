"""Shared helpers for reading the append-only pre-processing log."""

from __future__ import annotations

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.helper.constant import STATUS_RANK
from tasks.ocr_tax_invoice_pipeline.schema.contracts import OCRResult

_DEDUP_KEY = "sharepoint_input_path"
_REQUIRED_COLUMNS = {_DEDUP_KEY, "update_dt", "status"}


def unwrap_ocr_result(pre_result: object) -> tuple[pd.DataFrame | None, pd.DataFrame]:
    """Return ``(final_df, latest-status-per-file log frame)`` from an ``OCRResult`` hand-off.

    ``(None, empty frame)`` when the upstream hand-off is not an ``OCRResult`` — business
    tasks pass through in that case so a trailing ``OCRFinalizeTask`` can still run.
    """
    if isinstance(pre_result, OCRResult):
        return pre_result.final_df, latest_status_per_file(pre_result.pre_processing_log)
    return None, pd.DataFrame()


def latest_status_per_file(log_df: pd.DataFrame) -> pd.DataFrame:
    """Furthest-progressed append-only log row per ``sharepoint_input_path``.

    The log is append-only and ``update_dt`` is wall-clock — on Windows two rows
    written in the same ~15 ms clock tick collide, so ``update_dt`` alone cannot order
    ``INITIAL`` before ``PENDING``. Sorting by ``update_dt`` then the status lifecycle
    rank (:data:`STATUS_RANK`) with a stable kind breaks such ties deterministically
    toward the later status.

    Args:
        log_df: Append-only pre-processing log (full history).

    Returns:
        One row per file (the latest), or an empty frame when ``log_df`` is empty or
        missing any of the required columns.
    """
    if log_df.empty or not _REQUIRED_COLUMNS.issubset(log_df.columns):
        return log_df.iloc[0:0]
    ranked = log_df.assign(_rank=log_df["status"].map(STATUS_RANK).fillna(-1))
    ordered = ranked.sort_values(["update_dt", "_rank"], kind="stable")
    return ordered.groupby(_DEDUP_KEY).last().reset_index().drop(columns="_rank")
