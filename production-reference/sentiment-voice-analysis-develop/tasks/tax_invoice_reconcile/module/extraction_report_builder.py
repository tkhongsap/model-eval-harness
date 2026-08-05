"""Build the extraction report (the "first evidence" CSV) from OCR output + the Master Buyer.

OCR output is one row per line item; a tax invoice can span several pages and a file
can hold several invoices. This builder collapses line items to one row per resolved
document (multi-page totals taken from the page that carries them), then LEFT JOINs the
Master Buyer on a normalized, zero-padded tax id to attach the SAP company code and
compute buyer name/address similarity. It **folds the Master-Buyer verdict** into
``DOC_STATUS`` (Completed only when OCR succeeded *and* buyer name+address match) and into
``REMARK`` (the OCR message plus any company-code / tax-id / name / address mismatch
reasons). The match-input booleans are intermediates only and are not emitted; the Z45
verdict and the ``Remark_Mapping`` text are assembled downstream in
:class:`ReconciliationBuilder`.
"""

from __future__ import annotations

import pandas as pd

from src.utils.duckdb_utils import connect_decimal_safe
from tasks.ocr_tax_invoice_pipeline.helper.constant import OCROutputStatus
from tasks.tax_invoice_reconcile.helper.constant import ExtractionStatus
from tasks.tax_invoice_reconcile.helper.messages import (
    EXTRACTION_SYSTEM_FAILURE_REMARK,
    MappingMasterMessage,
    RequiredFieldMessage,
    ValidationMessage,
)
from tasks.tax_invoice_reconcile.helper.sql_normalize import norm_taxid_sql, norm_text_sql
from tasks.tax_invoice_reconcile.schema.extraction_processing import ExtractionProcessing


def _sql_literal(message_enum) -> str:
    """Return an enum message as a single-quote-safe SQL string literal body."""
    return message_enum.value.replace("'", "''")


