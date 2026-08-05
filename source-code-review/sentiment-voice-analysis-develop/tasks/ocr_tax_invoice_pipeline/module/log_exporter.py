"""Log exporter — write pre-processing and page-manifest CSVs to GCS + SharePoint.

GCS writes use optimistic concurrency (``if_generation_match``): the existing object's
generation is read on load and asserted on save, so a concurrent submit/retrieve run that
appended between our read and write loses the precondition and we reload + retry once instead
of silently dropping its rows.

Retention is **intrinsic, not optional**: the exporter is constructed with the run's window and
prunes the merged frame inline, right before the upload — inside the same generation precondition,
and re-applied on the retry attempt. It is disabled only by configuring a negative
``log_retention_days`` (see :mod:`~tasks.ocr_tax_invoice_pipeline.helper.log_retention`).
"""

from __future__ import annotations

import io
from collections.abc import Iterable
from zoneinfo import ZoneInfo

import pandas as pd
from google.api_core.exceptions import PreconditionFailed

from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.log_retention import (
    DEFAULT_RETENTION_DAYS,
    prune_by_timestamp,
    prune_manifest,
    retention_cutoff,
)

logger = Logger(__name__)

_CSV_MIME = "text/csv"
_CSV_ENCODING = "utf-8-sig"  # BOM for Thai character compatibility in Excel
_MAX_WRITE_ATTEMPTS = 2  # one initial write + one retry after a concurrent-write precondition
_PRE_LOG_KEY = "sharepoint_input_path"  # present only in the pre-processing log
_PRE_LOG_TS = "update_dt"  # timestamp column that ages a pre-processing-log row
_MANIFEST_KEY = "job_id"  # present in the page manifest (and the pre-processing log)


