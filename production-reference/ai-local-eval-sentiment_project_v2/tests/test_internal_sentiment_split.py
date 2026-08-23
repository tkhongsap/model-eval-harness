"""Offline tests for the split sentiment pipeline's slicing, fan-out and merge.

Three seams, no network: ``_split_system_prompt`` against the *real* prompt files
(the slicing contract is about their actual headers), ``_analyze_section``'s call
shape via the FakeClient pattern from test_internal_analysis_retry, and
``analyze_transcript``'s merge/failure/metrics semantics with the section call
stubbed out.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS
from src.local_model.sentiment.internal_asr_llm_split_output import (
    Config,
    _analyze_section,
    _SlicedPrompt,
    _split_system_prompt,
    analyze_transcript,
)
from src.local_model.sentiment.schema.model_response import ModelResponse, Network
from src.local_model.sentiment.schema.split_model_response import (
    EXPECTED_PROMPT_SECTIONS,
    SECTIONS,
)
from tests.helpers_fabricate import fabricate_payload

PAYLOAD = {"duration": 156.1, "language": "th", "segments": [], "text": "", "words": None}
TRANSCRIPT = "Agent: สวัสดีค่ะ\nCustomer: ครับคือเน็ตบ้านผมใช้ไม่ได้"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(
        "src.local_model.sentiment.internal_asr_llm_split_output.time.sleep", lambda _: None
    )


def _read_prompt(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module", params=["inbound", "outbound"])
def prompt_text(request) -> str:
    config = Config()
    path = (
        config.system_inbound_prompt_file
        if request.param == "inbound"
        else config.system_outbound_prompt_file
    )
    return _read_prompt(path)


class TestSplitSystemPrompt:
    """The slicing contract, against the real prompt files."""

    def test_every_expected_section_is_found(self, prompt_text):
        sliced = _split_system_prompt(prompt_text)
        assert EXPECTED_PROMPT_SECTIONS <= sliced.sections.keys()

    def test_slices_round_trip_to_the_full_text(self, prompt_text):
        """Nothing the prompt authors wrote may fall between two slices."""
        sliced = _split_system_prompt(prompt_text)
        assert sliced.preamble + "".join(sliced.sections.values()) == prompt_text

    def test_preamble_carries_the_top_level_field_definitions(self, prompt_text):
        """The classification call rides the preamble alone, so they must be there."""
        preamble = _split_system_prompt(prompt_text).preamble
        for field in ("service_number", "call_type", "call_type_confident"):
            assert field in preamble

    def test_decision_rules_stay_inside_service_quality(self):
        """`## Decision Rules` sits between two Category headers -- it must ride
        with the category it belongs to, not leak into a neighbour or the preamble."""
        text = _read_prompt(Config().system_inbound_prompt_file)
        sliced = _split_system_prompt(text)
        assert "## Decision Rules" in sliced.sections["service_quality"]
        assert "## Decision Rules" not in sliced.preamble

    def test_headerless_text_is_all_preamble(self):
        sliced = _split_system_prompt("no category headers here")
        assert sliced.preamble == "no category headers here"
        assert sliced.sections == {}


class TestSlicedPromptForSection:
    def test_none_is_the_preamble_alone(self):
        sliced = _SlicedPrompt(preamble="PRE", sections={"network": "### net rules"})
        assert sliced.for_section(None) == "PRE"

    def test_a_key_appends_that_section_only(self):
        sliced = _SlicedPrompt(
            preamble="PRE", sections={"network": "NET", "service_quality": "SQ"}
        )
        assert sliced.for_section("network") == "PRE\nNET"


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


class TestAnalyzeSection:
    """The per-topic call: narrowed schema in, short-named metrics out."""

    def test_success_returns_the_dump_and_section_metrics(self):
        parsed = Network.model_validate(fabricate_payload(Network))
        client = FakeClient([_success_response(parsed)])

        dump, metrics = _analyze_section(
            client, "user", "a.wav", Config(), "system", "network", Network
        )

        assert client.calls[0]["response_format"] is Network
        assert client.calls[0]["messages"][0] == {"role": "system", "content": "system"}
        assert dump == parsed.model_dump(mode="json")
        assert metrics == {
            "attempts": 1,
            "error_type": None,
            "prompt_tokens": 100,
            "completion_tokens": 50,
            "reasoning_tokens": None,
            "cached_tokens": None,
            "total_tokens": 150,
        }

    def test_failure_reports_blank_tokens_and_the_error(self):
        config = replace(Config(), llm_max_attempts=2)
        client = FakeClient([_unparsed_response(), _unparsed_response()])

        dump, metrics = _analyze_section(
            client, "user", "a.wav", config, "system", "network", Network
        )

        assert dump is None
        assert metrics["attempts"] == 2
        assert metrics["error_type"] == "ValueError"
        assert metrics["total_tokens"] is None


def _section_dumps() -> dict[str, dict]:
    return {section.key: fabricate_payload(section.model) for section in SECTIONS}


def _stub_metrics(attempts: int = 1, tokens: int | None = 10, error: str | None = None) -> dict:
    return {
        "attempts": attempts,
        "error_type": error,
        "prompt_tokens": tokens,
        "completion_tokens": tokens,
        "reasoning_tokens": None,
        "cached_tokens": None,
        "total_tokens": None if tokens is None else tokens * 2,
    }


def _stub_analyze_section(monkeypatch, results: dict, seen: list | None = None):
    """Replace the section call; ``results`` maps section key -> (dump, metrics)."""

    def stub(client_llm, user_content, file_name, config, system_prompt, key, model):
        if seen is not None:
            seen.append((key, system_prompt, model))
        return results[key]

    monkeypatch.setattr(
        "src.local_model.sentiment.internal_asr_llm_split_output._analyze_section", stub
    )


SLICED = _SlicedPrompt(
    preamble="PRE",
    sections={section.prompt_key: f"S:{section.prompt_key}" for section in SECTIONS[1:]},
)


class TestAnalyzeTranscriptOrchestration:
    def test_all_sections_merge_into_one_model_response(self, monkeypatch):
        dumps = _section_dumps()
        seen: list = []
        _stub_analyze_section(
            monkeypatch, {key: (dump, _stub_metrics()) for key, dump in dumps.items()}, seen
        )

        content, metrics = analyze_transcript(
            object(), PAYLOAD, TRANSCRIPT, "a.wav", Config(), SLICED
        )

        # The merged dump is a full, re-validated ModelResponse -- call_type
        # comes back through its serializer as the comma-joined cell.
        assert content == ModelResponse.model_validate(content).model_dump(mode="json")
        assert content["call_type"] == "Enquiry"
        assert content["network"] == dumps["network"]
        assert metrics["status"] == STATUS_SUCCESS
        assert metrics["analysis_attempts"] == len(SECTIONS)
        assert metrics["analysis_prompt_tokens"] == 10 * len(SECTIONS)
        assert metrics["analysis_total_tokens"] == 20 * len(SECTIONS)
        # Every section ran, each with its own sliced prompt and model.
        assert {key for key, _, _ in seen} == {section.key for section in SECTIONS}
        by_key = {key: (prompt, model) for key, prompt, model in seen}
        assert by_key["classification"][0] == "PRE"
        assert by_key["network"][0] == "PRE\nS:network"
        assert by_key["network"][1] is Network

    def test_one_failed_section_fails_the_file_but_keeps_paid_tokens(self, monkeypatch):
        dumps = _section_dumps()
        results = {key: (dump, _stub_metrics(attempts=1)) for key, dump in dumps.items()}
        results["service_quality"] = (None, _stub_metrics(attempts=3, tokens=None, error="E"))
        _stub_analyze_section(monkeypatch, results)

        content, metrics = analyze_transcript(
            object(), PAYLOAD, TRANSCRIPT, "a.wav", Config(), SLICED
        )

        assert content is None
        assert metrics["status"] == STATUS_FAILED
        assert metrics["failed_stage"] == "analysis"
        # Section-qualified, so the Matrix's Error Type cell names the topic.
        assert metrics["error_type"] == "service_quality:E"
        # Sum over sections = endpoint calls paid, the ASR-chunk convention.
        assert metrics["analysis_attempts"] == len(SECTIONS) - 1 + 3
        # The finished sections' spend still reaches the sheet.
        assert metrics["analysis_prompt_tokens"] == 10 * (len(SECTIONS) - 1)

    def test_all_blank_token_cells_stay_blank(self, monkeypatch):
        dumps = _section_dumps()
        _stub_analyze_section(
            monkeypatch, {key: (dump, _stub_metrics(tokens=None)) for key, dump in dumps.items()}
        )

        _, metrics = analyze_transcript(
            object(), PAYLOAD, TRANSCRIPT, "a.wav", Config(), SLICED
        )

        assert metrics["status"] == STATUS_SUCCESS
        assert metrics["analysis_prompt_tokens"] is None
        assert metrics["analysis_total_tokens"] is None

    def test_first_failure_in_section_order_names_the_row(self, monkeypatch):
        """Deterministic Error Type: SECTIONS order, not dict or completion order."""
        dumps = _section_dumps()
        results = {key: (dump, _stub_metrics()) for key, dump in dumps.items()}
        results["network"] = (None, _stub_metrics(tokens=None, error="LateError"))
        results["classification"] = (None, _stub_metrics(tokens=None, error="EarlyError"))
        _stub_analyze_section(monkeypatch, results)

        _, metrics = analyze_transcript(
            object(), PAYLOAD, TRANSCRIPT, "a.wav", Config(), SLICED
        )

        assert metrics["error_type"] == "classification:EarlyError"
