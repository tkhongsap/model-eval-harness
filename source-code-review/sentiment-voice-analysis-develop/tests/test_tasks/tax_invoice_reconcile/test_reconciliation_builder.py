"""Tests for the v2 :class:`ReconciliationBuilder` (tasks.tax_invoice_reconcile).

Covers the reworked Functions 3-5: a row is *mapped* only when all four keys align (company,
ref_doc_inv, vendor name, payment month+year); VAT is a separate verification; Mapping_Status
is Completed iff mapped **and** VAT matches (independent of the extraction verdict); Fn-4 fields
are copied as-is when mapped (blank Z45 field copied blank, no "missing" remark); the enriched
Z45 ``Mapping Tax Invoice Status`` is tri-state and a Completed line carries the mapped row's Tax
Invoice Number (Tax ID / Branch are never reconciled — they echo the Z45 source input verbatim);
Send Date stays blank; and a non-Completed extraction with no specific reason gets the fallback
Remark_AI Extract.
"""

from __future__ import annotations

import datetime as dt

import pandas as pd

from tasks.tax_invoice_reconcile.helper.messages import MappingZ45Message
from tasks.tax_invoice_reconcile.module.reconciliation_builder import ReconciliationBuilder
from tasks.tax_invoice_reconcile.schema.extraction_processing import ExtractionProcessing
from tasks.tax_invoice_reconcile.schema.z45_input import Z45Input


def _extraction_row(**overrides) -> dict:
    """A document that fully matches the default Z45 row (current ExtractionProcessing schema)."""
    row = {
        "DOC_NAME": "doc1.pdf",
        "BUYER_NAME_TH": "ACME CO",
        "BUYER_ADDRESS_TH": "123 ROAD",
        "BUYER_NAME_ENG": "ACME CO",
        "BUYER_ADDRESS_ENG": "123 ROAD",
        "BUYER_COMPANY_CODE": "1000",
        "BUYER_TAX_ID": "0105553045044",
        "BUYER_BRANCH_CODE": "00000",
        "BUYER_BRANCH_NAME": "HQ",
        "VENDOR_NAME_TH": "VENDOR CO",
        "VENDOR_ADDRESS_TH": "456 LANE",
        "VENDOR_NAME_ENG": "VENDOR CO",
        "VENDOR_ADDRESS_ENG": "456 LANE",
        "VENDOR_TAX_ID": "9999999999999",
        "VENDOR_BRANCH_CODE": "00000",
        "VENDOR_BRANCH_NAME": "VHQ",
        "TAX_INVOICE_NUMBER": "TINV-1",
        "TAX_INVOICE_DATE": dt.date(2026, 6, 10),
        "TOTAL_AMOUNT": "1000.00",
        "VAT_AMOUNT": "70.00",
        "NET_AMOUNT": "1070.00",
        "COPY": False,
        "RECEIVER_SIGNATURE": True,
        "WITHHOLDING_TAX": None,
        "INVOICE_NUMBER": "INV-1",
        "INVOICE_AMOUNT": "1000.00",
        "VAT_INVOICE": None,
        "STAMP": True,
        "FILE_NAME": "doc1.pdf",
        "FILE_PATH": "/sp/doc1.pdf",
        "IQS_SCORE": 0.95,
        "BUYER_NAME_LOOKUP_TH": "ACME CO",
        "BUYER_ADDRESS_LOOKUP_TH": "123 ROAD",
        "BUYER_NAME_LOOKUP_ENG": "ACME CO",
        "BUYER_ADDRESS_LOOKUP_ENG": "123 ROAD",
        "DOC_NAME_CONF_SCORE": 1.0,
        "BUYER_TAX_ID_CONF_SCORE": 1.0,
        "VENDOR_TAX_ID_CONF_SCORE": 1.0,
        "TAX_INVOICE_NUMBER_CONF_SCORE": 1.0,
        "TAX_INVOICE_DATE_CONF_SCORE": 1.0,
        "TOTAL_AMOUNT_CONF_SCORE": 1.0,
        "VAT_AMOUNT_CONF_SCORE": 1.0,
        "NET_AMOUNT_CONF_SCORE": 1.0,
        "DOC_CONF_SCORE": 100.0,
        "DOC_STATUS": "Completed",
        "REMARK": None,
        "DATADATE": 20260610,
        "ISSUE_FLAG": False,
    }
    row.update(overrides)
    return row


