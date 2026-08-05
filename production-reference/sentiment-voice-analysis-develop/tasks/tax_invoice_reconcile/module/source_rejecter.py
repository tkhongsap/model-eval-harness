"""Copy each Suspicious document's exact GCS page into the SharePoint reject folder.

The flagged page already lives in GCS as the page-manifest ``child_path`` — the immutable bytes
Gemini processed and flagged. This module resolves that path from ``(FILE_PATH, PAGE_NO)`` via the
pre-processing log + page manifest, downloads it, and uploads it to the reject folder. The whole
intact original is archived elsewhere (see :class:`SourceArchiver`); the source is never
page-edited.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable

import pandas as pd

from src.modules.google.gcs import GCSModule
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger
from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.tax_invoice_reconcile.helper import output_layout as ol

logger = Logger(__name__)


class SourceRejecter:
    """Routes each Suspicious page's immutable GCS chunk into the reject folder."""

    def __init__(
        self,
        sp_dest: SharePointModule,
        gcs_factory: Callable[[str], GCSModule],
        reject_root: str,
    ) -> None:
        """Initialise with the upload connection, a per-bucket GCS resolver, and the reject root.

        Args:
            sp_dest: SharePoint module the reject page is uploaded through.
            gcs_factory: Maps a bucket name to a :class:`GCSModule` for that bucket (the
                ``child_path`` may live in a different bucket than the task's default).
            reject_root: Fully env-resolved, date-less reject root.
        """
        self._sp = sp_dest
        self._gcs_factory = gcs_factory
        self._reject_root = reject_root

    def reject_suspicious(
        self, final_df: pd.DataFrame, pre_log: pd.DataFrame, manifest: pd.DataFrame, datadate: int
    ) -> None:
        """Copy every Suspicious row's GCS page into the reject folder (best-effort per page)."""
        if not self._reject_root:
            return
        for file_path, child_path in self._resolve_targets(final_df, pre_log, manifest):
            self._copy_page(file_path, child_path, datadate)

    @staticmethod
    def _resolve_targets(final_df: pd.DataFrame, pre_log: pd.DataFrame, manifest: pd.DataFrame) -> list[tuple]:
        """Return ``[(FILE_PATH, child_path)]`` for each Suspicious row via the manifest join.

        Joins ``(FILE_PATH, PAGE_NO)`` → pre-log (``sharepoint_input_path`` → ``gcs_landing_path``,
        ``job_id``) → manifest (``parent_path``, ``job_id``, ``page_no``) to recover the immutable
        GCS ``child_path``. De-duplicated on ``(FILE_PATH, child_path)``.
        """
        if final_df is None or final_df.empty or "STATUS" not in final_df.columns:
            return []
        susp = final_df.loc[final_df["STATUS"] == OCROutputStatus.SUSPICIOUS.value, ["FILE_PATH", "PAGE_NO"]].dropna()
        if susp.empty or pre_log.empty or manifest.empty:
            return []
        pre = pre_log[["sharepoint_input_path", "gcs_landing_path", "job_id"]].dropna().drop_duplicates()
        man = manifest[["parent_path", "job_id", "page_no", "child_path"]].copy()
        susp = susp.assign(PAGE_NO=susp["PAGE_NO"].astype("int64"))
        man = man.assign(page_no=man["page_no"].astype("int64"))
        joined = susp.merge(pre, left_on="FILE_PATH", right_on="sharepoint_input_path", how="inner").merge(
            man,
            left_on=["gcs_landing_path", "job_id", "PAGE_NO"],
            right_on=["parent_path", "job_id", "page_no"],
            how="inner",
        )
        joined = joined.assign(child_path=joined["child_path"].astype("string").fillna("").str.strip())
        joined = joined[joined["child_path"].str.startswith("gs://")].drop_duplicates(["FILE_PATH", "child_path"])
        return list(zip(joined["FILE_PATH"], joined["child_path"], strict=True))

    def _copy_page(self, file_path: str, child_path: str, datadate: int) -> None:
        """Download one page from GCS and upload it to the reject folder (best-effort)."""
        bucket = child_path[len("gs://") :].split("/", 1)[0]
        name = posixpath.basename(child_path)
        dest = ol.reject_dest(self._reject_root, file_path, datadate, name=name)
        try:
            content = self._gcs_factory(bucket).download_file_from_gcs(child_path)
            self._sp.upload_file(dest, content)
            logger.info(f"Suspicious page rejected: {file_path} ({name}) -> {dest}")
        except Exception as exc:
            logger.warning(f"Suspicious reject failed for {file_path} ({child_path} -> {dest}): {exc}")
