"""Tests for the document pipeline's model-response -> output-sheet mapping.

Every page fixture is built by running a real ``ReceiptExtraction.model_validate(...)`` and
dumping it, rather than hand-writing the dict the function will read. That is deliberate: the
dump renders money as *strings* and dates as ISO strings, and a hand-written fixture full of
``Decimal`` objects would pass while the real batch output failed.
"""

from decimal import Decimal

import pandas as pd
import pytest
from pandera.errors import SchemaErrors

from src.google_model.documents.google_output import (
    _coerce_for_validation,
    _output_rename_map,
    _page_sort_key,
    _to_bool,
    build_metrics_df,
    build_output_df,
)
from src.google_model.documents.schema.input_gt_schema import InputGTSchema
from src.google_model.documents.schema.metrics_schema import MetricsSchema
from src.google_model.documents.schema.model_response import ReceiptExtraction
from src.google_model.documents.schema.output_schema import OutputSchema
from src.google_model.documents.schema.processing_schema import ProcessingSchema
from src.google_model.usage_metrics import USAGE_FIELDS


def make_line(invoice_number=None, before_vat=None, vat=None):
    """One line item, with the four no-default fields always present."""
    return {
        "INVOICE_NUMBER": invoice_number,
        "INVOICE_AMOUNT_BEFORE_VAT": before_vat,
        "INVOICE_VAT_AMOUNT": vat,
        "INVOICE_AMOUNT_AFTER_VAT": None,
    }


def make_page(**overrides):
    """A dumped ``ReceiptExtraction``, exactly as ``run()`` hands it to ``build_output_df``."""
    payload = {
        "DOC_NAME": "ใบกำกับภาษี",
        "DOC_TYPE": "TaxInvoice",
        "TAX_INVOICE_NUMBER": "TX-0007",
        "TAX_INVOICE_DATE": "2026-01-05",
        "VENDOR_TAX_ID": "0105500000001",
        "CUSTOMER_TAX_ID": "0105500000002",
        "BEFORE_VAT_AMOUNT": "100.00",
        "VAT_AMOUNT": "7.00",
        "AFTER_VAT_AMOUNT": "107.00",
        "WITHHOLDING_TAX_AMOUNT": "3.00",
        "NET_AMOUNT": "104.00",
        "line_items": [],
    }
    payload.update(overrides)
    return ReceiptExtraction.model_validate(payload).model_dump(mode="json")


def make_item(unique_name="doc_p1.pdf", file_name="doc.pdf", **overrides):
    return {
        "unique_name": unique_name,
        "file_name": file_name,
        "content": make_page(**overrides),
    }


def make_usage_row(unique_name="doc_p1.pdf", status="Success", error_type="", **overrides):
    """One ``usage_rows`` record, as ``run()`` assembles it after the item loop."""
    return {
        "unique_name": unique_name,
        "status": status,
        "error_type": error_type,
        **dict.fromkeys(USAGE_FIELDS),
        **overrides,
    }


def build(items, submitted=None, metadata=None):
    """``build_output_df`` with the submitted set defaulted to the items' own pages.

    Every test predating the row-parity change asserts on the *parsed* rows, and defaulting here
    keeps them asserting exactly that. The parity behaviour -- a submitted page that never parsed
    -- is what :class:`TestPageParity` passes an explicit ``submitted`` for.
    """
    if submitted is None:
        submitted = [item["unique_name"] for item in items]
    if metadata is None:
        metadata = {item["unique_name"]: item["file_name"] for item in items}
    return build_output_df(items, submitted, metadata)


