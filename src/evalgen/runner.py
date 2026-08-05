"""One arm of a comparison, executed: every item, every replicate, in testset order.

This is the loop between the testset and the classifier. It calls the model, records
what came back, and judges nothing: `outcomes.classify` decides `parse_ok`, and this
module never recomputes it. A second `parse_ok` written inline here is precisely the
defect `outcomes.py` was built to prevent, and it would arrive in the one file with a
plausible excuse for having one ("we already have the response, it is right here").

Four refusals, each with its own docstring below because each is a way an evaluation
quietly reports a number it did not earn:

1. **Never retry a parse failure.** Re-rolling until a model emits valid JSON
   manufactures coverage. The arm that needed three attempts then scores identically
   to the arm that needed one.
2. **Never drop an item.** Every item emits exactly one `ItemResult` per replicate,
   including the ones whose HTTP call never returned. An arm whose dead items vanish
   has an easier denominator than its opponent (CHANGELOG.md, "Coverage accounting on
   every dimension").
3. **Never reorder the output.** Results come back in testset order regardless of
   which thread finished first, so two runs of the same testset produce logs that
   diff line for line.
4. **Never assume the model that answered is the model that was asked for.**
   `observed_model` is captured per call and summarised by `RunResult.observed_models`.
   MEASURED 2026-08-04: that check is necessary and insufficient. One 60-call arm was
   served by two different backends under a single model id, so the histogram had one
   key and reported nothing. `provider` and `RunResult.prompt_token_spread` are the
   two additions that see it -- the first because it names the backend, the second
   because it is the backend's own tokenizer answering and cannot be echoed back.
5. **Never discard the model's text because the harness decided it was wrong.** A
   `schema_violation` row whose content was dropped is a failure nobody can diagnose,
   which is how ten of them survived a full run without an explanation. `raw_content`
   is persisted for every row, truncated to a stated cap and never to nothing.

Threads, not asyncio
--------------------
`ThreadPoolExecutor` with a small `max_workers`. The OpenAI SDK's sync client is
thread-safe (it is an `httpx.Client` underneath, which is), the workload is 20-60
network-bound calls, and an event loop would be the only async thing in this
repository -- `evalharness` is synchronous, `outcomes` is synchronous, and the tests
are synchronous. Production's own batch path is asyncio (`main.py:1163-1169`), but it
is orchestrating GCS copies and a Vertex batch job, not 60 chat completions.

No `openai` import, on purpose
------------------------------
This module never imports `client.py`, so the SDK never enters its import graph. The
root `requirements.txt` pins the *scoring* path and deliberately omits `openai` (see
`src/evalgen/requirements.txt`), which is the environment CI builds and runs the suite
in. `outcomes.py` states the same rule for the same reason. The client is therefore a
duck-typed seam -- see `ClientLike` -- and the tests drive it with a fake that has no
network and no SDK behind it.

This module never prints, so it does not need `console.configure_stdout()`. The CLI
that wraps it does, first thing in `main()`, before this module is reached.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from evalgen import outcomes
from evalgen.prompts import Prompt, build_messages
from evalgen.testsets import TestItem, TestSet, testset_sha

__all__ = [
    "BACKOFF_BASE_S",
    "BACKOFF_CAP_S",
    "MAX_ATTEMPTS",
    "RAW_CONTENT_MAX_CHARS",
    "ItemResult",
    "RunConfig",
    "RunError",
    "RunResult",
    "run",
    "write_run_log",
]

# One initial call plus two retries, and only for the failures listed in
# `_is_retryable`. Small on purpose: a run that needs many retries has a problem a
# human should see now (a rate limit set too low, a provider degraded) rather than one
# the harness absorbs by taking four times as long and reporting nothing unusual.
MAX_ATTEMPTS = 3

# Capped exponential backoff: 1s then 2s. Module-level rather than baked into the
# arithmetic so a test can flatten them to zero and prove the retry policy without
# spending wall clock on it -- see tests/test_runner.py.
#
# Known limitation, stated rather than discovered later: there is no jitter and
# `Retry-After` is ignored. With `concurrency=4` a 429 typically hits all four workers
# at once and they will retry in step. Adding jitter would make a run log's latency
# story non-reproducible for a benefit that four workers do not need; if concurrency
# ever grows past a handful, revisit this line first.
BACKOFF_BASE_S = 1.0
BACKOFF_CAP_S = 16.0

# How much of the model's own text each row keeps. A cap rather than the whole string
# because `max_tokens=8000` of Thai can exceed 100 KB, and a 60-row log that nobody
# will open is a log that stops catching things.
#
# 12000 characters is chosen against the thing this field exists to diagnose. The
# retention schema's valid answer is roughly 1-3 KB of Thai; the failures worth reading
# are a truncated object, an object wrapped in prose, a code fence, or a refusal, and
# all four are legible in their opening. The cap is deliberately several times the size
# of a correct answer so that a `schema_violation` is almost never cut at all, and the
# few that are, are cut in their tail.
#
# **Truncation is marked in the string itself, not merely implied.** A silently clipped
# JSON object reads as a model that stopped mid-object, which is a different defect
# from a harness that stopped copying -- and it is the exact wrong conclusion to hand
# someone debugging a parse failure. `_truncate_raw` appends the counts.
RAW_CONTENT_MAX_CHARS = 12000

# Raised through rather than recorded as a transport failure. `OpenRouterClient.complete`
# already funnels every SDK and HTTP failure into `TransportError` (client.py:197-201),
# so a raw TypeError or AttributeError escaping the call is this harness calling the
# client wrongly, not the network failing. Recorded as `transport_error` it would
# produce a full run of dead items, a coverage of zero, and a report that blames the
# model for a signature mismatch. Failing loudly on the first item is the cheaper bug.
_PROGRAMMING_ERRORS = (TypeError, AttributeError, NameError, ImportError)


class RunError(ValueError):
    """Raised when a run is configured in a way that would produce a misleading result.

    Every check that raises this is a check whose failure mode is a run that completes
    and reports something wrong: a prompt that is not the prompt the arm claims to use,
    a testset with nothing in it, a replicate count of zero.
    """


class CompletionLike(Protocol):
    """The fields this runner reads off a completion.

    A structural type rather than `client.Completion` itself, because naming that class
    here would import `openai` (see the module docstring). It lists exactly what is
    read, so a fake in a test and a real completion are the same contract rather than
    the same accident.
    """

    content: str | None
    finish_reason: str | None
    observed_model: str | None
    generation_id: str | None
    provider: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cost: float | None
    latency_s: float


class ClientLike(Protocol):
    """The one method this runner calls. Mirrors `client.OpenRouterClient.complete`."""

    def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        seed: int | None = None,
        response_format: Mapping[str, Any] | None = None,
        provider: str | None = None,
    ) -> CompletionLike:
        ...  # pragma: no cover - a shape, never executed


@dataclass(frozen=True)
class RunConfig:
    """Everything that decides what one call looks like, in one hashable object.

    The decoding defaults are production's, transcribed from `main.py:1116-1119`:
    `temperature: 0.0`, `topP: 0`, `seed: 0`. `top_p=0.0` is kept as production wrote
    it rather than "corrected" to 1.0; it is a stated production value and this harness
    reproduces the arm it is comparing against, not the arm it would have configured.

    **`max_tokens` deliberately differs.** Production sends `maxOutputTokens: 65535`.
    8000 is the harness default because both candidates are reasoning models that spend
    completion tokens before emitting a visible one -- `qwen3.6-27b` was measured at 105
    reasoning tokens for 16 visible on a one-word prompt
    (scripts/openrouter-smoketest/README.md:122-126) -- and OpenRouter bills those
    tokens. 8000 is far above the observed envelope for one JSON object while capping
    what a runaway generation can cost. The check that this default is not too small is
    not an opinion: `RunResult.truncated_rate()` and the `empty_length` count in
    `outcome_counts()` are the measurement, and a non-zero `empty_length` means the
    budget was the bug, not the model (`outcomes._classify_empty`).

    **`repeats` sends the SAME request every time**, seed included. That is the point:
    at fixed decoding parameters the remaining variance belongs to the provider, and it
    is real -- `qwen3.6-27b` returned an empty response on run 2 of three identical Thai
    round-trips (README.md:140-148). Varying the seed per replicate would measure
    sampling spread instead, and the log could no longer say "the same request produced
    three different answers".

    `arm` and `prompt_id` are identity, not behaviour: they name the thing being
    measured so that a row in a concatenated log still knows which arm produced it.

    **`provider` defaults to None, which means unpinned, which is what produced the
    two-backend run.** It is not defaulted to a name because the right name is a
    per-model decision that has to be made and defended (production's regime, the
    parameters the endpoint can honour), and a default here would make that decision
    invisibly for every future model id. None is the honest default and the run log
    says so; `RunResult.prompt_token_spread` is what tells you whether it cost you.
    """

    model: str
    arm: str
    prompt_id: str
    temperature: float = 0.0
    top_p: float | None = 0.0  # production: main.py:1116-1119
    seed: int | None = 0
    max_tokens: int = 8000
    repeats: int = 3
    concurrency: int = 4
    provider: str | None = None


@dataclass(frozen=True)
class ItemResult:
    """One item, one replicate, one row -- whether or not a response ever arrived.

    `replicate` is numbered from 1. A log whose first replicate is 0 reads as an
    off-by-one to the analyst who did not write it, and this file is not the place to
    spend that confusion.

    `outcome`, `parse_ok`, `truncated`, `payload` and `repairs` are copied verbatim off
    the `outcomes.Classified` that judged the response. Nothing here re-derives them.

    `error` is set only for a call that never produced a response, and carries the
    exception type, its message, and the attempt count when more than one was made --
    the only place the retry count survives, and the answer to "did this item cost us
    three calls?" when the cost table is read.

    `latency_s` is the last attempt's request time, excluding backoff sleeps. A 120s
    timeout and an instant 401 are the same outcome and very different facts
    (`client.TransportError`); folding sleep into the number would erase that.

    **`raw_content` is the model's own text, and it is here because its absence was
    measured.** `payload` holds what `classify` managed to parse, which for the rows
    that matter most is None. Ten `schema_violation` rows in the 2026-08-04 candidate
    run were therefore unexplainable after the fact: the harness had recorded that the
    answer was wrong and thrown away the answer. Truncated to `RAW_CONTENT_MAX_CHARS`
    with the truncation stated in the string. None only when no response arrived, or
    when the response genuinely carried no content -- and those two are told apart by
    `error` and by `outcome`, not by guessing at this field.

    `finish_reason`, `generation_id` and `provider` are the other three facts that
    existed at the boundary and did not survive it. `finish_reason` separates "the
    model stopped" from "the budget stopped it" from "the provider gave up"
    (`_is_abandoned_generation` acts on it and nothing recorded it). `generation_id`
    is the handle OpenRouter's own `/generation` endpoint takes, so a row can be
    followed up days later against the router's records rather than argued about.
    `provider` names the backend -- see `RunResult.observed_providers`.
    """

    item_id: str
    call_id: str
    replicate: int
    outcome: str
    parse_ok: bool
    truncated: bool
    payload: dict | None
    repairs: tuple[str, ...]
    observed_model: str | None
    provider: str | None
    generation_id: str | None
    finish_reason: str | None
    raw_content: str | None
    latency_s: float
    prompt_tokens: int | None
    completion_tokens: int | None
    reasoning_tokens: int | None
    cost: float | None
    error: str | None


@dataclass(frozen=True)
class RunResult:
    """One arm's complete output, plus the provenance needed to cite it.

    `prompt_sha` and `testset_sha` are stored rather than recomputed on demand, because
    a result that re-reads its inputs when asked would report today's files against
    yesterday's numbers.
    """

    config: RunConfig
    prompt_sha: str
    testset_sha: str
    results: tuple[ItemResult, ...]

    def observed_models(self) -> dict[str, int]:
        """Histogram of the model that actually answered, keyed by id, sorted by id.

        The reason this exists at all: OpenRouter routes across providers, and an arm
        half-served by a different checkpoint is two systems reported as one, which no
        downstream statistic can un-blend. A run whose histogram has one key is one
        arm; a run whose histogram has two needs explaining before its accuracy means
        anything.

        Calls that never produced a response contribute nothing, so the counts sum to
        fewer than `len(results)` exactly when some call failed in transport. That gap
        is not hidden: it is the `transport_error` entry in `outcome_counts`.
        """
        counts: dict[str, int] = {}
        for item in self.results:
            if item.observed_model is None:
                continue
            counts[item.observed_model] = counts.get(item.observed_model, 0) + 1
        return {name: counts[name] for name in sorted(counts)}

    def observed_providers(self) -> dict[str, int]:
        """Histogram of the BACKEND that answered, keyed by OpenRouter provider name.

        `observed_models` asks "which model id answered"; this asks "whose hardware
        ran it", and the 2026-08-04 candidate run is the case where the first question
        has one answer and the second has two. Two providers under one model id is two
        builds -- different quantisation, different chat template, different tokenizer
        -- reported as one arm.

        Rows with no provider contribute nothing, so a run against a backend that does
        not expose the field yields an empty dict rather than a fabricated key. An
        empty histogram is not a clean sheet: it means this signal is unavailable and
        `prompt_token_spread` is the one to read.
        """
        counts: dict[str, int] = {}
        for item in self.results:
            if item.provider is None:
                continue
            counts[item.provider] = counts.get(item.provider, 0) + 1
        return {name: counts[name] for name in sorted(counts)}

    def prompt_token_spread(self) -> dict[str, tuple[int, ...]]:
        """Per item, the distinct `prompt_tokens` values its replicates came back with.

        **This is the evidence that a pin held, and it is the only one that cannot be
        faked.** Every replicate of one item sends a byte-identical request
        (`RunConfig.repeats`), so the prompt token count is a pure function of the
        backend's tokenizer. One value per item means one tokenizer answered. Two means
        two builds served the arm, whatever the `provider` field said -- that field is
        the router describing its own routing, and asking it to confirm the routing is
        circular.

        Measured on the two 2026-08-04 runs: the incumbent had 0 of 20 items with more
        than one value; the unpinned candidate had 14 of 20, with the two clusters
        sitting at 2538-2643 and 3583-3931 tokens for the same text.

        Keyed by item, sorted, values ascending. Items whose calls all failed in
        transport report an empty tuple rather than being omitted, so the denominator
        of "how many items did this actually check" stays visible to the caller instead
        of being inferred from a dict's length.
        """
        spread: dict[str, set[int]] = {}
        for item in self.results:
            seen = spread.setdefault(item.item_id, set())
            if item.prompt_tokens is not None:
                seen.add(item.prompt_tokens)
        return {name: tuple(sorted(spread[name])) for name in sorted(spread)}

    def split_items(self) -> dict[str, tuple[int, ...]]:
        """The items that were served by more than one tokenizer. Empty is the pass.

        A named method rather than a comprehension at each call site, because this is
        the assertion a pinned run is checked against and it should read the same way
        in the CLI, in a test and in a review.
        """
        return {
            item_id: values
            for item_id, values in self.prompt_token_spread().items()
            if len(values) > 1
        }

    def outcome_counts(self) -> dict[str, int]:
        """How many rows landed in each outcome. Sums to `len(results)`, always.

        That sum is the invariant worth testing: it is the arithmetic form of "every
        item emits exactly one row per replicate, including failures". If it ever
        totals less, an item was dropped somewhere and the arm's denominator got easier
        than its opponent's.

        Only observed outcomes appear. A zero-filled vocabulary would read as though
        the run had checked for a refusal and found none, when the truthful statement
        is that no row was classified that way.
        """
        counts: dict[str, int] = {}
        for item in self.results:
            counts[item.outcome] = counts.get(item.outcome, 0) + 1
        return {name: counts[name] for name in sorted(counts)}

    def truncated_rate(self) -> float:
        """Share of rows whose response hit the token budget.

        The denominator is every attempted call, not every parsed one. A rate computed
        over successes only would fall as the budget got worse, which is the wrong
        direction for the one number whose job is to tell you the budget is wrong.

        Read it with `outcome_counts()['empty_length']`: truncation after a complete
        JSON object is survivable and still flagged, truncation before one is an
        `empty_length` row and a run-configuration bug.
        """
        if not self.results:
            return 0.0
        return sum(1 for item in self.results if item.truncated) / len(self.results)

    def total_cost(self) -> float:
        """Sum of the reported per-call costs, in USD.

        A **lower bound**, not a total: OpenRouter reports `usage.cost` only for
        providers that supply it, and `client.Completion` keeps a missing value as
        None rather than zero so that "not reported" and "measured as zero" stay
        different facts. Rows with no cost are skipped here rather than counted as
        free, and a caller quoting this number beside `len(results)` can see how much
        of the run it actually covers.
        """
        return sum(item.cost for item in self.results if item.cost is not None)


def _required_keys(response_format: Mapping[str, Any] | None) -> tuple[str, ...]:
    """The top-level keys `classify` must find, read out of the schema that was sent.

    Read from the request rather than written down again, because a second hand-kept
    list is how a harness ends up enforcing a key the schema stopped requiring -- and
    that mismatch shows up as `schema_violation` rows blamed on the model.

    Recognises the two shapes a caller can plausibly pass: an OpenAI-style
    `{"type": "json_schema", "json_schema": {"schema": {...}}}` wrapper, and a bare
    JSON Schema object. Anything else yields no required keys, which means `classify`
    checks that the response is an object and stops there. That is the honest floor:
    presence can only be checked against keys someone actually asked for.
    """
    if not response_format:
        return ()

    candidates: list[Any] = [response_format]
    wrapper = response_format.get("json_schema")
    if isinstance(wrapper, Mapping):
        candidates.append(wrapper)
        if isinstance(wrapper.get("schema"), Mapping):
            candidates.append(wrapper["schema"])
    if isinstance(response_format.get("schema"), Mapping):
        candidates.append(response_format["schema"])

    for candidate in reversed(candidates):
        required = candidate.get("required")
        if isinstance(required, (list, tuple)) and all(isinstance(k, str) for k in required):
            return tuple(required)
    return ()


def _status_code(exc: BaseException) -> int | None:
    """The HTTP status behind a failed call, or None when no response ever arrived.

    Read by attribute off the exception and its `__cause__`, never by importing
    `openai` to name its classes: `client.TransportError` keeps the SDK exception as
    `__cause__`, and every SDK status error carries `status_code` (directly, or on its
    `response`). Duck-typing here is what lets this module -- and its tests -- run in
    the environment CI builds, which has no SDK in it.

    None is a meaningful answer, not a failure to find one: a timeout, a reset
    connection or a DNS failure has no status because there was no response.
    """
    for candidate in (exc, exc.__cause__):
        if candidate is None:
            continue
        for holder in (candidate, getattr(candidate, "response", None)):
            status = getattr(holder, "status_code", None)
            if isinstance(status, int):
                return status
    return None


def _is_retryable(exc: BaseException) -> bool:
    """Whether this failure may be tried again. Transport only, never a parse failure.

    The line this draws is the difference between recovering a lost request and
    re-rolling a sample:

      * **No status at all** -- timeout, connection reset, DNS. No generation was
        produced, so there is nothing to re-roll: trying again cannot launder a bad
        answer into a good one, because there was no answer.
      * **429 and 5xx** -- the provider said "later" or "my fault". Same argument.
      * **Everything else** (401, 400, 402, 404) fails identically on every attempt. A
        retry buys nothing but delay before a human is told the key is wrong.

    A response that *did* arrive and could not be parsed never reaches this function.
    That is deliberate and it is the rule the whole harness is built around: `classify`
    has no retry parameter, `OpenRouterClient` is constructed with `max_retries=0` so
    the SDK cannot re-roll one either, and this runner returns the classified failure
    as the measurement.
    """
    status = _status_code(exc)
    if status is None:
        return True
    return status == 429 or 500 <= status < 600


def _is_abandoned_generation(completion: CompletionLike) -> bool:
    """A 200 that carries a provider failure instead of a generation. Retryable.

    The other half of `_is_retryable`, for a failure that arrives as a successful HTTP
    response and therefore never raises. MEASURED on 2026-08-04: `gemini-2.5-flash`
    returned HTTP 200 with `finish_reason="error"`, 86 characters of a half-written
    object, `completion_tokens=0`, `cost=$0.00000`, and `choices[0].error` reading
    `code: 429, error_type: rate_limit_exceeded`.

    **Both conditions are load-bearing, and the second is what keeps the rule against
    re-rolling a sample intact.** `finish_reason == "error"` alone would let the
    harness retry a generation that WAS produced, billed and abandoned partway -- that
    is a sample, and trying again would draw a second one and keep whichever arrived.
    Zero completion tokens means no sample was drawn, which is exactly the test
    `_is_retryable` applies to a timeout: "no generation was produced, so there is
    nothing to re-roll".

    A provider that reports no usage at all (`completion_tokens is None`) is treated
    the same way: that is the shape of the failure above, and the alternative -- not
    retrying because an optional accounting field is missing -- would make the policy
    depend on which extensions a provider happens to send.

    An abandoned generation that DID produce tokens is not retried. It is recorded as
    `provider_error` by `outcomes.classify`, where it is counted and visible.
    """
    if getattr(completion, "finish_reason", None) != "error":
        return False
    tokens = getattr(completion, "completion_tokens", None)
    return tokens is None or tokens == 0


def _backoff_delay(attempt: int) -> float:
    """Seconds to wait after `attempt` (1-based) failed. Capped exponential: 1s, 2s, ...

    Looked up through module globals at call time so a test can set `BACKOFF_BASE_S` to
    zero and exercise the retry policy without spending the wall clock.
    """
    return min(BACKOFF_CAP_S, BACKOFF_BASE_S * (2 ** (attempt - 1)))


def _truncate_raw(content: str | None) -> str | None:
    """The model's text, capped at `RAW_CONTENT_MAX_CHARS`, saying so when it cut.

    None passes through as None. That distinction is load-bearing: "the model returned
    nothing" and "the model returned an empty string" are different failures, and
    `outcomes._classify_empty` already tells them apart -- flattening either to `""`
    here would put a second, disagreeing answer in the log.

    The marker names both numbers because a reader who sees only "truncated" has to go
    and find out by how much, and a reader who sees the original length can tell a
    2 KB refusal from a 200 KB runaway generation without opening anything else.
    """
    if content is None or len(content) <= RAW_CONTENT_MAX_CHARS:
        return content
    kept = content[:RAW_CONTENT_MAX_CHARS]
    return f"{kept}\n...[evalgen truncated: kept {RAW_CONTENT_MAX_CHARS} of {len(content)} chars]"


def _result_from_completion(
    item: TestItem,
    replicate: int,
    completion: CompletionLike,
    required_keys: tuple[str, ...],
) -> ItemResult:
    """Turn one response into one row. `parse_ok` comes from `classify` and nowhere else.

    Note what is copied and what is decided: everything about *whether the response
    counted* is read off the `Classified`, and everything about *what the call cost* is
    read off the completion. This function contains no judgement of its own.

    **`raw_content` is copied from the same string `classify` was handed**, not
    re-derived and not conditioned on the verdict. A row that kept the text only when
    the verdict was bad would be a log that changes shape depending on the answer, and
    a `parse_ok` row whose text nobody kept is exactly how a repaired-but-wrong payload
    goes unnoticed -- `classified.repairs` says something was fixed and only this field
    can say what it was fixed from.
    """
    classified = outcomes.classify(
        completion.content,
        completion.finish_reason,
        required_keys=required_keys,
    )
    return ItemResult(
        item_id=item.item_id,
        call_id=item.call_id,
        replicate=replicate,
        outcome=classified.outcome,
        parse_ok=classified.parse_ok,
        truncated=classified.truncated,
        payload=classified.payload,
        repairs=classified.repairs,
        observed_model=completion.observed_model,
        provider=completion.provider,
        generation_id=completion.generation_id,
        finish_reason=completion.finish_reason,
        raw_content=_truncate_raw(completion.content),
        latency_s=completion.latency_s,
        prompt_tokens=completion.prompt_tokens,
        completion_tokens=completion.completion_tokens,
        reasoning_tokens=completion.reasoning_tokens,
        cost=completion.cost,
        error=None,
    )


def _result_from_failure(
    item: TestItem,
    replicate: int,
    exc: BaseException,
    attempts: int,
    latency_s: float,
) -> ItemResult:
    """The row for a call that never returned. It is a row, and that is the point.

    Built from `outcomes.transport_error()` rather than from a locally invented outcome
    string, so a dead request and a bad response are counted in the same vocabulary and
    land in the same coverage accounting. Dropping it instead would give this arm a
    smaller denominator than the arm whose network held up.

    Usage fields are None throughout: nothing was billed for a request that failed, and
    a zero would be indistinguishable from a provider that reported zero.

    `provider`, `generation_id`, `finish_reason` and `raw_content` are None for the
    same reason and it is the stronger one here: no response existed, so there is no
    backend to name and no text to keep. In particular `provider` is NOT filled in from
    the pin that was requested -- a row claiming DeepInfra answered when nothing
    answered would turn `observed_providers` from a measurement into a restatement of
    the request.
    """
    classified = outcomes.transport_error()
    detail = f"{type(exc).__name__}: {exc}"
    if attempts > 1:
        detail = f"{detail} (after {attempts} attempts)"
    return ItemResult(
        item_id=item.item_id,
        call_id=item.call_id,
        replicate=replicate,
        outcome=classified.outcome,
        parse_ok=classified.parse_ok,
        truncated=classified.truncated,
        payload=classified.payload,
        repairs=classified.repairs,
        observed_model=None,
        provider=None,
        generation_id=None,
        finish_reason=None,
        raw_content=None,
        latency_s=latency_s,
        prompt_tokens=None,
        completion_tokens=None,
        reasoning_tokens=None,
        cost=None,
        error=detail,
    )


def _run_one(
    item: TestItem,
    replicate: int,
    *,
    client: ClientLike,
    prompt: Prompt,
    config: RunConfig,
    response_format: Mapping[str, Any] | None,
    required_keys: tuple[str, ...],
) -> ItemResult:
    """One item, one replicate, up to `MAX_ATTEMPTS` transport attempts, exactly one row.

    The messages are built once, outside the retry loop, so every attempt sends the
    identical request. Rebuilding them per attempt would be harmless today and would
    quietly stop being harmless the moment anything in the prompt path became
    time-dependent.

    Only `client.complete` sits inside the `try`. A failure anywhere else -- assembling
    the prompt, classifying the response -- is this harness's bug and must not be filed
    as the network's.

    A response that arrived can also be retried, but only through the one door
    `_is_abandoned_generation` opens: a 200 whose `finish_reason` is `"error"` and
    which produced no completion tokens. That is a request that failed on the way,
    dressed as a success; it is not a parse failure and retrying it cannot re-roll a
    sample, because there is no sample. Nothing else about a response is ever retried.
    """
    messages = build_messages(item, prompt)

    attempts = 0
    while True:
        attempts += 1
        start = time.monotonic()
        try:
            completion = client.complete(
                model=config.model,
                messages=messages,
                max_tokens=config.max_tokens,
                temperature=config.temperature,
                top_p=config.top_p,
                seed=config.seed,
                response_format=response_format,
                provider=config.provider,
            )
        except _PROGRAMMING_ERRORS:
            raise
        except Exception as exc:  # noqa: BLE001 - any failed request is one outcome
            # `exc.latency_s` is the client's own measurement of the request that
            # failed; the local clock is the fallback for a client that does not
            # report one. Either way this is the last attempt's request time, with
            # backoff excluded.
            measured = getattr(exc, "latency_s", None)
            latency_s = measured if isinstance(measured, (int, float)) else time.monotonic() - start

            if attempts < MAX_ATTEMPTS and _is_retryable(exc):
                time.sleep(_backoff_delay(attempts))
                continue
            return _result_from_failure(item, replicate, exc, attempts, float(latency_s))

        if attempts < MAX_ATTEMPTS and _is_abandoned_generation(completion):
            time.sleep(_backoff_delay(attempts))
            continue
        return _result_from_completion(item, replicate, completion, required_keys)


def run(
    testset: TestSet,
    *,
    client: ClientLike,
    prompt: Prompt,
    config: RunConfig,
    response_format: Mapping[str, Any] | None = None,
    progress: Callable[[int, int, ItemResult], None] | None = None,
) -> RunResult:
    """Run every item, `config.repeats` times each, and return the rows in testset order.

    **Testset order, never completion order.** The pool hands work back as it finishes,
    which depends on the network, the provider's queue and how many workers were free.
    Writing that order into the log would mean two runs of the same testset produce
    files that cannot be diffed, and a reviewer comparing two arms would be reading
    scheduling noise. Each task therefore owns a fixed slot, assigned before any call
    is made, and completion order survives only in the `progress` callback -- which is
    where it is useful and nowhere else.

    `progress` is called on the calling thread as each row lands, with
    `(done, total, result)`. Exceptions from it are swallowed: it is a display detail,
    and a broken progress bar must not destroy results that have already been paid for.

    Raises `RunError` before any call is made when the configuration would produce a
    misleading result. The prompt id check is the one that matters: two arms silently
    sharing one prompt is a comparison that measures nothing and reports a number
    anyway (`prompts.get` refuses the same mistake from the other direction).
    """
    if config.prompt_id != prompt.id:
        raise RunError(
            f"config.prompt_id is {config.prompt_id!r} but the prompt passed in is "
            f"{prompt.id!r}. The run log would name a prompt the model never saw, and a "
            "comparison between two arms labelled with the wrong prompt measures nothing."
        )
    if config.repeats < 1:
        raise RunError(
            f"repeats={config.repeats} produces no rows at all. A run that emits nothing "
            "reports 0/0 coverage, which reads exactly like a run with no failures."
        )
    if config.concurrency < 1:
        raise RunError(f"concurrency={config.concurrency} would start no workers.")
    if not testset.items:
        raise RunError(
            f"{testset.path} has no items. An empty run scores 0 of 0 on every dimension "
            "and prints a table that looks like a clean sheet."
        )

    required_keys = _required_keys(response_format)

    # Slots first, calls second. The order of this list IS the order of the output, and
    # it is fixed before a single request leaves the process.
    tasks: list[tuple[TestItem, int]] = [
        (item, replicate)
        for item in testset.items
        for replicate in range(1, config.repeats + 1)
    ]
    slots: list[ItemResult | None] = [None] * len(tasks)

    with ThreadPoolExecutor(
        max_workers=config.concurrency, thread_name_prefix=f"evalgen-{config.arm}"
    ) as pool:
        futures = {
            pool.submit(
                _run_one,
                item,
                replicate,
                client=client,
                prompt=prompt,
                config=config,
                response_format=response_format,
                required_keys=required_keys,
            ): slot
            for slot, (item, replicate) in enumerate(tasks)
        }
        for done, future in enumerate(as_completed(futures), start=1):
            # `.result()` re-raises whatever the worker raised, which by construction is
            # only a programming error (see `_PROGRAMMING_ERRORS`). Letting it out aborts
            # the run: a harness that calls its client wrongly fails every item the same
            # way, so there is nothing here worth keeping.
            result = future.result()
            slots[futures[future]] = result
            if progress is not None:
                try:
                    progress(done, len(tasks), result)
                except Exception:  # noqa: BLE001 - a progress bar must never lose a run
                    pass

    missing = [index for index, value in enumerate(slots) if value is None]
    if missing:  # pragma: no cover - unreachable; the invariant is stated, not assumed
        raise RunError(
            f"{len(missing)} of {len(tasks)} tasks produced no row (slots {missing[:5]}...). "
            "Every item must emit exactly one row per replicate, including failures."
        )

    return RunResult(
        config=config,
        prompt_sha=prompt.sha,
        testset_sha=testset_sha(testset.path),
        results=tuple(item for item in slots if item is not None),
    )


def write_run_log(result: RunResult, path: Path) -> None:
    """Write the run as JSONL: one line per `ItemResult`, UTF-8 with no BOM, LF endings.

    **Every row carries the run's identity**, not just the item's. Comparing two arms
    means putting their logs side by side, and concatenated rows that cannot say which
    arm, which model, which prompt and which testset produced them are rows that will
    eventually be attributed to the wrong one. The requested model sits beside the
    observed model for the same reason: the pair is what makes a mid-run reroute
    visible, and either alone hides it.

    `ensure_ascii=False` because the corpus is Thai and a log full of `\\u0e2a` escapes
    is a log nobody reads, which is a log that stops catching things. `newline="\\n"`
    because Python would otherwise translate to CRLF on Windows, and `load_testset`
    already refuses a CR for the reason that applies here too: bytes that change
    without the text changing on screen.

    Keys are written in a fixed, meaningful order rather than sorted, so the identity
    of a row is readable at the start of the line and the payload sits at the end where
    it can be skipped by eye. `raw_content` sits last, after `payload`, because it is
    the longest field and the one a reader skips until they need it -- and on the rows
    where they need it, `payload` is None anyway.

    **`provider_requested` is written beside `provider`**, the same pairing as
    `model_requested` beside `observed_model` and for the same reason: either one alone
    hides a fallback. A row saying `provider: "CoreWeave"` is only interesting once you
    know whether CoreWeave was asked for.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for item in result.results:
            row = {
                "arm": result.config.arm,
                "model_requested": result.config.model,
                "observed_model": item.observed_model,
                "provider_requested": result.config.provider,
                "provider": item.provider,
                "generation_id": item.generation_id,
                "prompt_id": result.config.prompt_id,
                "prompt_sha": result.prompt_sha,
                "testset_sha": result.testset_sha,
                "item_id": item.item_id,
                "call_id": item.call_id,
                "replicate": item.replicate,
                "outcome": item.outcome,
                "parse_ok": item.parse_ok,
                "truncated": item.truncated,
                "finish_reason": item.finish_reason,
                "repairs": list(item.repairs),
                "error": item.error,
                "latency_s": item.latency_s,
                "prompt_tokens": item.prompt_tokens,
                "completion_tokens": item.completion_tokens,
                "reasoning_tokens": item.reasoning_tokens,
                "cost": item.cost,
                "payload": item.payload,
                "raw_content": item.raw_content,
            }
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
