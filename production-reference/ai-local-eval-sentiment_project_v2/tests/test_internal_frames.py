"""Offline tests for the internal ASR + LLM pipeline's sheet builders and helpers.

No network and no SDK objects: every function under test is pure, which is what
splitting the labelling call out and dropping the streamed call bought.
"""

from __future__ import annotations

import pandas as pd
import pytest

from src.local_model.sentiment.internal_asr_llm_output import (
    _METRICS_DEFAULTS,
    _asr_payload_valid,
    _build_analysis_content,
    _build_user_content,
    _labelled_transcript_error,
    build_metrics_df,
    build_output_df,
    build_summary_df,
)
from src.local_model.sentiment.schema.metrics_schema import MetricsSchema
from src.local_model.sentiment.schema.model_response import ModelResponse
from src.local_model.sentiment.schema.output_schema import OutputSchema
from tests.helpers_fabricate import fabricate_payload

# The Matrix sheet's contract. Spelled out rather than derived from the schema,
# because build_metrics_df indexes against these exact headers and every column
# is nullable -- so a rename drops the column to all-NA and still validates.
EXPECTED_METRICS_COLUMNS = [
    "No",
    "Voice File Name",
    "Status",
    "Failed Stage",
    "Error Type",
    "ASR Attempts",
    "Label Attempts",
    "Analysis Attempts",
    "Audio Seconds",
    "ASR Segments",
    "Label Prompt Tokens",
    "Label Completion Tokens",
    "Analysis Prompt Tokens",
    "Analysis Completion Tokens",
    "Analysis Reasoning Tokens",
    "Analysis Cached Tokens",
    "Total Tokens",
]


@pytest.fixture()
def content() -> dict:
    """A fully valid ModelResponse dump, as run() would carry it."""
    return ModelResponse.model_validate(fabricate_payload(ModelResponse)).model_dump(mode="json")


@pytest.fixture()
def asr_payload() -> dict:
    """A Whisper ``verbose_json`` dump, shaped like the endpoint's real response.

    Only ``id``/``start``/``end``/``text`` are populated on the segments, and the
    usage block is duration-based -- both are what this endpoint actually returns.
    """
    return {
        "duration": 156.1,
        "language": "th",
        "text": "สวัสดีค่ะ ครับคือเน็ตบ้านผมใช้ไม่ได้",
        "segments": [
            {"id": 0, "start": 0.188, "end": 7.556, "text": "สวัสดีค่ะ", "tokens": None},
            {"id": 1, "start": 8.796, "end": 14.98, "text": "ครับคือเน็ตบ้านผมใช้ไม่ได้", "tokens": None},
        ],
        "usage": {"seconds": 156.1, "type": None, "cost": 0.0039025},
        "words": None,
    }


LABELLED = "Agent: สวัสดีค่ะ\nCustomer: ครับคือเน็ตบ้านผมใช้ไม่ได้"


def _success_metrics(name: str) -> dict:
    return {
        "file_name": name,
        **_METRICS_DEFAULTS,
        "status": "Success",
        "asr_attempts": 1,
        "label_attempts": 1,
        "analysis_attempts": 2,
        "audio_seconds": 156.1,
        "asr_segments": 23,
        "label_prompt_tokens": 1130,
        "label_completion_tokens": 764,
        "label_total_tokens": 1894,
        "analysis_prompt_tokens": 34577,
        "analysis_completion_tokens": 3541,
        "analysis_reasoning_tokens": 11641,
        "analysis_cached_tokens": 0,
        "analysis_total_tokens": 38118,
    }


