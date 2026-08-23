"""Monitoring primitives over a Vertex batch run: token usage, latency, success rate.

Pure functions over plain values and DataFrames: no I/O, no logging, no SDK client, so the
monitoring paths in the google pipelines can be tested offline without GCS or Vertex. Split
from :mod:`src.google_model.metrics` rather than added to it because that module's contract
is explicitly *no pandas*.
"""

from __future__ import annotations

# Library imports
from datetime import datetime
from typing import Any

import pandas as pd
from google.genai.types import GenerateContentResponseUsageMetadata, ModalityTokenCount
from pydantic import ValidationError

STATUS_SUCCESS = "Success"
STATUS_FAILED = "Failed"

# {modality: summary key} for the prompt-side breakdown -- ``MediaModality``'s members, minus
# MODALITY_UNSPECIFIED which folds into "other" with anything the SDK does not recognise.
# One column per modality because they are billed at different rates; every pipeline gets every
# column and leaves the modalities it never sends blank.
PROMPT_MODALITY_KEYS = {
    "TEXT": "prompt_text_tokens",
    "AUDIO": "prompt_audio_tokens",
    "IMAGE": "prompt_image_tokens",
    "VIDEO": "prompt_video_tokens",
    "DOCUMENT": "prompt_document_tokens",
}

# {summary key: sheet header} -- one mapping, iterated in this order everywhere, so the totals,
# the log record and the sheet cannot drift. The six prompt-side entries sum to Prompt Tokens on
# any fully-reported row; "other" keeps that identity when a new modality arrives.
USAGE_TOTALS = {
    "prompt_tokens": "Prompt Tokens",
    "prompt_text_tokens": "Prompt Text Tokens",
    "prompt_audio_tokens": "Prompt Audio Tokens",
    "prompt_image_tokens": "Prompt Image Tokens",
    "prompt_video_tokens": "Prompt Video Tokens",
    "prompt_document_tokens": "Prompt Document Tokens",
    "prompt_other_tokens": "Prompt Other Tokens",
    "cached_tokens": "Cached Tokens",
    "candidates_tokens": "Candidates Tokens",
    "thoughts_tokens": "Thoughts Tokens",
    "total_tokens": "Total Tokens",
}

# The keys extract_usage returns, and therefore the keys build_metrics_df expects on every row.
# The token keys are USAGE_TOTALS'; these two are carried alongside them but are not totals.
USAGE_FIELDS = (*USAGE_TOTALS, "prompt_modalities", "traffic_type")


def plan_row_order(
    submitted: list[str], parsed: dict[str, Any]
) -> tuple[list[str], dict[str, int]]:
    """Decide which names get a sheet row, in which order, and count every disagreement.

    The submitted list is the source of truth: a file the model failed on still gets a blank
    row, because the scorers join with ``how="inner"`` and a dropped row would quietly reduce N
    on every metric. Submit 300, ship 300. Returns counts rather than logging them, so this
    module keeps its no-logging contract.

    Returns:
      ``(order, counts)``. ``order`` is the submitted names sorted, then any parsed-but-never-
      submitted name appended -- an unexplained row is a fault to look at. ``counts`` carries
      ``duplicates``, ``missing`` and ``extra``.
    """
    # Order-preserving dedupe, then sorted: output rows come back in no guaranteed order, and
    # sorting the *submitted* list keeps numbering stable as the failure set changes.
    ordered = sorted(dict.fromkeys(submitted))
    extra = sorted(set(parsed) - set(ordered))
    counts = {
        "duplicates": len(submitted) - len(ordered),
        "missing": sum(1 for name in ordered if name not in parsed),
        "extra": len(extra),
    }
    return [*ordered, *extra], counts


def _modality_tokens(details: list[ModalityTokenCount] | None) -> dict[str, int]:
    """Sum a token-details list into ``{modality: tokens}``.

    Summed rather than indexed: nothing says a modality appears at most once. Unknown modalities
    keep their own key -- google-genai mints a new enum member rather than raising.

    Returns:
      Modality name (the enum's ``.value``) to summed token count. Entries with no modality or
      no count are skipped rather than counted as zero.
    """
    totals: dict[str, int] = {}
    for detail in details or []:
        if detail.modality is None or detail.token_count is None:
            continue
        # .value, not the member: MediaModality subclasses str but Enum overrides __str__, so
        # the member renders as "MediaModality.AUDIO" in a cell or a log.
        key = detail.modality.value
        totals[key] = totals.get(key, 0) + detail.token_count
    return totals


