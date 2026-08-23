"""Tests for the batch-run monitoring primitives: token usage, latency, success rate.

The usage fixture is the body of a real Gemini ``usageMetadata`` block, camelCase and all, rather
than the snake_case dict the SDK model exposes. That is deliberate and is the whole point of the
extraction step: the JSONL carries camelCase, and a snake_case fixture would pass while every real
prediction row returned nothing.
"""

from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest

from src.google_model.sentiment.schema.metrics_schema import MetricsSchema
from src.google_model.usage_metrics import (
    USAGE_FIELDS,
    USAGE_TOTALS,
    batch_latency,
    build_summary_df,
    extract_usage,
    plan_row_order,
    summarize_usage,
)

# The real block, copied from a Gemini batch prediction row. Copied rather than imported: its
# other home is a scratch script at the repo root, which is not a test dependency.
REAL_USAGE = {
    "cacheTokensDetails": [
        {"modality": "AUDIO", "tokenCount": 12441},
        {"modality": "TEXT", "tokenCount": 19372},
    ],
    "cachedContentTokenCount": 31813,
    "candidatesTokenCount": 2825,
    "candidatesTokensDetails": [{"modality": "TEXT", "tokenCount": 2825}],
    "promptTokenCount": 50757,
    "promptTokensDetails": [
        {"modality": "TEXT", "tokenCount": 30907},
        {"modality": "AUDIO", "tokenCount": 19850},
    ],
    "thoughtsTokenCount": 6778,
    "totalTokenCount": 60360,
    "trafficType": "ON_DEMAND",
}


def make_item(usage=None, **response):
    """A prediction row carrying a response envelope, which is where usageMetadata lives."""
    body = dict(response)
    if usage is not None:
        body["usageMetadata"] = usage
    return {"request": {}, "response": body}


def make_metrics_df(*rows):
    """A validated metrics frame from partial row dicts, so a test states only what it is about."""
    blank = {
        "No": None,
        "Voice File Name": "x.wav",
        "Status": "Failed",
        "Error Type": "",
        **dict.fromkeys(USAGE_TOTALS.values()),
        "Prompt Modalities": None,
        "Traffic Type": None,
    }
    columns = list(MetricsSchema.to_schema().columns)
    records = [{**blank, **row} for row in rows]
    return MetricsSchema.validate(pd.DataFrame(records, columns=columns))


class TestExtractUsage:
    def test_maps_every_field_from_a_real_block(self):
        usage = extract_usage(make_item(REAL_USAGE))

        assert usage["prompt_tokens"] == 50757
        assert usage["cached_tokens"] == 31813
        assert usage["candidates_tokens"] == 2825
        assert usage["thoughts_tokens"] == 6778
        assert usage["total_tokens"] == 60360
        assert usage["traffic_type"] == "ON_DEMAND"

    def test_breaks_the_prompt_down_by_modality(self):
        usage = extract_usage(make_item(REAL_USAGE))

        assert usage["prompt_text_tokens"] == 30907
        assert usage["prompt_audio_tokens"] == 19850
        assert usage["prompt_modalities"] == "AUDIO, TEXT"
        # A modality this request did not carry is blank, not zero -- the API reported no entry,
        # which is a different fact from an entry reading 0.
        assert usage["prompt_image_tokens"] is None
        assert usage["prompt_video_tokens"] is None
        assert usage["prompt_document_tokens"] is None
        assert usage["prompt_other_tokens"] is None

    def test_the_modality_columns_sum_to_the_prompt_total(self):
        usage = extract_usage(make_item(REAL_USAGE))

        parts = sum(
            usage[key] or 0 for key in USAGE_TOTALS if key.startswith("prompt_") and key != "prompt_tokens"
        )
        assert parts == usage["prompt_tokens"]

    def test_the_components_sum_to_the_reported_total(self):
        usage = extract_usage(make_item(REAL_USAGE))

        # The identity a reader uses to sanity-check the sheet. Asserted on the real block so a
        # future SDK change to what totalTokenCount includes shows up here.
        assert (
            usage["prompt_tokens"] + usage["candidates_tokens"] + usage["thoughts_tokens"]
            == usage["total_tokens"]
        )

    def test_cached_tokens_are_a_subset_of_the_prompt(self):
        usage = extract_usage(make_item(REAL_USAGE))

        assert usage["cached_tokens"] <= usage["prompt_tokens"]

    def test_enums_arrive_as_plain_strings(self):
        usage = extract_usage(make_item(REAL_USAGE))

        # str(MediaModality.AUDIO) is "MediaModality.AUDIO"; a leaked enum member would put that
        # in a cell, and pandera's coerce=True on a str column would not catch it.
        assert type(usage["traffic_type"]) is str
        assert type(usage["prompt_modalities"]) is str
        assert "MediaModality" not in usage["prompt_modalities"]

    def test_returns_every_declared_field(self):
        # The guard against build_metrics_df's indexed access raising on a field this forgot.
        assert set(extract_usage(make_item(REAL_USAGE))) == set(USAGE_FIELDS)