class TestBuildOutputDf:
    def test_row_per_submitted_file_with_blank_failures(self, content):
        items = [{"file_name": "a_IN.wav", "content": content}]
        submitted = ["a_IN.wav", "b_OUT.wav"]
        df = build_output_df(items, submitted)

        assert list(df.columns) == list(OutputSchema.to_schema().columns)
        assert len(df) == 2
        populated = df[df["Voice File Name"] == "a_IN.wav"].iloc[0]
        blank = df[df["Voice File Name"] == "b_OUT.wav"].iloc[0]
        # The renamed columns are sourced from the schema-spelled keys.
        assert populated["company_verification"] == "Meet"
        assert populated["self_service"] == "Meet"
        # call_type reaches the sheet as the serialized comma string.
        assert populated["call_type"] == "Enquiry"
        assert pd.isna(blank["overall_sentiment"])
        assert pd.isna(blank["call_type"])

    def test_empty_items_still_yields_full_row_set(self):
        df = build_output_df([], ["a_IN.wav"])
        assert len(df) == 1
        assert pd.isna(df.iloc[0]["summary_story"])


class TestModelResponseHasNoTranscript:
    def test_transcript_field_is_gone(self):
        # Load-bearing: the field is produced by a separate schema-free call now,
        # because a description-only spec came back as a verbatim echo of the input.
        assert "transcript" not in ModelResponse.model_fields
        assert "transcript" not in ModelResponse.model_json_schema()["properties"]


class TestMetricsSchemaContract:
    def test_columns_are_the_expected_headers(self):
        assert list(MetricsSchema.to_schema().columns) == EXPECTED_METRICS_COLUMNS


class TestBuildMetricsDf:
    def test_total_is_the_sum_of_two_reported_totals(self):
        rows = [_success_metrics("a_IN.wav")]
        df = build_metrics_df(rows, ["a_IN.wav", "b_OUT.wav"])

        assert list(df.columns) == EXPECTED_METRICS_COLUMNS
        assert len(df) == 2
        ok = df[df["Voice File Name"] == "a_IN.wav"].iloc[0]
        # Each call's own reported total, added together -- NOT recomputed from
        # prompt + completion, and reasoning never enters it. This provider
        # reports a reasoning count larger than the completion count it is
        # nominally part of, so any recomputation would be wrong.
        assert ok["Total Tokens"] == 1894 + 38118
        assert ok["Analysis Reasoning Tokens"] == 11641
        assert ok["Failed Stage"] == ""
        assert ok["Audio Seconds"] == pytest.approx(156.1)

        absent = df[df["Voice File Name"] == "b_OUT.wav"].iloc[0]
        assert absent["Status"] == "Failed"
        assert pd.isna(absent["Total Tokens"])
        assert pd.isna(absent["ASR Attempts"])

    def test_asr_failure_keeps_its_row_and_names_the_stage(self):
        row = {
            "file_name": "a_IN.wav",
            **_METRICS_DEFAULTS,
            "failed_stage": "asr",
            "error_type": "APIError",
            "asr_attempts": 3,
        }
        record = build_metrics_df([row], ["a_IN.wav"]).iloc[0]
        assert record["Status"] == "Failed"
        assert record["Failed Stage"] == "asr"
        assert record["ASR Attempts"] == 3
        # Nothing downstream ran, so nothing downstream was measured.
        assert pd.isna(record["Label Attempts"])
        assert pd.isna(record["Analysis Attempts"])
        assert pd.isna(record["Total Tokens"])

    def test_label_failure_keeps_the_asr_spend_and_costs_no_analysis(self):
        row = {
            "file_name": "a_IN.wav",
            **_METRICS_DEFAULTS,
            "failed_stage": "label",
            "error_type": "transcript_echoed_input",
            "asr_attempts": 1,
            "audio_seconds": 156.1,
            "asr_segments": 23,
            "label_attempts": 3,
        }
        record = build_metrics_df([row], ["a_IN.wav"]).iloc[0]
        assert record["Failed Stage"] == "label"
        assert record["ASR Segments"] == 23
        # The expensive call never ran -- that is the point of failing here.
        assert pd.isna(record["Analysis Attempts"])
        assert pd.isna(record["Analysis Prompt Tokens"])

    def test_analysis_failure_still_reports_the_label_spend(self):
        row = {
            "file_name": "a_IN.wav",
            **_METRICS_DEFAULTS,
            "failed_stage": "analysis",
            "error_type": "LengthFinishReasonError",
            "asr_attempts": 1,
            "audio_seconds": 156.1,
            "asr_segments": 23,
            "label_attempts": 1,
            "label_prompt_tokens": 1130,
            "label_completion_tokens": 764,
            "label_total_tokens": 1894,
            "analysis_attempts": 3,
        }
        record = build_metrics_df([row], ["a_IN.wav"]).iloc[0]
        assert record["Failed Stage"] == "analysis"
        # The labelling call was paid for; a total built from the analysis call
        # alone would under-report the run by exactly the failing rows.
        assert record["Total Tokens"] == 1894
        assert pd.isna(record["Analysis Prompt Tokens"])

    def test_resumed_file_reports_no_asr_attempts(self):
        row = {**_success_metrics("a_IN.wav"), "asr_attempts": None}
        record = build_metrics_df([row], ["a_IN.wav"]).iloc[0]
        # Blank means "this run made no ASR call", never 0.
        assert pd.isna(record["ASR Attempts"])
        assert record["ASR Segments"] == 23