class TestRowGrain:
    def test_one_row_per_printed_line_item_carried_verbatim(self):
        df = build(
            [
                make_item(
                    line_items=[
                        make_line("A", "5.50", "0.39"),
                        make_line("A", "4.50", None),
                        make_line("B", "20.00", "1.40"),
                    ]
                )
            ]
        )

        assert len(df) == 3
        # Every printed line keeps its own amount -- the two 'A' rows are not merged into 10.00.
        assert list(df["Invoice number"]) == ["A", "A", "B"]
        assert list(df["Invoice amount"]) == [
            Decimal("5.50"),
            Decimal("4.50"),
            Decimal("20.00"),
        ]
        # The page-level values repeat across all three of the page's rows.
        assert list(df["Total amount"]) == [Decimal("100.00")] * 3
        assert list(df["Tax invoice number"]) == ["TX-0007"] * 3

    def test_lines_keep_printed_order(self):
        df = build(
            [make_item(line_items=[make_line("Z"), make_line("A"), make_line("Z")])]
        )

        # As returned by the model, which is as printed -- never sorted or deduplicated.
        assert list(df["Invoice number"]) == ["Z", "A", "Z"]

    def test_page_with_no_line_items_still_emits_one_row(self):
        df = build([make_item(line_items=[])])

        assert len(df) == 1
        assert df["Invoice number"].isna().all()
        assert df["Invoice amount"].isna().all()
        assert df["Vat invoice"].isna().all()
        # The page-level extraction is still there -- only the line columns are blank.
        assert df.loc[0, "Total amount"] == Decimal("100.00")

    def test_a_line_without_an_invoice_number_keeps_its_row(self):
        df = build(
            [make_item(line_items=[make_line("A", "1.00"), make_line(None, "2.00")])]
        )

        assert len(df) == 2
        assert df["Invoice number"].isna().sum() == 1
        assert list(df["Invoice amount"]) == [Decimal("1.00"), Decimal("2.00")]

    def test_a_line_with_no_printed_vat_is_null_not_zero(self):
        df = build([make_item(line_items=[make_line("A", "1.00", None)])])

        # A blank cell, not a printed 0.00 -- the distinction is what the evaluator scores.
        assert pd.isna(df.loc[0, "Vat invoice"])
        assert df.loc[0, "Invoice amount"] == Decimal("1.00")


class TestFieldMapping:
    def test_maps_every_renamed_column(self):
        df = build([make_item(line_items=[make_line("A", "1.00", "0.07")])])
        row = df.loc[0]

        assert row["File name"] == "doc.pdf"
        assert row["Document name"] == "ใบกำกับภาษี"
        assert row["Document type"] == "TaxInvoice"
        # total_amount is BEFORE_VAT_AMOUNT, not the after-VAT total.
        assert row["Total amount"] == Decimal("100.00")
        assert row["Vat amount"] == Decimal("7.00")
        assert row["Net amount"] == Decimal("104.00")
        assert row["Withholding tax"] == Decimal("3.00")
        assert row["Vendor tax id"] == "0105500000001"
        assert row["Buyer tax id"] == "0105500000002"

    def test_buyer_company_code_is_absent_from_the_sheet(self):
        # Dropped from all three schemas: it was never extracted from the page by any prompt
        # field, so an all-null column only ever scored the model 0% for an unimplemented lookup.
        df = build([make_item()])

        assert "Buyer company code" not in df.columns
        assert "CUSTOMER_COMPANY_CODE" not in ProcessingSchema.__annotations__

    def test_after_vat_amount_is_not_carried_to_the_sheet(self):
        # ProcessingSchema is the filter: AFTER_VAT_AMOUNT is extracted but deliberately unused.
        assert "AFTER_VAT_AMOUNT" not in ProcessingSchema.__annotations__

    def test_flags_survive_as_nullable_booleans(self):
        df = build([make_item(COPY=True, STAMP=None)])

        assert df["Copy"].dtype == "boolean"
        assert bool(df.loc[0, "Copy"]) is True
        # An explicit null flag is coerced to False by the model, never left blank.
        assert bool(df.loc[0, "Stamp"]) is False


class TestIdentity:
    def test_unique_identifier_is_page_and_printed_position(self):
        df = build([make_item(line_items=[make_line("A"), make_line(None)])])

        assert list(df["Unique identifier"]) == ["doc_p1.pdf#1", "doc_p1.pdf#2"]

    def test_a_page_with_no_line_items_is_still_position_one(self):
        df = build([make_item(line_items=[])])

        assert list(df["Unique identifier"]) == ["doc_p1.pdf#1"]

    def test_identifier_does_not_depend_on_the_extracted_invoice_number(self):
        # The key stays put when the model misreads the number, so the join survives it and
        # the mistake scores as one wrong field rather than two unmatched rows.
        printed = build([make_item(line_items=[make_line("INV-0098")])])
        misread = build([make_item(line_items=[make_line("INV-OO98")])])

        assert list(printed["Unique identifier"]) == list(misread["Unique identifier"])

    def test_unique_identifier_is_unique_per_row(self):
        df = build(
            [
                make_item("doc_p1.pdf", line_items=[make_line("A"), make_line("A")]),
                make_item("doc_p2.pdf", line_items=[make_line("A")]),
            ]
        )

        # Two identical invoice numbers on one page still get distinct keys -- position is what
        # separates them.
        assert df["Unique identifier"].is_unique

    def test_numbering_follows_natural_page_order(self):
        df = build(
            [
                make_item("doc_p10.pdf", line_items=[make_line("A")]),
                make_item("doc_p2.pdf", line_items=[make_line("B")]),
            ]
        )

        assert list(df["No"]) == [1, 2]
        # p2 before p10: a lexicographic sort would have reversed them.
        assert list(df["Unique identifier"]) == ["doc_p2.pdf#1", "doc_p10.pdf#1"]

    def test_page_sort_key_orders_numbers_numerically(self):
        assert _page_sort_key("a_p2.pdf") < _page_sort_key("a_p10.pdf")
        # Unlike shapes stay comparable rather than raising on int-vs-str.
        assert sorted(["a.pdf", "2.pdf", "a_p1.pdf"], key=_page_sort_key) == [
            "2.pdf",
            "a.pdf",
            "a_p1.pdf",
        ]


