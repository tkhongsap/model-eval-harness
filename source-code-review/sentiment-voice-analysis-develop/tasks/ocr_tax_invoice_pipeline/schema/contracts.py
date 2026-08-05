"""Typed hand-off contracts between the OCR-pipeline tasks and business tasks."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ChunkEntry:
    """One accepted page chunk uploaded to the GCS processing path."""

    parent_landing_path: str  # gs:// landing URI of the source document
    gcs_uri: str  # gs:// URI of the per-page chunk


@dataclass(frozen=True)
class BatchSubmission:
    """Outcome of one Vertex batch submission attempt."""

    payload_name: str
    payload_uri: str
    output_uri: str
    parent_paths: frozenset[str]  # landing URIs of files whose pages are in this payload
    job: Any | None = None  # google BatchJob; None when submission failed
    error: str | None = None


@dataclass(frozen=True)
class OCRResult:
    """Typed hand-off from ``OCRRetrieveTask`` through business tasks to ``OCRFinalizeTask``.

    ``frozen=True`` is shallow — the DataFrames themselves are mutable; treat them as read-only.

    Attributes:
        final_df: ``OCROutputSchema``-validated frame, one row per page/line item. May be
            EMPTY when every in-flight job died without predictions.
        file_statuses: Terminal ``JobStatus`` value per ``sharepoint_input_path``. Keys are the
            SharePoint source paths from the pre-processing log — NEVER GCS URIs. Computed once
            in retrieve so finalize never re-polls Vertex.
        pre_processing_log: Append-only pre-processing-log snapshot loaded once in retrieve and
            threaded forward so business tasks (audit logs) and finalize (terminal-status rows)
            read it from here instead of re-reading GCS — one consistent snapshot per run.
            Treat as read-only.
        page_manifest_log: Per-page manifest snapshot loaded once in retrieve and threaded
            forward. Carries each page's immutable GCS ``child_path``, so a business task can
            copy the exact processed page (e.g. a Suspicious page) to the reject folder rather
            than re-deriving it from the source. Treat as read-only.
    """

    final_df: pd.DataFrame
    file_statuses: dict[str, str] = field(default_factory=dict)
    pre_processing_log: pd.DataFrame = field(default_factory=pd.DataFrame)
    page_manifest_log: pd.DataFrame = field(default_factory=pd.DataFrame)
