"""Tests for :class:`FactCheckEvaluator` and its ``confusion_metrics`` static method.

The evaluator pairs GT and extraction rows on the composite document-line key
(file + tax invoice number + copy + invoice number), so key-field mismatches unpair the
whole row while non-key mismatches only zero their own field.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pandas as pd

from tasks.tax_invoice_reconcile.helper.constant import FIELD_MAPPING, OVERALL_LABEL
from tasks.tax_invoice_reconcile.module.fact_check_evaluator import FactCheckEvaluator

_SCORED_FIELD_COUNT = len(FIELD_MAPPING)


def _gt_row(**overrides) -> dict:
    """One ground-truth row (canonical column names) — perfectly matchable defaults."""
    row = {
        "file_name": "invoice_001.pdf",
        "document_name": "ใบกำกับภาษี",
        "buyer_name": "บริษัท ทดสอบ จำกัด",
        "buyer_address": "123 ถนนทดสอบ",
        "buyer_tax_id": "0105553045044",
        "buyer_branch_code": "00000",
        "buyer_branch_name": "สำนักงานใหญ่",
        "vendor_name": "Vendor Co",
        "vendor_address": "9 Market Rd",
        "vendor_tax_id": "0107538000012",
        "vendor_branch_code": "00001",
        "vendor_branch_name": "Branch 1",
        "tax_invoice_number": "B00110053682",
        "tax_invoice_date": "02/03/2026",
        "total_amount": "100.00",
        "vat": "7.00",
        "net_amount": "107.00",
        "copy": "No",
        "receiver_signature": "Yes",
        "withholding_tax": "3.00",
        "invoice_number": "367687312",
        "invoice_amount": "100.00",
        "vat_invoice": "7.00",
        "stamp": "No",
    }
    row.update(overrides)
    return row


def _proc_row(**overrides) -> dict:
    """One extraction row (ExtractionProcessing columns) matching ``_gt_row`` defaults."""
    row = {
        "FILE_NAME": "invoice_001.pdf",
        "DOC_NAME": "ใบกำกับภาษี",
        "BUYER_NAME_TH": "บริษัท ทดสอบ จำกัด",
        "BUYER_NAME_ENG": None,
        "BUYER_ADDRESS_TH": "123 ถนนทดสอบ",
        "BUYER_ADDRESS_ENG": None,
        "BUYER_TAX_ID": "0105553045044",
        "BUYER_BRANCH_CODE": "00000",
        "BUYER_BRANCH_NAME": "สำนักงานใหญ่",
        "VENDOR_NAME_TH": None,
        "VENDOR_NAME_ENG": "Vendor Co",
        "VENDOR_ADDRESS_TH": None,
        "VENDOR_ADDRESS_ENG": "9 Market Rd",
        "VENDOR_TAX_ID": "0107538000012",
        "VENDOR_BRANCH_CODE": "00001",
        "VENDOR_BRANCH_NAME": "Branch 1",
        "TAX_INVOICE_NUMBER": "B00110053682",
        "TAX_INVOICE_DATE": date(2026, 3, 2),
        "TOTAL_AMOUNT": Decimal("100.00"),
        "VAT_AMOUNT": Decimal("7.00"),
        "NET_AMOUNT": Decimal("107.00"),
        "COPY": False,
        "RECEIVER_SIGNATURE": True,
        "WITHHOLDING_TAX": Decimal("3.00"),
        "INVOICE_NUMBER": "367687312",
        "INVOICE_AMOUNT": Decimal("100.00"),
        "VAT_INVOICE": Decimal("7.00"),
        "STAMP": False,
    }
    row.update(overrides)
    return row


def _metrics_by_label(rows: list[dict]) -> dict[str, dict]:
    return {r["label"]: r for r in rows}


class TestConfusionMetrics:
    def test_all_correct_gives_full_marks(self):
        assert FactCheckEvaluator.confusion_metrics(10, 0, 0, 0) == {
            "accuracy": 100.0,
            "precision": 100.0,
            "recall": 100.0,
            "f1_score": 100.0,
        }

    def test_all_wrong_gives_zeros(self):
        assert FactCheckEvaluator.confusion_metrics(0, 5, 0, 0) == {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
        }

    def test_mixed_uses_raw_count_f1(self):
        # 9 correct / 1 wrong -> precision == accuracy == 90, recall 100,
        # f1 = 2*9 / (2*9 + 1 + 0) = 18/19 = 94.74.
        assert FactCheckEvaluator.confusion_metrics(9, 1, 0, 0) == {
            "accuracy": 90.0,
            "precision": 90.0,
            "recall": 100.0,
            "f1_score": 94.74,
        }

    def test_f1_uses_raw_counts_when_fn_and_tn_are_non_zero(self):
        # Arrange — the tax-invoice matrix always passes fn=tn=0, but the formula must not depend on
        # that. Asymmetric counts so precision != recall and neither equals f1.
        tp, fp, fn, tn = 8, 2, 4, 6

        # Act
        metrics = FactCheckEvaluator.confusion_metrics(tp, fp, fn, tn)

        # Assert — f1 is 2*TP / (2*TP + FP + FN), taken straight from the counts.
        assert metrics["f1_score"] == round(2 * tp / (2 * tp + fp + fn) * 100, 2)
        assert metrics == {
            "accuracy": 70.0,  # (8+6)/20
            "precision": 80.0,  # 8/10
            "recall": 66.67,  # 8/12
            "f1_score": 72.73,  # 16/22
        }

    def test_empty_matrix_is_all_zero(self):
        assert FactCheckEvaluator.confusion_metrics(0, 0, 0, 0) == {
            "accuracy": 0.0,
            "precision": 0.0,
            "recall": 0.0,
            "f1_score": 0.0,
        }


class TestEvaluate:
    def test_perfect_match_scores_every_field_and_overall_100(self):
        # Arrange
        gt = pd.DataFrame([_gt_row()])
        proc = pd.DataFrame([_proc_row()])

        # Act
        rows = FactCheckEvaluator().evaluate(proc, gt)

        # Assert — one row per scored field + overall, all 100%.
        assert len(rows) == _SCORED_FIELD_COUNT + 1
        assert all(r["accuracy"] == 100.0 for r in rows)
        assert rows[-1]["label"] == OVERALL_LABEL

    def test_single_field_mismatch_zeroes_that_field_only(self):
        # Arrange — wrong total amount (a non-key field), everything else correct.
        gt = pd.DataFrame([_gt_row()])
        proc = pd.DataFrame([_proc_row(TOTAL_AMOUNT=Decimal("999.99"))])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert
        assert by_label["Total Amount"]["accuracy"] == 0.0
        assert by_label["Tax Invoice Number"]["accuracy"] == 100.0

    def test_overall_is_micro_average_across_fields(self):
        # Arrange — one wrong non-key field out of 23 over a single document.
        gt = pd.DataFrame([_gt_row()])
        proc = pd.DataFrame([_proc_row(TOTAL_AMOUNT=Decimal("999.99"))])

        # Act
        overall = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))[OVERALL_LABEL]

        # Assert — 22 correct of 23 field-comparisons.
        assert overall["accuracy"] == round(22 / 23 * 100, 2)

    def test_key_field_mismatch_unpairs_row_and_zeroes_all_fields(self):
        # Arrange — the tax invoice number is part of the pairing key, so getting it wrong
        # means the extraction line cannot be matched to the GT line at all.
        gt = pd.DataFrame([_gt_row()])
        proc = pd.DataFrame([_proc_row(TAX_INVOICE_NUMBER="WRONG-999")])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — the unpaired GT row counts as incorrect on every field.
        assert by_label["Tax Invoice Number"]["accuracy"] == 0.0
        assert by_label["Buyer Name"]["accuracy"] == 0.0
        assert by_label[OVERALL_LABEL]["accuracy"] == 0.0

    def test_blank_gt_with_hallucinated_value_scores_incorrect(self):
        # Arrange — ground truth has no buyer name, but the model invented one in the Thai column
        # while the English column stayed null. Buyer Name matches against EITHER column, so the null
        # sibling puts the blank sentinel into the candidate set; it must not let the hallucination
        # match the blank label.
        gt = pd.DataFrame([_gt_row(buyer_name=None)])
        proc = pd.DataFrame([_proc_row(BUYER_NAME_TH="บริษัท ไม่มีจริง จำกัด", BUYER_NAME_ENG=None)])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — a value where ground truth says there is none is incorrect.
        assert by_label["Buyer Name"]["accuracy"] == 0.0

    def test_blank_gt_with_blank_extraction_scores_correct(self):
        # Arrange — ground truth leaves the buyer name blank and the model extracted nothing in
        # EITHER language column.
        gt = pd.DataFrame([_gt_row(buyer_name=None)])
        proc = pd.DataFrame([_proc_row(BUYER_NAME_TH=None, BUYER_NAME_ENG=None)])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — correctly writing nothing is a match under the correct-vs-incorrect rule.
        assert by_label["Buyer Name"]["accuracy"] == 100.0

    def test_multi_line_file_scores_each_invoice_line(self):
        # Arrange — one file, two invoice lines; extraction returns them in reverse order.
        gt = pd.DataFrame(
            [
                _gt_row(invoice_number="INV-A", invoice_amount="100.00"),
                _gt_row(invoice_number="INV-B", invoice_amount="200.00"),
            ]
        )
        proc = pd.DataFrame(
            [
                _proc_row(INVOICE_NUMBER="INV-B", INVOICE_AMOUNT=Decimal("200.00")),
                _proc_row(INVOICE_NUMBER="INV-A", INVOICE_AMOUNT=Decimal("100.00")),
            ]
        )

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — every line pairs with its own extraction row, regardless of order.
        assert by_label["Invoice Number"]["accuracy"] == 100.0
        assert by_label["Invoice Amount"]["accuracy"] == 100.0

    def test_multi_document_file_scores_each_document(self):
        # Arrange — one file holding two tax-invoice documents with different totals.
        gt = pd.DataFrame(
            [
                _gt_row(tax_invoice_number="DOC-1", total_amount="100.00"),
                _gt_row(tax_invoice_number="DOC-2", total_amount="500.00"),
            ]
        )
        proc = pd.DataFrame(
            [
                _proc_row(TAX_INVOICE_NUMBER="DOC-2", TOTAL_AMOUNT=Decimal("500.00")),
                _proc_row(TAX_INVOICE_NUMBER="DOC-1", TOTAL_AMOUNT=Decimal("100.00")),
            ]
        )

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — each document scores against its own extraction row, not the file's first.
        assert by_label["Tax Invoice Number"]["accuracy"] == 100.0
        assert by_label["Total Amount"]["accuracy"] == 100.0

    def test_missed_line_counts_as_incorrect_on_all_fields(self):
        # Arrange — GT has two invoice lines; the extraction only produced one.
        gt = pd.DataFrame(
            [
                _gt_row(invoice_number="INV-A", invoice_amount="100.00"),
                _gt_row(invoice_number="INV-B", invoice_amount="200.00"),
            ]
        )
        proc = pd.DataFrame([_proc_row(INVOICE_NUMBER="INV-A", INVOICE_AMOUNT=Decimal("100.00"))])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — the missed line is one FP on every field: 1 of 2 correct.
        assert by_label["Invoice Number"]["accuracy"] == 50.0
        assert by_label["Buyer Name"]["accuracy"] == 50.0
        assert by_label[OVERALL_LABEL]["accuracy"] == 50.0

    def test_buyer_name_matches_english_extraction_column(self):
        # Arrange — GT holds the English name; extraction put it in the ENG column, TH is blank.
        gt = pd.DataFrame([_gt_row(buyer_name="Test Co Ltd")])
        proc = pd.DataFrame([_proc_row(BUYER_NAME_TH=None, BUYER_NAME_ENG="Test Co Ltd")])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert
        assert by_label["Buyer Name"]["accuracy"] == 100.0

    def test_blank_gt_and_null_extraction_count_as_match(self):
        # Arrange — no withholding tax on either side.
        gt = pd.DataFrame([_gt_row(withholding_tax=None)])
        proc = pd.DataFrame([_proc_row(WITHHOLDING_TAX=None)])

        # Act
        by_label = _metrics_by_label(FactCheckEvaluator().evaluate(proc, gt))

        # Assert — both "N/A" -> agree -> true positive.
        assert by_label["Withholding Tax"]["accuracy"] == 100.0

    def test_no_file_match_returns_empty(self):
        # Arrange — file names do not overlap.
        gt = pd.DataFrame([_gt_row(file_name="a.pdf")])
        proc = pd.DataFrame([_proc_row(FILE_NAME="b.pdf")])

        # Act / Assert
        assert FactCheckEvaluator().evaluate(proc, gt) == []

    def test_join_ignores_extension_and_case(self):
        # Arrange — same stem, different extension + case.
        gt = pd.DataFrame([_gt_row(file_name="Invoice_001.PDF")])
        proc = pd.DataFrame([_proc_row(FILE_NAME="invoice_001.pdf")])

        # Act
        rows = FactCheckEvaluator().evaluate(proc, gt)

        # Assert — matched and scored.
        assert rows and rows[-1]["accuracy"] == 100.0