class TestBuildSummaryDf:
    def test_summary_matches_metrics_frame(self):
        rows = [
            _success_metrics("a_IN.wav"),
            {
                "file_name": "b_OUT.wav",
                **_METRICS_DEFAULTS,
                "failed_stage": "label",
                "error_type": "transcript_echoed_input",
                "asr_attempts": 1,
                "audio_seconds": 100.0,
                "asr_segments": 12,
                "label_attempts": 3,
            },
        ]
        metrics_df = build_metrics_df(rows, ["a_IN.wav", "b_OUT.wav"])
        summary_df = build_summary_df(
            run_id="2026-08-14_00-00-00",
            model="qwen/qwen3.6-27b",
            label_model="google/gemma-4-31b-it",
            asr_model="openai/whisper-large-v3",
            resumed=False,
            counts={
                "source_files": 2,
                "resumed_files": 0,
                "transcribed_files": 2,
                "labelled_files": 1,
                "analyzed_files": 1,
            },
            metrics_df=metrics_df,
            latency={"files_ms": 1234.5, "results_ms": 10.0},
        )
        values = dict(zip(summary_df["Metric"], summary_df["Value"]))
        assert list(summary_df.columns) == ["Metric", "Value"]
        assert values["Model"] == "qwen/qwen3.6-27b"
        assert values["Label Model"] == "google/gemma-4-31b-it"
        assert values["Succeeded"] == 1
        assert values["Failed"] == 1
        assert values["Success Rate"] == 0.5
        assert values["ASR Failures"] == 0
        assert values["Label Failures"] == 1
        assert values["Analysis Failures"] == 0
        assert values["Labelled Files"] == 1
        assert values["Total Audio Seconds"] == pytest.approx(256.1)
        assert values["Total ASR Segments"] == 35
        assert values["Total Tokens"] == 1894 + 38118
        assert values["Analysis Reasoning Tokens"] == 11641
        assert values["Files With Usage"] == 1
        assert values["Files Elapsed (ms)"] == 1234.5

    def test_empty_frame_does_not_divide_by_zero(self):
        metrics_df = build_metrics_df([], [])
        summary_df = build_summary_df(
            run_id="r",
            model="m",
            label_model="l",
            asr_model="a",
            resumed=True,
            counts={
                "source_files": 0,
                "resumed_files": 0,
                "transcribed_files": 0,
                "labelled_files": 0,
                "analyzed_files": 0,
            },
            metrics_df=metrics_df,
            latency={"files_ms": 0.0, "results_ms": 0.0},
        )
        values = dict(zip(summary_df["Metric"], summary_df["Value"]))
        assert values["Success Rate"] == 0.0
        assert values["Avg Total Tokens / File"] == 0.0
        assert values["Avg Tokens / Audio Minute"] == 0.0