class ExtractionReportBuilder:
    """Aggregate OCR rows to one row per document and enrich with the Master Buyer."""

    BUYER_NAME_MATCH_THRESHOLD = 0.90
    BUYER_ADDRESS_MATCH_THRESHOLD = 0.80

    def build(self, ocr_results_df: pd.DataFrame, master_buyer_df: pd.DataFrame) -> pd.DataFrame:
        """Return the per-document extraction report validated against ``ExtractionProcessing``.

        Args:
            ocr_results_df: OCR output (one row per line item; ``OCROutputSchema``).
            master_buyer_df: Master Buyer with canonical field-name columns.

        Returns:
            One row per resolved document with extracted fields and match inputs.
        """
        con = connect_decimal_safe()
        con.register("ocr_results", ocr_results_df)
        con.register("master_buyer", master_buyer_df)
        result = con.execute(self._sql()).df()
        return ExtractionProcessing.validate(result)

    def _sql(self) -> str:
        """Compose the DuckDB statement for the extraction report."""
        name_th = self.BUYER_NAME_MATCH_THRESHOLD
        addr_th = self.BUYER_ADDRESS_MATCH_THRESHOLD
        norm_buyer_name_th = norm_text_sql("ao.BUYER_NAME_TH")
        norm_buyer_name_eng = norm_text_sql("ao.BUYER_NAME_ENG")
        norm_buyer_addr_th = norm_text_sql("ao.BUYER_ADDRESS_TH")
        norm_buyer_addr_eng = norm_text_sql("ao.BUYER_ADDRESS_ENG")
        norm_mb_name_th = norm_text_sql("mb.company_name_th")
        norm_mb_name_eng = norm_text_sql("mb.company_name_eng")
        norm_mb_addr_th = norm_text_sql("mb.company_address_th")
        norm_mb_addr_eng = norm_text_sql("mb.company_address_eng")
        ocr_taxid = norm_taxid_sql("ao.BUYER_TAX_ID")
        mb_taxid = norm_taxid_sql("mb.tax_id")
        completed = ExtractionStatus.COMPLETED.value
        requires_review = ExtractionStatus.REQUIRES_REVIEW.value
        # OCR statuses that BU field-validation cannot re-derive, so they must be carried
        # through verbatim and force a review (e.g. prompt-injection / unsupported doc type).
        suspicious = OCROutputStatus.SUSPICIOUS.value
        unsupported = OCROutputStatus.UNSUPPORTED.value
        blank = OCROutputStatus.BLANK.value
        # A true system/batch failure (batch job failed, batch line error, no response text). Its real
        # cause stays in the pre-processing log; the report shows a clean business remark instead.
        failed = OCROutputStatus.FAILED.value
        system_failure_remark = EXTRACTION_SYSTEM_FAILURE_REMARK.replace("'", "''")

        # REMARK reasons folded in below, keyed by trimmed enum-member name: required-field
        # (rq), validation-rule (vd), and Master-Buyer mismatch (mm) — the "first evidence".
        rq = {m.name.removesuffix("_MISSING_MESSAGE"): _sql_literal(m) for m in RequiredFieldMessage}
        vd = {m.name.removesuffix("_MESSAGE"): _sql_literal(m) for m in ValidationMessage}
        mm = {m.name.removesuffix("_MESSAGE"): _sql_literal(m) for m in MappingMasterMessage}

        return f"""
            WITH resolved_ocr AS (
                -- Collapse pages to documents (window-only field resolution). See guide § 3.1.
                SELECT MAX(DOC_NAME) FILTER (WHERE DOC_NAME IS NOT NULL) OVER w AS DOC_NAME
                    , MAX(CUSTOMER_NAME_TH) FILTER (WHERE CUSTOMER_NAME_TH IS NOT NULL) OVER w AS BUYER_NAME_TH
                    , MAX(CUSTOMER_ADDRESS_TH) FILTER (WHERE CUSTOMER_ADDRESS_TH IS NOT NULL) OVER w AS BUYER_ADDRESS_TH
                    , MAX(CUSTOMER_NAME_ENG) FILTER (WHERE CUSTOMER_NAME_ENG IS NOT NULL) OVER w AS BUYER_NAME_ENG
                    , MAX(CUSTOMER_ADDRESS_ENG) FILTER (WHERE CUSTOMER_ADDRESS_ENG IS NOT NULL) OVER w AS \
BUYER_ADDRESS_ENG
                    , MAX(CUSTOMER_TAX_ID) FILTER (WHERE CUSTOMER_TAX_ID IS NOT NULL) OVER w AS BUYER_TAX_ID
                    , MAX(CUSTOMER_BRANCH_CODE) FILTER (WHERE CUSTOMER_BRANCH_CODE IS NOT NULL) OVER w AS \
BUYER_BRANCH_CODE
                    , MAX(CUSTOMER_BRANCH_NAME) FILTER (WHERE CUSTOMER_BRANCH_NAME IS NOT NULL) OVER w AS \
BUYER_BRANCH_NAME
                    , MAX(VENDOR_NAME_TH) FILTER (WHERE VENDOR_NAME_TH IS NOT NULL) OVER w AS VENDOR_NAME_TH
                    , MAX(VENDOR_ADDRESS_TH) FILTER (WHERE VENDOR_ADDRESS_TH IS NOT NULL) OVER w AS VENDOR_ADDRESS_TH
                    , MAX(VENDOR_NAME_ENG) FILTER (WHERE VENDOR_NAME_ENG IS NOT NULL) OVER w AS VENDOR_NAME_ENG
                    , MAX(VENDOR_ADDRESS_ENG) FILTER (WHERE VENDOR_ADDRESS_ENG IS NOT NULL) OVER w AS VENDOR_ADDRESS_ENG
                    , MAX(VENDOR_TAX_ID) FILTER (WHERE VENDOR_TAX_ID IS NOT NULL) OVER w AS VENDOR_TAX_ID
                    , MAX(VENDOR_BRANCH_CODE) FILTER (WHERE VENDOR_BRANCH_CODE IS NOT NULL) OVER w AS VENDOR_BRANCH_CODE
                    , MAX(VENDOR_BRANCH_NAME) FILTER (WHERE VENDOR_BRANCH_NAME IS NOT NULL) OVER w AS VENDOR_BRANCH_NAME
                    , TAX_INVOICE_NUMBER
                    , MAX(CAST(TAX_INVOICE_DATE AS DATE)) FILTER (WHERE TAX_INVOICE_DATE IS NOT NULL) OVER w AS \
TAX_INVOICE_DATE
                    , MAX(BEFORE_VAT_AMOUNT) FILTER (WHERE BEFORE_VAT_AMOUNT IS NOT NULL) OVER w AS TOTAL_AMOUNT
                    , MAX(VAT_AMOUNT) FILTER (WHERE BEFORE_VAT_AMOUNT IS NOT NULL) OVER w AS DOC_VAT_AMOUNT
                    , MAX(NET_AMOUNT) FILTER (WHERE BEFORE_VAT_AMOUNT IS NOT NULL) OVER w AS NET_AMOUNT
                    , COPY
                    , COALESCE(BOOL_OR(PAYEE_SIGNATURE_FLAG OR AUTHORIZED_RECEIVER_SIGNATURE_FLAG OR \
AUTHORIZED_SIGNATORY_SIGNATURE_FLAG) OVER w, FALSE) AS RECEIVER_SIGNATURE
                    , MAX(WITHHOLDING_TAX_AMOUNT) FILTER (WHERE BEFORE_VAT_AMOUNT IS NOT NULL) OVER w AS WITHHOLDING_TAX
                    , INVOICE_NUMBER
                    -- INVOICE_AMOUNT: BEFORE/AFTER-VAT fallback. See guide § 3.1.
                    , COALESCE(
                          INVOICE_AMOUNT_BEFORE_VAT
                        , CASE WHEN INVOICE_VAT_AMOUNT IS NULL THEN INVOICE_AMOUNT_AFTER_VAT END
                      ) AS INVOICE_AMOUNT
                    , CASE WHEN (COUNT(TAX_INVOICE_NUMBER) OVER w = 1) AND (DOC_VAT_AMOUNT IS NOT NULL)
                            THEN DOC_VAT_AMOUNT
                            ELSE INVOICE_VAT_AMOUNT END AS VAT_INVOICE
                    , COALESCE(BOOL_OR(STAMP) OVER w, FALSE) AS STAMP
                    , FILE_NAME
                    , FILE_PATH
                    , IQS_SCORE
                    , STATUS
                    , MESSAGE
                    , DATADATE
                FROM ocr_results
                WINDOW w AS (PARTITION BY FILE_PATH, TAX_INVOICE_NUMBER, CUSTOMER_TAX_ID, VENDOR_TAX_ID, DATADATE, COPY)
            ),
            agg_orc AS (
                SELECT DOC_NAME
                    , BUYER_NAME_TH
                    , BUYER_ADDRESS_TH
                    , BUYER_NAME_ENG
                    , BUYER_ADDRESS_ENG
                    , BUYER_TAX_ID
                    , BUYER_BRANCH_CODE
                    , BUYER_BRANCH_NAME
                    , VENDOR_NAME_TH
                    , VENDOR_ADDRESS_TH
                    , VENDOR_NAME_ENG
                    , VENDOR_ADDRESS_ENG
                    , VENDOR_TAX_ID
                    , VENDOR_BRANCH_CODE
                    , VENDOR_BRANCH_NAME
                    , TAX_INVOICE_NUMBER
                    , TAX_INVOICE_DATE
                    , TOTAL_AMOUNT
                    , DOC_VAT_AMOUNT AS VAT_AMOUNT
                    , NET_AMOUNT
                    , COPY
                    , RECEIVER_SIGNATURE
                    , WITHHOLDING_TAX
                    , INVOICE_NUMBER
                    , SUM(CAST(INVOICE_AMOUNT AS DECIMAL(18, 2))) AS INVOICE_AMOUNT
                    , SUM(CAST(VAT_INVOICE AS DECIMAL(18, 2))) AS VAT_INVOICE
                    , STAMP
                    , FILE_NAME
                    , FILE_PATH
                    , AVG(IQS_SCORE) AS IQS_SCORE
                    , BOOL_OR(STATUS = '{suspicious}') AS OCR_SUSPICIOUS
                    , string_agg(DISTINCT CASE WHEN STATUS = '{suspicious}' THEN MESSAGE END, ', ') AS \
OCR_SUSPICIOUS_MESSAGE
                    , BOOL_OR(STATUS = '{unsupported}') AS OCR_UNSUPPORTED
                    , string_agg(DISTINCT CASE WHEN STATUS = '{unsupported}' THEN MESSAGE END, ', ') AS \
OCR_UNSUPPORTED_MESSAGE
                    , BOOL_OR(STATUS = '{blank}') AS OCR_BLANK
                    , string_agg(DISTINCT CASE WHEN STATUS = '{blank}' THEN MESSAGE END, ', ') AS OCR_BLANK_MESSAGE
                    , BOOL_OR(STATUS = '{failed}') AS OCR_FAILED
                    , BOOL_OR(STATUS IN ('{suspicious}', '{unsupported}', '{failed}')) AS OCR_REDACT
                    , BOOL_OR(STATUS IN ('{suspicious}', '{unsupported}', '{failed}', '{blank}')) AS OCR_ISSUE_FLAG
                    -- IS_PIPELINE_ISSUE: segments pipeline-issue rows from invoice rows. See guide § 3.2.
                    , STATUS IN ('{failed}', '{suspicious}', '{unsupported}', '{blank}') AS IS_PIPELINE_ISSUE
                    , DATADATE
                FROM resolved_ocr
                GROUP BY ALL
            ),
            redacted AS (
                SELECT FILE_NAME
                    , FILE_PATH
                    , IQS_SCORE
                    , DOC_NAME
                    , OCR_SUSPICIOUS
                    , OCR_SUSPICIOUS_MESSAGE
                    , OCR_UNSUPPORTED
                    , OCR_UNSUPPORTED_MESSAGE
                    , OCR_BLANK
                    , OCR_ISSUE_FLAG
                    , OCR_BLANK_MESSAGE
                    , OCR_FAILED
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_NAME_TH END AS BUYER_NAME_TH
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_ADDRESS_TH END AS BUYER_ADDRESS_TH
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_NAME_ENG END AS BUYER_NAME_ENG
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_ADDRESS_ENG END AS BUYER_ADDRESS_ENG
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_TAX_ID END AS BUYER_TAX_ID
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_BRANCH_CODE END AS BUYER_BRANCH_CODE
                    , CASE WHEN OCR_REDACT THEN NULL ELSE BUYER_BRANCH_NAME END AS BUYER_BRANCH_NAME
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_NAME_TH END AS VENDOR_NAME_TH
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_ADDRESS_TH END AS VENDOR_ADDRESS_TH
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_NAME_ENG END AS VENDOR_NAME_ENG
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_ADDRESS_ENG END AS VENDOR_ADDRESS_ENG
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_TAX_ID END AS VENDOR_TAX_ID
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_BRANCH_CODE END AS VENDOR_BRANCH_CODE
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VENDOR_BRANCH_NAME END AS VENDOR_BRANCH_NAME
                    , CASE WHEN OCR_REDACT THEN NULL ELSE TAX_INVOICE_NUMBER END AS TAX_INVOICE_NUMBER
                    , CASE WHEN OCR_REDACT THEN NULL ELSE TAX_INVOICE_DATE END AS TAX_INVOICE_DATE
                    , CASE WHEN OCR_REDACT THEN NULL ELSE TOTAL_AMOUNT END AS TOTAL_AMOUNT
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VAT_AMOUNT END AS VAT_AMOUNT
                    , CASE WHEN OCR_REDACT THEN NULL ELSE NET_AMOUNT END AS NET_AMOUNT
                    , CASE WHEN OCR_REDACT THEN FALSE ELSE COPY END AS COPY
                    , CASE WHEN OCR_REDACT THEN FALSE ELSE RECEIVER_SIGNATURE END AS RECEIVER_SIGNATURE
                    , CASE WHEN OCR_REDACT THEN NULL ELSE WITHHOLDING_TAX END AS WITHHOLDING_TAX
                    , CASE WHEN OCR_REDACT THEN NULL ELSE INVOICE_NUMBER END AS INVOICE_NUMBER
                    , CASE WHEN OCR_REDACT THEN NULL ELSE INVOICE_AMOUNT END AS INVOICE_AMOUNT
                    , CASE WHEN OCR_REDACT THEN NULL ELSE VAT_INVOICE END AS VAT_INVOICE
                    , CASE WHEN OCR_REDACT THEN FALSE ELSE STAMP END AS STAMP
                    , DATADATE
                FROM agg_orc
            ),
            master_scored AS (
                SELECT ao.*
                    , mb.com_code_in_sap AS BUYER_COMPANY_CODE
                    , (mb.tax_id IS NOT NULL) AS BUYER_FOUND
                    , mb.company_name_th AS BUYER_NAME_LOOKUP_TH
                    , mb.company_address_th AS BUYER_ADDRESS_LOOKUP_TH
                    , mb.company_name_eng AS BUYER_NAME_LOOKUP_ENG
                    , mb.company_address_eng AS BUYER_ADDRESS_LOOKUP_ENG
                    , (LENGTH({norm_buyer_name_th}) > 0 AND COALESCE(JARO_WINKLER_SIMILARITY({norm_buyer_name_th}, \
{norm_mb_name_th}), 0) >= {name_th})
                        OR (LENGTH({norm_buyer_name_eng}) > 0 AND \
COALESCE(JARO_WINKLER_SIMILARITY({norm_buyer_name_eng}, {norm_mb_name_eng}), 0) >= {name_th}) AS NAME_MATCH
                    , (LENGTH({norm_buyer_addr_th}) > 0 AND COALESCE(JARO_WINKLER_SIMILARITY({norm_buyer_addr_th}, \
{norm_mb_addr_th}), 0) >= {addr_th})
                        OR (LENGTH({norm_buyer_addr_eng}) > 0 AND \
COALESCE(JARO_WINKLER_SIMILARITY({norm_buyer_addr_eng}, {norm_mb_addr_eng}), 0) >= {addr_th}) AS ADDR_MATCH
                FROM redacted ao
                LEFT JOIN master_buyer mb
                    ON {ocr_taxid} = {mb_taxid}
            ),
            conf_scored AS (
                SELECT *
                    , CASE WHEN DOC_NAME IS NOT NULL AND DOC_NAME != '' THEN 1
                        ELSE 0 END AS DOC_NAME_CONF_SCORE
                    , CASE WHEN LEN(BUYER_TAX_ID) = 13 AND BUYER_FOUND THEN 1
                        ELSE 0 END AS BUYER_TAX_ID_CONF_SCORE
                    , CASE WHEN LEN(VENDOR_TAX_ID) = 13 THEN 1
                        ELSE 0 END AS VENDOR_TAX_ID_CONF_SCORE
                    , CASE WHEN TAX_INVOICE_NUMBER IS NOT NULL AND TAX_INVOICE_NUMBER != '' THEN 1
                        ELSE 0 END AS TAX_INVOICE_NUMBER_CONF_SCORE
                    , CASE WHEN TAX_INVOICE_DATE IS NOT NULL THEN 1
                        ELSE 0 END AS TAX_INVOICE_DATE_CONF_SCORE
                    , CASE WHEN TOTAL_AMOUNT IS NULL THEN 0
                        ELSE ROUND(EXP(-ABS(TOTAL_AMOUNT
                            - (COALESCE(NET_AMOUNT, 0) - COALESCE(VAT_AMOUNT, 0) + COALESCE(WITHHOLDING_TAX, 0))
                        )), 2) END AS TOTAL_AMOUNT_CONF_SCORE
                    , CASE WHEN VAT_AMOUNT IS NULL THEN 0
                        ELSE ROUND(EXP(-ABS(VAT_AMOUNT
                            - (COALESCE(NET_AMOUNT, 0) - COALESCE(TOTAL_AMOUNT, 0) + COALESCE(WITHHOLDING_TAX, 0))
                        )), 2) END AS VAT_AMOUNT_CONF_SCORE
                    , CASE WHEN NET_AMOUNT IS NULL THEN 0
                        ELSE ROUND(EXP(-ABS(NET_AMOUNT
                            - (COALESCE(TOTAL_AMOUNT, 0) + COALESCE(VAT_AMOUNT, 0) - COALESCE(WITHHOLDING_TAX, 0))
                        )), 2) END AS NET_AMOUNT_CONF_SCORE
                    , ROUND((
                        DOC_NAME_CONF_SCORE
                        + BUYER_TAX_ID_CONF_SCORE
                        + VENDOR_TAX_ID_CONF_SCORE
                        + TAX_INVOICE_NUMBER_CONF_SCORE
                        + TAX_INVOICE_DATE_CONF_SCORE
                        + TOTAL_AMOUNT_CONF_SCORE
                        + VAT_AMOUNT_CONF_SCORE
                        + NET_AMOUNT_CONF_SCORE
                    ) / 8 * 100, 2) AS OVERALL_CONF_SCORE
                    , CASE WHEN DOC_NAME_CONF_SCORE = 0 THEN '{rq["DOC_NAME"]}'
                        ELSE NULL END AS REMARK_DOC_NAME
                    , CASE WHEN BUYER_NAME_TH IS NULL OR BUYER_NAME_TH = '' THEN '{rq["BUYER_NAME"]}'
                        WHEN BUYER_NAME_ENG IS NULL OR BUYER_NAME_ENG = '' THEN '{rq["BUYER_NAME"]}'
                        ELSE NULL END AS REMARK_BUYER_NAME
                    , CASE WHEN BUYER_ADDRESS_TH IS NULL OR BUYER_ADDRESS_TH = '' THEN '{rq["BUYER_ADDRESS"]}'
                        WHEN BUYER_ADDRESS_ENG IS NULL OR BUYER_ADDRESS_ENG = '' THEN '{rq["BUYER_ADDRESS"]}'
                        ELSE NULL END AS REMARK_BUYER_ADDRESS
                    , CASE WHEN BUYER_TAX_ID IS NULL OR BUYER_TAX_ID = '' THEN '{rq["BUYER_TAX_ID"]}'
                        WHEN BUYER_TAX_ID_CONF_SCORE = 0 THEN '{vd["BUYER_TAX_ID_RULE"]}'
                        ELSE NULL END AS REMARK_BUYER_TAX_ID
                    , CASE WHEN BUYER_BRANCH_CODE IS NULL OR BUYER_BRANCH_CODE = '' THEN '{rq["BUYER_BRANCH_CODE"]}'
                        WHEN LEN(BUYER_BRANCH_CODE) != 5 THEN '{vd["BUYER_BRANCH_CODE_RULE"]}'
                        ELSE NULL END AS REMARK_BUYER_BRANCH_CODE
                    , CASE WHEN BUYER_BRANCH_NAME IS NULL OR BUYER_BRANCH_NAME = '' THEN '{rq["BUYER_BRANCH_NAME"]}'
                        ELSE NULL END AS REMARK_BUYER_BRANCH_NAME
                    , CASE WHEN VENDOR_NAME_TH IS NULL OR VENDOR_NAME_TH = '' THEN '{rq["VENDOR_NAME"]}'
                        WHEN VENDOR_NAME_ENG IS NULL OR VENDOR_NAME_ENG = '' THEN '{rq["VENDOR_NAME"]}'
                        ELSE NULL END AS REMARK_VENDOR_NAME
                    , CASE WHEN VENDOR_ADDRESS_TH IS NULL OR VENDOR_ADDRESS_TH = '' THEN '{rq["VENDOR_ADDRESS"]}'
                        WHEN VENDOR_ADDRESS_ENG IS NULL OR VENDOR_ADDRESS_ENG = '' THEN '{rq["VENDOR_ADDRESS"]}'
                        ELSE NULL END AS REMARK_VENDOR_ADDRESS
                    , CASE WHEN VENDOR_TAX_ID IS NULL OR VENDOR_TAX_ID = '' THEN '{rq["VENDOR_TAX_ID"]}'
                        WHEN VENDOR_TAX_ID_CONF_SCORE = 0 THEN '{vd["VENDOR_TAX_ID_RULE"]}'
                        ELSE NULL END AS REMARK_VENDOR_TAX_ID
                    , CASE WHEN VENDOR_BRANCH_CODE IS NULL OR VENDOR_BRANCH_CODE = '' THEN '{rq["VENDOR_BRANCH_CODE"]}'
                        WHEN LEN(VENDOR_BRANCH_CODE) != 5 THEN '{vd["VENDOR_BRANCH_CODE_RULE"]}'
                        ELSE NULL END AS REMARK_VENDOR_BRANCH_CODE
                    , CASE WHEN VENDOR_BRANCH_NAME IS NULL OR VENDOR_BRANCH_NAME = '' THEN '{rq["VENDOR_BRANCH_NAME"]}'
                        ELSE NULL END AS REMARK_VENDOR_BRANCH_NAME
                    , CASE WHEN TAX_INVOICE_NUMBER IS NULL OR TAX_INVOICE_NUMBER = '' THEN '{rq["TAX_INVOICE_NUMBER"]}'
                        ELSE NULL END AS REMARK_TAX_INVOICE_NUMBER
                    , CASE WHEN TAX_INVOICE_DATE IS NULL THEN '{rq["TAX_INVOICE_DATE"]}'
                        ELSE NULL END AS REMARK_TAX_INVOICE_DATE
                    , CASE WHEN TOTAL_AMOUNT IS NULL THEN '{rq["TOTAL_AMOUNT"]}'
                        WHEN TOTAL_AMOUNT < 0 THEN '{vd["TOTAL_AMOUNT_GT_NEGATIVE_RULE"]}'
                        WHEN TOTAL_AMOUNT_CONF_SCORE = 0 THEN '{vd["TOTAL_AMOUNT_RULE"]}'
                        ELSE NULL END AS REMARK_TOTAL_AMOUNT
                    , CASE WHEN VAT_AMOUNT IS NULL THEN '{rq["VAT_AMOUNT"]}'
                        WHEN VAT_AMOUNT < 0 THEN '{vd["VAT_AMOUNT_GT_NEGATIVE_RULE"]}'
                        WHEN VAT_AMOUNT_CONF_SCORE = 0 THEN '{vd["VAT_AMOUNT_RULE"]}'
                        ELSE NULL END AS REMARK_VAT_AMOUNT
                    , CASE WHEN NET_AMOUNT IS NULL THEN '{rq["NET_AMOUNT"]}'
                        WHEN NET_AMOUNT < 0 THEN '{vd["NET_AMOUNT_GT_NEGATIVE_RULE"]}'
                        WHEN NET_AMOUNT_CONF_SCORE = 0 THEN '{vd["NET_AMOUNT_RULE"]}'
                        ELSE NULL END AS REMARK_NET_AMOUNT
                FROM master_scored
            )
            SELECT DOC_NAME
                , BUYER_NAME_TH
                , BUYER_ADDRESS_TH
                , BUYER_NAME_ENG
                , BUYER_ADDRESS_ENG
                , BUYER_COMPANY_CODE
                , BUYER_TAX_ID
                , BUYER_BRANCH_CODE
                , BUYER_BRANCH_NAME
                , VENDOR_NAME_TH
                , VENDOR_ADDRESS_TH
                , VENDOR_NAME_ENG
                , VENDOR_ADDRESS_ENG
                , VENDOR_TAX_ID
                , VENDOR_BRANCH_CODE
                , VENDOR_BRANCH_NAME
                , TAX_INVOICE_NUMBER
                , TAX_INVOICE_DATE
                , TOTAL_AMOUNT
                , VAT_AMOUNT
                , NET_AMOUNT
                , COPY
                , RECEIVER_SIGNATURE
                , WITHHOLDING_TAX
                , INVOICE_NUMBER
                , INVOICE_AMOUNT
                , VAT_INVOICE
                , STAMP
                , FILE_NAME
                , FILE_PATH
                , ROUND(IQS_SCORE, 2) AS IQS_SCORE
                , BUYER_NAME_LOOKUP_TH
                , BUYER_ADDRESS_LOOKUP_TH
                , BUYER_NAME_LOOKUP_ENG
                , BUYER_ADDRESS_LOOKUP_ENG
                , DOC_NAME_CONF_SCORE
                , BUYER_TAX_ID_CONF_SCORE
                , VENDOR_TAX_ID_CONF_SCORE
                , TAX_INVOICE_NUMBER_CONF_SCORE
                , TAX_INVOICE_DATE_CONF_SCORE
                , TOTAL_AMOUNT_CONF_SCORE
                , VAT_AMOUNT_CONF_SCORE
                , NET_AMOUNT_CONF_SCORE
                , OVERALL_CONF_SCORE AS DOC_CONF_SCORE
                , CASE WHEN OCR_FAILED THEN '{requires_review}'
                        WHEN OCR_SUSPICIOUS THEN '{requires_review}'
                        WHEN OCR_UNSUPPORTED THEN '{requires_review}'
                        WHEN OCR_BLANK THEN '{requires_review}'
                        WHEN OVERALL_CONF_SCORE != 100 THEN '{requires_review}'
                        WHEN NOT(NAME_MATCH AND ADDR_MATCH) THEN '{requires_review}'
                        WHEN COALESCE(REMARK_DOC_NAME, REMARK_BUYER_NAME, REMARK_BUYER_ADDRESS, REMARK_BUYER_TAX_ID
                                , REMARK_BUYER_BRANCH_CODE, REMARK_BUYER_BRANCH_NAME, REMARK_VENDOR_NAME
                                , REMARK_VENDOR_ADDRESS, REMARK_VENDOR_TAX_ID, REMARK_VENDOR_BRANCH_CODE
                                , REMARK_VENDOR_BRANCH_NAME, REMARK_TAX_INVOICE_NUMBER, REMARK_TAX_INVOICE_DATE
                                , REMARK_TOTAL_AMOUNT, REMARK_VAT_AMOUNT, REMARK_NET_AMOUNT) IS NOT NULL
                            THEN '{requires_review}'
                        ELSE '{completed}' END AS DOC_STATUS
                , CASE WHEN OCR_FAILED THEN '{system_failure_remark}'
                        WHEN OCR_SUSPICIOUS THEN concat('Suspicious: ', OCR_SUSPICIOUS_MESSAGE)
                        WHEN OCR_UNSUPPORTED THEN concat('Unsupported: ', OCR_UNSUPPORTED_MESSAGE)
                        WHEN OCR_BLANK THEN concat('Blank: ', OCR_BLANK_MESSAGE)
                        ELSE concat_ws(', '
                                , REMARK_DOC_NAME, REMARK_BUYER_NAME, REMARK_BUYER_ADDRESS, REMARK_BUYER_TAX_ID
                                , REMARK_BUYER_BRANCH_CODE, REMARK_BUYER_BRANCH_NAME, REMARK_VENDOR_NAME
                                , REMARK_VENDOR_ADDRESS, REMARK_VENDOR_TAX_ID, REMARK_VENDOR_BRANCH_CODE
                                , REMARK_VENDOR_BRANCH_NAME, REMARK_TAX_INVOICE_NUMBER, REMARK_TAX_INVOICE_DATE
                                , REMARK_TOTAL_AMOUNT, REMARK_VAT_AMOUNT, REMARK_NET_AMOUNT
                                , CASE WHEN BUYER_COMPANY_CODE IS NULL THEN '{mm["COMPANY_CODE_MISMATCH"]}' END
                                , CASE WHEN NOT BUYER_FOUND THEN '{mm["BUYER_TAX_ID_NOT_FOUND"]}' END
                                , CASE WHEN BUYER_FOUND AND NOT NAME_MATCH THEN '{mm["BUYER_NAME_MISMATCH"]}' END
                                , CASE WHEN BUYER_FOUND AND NOT ADDR_MATCH THEN '{mm["BUYER_ADDRESS_MISMATCH"]}' END
                                ) END AS REMARK
                    , DATADATE
                    , OCR_ISSUE_FLAG AS ISSUE_FLAG
            FROM conf_scored
            ;
        """