class TestPageParity:
    """A submitted page reaches the sheet whether or not the model returned anything for it.

    The scorers join this sheet to the ground truth with ``how="inner"`` and nothing downstream
    checks row counts, so a dropped page does not misalign anything -- it quietly reduces N on
    every metric while the dashboard still reads as complete. These are the tests that keep
    "300 pages in" producing "300 pages out".
    """

    def test_a_page_that_never_parsed_still_gets_one_row(self):
        df = build(
            [make_item("doc_p1.pdf", line_items=[make_line("A")])],
            submitted=["doc_p1.pdf", "doc_p2.pdf"],
            metadata={"doc_p1.pdf": "doc.pdf", "doc_p2.pdf": "doc.pdf"},
        )

        assert len(df) == 2
        assert list(df["Unique identifier"]) == ["doc_p1.pdf#1", "doc_p2.pdf#1"]

    def test_a_failed_page_keeps_its_source_document_and_nothing_else(self):
        df = build(
            [],
            submitted=["doc_p1.pdf"],
            metadata={"doc_p1.pdf": "doc.pdf"},
        )
        row = df.loc[0]

        # File name comes from split_doc, which knows it regardless of what the model did; it is
        # the one column a failure can honestly populate.
        assert row["File name"] == "doc.pdf"
        assert row["Unique identifier"] == "doc_p1.pdf#1"
        # Everything the model would have extracted is blank -- never a sentinel. The scorers
        # compare raw cell text, and 'N/A' is a real grader-entered value in this sheet.
        extracted = df.drop(columns=["No", "Unique identifier", "File name"])
        assert extracted.isna().all().all()

    def test_a_page_missing_from_the_split_metadata_still_gets_its_row(self):
        df = build([], submitted=["ghost_p1.pdf"], metadata={})

        assert len(df) == 1
        assert pd.isna(df.loc[0, "File name"])

    def test_no_parsed_pages_yields_a_full_sheet_not_an_empty_one(self):
        submitted = [f"doc_p{n}.pdf" for n in range(1, 11)]
        df = build([], submitted=submitted, metadata=dict.fromkeys(submitted, "doc.pdf"))

        # The case the whole change exists to make visible: a run that returned nothing usable
        # used to ship a 0-row sheet, which scored as "nothing to compare" rather than "all bad".
        assert len(df) == 10
        assert list(df["No"]) == list(range(1, 11))

    def test_parity_holds_while_a_parsed_page_still_expands_to_its_line_items(self):
        df = build(
            [make_item("doc_p1.pdf", line_items=[make_line("A"), make_line("B")])],
            submitted=["doc_p1.pdf", "doc_p2.pdf"],
            metadata={"doc_p1.pdf": "doc.pdf", "doc_p2.pdf": "doc.pdf"},
        )

        # Parity is at-least-one-row-per-page, not one-row-per-page: the grain is line items.
        assert len(df) == 3
        assert set(df["Unique identifier"]) == {
            "doc_p1.pdf#1",
            "doc_p1.pdf#2",
            "doc_p2.pdf#1",
        }

    def test_blank_pages_keep_the_natural_page_order(self):
        df = build(
            [make_item("doc_p2.pdf", line_items=[make_line("A")])],
            submitted=["doc_p10.pdf", "doc_p2.pdf", "doc_p1.pdf"],
            metadata={},
        )

        # p2 before p10 across the mixed parsed/blank set -- a lexicographic sort would reverse
        # them, and a sort applied only to the parsed pages would leave the blanks trailing.
        assert list(df["Unique identifier"]) == [
            "doc_p1.pdf#1",
            "doc_p2.pdf#1",
            "doc_p10.pdf#1",
        ]

    def test_a_parsed_page_that_was_never_submitted_is_appended_not_dropped(self):
        df = build(
            [make_item("stale_p1.pdf", line_items=[make_line("A")])],
            submitted=["doc_p1.pdf"],
            metadata={"doc_p1.pdf": "doc.pdf"},
        )

        # An unexplained row is a fault to look at; silently discarding one is how this code
        # lost the failures in the first place.
        assert len(df) == 2
        assert "stale_p1.pdf#1" in list(df["Unique identifier"])

    def test_duplicate_submitted_pages_collapse_to_one_row(self):
        df = build([], submitted=["doc_p1.pdf", "doc_p1.pdf"], metadata={})

        assert len(df) == 1


