"""What the run loop must do to every item, proved without a network.

Five properties, each of which fails silently in production code and each of which
would still let a run finish and print a table:

  * **Testset order out, whatever order the calls came back in.** Completion order is
    scheduling noise; a log written in it cannot be diffed against the same run done
    twice, let alone against the other arm.
  * **A dead request is still a row.** An arm whose transport failures vanish has an
    easier denominator than the arm whose network held up (CHANGELOG.md, "Coverage
    accounting on every dimension").
  * **A parse failure is never retried; a transport failure is.** This is the line
    between recovering a lost request and re-rolling a sample until it looks better,
    and it is the reason `classify` has no retry parameter and `OpenRouterClient` is
    built with `max_retries=0`.
  * **`repeats` means replicates, not attempts.** Three identical requests, three rows,
    numbered from 1.
  * **The observed-model histogram is built.** An arm half-served by a second
    checkpoint is two systems reported as one.

The client is a FAKE and there is no network here. The fake declares
`complete(*, model, messages, max_tokens, temperature, top_p, seed, response_format)`
explicitly rather than swallowing `**kwargs`, so a runner that stopped sending
production's decoding parameters would fail these tests instead of quietly running an
arm at different settings.

`client.Completion` is copied structurally rather than imported, for the same reason
`runner.py` does not import it: the SDK is not in the environment CI builds (the root
`requirements.txt` pins the scoring path and omits `openai`), and a suite that imports
it would pass on a laptop and error in CI.

The testset is the real committed one, `tests/fixtures/testsets/retention_v1.jsonl`,
sliced to size by writing the leading lines to a temporary file so `testset_sha` still
describes the exact bytes that were run.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen import runner  # noqa: E402
from evalgen.outcomes import REPAIR_STRIP_FENCE  # noqa: E402
from evalgen.prompts import build_base  # noqa: E402
from evalgen.runner import (  # noqa: E402
    ItemResult,
    RunConfig,
    RunError,
    RunResult,
    run,
    write_run_log,
)
# `testset_sha` starts with "test", so pytest's default collection rules would gather it
# as a test function; `testsets.py` marks it `__test__ = False` precisely for this.
from evalgen.testsets import load_testset, testset_sha  # noqa: E402

TESTSET_PATH = ROOT / "tests" / "fixtures" / "testsets" / "retention_v1.jsonl"

# The committed retention schema's top-level contract. Used to prove that
# `required_keys` is read out of the request rather than hardcoded twice.
RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "analyze_call",
        "schema": json.loads(
            (ROOT / "src" / "evalgen" / "schemas" / "retention.json").read_text(
                encoding="utf-8"
            )
        ),
    },
}

# A well-formed answer, in Thai, of the shape the schema asks for.
GOOD_PAYLOAD = {
    "product": {
        "Postpaid": {
            "main": {"reason": "network", "keyword": "เน็ตช้า"},
            "retention_outcome": "save",
        }
    },
    "call_event_detection": "Technology & Service Events (เหตุการณ์ด้านเทคโนโลยี/บริการ)",
    "recommendation": "เสนอโปรที่ราคาถูกลง",
}


# --------------------------------------------------------------------------- fakes


@dataclass(frozen=True)
class FakeCompletion:
    """A structural copy of `client.Completion`. See the module docstring for why."""

    content: str | None = None
    finish_reason: str | None = "stop"
    observed_model: str | None = "google/gemini-3.6-flash"
    generation_id: str | None = "gen-fake"
    provider: str | None = "FakeProvider"
    prompt_tokens: int | None = 1200
    completion_tokens: int | None = 180
    reasoning_tokens: int | None = 105
    cost: float | None = 0.0004
    latency_s: float = 0.5
    raw: dict | None = None


class FakeTransportError(RuntimeError):
    """What the fake raises where `client.TransportError` would be raised.

    Carries `latency_s` like the real one, and an optional `status_code` so the retry
    policy can be exercised across "no response at all" (None), "try later" (429/5xx)
    and "this will fail forever" (401). Not imported from `client.py`, which would drag
    `openai` into a suite that must run without it.
    """

    def __init__(self, message: str, *, latency_s: float = 0.1, status_code: int | None = None):
        super().__init__(message)
        self.latency_s = latency_s
        if status_code is not None:
            self.status_code = status_code


class FakeClient:
    """Answers by item id, and counts every call it received.

    `responder(item_id, nth_call_for_that_item)` returns a `FakeCompletion` or raises.
    The counter is per item and spans replicates, so retry tests use `repeats=1` and
    read the count directly as the attempt number.
    """

    def __init__(self, responder, *, testset) -> None:
        transcripts = {item.transcript_th: item.item_id for item in testset.items}
        assert len(transcripts) == len(testset.items), "two items share a transcript"
        self._transcripts = transcripts
        self._responder = responder
        self._lock = threading.Lock()
        self.calls: list[dict] = []

    def complete(
        self,
        *,
        model: str,
        messages,
        max_tokens: int,
        temperature: float,
        top_p=None,
        seed=None,
        response_format=None,
        provider=None,
        reasoning_effort="provider-default",
    ) -> FakeCompletion:
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        item_id = self._transcripts[messages[1]["content"]]
        with self._lock:
            self.calls.append(
                {
                    "item_id": item_id,
                    "model": model,
                    "max_tokens": max_tokens,
                    "temperature": temperature,
                    "top_p": top_p,
                    "seed": seed,
                    "response_format": response_format,
                    "provider": provider,
                    "reasoning_effort": reasoning_effort,
                }
            )
            nth = sum(1 for call in self.calls if call["item_id"] == item_id)
        return self._responder(item_id, nth)

    def calls_for(self, item_id: str) -> int:
        return sum(1 for call in self.calls if call["item_id"] == item_id)


def answer(payload: dict | None = None, **overrides) -> FakeCompletion:
    """A completion whose content is `payload` as JSON, Thai left unescaped."""
    body = GOOD_PAYLOAD if payload is None else payload
    return FakeCompletion(content=json.dumps(body, ensure_ascii=False), **overrides)


# ------------------------------------------------------------------------ fixtures


@pytest.fixture(scope="module")
def prompt():
    return build_base()


@pytest.fixture(scope="module")
def full_testset():
    return load_testset(TESTSET_PATH)


def slice_testset(tmp_path: Path, count: int):
    """The first `count` items of the real testset, written out and loaded back.

    Written to a real file rather than constructed in memory so `testset_sha` hashes
    the exact bytes that were run. A sha describing 20 items over a run of 2 would be
    the kind of provenance that is worse than none.
    """
    lines = [line for line in TESTSET_PATH.read_bytes().split(b"\n") if line.strip()]
    target = tmp_path / f"retention_v1_first{count}.jsonl"
    target.write_bytes(b"\n".join(lines[:count]) + b"\n")
    return load_testset(target)


def config(**overrides) -> RunConfig:
    base = {
        "model": "google/gemini-3.6-flash",
        "arm": "baseline",
        "prompt_id": "v9_16_base",
        "repeats": 1,
        "concurrency": 4,
    }
    base.update(overrides)
    return RunConfig(**base)


@pytest.fixture(autouse=True)
def no_backoff(monkeypatch):
    """Flatten the retry backoff. The policy is under test, not the clock."""
    monkeypatch.setattr(runner, "BACKOFF_BASE_S", 0.0)


# ------------------------------------------------------------------------- ordering


def test_results_are_emitted_in_testset_order_not_completion_order(prompt, full_testset):
    """The output order is the testset's, and the test proves the two differ here.

    The fake sleeps longest on the first item and shortest on the last, so with four
    workers the calls demonstrably finish out of order -- the completion order recorded
    through `progress` is asserted NOT to be testset order, because a test that only
    checks the output would also pass against a runner that never reordered anything.
    """
    positions = {item.item_id: index for index, item in enumerate(full_testset.items)}
    total = len(full_testset.items)

    def responder(item_id, _nth):
        time.sleep((total - positions[item_id]) * 0.01)
        return answer()

    client = FakeClient(responder, testset=full_testset)
    completion_order: list[str] = []

    result = run(
        full_testset,
        client=client,
        prompt=prompt,
        config=config(),
        progress=lambda done, count, row: completion_order.append(row.item_id),
    )

    assert [row.item_id for row in result.results] == [i.item_id for i in full_testset.items]
    assert completion_order != [i.item_id for i in full_testset.items], (
        "the calls finished in testset order, so this test proved nothing about "
        "ordering; make the fake's sleeps more skewed"
    )
    assert sorted(completion_order) == sorted(row.item_id for row in result.results)


def test_progress_is_called_once_per_row_with_a_running_count(prompt, tmp_path):
    """`progress` is the only place completion order is visible, and it counts rows."""
    testset = slice_testset(tmp_path, 3)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)
    seen: list[tuple[int, int]] = []

    result = run(
        testset,
        client=client,
        prompt=prompt,
        config=config(repeats=2, concurrency=1),
        progress=lambda done, total, row: seen.append((done, total)),
    )

    assert seen == [(1, 6), (2, 6), (3, 6), (4, 6), (5, 6), (6, 6)]
    assert len(result.results) == 6


def test_a_broken_progress_callback_does_not_lose_the_run(prompt, tmp_path):
    """A display detail must never destroy results that have already been paid for."""
    testset = slice_testset(tmp_path, 2)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    def exploding(done, total, row):
        raise ZeroDivisionError("the progress bar is broken")

    result = run(testset, client=client, prompt=prompt, config=config(), progress=exploding)

    assert len(result.results) == 2
    assert all(row.parse_ok for row in result.results)


# ------------------------------------------------------------- failures are still rows


def test_a_transport_failure_still_produces_a_row(prompt, tmp_path):
    """The item whose call never returned is counted, in the same vocabulary as the rest.

    `outcome == "transport_error"` comes from `outcomes.transport_error()`, so a dead
    request lands in the same coverage accounting as a bad response instead of being
    dropped and quietly shrinking this arm's denominator.
    """
    testset = slice_testset(tmp_path, 3)
    dead = testset.items[1].item_id

    def responder(item_id, _nth):
        if item_id == dead:
            raise FakeTransportError("APITimeoutError: timed out", latency_s=120.0)
        return answer()

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert [row.item_id for row in result.results] == [i.item_id for i in testset.items]
    row = next(r for r in result.results if r.item_id == dead)
    assert row.outcome == "transport_error"
    assert row.parse_ok is False
    assert row.payload is None
    assert row.observed_model is None
    assert row.cost is None
    assert "APITimeoutError" in row.error
    assert row.latency_s == 120.0, "the client's own measurement of the failed request"
    assert sum(result.outcome_counts().values()) == len(result.results)


def test_outcome_counts_sum_to_every_attempted_call(prompt, full_testset):
    """The arithmetic form of "every item emits one row per replicate, failures included"."""
    outcomes_by_position = ["ok", "not_json", "transport_error", "empty_length"]
    plan = {
        item.item_id: outcomes_by_position[index % len(outcomes_by_position)]
        for index, item in enumerate(full_testset.items)
    }

    def responder(item_id, _nth):
        kind = plan[item_id]
        if kind == "transport_error":
            raise FakeTransportError("APIConnectionError: reset")
        if kind == "not_json":
            return FakeCompletion(content="ขออนุญาตสรุปว่าลูกค้าไม่พอใจสัญญาณ")
        if kind == "empty_length":
            return FakeCompletion(content="", finish_reason="length")
        return answer()

    client = FakeClient(responder, testset=full_testset)
    result = run(full_testset, client=client, prompt=prompt, config=config(repeats=2))

    counts = result.outcome_counts()
    assert sum(counts.values()) == len(result.results) == 40
    assert set(counts) == {"ok", "not_json", "transport_error", "empty_length"}
    assert counts["empty_length"] == 10  # 5 items x 2 replicates, kept not dropped


# ---------------------------------------------------------------------- retry policy


def test_a_parse_failure_is_never_retried(prompt, tmp_path):
    """One call, one measurement. Re-rolling until the JSON parses manufactures coverage.

    The failure here is the expensive kind to get wrong: the response arrived, the
    model produced it, and asking again would hand this arm a second draw that its
    opponent did not get.
    """
    testset = slice_testset(tmp_path, 2)
    bad = testset.items[0].item_id

    def responder(item_id, _nth):
        if item_id == bad:
            return FakeCompletion(content="ขออภัยค่ะ ไม่สามารถวิเคราะห์ได้")
        return answer()

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert client.calls_for(bad) == 1, "a parse failure was retried"
    row = next(r for r in result.results if r.item_id == bad)
    assert row.outcome == "refusal"
    assert row.parse_ok is False
    assert row.error is None, "nothing failed in transport; the model answered"


def test_a_retryable_transport_error_is_retried_and_can_succeed(prompt, tmp_path):
    """503 twice, then a response. Three attempts, one row, and the row is the response.

    Retrying here re-rolls nothing, because a request that produced no generation is
    not a sample. That is the whole distinction from the test above.
    """
    testset = slice_testset(tmp_path, 1)
    flaky = testset.items[0].item_id

    def responder(item_id, nth):
        if nth < 3:
            raise FakeTransportError("InternalServerError: 503", status_code=503)
        return answer()

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert client.calls_for(flaky) == 3
    assert len(result.results) == 1
    assert result.results[0].outcome == "ok"
    assert result.results[0].error is None


def test_a_persistent_transport_error_stops_at_the_attempt_cap(prompt, tmp_path):
    """A 429 that never clears costs MAX_ATTEMPTS calls and then becomes one honest row."""
    testset = slice_testset(tmp_path, 1)

    def responder(item_id, nth):
        raise FakeTransportError("RateLimitError: 429", status_code=429)

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert len(client.calls) == runner.MAX_ATTEMPTS == 3
    assert result.results[0].outcome == "transport_error"
    assert "after 3 attempts" in result.results[0].error


def test_enterprise_one_attempt_records_http_status_and_does_not_recover(prompt, tmp_path):
    testset = slice_testset(tmp_path, 1)

    def always_fails(_item_id, _nth):
        raise FakeTransportError("BadRequest: 400", status_code=400)

    client = FakeClient(always_fails, testset=testset)
    result = run(
        testset,
        client=client,
        prompt=prompt,
        config=config(max_attempts=1, reasoning_effort="none"),
    )

    assert client.calls_for("RET-01") == 1
    assert client.calls[0]["reasoning_effort"] == "none"
    assert result.results[0].attempt_count == 1
    assert result.results[0].http_status == 400


def test_an_auth_failure_is_not_retried(prompt, tmp_path):
    """401 fails identically every time; retrying only delays telling a human.

    The split matters because "retry transport errors" read loosely would burn three
    calls per item on a bad key and report the run as slow rather than as misconfigured.
    """
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, nth):
        raise FakeTransportError("AuthenticationError: 401", status_code=401)

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert len(client.calls) == 2, "one call per item, no retries"
    assert [row.outcome for row in result.results] == ["transport_error"] * 2
    assert all("after" not in row.error for row in result.results)


def test_a_status_on_the_cause_is_read_like_one_on_the_exception(prompt, tmp_path):
    """`client.TransportError` keeps the SDK exception as `__cause__`; the status is there.

    This is the shape the real client produces, so the retry rule has to see through
    the wrapper without importing `openai` to name its classes.
    """
    testset = slice_testset(tmp_path, 1)

    def responder(item_id, nth):
        try:
            raise FakeTransportError("AuthenticationError: 401", status_code=401)
        except FakeTransportError as inner:
            raise RuntimeError("TransportError: AuthenticationError: 401") from inner

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert len(client.calls) == 1, "a 401 behind a wrapper was retried"
    assert result.results[0].outcome == "transport_error"


def test_a_harness_bug_is_raised_not_recorded_as_a_transport_failure(prompt, tmp_path):
    """A TypeError out of `complete` is this harness calling it wrongly, and it must shout.

    Recorded as `transport_error` it would produce a full run of dead items, coverage
    of zero, and a report blaming the model for a signature mismatch.
    """
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, nth):
        raise TypeError("complete() got an unexpected keyword argument 'top_p'")

    client = FakeClient(responder, testset=testset)
    with pytest.raises(TypeError):
        run(testset, client=client, prompt=prompt, config=config(concurrency=1))


# --------------------------------------------------------------------------- repeats


def test_repeats_three_yields_three_rows_per_item_numbered_from_one(prompt, tmp_path):
    """20 items would be 60 rows; here 4 items are 12, grouped and ordered by item."""
    testset = slice_testset(tmp_path, 4)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    result = run(testset, client=client, prompt=prompt, config=config(repeats=3))

    assert len(result.results) == 12
    assert [row.item_id for row in result.results] == [
        item.item_id for item in testset.items for _ in range(3)
    ]
    assert [row.replicate for row in result.results] == [1, 2, 3] * 4
    for item in testset.items:
        assert client.calls_for(item.item_id) == 3


def test_every_replicate_sends_an_identical_request(prompt, tmp_path):
    """Same seed, same temperature, same top_p, three times.

    Replicates exist to measure the provider's own variance at fixed decoding
    parameters -- `qwen3.6-27b` returned an empty response on run 2 of three identical
    Thai round-trips. Varying the seed per replicate would measure sampling spread
    instead, and the log could no longer say "the same request produced three
    different answers".
    """
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    run(testset, client=client, prompt=prompt, config=config(repeats=3))

    assert len(client.calls) == 3
    assert client.calls[0] == client.calls[1] == client.calls[2]
    assert client.calls[0]["temperature"] == 0.0
    assert client.calls[0]["top_p"] == 0.0  # production: main.py:1116-1119
    assert client.calls[0]["seed"] == 0
    assert client.calls[0]["max_tokens"] == 8000


# ------------------------------------------------------------------ observed models


def test_the_observed_model_histogram_is_built(prompt, full_testset):
    """A mid-run reroute is a fact the log can show, not an anomaly nobody can explain.

    OpenRouter routes across providers, so an arm half-served by a second checkpoint is
    two systems reported as one, and no downstream statistic recovers the split. The
    counts sum to fewer than the rows exactly when a call never got a response, which
    is the `transport_error` entry in `outcome_counts`.
    """
    positions = {item.item_id: index for index, item in enumerate(full_testset.items)}
    rerouted = full_testset.items[0].item_id
    dead = full_testset.items[1].item_id

    def responder(item_id, _nth):
        if item_id == dead:
            raise FakeTransportError("APIConnectionError: reset")
        if positions[item_id] % 5 == 0:
            return answer(observed_model="google/gemini-3.6-flash-preview-09-2025")
        return answer(observed_model="google/gemini-3.6-flash")

    client = FakeClient(responder, testset=full_testset)
    result = run(full_testset, client=client, prompt=prompt, config=config())

    histogram = result.observed_models()
    assert histogram == {
        "google/gemini-3.6-flash": 15,
        "google/gemini-3.6-flash-preview-09-2025": 4,
    }
    assert sum(histogram.values()) == len(result.results) - 1
    assert result.outcome_counts()["transport_error"] == 1
    assert rerouted in {row.item_id for row in result.results}


# ----------------------------------------------------------- classification passthrough


def test_parse_ok_and_repairs_come_from_classify(prompt, tmp_path):
    """The runner copies the verdict; it never forms one.

    Fenced JSON is the cheapest proof: `parse_ok` is True, and the repair is recorded
    rather than silently applied, so a run where every item needed unwrapping still
    looks different from one where none did.
    """
    testset = slice_testset(tmp_path, 1)
    fenced = "```json\n" + json.dumps(GOOD_PAYLOAD, ensure_ascii=False) + "\n```"
    client = FakeClient(
        lambda item_id, nth: FakeCompletion(content=fenced), testset=testset
    )

    result = run(
        testset,
        client=client,
        prompt=prompt,
        config=config(),
        response_format=RESPONSE_FORMAT,
    )

    row = result.results[0]
    assert row.outcome == "ok"
    assert row.parse_ok is True
    assert REPAIR_STRIP_FENCE in row.repairs
    assert row.payload["product"]["Postpaid"]["main"]["keyword"] == "เน็ตช้า"


def test_required_keys_are_read_out_of_the_response_format(prompt, tmp_path):
    """A missing top-level key is a schema violation, and the payload is kept anyway.

    The key list comes from the schema that was actually sent. A second hand-kept list
    is how a harness ends up enforcing a key the schema stopped requiring, and blaming
    the model for the difference.
    """
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(
        lambda item_id, nth: answer({"product": {}}), testset=testset
    )

    result = run(
        testset,
        client=client,
        prompt=prompt,
        config=config(),
        response_format=RESPONSE_FORMAT,
    )

    row = result.results[0]
    assert row.outcome == "schema_violation"
    assert row.parse_ok is False
    assert row.payload == {"product": {}}, "what it actually said is the next question"


def test_without_a_response_format_only_object_shape_is_required(prompt, tmp_path):
    """Presence can only be checked against keys someone asked for. No schema, no check."""
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(lambda item_id, nth: answer({"product": {}}), testset=testset)

    result = run(testset, client=client, prompt=prompt, config=config())

    assert result.results[0].outcome == "ok"


def test_truncated_rate_counts_every_attempted_call(prompt, tmp_path):
    """The denominator is the whole run. Computed over successes it would fall as the
    token budget got worse, which is backwards for the number whose job is to say the
    budget is wrong."""
    testset = slice_testset(tmp_path, 4)
    truncated = {testset.items[0].item_id, testset.items[1].item_id}

    def responder(item_id, nth):
        if item_id in truncated:
            return answer(finish_reason="length")
        return answer()

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert result.truncated_rate() == 0.5
    assert sum(1 for row in result.results if row.truncated) == 2


def test_total_cost_skips_unreported_costs_rather_than_counting_them_as_free(
    prompt, tmp_path
):
    """None is "not reported", not zero, so the total is a floor and says so."""
    testset = slice_testset(tmp_path, 3)
    silent = testset.items[2].item_id

    def responder(item_id, nth):
        if item_id == silent:
            return answer(cost=None)
        return answer(cost=0.25)

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    assert result.total_cost() == pytest.approx(0.5)
    assert sum(1 for row in result.results if row.cost is None) == 1


# --------------------------------------------------------------------------- run log


def test_write_run_log_is_one_line_per_row_utf8_no_bom_lf(prompt, tmp_path):
    """One line per `ItemResult`, Thai unescaped, no BOM, no CR, and every row self-describing.

    Rows carry the arm, both model ids, the prompt sha and the testset sha because
    comparing arms means concatenating logs, and a row that cannot say which arm
    produced it will eventually be attributed to the wrong one.
    """
    testset = slice_testset(tmp_path, 3)
    dead = testset.items[2].item_id

    def responder(item_id, nth):
        if item_id == dead:
            raise FakeTransportError("APIConnectionError: reset")
        return answer()

    client = FakeClient(responder, testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config(repeats=2))

    path = tmp_path / "logs" / "baseline.jsonl"
    write_run_log(result, path)

    raw = path.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "a BOM would break every downstream reader"
    assert b"\r" not in raw, "CRLF changes the bytes without changing the text on screen"
    assert "เน็ตช้า".encode("utf-8") in raw, "ensure_ascii=False; a \\u-escaped log is unread"

    lines = raw.decode("utf-8").splitlines()
    assert len(lines) == len(result.results) == 6

    rows = [json.loads(line) for line in lines]
    assert [row["item_id"] for row in rows] == [r.item_id for r in result.results]
    assert [row["replicate"] for row in rows] == [1, 2, 1, 2, 1, 2]
    assert {row["arm"] for row in rows} == {"baseline"}
    assert {row["prompt_sha"] for row in rows} == {result.prompt_sha}
    assert {row["testset_sha"] for row in rows} == {result.testset_sha}
    assert {row["model_requested"] for row in rows} == {"google/gemini-3.6-flash"}

    failures = [row for row in rows if row["outcome"] == "transport_error"]
    assert len(failures) == 2, "the dead item is in the log, both replicates of it"
    assert failures[0]["observed_model"] is None
    assert failures[0]["error"]


def test_write_run_log_is_byte_stable(prompt, tmp_path):
    """The same result written twice is the same file, so two runs can be diffed."""
    testset = slice_testset(tmp_path, 3)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config(repeats=2))

    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    write_run_log(result, first)
    write_run_log(result, second)

    assert first.read_bytes() == second.read_bytes()


def test_the_run_records_the_sha_of_the_file_it_actually_ran(prompt, tmp_path):
    """Provenance is the sliced file's bytes, not the full testset's."""
    testset = slice_testset(tmp_path, 2)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    result = run(testset, client=client, prompt=prompt, config=config())

    assert result.testset_sha == testset_sha(testset.path)
    assert result.testset_sha != testset_sha(TESTSET_PATH)
    assert result.prompt_sha == prompt.sha


# ------------------------------------------------------------------- refused configs


def test_a_prompt_id_that_does_not_match_the_prompt_is_refused(prompt, tmp_path):
    """Two arms silently sharing one prompt is a comparison that measures nothing."""
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    with pytest.raises(RunError, match="prompt_id"):
        run(testset, client=client, prompt=prompt, config=config(prompt_id="v9_16_e1"))

    assert client.calls == [], "refused before a single token was paid for"


def test_zero_repeats_is_refused(prompt, tmp_path):
    """A run that emits nothing reports 0/0 coverage, which reads like a clean sheet."""
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    with pytest.raises(RunError, match="repeats"):
        run(testset, client=client, prompt=prompt, config=config(repeats=0))


def test_an_empty_testset_is_refused(prompt, tmp_path):
    """Same reasoning, one level up: nothing to score is not the same as nothing wrong."""
    empty = tmp_path / "empty.jsonl"
    empty.write_bytes(b"\n")
    testset = load_testset(empty)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    with pytest.raises(RunError, match="no items"):
        run(testset, client=client, prompt=prompt, config=config())


def test_result_shapes_are_frozen():
    """The three dataclasses are immutable; a consumer cannot rebind a scored field."""
    row = ItemResult(
        item_id="RET-01",
        call_id="5001",
        replicate=1,
        outcome="ok",
        parse_ok=True,
        truncated=False,
        payload={},
        repairs=(),
        observed_model="m",
        provider="p",
        generation_id="gen-1",
        finish_reason="stop",
        raw_content="{}",
        latency_s=0.1,
        prompt_tokens=1,
        completion_tokens=1,
        reasoning_tokens=1,
        cost=0.1,
        error=None,
    )
    result = RunResult(config=config(), prompt_sha="a", testset_sha="b", results=(row,))

    with pytest.raises(Exception):
        row.parse_ok = False
    with pytest.raises(Exception):
        result.results = ()


# ============================================ backend identity and what survives a run
#
# MEASURED 2026-08-04, and the reason every test below exists: a 60-call
# `qwen/qwen3.6-27b` run was served by two backends under one model id.
# `observed_models()` reported `60 x qwen/qwen3.6-27b` and the split was invisible.
# 10 of those rows were `schema_violation` and none could be diagnosed afterwards,
# because the model's own text was dropped at the boundary between the client and the
# run log.


def test_the_pin_reaches_the_client_on_every_call(prompt, tmp_path):
    """`RunConfig.provider` is not decoration: it must arrive at `complete`, every time.

    A pin recorded in run.json but never sent produces exactly the run it was meant to
    prevent, described as the run it was meant to produce -- which is worse than not
    pinning at all, because the log now asserts something false.
    """
    testset = slice_testset(tmp_path, 3)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    run(testset, client=client, prompt=prompt,
        config=config(repeats=2, provider="DeepInfra"))

    assert [call["provider"] for call in client.calls] == ["DeepInfra"] * 6


def test_an_unpinned_run_sends_no_provider_rather_than_a_default(prompt, tmp_path):
    """None means unpinned and is sent as None. A default here would choose a backend
    for every model id anyone ever passes in, silently and without an argument."""
    testset = slice_testset(tmp_path, 2)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)

    run(testset, client=client, prompt=prompt, config=config())

    assert all(call["provider"] is None for call in client.calls)


def test_the_provider_histogram_sees_a_split_that_observed_models_cannot(prompt, tmp_path):
    """The 2026-08-04 defect, in miniature: one model id, two backends.

    `observed_models()` must show one key -- that is what actually happened and what
    made the split invisible -- while `observed_providers()` shows two. A test that let
    the model id differ too would prove nothing, because the existing guard already
    catches that case.
    """
    testset = slice_testset(tmp_path, 4)

    def responder(item_id, _nth):
        backend = "DeepInfra" if item_id in {"RET-01", "RET-02"} else "CoreWeave"
        return answer(provider=backend)

    result = run(testset, client=FakeClient(responder, testset=testset),
                 prompt=prompt, config=config())

    assert result.observed_models() == {"google/gemini-3.6-flash": 4}, (
        "the model id must NOT differ; this reproduces the case the id-based guard "
        "cannot see"
    )
    assert result.observed_providers() == {"CoreWeave": 2, "DeepInfra": 2}


def test_a_backend_that_reports_no_provider_yields_an_empty_histogram(prompt, tmp_path):
    """Absent is absent. An invented key would make an unavailable signal read as a
    measured one, and this histogram is checked for having exactly one entry."""
    testset = slice_testset(tmp_path, 2)
    client = FakeClient(lambda item_id, nth: answer(provider=None), testset=testset)

    result = run(testset, client=client, prompt=prompt, config=config())

    assert result.observed_providers() == {}


def test_prompt_token_spread_flags_one_item_served_by_two_tokenizers(prompt, tmp_path):
    """The proof that a pin held, and the only signal the router cannot echo.

    Every replicate of an item sends a byte-identical request, so `prompt_tokens` is a
    pure function of the backend's tokenizer. Two values means two builds. The numbers
    below are the measured clusters from the 2026-08-04 candidate run (RET-01 returned
    2587 and 3691 for the same bytes).
    """
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, nth):
        if item_id == "RET-01":
            return answer(prompt_tokens=2587 if nth == 1 else 3691)
        return answer(prompt_tokens=2561)

    result = run(testset, client=FakeClient(responder, testset=testset),
                 prompt=prompt, config=config(repeats=2))

    assert result.prompt_token_spread() == {"RET-01": (2587, 3691), "RET-02": (2561,)}
    assert result.split_items() == {"RET-01": (2587, 3691)}


def test_prompt_token_spread_keeps_an_item_whose_calls_all_died(prompt, tmp_path):
    """An item with no usable measurement is reported as empty, not omitted.

    Omitting it would shrink the denominator of "how many items did this check", so a
    run where every call failed would report 0 splits out of 0 items and read as a
    clean pin.
    """
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, _nth):
        if item_id == "RET-01":
            raise FakeTransportError("timeout")
        return answer(prompt_tokens=99)

    result = run(testset, client=FakeClient(responder, testset=testset),
                 prompt=prompt, config=config())

    assert result.prompt_token_spread() == {"RET-01": (), "RET-02": (99,)}
    assert result.split_items() == {}


def test_zero_usage_failure_cannot_manufacture_a_tokenizer_split(prompt, tmp_path):
    testset = slice_testset(tmp_path, 1)

    def responder(_item_id, nth):
        return answer(prompt_tokens=0 if nth == 1 else 2791)

    result = run(
        testset,
        client=FakeClient(responder, testset=testset),
        prompt=prompt,
        config=config(repeats=2),
    )

    assert result.prompt_token_spread() == {"RET-01": (2791,)}
    assert result.split_items() == {}
    assert result.calls_without_prompt_usage() == 1


def test_a_pinned_run_that_did_hold_reports_one_value_per_item(prompt, tmp_path):
    """The passing shape, asserted rather than assumed: 3 items x 3 replicates, one
    tokenizer. This is the assertion the real pinning proof runs."""
    testset = slice_testset(tmp_path, 3)
    client = FakeClient(lambda item_id, nth: answer(prompt_tokens=2600), testset=testset)

    result = run(testset, client=client, prompt=prompt,
                 config=config(repeats=3, provider="CoreWeave"))

    spread = result.prompt_token_spread()
    assert len(spread) == 3
    assert all(len(values) == 1 for values in spread.values())
    assert result.split_items() == {}


def test_the_models_own_text_survives_onto_every_row(prompt, tmp_path):
    """`raw_content` is kept whatever the verdict, including when the verdict is good.

    Keeping it only on failures would make the log's shape depend on the answer, and
    `repairs` would still say something was fixed with nothing to say what from.
    """
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, _nth):
        if item_id == "RET-01":
            return FakeCompletion(content="{not json at all")
        return answer()

    result = run(testset, client=FakeClient(responder, testset=testset),
                 prompt=prompt, config=config())

    bad, good = result.results
    assert bad.outcome != "ok" and bad.payload is None
    assert bad.raw_content == "{not json at all", (
        "the one row that needs explaining is the one whose text was dropped"
    )
    assert good.parse_ok and good.raw_content == json.dumps(GOOD_PAYLOAD, ensure_ascii=False)


def test_a_response_with_no_content_keeps_none_rather_than_an_empty_string(prompt, tmp_path):
    """None and the empty string are different failures and `outcomes` already tells
    them apart. Flattening either one here would put a second, disagreeing answer in
    the log."""
    testset = slice_testset(tmp_path, 2)

    def responder(item_id, _nth):
        return FakeCompletion(content=None if item_id == "RET-01" else "")

    result = run(testset, client=FakeClient(responder, testset=testset),
                 prompt=prompt, config=config())

    assert result.results[0].raw_content is None
    assert result.results[1].raw_content == ""


def test_oversized_text_is_truncated_and_says_so_in_the_string(prompt, tmp_path):
    """A silently clipped object reads as a model that stopped mid-object, which is the
    wrong conclusion to hand someone debugging a parse failure. Both counts are named.

    The filler is Thai, because the corpus is: a cap counted in characters must not
    become a cap counted in bytes without the test noticing.
    """
    testset = slice_testset(tmp_path, 1)
    huge = "ก" * (runner.RAW_CONTENT_MAX_CHARS + 500)
    client = FakeClient(lambda item_id, nth: FakeCompletion(content=huge), testset=testset)

    row = run(testset, client=client, prompt=prompt, config=config()).results[0]

    assert row.raw_content.startswith("ก" * runner.RAW_CONTENT_MAX_CHARS)
    assert f"kept {runner.RAW_CONTENT_MAX_CHARS} of {len(huge)} chars" in row.raw_content
    assert len(row.raw_content) < len(huge)


def test_text_at_exactly_the_cap_is_not_marked_as_truncated(prompt, tmp_path):
    """Off-by-one on the cap would put a truncation notice on a complete answer."""
    testset = slice_testset(tmp_path, 1)
    exact = "x" * runner.RAW_CONTENT_MAX_CHARS
    client = FakeClient(lambda item_id, nth: FakeCompletion(content=exact), testset=testset)

    row = run(testset, client=client, prompt=prompt, config=config()).results[0]

    assert row.raw_content == exact


def test_a_dead_call_records_no_provider_even_when_one_was_pinned(prompt, tmp_path):
    """`provider` is what answered, never what was asked for. Filling it from the pin
    would turn `observed_providers` into a restatement of the request."""
    testset = slice_testset(tmp_path, 1)

    def responder(_item_id, _nth):
        raise FakeTransportError("timeout")

    row = run(testset, client=FakeClient(responder, testset=testset), prompt=prompt,
              config=config(provider="DeepInfra")).results[0]

    assert row.outcome == "transport_error"
    assert row.provider is None
    assert row.generation_id is None
    assert row.finish_reason is None
    assert row.raw_content is None


def test_the_run_log_persists_every_field_the_client_saw(prompt, tmp_path):
    """The regression this whole section is about: four facts existed at the boundary
    and none of them reached disk. `client.py` claimed they were still recoverable from
    the run log; they were not, and ten schema violations went unexplained."""
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(
        lambda item_id, nth: answer(
            provider="CoreWeave", generation_id="gen-abc123",
            finish_reason="length", prompt_tokens=2587,
        ),
        testset=testset,
    )
    result = run(testset, client=client, prompt=prompt,
                 config=config(provider="CoreWeave"))

    log = tmp_path / "run.jsonl"
    write_run_log(result, log)
    row = json.loads(log.read_text(encoding="utf-8").strip())

    assert row["provider"] == "CoreWeave"
    assert row["provider_requested"] == "CoreWeave", (
        "the pair is the point: a served provider means nothing until you know which "
        "one was asked for, exactly as with model_requested/observed_model"
    )
    assert row["generation_id"] == "gen-abc123"
    assert row["finish_reason"] == "length"
    assert row["prompt_tokens"] == 2587
    assert json.loads(row["raw_content"]) == GOOD_PAYLOAD


def test_an_unpinned_run_log_says_so_rather_than_omitting_the_key(prompt, tmp_path):
    """A row with no `provider_requested` key leaves "was this pinned?" answerable only
    by the file's age, and that answer decides whether the arm is one system."""
    testset = slice_testset(tmp_path, 1)
    client = FakeClient(lambda item_id, nth: answer(), testset=testset)
    result = run(testset, client=client, prompt=prompt, config=config())

    log = tmp_path / "run.jsonl"
    write_run_log(result, log)
    row = json.loads(log.read_text(encoding="utf-8").strip())

    assert "provider_requested" in row and row["provider_requested"] is None
