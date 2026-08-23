"""Offline tests for analyze_transcript's retry policy.

A fake client stands in for the chat endpoint, so unlike test_internal_frames these
tests do touch SDK exception types -- LengthFinishReasonError is part of the retry
contract under test. No network.
"""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from openai import LengthFinishReasonError

from src.google_model.usage_metrics import STATUS_FAILED, STATUS_SUCCESS
from src.local_model.sentiment.internal_asr_llm_output import Config, analyze_transcript
from src.local_model.sentiment.schema.model_response import ModelResponse
from tests.helpers_fabricate import fabricate_payload

PAYLOAD = {"duration": 156.1, "language": "th", "segments": [], "text": "", "words": None}
TRANSCRIPT = "Agent: สวัสดีค่ะ\nCustomer: ครับคือเน็ตบ้านผมใช้ไม่ได้"


def _escalation_config(**overrides) -> Config:
    """A Config with explicit token ceilings, so the tests don't track the defaults."""
    base = Config()
    config = replace(
        base,
        generation_config={**base.generation_config, "max_tokens": 16000},
        final_attempt_max_tokens=32000,
    )
    return replace(config, **overrides) if overrides else config


def _length_error() -> LengthFinishReasonError:
    """A length failure shaped like the SDK's: usage present, 16k burned."""
    return LengthFinishReasonError(
        completion=SimpleNamespace(
            usage=SimpleNamespace(
                completion_tokens=16000,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=15000),
            ),
            choices=[
                SimpleNamespace(
                    finish_reason="length",
                    message=SimpleNamespace(content="x" * 100, refusal=None),
                )
            ],
        )
    )


def _success_response() -> SimpleNamespace:
    parsed = ModelResponse.model_validate(fabricate_payload(ModelResponse))
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
    """A finished response whose message carries nothing parseable."""
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
    """Stands in for the SDK's stream manager and the stream it yields.

    One object plays both roles, which is enough here. A scripted exception is raised
    *during iteration*, not from the request: that is where the real SDK raises
    ``LengthFinishReasonError``, from ``_accumulate_chunk``.
    """

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
    """Records each stream call's kwargs, then raises or returns per the script."""

    def __init__(self, script):
        self.calls: list[dict] = []
        self.streams: list[FakeStream] = []
        self._script = list(script)
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(stream=self._stream)
        )

    def _stream(self, **kwargs) -> FakeStream:
        self.calls.append(kwargs)
        stream = FakeStream(self._script.pop(0))
        self.streams.append(stream)
        return stream


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(
        "src.local_model.sentiment.internal_asr_llm_output.time.sleep", lambda _: None
    )


class TestFinalAttemptEscalation:
    def test_last_attempt_raises_the_ceiling(self):
        config = _escalation_config()
        client = FakeClient([_length_error(), _success_response()])

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system"
        )

        assert [call["max_tokens"] for call in client.calls] == [16000, 32000]
        assert [call["seed"] for call in client.calls] == [0, 1]
        assert content is not None
        assert metrics["status"] == STATUS_SUCCESS
        assert metrics["analysis_attempts"] == 2

    def test_earlier_attempts_keep_the_configured_ceiling(self):
        # Three attempts, so attempt 2 is a middle attempt -- it must not escalate.
        config = _escalation_config(llm_max_attempts=3)
        client = FakeClient([_length_error(), _success_response()])

        analyze_transcript(client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system")

        assert [call["max_tokens"] for call in client.calls] == [16000, 16000]

    def test_single_attempt_config_never_escalates(self):
        config = _escalation_config(llm_max_attempts=1)
        client = FakeClient([_success_response()])

        analyze_transcript(client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system")

        assert [call["max_tokens"] for call in client.calls] == [16000]

    def test_escalation_does_not_mutate_the_shared_config(self):
        config = _escalation_config()
        client = FakeClient([_length_error(), _length_error(), _length_error()])

        analyze_transcript(client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system")

        assert config.generation_config["max_tokens"] == 16000
        assert config.generation_config["seed"] == 0


class TestFailurePaths:
    def test_all_length_failures_report_the_error_type(self):
        config = Config()
        client = FakeClient([_length_error(), _length_error()])

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system"
        )

        assert content is None
        assert metrics["status"] == STATUS_FAILED
        assert metrics["failed_stage"] == "analysis"
        assert metrics["error_type"] == "LengthFinishReasonError"
        assert metrics["analysis_attempts"] == 2

    def test_unparsed_content_retries_and_reports_value_error(self):
        config = Config()
        client = FakeClient([_unparsed_response(), _unparsed_response()])

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system"
        )

        assert len(client.calls) == 2
        assert content is None
        assert metrics["status"] == STATUS_FAILED
        assert metrics["error_type"] == "ValueError"


class TestStreaming:
    """The transport switch from .parse() to .stream(), and what rides on it."""

    def test_usage_is_requested_so_token_cells_are_not_blank(self):
        client = FakeClient([_success_response()])

        analyze_transcript(client, PAYLOAD, TRANSCRIPT, "a.wav", Config(), "system")

        # Without this the SDK never sees a usage-bearing chunk and res.usage is None.
        assert client.calls[0]["stream_options"] == {"include_usage": True}

    def test_usage_request_can_be_switched_off_for_a_strict_endpoint(self):
        config = replace(Config(), stream_include_usage=False)
        client = FakeClient([_success_response()])

        analyze_transcript(client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system")

        # Absent entirely, not None: a strict server can 400 on an unknown field.
        assert "stream_options" not in client.calls[0]

    def test_a_stream_past_the_deadline_is_abandoned_and_named(self, monkeypatch):
        """Streaming removes the gateway's cutoff, so this guard is the only stop."""
        config = replace(Config(), llm_max_attempts=1, llm_stream_deadline=900.0)
        client = FakeClient([_success_response()])
        # First reading is the start, the second arms the deadline off the first
        # event; every later one is past it.
        readings = iter([0.0, 0.0])
        monkeypatch.setattr(
            "src.local_model.sentiment.internal_asr_llm_output.time.monotonic",
            lambda: next(readings, 10_000.0),
        )

        content, metrics = analyze_transcript(
            client, PAYLOAD, TRANSCRIPT, "a.wav", config, "system"
        )

        assert content is None
        assert metrics["status"] == STATUS_FAILED
        # Distinct from a plain ValueError, so the Error Type cell names the cause.
        assert metrics["error_type"] == "StreamDeadlineError"
        # Raised inside the with, so the manager still released the connection.
        assert client.streams[0].exited
