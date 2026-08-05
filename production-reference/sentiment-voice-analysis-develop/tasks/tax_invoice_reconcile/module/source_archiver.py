"""Archive the source invoice files and the Z45 source report to SharePoint.

Copies (download + re-upload) each processed source invoice file into ``archive_invoice_path``
under its E-TAX / Paper [Scan] + dated folder, and the Z45 source workbook into
``archive_vat_path``; then deletes the original from the source site once its archive copy has
landed (the input/working folders are cleared so re-runs stay idempotent). Source files may be
PDFs or images; the raw bytes are copied verbatim and the original filename is preserved. A
failure on one file (archive or delete) is logged and swallowed so the rest of the run still
archives; an upload failure skips the delete so an un-archived original is never removed.
"""

from __future__ import annotations

import pandas as pd

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper import output_layout as ol

logger = Logger(__name__)


class SourceArchiver:
    """Copy processed source invoices and the Z45 report into the archive folders."""

    def __init__(
        self,
        sp_source: SharePointModule,
        sp_dest: SharePointModule,
        archive_invoice_root: str,
        archive_vat_root: str,
    ) -> None:
        """Initialise with the source (download) and destination (upload) connections.

        Args:
            sp_source: SharePoint module the originals are read from.
            sp_dest: SharePoint module the archive copies are written to.
            archive_invoice_root: Resolved ``archive_invoice_path`` root.
            archive_vat_root: Resolved ``archive_vat_path`` root.
        """
        self._src = sp_source
        self._dest = sp_dest
        self._invoice_root = archive_invoice_root
        self._vat_root = archive_vat_root

    def archive_invoices(self, processing_df: pd.DataFrame, datadate: int) -> None:
        """Archive each distinct processed source invoice file.

        Args:
            processing_df: Per-document frame whose ``FILE_PATH`` names the originals.
            datadate: The run's ``DATADATE`` value (``YYYYMMDD``) driving the dated folder.
        """
        for file_path in sorted(p for p in processing_df["FILE_PATH"].dropna().unique()):
            try:
                dest = ol.archive_invoice_dest(self._invoice_root, file_path, datadate)
            except ValueError as exc:
                logger.warning(f"Skipping archive for {file_path}: {exc}")
                continue
            self._copy(file_path, dest, "invoice")

    def archive_z45(self, z45_source_path: str, datadate: int) -> None:
        """Archive the Z45 source workbook (original filename preserved)."""
        if not z45_source_path:
            logger.warning("No Z45 source path available; skipping Z45 archive.")
            return
        dest = ol.archive_vat_dest(self._vat_root, z45_source_path, datadate)
        self._copy(z45_source_path, dest, "Z45 report")

    def _copy(self, src_path: str, dest_path: str, label: str) -> None:
        """Copy *src_path* to *dest_path*, then delete the source on a successful copy."""
        try:
            content = self._src.get_item_by_path(item_path=src_path).content
            self._dest.upload_file(dest_path, content)
            logger.info(f"Archived {label}: {src_path} -> {dest_path}")
        except Exception as exc:
            logger.warning(f"Archive failed for {label} {src_path} -> {dest_path}: {exc}")
            return  # upload failed → do NOT delete the un-archived source
        self._delete_source(src_path, label)

    def _delete_source(self, src_path: str, label: str) -> None:
        """Delete the archived original from the source site (best-effort; copy is kept)."""
        try:
            self._src.delete_item(src_path)
            logger.info(f"Deleted archived source {label}: {src_path}")
        except Exception as exc:
            logger.warning(f"Source delete failed for {label} {src_path} (archive copy kept): {exc}")