class TestLabelledTranscriptError:
    def test_accepts_speaker_labelled_turns(self):
        assert _labelled_transcript_error(LABELLED) is None

    def test_rejects_the_echoed_input(self, asr_payload):
        # The exact bug this split exists to fix: the model returned the
        # `[start-end]` block it was given, which is non-empty, parses, uploads,
        # and is only caught by a human opening the delivered .txt.
        echoed = "\n".join(
            f"[{s['start']}-{s['end']}] {s['text']}" for s in asr_payload["segments"]
        )
        assert _labelled_transcript_error(echoed) == "transcript_echoed_input"

    def test_rejects_a_partial_echo(self):
        mixed = "Agent: สวัสดีค่ะ\n[8.796-14.98] ครับคือเน็ตบ้านผมใช้ไม่ได้"
        assert _labelled_transcript_error(mixed) == "transcript_echoed_input"

    def test_rejects_unlabelled_prose(self):
        assert _labelled_transcript_error("สวัสดีค่ะ ครับ") == "transcript_unlabelled"

    def test_empty_is_not_an_error(self):
        # A call with no speech legitimately answers with an empty string.
        assert _labelled_transcript_error("") is None
        assert _labelled_transcript_error("   \n\n ") is None


class TestBuildUserContent:
    def test_no_line_carries_leading_whitespace(self, asr_payload):
        body = _build_user_content("a_IN.wav", asr_payload)
        # An indented triple-quoted f-string would carry its indentation into
        # every prompt line, and .strip() only removes it from the two ends.
        assert not any(line.startswith((" ", "\t")) for line in body.splitlines())

    def test_metadata_and_segment_lines(self, asr_payload):
        body = _build_user_content("a_IN.wav", asr_payload)
        assert "## METADATA" in body
        assert "FILE name: a_IN.wav" in body
        assert "Record duration: 156.1 seconds" in body
        assert "Language: th" in body
        assert "## TRANSCRIPT" in body
        assert "[0.188-7.556] สวัสดีค่ะ" in body

    def test_falls_back_to_flat_text_without_segments(self, asr_payload):
        body = _build_user_content("a_IN.wav", {**asr_payload, "segments": None})
        assert body.endswith("สวัสดีค่ะ ครับคือเน็ตบ้านผมใช้ไม่ได้")

    def test_empty_transcript_yields_empty_block(self):
        body = _build_user_content("a_IN.wav", {"duration": 0.0, "segments": [], "text": ""})
        assert body.endswith("## TRANSCRIPT\n")


class TestBuildAnalysisContent:
    def test_carries_the_labelled_turns_not_the_segments(self, asr_payload):
        body = _build_analysis_content("a_IN.wav", asr_payload, LABELLED)
        assert "FILE name: a_IN.wav" in body
        assert "Record duration: 156.1 seconds" in body
        assert body.endswith(LABELLED)
        # The analysis call must never see the raw timestamped block.
        assert "[0.188-7.556]" not in body

    def test_no_line_carries_leading_whitespace(self, asr_payload):
        body = _build_analysis_content("a_IN.wav", asr_payload, LABELLED)
        assert not any(line.startswith((" ", "\t")) for line in body.splitlines())


class TestAsrPayloadValid:
    def test_accepts_a_whisper_dump(self, asr_payload):
        assert _asr_payload_valid(asr_payload)

    def test_rejects_a_chunked_pipeline_checkpoint(self):
        # The deprecated chunk pipeline writes a list under the same payload
        # prefix; it deserialises cleanly and only fails later on ["duration"].
        assert not _asr_payload_valid([{"idx": 1, "res": None, "error": "APIError"}])

    def test_rejects_a_dict_missing_the_required_keys(self):
        assert not _asr_payload_valid({"text": "สวัสดี"})
        assert not _asr_payload_valid({"duration": 1.0})
