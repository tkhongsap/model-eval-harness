"""Retention rules for every tax-invoice log.

One knob — ``TAX_INVOICE_LOG_RETENTION_DAYS``, surfaced as ``framework.log_retention_days`` — bounds
all of them. **A negative value (e.g. ``-1``) disables retention entirely**: :func:`retention_cutoff`
returns ``None`` and every function here treats a ``None`` cutoff as "keep everything".

Two log shapes need two rules:

* **Cumulative files** (``ocr_pre_processing_log.csv``, ``page_manifest_log.csv``) — prune *rows*.
* **Month-partitioned files** (``transaction_log_YYYYMM.csv``, ``performance_log_YYYYMM.csv``,
  ``tracing_log_YYYYMM.csv``) — delete whole expired *month-files* (:func:`sweep_month_files`).
  Row-pruning those too only matters for a window shorter than a month, but it is what makes such a
  window behave correctly inside the current month's file.

Rows age out purely by timestamp, **regardless of status**. An in-flight (PENDING/PARTIAL) row old
enough to cross the retention window is a *stuck* file (see [P1-3] in the 2026-07 repo review) —
pruning it is the deliberate backstop: the dedupe stops skipping the file, so if it still sits in the
input folder it is re-processed on the next submit run, at fresh Gemini cost. The flip side: the
window must comfortably exceed batch-job wall time (days) — a very short window can prune a
*still-running* job's rows and double-submit its file.

Rows with an unparseable timestamp are kept, not silently purged. The page manifest carries no
timestamp column, so its age is derived from the pre-processing log via the shared ``job_id``. A
``job_id`` absent from the pre-processing log is *kept* — that fail-safe is what protects a
concurrent run's freshly written manifest rows.

Every rule here is pure; the one exception is :func:`sweep_month_files`, the shared best-effort
SharePoint month-file deletion.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from zoneinfo import ZoneInfo

import pandas as pd

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.common import resolve_env
from src.utils.logger import Logger

logger = Logger(__name__)

DEFAULT_RETENTION_DAYS = 90
DEFAULT_TIMEZONE = "Asia/Bangkok"

_AGE_COLUMN = "update_dt"
_JOB_ID = "job_id"


def resolve_retention_days(raw: object) -> int:
    """Env-resolve and coerce a configured retention window, falling back to the default.

    ``resolve_env`` substitutes an *unset* env var with ``""``, so a missing
    ``TAX_INVOICE_LOG_RETENTION_DAYS`` must degrade to the default rather than crash on ``int("")``.

    Args:
        raw: The configured value (typically ``framework.log_retention_days``, possibly still an
            unresolved ``${...}`` placeholder).

    Returns:
        The window in days. Negative means retention is disabled. Empty or unparseable input
        returns :data:`DEFAULT_RETENTION_DAYS` with a WARNING.
    """
    text = (resolve_env(str(raw)) or "").strip() if raw is not None else ""
    try:
        return int(text)
    except ValueError:
        logger.warning(
            f"log retention: {text!r} is not a valid day count; falling back to {DEFAULT_RETENTION_DAYS} day(s)"
        )
        return DEFAULT_RETENTION_DAYS


def retention_cutoff(days: int = DEFAULT_RETENTION_DAYS, tz: ZoneInfo | None = None) -> pd.Timestamp | None:
    """Return the instant before which rows/files are eligible for pruning, or ``None`` if disabled.

    Anchored to "now" in the pipeline's configured timezone (``framework.timezone`` in
    ``config/common.yml``, surfaced as ``OCRTaskContext.timezone``) — never a hardcoded UTC.

    Args:
        days: Retention window in days. **Negative disables retention** (returns ``None``).
        tz: Timezone to anchor "now" to; falls back to :data:`DEFAULT_TIMEZONE` when ``None`` so the
            module stays usable without a config.

    Returns:
        The tz-aware cutoff, or ``None`` when retention is disabled.
    """
    if days < 0:
        logger.info(f"log retention disabled (log_retention_days={days}); no rows or files will be pruned")
        return None
    zone = tz or ZoneInfo(DEFAULT_TIMEZONE)
    cutoff = pd.Timestamp.now(tz=zone) - pd.Timedelta(days=days)
    logger.info(f"log retention active: window={days} day(s), cutoff={cutoff.isoformat()} (tz={zone})")
    return cutoff


def prune_by_timestamp(
    df: pd.DataFrame,
    cutoff: pd.Timestamp | None,
    ts_column: str,
    label: str = "log",
) -> pd.DataFrame:
    """Drop rows whose *ts_column* is older than *cutoff*; the row-level rule for every log.

    Rows with an unparseable timestamp are **kept**, not silently purged (``NaT < cutoff`` is
    ``False``, so a naive filter drops them). Naive timestamps are read as UTC, which errs toward
    keeping a row slightly longer — never toward pruning it early.

    Args:
        df: The frame to prune.
        cutoff: From :func:`retention_cutoff`; ``None`` keeps everything.
        ts_column: Name of the timestamp column that dates a row.
        label: Human-readable label for log lines.

    Returns:
        The retained rows (the frame unchanged when nothing is eligible).
    """
    if cutoff is None or df.empty or ts_column not in df.columns:
        return df
    parsed = pd.to_datetime(df[ts_column], errors="coerce", utc=True)
    unparseable = int(parsed.isna().sum())
    if unparseable:
        logger.warning(f"{label} retention: {unparseable} row(s) have an unparseable {ts_column}; keeping them")
    retained = parsed.isna() | (parsed >= cutoff)
    pruned = int((~retained).sum())
    logger.info(
        f"{label} retention: pruned {pruned} of {len(df)} row(s); {len(df) - pruned} kept (cutoff={cutoff.isoformat()})"
    )
    if not pruned:
        return df
    return df.loc[retained].reset_index(drop=True)


def expired_job_ids(log_df: pd.DataFrame, cutoff: pd.Timestamp | None) -> set[str]:
    """Return job ids whose *every* pre-processing-log row is older than *cutoff*.

    A job with even one recent or unparseable-timestamp row keeps its manifest pages.

    Args:
        log_df: The pre-processing log snapshot loaded for this run.
        cutoff: From :func:`retention_cutoff`; ``None`` expires nothing.

    Returns:
        The set of fully-expired job ids.
    """
    if cutoff is None or log_df.empty or not {_JOB_ID, _AGE_COLUMN}.issubset(log_df.columns):
        return set()
    parsed = pd.to_datetime(log_df[_AGE_COLUMN], errors="coerce", utc=True)
    retained = parsed.isna() | (parsed >= cutoff)
    return set(log_df[_JOB_ID].dropna()) - set(log_df.loc[retained, _JOB_ID].dropna())


def prune_manifest(manifest_df: pd.DataFrame, expired_ids: Iterable[str]) -> pd.DataFrame:
    """Drop page-manifest rows belonging to a fully-expired job.

    Args:
        manifest_df: The merged (existing + new) append-only page-manifest log.
        expired_ids: Job ids from :func:`expired_job_ids`. A ``job_id`` outside this set — including
            one absent from the pre-processing log entirely — is kept.

    Returns:
        The retained rows.
    """
    expired = set(expired_ids)
    if manifest_df.empty or _JOB_ID not in manifest_df.columns:
        return manifest_df
    drop = manifest_df[_JOB_ID].isin(expired)
    pruned = int(drop.sum())
    logger.info(
        f"page-manifest retention: pruned {pruned} of {len(manifest_df)} row(s) "
        f"across {len(expired)} fully-expired job(s)"
    )
    if not pruned:
        return manifest_df
    return manifest_df.loc[~drop].reset_index(drop=True)


def month_file_pattern(prefix: str) -> re.Pattern[str]:
    """Compile the ``<prefix>_YYYYMM.csv`` filename pattern (group 1 is the ``YYYYMM``)."""
    return re.compile(rf"{re.escape(prefix)}_(\d{{6}})\.csv$")


def expired_month_files(file_names: Iterable[str], pattern: re.Pattern[str], cutoff: pd.Timestamp | None) -> list[str]:
    """Return the month-partitioned file names lying entirely before *cutoff*'s month.

    A month-file is deleted only when its whole month predates the cutoff's month, so the current
    month's file is never deleted and no row still inside the window is lost with it.

    Args:
        file_names: Candidate names from the log folder.
        pattern: From :func:`month_file_pattern`.
        cutoff: From :func:`retention_cutoff`; ``None`` expires nothing.

    Returns:
        The names to delete (non-matching names are ignored).
    """
    if cutoff is None:
        return []
    cutoff_month = cutoff.year * 12 + cutoff.month
    expired = []
    for name in file_names:
        match = pattern.search(name or "")
        if match and int(match.group(1)[:4]) * 12 + int(match.group(1)[4:6]) < cutoff_month:
            expired.append(name)
    return expired


def sweep_month_files(
    sp_conn: SharePointModule,
    sp_path: str,
    pattern: re.Pattern[str],
    cutoff: pd.Timestamp | None,
    label: str,
) -> None:
    """Delete sibling month-files of *sp_path* lying entirely before *cutoff*'s month (best-effort).

    A SharePoint failure is logged and swallowed so a hiccup in the retention sweep never fails a
    run whose logs were already written. Skipped entirely when *cutoff* is ``None`` (retention
    disabled) or *sp_path* is not a month-partitioned filename (e.g. a static name).

    Args:
        sp_conn: SharePoint connection owning the log folder.
        sp_path: Path of the month-file just written (its folder is swept).
        pattern: From :func:`month_file_pattern`.
        cutoff: From :func:`retention_cutoff`; ``None`` sweeps nothing.
        label: Human-readable label for log lines.
    """
    if cutoff is None:
        return
    if not pattern.search(sp_path):
        logger.debug(f"{label}: {sp_path} is not month-partitioned; skipping retention sweep")
        return
    folder = sp_path.rsplit("/", 1)[0]
    try:
        names = [item.get("name", "") for item in sp_conn.list_files(folder)]
        candidates = [name for name in names if pattern.search(name or "")]
        expired = expired_month_files(candidates, pattern, cutoff)
        for name in expired:
            sp_conn.delete_item(f"{folder}/{name}")
            logger.info(f"{label}: pruned month-file outside the retention window: {name}")
        logger.info(f"{label} retention sweep: deleted {len(expired)} of {len(candidates)} month-file(s) in {folder}")
    except Exception as exc:
        logger.warning(f"{label}: retention sweep failed for {folder}: {exc}")