class TestExtractUsageDegradesQuietly:
    """A counter the SDK rejects must never cost a good response its row in the output sheet."""

    def test_a_vertex_status_row_has_no_usage_and_does_not_raise(self):
        # A row Vertex failed carries "status" instead of "response".
        assert extract_usage({"status": "quota exceeded"}) == dict.fromkeys(USAGE_FIELDS)

    def test_a_response_without_usage_metadata_yields_all_none(self):
        assert extract_usage(make_item(candidates=[])) == dict.fromkeys(USAGE_FIELDS)

    def test_a_wrongly_typed_counter_yields_all_none_rather_than_raising(self):
        assert extract_usage(make_item({"promptTokenCount": "lots"})) == dict.fromkeys(
            USAGE_FIELDS
        )

    def test_top_level_usage_metadata_is_not_read(self):
        # The block lives under "response". Reading the top level produced an all-blank Matrix
        # that looked like "the model reported nothing" rather than "we looked in the wrong place".
        assert extract_usage({"usageMetadata": REAL_USAGE}) == dict.fromkeys(USAGE_FIELDS)

    def test_a_missing_total_is_not_synthesized_from_the_components(self):
        usage = extract_usage(
            make_item(
                {"promptTokenCount": 10, "candidatesTokenCount": 5, "thoughtsTokenCount": 2}
            )
        )

        # The sheet must say what the API charged for. A synthesized 17 would silently disagree
        # with the billing export on exactly the rows worth investigating.
        assert usage["total_tokens"] is None
        assert usage["prompt_tokens"] == 10


class TestModalityBreakdown:
    def test_repeated_modalities_are_summed_not_overwritten(self):
        usage = extract_usage(
            make_item(
                {
                    "promptTokensDetails": [
                        {"modality": "AUDIO", "tokenCount": 100},
                        {"modality": "AUDIO", "tokenCount": 25},
                    ]
                }
            )
        )

        # details[0] would report 100; last-wins would report 25.
        assert usage["prompt_audio_tokens"] == 125

    def test_an_entry_with_no_count_is_skipped_not_zeroed(self):
        usage = extract_usage(
            make_item({"promptTokensDetails": [{"modality": "IMAGE", "tokenCount": None}]})
        )

        assert usage["prompt_image_tokens"] is None

    def test_an_unrecognised_modality_lands_in_other_rather_than_being_lost(self):
        usage = extract_usage(
            make_item(
                {
                    "promptTokenCount": 40,
                    "promptTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 30},
                        {"modality": "HOLOGRAM", "tokenCount": 10},
                    ],
                }
            )
        )

        # Folded rather than dropped, so the six prompt columns still add up to Prompt Tokens
        # when a modality arrives that this table predates -- and named, so it is findable.
        assert usage["prompt_text_tokens"] == 30
        assert usage["prompt_other_tokens"] == 10
        assert usage["prompt_text_tokens"] + usage["prompt_other_tokens"] == 40
        assert "HOLOGRAM" in usage["prompt_modalities"]

    def test_a_documents_style_payload_fills_the_document_column(self):
        usage = extract_usage(
            make_item(
                {
                    "promptTokensDetails": [
                        {"modality": "TEXT", "tokenCount": 900},
                        {"modality": "DOCUMENT", "tokenCount": 1600},
                    ]
                }
            )
        )

        # One schema serves both pipelines: the voice run fills AUDIO and this one DOCUMENT.
        assert usage["prompt_document_tokens"] == 1600
        assert usage["prompt_audio_tokens"] is None


