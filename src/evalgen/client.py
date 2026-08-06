"""The only file in this package that imports `openai`.

`src/evalharness/` makes no model calls at all, and `tests/test_boundary.py` enforces
that by parsing its imports. `src/evalgen/` is the other side of that line: it exists
to call models, so the client dependency is concentrated here and nowhere else. Every
other module in this package -- `outcomes.py` above all -- stays importable, testable
and runnable with `openai` absent.

That is not tidiness. The root `requirements.txt` pins the *scoring* path to
production's own versions and deliberately does not carry `openai` (the SDK is
declared separately, in `src/evalgen/requirements.txt`), so the environment CI builds
has no client in it. If `outcomes.py` reached for one, the rule that decides
`parse_ok` would stop being testable in the environment that runs the suite.

Per canon and AGENTS.md ("Service layers do not apply"), model calls route through
OpenRouter rather than a provider SDK directly. AGENTS.md anticipated this file:
*"If a future version runs candidate models as part of a real evaluation pipeline
(not just a pipe test), that is new work with its own review... and OpenRouter becomes
a real dependency of `src/` at that point."*
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from openai import OpenAI

from evalgen.request import build_request

OPENROUTER_BASE_URL: Final = "https://openrouter.ai/api/v1"

# Optional attribution headers; OpenRouter uses them for its public leaderboard.
# Harmless to omit, kept consistent with scripts/openrouter-smoketest/smoketest.py.
_DEFAULT_HEADERS: Final = {
    "HTTP-Referer": "https://github.com/tkhongsap/model-eval-harness",
    "X-Title": "model-eval-harness evalgen",
}


class TransportError(RuntimeError):
    """The request failed before any response existed: timeout, connection, HTTP error.

    Wraps whatever the SDK raised so that callers can catch a request failure without
    importing `openai` themselves, which is what keeps this the only module that does.
    The original exception is available as `__cause__`, and `latency_s` records how
    long the failure took -- a 120s timeout and an instant 401 are the same outcome
    and very different facts.

    Callers record this as `outcomes.transport_error()`. It is deliberately raised
    rather than returned: only the caller knows whether one dead item should be logged
    and stepped over, or whether the run should stop.
    """

    def __init__(self, message: str, *, latency_s: float) -> None:
        super().__init__(message)
        self.latency_s = latency_s


@dataclass(frozen=True)
class Completion:
    """One response, with the accounting an evaluation needs to defend its numbers.

    `observed_model` is the field that earns its place. See the class docstring on
    `OpenRouterClient.complete`.

    `provider` is OpenRouter's own name for the backend that served the call
    (`"DeepInfra"`, `"CoreWeave"`, ...), read off the top-level `provider` field of the
    response body. It is the field `observed_model` was assumed to cover and does not:
    a 60-call run reported `60 x qwen/qwen3.6-27b` while being served by two different
    builds, because the model id was never the thing that changed. None when the
    backend does not expose it, in which case `prompt_tokens` is the fallback identity
    signal -- see `OpenRouterClient.complete`.

    `raw` is the full response as a dict. **It lives only as long as this object.**
    An earlier version of this docstring claimed anything not surfaced as a named
    field was "still recoverable from the run log", and that was false:
    `runner._result_from_completion` reads named fields off this dataclass and never
    touches `raw`, so nothing in `raw` ever reached disk. The cost was measured -- all
    ten `schema_violation` rows of the 2026-08-04 candidate run were undiagnosable,
    because the model's actual text was discarded at exactly the boundary where the
    harness decided it was wrong. What a run log needs is now named:
    `content`/`finish_reason`/`generation_id`/`provider` are persisted by
    `runner.ItemResult`. `raw` remains for an in-process caller that wants the rest
    (a `message.reasoning` trace, `tool_calls`), and claims nothing about persistence.
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
    raw: dict


def _as_dict(response: Any) -> dict:
    """Best-effort dict of an SDK response object, for the run log."""
    dump = getattr(response, "model_dump", None)
    if callable(dump):
        try:
            return dump()
        except Exception:  # noqa: BLE001 - the log must never break the run
            pass
    return {}


