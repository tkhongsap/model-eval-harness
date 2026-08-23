"""Offline tests for the trim sentiment pipeline's schema contract and wiring.

Three seams, no network: the trimmed schema against ``OutputSchema``'s columns (the
whole point of the variant is that the two coincide), ``build_output_df`` fed a trim
dump, and ``analyze_transcript``'s call shape via the FakeClient pattern from
test_internal_analysis_retry. Plus a guard that the three variants' destinations
stay pairwise disjoint, which is what makes a trim run un-confusable with the others.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS
from src.local_model.sentiment.internal_asr_llm_output import Config as BaseConfig
from src.local_model.sentiment.internal_asr_llm_split_output import Config as SplitConfig
from src.local_model.sentiment.internal_asr_llm_trim_output import (
    Config,
    analyze_transcript,
    build_output_df,
)
from src.local_model.sentiment.schema.model_response import ServiceQuality
from src.local_model.sentiment.schema.output_schema import OutputSchema
from src.local_model.sentiment.schema.trim_model_response import (
    TrimBinaryCriterionEvaluation,
    TrimCriterionEvaluation,
    TrimCustomerSentiment,
    TrimModelResponse,
    TrimServiceQuality,
)
from tests.helpers_fabricate import fabricate_payload

PAYLOAD = {"duration": 156.1, "language": "th", "segments": [], "text": "", "words": None}
TRANSCRIPT = "Agent: สวัสดีค่ะ\nCustomer: ครับคือเน็ตบ้านผมใช้ไม่ได้"

# Schema field -> sheet column, where the two disagree (the sheet's headers predate
# the schema rename). Mirrors build_output_df's column_renames, inverted.
FIELD_TO_COLUMN = {
    "customer_verification": "company_verification",
    "true_application": "self_service",
}

# Everything the trim schema exists to stop the model from generating.
DROPPED_KEYS = {
    "reason",
    "service_number",
    "call_type_confident",
    "omotenashi",
    "service_quality_performance_insight",
    "sale_opportunity",
    "customer_experience",
    "network",
    "csat",
}


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(
        "src.local_model.sentiment.internal_asr_llm_trim_output.time.sleep", lambda _: None
    )


def _trim_dump() -> dict[str, Any]:
    parsed = TrimModelResponse.model_validate(fabricate_payload(TrimModelResponse))
    return parsed.model_dump(mode="json")


def _walk_property_keys(node: Any):
    """Every property name at every object level of a JSON schema, ``$defs`` included."""
    if isinstance(node, dict):
        for key, value in node.get("properties", {}).items():
            yield key
            yield from _walk_property_keys(value)
        for value in node.get("$defs", {}).values():
            yield from _walk_property_keys(value)
        for nested in ("items", "anyOf"):
            if nested in node:
                yield from _walk_property_keys(node[nested])
    elif isinstance(node, list):
        for item in node:
            yield from _walk_property_keys(item)


class TestTrimSchemaContract:
    """The schema<->sheet contract: exactly the Excel-bound fields, nothing more."""

    def test_no_dropped_field_survives_in_the_json_schema(self):
        """The token-saving property itself: none of the discarded fields can be
        generated, because none exists in the decoding grammar."""
        keys = set(_walk_property_keys(TrimModelResponse.model_json_schema()))
        assert keys.isdisjoint(DROPPED_KEYS)
        assert not {key for key in keys if key.endswith("_reason")}

    def test_service_quality_fields_cover_the_qa_columns_in_order(self):
        fields = list(TrimServiceQuality.model_fields)
        columns = [FIELD_TO_COLUMN.get(name, name) for name in fields]
        assert columns == list(OutputSchema.to_schema().columns)[2:24]
        # Same relative order as the full model, with only the two non-sheet
        # fields removed -- declaration order is generation order.
        full = [
            name
            for name in ServiceQuality.model_fields
            if name not in ("omotenashi", "service_quality_performance_insight")
        ]
        assert fields == full

    def test_binary_criteria_have_no_na(self):
        binary = {"manners", "enthusiasm", "communication_skill", "problem_understanding"}
        for name, info in TrimServiceQuality.model_fields.items():
            expected = TrimBinaryCriterionEvaluation if name in binary else TrimCriterionEvaluation
            assert info.annotation is expected, name

    def test_call_type_round_trips_through_its_own_dump(self):
        """The resume contract: run() re-validates a stored dump, whose call_type
        is the serializer's comma-joined string, not the model's array."""
        dump = _trim_dump()
        assert dump["call_type"] == "Enquiry"
        assert TrimModelResponse.model_validate(dump).model_dump(mode="json") == dump


class TestBuildOutputDf:
    def test_trim_dump_fills_every_data_column(self):
        content = _trim_dump()

        output_df = build_output_df(
            [{"file_name": "a_IN.wav", "content": content}], ["a_IN.wav", "b_OUT.wav"]
        )

        assert list(output_df.columns) == list(OutputSchema.to_schema().columns)
        parsed_row = output_df.iloc[0]
        assert not parsed_row.isna().any()
        assert parsed_row["company_verification"] == "Meet"
        assert parsed_row["self_service"] == "Meet"
        assert parsed_row["overall_sentiment"] == "Positive"
        assert parsed_row["summary_story"] == "x"
        assert parsed_row["call_type"] == "Enquiry"
        # The failed file keeps its identity columns and nothing else.
        blank_row = output_df.iloc[1]
        assert blank_row["Voice File Name"] == "b_OUT.wav"
        assert blank_row[2:].isna().all()


def _success_response(parsed) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason="stop", message=SimpleNamespace(parsed=parsed))],
        usage=SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
            completion_tokens_details=None,
            prompt_tokens_details=None,
        ),
    )


def _unparsed_response() -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                finish_reason="stop",
                message=SimpleNamespace(parsed=None, content=None, refusal=None),
            )
        ],
        usage=None,
    )


class FakeStream:
    """See test_internal_analysis_retry.FakeStream -- one object plays both roles."""

    def __init__(self, step, chunks: int = 2):
        self._step = step
        self._chunks = chunks
        self.exited = False

    def __enter__(self) -> FakeStream:
        return self

    def __exit__(self, *exc_info) -> bool:
        self.exited = True
        return False

    def __iter__(self):
        for _ in range(self._chunks):
            if isinstance(self._step, Exception):
                raise self._step
            # The drain reads event.type for the time-to-first-token record.
            yield SimpleNamespace(type="content.delta")

    def get_final_completion(self):
        return self._step


class FakeClient:
    def __init__(self, script):
        self.calls: list[dict] = []
        self._script = list(script)
        self.chat = SimpleNamespace(completions=SimpleNamespace(stream=self._stream))

    def _stream(self, **kwargs) -> FakeStream:
        self.calls.append(kwargs)
        return FakeStream(self._script.pop(0))


class TestAnalyzeTranscript:
    """The one behavioural change: the trim schema is what constrains decoding."""

    def test_success_sends_the_trim_schema_and_returns_its_dump(self):
        parsed = TrimModelResponse.model_validate(fabricate_payload(TrimModelResponse))
        client = FakeClient([_success_response(parsed)])

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", Config(), "system"
        )

        assert client.calls[0]["response_format"] is TrimModelResponse
        # Whatever prompt Config selects passes through to the call verbatim --
        # the trimming lives in the prompt files themselves, not in this function.
        assert client.calls[0]["messages"][0] == {"role": "system", "content": "system"}
        assert content == parsed.model_dump(mode="json")
        assert metrics["status"] == STATUS_SUCCESS
        assert metrics["analysis_attempts"] == 1
        assert metrics["analysis_prompt_tokens"] == 100
        assert metrics["analysis_total_tokens"] == 150

    def test_unparsed_content_fails_with_blank_tokens(self):
        config = replace(Config(), llm_max_attempts=2)
        client = FakeClient([_unparsed_response(), _unparsed_response()])

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system"
        )

        assert content is None
        assert metrics["status"] == STATUS_FAILED
        assert metrics["failed_stage"] == "analysis"
        assert metrics["error_type"] == "ValueError"
        assert metrics["analysis_attempts"] == 2
        assert "analysis_total_tokens" not in metrics


class TestConfigIsolation:
    """Disjoint destinations are what make a trim run un-confusable with the others."""

    def test_destinations_are_disjoint_from_baseline_and_split(self):
        base, split, trim = BaseConfig(), SplitConfig(), Config()
        assert trim.dest_file == "/poc_internal_model_migration/voicefiles_internal_trim_output"
        assert trim.gcs_payload_path == "poc_internal_model/sentiment_qa/internal_trim/payload_files"
        assert trim.gcs_dest_path == "poc_internal_model/sentiment_qa/internal_trim/dest_files"
        for attr in ("dest_file", "gcs_payload_path", "gcs_dest_path"):
            values = {getattr(config, attr) for config in (base, split, trim)}
            assert len(values) == 3, attr
        # Sources are deliberately shared: all three variants read the same files.
        assert base.gcs_src_path == split.gcs_src_path == trim.gcs_src_path


PROMPT_DIR = Path("src/local_model/sentiment/prompt")
JSON_EXAMPLE_MARKER = "Example of Output JSON:"
# Category and field headers, as scripts/build_trim_prompts.py recognizes them.
PROMPT_HEADER_RE = re.compile(r"^(?:### \*\*Category \d+\.: ?`\w+`\*\*|\*\*`[\w&]+`\*\*)", re.M)

# Every section the trimmed prompts exist to remove: fields TrimModelResponse
# cannot emit, plus the three whole categories with no trim counterpart.
DROPPED_SECTION_MARKERS = (
    "**`service_number`**",
    "**`call_type_confident`**",
    "**`product_category`**",
    "**`repeat_call`**",
    "**`fcr`**",
    "**`churn_probability`**",
    "**`churn_reason`**",
    "**`customer_insight_summary`**",
    "**`standard_gsd_name`**",
    "**`service_quality_performance_insight`**",
    "**`omotenashi`**",
    "**`primary_sentiment_driver`**",
    "**`csat`**",
    "**`cs_performance_insight`**",
    "### **Category 3",
    "### **Category 5",
    "### **Category 6",
)


def _read_prompt(name: str) -> str:
    return (PROMPT_DIR / name).read_text(encoding="utf-8")


def _json_example(text: str) -> dict[str, Any]:
    example = text[text.index(JSON_EXAMPLE_MARKER) :]
    return json.loads(example[example.index("```json") + len("```json") : example.rindex("```")])


class TestTrimPromptFiles:
    """The trimmed prompt files against the schema and against their sources.

    These lock in the experiment's one-variable property: the Thai trim files are
    pure deletions from the originals (retained lines verbatim, in order), the
    ``_eng`` files mirror their structure exactly, and none of the four describes a
    field the trim grammar cannot emit.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "inbound_trim_prompt.txt",
            "outbound_trim_prompt.txt",
            "inbound_trim_prompt_eng.txt",
            "outbound_trim_prompt_eng.txt",
        ],
    )
    def test_dropped_sections_are_absent(self, name):
        text = _read_prompt(name)
        for marker in DROPPED_SECTION_MARKERS:
            assert marker not in text, f"{name} still carries {marker!r}"
        # The date placeholder must survive: the pipeline substitutes it at load.
        assert "{date}" in text
        # The embedded example must not resurrect the dropped per-criterion reasons.
        assert '"reason"' not in text[text.index(JSON_EXAMPLE_MARKER) :]

    @pytest.mark.parametrize("direction", ["inbound", "outbound"])
    def test_trim_lines_are_a_subsequence_of_the_source(self, direction):
        source_lines = iter(_read_prompt(f"{direction}_prompt.txt").split("\n"))
        for line in _read_prompt(f"{direction}_trim_prompt.txt").split("\n"):
            if line.startswith(JSON_EXAMPLE_MARKER):
                break  # the example block is the one rewritten piece
            assert any(line == candidate for candidate in source_lines), (
                f"{direction} trim line is not in the source (or out of order): {line!r}"
            )

    @pytest.mark.parametrize(
        "name",
        [
            "inbound_trim_prompt.txt",
            "outbound_trim_prompt.txt",
            "inbound_trim_prompt_eng.txt",
            "outbound_trim_prompt_eng.txt",
        ],
    )
    def test_json_example_matches_the_trim_schema(self, name):
        example = _json_example(_read_prompt(name))
        assert list(example) == list(TrimModelResponse.model_fields)
        assert list(example["customer_insight"]) == ["summary_story"]
        assert list(example["service_quality"]) == list(TrimServiceQuality.model_fields)
        assert list(example["customer_sentiment"]) == list(TrimCustomerSentiment.model_fields)
        for criterion, value in example["service_quality"].items():
            assert list(value) == ["evaluation"], criterion

    @pytest.mark.parametrize("direction", ["inbound", "outbound"])
    def test_eng_files_mirror_the_trim_structure(self, direction):
        trim_text = _read_prompt(f"{direction}_trim_prompt.txt")
        eng_text = _read_prompt(f"{direction}_trim_prompt_eng.txt")
        assert PROMPT_HEADER_RE.findall(eng_text) == PROMPT_HEADER_RE.findall(trim_text)
        # The translation must not touch the example: byte-identical from the marker on.
        assert eng_text[eng_text.index(JSON_EXAMPLE_MARKER) :] == (
            trim_text[trim_text.index(JSON_EXAMPLE_MARKER) :]
        )
