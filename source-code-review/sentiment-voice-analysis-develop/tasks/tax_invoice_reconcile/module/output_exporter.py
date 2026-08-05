"""Route the reconciled frames to per-document Output workbooks on SharePoint.

Each Output-Report row is annotated (via DuckDB) with the destination ``OUTPUT_PATH``
(Extract&Mapping) and ``Z45_OUTPUT_PATH`` (VAT Report) derived from its source path: E-TAX
rows sharing one source folder merge into a single Extract&Mapping + single VAT workbook,
while Paper [Scan] rows stay one-to-one (path from the source filename stem). The dated
folder is the flat ``DATADATE`` token. Each VAT workbook carries the Z45 lines the
reconciliation engine actually matched to that workbook's documents (the builder's
``z45_link_df``, statuses Completed/Incompleted) — attribution is by the engine's match
link, NOT by invoice-number equality, which would silently drop documents that reconcile
without line-item refs. A SharePoint failure on one workbook is logged and swallowed so the
rest of the run still exports.
"""

from __future__ import annotations

import pandas as pd

from src.modules.microsoft.sharepoint import SharePointModule
from src.utils.duckdb_utils import connect_decimal_safe
from src.utils.logger import Logger
from src.utils.pandas_utils import df_to_excel_bytes
from tasks.tax_invoice_reconcile.schema.report_output import ReportOutput
from tasks.tax_invoice_reconcile.schema.z45_output import Z45Output

logger = Logger(__name__)

_MAP_SHEET = "Output Report"
_VAT_SHEET = "VAT Report"
_Z45_REF_FIELD = "ref_doc_inv"
_Z45_VENDOR_FIELD = "vendor_name"
# Row order within each workbook: Extract&Mapping by source file then tax-invoice number;
# the VAT report by vendor name then invoice number (``vendor_name``/``ref_doc_inv``,
# located positionally).
_MAP_SORT_KEYS = ["File Name", "Tax Invoice Number"]
# Canonical Output-Report columns (aliases, in contract order) — used to project the
# Extract&Mapping slice and drop the SQL-added OUTPUT_PATH/Z45_OUTPUT_PATH columns.
_REPORT_COLUMNS = list(ReportOutput.to_schema().columns.keys())

# Derive each report row's destination paths from its source FILE_PATH/DATADATE. See guide § 5.7.
# The shared `{date}/{stem}` core is computed once per file: E-TAX = {company}_{DATADATE}_{user}
# from the source folder name; Paper = the source filename minus its extension.
_ROUTE_SQL = """
    WITH pf AS (
        SELECT DISTINCT FILE_NAME, FILE_PATH, DATADATE
            , DATADATE || '/' || COALESCE(TRIM(SPLIT(SPLIT(FILE_PATH, '/')[-2], '_')[2]), '0000')
                || '_' || DATADATE || '_'
                || COALESCE(TRIM(SPLIT(SPLIT(FILE_PATH, '/')[-2], '_')[1]), 'UNKNOW') AS ETAX_STEM
            , DATADATE || '/' || COALESCE(split_part(FILE_NAME, '.', -2), FILE_NAME) AS PAPER_STEM
        FROM processing_df
    )
    SELECT rdf.*
        , CASE
            WHEN contains(pf.FILE_PATH, 'E-TAX') THEN '/Extract&Mapping_E-TAX/' || pf.ETAX_STEM || '_Output_ETAX.xlsx'
            WHEN contains(pf.FILE_PATH, 'Paper [Scan]')
                THEN '/Extract&Mapping_Paper [Scan]/' || pf.PAPER_STEM || '_Output.xlsx'
            ELSE NULL END AS OUTPUT_PATH
        , CASE
            WHEN contains(pf.FILE_PATH, 'E-TAX') THEN '/VAT Report_E-TAX/' || pf.ETAX_STEM || '_Output_Z45_ETAX.xlsx'
            WHEN contains(pf.FILE_PATH, 'Paper [Scan]')
                THEN '/VAT Report_Paper [Scan]/' || pf.PAPER_STEM || '_Output_Z45.xlsx'
            ELSE NULL END AS Z45_OUTPUT_PATH
    FROM report_df rdf
    LEFT JOIN pf ON rdf."File Name" = pf.FILE_NAME
"""