class TestBuildMetricsDf:
    def test_matrix_covers_every_submitted_page(self):
        submitted = ["doc_p1.pdf", "doc_p2.pdf", "doc_p3.pdf"]
        df = build_metrics_df(
            [make_usage_row("doc_p2.pdf")],
            submitted,
            dict.fromkeys(submitted, "doc.pdf"),
        )

        assert len(df) == 3
        assert list(df["Page Name"]) == submitted
        assert list(df["Status"]) == ["Failed", "Success", "Failed"]

    def test_a_page_that_never_returned_has_blank_tokens_not_zero(self):
        df = build_metrics_df([], ["doc_p1.pdf"], {"doc_p1.pdf": "doc.pdf"})

        # Blank, not 0: a 0 would be summed into the run's totals as if the page had genuinely
        # cost nothing, and summarize_usage counts notna() to find what was actually reported.
        assert df["Total Tokens"].isna().all()
        assert df["Prompt Tokens"].isna().all()
        # And no error type -- nothing came back to fail.
        assert df.loc[0, "Error Type"] == ""

    def test_a_failed_page_keeps_the_tokens_it_burned(self):
        df = build_metrics_df(
            [
                make_usage_row(
                    "doc_p1.pdf",
                    status="Failed",
                    error_type="ValidationError",
                    prompt_tokens=500,
                    total_tokens=800,
                )
            ],
            ["doc_p1.pdf"],
            {"doc_p1.pdf": "doc.pdf"},
        )

        # A malformed response is the common failure, and those tokens are real spend.
        assert df.loc[0, "Status"] == "Failed"
        assert df.loc[0, "Error Type"] == "ValidationError"
        assert df.loc[0, "Total Tokens"] == 800

    def test_source_document_is_resolved_for_every_page(self):
        df = build_metrics_df(
            [], ["doc_p1.pdf", "ghost_p1.pdf"], {"doc_p1.pdf": "doc.pdf"}
        )

        assert df.loc[0, "File name"] == "doc.pdf"
        assert pd.isna(df.loc[1, "File name"])

    def test_empty_input_yields_a_valid_zero_row_frame(self):
        df = build_metrics_df([], [], {})

        assert len(df) == 0
        assert list(df.columns) == list(MetricsSchema.to_schema().columns)


class TestFrameShape:
    def test_empty_items_yield_a_valid_zero_row_frame(self):
        df = build([])

        assert len(df) == 0
        assert list(df.columns) == list(OutputSchema.to_schema().columns)

    def test_columns_are_the_schema_in_declaration_order(self):
        df = build([make_item()])

        assert list(df.columns) == list(OutputSchema.to_schema().columns)

    def test_rename_map_covers_every_processing_column(self):
        # The guard that catches the two schemas drifting apart -- an uncovered column would
        # otherwise be dropped by the final reindex without a word.
        renames = _output_rename_map()

        for column in ProcessingSchema.to_schema().columns:
            assert column in renames

    @pytest.mark.parametrize(
        ("field", "column"),
        [
            ("INVOICE_AMOUNT_BEFORE_VAT", "invoice_amount"),
            ("INVOICE_VAT_AMOUNT", "vat_invoice"),
            ("WITHHOLDING_TAX_AMOUNT", "withholding_tax"),
        ],
    )
    def test_processing_annotations_are_the_model_field_names(self, field, column):
        # The mapping is name-driven, so a renamed annotation here silently produces an
        # all-null column rather than an error.
        assert ProcessingSchema.__annotations__[field]
        assert ProcessingSchema.to_schema().columns[column] is not None


