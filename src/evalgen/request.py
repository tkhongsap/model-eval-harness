"""The OpenRouter request body, built in exactly one place.

`--dry-run` exists so a 9.6 KB Thai prompt can be read by a human before any tokens
are bought. That is only worth doing if the body the dry run prints is the body a real
run sends, and the cheapest way to get that wrong is to build the request twice: once
in `client.complete` and once in whatever writes the review file. The two copies agree
on the day they are written and disagree the first time one of them changes.

The specific drifts that would survive review unnoticed, because each produces a body
that still looks right:

  * `extra_body={"usage": {"include": True}}` omitted. OpenRouter then reports no
    `usage.cost`, `Completion.cost` is None on every row, and `RunResult.total_cost()`
    sums to 0.0 -- a comparison that prices the candidate at nothing.
  * `top_p` or `seed` sent as an explicit null instead of being omitted. Providers
    differ on how they read a null, so the arm's decoding would depend on which fields
    the caller happened to leave unset (`client.complete` documents this).
  * `response_format` present in one and absent in the other. `runner._required_keys`
    reads the required keys out of the request, so the review would show a schema the
    run does not enforce.
  * `provider: {"require_parameters": true}` sent with the schema in one and not the
    other. OpenRouter DROPS a parameter an endpoint does not support rather than
    failing, so without it an arm rerouted mid-run to an endpoint with no
    structured-output support falls back to prompt-coaxing, and the run log looks
    identical. `client.py` records `observed_model` for exactly this class of silent
    reroute; this is the same guard for the other half of the request.
  * `provider.order` / `allow_fallbacks` present in one and absent from the other. That
    is the difference between an arm served by one backend and an arm served by
    whichever backend the router preferred that second -- see `provider` below.

**Why pinning exists at all. MEASURED on 2026-08-04.** A 60-call run of
`qwen/qwen3.6-27b` was served by TWO backends under one model id. One answered with
`reasoning_tokens=0`, 2538-2643 prompt tokens, a 5.8s median and 10 of 31 rows in
`schema_violation`; the other with `reasoning_tokens>0`, 3583-3931 prompt tokens, a
71.7s median and 0 of 29 violations. Same bytes in, ~1.4x the token count back, which
is a different tokenizer and therefore a different build. `observed_models` reported
`60 x qwen/qwen3.6-27b` and saw nothing, because `response.model` is the model id and
the model id was never in question. 14 of 20 items returned two distinct
`prompt_tokens` values for a byte-identical request; the incumbent arm returned 0 of
20. `require_parameters` alone does not fix this: it narrows the eligible set to
endpoints that can honour the schema, and that set had more than one member.

So `client.complete` sends what this function returns, and the CLI's dry run writes
what this function returns. There is no second construction to drift, and
`tests/test_cli.py::test_the_dry_run_body_is_the_body_the_client_would_send` pins the
two together by driving the real client against a stubbed SDK.

**No `openai` import, deliberately.** `client.py` is the only module in this package
allowed to reach for the SDK (see its docstring), and a dry run must stay runnable
where no client is installed -- which is the environment CI builds, and therefore the
environment the test above runs in.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

__all__ = ["build_request"]


def build_request(
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
) -> dict[str, Any]:
    """The kwargs for one `chat.completions.create` call, as a plain dict.

    Optional parameters are omitted when None rather than sent as nulls; see the
    module docstring for why that distinction is load-bearing rather than tidy.

    `provider` is an OpenRouter provider name (`"DeepInfra"`, `"CoreWeave"`, ... --
    the `provider_name` field of `GET /models/{id}/endpoints`). Passing one sends
    `{"order": [name], "allow_fallbacks": false, "require_parameters": true}`, which
    is three separate refusals and all three are needed:

      * `order` names the backend to prefer.
      * **`allow_fallbacks: false` is the one that does the work.** `order` alone is a
        preference; OpenRouter falls through to any other eligible endpoint when the
        named one is busy, rate-limited or down, and a run that fell through is
        indistinguishable in the log from one that did not.
      * `require_parameters` stays on, so a named endpoint that cannot honour the
        schema is refused rather than silently served without it. With fallbacks off
        that becomes a hard failure, which is the honest outcome: the alternative is
        prompt-coaxed output scored as though it were constrained decoding.

    **A pinned run is not proved by the `provider` field coming back with the
    requested name in it.** That is the router echoing the request. The proof is
    `prompt_tokens` uniformity across replicates of one byte-identical item, because
    the token count is produced by the backend's own tokenizer and cannot be echoed --
    see `runner.RunResult.prompt_token_spread`.

    Everything is copied out of its argument (`list(messages)`, `dict(...)`) so a
    caller that mutates what it passed in cannot change a request already built, and
    so the returned dict is safe to serialise into a dry-run file that claims to
    describe the call that was made.
    """
    request: dict[str, Any] = {
        "model": model,
        "messages": [dict(message) for message in messages],
        "max_tokens": max_tokens,
        "temperature": temperature,
        # OpenRouter returns usage.cost only when usage accounting is asked for. Cost
        # is a first-class result here: the smoke test measured the incumbent at ~47x
        # cheaper than the candidate on a one-word prompt, entirely from reasoning
        # overhead, so a comparison that cannot price an arm is missing half the
        # migration question.
        "extra_body": {"usage": {"include": True}},
    }
    if top_p is not None:
        request["top_p"] = top_p
    if seed is not None:
        request["seed"] = seed
    if tools is not None:
        request["tools"] = [dict(tool) for tool in tools]
    if tool_choice is not None:
        request["tool_choice"] = tool_choice
    if response_format is not None:
        request["response_format"] = dict(response_format)
        # Only alongside a schema, because that is the only parameter whose silent
        # removal changes what is being measured rather than how it decodes. Asking
        # for it unconditionally would narrow routing on calls that do not need it,
        # and a narrower route is a different arm.
        request["extra_body"]["provider"] = {"require_parameters": True}
    if provider is not None:
        # Written last and unconditionally, so a pin holds whether or not a schema was
        # sent, and so it cannot be half-applied by the branch above having run or not
        # run. `require_parameters` is repeated here rather than merged into whatever
        # the branch above left behind: a pinned request must carry all three keys on
        # its own, because a reader of a dry-run body should not have to reason about
        # which branch built which key.
        request["extra_body"]["provider"] = {
            "order": [provider],
            "allow_fallbacks": False,
            "require_parameters": True,
        }
    return request
