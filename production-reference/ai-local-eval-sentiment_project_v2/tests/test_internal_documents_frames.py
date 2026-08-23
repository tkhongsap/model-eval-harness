"""Offline tests for the internal documents pipeline's sheet builders."""

from __future__ import annotations

import pandas as pd
import pytest

from src.google_model.documents.schema.model_response import InvoiceLineItem, ReceiptExtraction
from src.google_model.documents.schema.output_schema import OutputSchema
from src.local_model.documents.internal_direct_output import (
    _METRICS_DEFAULTS,
    build_metrics_df,
    build_output_df,
    build_summary_df,
)
from src.local_model.documents.schema.metrics_schema import MetricsSchema
from tests.helpers_fabricate import fabricate_payload


@pytest.fixture()
def content() -> dict:
    """A fully valid ReceiptExtraction dump, as run() would carry it."""
    payload = fabricate_payload(ReceiptExtraction)
    payload["TAX_INVOICE_NUMBER"] = "TI-001"
    # Line-item fields are required-but-nullable, so each row must carry the full shape.
    payload["line_items"] = [
        {**fabricate_payload(InvoiceLineItem), "ITEM_NO": 1, "INVOICE_NUMBER": "INV-1"},
        {**fabricate_payload(InvoiceLineItem), "ITEM_NO": 2, "INVOICE_NUMBER": "INV-2"},
    ]
    return ReceiptExtraction.model_validate(payload).model_dump(mode="json")


def _success_metrics(name: str) -> dict:
    return {
        "unique_name": name,
        **_METRICS_DEFAULTS,
        "status": "Success",
        "attempts": 1,
        "prompt_tokens": 17000,
        "completion_tokens": 1500,
        "total_tokens": 18500,
    }


METADATA = {"a_p1.png": "a.pdf", "a_p2.png": "a.pdf", "b_p1.jpg": "b.jpg"}


class TestBuildOutputDf:
    def test_line_items_explode_and_failures_stay_blank(self, content):
        items = [{"unique_name": "a_p1.png", "file_name": "a.pdf", "content": content}]
        submitted = ["a_p1.png", "b_p1.jpg"]
        df = build_output_df(items, submitted, METADATA)

        assert list(df.columns) == list(OutputSchema.to_schema().columns)
        # Two line items on the parsed page, one blank row for the failed one.
        assert len(df) == 3
        parsed = df[df["Unique identifier"].isin(["a_p1.png#1", "a_p1.png#2"])]
        # The page-level value repeats across the page's line-item rows; the line-level
        # column differs per row.
        assert parsed["Tax invoice number"].tolist() == ["TI-001", "TI-001"]
        assert parsed["Invoice number"].tolist() == ["INV-1", "INV-2"]
        assert parsed["File name"].tolist() == ["a.pdf", "a.pdf"]

        blank = df[df["Unique identifier"] == "b_p1.jpg#1"].iloc[0]
        # The one column a failure can honestly populate comes from the split metadata.
        assert blank["File name"] == "b.jpg"
        assert pd.isna(blank["Tax invoice number"])

    def test_empty_items_still_yields_full_row_set(self):
        df = build_output_df([], ["a_p1.png", "a_p2.png"], METADATA)
        assert len(df) == 2
        assert df["File name"].tolist() == ["a.pdf", "a.pdf"]

    def test_natural_page_order(self, content):
        # _p10 must sort after _p2, unlike a lexicographic sort.
        submitted = [f"a_p{n}.png" for n in (10, 2, 1)]
        metadata = dict.fromkeys(submitted, "a.pdf")
        df = build_output_df([], submitted, metadata)
        assert df["Unique identifier"].tolist() == ["a_p1.png#1", "a_p2.png#1", "a_p10.png#1"]


class TestBuildMetricsDf:
    def test_absent_rows_fail_with_blank_tokens(self):
        rows = [_success_metrics("a_p1.png")]
        df = build_metrics_df(rows, ["a_p1.png", "b_p1.jpg"], METADATA)

        assert list(df.columns) == list(MetricsSchema.to_schema().columns)
        assert len(df) == 2
        ok = df[df["Page Name"] == "a_p1.png"].iloc[0]
        assert ok["Status"] == "Success"
        assert ok["Total Tokens"] == 18500
        assert ok["File name"] == "a.pdf"

        absent = df[df["Page Name"] == "b_p1.jpg"].iloc[0]
        assert absent["Status"] == "Failed"
        # Blank means "not reported", never 0 -- an Int64 NA, not a coerced zero.
        assert pd.isna(absent["Total Tokens"])
        assert pd.isna(absent["Attempts"])

    def test_failed_page_keeps_attempts_without_tokens(self):
        row = {
            "unique_name": "a_p1.png",
            **_METRICS_DEFAULTS,
            "error_type": "ValidationError",
            "attempts": 3,
        }
        df = build_metrics_df([row], ["a_p1.png"], METADATA)
        record = df.iloc[0]
        assert record["Status"] == "Failed"
        assert record["Error Type"] == "ValidationError"
        assert record["Attempts"] == 3
        assert pd.isna(record["Prompt Tokens"])


class TestBuildSummaryDf:
    def test_summary_matches_metrics_frame(self):
        rows = [
            _success_metrics("a_p1.png"),
            {
                "unique_name": "b_p1.jpg",
                **_METRICS_DEFAULTS,
                "error_type": "APIError",
                "attempts": 3,
            },
        ]
        metrics_df = build_metrics_df(rows, ["a_p1.png", "b_p1.jpg"], METADATA)
        summary_df = build_summary_df(
            run_id="2026-08-10_00-00-00",
            model="gemma-4-12b",
            resumed=False,
            counts={
                "source_files": 2,
                "pages": 2,
                "resumed_pages": 0,
                "extracted_pages": 1,
            },
            metrics_df=metrics_df,
            latency={"files_ms": 1234.5, "results_ms": 10.0},
        )
        values = dict(zip(summary_df["Metric"], summary_df["Value"]))
        assert values["Succeeded"] == 1
        assert values["Failed"] == 1
        assert values["Success Rate"] == 0.5
        assert values["Pages With Usage"] == 1
        assert values["Total Tokens"] == 18500
        assert values["Avg Total Tokens / Page"] == 18500.0
        assert values["Files Elapsed (ms)"] == 1234.5
