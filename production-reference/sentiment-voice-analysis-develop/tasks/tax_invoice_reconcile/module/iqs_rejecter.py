"""Reject-folder handler for the tax-invoice reconcile package.

Moves files and pages rejected in pre-processing — either all pages failed IQS, or the
file's extension is unsupported — from the SharePoint source into the reject folder,
using the per-run stamped logs (pre-processing + page-manifest) as the source of truth.
Reject layout is date-first, E-TAX subfolder aware (see :mod:`.helper.output_layout`).
"""

from __future__ import annotations

from pathlib import PurePosixPath

import pandas as pd

import tasks.tax_invoice_reconcile.helper.output_layout as ol
from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger
from src.utils.pdf_utils import extract_single_page
from tasks.ocr_tax_invoice_pipeline.helper.constant import JobStatus, QualityStatus

logger = Logger(__name__)


class IqsRejecter:
    """Routes pages/files rejected in pre-processing into the SharePoint reject folder.

    Reads reject decisions from the stamped pre-processing and page-manifest logs
    (written by ``OCRSubmitTask``) and moves or copies pages/files into the reject tree.
    PARTIAL files keep their source intact; only the bad pages are split and uploaded.
    Whole-file REJECTED covers both an all-pages-failed-IQS file and an unsupported-extension
    file — the pre-processing-log ``message`` column distinguishes them.
    """

    def __init__(self, sp_source: SharePointModule, reject_root: str) -> None:
        """Initialise with the source-site SharePoint module and the resolved reject root.

        Args:
            sp_source: SharePoint module bound to the source site.
            reject_root: Fully env-resolved, date-less reject root (no placeholders left).
        """
        self._sp = sp_source
        self._root = reject_root

    def reject(
        self,
        pre_run_df: pd.DataFrame,
        manifest_run_df: pd.DataFrame,
        datadate: int,
    ) -> None:
        """Route all IQS rejects from this run to the reject folder.

        Args:
            pre_run_df: Pre-processing log rows filtered to this run's ``job_id``.
            manifest_run_df: Page-manifest log rows filtered to this run's ``job_id``.
            datadate: Run date as ``YYYYMMDD`` int (e.g. 20260622).
        """
        if pre_run_df.empty:
            return
        self._reject_whole_files(pre_run_df, datadate)
        self._reject_partial_pages(pre_run_df, manifest_run_df, datadate)

    def _reject_whole_files(self, pre_run_df: pd.DataFrame, datadate: int) -> None:
        """Copy-then-delete fully REJECTED files (rejected in pre-processing: failed IQS or unsupported file type)."""
        rejected = pre_run_df[pre_run_df["status"] == JobStatus.REJECTED.value]
        for sp_path in rejected["sharepoint_input_path"].unique():
            try:
                self._move_file(sp_path, datadate)
            except Exception as exc:
                logger.warning(f"Failed to move rejected file {sp_path}: {exc}")

    def _move_file(self, sp_path: str, datadate: int) -> None:
        """Copy a whole file to the reject folder, then delete the source."""
        dest = ol.reject_dest(self._root, sp_path, datadate)
        if not self._sp.copy_file(sp_path, dest):
            logger.warning(f"Reject copy failed; leaving source in place: {sp_path}")
            return
        try:
            self._sp.delete_item(sp_path)
        except Exception as exc:
            logger.warning(f"Reject copy ok but source delete failed {sp_path}: {exc}")

    def _reject_partial_pages(
        self,
        pre_run_df: pd.DataFrame,
        manifest_run_df: pd.DataFrame,
        datadate: int,
    ) -> None:
        """Extract and upload bad pages from PARTIAL files (source left in place)."""
        if manifest_run_df.empty:
            return
        partial_df = pre_run_df[pre_run_df["status"] == JobStatus.PARTIAL.value]
        if partial_df.empty:
            return
        bad_pages = self._resolve_partial_pages(partial_df, manifest_run_df)
        for sp_path, pages_df in bad_pages.groupby("sharepoint_input_path"):
            try:
                self._upload_page_splits(sp_path, pages_df, datadate)
            except Exception as exc:
                logger.warning(f"Failed to process partial rejects for {sp_path}: {exc}")

    @staticmethod
    def _resolve_partial_pages(
        partial_df: pd.DataFrame,
        manifest_run_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Join manifest rejected pages to their PARTIAL source SharePoint paths.

        Merges on ``manifest.parent_path == pre_log.gcs_landing_path`` (both are full
        ``gs://`` URIs set by ``PageProcessor`` and ``PreLogRowBuilder`` respectively).

        Returns:
            Frame with ``sharepoint_input_path`` and ``page_no`` columns.
        """
        rejected_pages = manifest_run_df[manifest_run_df["quality_status"] == QualityStatus.REJECTED.value]
        joined = rejected_pages.merge(
            partial_df[["gcs_landing_path", "sharepoint_input_path"]].drop_duplicates(),
            left_on="parent_path",
            right_on="gcs_landing_path",
            how="inner",
        )
        return joined[["sharepoint_input_path", "page_no"]].drop_duplicates()

    def _upload_page_splits(self, sp_path: str, pages_df: pd.DataFrame, datadate: int) -> None:
        """Download source once, extract each bad page, and upload to the reject folder."""
        content = self._sp.get_item_by_path(item_path=sp_path).content
        stem = PurePosixPath(sp_path).stem
        for page_no in sorted(pages_df["page_no"].astype(int)):
            page_bytes = extract_single_page(content, page_no - 1)
            name = f"{stem}_p{page_no:03d}.pdf"
            dest = ol.reject_dest(self._root, sp_path, datadate, name=name)
            try:
                self._sp.upload_file(dest, page_bytes)
                logger.info(f"Rejected page uploaded: {dest}")
            except Exception as exc:
                logger.warning(f"Rejected page upload failed {dest}: {exc}")
