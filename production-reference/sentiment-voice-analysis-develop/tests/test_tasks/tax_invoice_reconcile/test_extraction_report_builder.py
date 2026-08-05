"""Tests for :class:`ExtractionReportBuilder` (tax_invoice_reconcile v2).

Focus: OCR rows collapse to one row per document, the Master Buyer is attached on a
zero-padded tax id, and the Master-Buyer verdict is folded into ``DOC_STATUS`` (Completed
only when OCR succeeded *and* buyer name+address match) and ``REMARK`` (OCR message plus
company-code / tax-id / name / address mismatch reasons, with the empty-string guard).

The final test exercises the builder against a frame validated through the REAL
``OCROutputSchema`` (Int64 ``DATADATE``, Decimal money, BooleanDtype flags) — the exact
upstream contract whose missing ``DATADATE`` column caused the runtime binder error.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pandas as pd

from tasks.ocr_tax_invoice_pipeline.schema.ocr_output import OCROutputSchema
from tasks.tax_invoice_reconcile.helper.constant import ExtractionStatus
from tasks.tax_invoice_reconcile.helper.messages import EXTRACTION_SYSTEM_FAILURE_REMARK
from tasks.tax_invoice_reconcile.module.extraction_report_builder import ExtractionReportBuilder
from tasks.tax_invoice_reconcile.schema.extraction_output import to_extraction_output

_TAX_ID = "0105553045044"


def _ocr_row(**overrides) -> dict:
    """One OCR line-item row (single page carrying the totals)."""
    row = {
        "DOC_NAME": "doc1.pdf",
        "CUSTOMER_NAME_TH": "ACME CO",
        "CUSTOMER_ADDRESS_TH": "123 ROAD",
        "CUSTOMER_NAME_ENG": "ACME CO",
        "CUSTOMER_ADDRESS_ENG": "123 ROAD",
        "CUSTOMER_TAX_ID": _TAX_ID,
        "CUSTOMER_BRANCH_CODE": "00000",
        "CUSTOMER_BRANCH_NAME": "HQ",
        "VENDOR_NAME_TH": "VENDOR CO",
        "VENDOR_ADDRESS_TH": "456 LANE",
        "VENDOR_NAME_ENG": "VENDOR CO",
        "VENDOR_ADDRESS_ENG": "456 LANE",
        "VENDOR_TAX_ID": "9999999999999",
        "VENDOR_BRANCH_CODE": "00000",
        "VENDOR_BRANCH_NAME": "VHQ",
        "TAX_INVOICE_NUMBER": "TINV-1",
        "TAX_INVOICE_DATE": dt.date(2026, 6, 10),
        "BEFORE_VAT_AMOUNT": 1000.00,
        "VAT_AMOUNT": 70.00,
        "NET_AMOUNT": 1070.00,
        "WITHHOLDING_TAX_AMOUNT": None,
        "PAYEE_SIGNATURE_FLAG": True,
        "AUTHORIZED_RECEIVER_SIGNATURE_FLAG": False,
        "AUTHORIZED_SIGNATORY_SIGNATURE_FLAG": False,
        "COPY": False,
        "INVOICE_NUMBER": "INV-1",
        "INVOICE_AMOUNT_BEFORE_VAT": 1000.00,
        "INVOICE_VAT_AMOUNT": 70.00,
        "INVOICE_AMOUNT_AFTER_VAT": None,
        "STAMP": True,
        "FILE_NAME": "doc1.pdf",
        "FILE_PATH": "/sp/doc1.pdf",
        "IQS_SCORE": 0.95,
        "STATUS": "SUCCESS",
        "MESSAGE": None,
        "PAGE_NO": 1,
        "DATADATE": 20260610,
    }
    row.update(overrides)
    return row


def _master_row(**overrides) -> dict:
    row = {
        "no": 1,
        "com_code_in_sap": "1000",
        "company_name_th": "ACME CO",
        "company_name_eng": "ACME CO",
        "tax_id": _TAX_ID,
        "company_address_th": "123 ROAD",
        "company_address_eng": "123 ROAD",
    }
    row.update(overrides)
    return row


def test_build_full_match_marks_completed_with_blank_remark():
    # Arrange
    ocr_df = pd.DataFrame([_ocr_row()])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert
    assert len(result) == 1
    row = result.iloc[0]
    assert row["BUYER_COMPANY_CODE"] == "1000"
    assert row["DOC_STATUS"] == ExtractionStatus.COMPLETED.value
    assert row["REMARK"] == ""
    # The data-date carried from the OCR output must survive aggregation (reconcile uses it).
    assert row["DATADATE"] == 20260610


def test_build_suspicious_ocr_status_forces_review_and_carries_message():
    # Arrange — a SUSPICIOUS doc whose fields all look valid must still be flagged: BU field
    # validation can't re-derive a prompt-injection signal, so the OCR verdict is carried through.
    ocr_df = pd.DataFrame([_ocr_row(STATUS="SUSPICIOUS", MESSAGE="prompt injection detected")])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert row["REMARK"] == "Suspicious: prompt injection detected"


def test_build_suspicious_ocr_status_blanks_leaked_fields():
    # Arrange — the OCR model can leak extracted fields on a SUSPICIOUS page (ignoring the
    # "return the empty shape" prompt rule). The builder must blank them so the report never
    # shows untrusted data or an inflated confidence for a flagged doc.
    ocr_df = pd.DataFrame([_ocr_row(STATUS="SUSPICIOUS", MESSAGE="prompt injection detected")])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert — extracted fields blanked, no master match, confidence collapses; identity kept.
    assert pd.isna(row["BUYER_NAME_TH"])
    assert pd.isna(row["BUYER_TAX_ID"])
    assert pd.isna(row["TOTAL_AMOUNT"])
    assert pd.isna(row["BUYER_COMPANY_CODE"])
    assert row["DOC_CONF_SCORE"] < 100
    assert row["FILE_PATH"] == "/sp/doc1.pdf"
    assert row["DOC_NAME"] == "doc1.pdf"


def test_build_null_amounts_score_zero_confidence():
    # Arrange — a non-suspicious doc that simply prints no amounts. A not-printed amount has no
    # confidence and must score 0, not EXP(0)=1.
    ocr_df = pd.DataFrame([_ocr_row(BEFORE_VAT_AMOUNT=None, VAT_AMOUNT=None, NET_AMOUNT=None)])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["TOTAL_AMOUNT_CONF_SCORE"] == 0
    assert row["VAT_AMOUNT_CONF_SCORE"] == 0
    assert row["NET_AMOUNT_CONF_SCORE"] == 0
    assert row["DOC_CONF_SCORE"] < 100


def test_build_unsupported_ocr_status_forces_review_and_carries_message():
    # Arrange — UNSUPPORTED is the other status BU validation cannot reproduce.
    ocr_df = pd.DataFrame([_ocr_row(STATUS="UNSUPPORTED", MESSAGE="document type not supported")])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert row["REMARK"] == "Unsupported: document type not supported"


def test_build_blank_ocr_status_forces_review_and_carries_message():
    # Arrange — a BLANK doc is forced to review with its OCR reason, but (unlike SUSPICIOUS /
    # UNSUPPORTED / FAILED) its extracted fields are trustworthy-but-empty, so they are KEPT
    # rather than redacted — BLANK is not in the OCR_REDACT set.
    ocr_df = pd.DataFrame([_ocr_row(STATUS="BLANK", MESSAGE="No line items found on the document")])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert row["REMARK"] == "Blank: No line items found on the document"
    assert row["BUYER_NAME_TH"] == "ACME CO"
    assert row["TOTAL_AMOUNT"] == Decimal("1000.00")


def test_build_keeps_failed_page_with_null_invoice_number():
    # Arrange — a validation-FAILED page nulls all document fields (incl. TAX_INVOICE_NUMBER),
    # exactly as result_retriever emits it. A non-NULL-safe join would silently drop it, hiding
    # the source file from the Output Report. It must survive alongside the SUCCESS row.
    success = _ocr_row()
    failed = _ocr_row(
        TAX_INVOICE_NUMBER=None,
        STATUS="FAILED",
        MESSAGE="VAT_AMOUNT is required for DOC_TYPE='TaxInvoice'",
        DOC_NAME=None,
        CUSTOMER_NAME_TH=None,
        CUSTOMER_NAME_ENG=None,
        CUSTOMER_TAX_ID=None,
        INVOICE_NUMBER=None,
        # result_retriever emits a validation-FAILED row as metadata only, so the document flags are
        # NULL. A plain NumPy bool column cannot hold <NA>, so this guards the coercion crash.
        COPY=None,
        STAMP=None,
        FILE_NAME="doc2.pdf",
        FILE_PATH="/sp/doc2.pdf",
        PAGE_NO=1,
    )
    ocr_df = pd.DataFrame([success, failed])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — both source files appear; the system-FAILED page is flagged for review and carries the
    # clean system-failure remark (NOT the misleading "… is missing" list re-derived from its null fields).
    assert set(result["FILE_PATH"]) == {"/sp/doc1.pdf", "/sp/doc2.pdf"}
    failed_row = result[result["FILE_PATH"] == "/sp/doc2.pdf"].iloc[0]
    assert failed_row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert failed_row["REMARK"] == EXTRACTION_SYSTEM_FAILURE_REMARK


def test_to_extraction_output_redacts_failed_page_flags():
    # Arrange — a FAILED page is in the OCR_REDACT set, so its untrusted visual flags are
    # blanked to FALSE (renders 'No') rather than left NULL. Run the full build -> project chain
    # that crashed in production (to_extraction_output is otherwise mocked in the reconcile-task
    # tests, so this is the only place the export dtypes are exercised).
    success = _ocr_row()
    failed = _ocr_row(
        TAX_INVOICE_NUMBER=None,
        STATUS="FAILED",
        MESSAGE="VAT_AMOUNT is required for DOC_TYPE='TaxInvoice'",
        COPY=None,
        STAMP=None,
        FILE_NAME="doc2.pdf",
        FILE_PATH="/sp/doc2.pdf",
    )
    processing_df = ExtractionReportBuilder().build(pd.DataFrame([success, failed]), pd.DataFrame([_master_row()]))

    # Act — must not raise the <NA>-into-bool coercion error.
    output_df = to_extraction_output(processing_df)

    # Assert — both source files survive the projection; DATADATE is dropped from the export; the
    # FAILED page's redacted COPY flag lands as FALSE (not NULL).
    assert set(output_df["FILE_PATH"]) == {"/sp/doc1.pdf", "/sp/doc2.pdf"}
    assert "DATADATE" not in output_df.columns
    assert output_df[output_df["FILE_PATH"] == "/sp/doc2.pdf"].iloc[0]["COPY"] == False  # noqa: E712


def test_build_blank_buyer_name_folds_mismatch_into_status_and_remark():
    # Arrange — both OCR and master buyer name blank must NOT be treated as a match.
    ocr_df = pd.DataFrame([_ocr_row(CUSTOMER_NAME_TH="", CUSTOMER_NAME_ENG="")])
    master_df = pd.DataFrame([_master_row(company_name_th="", company_name_eng="")])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert "Buyer Name mismatch with Master Buyer" in row["REMARK"]


def test_build_multipage_distributes_totals_from_last_page_to_earlier_pages():
    # Arrange — a single invoice spanning two pages: page 1 carries detail line items
    # with no totals, page 2 is the summary page that carries the totals.
    detail_a = _ocr_row(
        INVOICE_NUMBER="INV-A",
        BEFORE_VAT_AMOUNT=None,
        VAT_AMOUNT=None,
        NET_AMOUNT=None,
        INVOICE_AMOUNT_BEFORE_VAT=400.00,
        INVOICE_VAT_AMOUNT=28.00,
        PAGE_NO=1,
    )
    detail_b = _ocr_row(
        INVOICE_NUMBER="INV-B",
        BEFORE_VAT_AMOUNT=None,
        VAT_AMOUNT=None,
        NET_AMOUNT=None,
        INVOICE_AMOUNT_BEFORE_VAT=600.00,
        INVOICE_VAT_AMOUNT=42.00,
        PAGE_NO=2,
    )
    totals = _ocr_row(INVOICE_NUMBER="INV-C", PAGE_NO=3)
    ocr_df = pd.DataFrame([detail_a, detail_b, totals])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — the page-3 totals reach every line item of the same invoice.
    assert len(result) == 3
    assert (result["TOTAL_AMOUNT"] == 1000.00).all()
    assert (result["VAT_AMOUNT"] == 70.00).all()
    assert (result["NET_AMOUNT"] == 1070.00).all()


_NULL_HEADER = {
    "DOC_NAME": None,
    "CUSTOMER_NAME_TH": None,
    "CUSTOMER_ADDRESS_TH": None,
    "CUSTOMER_NAME_ENG": None,
    "CUSTOMER_ADDRESS_ENG": None,
    "CUSTOMER_BRANCH_CODE": None,
    "CUSTOMER_BRANCH_NAME": None,
    "VENDOR_NAME_TH": None,
    "VENDOR_ADDRESS_TH": None,
    "VENDOR_NAME_ENG": None,
    "VENDOR_ADDRESS_ENG": None,
    "VENDOR_BRANCH_CODE": None,
    "VENDOR_BRANCH_NAME": None,
    "TAX_INVOICE_DATE": None,
}


def test_build_multipage_propagates_header_from_first_page_to_continuation_page():
    # Arrange — a 2-page invoice like the 50-page Scenario4 doc: page 1 carries the full header
    # (buyer / vendor / date) but no totals; the continuation page correctly returns those header
    # fields NULL (they are not printed on it) and carries the totals. Per the extraction contract,
    # CUSTOMER_TAX_ID/VENDOR_TAX_ID repeat on every page of a document, so the continuation page
    # keeps the document's tax IDs. The tax-invoice number is stable on both pages. Without header
    # propagation the NULL page split off a "Buyer Name is missing" ghost review row; it must now
    # inherit the document's header and collapse to one row.
    header_page = _ocr_row(
        INVOICE_NUMBER="INV-1",
        BEFORE_VAT_AMOUNT=None,
        VAT_AMOUNT=None,
        NET_AMOUNT=None,
        INVOICE_AMOUNT_BEFORE_VAT=400.00,
        INVOICE_VAT_AMOUNT=28.00,
        PAGE_NO=1,
    )
    continuation_page = _ocr_row(
        INVOICE_NUMBER="INV-1",
        INVOICE_AMOUNT_BEFORE_VAT=600.00,
        INVOICE_VAT_AMOUNT=42.00,
        PAGE_NO=2,
        **_NULL_HEADER,
    )
    ocr_df = pd.DataFrame([header_page, continuation_page])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — one clean row: header inherited from page 1, totals from page 2, no ghost.
    assert len(result) == 1
    row = result.iloc[0]
    assert row["BUYER_NAME_TH"] == "ACME CO"
    assert row["VENDOR_NAME_TH"] == "VENDOR CO"
    assert pd.Timestamp(row["TAX_INVOICE_DATE"]).date() == dt.date(2026, 6, 10)
    assert row["TOTAL_AMOUNT"] == Decimal("1000.00")
    assert row["DOC_STATUS"] == ExtractionStatus.COMPLETED.value
    assert "missing" not in (row["REMARK"] or "")


def _blank_page(**overrides) -> dict:
    """A 0-item continuation / signature page: STATUS=BLANK, null line fields."""
    return _ocr_row(
        STATUS="BLANK",
        MESSAGE="No line items found on the document",
        INVOICE_NUMBER=None,
        INVOICE_AMOUNT_BEFORE_VAT=None,
        INVOICE_VAT_AMOUNT=None,
        **overrides,
    )


def test_build_multipage_totals_first_splits_blank_totals_row():
    # Arrange — a 2-page invoice printed totals-first (Yeeraf INV…07 shape): page 1 is a totals-only
    # page (0 line items -> BLANK) carrying the grand total, page 2 holds the line items but no totals
    # footer. The BLANK totals page is surfaced as its own review row; the SUCCESS line-item row still
    # inherits the propagated totals.
    totals_first = _blank_page(PAGE_NO=1)  # keeps BEFORE_VAT/VAT/NET defaults (the totals)
    items_after = _ocr_row(
        INVOICE_NUMBER="INV-1",
        BEFORE_VAT_AMOUNT=None,
        VAT_AMOUNT=None,
        NET_AMOUNT=None,
        INVOICE_AMOUNT_BEFORE_VAT=1000.00,
        INVOICE_VAT_AMOUNT=70.00,
        PAGE_NO=2,
    )
    ocr_df = pd.DataFrame([totals_first, items_after])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — two rows: the SUCCESS line-item row (totals propagated, not flagged blank) and the
    # BLANK totals page as its own review row.
    assert len(result) == 2
    success_row = result[result["INVOICE_NUMBER"] == "INV-1"].iloc[0]
    assert success_row["TOTAL_AMOUNT"] == Decimal("1000.00")
    assert success_row["VAT_AMOUNT"] == Decimal("70.00")
    assert success_row["NET_AMOUNT"] == Decimal("1070.00")
    assert "No line items" not in (success_row["REMARK"] or "")
    blank_row = result[result["INVOICE_NUMBER"].isna()].iloc[0]
    assert blank_row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert "No line items" in (blank_row["REMARK"] or "")


def test_build_signature_page_splits_and_flags_the_invoice():
    # Arrange — the line-item page carries no signature/stamp; the trailing signature page is a 0-item
    # BLANK page that holds the signature + stamp (Yeeraf INV…06 / amity shape). The BLANK page is its
    # own review row, but its signature/stamp still propagate onto the SUCCESS invoice row.
    items_page = _ocr_row(
        INVOICE_NUMBER="INV-1",
        PAYEE_SIGNATURE_FLAG=False,
        AUTHORIZED_RECEIVER_SIGNATURE_FLAG=False,
        AUTHORIZED_SIGNATORY_SIGNATURE_FLAG=False,
        STAMP=False,
        PAGE_NO=1,
    )
    signature_page = _blank_page(
        BEFORE_VAT_AMOUNT=None,
        VAT_AMOUNT=None,
        NET_AMOUNT=None,
        PAYEE_SIGNATURE_FLAG=True,
        STAMP=True,
        PAGE_NO=2,
    )
    ocr_df = pd.DataFrame([items_page, signature_page])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — two rows: the SUCCESS invoice (signature/stamp propagated true, not flagged blank) plus
    # the BLANK signature page as its own review row.
    assert len(result) == 2
    success_row = result[result["INVOICE_NUMBER"] == "INV-1"].iloc[0]
    assert bool(success_row["RECEIVER_SIGNATURE"]) is True
    assert bool(success_row["STAMP"]) is True
    assert "No line items" not in (success_row["REMARK"] or "")
    blank_row = result[result["INVOICE_NUMBER"].isna()].iloc[0]
    assert blank_row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert "No line items" in (blank_row["REMARK"] or "")


def test_build_original_and_copy_split_success_and_blank_rows():
    # Arrange — one invoice printed as a COPY (pages 1–2) and an ORIGINAL (pages 3–4) sharing the same
    # tax-invoice number (amity shape); each side has a line-item page and a 0-item signature page.
    # Segmenting by COPY and status class yields four rows: 2 SUCCESS invoices + 2 BLANK review rows.
    copy_items = _ocr_row(INVOICE_NUMBER="INV-1", COPY=True, PAGE_NO=1)
    copy_sig = _blank_page(COPY=True, BEFORE_VAT_AMOUNT=None, VAT_AMOUNT=None, NET_AMOUNT=None, PAGE_NO=2)
    orig_items = _ocr_row(INVOICE_NUMBER="INV-1", COPY=False, PAGE_NO=3)
    orig_sig = _blank_page(COPY=False, BEFORE_VAT_AMOUNT=None, VAT_AMOUNT=None, NET_AMOUNT=None, PAGE_NO=4)
    ocr_df = pd.DataFrame([copy_items, copy_sig, orig_items, orig_sig])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — four rows: two SUCCESS invoices (COPY split, totals present) + two BLANK review rows.
    assert len(result) == 4
    success = result[result["INVOICE_NUMBER"] == "INV-1"]
    blanks = result[result["INVOICE_NUMBER"].isna()]
    assert len(success) == 2
    assert len(blanks) == 2
    assert {bool(v) for v in success["COPY"]} == {True, False}
    assert (success["TOTAL_AMOUNT"] == Decimal("1000.00")).all()
    assert not any("No line items" in (r or "") for r in success["REMARK"])
    assert (blanks["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value).all()
    assert all("No line items" in (r or "") for r in blanks["REMARK"])


def test_build_blank_page_with_stray_vat_still_propagates_invoice_vat():
    # Arrange — the amity regression: ORIGINAL + COPY, each a SUCCESS invoice page + a 0-item BLANK
    # signature page. The COPY-side blank page carries a stray VAT_AMOUNT=0.0 (not null). The four rows
    # must survive and every row must show the propagated invoice VAT (70), never the stray 0.0.
    copy_items = _ocr_row(INVOICE_NUMBER="INV-1", COPY=True, PAGE_NO=1)
    copy_sig = _blank_page(COPY=True, BEFORE_VAT_AMOUNT=None, VAT_AMOUNT=0.0, NET_AMOUNT=None, PAGE_NO=2)
    orig_items = _ocr_row(INVOICE_NUMBER="INV-1", COPY=False, PAGE_NO=3)
    orig_sig = _blank_page(COPY=False, BEFORE_VAT_AMOUNT=None, VAT_AMOUNT=None, NET_AMOUNT=None, PAGE_NO=4)
    ocr_df = pd.DataFrame([copy_items, copy_sig, orig_items, orig_sig])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — four rows; the stray 0.0 never appears — every row carries the propagated VAT of 70.
    assert len(result) == 4
    assert (result["VAT_AMOUNT"] == Decimal("70.00")).all()
    blanks = result[result["INVOICE_NUMBER"].isna()]
    assert len(blanks) == 2
    assert (blanks["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value).all()


def test_build_non_pipeline_status_stays_in_invoice_group():
    # Arrange — only the four pipeline-issue statuses (FAILED/SUSPICIOUS/UNSUPPORTED/BLANK) split into
    # their own review row. A cleanly-extracted SUCCESS row is not a pipeline issue (the common OCR
    # pipeline no longer validates), so it stays in the invoice group for this task to validate —
    # grouping keys on the IS_PIPELINE_ISSUE flag, not the raw STATUS value.
    line_a = _ocr_row(INVOICE_NUMBER="INV-1", STATUS="SUCCESS", INVOICE_VAT_AMOUNT=28.00, PAGE_NO=1)
    line_b = _ocr_row(INVOICE_NUMBER="INV-1", STATUS="SUCCESS", INVOICE_VAT_AMOUNT=42.00, PAGE_NO=1)
    ocr_df = pd.DataFrame([line_a, line_b])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — a single invoice row (the two line items summed), not two split rows.
    assert len(result) == 1
    assert float(result.iloc[0]["VAT_INVOICE"]) == 70.00


def test_build_single_item_no_line_vat_falls_back_to_document_vat():
    # Arrange — a single-line-item invoice that prints no per-line VAT. VAT_INVOICE must fall back
    # to the document-level VAT total (DOC_VAT_AMOUNT), not stay null; without the fallback it would.
    ocr_df = pd.DataFrame([_ocr_row(INVOICE_VAT_AMOUNT=None, VAT_AMOUNT=70.00)])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert float(row["VAT_INVOICE"]) == 70.00


def test_build_multi_item_uses_per_line_vat_sum_not_document_vat():
    # Arrange — a 2-line invoice (both rows one page, one invoice number) with per-line VAT summing
    # to 78, while the footer VAT is 70. The single-item fallback must NOT fire (COUNT > 1):
    # VAT_INVOICE is the per-line sum, not the footer.
    line_a = _ocr_row(INVOICE_NUMBER="INV-1", INVOICE_VAT_AMOUNT=28.00, PAGE_NO=1)
    line_b = _ocr_row(INVOICE_NUMBER="INV-1", INVOICE_VAT_AMOUNT=50.00, PAGE_NO=1)
    ocr_df = pd.DataFrame([line_a, line_b])
    master_df = pd.DataFrame([_master_row()])

    # Act
    result = ExtractionReportBuilder().build(ocr_df, master_df)

    # Assert — the two lines collapse to one row; 28 + 50 = 78 (per-line ELSE branch), distinct
    # from the footer VAT of 70.
    assert len(result) == 1
    assert float(result.iloc[0]["VAT_INVOICE"]) == 78.00


def test_build_after_vat_only_no_line_vat_recovers_invoice_amount():
    # Arrange — a footer-less listing row where the model misfiled the printed pre-VAT amount into
    # INVOICE_AMOUNT_AFTER_VAT (BEFORE null) with no per-line VAT. The report must recover it as the
    # Invoice Amount via the VAT-null-guarded fallback (after == before when there is no line VAT).
    ocr_df = pd.DataFrame(
        [
            _ocr_row(
                INVOICE_AMOUNT_BEFORE_VAT=None,
                INVOICE_VAT_AMOUNT=None,
                INVOICE_AMOUNT_AFTER_VAT=191773.83,
            )
        ]
    )
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert float(row["INVOICE_AMOUNT"]) == 191773.83


def test_build_after_vat_only_with_line_vat_keeps_invoice_amount_null():
    # Arrange — an itemized row with per-line VAT where only the VAT-inclusive (gross) AFTER value is
    # present. The fallback must NOT fire (line VAT present → after is gross, not a pre-VAT amount),
    # so Invoice Amount stays null rather than leaking a gross figure into a before-VAT column.
    ocr_df = pd.DataFrame(
        [
            _ocr_row(
                INVOICE_AMOUNT_BEFORE_VAT=None,
                INVOICE_VAT_AMOUNT=70.00,
                INVOICE_AMOUNT_AFTER_VAT=1070.00,
            )
        ]
    )
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert pd.isna(row["INVOICE_AMOUNT"])


def test_build_unknown_tax_id_folds_taxid_and_company_code_remarks():
    # Arrange
    ocr_df = pd.DataFrame([_ocr_row(CUSTOMER_TAX_ID="1111111111111")])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_STATUS"] == ExtractionStatus.REQUIRES_REVIEW.value
    assert pd.isna(row["BUYER_COMPANY_CODE"]) or row["BUYER_COMPANY_CODE"] is None
    assert "Buyer Tax ID not found in Master Buyer" in row["REMARK"]
    assert "Company code doesn't match Master Buyer" in row["REMARK"]


def test_build_against_real_ocr_output_schema_contract():
    # Arrange — feed a frame validated through the REAL OCROutputSchema so DATADATE is the
    # nullable Int64 and money columns are Decimal, exactly as final_df arrives at runtime.
    raw = {
        "DOC_NAME": "doc1.pdf",
        "DOC_TYPE": "tax_invoice",
        "TAX_INVOICE_NUMBER": "TINV-1",
        "TAX_INVOICE_DATE": dt.date(2026, 6, 10),
        "CUSTOMER_NAME_TH": "ACME CO",
        "CUSTOMER_ADDRESS_TH": "123 ROAD",
        "CUSTOMER_NAME_ENG": "ACME CO",
        "CUSTOMER_ADDRESS_ENG": "123 ROAD",
        "CUSTOMER_TAX_ID": _TAX_ID,
        "CUSTOMER_BRANCH_CODE": "00000",
        "CUSTOMER_BRANCH_NAME": "HQ",
        "VENDOR_NAME_TH": "VENDOR CO",
        "VENDOR_ADDRESS_TH": "456 LANE",
        "VENDOR_NAME_ENG": "VENDOR CO",
        "VENDOR_ADDRESS_ENG": "456 LANE",
        "VENDOR_TAX_ID": "9999999999999",
        "VENDOR_BRANCH_CODE": "00000",
        "VENDOR_BRANCH_NAME": "VHQ",
        "BEFORE_VAT_AMOUNT": "1000.00",
        "VAT_AMOUNT": "70.00",
        "AFTER_VAT_AMOUNT": "1070.00",
        "WITHHOLDING_TAX_AMOUNT": None,
        "NET_AMOUNT": "1070.00",
        "ITEM_NO": 1,
        "INVOICE_NUMBER": "INV-1",
        "DESCRIPTION": "item",
        "QUANTITY": 1.0,
        "UNIT_PRICE": 1000.0,
        "INVOICE_AMOUNT_BEFORE_VAT": "1000.00",
        "INVOICE_VAT_AMOUNT": "70.00",
        "INVOICE_AMOUNT_AFTER_VAT": "1070.00",
        "COPY": False,
        "PAYEE_SIGNATURE_FLAG": True,
        "PAYEE_SIGNATURE_NAME": None,
        "AUTHORIZED_RECEIVER_SIGNATURE_FLAG": False,
        "AUTHORIZED_RECEIVER_SIGNATURE_NAME": None,
        "AUTHORIZED_SIGNATORY_SIGNATURE_FLAG": False,
        "AUTHORIZED_SIGNATORY_SIGNATURE_NAME": None,
        "STAMP": True,
        "FILE_PATH": "/sp/doc1.pdf",
        "FILE_NAME": "doc1.pdf",
        "PAGE_NO": 1,
        "IQS_SCORE": 0.95,
        "START_TIME": dt.datetime(2026, 6, 10, 1, 0, 0),
        "END_TIME": dt.datetime(2026, 6, 10, 1, 0, 5),
        "STATUS": "SUCCESS",
        "MESSAGE": None,
        "USAGE_METADATA": {"total_token_count": 10},
        "DATADATE": "20260610",
    }
    ocr_df = OCROutputSchema.validate(pd.DataFrame([raw]))
    assert str(ocr_df["DATADATE"].dtype) == "Int64"  # guards the upstream contract shape

    # Act
    result = ExtractionReportBuilder().build(ocr_df, pd.DataFrame([_master_row()]))

    # Assert — the builder binds DATADATE and produces a valid per-document row.
    assert len(result) == 1
    assert result.iloc[0]["DATADATE"] == 20260610
    assert result.iloc[0]["DOC_STATUS"] == ExtractionStatus.COMPLETED.value


def test_build_full_match_scores_document_confidence_100():
    # Arrange — every field present/valid and the amounts reconcile, so every field score is 1.
    ocr_df = pd.DataFrame([_ocr_row()])
    master_df = pd.DataFrame([_master_row()])

    # Act
    row = ExtractionReportBuilder().build(ocr_df, master_df).iloc[0]

    # Assert
    assert row["DOC_CONF_SCORE"] == 100
    assert row["TOTAL_AMOUNT_CONF_SCORE"] == 1
    assert row["DOC_NAME_CONF_SCORE"] == 1


def test_to_extraction_output_includes_confidence_scores():
    # Arrange — confidence scores are part of the exported extraction report (not the Output Report).
    processing_df = ExtractionReportBuilder().build(pd.DataFrame([_ocr_row()]), pd.DataFrame([_master_row()]))

    # Act
    output_df = to_extraction_output(processing_df)

    # Assert
    assert "DOC_CONF_SCORE" in output_df.columns
    assert "BUYER_TAX_ID_CONF_SCORE" in output_df.columns
    assert output_df.iloc[0]["DOC_CONF_SCORE"] == 100