class TestSummarizeUsage:
    def test_totals_include_the_tokens_failed_rows_burned(self):
        summary = summarize_usage(
            make_metrics_df(
                {"Status": "Success", "Total Tokens": 100, "Prompt Tokens": 80},
                {"Status": "Failed", "Total Tokens": 60, "Prompt Tokens": 50},
            )
        )

        # Excluding the failure would under-report spend by exactly the rows worth investigating.
        assert summary["total_tokens"] == 160
        assert summary["prompt_tokens"] == 130
        assert summary["succeeded"] == 1
        assert summary["failed"] == 1
        assert summary["success_rate"] == 0.5

    def test_an_empty_frame_reports_zeros_rather_than_raising(self):
        summary = summarize_usage(make_metrics_df())

        assert summary["files"] == 0
        assert summary["success_rate"] == 0.0
        assert summary["avg_total_tokens"] == 0.0
        assert summary["total_tokens"] == 0
        assert summary["traffic_types"] == []

    def test_an_all_failed_run_still_reports_the_spend(self):
        summary = summarize_usage(
            make_metrics_df(
                {"Status": "Failed", "Total Tokens": 100},
                {"Status": "Failed", "Total Tokens": 40},
            )
        )

        assert summary["success_rate"] == 0.0
        assert summary["total_tokens"] == 140

    def test_averages_divide_by_the_rows_that_reported_usage(self):
        summary = summarize_usage(
            make_metrics_df(
                {"Total Tokens": 100},
                {"Total Tokens": 300},
                # Two files that never came back, so nothing was ever reported for them.
                {},
                {},
            )
        )

        # 400 / 2, not 400 / 4: dividing by the row count would halve the reported average on a
        # run where half the files never returned.
        assert summary["files"] == 4
        assert summary["files_with_usage"] == 2
        assert summary["avg_total_tokens"] == 200.0

    def test_traffic_types_and_modalities_are_sorted_deduplicated_and_null_free(self):
        summary = summarize_usage(
            make_metrics_df(
                {"Traffic Type": "ON_DEMAND", "Prompt Modalities": "TEXT, AUDIO"},
                {"Traffic Type": "ON_DEMAND", "Prompt Modalities": "AUDIO"},
                {"Traffic Type": "PROVISIONED_THROUGHPUT", "Prompt Modalities": None},
                {},
            )
        )

        # A mixed run -- some rows on provisioned throughput, some on demand -- is what this
        # reveals, and it changes which rate card applies to the token totals beside it.
        assert summary["traffic_types"] == ["ON_DEMAND", "PROVISIONED_THROUGHPUT"]
        assert summary["prompt_modalities"] == ["AUDIO", "TEXT"]

    def test_every_value_is_a_builtin_structlog_can_render(self):
        summary = summarize_usage(make_metrics_df({"Total Tokens": 10}))

        for key, value in summary.items():
            assert type(value) in (int, float, list), f"{key} is {type(value).__name__}"


class TestBatchLatency:
    START = datetime(2026, 8, 7, 10, 0, tzinfo=UTC)

    def test_splits_the_wall_clock_into_queue_and_run(self):
        latency = batch_latency(
            self.START,
            self.START + timedelta(seconds=45),
            self.START + timedelta(minutes=30),
        )

        assert latency["queue_seconds"] == 45.0
        assert latency["run_seconds"] == 1755.0
        assert latency["wall_seconds"] == 1800.0

    def test_a_job_that_never_ran_has_a_wall_but_no_run(self):
        latency = batch_latency(self.START, None, self.START + timedelta(seconds=90))

        # None rather than 0.0: a zero-second run is a claim, and a blank cell is the truth.
        assert latency["queue_seconds"] is None
        assert latency["run_seconds"] is None
        assert latency["wall_seconds"] == 90.0

    def test_a_missing_terminal_timestamp_leaves_both_durations_blank(self):
        latency = batch_latency(self.START, self.START + timedelta(seconds=10), None)

        assert latency["queue_seconds"] == 10.0
        assert latency["run_seconds"] is None
        assert latency["wall_seconds"] is None

    def test_clock_skew_is_blank_rather_than_negative(self):
        latency = batch_latency(self.START, None, self.START - timedelta(seconds=5))

        assert latency["wall_seconds"] is None

    def test_a_naive_aware_mix_does_not_kill_the_run(self):
        # This runs at the very end of a paid batch job; a TypeError here would lose the sheet.
        latency = batch_latency(self.START, None, datetime(2026, 8, 7, 11, 0))

        assert latency["wall_seconds"] is None