def _z45_row(**overrides) -> dict:
    """A Z45 payment row that matches the default extraction document."""
    row = {
        "company": "1000",
        "ref_doc_inv": "INV-1",
        "doc_type": "RE",
        "invoice_document": "1900000001",
        "vendor_code": "V001",
        "vendor_name": "VENDOR CO",
        "vat_amount": "70.00",
        "tax_base_amount": "1000.00",
        "payment_document": "2000000001",
        "payment_method": "C",
        "short_text": "",
        "encashment": "",
        "payment_date": "15.06.2026",
        "cheque_no": "",
        "payee_name": "",
        "doc_header_text": "",
        "document_currency": "THB",
        "net_paid": "1070.00",
        "cost_center": "",
        "tax_code": "V7",
        "tax_clearing_doc": "",
        "tax_clearing_date": "",
        "tax_id": "",
        "branch_code": "",
        "email_requester": "",
        "tax_invoice_number": "",
        "check_duplicate": "",
        "send_date": "",
        "aging": "",
        "pending_for_release": "",
        "vat_status": "",
        "clearing_doc": "",
        "process_date": "",
        "remark": "",
        "user": "",
        "user_outward": "",
        "department": "",
    }
    row.update(overrides)
    return row


def _master_vendor_df(names: list[str]) -> pd.DataFrame:
    """Master-vendor frame with the canonical field-name columns the loader returns."""
    cols = ["vendor_code", "vendor_name_eng", "vendor_name_th"]
    rows = [
        {"vendor_code": f"V{i:03d}", "vendor_name_eng": name, "vendor_name_th": name} for i, name in enumerate(names)
    ]
    return pd.DataFrame(rows, columns=cols)