class LogExporter:
    """Appends new log rows to existing CSVs, prunes aged rows, and mirrors to SharePoint.

    Both log types (pre-processing and page-manifest) are append-only **within the retention
    window**: existing rows are never modified — each run contributes new rows, and rows that have
    aged past the window are dropped on the next write.
    """

    def __init__(
        self,
        gcs_conn: GCSModule,
        sp_conn: SharePointModule,
        retention_days: int = DEFAULT_RETENTION_DAYS,
        timezone: ZoneInfo | None = None,
    ) -> None:
        """Initialise with injected connections and the run's retention window.

        Args:
            gcs_conn: GCS module pointing at the processing bucket.
            sp_conn: SharePoint control-site module.
            retention_days: Rows older than this are pruned on write. **Negative disables
                retention** — the cutoff becomes ``None`` and nothing is ever pruned.
            timezone: Timezone the cutoff is anchored to (``framework.timezone``); falls back to
                the retention module's default when ``None``.
        """
        self._gcs = gcs_conn
        self._sp = sp_conn
        self._cutoff = retention_cutoff(retention_days, timezone)

    def load_log(self, gcs_path: str) -> pd.DataFrame:
        """Download and parse an existing log CSV (pre-processing or page-manifest) from GCS.

        Args:
            gcs_path: Full GCS URI (``gs://...``) or bucket-relative path.

        Returns:
            Parsed DataFrame, or an empty DataFrame when the file does not
            exist or cannot be parsed.
        """
        return self._load_csv(gcs_path)[0]

    def save_log(
        self,
        new_rows: pd.DataFrame,
        gcs_path: str,
        sp_path: str,
        *,
        label: str = "log",
        sort_by: str | None = None,
        expired_ids: Iterable[str] = (),
    ) -> None:
        """Append *new_rows* to the CSV at *gcs_path*, prune aged rows, and mirror to SharePoint.

        The GCS write is guarded by the existing object's generation and retried once if a
        concurrent run wrote in between — so new rows are never silently dropped.

        Args:
            new_rows: DataFrame of new rows to append (existing rows are never modified).
            gcs_path: Full GCS URI (``gs://...``) for the primary CSV storage.
            sp_path: SharePoint path where the CSV is mirrored.
            label: Human-readable log label (e.g. "pre-processing log") for log lines.
            sort_by: Column to sort the merged log by, latest first (descending). Ignored
                when ``None`` or absent from the merged frame — presentation only.
            expired_ids: Page manifest only. Fully-expired job ids (from ``expired_job_ids``)
                whose pages may be pruned.
        """
        bucket_path = self._strip_gs_prefix(gcs_path)
        csv_bytes = self._append_with_retry(new_rows, gcs_path, bucket_path, sort_by, label, expired_ids)
        logger.info(f"{label} saved to GCS: {gcs_path}")

        try:
            self._sp.upload_file(sp_path, csv_bytes)
            logger.info(f"{label} mirrored to SharePoint: {sp_path}")
        except Exception as exc:
            logger.warning(f"SharePoint mirror failed for {sp_path}: {exc}")

    def _append_with_retry(
        self,
        new_rows: pd.DataFrame,
        gcs_path: str,
        bucket_path: str,
        sort_by: str | None,
        label: str,
        expired_ids: Iterable[str],
    ) -> bytes:
        """Read-merge-prune-write the CSV under a generation precondition; retry once on a race.

        Returns:
            The CSV bytes actually written (for the SharePoint mirror).
        """
        for attempt in range(1, _MAX_WRITE_ATTEMPTS + 1):
            existing, generation = self._load_csv(gcs_path)
            merged = self._merge(existing, new_rows, sort_by)
            merged = self._prune(merged, expired_ids)
            csv_bytes = merged.to_csv(index=False, encoding=_CSV_ENCODING).encode(_CSV_ENCODING)
            # generation None means "no object yet" → require create-only (0) so a concurrent
            # create also trips the precondition and forces a reload+merge.
            precondition = generation if generation is not None else 0
            try:
                self._gcs.update_content_to_gcs(csv_bytes, _CSV_MIME, bucket_path, if_generation_match=precondition)
                return csv_bytes
            except PreconditionFailed:
                if attempt == _MAX_WRITE_ATTEMPTS:
                    logger.error(f"{label}: lost the write race after {attempt} attempt(s); aborting")
                    raise
                logger.warning(f"{label}: concurrent log write detected; reloading and retrying once")
        raise RuntimeError("unreachable: loop always returns or raises")  # pragma: no cover

    def _prune(self, merged: pd.DataFrame, expired_ids: Iterable[str]) -> pd.DataFrame:
        """Apply the retention rule for whichever of the two logs this frame is.

        The dispatch is closed: this exporter writes exactly the pre-processing log (aged by
        ``update_dt``, regardless of status) and the page manifest (which has no timestamp column,
        so it ages out via ``job_id``). A no-op when the cutoff is ``None`` (retention disabled).
        """
        if self._cutoff is None or merged.empty:
            return merged
        if _PRE_LOG_KEY in merged.columns:
            return prune_by_timestamp(merged, self._cutoff, _PRE_LOG_TS, label="pre-processing log")
        if _MANIFEST_KEY in merged.columns:
            return prune_manifest(merged, expired_ids)
        return merged

    @staticmethod
    def _merge(existing: pd.DataFrame, new_rows: pd.DataFrame, sort_by: str | None) -> pd.DataFrame:
        """Concatenate new rows onto existing (append-only) and optionally sort, latest-first."""
        merged = pd.concat([existing, new_rows], ignore_index=True) if not existing.empty else new_rows
        if sort_by and sort_by in merged.columns:
            merged = merged.sort_values(sort_by, ascending=False, kind="stable").reset_index(drop=True)
        return merged

    def _load_csv(self, gcs_path: str) -> tuple[pd.DataFrame, int | None]:
        """Download + parse a CSV from GCS, returning ``(frame, generation)``.

        Returns an empty frame with ``None`` generation when the object is absent or unparseable.
        """
        try:
            raw, generation = self._gcs.download_bytes_with_generation(gcs_path)
            return pd.read_csv(io.BytesIO(raw), dtype=str), generation
        except FileNotFoundError:
            return pd.DataFrame(), None
        except Exception as exc:
            logger.warning(f"Could not load existing CSV from {gcs_path}: {exc}")
            return pd.DataFrame(), None

    def _strip_gs_prefix(self, gcs_uri: str) -> str:
        """Remove ``gs://bucket/`` from a full GCS URI to get the bucket-relative path."""
        if not gcs_uri.startswith("gs://"):
            return gcs_uri
        without_scheme = gcs_uri[len("gs://") :]
        return without_scheme[len(self._gcs.bucket_name) + 1 :]