class TestBuildSummaryDf:
    def build(self, **overrides):
        kwargs = {
            "run_id": "2026-08-07_10-00-00",
            "model": "gemini-2.5-flash",
            "job_name": "projects/p/locations/global/batchPredictionJobs/1",
            "job_state": "JOB_STATE_SUCCEEDED",
            "counts": {"source_files": 10, "submitted": 10, "line_errors": 0},
            "usage": summarize_usage(make_metrics_df({"Status": "Success", "Total Tokens": 10})),
            "latency": {
                "queue_seconds": 1.0,
                "run_seconds": 20.0,
                "wall_seconds": 21.0,
                "payload_ms": 5.0,
                "batch_ms": 6.0,
                "results_ms": 7.0,
            },
        }
        kwargs.update(overrides)
        return build_summary_df(**kwargs)

    def test_is_a_two_column_metric_value_frame(self):
        assert list(self.build().columns) == ["Metric", "Value"]

    @pytest.mark.parametrize(
        "metric",
        [
            "Run ID",
            "Model",
            "Job State",
            "Traffic Type",
            "Prompt Modalities",
            "Submitted Rows",
            "Prediction Rows",
            "Succeeded",
            "Failed",
            "Success Rate",
            "Files With Usage",
            "Prompt Tokens",
            "Prompt Audio Tokens",
            "Prompt Document Tokens",
            "Total Tokens",
            "Queue Seconds",
            "Batch Run Seconds",
            "Seconds / File",
        ],
        ids=lambda metric: metric.replace(" ", "_"),
    )
    def test_carries_the_metric(self, metric):
        # A fixed list, so a rename cannot silently drop a metric from the delivered sheet.
        assert metric in list(self.build()["Metric"])

    def test_an_unmeasured_latency_stays_blank_rather_than_the_string_none(self):
        df = self.build(
            latency={
                "queue_seconds": None,
                "run_seconds": None,
                "wall_seconds": None,
                "payload_ms": 5.0,
                "batch_ms": 6.0,
                "results_ms": 7.0,
            }
        )
        values = dict(zip(df["Metric"], df["Value"]))

        assert values["Queue Seconds"] is None
        assert values["Seconds / File"] is None

    def test_seconds_per_file_is_blank_when_there_are_no_files(self):
        df = self.build(usage=summarize_usage(make_metrics_df()))
        values = dict(zip(df["Metric"], df["Value"]))

        assert values["Seconds / File"] is None
        assert values["Success Rate"] == 0.0


class TestPlanRowOrder:
    def test_the_submitted_set_defines_the_rows_sorted(self):
        order, counts = plan_row_order(["c", "a", "b"], {"a": 1})

        assert order == ["a", "b", "c"]
        assert counts["missing"] == 2

    def test_a_parsed_name_that_was_never_submitted_is_appended(self):
        order, counts = plan_row_order(["a"], {"a": 1, "zz": 2})

        # Appended rather than dropped: an unexplained row is a fault to look at.
        assert order == ["a", "zz"]
        assert counts["extra"] == 1

    def test_duplicate_submitted_names_collapse_and_are_counted(self):
        order, counts = plan_row_order(["a", "a", "b"], {})

        assert order == ["a", "b"]
        assert counts["duplicates"] == 1

    def test_a_clean_run_reports_no_disagreement(self):
        order, counts = plan_row_order(["a", "b"], {"a": 1, "b": 2})

        assert order == ["a", "b"]
        assert counts == {"duplicates": 0, "missing": 0, "extra": 0}