def _build(
    extraction_rows: list[dict],
    z45_rows: list[dict],
    master_vendor_names: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run the builder and return the full ``(report, z45_enriched, z45_link)`` triple."""
    extraction_df = ExtractionProcessing.validate(pd.DataFrame(extraction_rows))
    z45_df = Z45Input.validate(pd.DataFrame(z45_rows))
    master_vendor_df = _master_vendor_df(master_vendor_names or [])
    return ReconciliationBuilder().build(extraction_df, z45_df, master_vendor_df)


def _run(
    extraction_rows: list[dict],
    z45_rows: list[dict],
    master_vendor_names: list[str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Like :func:`_build` but without the link frame (most tests only need the two outputs)."""
    report_df, z45_enriched_df, _ = _build(extraction_rows, z45_rows, master_vendor_names)
    return report_df, z45_enriched_df


def test_full_match_maps_fields_and_marks_completed():
    report, z45 = _run([_extraction_row()], [_z45_row()])
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["Remark_Mapping"] == ""
    assert r["Send Date"] == ""
    assert r["Invoice Document (VAT Report)"] == "1900000001"
    assert r["Payment Date (VAT Report)"] == "15.06.2026"
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == "Completed"


def test_copy_document_is_skipped():
    # Scenario 0: a copy is not reconciled — blank mapping status + Z45 fields, but Remark_Mapping
    # carries the copy explanation so the blank status is self-explanatory.
    report, z45 = _run([_extraction_row(COPY=True)], [_z45_row()])
    r = report.iloc[0]

    assert r["Copy"] == "Yes"
    assert r["Mapping_Status"] == ""
    assert r["Remark_Mapping"] == MappingZ45Message.COPY_NOT_RECONCILED_MESSAGE.value
    assert r["Invoice Document (VAT Report)"] == ""
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == ""


def test_issue_flag_document_is_blanked():
    # Scenario 0: an issue-flagged (non-copy) row is not reconciled — blank mapping + blank Z45 fields,
    # while Copy still reads 'No'. Remark_Mapping carries the issue explanation; the Z45 line it would
    # otherwise match stays blank too.
    report, z45 = _run([_extraction_row(ISSUE_FLAG=True)], [_z45_row()])
    r = report.iloc[0]

    assert r["Copy"] == "No"
    assert r["Mapping_Status"] == ""
    assert r["Remark_Mapping"] == MappingZ45Message.ISSUE_NOT_RECONCILED_MESSAGE.value
    assert r["Invoice Document (VAT Report)"] == ""
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == ""


def test_copy_and_issue_flag_prefers_issue_remark():
    # Scenario 0 fires on either flag; when both are set the issue explanation wins (it's the
    # actionable one), and Mapping_Status stays blank.
    report, _ = _run([_extraction_row(COPY=True, ISSUE_FLAG=True)], [_z45_row()])
    r = report.iloc[0]

    assert r["Copy"] == "Yes"
    assert r["Mapping_Status"] == ""
    assert r["Remark_Mapping"] == MappingZ45Message.ISSUE_NOT_RECONCILED_MESSAGE.value


def test_vat_mismatch_incompleted_and_fields_blanked():
    # Keys match but VAT differs: VAT is a hard match key now, so the row is Incompleted with a VAT
    # remark AND the Z45 fields stay blank (no enrichment). The Z45 sheet stays tri-state Incompleted.
    report, z45 = _run([_extraction_row()], [_z45_row(vat_amount="99.00")])
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Incompleted"
    assert "VAT amount does not match Z45 report" in r["Remark_Mapping"]
    assert r["Invoice Document (VAT Report)"] == ""  # VAT mismatch → not mapped → blank
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == "Incompleted"


def test_per_item_vat_invoice_match_overrides_header_mismatch():
    # A multi-reference tax invoice: each row carries the whole invoice's header VAT (999.00),
    # but the per-item Vat Invoice (70.00) is what lines up with the Z45 line. The check must use
    # the per-item VAT, so this maps Completed despite the header total differing from the line.
    report, z45 = _run(
        [_extraction_row(VAT_INVOICE="70.00", VAT_AMOUNT="999.00")],
        [_z45_row(vat_amount="70.00")],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["Remark_Mapping"] == ""
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == "Completed"


def test_per_item_vat_invoice_mismatch_is_incompleted_even_if_header_matches():
    # When Vat Invoice is present it is authoritative — no fallback to the header VAT. Here the
    # header (70.00) would match Z45 but the per-item Vat Invoice (50.00) does not, so it is flagged.
    report, _ = _run(
        [_extraction_row(VAT_INVOICE="50.00", VAT_AMOUNT="70.00")],
        [_z45_row(vat_amount="70.00")],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Incompleted"
    assert "VAT amount does not match Z45 report" in r["Remark_Mapping"]


def test_mapping_status_completed_independent_of_extraction_verdict():
    # C2: a RequiresReview extraction that maps cleanly + VAT ok is Mapping_Status=Completed.
    report, _ = _run(
        [_extraction_row(DOC_STATUS="RequiresReview", REMARK="Buyer Name mismatch with Master Buyer")],
        [_z45_row()],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["AI_Extract_Result"] == "RequiresReview"
    assert r["Remark_AI Extract"] == "Buyer Name mismatch with Master Buyer"
    assert "Buyer Name mismatch with Master Buyer" not in (r["Remark_Mapping"] or "")


def test_blank_z45_field_is_copied_blank_and_stays_completed():
    # C3 (user): a field blank in Z45 is the source of truth — copied blank, no "missing" remark.
    report, _ = _run([_extraction_row()], [_z45_row(vendor_code="")])
    r = report.iloc[0]

    assert r["Vendor Code (VAT Report)"] == ""
    assert r["Mapping_Status"] == "Completed"
    assert r["Remark_Mapping"] == ""


def test_unmatched_invoice_number_is_incompleted():
    report, _ = _run([_extraction_row(INVOICE_NUMBER="NOPE")], [_z45_row()])
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Incompleted"
    assert "Invoice Number does not match Z45 report" in r["Remark_Mapping"]


def test_company_mismatch_is_incompleted():
    report, z45 = _run([_extraction_row(BUYER_COMPANY_CODE="2000")], [_z45_row()])
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Incompleted"
    assert "Company code does not match Z45 report" in r["Remark_Mapping"]
    assert z45.iloc[0]["Mapping Tax Invoice Status"] == ""  # not mapped → blank


def test_remark_ai_extract_fallback_for_noncompleted_without_reason():
    # C1: a non-Completed extraction with no specific reason still tells the user something.
    report, _ = _run([_extraction_row(DOC_STATUS="RequiresReview", REMARK=None)], [_z45_row()])
    r = report.iloc[0]

    assert r["Remark_AI Extract"] == "Extraction requires review"


def test_send_date_is_blank_for_manual_entry():
    report, _ = _run([_extraction_row()], [_z45_row()])

    assert report.iloc[0]["Send Date"] == ""


def test_scenario_2_no_invoice_with_vat_invoice_maps_on_company_vendor_month():
    # Case 2: no Invoice No. but Vat Invoice present → match company + vendor + payment month and
    # verify per-invoice VAT (Z45's ref_doc_inv is not a key here).
    report, _ = _run(
        [_extraction_row(INVOICE_NUMBER=None, VAT_INVOICE="70.00")],
        [_z45_row()],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["Invoice Document (VAT Report)"] == "1900000001"


def test_scenario_2_vat_invoice_sums_z45_lines_by_payment_document():
    # Case 2: Vat Invoice present, no Invoice No. The per-item Vat Invoice (70.00) is verified against
    # the SUM of the Z45 lines sharing one payment document (40 + 30) — not any single line — so it maps.
    report, _ = _run(
        [_extraction_row(INVOICE_NUMBER=None, VAT_INVOICE="70.00", VAT_AMOUNT="70.00")],
        [
            _z45_row(ref_doc_inv="A", vat_amount="40.00", payment_document="PD1"),
            _z45_row(ref_doc_inv="B", vat_amount="30.00", payment_document="PD1"),
        ],
    )

    assert report.iloc[0]["Mapping_Status"] == "Completed"
    assert report.iloc[0]["Payment Document (VAT Report)"] == "PD1"


def test_scenario_4_no_invoice_no_vat_sums_z45_lines():
    # Case 4: no Invoice No., no per-item VAT → match company + vendor + month, verify the receipt
    # total against the SUM of the matched Z45 lines (40 + 30 == header 70).
    report, _ = _run(
        [_extraction_row(INVOICE_NUMBER=None, VAT_INVOICE=None, VAT_AMOUNT="70.00")],
        [
            _z45_row(ref_doc_inv="A", vat_amount="40.00"),
            _z45_row(ref_doc_inv="B", vat_amount="30.00"),
        ],
    )

    assert report.iloc[0]["Mapping_Status"] == "Completed"


def test_scenario_3_multi_invoice_voucher_sums_header_vat_against_z45_lines():
    # A multi-invoice payment voucher: one tax invoice (header VAT 70.00 repeated on every line item)
    # settles four underlying invoices, each with its own Z45 line and NO per-line Vat Invoice. The
    # header total must reconcile against the SUM of the document's four matched Z45 lines (10+20+15+25).
    refs = ["A", "B", "C", "D"]
    vats = ["10.00", "20.00", "15.00", "25.00"]
    extraction = [
        _extraction_row(
            FILE_NAME="voucher.pdf",
            TAX_INVOICE_NUMBER="TINV-V",
            INVOICE_NUMBER=ref,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
        )
        for ref in refs
    ]
    z45 = [
        _z45_row(ref_doc_inv=ref, vat_amount=vat, invoice_document=f"DOC{i}")
        for i, (ref, vat) in enumerate(zip(refs, vats, strict=True))
    ]
    report, z45_out = _run(extraction, z45)

    assert len(report) == 4
    assert list(report["Mapping_Status"]) == ["Completed"] * 4
    assert all(v != "" for v in report["Invoice Document (VAT Report)"])
    assert list(z45_out["Mapping Tax Invoice Status"]) == ["Completed"] * 4


def test_scenario_3_multi_invoice_voucher_vat_mismatch_incompleted():
    # Same voucher shape but the Z45 lines sum to 65.00, not the header 70.00 → every line is
    # Incompleted with the VAT remark and blank Z45 fields (the aggregate must still be exact).
    refs = ["A", "B", "C", "D"]
    vats = ["10.00", "20.00", "15.00", "20.00"]  # sum 65.00 != header 70.00
    extraction = [
        _extraction_row(
            FILE_NAME="voucher.pdf",
            TAX_INVOICE_NUMBER="TINV-V",
            INVOICE_NUMBER=ref,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
        )
        for ref in refs
    ]
    z45 = [_z45_row(ref_doc_inv=ref, vat_amount=vat) for ref, vat in zip(refs, vats, strict=True)]
    report, _ = _run(extraction, z45)

    assert list(report["Mapping_Status"]) == ["Incompleted"] * 4
    assert all("VAT amount does not match Z45 report" in r for r in report["Remark_Mapping"])
    assert all(v == "" for v in report["Invoice Document (VAT Report)"])


def test_scenario_3_single_invoice_still_completed():
    # No-regression guard: a single-line Scenario-3 doc (one invoice ref, header VAT == the one Z45
    # line) still maps Completed after the document-grain change (its document window is that one line).
    report, _ = _run(
        [_extraction_row(INVOICE_NUMBER="SOLO", VAT_INVOICE=None, VAT_AMOUNT="70.00")],
        [_z45_row(ref_doc_inv="SOLO", vat_amount="70.00")],
    )

    assert report.iloc[0]["Mapping_Status"] == "Completed"


def test_scenario_5_special_vendor_in_master_sums_by_payment_document():
    # Case 5: vendor in master, no per-item VAT → exact-date match; verify the extraction date-sum
    # against the Z45 sum by payment document.
    report, _ = _run(
        [
            _extraction_row(
                INVOICE_NUMBER=None,
                VAT_INVOICE=None,
                VAT_AMOUNT="70.00",
                TAX_INVOICE_DATE=dt.date(2026, 6, 15),
            )
        ],
        [_z45_row(vat_amount="70.00")],
        master_vendor_names=["VENDOR CO"],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["Payment Document (VAT Report)"] == "2000000001"


def test_scenario_5_selects_value_matching_payment_document():
    # Two payment documents match the keys; only PD1's VAT sum (70) equals the extraction sum, so PD1
    # is selected and enriches the row (PD2's 50 is rejected).
    report, _ = _run(
        [
            _extraction_row(
                INVOICE_NUMBER=None,
                VAT_INVOICE=None,
                VAT_AMOUNT="70.00",
                TAX_INVOICE_DATE=dt.date(2026, 6, 15),
            )
        ],
        [
            _z45_row(payment_document="PD1", invoice_document="DOC1", vat_amount="70.00"),
            _z45_row(payment_document="PD2", invoice_document="DOC2", vat_amount="50.00"),
        ],
        master_vendor_names=["VENDOR CO"],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Completed"
    assert r["Payment Document (VAT Report)"] == "PD1"
    assert r["Invoice Document (VAT Report)"] == "DOC1"


def test_scenario_5_multi_invoice_voucher_counts_header_vat_once():
    # Scenario 5 (vendor in master) + multi-invoice voucher: the header VAT (70.00) is repeated on all
    # four line items, so EXT_TOTAL_VAT must count it ONCE (via _doc_first), not 4x. It then equals the
    # Z45 payment-document sum (10+20+15+25 = 70) and maps Completed. Without the dedup it would be 280.
    refs = ["A", "B", "C", "D"]
    vats = ["10.00", "20.00", "15.00", "25.00"]
    extraction = [
        _extraction_row(
            FILE_NAME="voucher.pdf",
            TAX_INVOICE_NUMBER="TINV-V",
            INVOICE_NUMBER=ref,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        )
        for ref in refs
    ]
    z45 = [
        _z45_row(ref_doc_inv=ref, vat_amount=vat, payment_document="PD1") for ref, vat in zip(refs, vats, strict=True)
    ]
    report, _ = _run(extraction, z45, master_vendor_names=["VENDOR CO"])

    assert list(report["Mapping_Status"]) == ["Completed"] * 4
    assert all(pd == "PD1" for pd in report["Payment Document (VAT Report)"])


def test_scenario_5_two_documents_same_group_sum_both_headers():
    # Dedup must not under-count across DISTINCT documents: two single-line docs in the same
    # (date, buyer, vendor) group contribute their headers once each (70 + 30 = 100), matching the
    # Z45 payment-document sum → both Completed.
    extraction = [
        _extraction_row(
            FILE_NAME="a.pdf",
            TAX_INVOICE_NUMBER="TA",
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        ),
        _extraction_row(
            FILE_NAME="b.pdf",
            TAX_INVOICE_NUMBER="TB",
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="30.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        ),
    ]
    z45 = [
        _z45_row(ref_doc_inv="A", vat_amount="70.00", payment_document="PD1"),
        _z45_row(ref_doc_inv="B", vat_amount="30.00", payment_document="PD1"),
    ]
    report, _ = _run(extraction, z45, master_vendor_names=["VENDOR CO"])

    assert sorted(report["Mapping_Status"]) == ["Completed", "Completed"]


def test_scenario_5_enriched_line_concats_all_mapped_tax_invoice_numbers():
    # Scenario 5 maps a whole (date, buyer, vendor) group of documents against one payment document,
    # so each shared Z45 line legitimately belongs to several tax-invoice numbers. They must all
    # appear on the enriched line, de-duplicated + joined with ', ' (sorted for run-to-run
    # determinism) — the old MAX() kept only the lexicographically largest and silently dropped the
    # rest (the TRUE INTERNET group symptom).
    extraction = [
        _extraction_row(
            FILE_NAME="a.pdf",
            TAX_INVOICE_NUMBER="TIV-A",
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        ),
        _extraction_row(
            FILE_NAME="b.pdf",
            TAX_INVOICE_NUMBER="TIV-B",
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="30.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        ),
    ]
    z45 = [
        _z45_row(ref_doc_inv="A", vat_amount="70.00", payment_document="PD1"),
        _z45_row(ref_doc_inv="B", vat_amount="30.00", payment_document="PD1"),
    ]
    _, z45_out = _run(extraction, z45, master_vendor_names=["VENDOR CO"])

    assert list(z45_out["Mapping Tax Invoice Status"]) == ["Completed", "Completed"]
    # Both shared PD1 lines carry both group tax-invoice numbers, sorted and comma-joined.
    assert list(z45_out["Tax Invoice Number"]) == ["TIV-A, TIV-B", "TIV-A, TIV-B"]


def test_scenario_5_single_document_shows_its_one_tax_invoice_number():
    # Idempotence guard: with a single mapped document the ', ' join collapses to exactly that one
    # number (so scenarios 1-4 in the normal one-invoice-per-line case are unchanged — no trailing
    # comma, no MAX-vs-join difference).
    _, z45 = _run(
        [
            _extraction_row(
                INVOICE_NUMBER=None,
                VAT_INVOICE=None,
                VAT_AMOUNT="70.00",
                TAX_INVOICE_DATE=dt.date(2026, 6, 15),
            )
        ],
        [_z45_row(vat_amount="70.00")],
        master_vendor_names=["VENDOR CO"],
    )

    assert z45.iloc[0]["Mapping Tax Invoice Status"] == "Completed"
    assert z45.iloc[0]["Tax Invoice Number"] == "TINV-1"


def test_shared_z45_lines_summed_per_extraction_row_not_inflated():
    # The original bug: two separate documents that both match the same two Z45 lines (same
    # company/vendor/month) must each sum only their own matched lines (40 + 30 = 70), scoped by
    # _er_id — not double-count across rows. Both map Completed and the row count is preserved.
    report, _ = _run(
        [
            _extraction_row(FILE_NAME="a.pdf", INVOICE_NUMBER=None, VAT_INVOICE=None, VAT_AMOUNT="70.00"),
            _extraction_row(FILE_NAME="b.pdf", INVOICE_NUMBER=None, VAT_INVOICE=None, VAT_AMOUNT="70.00"),
        ],
        [
            _z45_row(ref_doc_inv="A", vat_amount="40.00"),
            _z45_row(ref_doc_inv="B", vat_amount="30.00"),
        ],
    )

    assert len(report) == 2
    assert sorted(report["Mapping_Status"]) == ["Completed", "Completed"]


def test_z45_grain_one_row_per_source_line_with_shared_company_candidates():
    # Grain regression: the candidate views pair one Z45 line with every extraction row of the same
    # company, so the status query must stay one row per _z_id or the export join fans out. The
    # reconciled Tax Invoice Number must come from the row that actually mapped (not a sibling
    # candidate), and a line no candidate touched stays blank. Tax ID / Branch are never reconciled —
    # they echo the Z45 source input verbatim (both blank here) on matched and unmatched lines alike.
    report, z45 = _run(
        [
            _extraction_row(),
            _extraction_row(
                FILE_NAME="other.pdf",
                DOC_NAME="other.pdf",
                VENDOR_NAME_TH="OTHER CO",
                VENDOR_NAME_ENG="OTHER CO",
                VENDOR_TAX_ID="1111111111111",
                TAX_INVOICE_NUMBER="TINV-9",
                INVOICE_NUMBER="NOPE",
            ),
        ],
        [_z45_row(), _z45_row(company="4242", ref_doc_inv="UNMATCHED")],
    )

    assert len(z45) == 2
    matched = z45[z45["Ref. Doc  (Inv.)"] == "INV-1"].iloc[0]
    unmatched = z45[z45["Ref. Doc  (Inv.)"] == "UNMATCHED"].iloc[0]
    assert matched["Mapping Tax Invoice Status"] == "Completed"
    assert matched["Tax ID"] == ""
    assert matched["Branch"] == ""
    assert matched["Tax Invoice Number"] == "TINV-1"
    assert unmatched["Mapping Tax Invoice Status"] == ""
    assert unmatched["Tax ID"] == ""
    assert unmatched["Tax Invoice Number"] == ""


def test_completed_line_reconciles_tax_invoice_number_but_trusts_source_tax_id_branch():
    # Only the Tax Invoice Number is reconciled on a Completed line — it takes the mapped extraction
    # row's value even over a stale source cell. Tax ID / Branch are trusted as-is: whatever the Z45
    # source input carried is returned verbatim, never overwritten from the extraction row.
    report, z45 = _run(
        [_extraction_row()],
        [_z45_row(tax_id="0000000000000", branch_code="99999", tax_invoice_number="OLD-1")],
    )
    line = z45.iloc[0]

    assert line["Mapping Tax Invoice Status"] == "Completed"
    assert line["Tax ID"] == "0000000000000"
    assert line["Branch"] == "99999"
    assert line["Tax Invoice Number"] == "TINV-1"


def test_incompleted_line_keeps_source_tax_keys():
    # Keys matched but VAT failed → Incompleted: no enrichment, the pre-filled source values stay.
    report, z45 = _run(
        [_extraction_row()],
        [_z45_row(vat_amount="99.00", tax_id="0105553045044", branch_code="00001", tax_invoice_number="KEEP-1")],
    )
    line = z45.iloc[0]

    assert line["Mapping Tax Invoice Status"] == "Incompleted"
    assert line["Tax ID"] == "0105553045044"
    assert line["Branch"] == "00001"
    assert line["Tax Invoice Number"] == "KEEP-1"


def test_no_candidate_at_all_gets_no_match_remark():
    # No Invoice No. and a company that matches nothing in Z45 → no candidate row at all, so the
    # diagnostic falls back to a single "no matching record" reason.
    report, _ = _run(
        [_extraction_row(INVOICE_NUMBER=None, BUYER_COMPANY_CODE="9999")],
        [_z45_row()],
    )
    r = report.iloc[0]

    assert r["Mapping_Status"] == "Incompleted"
    assert r["Remark_Mapping"] == "No matching record in Z45 report"


def _phantom_swapped_pair() -> list[dict]:
    """A Scenario-4-shaped pair: the real document plus a buyer↔vendor-swapped phantom row.

    Both rows share (FILE_NAME, TAX_INVOICE_NUMBER) with a NULL INVOICE_NUMBER — the shape a
    page-level party misread produces after the report builder groups by buyer/vendor. Only the
    real row carries the header VAT.
    """
    main = _extraction_row(
        INVOICE_NUMBER=None,
        VAT_INVOICE=None,
        VAT_AMOUNT="70.00",
        TAX_INVOICE_DATE=dt.date(2026, 6, 15),
    )
    phantom = _extraction_row(
        INVOICE_NUMBER=None,
        VAT_INVOICE=None,
        VAT_AMOUNT=None,
        TOTAL_AMOUNT=None,
        NET_AMOUNT=None,
        TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        BUYER_TAX_ID="9999999999999",
        VENDOR_TAX_ID="0105553045044",
        VENDOR_NAME_TH="ACME CO",
        VENDOR_NAME_ENG="ACME CO",
        DOC_STATUS="RequiresReview",
    )
    return [main, phantom]


def test_scenario_5_phantom_swapped_row_does_not_steal_header_vat():
    # Regression (run-to-run Completed/Incompleted flip): a phantom buyer↔vendor-swapped row shares
    # (FILE_NAME, TAX_INVOICE_NUMBER) with the real document and every INVOICE_NUMBER is NULL. The
    # old _doc_first partition ignored buyer/vendor, so the arbitrary tie-break could flag the
    # header-less phantom and drop the real header VAT from EXT_TOTAL_VAT. The partition now carries
    # the full document identity, so the real row always contributes its 70.00 and maps Completed.
    report, _ = _run(_phantom_swapped_pair(), [_z45_row(vat_amount="70.00")], master_vendor_names=["VENDOR CO"])
    main = report[report["Vendor Tax ID"] == "9999999999999"].iloc[0]

    assert main["Mapping_Status"] == "Completed"
    assert main["Payment Document (VAT Report)"] == "2000000001"


def test_scenario_5_copy_document_header_vat_excluded_from_group_sum():
    # A COPY of an already-counted invoice sits in the same (date, buyer, vendor) group. Scenario-0
    # rows never reconcile, so their header VAT must not inflate EXT_TOTAL_VAT (70 + 70 = 140 would
    # break the original's match against the 70.00 Z45 payment-document sum).
    original = _extraction_row(
        INVOICE_NUMBER=None,
        VAT_INVOICE=None,
        VAT_AMOUNT="70.00",
        TAX_INVOICE_DATE=dt.date(2026, 6, 15),
    )
    copy_row = _extraction_row(
        FILE_NAME="copy.pdf",
        INVOICE_NUMBER=None,
        VAT_INVOICE=None,
        VAT_AMOUNT="70.00",
        TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        COPY=True,
    )
    report, _ = _run([original, copy_row], [_z45_row(vat_amount="70.00")], master_vendor_names=["VENDOR CO"])
    main = report[report["Copy"] == "No"].iloc[0]

    assert main["Mapping_Status"] == "Completed"


def test_representative_pick_tied_candidates_is_deterministic_across_builds():
    # Two Z45 lines in ONE payment document tie on every preference (keys, VAT rule, vendor
    # similarity, payment document) and differ only in invoice_document. The _z_id tie-break must
    # pick the same line — the first source line — on every build, so re-runs on identical inputs
    # export identical Z45 fields.
    extraction = [
        _extraction_row(
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        )
    ]
    z45 = [
        _z45_row(invoice_document="DOC1", vat_amount="40.00"),
        _z45_row(invoice_document="DOC2", vat_amount="30.00"),
    ]
    picks = []
    for _ in range(5):
        report, _ = _run(extraction, z45, master_vendor_names=["VENDOR CO"])
        r = report.iloc[0]
        assert r["Mapping_Status"] == "Completed"
        picks.append(r["Invoice Document (VAT Report)"])

    assert picks == ["DOC1"] * 5


def test_scenario_4_shaped_frames_reconcile_identically_across_builds():
    # End-to-end determinism: repeated builds over the phantom-split shape must return identical
    # report frames (statuses, remarks, and Z45 fields) — the user-observed Completed/Incompleted
    # flip on the same input must be impossible.
    z45 = [_z45_row(vat_amount="70.00")]
    frames = []
    for _ in range(5):
        report, _ = _run(_phantom_swapped_pair(), z45, master_vendor_names=["VENDOR CO"])
        frames.append(report.sort_values(list(report.columns)).reset_index(drop=True))

    for other in frames[1:]:
        pd.testing.assert_frame_equal(frames[0], other)


# -- z45_link_df (Z45↔document match link) ---------------------------------------------------------


def test_link_scenario_5_document_without_invoice_numbers_links_its_z45_lines():
    # The TRUE INTERNET regression shape: a scen-5 document with all-NULL invoice numbers maps at
    # header level (group VAT sum), so the exporter can only attribute its Z45 lines through the
    # link frame — invoice-number equality would find nothing and silently drop them.
    extraction = [
        _extraction_row(
            FILE_NAME="tiv.pdf",
            INVOICE_NUMBER=None,
            VAT_INVOICE=None,
            VAT_AMOUNT="70.00",
            TAX_INVOICE_DATE=dt.date(2026, 6, 15),
        )
    ]
    z45 = [
        _z45_row(ref_doc_inv="A", vat_amount="40.00", payment_document="PD1"),
        _z45_row(ref_doc_inv="B", vat_amount="30.00", payment_document="PD1"),
        _z45_row(company="4242", ref_doc_inv="UNRELATED"),
    ]

    _, _, link = _build(extraction, z45, master_vendor_names=["VENDOR CO"])

    assert sorted(link["_z_id"]) == [0, 1]
    assert set(link["file_name"]) == {"tiv.pdf"}


def test_link_includes_keys_matched_vat_failed_lines():
    # Incompleted-from-VAT-mismatch lines belong in the document's VAT workbook too, so the link
    # must carry them (its ALLKEYS condition, not the stricter ER_MAPPED).
    _, z45, link = _build([_extraction_row()], [_z45_row(vat_amount="99.00")])

    assert z45.iloc[0]["Mapping Tax Invoice Status"] == "Incompleted"
    assert list(link["_z_id"]) == [0]
    assert list(link["file_name"]) == ["doc1.pdf"]


def test_link_excludes_unmatched_z45_lines():
    _, _, link = _build(
        [_extraction_row()],
        [_z45_row(), _z45_row(company="4242", ref_doc_inv="UNMATCHED")],
    )

    assert list(link["_z_id"]) == [0]


def test_link_is_deduplicated_per_document():
    # A multi-line document produces many candidate rows against the same Z45 line; the link must
    # still be one row per (Z45 line, document) pair.
    extraction = [
        _extraction_row(INVOICE_NUMBER=None, VAT_INVOICE=None, VAT_AMOUNT="70.00"),
        _extraction_row(INVOICE_NUMBER=None, VAT_INVOICE=None, VAT_AMOUNT="70.00"),
    ]

    _, _, link = _build(extraction, [_z45_row(vat_amount="70.00")])

    assert len(link) == 1
    assert (link["_z_id"].iloc[0], link["file_name"].iloc[0]) == (0, "doc1.pdf")


def test_enriched_z45_rows_stay_in_source_order():
    # The exporter slices the enriched frame positionally by _z_id, so the builder must emit it
    # in source-row order even when the first source line is the unmatched one.
    _, z45, _ = _build(
        [_extraction_row()],
        [_z45_row(company="4242", ref_doc_inv="ZZZ"), _z45_row()],
    )

    assert list(z45["Ref. Doc  (Inv.)"]) == ["ZZZ", "INV-1"]