def make_gt_frame(**columns):
    """A GT sheet as ``run()`` reads it: every cell a string, blanks as NaN.

    Mirrors the ``dtype=str, na_values=[""]`` parse -- the exact shape whose flags and money
    the schema cannot coerce without :func:`_coerce_for_validation`.
    """
    rows = len(next(iter(columns.values()))) if columns else 2
    frame = {
        "No": [str(i + 1) for i in range(rows)],
        "Unique identifier": [f"doc_{i}_p1.pdf#1" for i in range(rows)],
        "File name": [f"doc_{i}.pdf" for i in range(rows)],
        "Document name": ["Tax Invoice"] * rows,
        "Document type": ["Receipt"] * rows,
        "Buyer name th": ["ผู้ซื้อ"] * rows,
        "Buyer address th": ["ที่อยู่"] * rows,
        "Buyer name eng": ["Acme Ltd"] * rows,
        "Buyer address eng": ["1 Test Road Bangkok"] * rows,
        "Buyer tax id": ["0105500000001"] * rows,
        "Buyer branch code": ["00000"] * rows,
        "Buyer branch name": ["สำนักงานใหญ่"] * rows,
        "Vendor name th": ["ผู้ขาย"] * rows,
        "Vendor address th": ["ที่อยู่"] * rows,
        "Vendor name eng": ["Vendor Co Ltd"] * rows,
        "Vendor address eng": ["2 Sample Street Bangkok"] * rows,
        "Vendor tax id": ["0105500000002"] * rows,
        "Vendor branch code": ["00000"] * rows,
        "Vendor branch name": ["สำนักงานใหญ่"] * rows,
        "Tax invoice number": ["INV-001"] * rows,
        "Tax invoice date": ["2026-01-05"] * rows,
        "Total amount": ["100.00"] * rows,
        "Vat amount": ["7.00"] * rows,
        "Net amount": ["107.00"] * rows,
        "Copy": ["False"] * rows,
        "Payee signature flag": ["True"] * rows,
        "Authorized receiver signature flag": ["True"] * rows,
        "Authorized signatory signature flag": ["False"] * rows,
        "Withholding tax": ["3.00"] * rows,
        "Invoice number": ["1"] * rows,
        "Invoice amount": ["100.00"] * rows,
        "Vat invoice": ["7.00"] * rows,
        "Stamp": ["False"] * rows,
    }
    frame.update(columns)
    return pd.DataFrame(frame)


class TestGTCoercion:
    """The Excel-string -> InputGTSchema gate that run() puts the GT sheet through."""

    def test_raw_string_frame_fails_validation(self):
        # The pre-fix crash: pandas' boolean coercion rejects 'True'/'False' strings outright,
        # and pandera collects the coercion failures into SchemaErrors (plural).
        with pytest.raises(SchemaErrors):
            InputGTSchema.validate(make_gt_frame())

    def test_flags_coerce_to_nullable_boolean(self):
        frame = make_gt_frame(**{"Copy": ["True", "False", float("nan")]})

        validated = InputGTSchema.validate(_coerce_for_validation(frame))

        assert validated["Copy"].dtype == "boolean"
        assert list(validated["Copy"][:2]) == [True, False]
        assert pd.isna(validated["Copy"][2])

    def test_money_tolerates_thousands_separators(self):
        frame = make_gt_frame(**{"Total amount": ["1,234.56", "100.00"]})

        validated = InputGTSchema.validate(_coerce_for_validation(frame))

        assert validated["Total amount"][0] == Decimal("1234.56")

    def test_unparsable_money_becomes_none_and_still_validates(self):
        frame = make_gt_frame(**{"Vat amount": ["N/A", "7.00"]})

        validated = InputGTSchema.validate(_coerce_for_validation(frame))

        assert pd.isna(validated["Vat amount"][0])
        assert validated["Vat amount"][1] == Decimal("7.00")

    def test_to_bool_token_set(self):
        assert _to_bool(True) is True
        assert _to_bool(False) is False
        assert _to_bool("true") is True
        assert _to_bool("FALSE") is False
        assert _to_bool("1") is True
        assert _to_bool("0") is False
        assert _to_bool(None) is None
        assert _to_bool(float("nan")) is None
        assert _to_bool("maybe") is None