def extract_usage(item: dict[str, Any]) -> dict[str, Any]:
    """Read one prediction row's ``usageMetadata`` into flat, sheet-ready values.

    **Never raises** -- it is called *outside* the content-parsing ``try`` in ``run()``, and a
    counter the SDK model rejects must not cost a good response its row in the output sheet.
    A row with no usage and a row with an unreadable one return the same all-None dict.

    Args:
      item: One decoded ``predictions.jsonl`` row.

    Returns:
      The keys in :data:`USAGE_FIELDS`, as plain JSON types only -- ints, strs and None.
    """
    empty: dict[str, Any] = dict.fromkeys(USAGE_FIELDS)

    raw = (item.get("response") or {}).get("usageMetadata") or {}
    if not raw:
        return empty

    try:
        # google-genai's BaseModel accepts the JSONL's camelCase keys as-is, and every field is
        # Optional -- only a wrongly *typed* value raises, which is what this guard is for.
        usage = GenerateContentResponseUsageMetadata.model_validate(raw)
    except ValidationError:
        return empty

    prompt_by_modality = _modality_tokens(usage.prompt_tokens_details)
    # MODALITY_UNSPECIFIED plus anything unrecognised, folded into one column so the prompt-side
    # figures still add up to Prompt Tokens when a modality PROMPT_MODALITY_KEYS predates arrives.
    other = sum(
        tokens
        for modality, tokens in prompt_by_modality.items()
        if modality not in PROMPT_MODALITY_KEYS
    )
    return {
        "prompt_tokens": usage.prompt_token_count,
        # None, not 0, when a modality is absent: only the modalities the API listed get a
        # number, since a 0 is indistinguishable in a sheet from "reported as zero".
        **{
            key: prompt_by_modality.get(modality)
            for modality, key in PROMPT_MODALITY_KEYS.items()
        },
        "prompt_other_tokens": other or None,
        # Which modalities the prompt actually carried; sorted so a run's rows compare.
        "prompt_modalities": ", ".join(sorted(prompt_by_modality)) or None,
        # candidates_tokens_details is a single TEXT entry for structured output, and
        # cache_tokens_details splits a subset of cached_tokens -- neither earns a column.
        "cached_tokens": usage.cached_content_token_count,
        "candidates_tokens": usage.candidates_token_count,
        "thoughts_tokens": usage.thoughts_token_count,
        # Reported, never recomputed -- a synthesized total could disagree with billing.
        "total_tokens": usage.total_token_count,
        "traffic_type": usage.traffic_type.value if usage.traffic_type else None,
    }


def summarize_usage(metrics_df: pd.DataFrame) -> dict[str, Any]:
    """Aggregate the per-file metrics block into the run-level numbers.

    Totals sum over *every* row, failures included -- a failed row still consumed tokens.
    Averages divide by ``files_with_usage``, not the row count, and every denominator falls
    back to 0.0 on an empty frame rather than raising.

    Args:
      metrics_df: The frame ``build_metrics_df`` returns -- the same numbers the sheet shows.

    Returns:
      Plain Python scalars, ready for both a structlog record and a Metric/Value cell.
    """
    files = len(metrics_df)
    succeeded = int((metrics_df["Status"] == STATUS_SUCCESS).sum())
    # notna(), not "Status == Success": a failed row often carries real usage. This counts the
    # line between "the API reported" and "there was nothing to report".
    with_usage = int(metrics_df["Total Tokens"].notna().sum())

    # int(): an Int64 sum over an all-NA column is a numpy scalar structlog should not see.
    totals = {key: int(metrics_df[column].sum()) for key, column in USAGE_TOTALS.items()}

    return {
        "files": files,
        "succeeded": succeeded,
        "failed": files - succeeded,
        "files_with_usage": with_usage,
        "success_rate": succeeded / files if files else 0.0,
        **totals,
        "avg_total_tokens": totals["total_tokens"] / with_usage if with_usage else 0.0,
        "avg_prompt_tokens": totals["prompt_tokens"] / with_usage if with_usage else 0.0,
        # Sorted and deduplicated across the run; an unexpected modality means a payload did
        # not arrive as intended.
        "prompt_modalities": sorted(
            {
                modality.strip()
                for value in metrics_df["Prompt Modalities"].dropna()
                for modality in str(value).split(",")
                if modality.strip()
            }
        ),
        # At most five values exist; a mixed run (provisioned + on-demand) is what this reveals.
        "traffic_types": sorted(
            str(value) for value in metrics_df["Traffic Type"].dropna().unique()
        ),
    }