class OutputExporter:
    """Group reconciled report rows by destination path and upload them as Excel."""

    def __init__(self, sp_dest: SharePointModule, dest_root: str) -> None:
        """Initialise with the destination SharePoint module and resolved output root.

        Args:
            sp_dest: SharePoint module for the destination site.
            dest_root: Fully resolved ``dest_path`` root (no placeholders left).
        """
        self._sp = sp_dest
        self._dest_root = dest_root

    def export(
        self,
        processing_df: pd.DataFrame,
        report_df: pd.DataFrame,
        z45_enriched_df: pd.DataFrame,
        z45_link_df: pd.DataFrame | None = None,
    ) -> None:
        """Write the Extract&Mapping and VAT Report workbooks per destination path.

        Args:
            processing_df: Per-document extraction frame (carries ``FILE_PATH``/``DATADATE``).
            report_df: The aliased Output Report (``ReportOutput``).
            z45_enriched_df: The enriched Z45 report (``Z45_OUTPUT_HEADERS`` columns, rows in
                source order so the positional index equals the builder's ``_z_id``).
            z45_link_df: The builder's Z45↔document match link (``_z_id``/``file_name``) that
                attributes each mapped Z45 line to the document(s) it reconciled with.
        """
        if report_df is None or report_df.empty:
            logger.info("No reconciled rows; skipping output workbook export.")
            return
        if z45_enriched_df is None:
            z45_enriched_df = pd.DataFrame()
        sort_keys = self._z45_sort_keys(z45_enriched_df)
        routed = self._prep_output_path(report_df, processing_df)
        unrouted = int(routed["OUTPUT_PATH"].isna().sum())
        if unrouted:
            logger.warning(f"{unrouted} report row(s) had no resolvable output path; skipping them.")
        for out_path, grp in routed.dropna(subset=["OUTPUT_PATH"]).groupby("OUTPUT_PATH", sort=True):
            self._export_mapping(grp[_REPORT_COLUMNS], out_path)
            self._export_vat(grp, z45_enriched_df, sort_keys, grp["Z45_OUTPUT_PATH"].iloc[0], z45_link_df)

    @staticmethod
    def _prep_output_path(report_df: pd.DataFrame, processing_df: pd.DataFrame) -> pd.DataFrame:
        """Return ``report_df`` with ``OUTPUT_PATH`` and ``Z45_OUTPUT_PATH`` columns added."""
        con = connect_decimal_safe()
        try:
            con.register("report_df", report_df)
            con.register("processing_df", processing_df)
            return con.execute(_ROUTE_SQL).df()
        finally:
            con.close()

    def _export_mapping(self, slice_df: pd.DataFrame, out_path: str) -> None:
        """Upload the merged Extract&Mapping workbook for one output path (ordered)."""
        ordered = slice_df.sort_values(_MAP_SORT_KEYS, kind="stable")
        self._upload(ordered, self._full(out_path), _MAP_SHEET, "Extract&Mapping")

    def _export_vat(
        self,
        grp: pd.DataFrame,
        z45_enriched_df: pd.DataFrame,
        sort_keys: pd.DataFrame | None,
        z45_path: str,
        link: pd.DataFrame | None,
    ) -> None:
        """Upload the per-document VAT workbook: the group's engine-linked Z45 rows, else header-only.

        The rows are the Z45 lines the reconciliation engine matched to this group's documents
        (Completed / Incompleted by construction), sliced positionally via the link's ``_z_id``
        and ordered by ``vendor_name`` then ``ref_doc_inv``. When nothing is linked (or the Z45
        source has no records at all), a **header-only** workbook is written so every document
        still gets a VAT file.
        """
        ids = self._linked_row_ids(grp, link, len(z45_enriched_df))
        if ids and sort_keys is not None:
            # Order by vendor name then the stripped ref_doc_inv (so the VAT report groups by
            # vendor and follows invoice number within it); the trailing position keeps fully
            # tied rows in source order.
            vendor, ref = sort_keys[_Z45_VENDOR_FIELD], sort_keys[_Z45_REF_FIELD]
            order = sorted(ids, key=lambda i: (vendor.iat[i], ref.iat[i], i))
            self._upload(z45_enriched_df.iloc[order], self._full(z45_path), _VAT_SHEET, "VAT Report")
            return
        logger.info(f"No Z45 rows mapped to {z45_path}; writing header-only VAT workbook.")
        self._upload(z45_enriched_df.iloc[0:0], self._full(z45_path), _VAT_SHEET, "VAT Report")

    @staticmethod
    def _linked_row_ids(grp: pd.DataFrame, link: pd.DataFrame | None, n_rows: int) -> list[int]:
        """Return the sorted Z45 row positions the link attributes to this group's documents.

        ``_z_id`` is the enriched frame's positional index (the builder emits it in source-row
        order); out-of-range ids are dropped defensively so a mismatched frame cannot raise.
        """
        if link is None or link.empty or n_rows == 0:
            return []
        files = set(grp["File Name"].dropna())
        ids = link.loc[link["file_name"].isin(files), "_z_id"].astype(int)
        return sorted({i for i in ids if 0 <= i < n_rows})

    @staticmethod
    def _z45_sort_keys(z45_df: pd.DataFrame) -> pd.DataFrame | None:
        """Return the stripped ``vendor_name`` + ``ref_doc_inv`` columns (located by position).

        ``Z45_OUTPUT_HEADERS`` carries duplicate labels (two ``Tax Cleari``), so the columns
        are found by their canonical field index rather than by name.
        """
        if z45_df is None or z45_df.empty:
            return None
        fields = list(Z45Output.__annotations__)
        return pd.DataFrame(
            {
                field: z45_df.iloc[:, fields.index(field)].astype("string").fillna("").str.strip()
                for field in (_Z45_VENDOR_FIELD, _Z45_REF_FIELD)
            }
        )

    def _full(self, path: str) -> str:
        """Prepend the destination root to a site-relative output path."""
        return f"{self._dest_root.rstrip('/')}{path}"

    def _upload(self, df: pd.DataFrame, path: str, sheet: str, label: str) -> None:
        """Serialize *df* to a text-formatted Excel workbook and upload it to *path*."""
        try:
            content = df_to_excel_bytes(df, sheet_name=sheet, text_columns="ALL")
            self._sp.upload_file(path, content)
            logger.info(f"{label} workbook ({len(df)} rows) saved: {path}")
        except Exception as exc:
            logger.warning(f"SharePoint upload failed for {label} at {path}: {exc}")
