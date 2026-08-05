"""Reconcile the extraction report against the SAP ZAPRPT45 (Z45) report.

Matching follows the per-document VAT spec as **six scenarios**, chosen per extraction row (one row
per receipt x invoice-reference) from three facts — whether the row is a copy / issue-flagged,
whether the per-invoice ``VAT_INVOICE`` is present, and whether the vendor is in the Master Vendor
list. The matching **engine** — the macros, the ``scenario_mapping`` assignment, and the five
candidate-grain ``scen_one`` .. ``scen_five`` views — lives in the sibling
[reconciliation.sql](reconciliation.sql) file, the single source of truth a developer reads/edits
per scenario. This builder is a thin loader: it registers the frames, executes that script to create
the views, then runs the **presentation** SELECTs below (the 37-column Output Report and the enriched
Z45 status) over them. Presentation stays in Python so the statuses/remark texts keep coming from
:mod:`helper.messages` as DuckDB bind parameters (the engine is intentionally paramless, because
DuckDB rejects bind params inside ``CREATE VIEW``).

A non-copy row is **mapped** only when every key aligns **and** the VAT verifies; on a VAT mismatch
the row is ``Incompleted`` and the Z45 fields stay blank. Per-key ``Remark_Mapping`` reasons come
from the per-candidate ``K_*`` flags the engine exposes. The enriched Z45's ``Mapping Tax Invoice
Status`` is tri-state — Completed (mapped), Incompleted (keys matched, VAT off), or blank — derived
from the same per-scenario candidate views; a Completed line also takes the mapped row's Tax Invoice
Number, the reconciled value winning over the source cell. Tax ID / Branch are never reconciled: they
are returned from the Z45 source input verbatim (no mapping, no transformation — the user's data is
trusted). Every Output Report column is rendered as text so ``'No'`` defaults can share columns with
numbers/dates.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.utils.duckdb_utils import connect_decimal_safe
from src.utils.logger import Logger
from tasks.tax_invoice_reconcile.helper.constant import ExtractionStatus, MappingZ45Status
from tasks.tax_invoice_reconcile.helper.messages import EXTRACTION_REVIEW_REMARK, MappingZ45Message
from tasks.tax_invoice_reconcile.schema.report_output import ReportOutput
from tasks.tax_invoice_reconcile.schema.z45_output import Z45_OUTPUT_HEADERS, Z45Output

logger = Logger(__name__)

# The matching engine (macros + scenario_mapping + scen_one..five candidate views). Read once at
# import; executed per build() against a fresh connection so the views/macros never leak between runs.
_ENGINE_SQL = (Path(__file__).with_name("reconciliation.sql")).read_text(encoding="utf-8")


class ReconciliationBuilder:
    """Join the extraction report to Z45, validate/map per the spec, emit the outputs + link."""

    # Ordered scenario view names (scenario 0 is handled separately — it has no Z45 join).
    _SCEN_NAMES: tuple[str, ...] = ("scen_one", "scen_two", "scen_three", "scen_four", "scen_five")

    def build(
        self, extraction_df: pd.DataFrame, z45_df: pd.DataFrame, master_vendor_df: pd.DataFrame
    ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """Return ``(report_df, z45_enriched_df, z45_link_df)``.

        Args:
            extraction_df: Per-document extraction report (``ExtractionProcessing``).
            z45_df: Validated/typed Z45 frame (canonical field names; may carry an
                extra ``path_file`` column, which is ignored).
            master_vendor_df: Master vendor data frame for the in-master split and vendor matching.

        Returns:
            The Output Report (``ReportOutput``); the enriched Z45 (``Z45Output``, in source-row
            order so its positional index equals the source line); and the Z45↔document link
            (columns ``_z_id`` int / ``file_name`` str) — one row per (Z45 line, document) pair
            whose scenario keys all matched, i.e. exactly the lines the tri-state status marks
            Completed or Incompleted. The exporter slices each document's VAT workbook with it.
        """
        con = connect_decimal_safe()
        con.register("extraction", extraction_df)
        # A stable per-row id lets the enriched-Z45 status join back to the exact source line
        # (row_number() OVER () is not guaranteed identical across separate executions).
        z45_keyed = z45_df.copy()
        z45_keyed["_z_id"] = range(len(z45_keyed))
        con.register("z45", z45_keyed)
        con.register("master_vendor", master_vendor_df)

        # Create the macros + candidate views (paramless), then read them from the presentation SELECTs.
        con.execute(_ENGINE_SQL)
        report_fields = con.execute(self._report_sql(), self._report_params()).df()
        z45_status = con.execute(self._z45_status_sql(), self._status_params()).df()
        con.register("z45_status", z45_status)
        z45_fields = con.execute(self._z45_sql()).df()
        z45_link_df = con.execute(self._z45_link_sql()).df()

        report_df = ReportOutput.validate(self._to_aliased(report_fields))
        z45_enriched_df = self._finalize_z45(z45_fields)
        return report_df, z45_enriched_df, z45_link_df

    @staticmethod
    def _to_aliased(df: pd.DataFrame) -> pd.DataFrame:
        """Rename the report frame's snake_case field columns onto ``ReportOutput``'s alias names."""
        field_names = list(ReportOutput.__annotations__.keys())
        alias_names = list(ReportOutput.to_schema().columns.keys())
        return df.rename(columns=dict(zip(field_names, alias_names, strict=True)))

    @staticmethod
    def _finalize_z45(df: pd.DataFrame) -> pd.DataFrame:
        """Blank-fill the field-name Z45 frame and apply the source export headers.

        Pandera can't validate the duplicate ``Tax Cleari`` headers, so the unique
        field-name frame is validated for completeness/order here, then relabelled with
        :data:`Z45_OUTPUT_HEADERS` (duplicates preserved) for a faithful re-export.
        """
        expected = list(Z45Output.__annotations__.keys())
        if list(df.columns) != expected:
            raise ValueError(f"Z45 output columns mismatch: {list(df.columns)} != {expected}")
        df = df.astype(object).where(df.notna(), "")
        df.columns = Z45_OUTPUT_HEADERS
        return df

    def _report_params(self) -> dict:
        """Bind values for the Output Report query (statuses + per-key remark messages)."""
        return {
            "status_completed": MappingZ45Status.COMPLETED.value,
            "status_incompleted": MappingZ45Status.INCOMPLETED.value,
            "ext_completed": ExtractionStatus.COMPLETED.value,
            "review_remark": EXTRACTION_REVIEW_REMARK,
            "remark_no_match": MappingZ45Message.NO_MATCH_MESSAGE.value,
            "remark_company": MappingZ45Message.COMPANY_CODE_MISMATCH_MESSAGE.value,
            "remark_ref_doc": MappingZ45Message.INVOICE_NUMBER_MISMATCH_MESSAGE.value,
            "remark_vendor": MappingZ45Message.VENDOR_NAME_MISMATCH_MESSAGE.value,
            "remark_payment_date": MappingZ45Message.PAYMENT_DATE_MISMATCH_MESSAGE.value,
            "remark_vat": MappingZ45Message.VAT_AMOUNT_MISMATCH_MESSAGE.value,
            "remark_copy_skip": MappingZ45Message.COPY_NOT_RECONCILED_MESSAGE.value,
            "remark_issue_skip": MappingZ45Message.ISSUE_NOT_RECONCILED_MESSAGE.value,
        }

    @staticmethod
    def _status_params() -> dict:
        """Bind values for the enriched-Z45 tri-state status."""
        return {
            "status_completed": MappingZ45Status.COMPLETED.value,
            "status_incompleted": MappingZ45Status.INCOMPLETED.value,
        }

    # -- report assembly --------------------------------------------------------------------------

    def _report_sql(self) -> str:
        """The Output Report: one representative row per extraction row, all scenarios unioned."""
        branches = [self._report_pick(name) for name in self._SCEN_NAMES]
        branches.append(self._scen_zero_report())
        return "\nUNION ALL\n".join(branches) + "\n;"

    def _report_pick(self, name: str) -> str:
        """Pick the representative Z45 candidate for a scenario, then project the 37 report columns.

        The pick prefers an all-key match, then the candidate that satisfies this scenario's VAT rule
        (``CAND_VAT_OK`` — the value-matching payment document / group / document / line), then best
        vendor similarity (via the engine's ``vendor_sim`` macro), so the enriched Z45 fields come from
        the row that actually verified. ``_z_id`` closes the ordering: fully tied candidates would
        otherwise be picked arbitrarily, making the exported Z45 fields differ between re-runs on
        identical inputs.
        """
        sim_eng = "vendor_sim(VENDOR_NAME_ENG, Z_VENDOR_NAME)"
        sim_th = "vendor_sim(VENDOR_NAME_TH, Z_VENDOR_NAME)"
        return f"""
            SELECT {self._extraction_columns_sql()}
                {self._mapping_tail_sql()}
            FROM {name}
            QUALIFY row_number() OVER (
                PARTITION BY _er_id
                ORDER BY
                    ALLKEYS DESC
                    , CASE WHEN CAND_VAT_OK THEN 0 ELSE 1 END
                    , {sim_eng} DESC
                    , {sim_th} DESC
                    , Z_PAYMENT_DOCUMENT NULLS LAST
                    , _z_id
            ) = 1
        """

    def _scen_zero_report(self) -> str:
        """Scenario 0 (copy / issue-flagged): the extraction columns with a blank Z45/mapping tail."""
        return f"""
            SELECT {self._extraction_columns_sql()}
                {self._blank_tail_sql()}
            FROM scenario_mapping
            WHERE SCENARIO = 0
        """

    @staticmethod
    def _extraction_columns_sql() -> str:
        """The 30 extraction-derived report columns, identical for every scenario branch."""
        return """FILE_NAME AS file_name
                , DOC_NAME AS document_name
                , BUYER_NAME_TH AS buyer_name_th
                , BUYER_ADDRESS_TH AS buyer_address_th
                , BUYER_NAME_ENG AS buyer_name_eng
                , BUYER_ADDRESS_ENG AS buyer_address_eng
                , BUYER_TAX_ID AS buyer_tax_id
                , CAST(BUYER_BRANCH_CODE AS VARCHAR) AS buyer_branch_code
                , CAST(BUYER_BRANCH_NAME AS VARCHAR) AS buyer_branch_name
                , VENDOR_NAME_TH AS vendor_name_th
                , VENDOR_ADDRESS_TH AS vendor_address_th
                , VENDOR_NAME_ENG AS vendor_name_eng
                , VENDOR_ADDRESS_ENG AS vendor_address_eng
                , VENDOR_TAX_ID AS vendor_tax_id
                , CAST(VENDOR_BRANCH_CODE AS VARCHAR) AS vendor_branch_code
                , CAST(VENDOR_BRANCH_NAME AS VARCHAR) AS vendor_branch_name
                , TAX_INVOICE_NUMBER AS tax_invoice_number
                , strftime(TAX_INVOICE_DATE, '%d/%m/%Y') AS tax_invoice_date
                , CAST(TOTAL_AMOUNT AS VARCHAR) AS total_amount
                , CAST(VAT_AMOUNT AS VARCHAR) AS vat
                , CAST(NET_AMOUNT AS VARCHAR) AS net_amount
                , CASE WHEN COPY IS TRUE THEN 'Yes' ELSE 'No' END AS copy
                , CASE WHEN RECEIVER_SIGNATURE IS TRUE THEN 'Yes' ELSE 'No' END AS receiver_signature
                , CAST(WITHHOLDING_TAX AS VARCHAR) AS withholding_tax
                , CAST(INVOICE_NUMBER AS VARCHAR) AS invoice_number
                , CAST(INVOICE_AMOUNT AS VARCHAR) AS invoice_amount
                , CAST(VAT_INVOICE AS VARCHAR) AS vat_invoice
                , CASE WHEN STAMP IS TRUE THEN 'Yes' ELSE 'No' END AS stamp
                , DOC_STATUS AS ai_extract_result
                -- CAST guards an all-NULL REMARK column (DuckDB types it INTEGER, so trim() would fail).
                , CASE WHEN DOC_STATUS <> $ext_completed AND (REMARK IS NULL OR trim(CAST(REMARK AS VARCHAR)) = '')
                       THEN $review_remark
                       ELSE NULLIF(CAST(REMARK AS VARCHAR), '') END AS remark_ai_extract"""

    @staticmethod
    def _mapping_tail_sql() -> str:
        """The 7 reconciliation columns for scenarios 1-5 (Fn-4 fields + status + per-key remark)."""
        return """, CASE WHEN ER_MAPPED THEN CAST(Z_INVOICE_DOCUMENT AS VARCHAR) END AS invoice_document
                , CASE WHEN ER_MAPPED THEN CAST(Z_PAYMENT_DOCUMENT AS VARCHAR) END AS payment_document
                , CASE WHEN ER_MAPPED THEN strftime(Z_PAYMENT_DATE, '%d.%m.%Y') END AS payment_date
                , CASE WHEN ER_MAPPED THEN CAST(Z_VENDOR_CODE AS VARCHAR) END AS vendor_code
                , '' AS send_date -- Left blank for the user to fill in manually
                , CASE WHEN ER_MAPPED THEN $status_completed
                       ELSE $status_incompleted END AS mapping_status
                , CASE WHEN ER_MAPPED THEN NULL
                       ELSE NULLIF(concat_ws(', '
                            , CASE WHEN NOT HAS_CANDIDATE THEN $remark_no_match END
                            , CASE WHEN HAS_CANDIDATE AND NOT K_COMPANY THEN $remark_company END
                            , CASE WHEN HAS_CANDIDATE AND SCENARIO IN (1, 3) AND NOT K_INVOICE THEN $remark_ref_doc END
                            , CASE WHEN HAS_CANDIDATE AND NOT K_VENDOR THEN $remark_vendor END
                            , CASE WHEN HAS_CANDIDATE AND NOT K_DATE THEN $remark_payment_date END
                            , CASE WHEN ER_MATCHED AND NOT ER_VAT_OK THEN $remark_vat END
                        ), '') END AS remark_mapping"""

    @staticmethod
    def _blank_tail_sql() -> str:
        """The 7 reconciliation columns for scenario 0 — blank Z45 fields + status, explanatory remark.

        Scenario 0 is never reconciled against Z45 (copy / issue-flagged), so ``Mapping_Status`` and
        the Z45 fields stay blank by design. ``Remark_Mapping`` instead carries a generic reason the
        row was skipped — the issue message when the row is issue-flagged (it wins over copy, since
        the data-quality issue is the actionable one), else the copy message. Because scenario 0 is
        ``ISSUE_FLAG OR COPY``, exactly one branch always fires.
        """
        return """, NULL AS invoice_document
                , NULL AS payment_document
                , NULL AS payment_date
                , NULL AS vendor_code
                , '' AS send_date
                , NULL AS mapping_status
                , CASE WHEN ISSUE_FLAG IS TRUE THEN $remark_issue_skip
                       WHEN COPY IS TRUE THEN $remark_copy_skip END AS remark_mapping"""

    # -- enriched Z45 -----------------------------------------------------------------------------

    def _scen_candidates_sql(self) -> str:
        """UNION of every scenario view's candidate rows (the status + link queries both read it)."""
        return "\nUNION ALL\n".join(
            # CAST guards all-NULL frame columns (DuckDB would type them INTEGER), as for REMARK.
            f"""                SELECT _z_id, FILE_NAME, ALLKEYS, ER_MATCHED, ER_MAPPED
                    , CAST(TAX_INVOICE_NUMBER AS VARCHAR) AS tax_invoice_number
                FROM {name}"""
            for name in self._SCEN_NAMES
        )

    def _z45_status_sql(self) -> str:
        """Per Z45 line: Completed if it fed a mapped row, Incompleted if keys matched but VAT failed.

        A Completed line also carries the mapped extraction row's tax-invoice number(s). The
        ``FILTER`` predicate is the same ``ALLKEYS AND ER_MAPPED`` that drives Completed, so the
        value stays NULL otherwise; grouping stays on ``_z_id`` alone because a candidate-grain view
        pairs one Z45 line with many extraction rows (grouping by the keys would emit one row per
        candidate combo and fan out the ``_z_id`` join). A single Z45 line can legitimately map more
        than one distinct tax-invoice number — structurally so in scenario 5, where one payment
        document reconciles a whole ``(date, buyer, vendor)`` group of documents via ``EXT_TOTAL_VAT``
        — so the mapped numbers are de-duplicated, sorted (for run-to-run determinism), and joined
        with ``', '`` rather than collapsed with ``MAX`` (which would silently drop all but one).
        Single-valued lines (scenarios 1-4 in the normal case) join back to their one number.
        """
        return f"""
            SELECT _z_id
                , CASE WHEN BOOL_OR(ALLKEYS AND ER_MAPPED) THEN $status_completed
                       WHEN BOOL_OR(ALLKEYS AND ER_MATCHED) THEN $status_incompleted
                       ELSE '' END AS mapping_tax_invoice_status
                , array_to_string(
                      list_sort(list_distinct(
                          array_agg(tax_invoice_number) FILTER (
                              WHERE ALLKEYS AND ER_MAPPED
                              AND tax_invoice_number IS NOT NULL AND tax_invoice_number <> ''
                          )
                      )), ', '
                  ) AS tax_invoice_number
            FROM (
{self._scen_candidates_sql()}
            )
            WHERE _z_id IS NOT NULL
            GROUP BY _z_id
            ;
        """

    def _z45_link_sql(self) -> str:
        """One row per (Z45 line, document) whose scenario keys all matched.

        ``ALLKEYS`` on a candidate implies ``ER_MATCHED``, so the linked ``_z_id``s are exactly
        the lines the tri-state status marks Completed or Incompleted — attributed to the
        document (``FILE_NAME``) that matched them. This is the authoritative attribution the
        exporter uses to fill each document's VAT workbook; matching by invoice number instead
        would silently drop documents that reconcile without line-item refs (scen 2/4/5).
        """
        return f"""
            SELECT DISTINCT _z_id, FILE_NAME AS file_name
            FROM (
{self._scen_candidates_sql()}
            )
            WHERE _z_id IS NOT NULL AND ALLKEYS
            ORDER BY _z_id, file_name
            ;
        """

    def _z45_sql(self) -> str:
        """Re-export the source Z45 with the tri-state status and mapped Tax Invoice Number joined by ``_z_id``.

        Only ``tax_invoice_number`` is reconciled — the mapped value wins and the source cell is kept
        otherwise, so a re-run never erases what SAP/manual work already filled in. ``tax_id`` and
        ``branch_code`` are returned from the Z45 source input unchanged (no mapping, no transformation —
        the user's data is trusted). ``ORDER BY _z_id`` keeps the frame in source-row order, so its
        positional index doubles as the ``_z_id`` the link frame refers to.
        """
        return """
            SELECT z.company AS company
                , z.ref_doc_inv AS ref_doc_inv
                , z.doc_type AS doc_type
                , z.invoice_document AS invoice_document
                , z.vendor_code AS vendor_code
                , z.vendor_name AS vendor_name
                , CAST(z.vat_amount AS VARCHAR) AS vat_amount
                , CAST(z.tax_base_amount AS VARCHAR) AS tax_base_amount
                , z.payment_document AS payment_document
                , z.payment_method AS payment_method
                , z.short_text AS short_text
                , z.encashment AS encashment
                , strftime(z.payment_date, '%d.%m.%Y') AS payment_date
                , z.cheque_no AS cheque_no
                , z.payee_name AS payee_name
                , z.doc_header_text AS doc_header_text
                , z.document_currency AS document_currency
                , CAST(z.net_paid AS VARCHAR) AS net_paid
                , z.cost_center AS cost_center
                , z.tax_code AS tax_code
                , z.tax_clearing_doc AS tax_clearing_doc
                , strftime(z.tax_clearing_date, '%d.%m.%Y') AS tax_clearing_date
                , z.tax_id AS tax_id
                , z.branch_code AS branch_code
                , z.email_requester AS email_requester
                , COALESCE(s.tax_invoice_number, z.tax_invoice_number) AS tax_invoice_number
                , z.check_duplicate AS check_duplicate
                , strftime(z.send_date, '%d.%m.%Y') AS send_date
                , z.aging AS aging
                , z.pending_for_release AS pending_for_release
                , z.vat_status AS vat_status
                , z.clearing_doc AS clearing_doc
                , strftime(z.process_date, '%d.%m.%Y') AS process_date
                , z.remark AS remark
                , z."user" AS "user"
                , z.user_outward AS user_outward
                , z.department AS department
                , COALESCE(s.mapping_tax_invoice_status, '') AS mapping_tax_invoice_status
            FROM z45 z
            LEFT JOIN z45_status s ON z._z_id = s._z_id
            ORDER BY z._z_id
            ;
        """
