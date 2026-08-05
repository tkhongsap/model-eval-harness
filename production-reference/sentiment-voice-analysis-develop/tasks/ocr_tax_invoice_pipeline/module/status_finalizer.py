"""Terminal-status finalization — pure rollup/stamp functions (no I/O, no Vertex SDK types)."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import pandas as pd

from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, OCROutputStatus
from tasks.ocr_tax_invoice_pipeline.helper.log_helper import latest_status_per_file

logger = Logger(__name__)

_IN_FLIGHT_STATUSES = (JobStatus.PENDING.value, JobStatus.PARTIAL.value)


def aggregate_file_messages(final_df: pd.DataFrame) -> dict[str, str]:
    """Per-file ``", "``-joined distinct non-empty MESSAGEs from that file's non-SUCCESS rows.

    Lets the terminal pre-processing-log row carry *why* a file is FAILED /
    SUCCESS_WITH_FAILURE (validation reason, IQS reject reason, Suspicious reason + page) instead
    of a blank message.

    Args:
        final_df: The finalized output frame (one row per page/line item; may be empty).

    Returns:
        ``{FILE_PATH: message}`` for files that have at least one non-SUCCESS row with a message.
    """
    if final_df.empty or "FILE_PATH" not in final_df.columns:
        return {}
    failing = final_df[final_df["STATUS"] != OCROutputStatus.SUCCESS.value].dropna(subset=["FILE_PATH"])
    messages: dict[str, str] = {}
    for file_path, group in failing.groupby("FILE_PATH"):
        distinct = [str(m).strip() for m in dict.fromkeys(group["MESSAGE"].dropna()) if str(m).strip()]
        if distinct:
            messages[file_path] = ", ".join(distinct)
    return messages


def rollup_status(page_statuses: set[str]) -> str:
    """Reduce a file's per-page row statuses to one file-level ``JobStatus`` value.

    All pages SUCCESS → ``SUCCESS``; some SUCCESS → ``SUCCESS_WITH_FAILURE``; none → ``FAILED``.
    Only ``OCROutputStatus.SUCCESS`` counts as success; every other terminal row status
    (BLANK, UNSUPPORTED, FAILED) counts as failure.

    Args:
        page_statuses: The set of row STATUS values observed for one file.

    Returns:
        The rolled-up file-level status value.
    """
    success = JobStatus.SUCCESS.value
    if page_statuses == {success}:
        return success
    if success in page_statuses:
        return JobStatus.SUCCESS_WITH_FAILURE.value
    return JobStatus.FAILED.value


def resolve_terminal_statuses(
    final_df: pd.DataFrame,
    pre_processing_log: pd.DataFrame,
    dead_job_names: Iterable[str],
    running_job_names: Iterable[str],
) -> dict[str, str]:
    """Compute the terminal ``JobStatus`` per ``sharepoint_input_path``.

    1. Group ``final_df`` by FILE_PATH and roll up row STATUS values.
    2. Force FAILED for in-flight files whose batch job is in ``dead_job_names`` — a
       fully-accepted file on a dead job emits no ``final_df`` rows at all.
    3. Exclude any file that still has a job in ``running_job_names`` (never stamp a file
       while one of its jobs is still running).

    Args:
        final_df: The finalized output frame (one row per page/line item; may be empty).
        pre_processing_log: Append-only pre-processing log (all-string columns).
        dead_job_names: Names of terminally-failed Vertex jobs this run.
        running_job_names: Names of jobs still running this run.

    Returns:
        ``{sharepoint_input_path: terminal_status}`` for files completed this run.
    """
    statuses: dict[str, str] = {}
    if not final_df.empty and "FILE_PATH" in final_df.columns:
        attributable = final_df.dropna(subset=["FILE_PATH"])
        for file_path, group in attributable.groupby("FILE_PATH"):
            statuses[file_path] = rollup_status(set(group["STATUS"]))
    _force_dead(statuses, pre_processing_log, dead_job_names)
    _exclude_running(statuses, pre_processing_log, running_job_names)
    return statuses


def build_terminal_log_rows(
    file_statuses: dict[str, str],
    pre_processing_log: pd.DataFrame,
    update_dt_iso: str,
    file_messages: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Clone each file's latest pre-log row and stamp the terminal status; idempotent.

    Only files whose CURRENT latest log status is still PENDING/PARTIAL are stamped. Files
    already terminal are skipped silently; paths absent from the log are skipped with a
    WARNING. Safe to call twice.

    Args:
        file_statuses: ``{sharepoint_input_path: terminal_status}`` to stamp.
        pre_processing_log: Append-only pre-processing log.
        update_dt_iso: ISO timestamp to stamp as ``update_dt``.
        file_messages: Optional ``{sharepoint_input_path: message}`` (from
            :func:`aggregate_file_messages`) stamped into the terminal row so a FAILED /
            SUCCESS_WITH_FAILURE file records *why*; ``None`` (blank) for a clean SUCCESS.

    Returns:
        One cloned-and-stamped row per file that is still in-flight (empty list otherwise).
    """
    if not file_statuses or pre_processing_log.empty:
        return []
    file_messages = file_messages or {}
    latest = latest_status_per_file(pre_processing_log).set_index("sharepoint_input_path")
    rows = []
    for file_path, status in file_statuses.items():
        if file_path not in latest.index:
            logger.warning(f"File absent from pre-processing log, skipping terminal stamp: {file_path}")
            continue
        if latest.loc[file_path, "status"] not in _IN_FLIGHT_STATUSES:
            continue  # already terminal — idempotent skip
        row = latest.loc[file_path].to_dict()
        row["sharepoint_input_path"] = file_path
        row["status"] = status
        row["update_dt"] = update_dt_iso
        row["message"] = file_messages.get(file_path)
        rows.append(row)
    return rows


def _force_dead(statuses: dict[str, str], pre_processing_log: pd.DataFrame, dead_job_names: Iterable[str]) -> None:
    """Force FAILED for in-flight files whose batch job died terminally (no final_df rows)."""
    dead = {name for name in dead_job_names if name}
    if not dead or pre_processing_log.empty:
        return
    latest = latest_status_per_file(pre_processing_log)
    hit = latest[latest["batch_inference_job_name"].isin(dead) & latest["status"].isin(_IN_FLIGHT_STATUSES)]
    for file_path in hit["sharepoint_input_path"]:
        statuses[file_path] = JobStatus.FAILED.value


def _exclude_running(
    statuses: dict[str, str], pre_processing_log: pd.DataFrame, running_job_names: Iterable[str]
) -> None:
    """Drop any file whose latest pre-log job is still running (premature to stamp)."""
    running = {name for name in running_job_names if name}
    if not running or pre_processing_log.empty:
        return
    latest = latest_status_per_file(pre_processing_log)
    running_files = set(latest.loc[latest["batch_inference_job_name"].isin(running), "sharepoint_input_path"])
    for file_path in list(statuses):
        if file_path in running_files:
            del statuses[file_path]
