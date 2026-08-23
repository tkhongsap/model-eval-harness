"""Offline tests for the aggregation helpers: no network, no endpoint."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from openai import BadRequestError

from src.local_model.sentiment.aggregation import (
    _OVERFLOW_MARGIN,
    AggregationError,
    AggregationSettings,
    _parse_context_overflow,
    aggregate_single,
    chunk_records,
    chunk_usage_totals,
    estimate_tokens,
    output_token_budget,
)
from src.local_model.sentiment.schema.model_response import ModelResponse
from tests.helpers_fabricate import fabricate_payload


def _chunk_entry(
    idx: int,
    transcript: str | None,
    *,
    error: str | None = None,
    completion_tokens: int | None = 20,
) -> dict:
    """A checkpoint entry shaped like transcribe_file's output.

    ``completion_tokens=None`` builds an entry whose response carries no usage
    block, the shape an endpoint that reports no usage produces.
    """
    if transcript is None:
        return {"original_file": "a_IN.wav", "idx": idx, "res": None, "error": error}
    parsed = {
        "transcript": transcript,
        "customer_speech_rate": "normal",
        "customer_voice_tone": "calm and steady",
        "customer_emotional": "neutral",
        "customer_sentiment": "neutral",
    }
    res: dict = {"choices": [{"message": {"parsed": parsed}}]}
    if completion_tokens is not None:
        res["usage"] = {
            "prompt_tokens": 100,
            "completion_tokens": completion_tokens,
            "total_tokens": 100 + completion_tokens,
        }
    return {"original_file": "a_IN.wav", "idx": idx, "res": res, "error": None}


class _FakeClient:
    """Stands in for the OpenAI client: records calls, returns a fixed response.

    ``errors`` are raised one per call, in order, before the success response --
    an empty list (the default) succeeds on the first call.
    """

    def __init__(self, parsed, errors: list[Exception] | None = None):
        self.calls: list[dict] = []
        pending = list(errors or [])

        def parse(**kwargs):
            self.calls.append(kwargs)
            if pending:
                raise pending.pop(0)
            message = SimpleNamespace(parsed=parsed, refusal=None)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)],
                usage=SimpleNamespace(prompt_tokens=1234, completion_tokens=56),
            )

        self.chat = SimpleNamespace(completions=SimpleNamespace(parse=parse))


def _bad_request(message: str) -> BadRequestError:
    """A real openai.BadRequestError, as the SDK would raise it."""
    response = httpx.Response(400, request=httpx.Request("POST", "http://test"))
    return BadRequestError(message, response=response, body=None)


_CONTEXT_400 = (
    "Error code: 400 - {'error': {'message': \"This endpoint's maximum context length is "
    "131072 tokens. However, you requested about 139907 tokens (81253 of text input, "
    "58654 in the output). Please reduce the length of either one, or use the "
    "context-compression plugin to compress your prompt automatically.\", 'code': 400}}"
)


class TestChunkRecords:
    def test_sorted_by_idx_and_validated(self):
        parts = [_chunk_entry(2, "Agent: b"), _chunk_entry(1, "Agent: a")]
        records = chunk_records(parts)
        assert [record["chunk"] for record in records] == [1, 2]
        assert records[0]["transcript"] == "Agent: a"
        assert records[0]["customer_sentiment"] == "neutral"

    def test_skips_failed_entries(self):
        parts = [_chunk_entry(1, "Agent: a"), _chunk_entry(2, None, error="ValueError")]
        assert [record["chunk"] for record in chunk_records(parts)] == [1]


class TestChunkUsageTotals:
    def test_sums_across_entries(self):
        parts = [_chunk_entry(1, "a"), _chunk_entry(2, "b")]
        assert chunk_usage_totals(parts) == {"prompt_tokens": 200, "completion_tokens": 40}

    def test_all_absent_is_none_not_zero(self):
        parts = [_chunk_entry(1, None, error="ValueError")]
        assert chunk_usage_totals(parts) == {"prompt_tokens": None, "completion_tokens": None}


class TestEstimateTokens:
    def test_default_ratio_rounds_up(self):
        assert estimate_tokens("ก" * 10) == 5
        assert estimate_tokens("ก" * 11) == 6

    def test_custom_ratio(self):
        assert estimate_tokens("abcd" * 10, chars_per_token=4.0) == 10


class TestOutputTokenBudget:
    _SCHEMA_TOKENS = estimate_tokens(json.dumps(ModelResponse.model_json_schema()))

    def test_usage_based_estimate(self):
        settings = AggregationSettings(model="m", context_limit=10000)
        parts = [_chunk_entry(1, "a", completion_tokens=100), _chunk_entry(2, "b")]
        prompt = "ก" * 200  # 100 estimated tokens
        # ceil((100 + 20) * 1.15) = 138 for the records, from usage, not chars.
        expected = 10000 - (138 + 100 + self._SCHEMA_TOKENS)
        assert output_token_budget(parts, prompt, "ignored when usage exists", settings) == expected

    def test_char_fallback_without_usage(self):
        settings = AggregationSettings(model="m", context_limit=10000)
        parts = [_chunk_entry(1, "a", completion_tokens=None)]
        user_content = "ข" * 300  # 150 estimated tokens
        expected = 10000 - (150 + 100 + self._SCHEMA_TOKENS)
        assert output_token_budget(parts, "ก" * 200, user_content, settings) == expected


class TestAggregateSingle:
    def test_small_input_keeps_single_max_tokens_ceiling(self):
        parsed = ModelResponse.model_validate(fabricate_payload(ModelResponse))
        client = _FakeClient(parsed)
        settings = AggregationSettings(model="m")
        parts = [_chunk_entry(1, "Agent: hello"), _chunk_entry(2, "Customer: hi")]

        result = aggregate_single(client, parts, "system prompt", settings)

        assert result.response is parsed
        assert result.agg_calls == 1
        assert result.prompt_tokens == 1234
        assert result.completion_tokens == 56
        assert len(client.calls) == 1
        assert client.calls[0]["max_tokens"] == settings.single_max_tokens
        assert client.calls[0]["response_format"] is ModelResponse

    def test_large_input_shrinks_max_tokens_to_budget(self):
        parsed = ModelResponse.model_validate(fabricate_payload(ModelResponse))
        client = _FakeClient(parsed)
        settings = AggregationSettings(model="m", single_max_tokens=100000)
        parts = [_chunk_entry(1, "Agent: hello", completion_tokens=80000)]
        prompt = "ก" * 2000

        aggregate_single(client, parts, prompt, settings)

        user_content = json.dumps(chunk_records(parts), ensure_ascii=False)
        budget = output_token_budget(parts, prompt, user_content, settings)
        assert budget < settings.single_max_tokens
        assert client.calls[0]["max_tokens"] == budget

    def test_context_overflow_fails_fast_without_a_call(self):
        client = _FakeClient(None)
        settings = AggregationSettings(model="m")
        parts = [_chunk_entry(1, "Agent: hello", completion_tokens=131072)]

        with pytest.raises(AggregationError) as excinfo:
            aggregate_single(client, parts, "system prompt", settings)

        assert excinfo.value.error_type == "context_overflow"
        assert client.calls == []


class TestParseContextOverflow:
    def test_production_message(self):
        assert _parse_context_overflow(_CONTEXT_400) == (131072, 81253)

    def test_unrelated_message_is_none(self):
        assert _parse_context_overflow("Error code: 400 - invalid model") is None


class TestContextCorrectedRetry:
    def test_server_count_shrinks_max_tokens_and_retries(self):
        parsed = ModelResponse.model_validate(fabricate_payload(ModelResponse))
        client = _FakeClient(parsed, errors=[_bad_request(_CONTEXT_400)])
        settings = AggregationSettings(model="m", single_max_tokens=100000)
        parts = [_chunk_entry(1, "Agent: hello")]

        result = aggregate_single(client, parts, "system prompt", settings)

        assert result.response is parsed
        assert len(client.calls) == 2
        assert client.calls[1]["max_tokens"] == 131072 - 81253 - _OVERFLOW_MARGIN
        # The request never ran, so the retry keeps the configured seed.
        assert client.calls[1]["seed"] == settings.generation_config["seed"]

    def test_server_says_input_fills_window(self):
        message = _CONTEXT_400.replace("81253 of text input", "130900 of text input")
        client = _FakeClient(None, errors=[_bad_request(message)])
        settings = AggregationSettings(model="m")
        parts = [_chunk_entry(1, "Agent: hello")]

        with pytest.raises(AggregationError) as excinfo:
            aggregate_single(client, parts, "system prompt", settings)

        assert excinfo.value.error_type == "context_overflow"
        assert len(client.calls) == 1

    def test_unrelated_400_is_not_retried(self):
        client = _FakeClient(None, errors=[_bad_request("Error code: 400 - invalid model")])
        settings = AggregationSettings(model="m")
        parts = [_chunk_entry(1, "Agent: hello")]

        with pytest.raises(BadRequestError):
            aggregate_single(client, parts, "system prompt", settings)

        assert len(client.calls) == 1