def _seconds_between(start: datetime | None, end: datetime | None) -> float | None:
    """Seconds from ``start`` to ``end``, or None if that is not a measurement.

    None for a missing endpoint or a negative result (clock skew is not a duration). The
    TypeError guard covers a naive/aware mismatch, which must not kill a run at the very end
    of a paid batch job.
    """
    if start is None or end is None:
        return None
    try:
        seconds = (end - start).total_seconds()
    except TypeError:
        return None
    return round(seconds, 1) if seconds >= 0 else None


def batch_latency(
    create_time: datetime | None,
    start_time: datetime | None,
    update_time: datetime | None,
) -> dict[str, float | None]:
    """Turn a batch job's timestamps into queue / run / wall seconds.

    ``BatchJob.end_time`` and ``completion_stats`` come back None on Vertex, so the terminal
    timestamp is ``update_time``; ``start_time`` is first entry into ``JOB_STATE_RUNNING``, so
    create -> start is queue time. Takes datetimes rather than a ``types.BatchJob`` so it is
    testable without the SDK.

    Returns:
      ``{"queue_seconds", "run_seconds", "wall_seconds"}``, each a float or None -- a job that
      failed before running has a queue and a wall but no run.
    """
    return {
        "queue_seconds": _seconds_between(create_time, start_time),
        "run_seconds": _seconds_between(start_time, update_time),
        "wall_seconds": _seconds_between(create_time, update_time),
    }


def build_summary_df(
    *,
    run_id: str,
    model: str,
    job_name: str,
    job_state: str,
    counts: dict[str, int],
    usage: dict[str, Any],
    latency: dict[str, float | None],
) -> pd.DataFrame:
    """Render the run-level block: two columns, ``Metric`` and ``Value``.

    A long Metric/Value frame rather than one wide row, matching the dashboard blocks.
    Deliberately not pandera-validated: the Value column mixes types by design.

    Args:
      run_id: The run stamp, identical to the GCS prefix and the SharePoint folder.
      model: The model the batch job ran.
      job_name: The Vertex resource name, the handle for re-pulling results by hand.
      job_state: The terminal state, as ``JobState.value``.
      counts: ``{"source_files", "submitted", "line_errors"}`` -- the stages upstream of the
        per-file block, which the block itself cannot see.
      usage: :func:`summarize_usage`'s return value.
      latency: :func:`batch_latency`'s return value, plus the ``payload_ms`` / ``batch_ms`` /
        ``results_ms`` phase timings measured locally.

    Returns:
      A two-column DataFrame. None values stay None so the cell is blank, never the string "None".
    """
    run_seconds = latency["run_seconds"]
    files = usage["files"]

    rows: list[tuple[str, Any]] = [
        ("Run ID", run_id),
        ("Model", model),
        ("Job Name", job_name),
        ("Job State", job_state),
        # Blank rather than "" when unknown; joined when a run somehow mixed two rate cards.
        ("Traffic Type", ", ".join(usage["traffic_types"]) or None),
        ("Prompt Modalities", ", ".join(usage["prompt_modalities"]) or None),
        ("Source Files", counts["source_files"]),
        ("Submitted Rows", counts["submitted"]),
        ("Prediction Rows", files),
        ("Line Parse Errors", counts["line_errors"]),
        ("Succeeded", usage["succeeded"]),
        ("Failed", usage["failed"]),
        ("Success Rate", round(usage["success_rate"], 4)),
        ("Files With Usage", usage["files_with_usage"]),
        *((header, usage[key]) for key, header in USAGE_TOTALS.items()),
        ("Avg Total Tokens / File", round(usage["avg_total_tokens"], 1)),
        ("Avg Prompt Tokens / File", round(usage["avg_prompt_tokens"], 1)),
        ("Queue Seconds", latency["queue_seconds"]),
        ("Batch Run Seconds", run_seconds),
        ("Batch Wall Seconds", latency["wall_seconds"]),
        ("Seconds / File", round(run_seconds / files, 2) if run_seconds and files else None),
        ("Payload Elapsed (ms)", latency["payload_ms"]),
        ("Batch Elapsed (ms)", latency["batch_ms"]),
        # Measured when the sheet is built, so it excludes the workbook write and the uploads.
        ("Results Elapsed (ms)", latency["results_ms"]),
    ]
    return pd.DataFrame(rows, columns=["Metric", "Value"])