class OpenRouterClient:
    """A thin, single-shot OpenRouter client.

    Thin on purpose. It does not classify, validate, repair or interpret; that is
    `outcomes.classify`, and a client that quietly decided what counted as a good
    response would put a second `parse_ok` in the codebase.
    """

    def __init__(self, api_key: str, *, timeout: float = 120.0) -> None:
        """Build the client.

        **`max_retries=0` is a deliberate departure from the SDK default of 2**, and
        the most consequential line in this file. The SDK's automatic retry is a
        transport-level convenience, but for an evaluation it silently re-rolls the
        sample: a retried 429 returns a *different* generation, whose latency and cost
        are attributed to a request that already happened. One item becomes two draws
        and the harness keeps the luckier one. That is the same defect as re-prompting
        a parse failure, arriving through the plumbing instead of the prompt.

        The cost is real and accepted: a transient 429 becomes a `TransportError` and
        lowers coverage. That is the honest presentation, and it is visible enough to
        act on -- re-run the batch, do not launder the retry inside one item.
        """
        if not api_key or not api_key.strip():
            raise ValueError(
                "OpenRouter API key is empty. Pass a real key; a blank one produces a "
                "401 on every item and an entire run of transport_error."
            )
        self._client = OpenAI(
            base_url=OPENROUTER_BASE_URL,
            api_key=api_key.strip(),
            timeout=timeout,
            max_retries=0,
            default_headers=_DEFAULT_HEADERS,
        )

    def complete(
        self,
        *,
        model: str,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int,
        temperature: float,
        top_p: float | None = None,
        seed: int | None = None,
        tools: Sequence[Mapping[str, Any]] | None = None,
        tool_choice: Any | None = None,
        response_format: Mapping[str, Any] | None = None,
        provider: str | None = None,
        reasoning_effort: str = "provider-default",
    ) -> Completion:
        """Make exactly one call and report what came back.

        **`provider` pins the backend; `Completion.provider` reports the one that
        answered.** They are two different facts and the pair is the point, exactly as
        `model` and `observed_model` are. `request.build_request` turns the argument
        into `{"order": [name], "allow_fallbacks": false, "require_parameters": true}`
        and documents why all three keys are needed.

        **`Completion.provider` is read off the response and is NOT proof the pin
        held.** OpenRouter returns a top-level `provider` string on chat completions,
        and the SDK's response model keeps unknown fields, so this is a plain
        `getattr`. But a field that names the backend is the router reporting its own
        routing, and a harness that accepted it as verification would be trusting the
        thing it is checking. The independent signal is `prompt_tokens`: it comes from
        the backend's own tokenizer, so two backends serving one byte-identical request
        return two different counts and cannot agree by echoing. That is what
        `runner.RunResult.prompt_token_spread` measures, and it is the check to run
        after a pinned run. Where a backend does not expose `provider` at all, the
        token fingerprint is not a weaker fallback -- it is the stronger of the two.

        **`observed_model` is `response.model`, not the constant that was requested.**
        OpenRouter routes across providers, and the model that answers is not
        guaranteed to be the model that was asked for. An arm half-served by a
        different build is not one arm: its accuracy is a blend of two systems
        reported as one, and no amount of downstream statistics recovers the split.
        The requested id is already known to the caller; only the observed one is new
        information, so only the observed one is stored. Recording it per item makes a
        mid-run reroute a fact the run log can show rather than an anomaly nobody can
        explain. `scripts/openrouter-smoketest/README.md` reaches the same conclusion:
        *"a provider can silently route to a different checkpoint"*.

        **It was necessary and it was not sufficient. MEASURED 2026-08-04.** The split
        that actually happened was two backends serving the SAME model id, so
        `observed_models()` printed one key over 60 calls and the guard never fired.
        `provider` is the field that would have. Both are recorded now; neither
        replaces `prompt_tokens` as the thing that cannot be faked.

        The body itself is built by `request.build_request`, not here. That function
        is also what `--dry-run` writes, so the request a reviewer reads before
        spending anything is the same object this method sends -- see its module
        docstring for the three drifts a second construction would hide.

        Optional parameters are omitted from the request when None rather than sent as
        nulls, because providers differ in how they treat an explicit null and a
        harness should not make an arm's request shape depend on which fields it
        happened to leave unset.

        Known limitation, stated rather than discovered later: when `tools` is used,
        the payload arrives in `message.tool_calls` and `content` is None. This
        dataclass reports that faithfully -- `content=None` -- and `classify` will
        call it `empty_other`. The tool-call path is not wired into the outcome
        vocabulary yet; until it is, a tool-calling run must read `raw`. Surfacing
        tool arguments as if they were `content` would be a guess about which call and
        which argument mattered.
        """
        request = build_request(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            top_p=top_p,
            seed=seed,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            provider=provider,
            reasoning_effort=reasoning_effort,
        )

        start = time.monotonic()
        try:
            response = self._client.chat.completions.create(**request)
        except Exception as exc:  # noqa: BLE001 - any SDK/HTTP failure is one outcome
            latency_s = time.monotonic() - start
            raise TransportError(
                f"{type(exc).__name__}: {exc}", latency_s=latency_s
            ) from exc
        latency_s = time.monotonic() - start

        # An empty `choices` list is reported as absent content rather than guessed
        # at. `classify` files it as `empty_other`, which is true and visible.
        choices = getattr(response, "choices", None) or []
        if choices:
            message = getattr(choices[0], "message", None)
            content = getattr(message, "content", None) if message else None
            finish_reason = getattr(choices[0], "finish_reason", None)
        else:
            content = None
            finish_reason = None

        # getattr throughout: `cost` and `reasoning_tokens` are provider-dependent
        # extensions that some backends omit entirely. A missing field is None, not a
        # zero -- "not reported" and "measured as zero" must not be the same number in
        # a cost table.
        usage = getattr(response, "usage", None)
        details = getattr(usage, "completion_tokens_details", None) if usage else None

        return Completion(
            content=content,
            finish_reason=finish_reason,
            observed_model=getattr(response, "model", None),
            generation_id=getattr(response, "id", None),
            # A plain getattr for the same reason `cost` is one: `provider` is an
            # OpenRouter extension, not an OpenAI field, and a backend that omits it
            # must produce None rather than an exception or an invented name.
            provider=getattr(response, "provider", None),
            prompt_tokens=getattr(usage, "prompt_tokens", None) if usage else None,
            completion_tokens=getattr(usage, "completion_tokens", None) if usage else None,
            reasoning_tokens=getattr(details, "reasoning_tokens", None) if details else None,
            cost=getattr(usage, "cost", None) if usage else None,
            latency_s=latency_s,
            raw=_as_dict(response),
        )
