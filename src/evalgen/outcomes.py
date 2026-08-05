"""The single place that decides whether a model response counts as parsed.

Every arm in a comparison must be judged by the same rule, applied in the same code
path, or the comparison is not paired. `parse_ok` is therefore a property of one
dataclass built by one function here, never a boolean recomputed at a call site.

Why that matters more than it looks. CHANGELOG.md ("Coverage accounting on every
dimension") records the failure this prevents: *"An arm whose unparseable output was
dropped has an easier denominator, so accuracy would favour the arm that failed more
often."* A second, slightly different `parse_ok` written inline in a runner is exactly
how two arms end up with two denominators, and the resulting number looks fine.

Three standing rules, each with its evidence in the docstrings below:

1. **Empty output from a reasoning model is a measured failure mode, not a rarity.**
   It gets its own outcome so it is never confused with a model that answered badly.
2. **Repairs are loss-free only, and every one is recorded.** A run where most items
   needed repair is visibly different from a run where none did.
3. **Nothing here re-prompts or retries.** Re-rolling until a model emits valid JSON
   manufactures a coverage number it did not earn.

This module imports the standard library only. It must never import `openai`, so its
tests run wherever the scoring library runs. `tests/test_boundary.py` gates the
neighbouring claim for `src/evalharness/`; `tests/test_outcomes.py` gates this one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Final, Literal

Outcome = Literal[
    "ok",
    "empty_length",
    "empty_other",
    "refusal",
    "not_json",
    "schema_violation",
    "transport_error",
    "provider_error",
]

# Repair names. Constants rather than bare strings so a run log and a test cannot
# drift apart over a typo, and so the full vocabulary is greppable from one place.
REPAIR_STRIP_BOM: Final = "strip_bom"
REPAIR_STRIP_FENCE: Final = "strip_markdown_fence"
REPAIR_FIRST_OBJECT: Final = "first_balanced_object"

# How far into a response a refusal marker is allowed to appear. See
# `_looks_like_refusal`: the window is what stops the Thai markers matching a
# transcript quoted back inside a long, broken answer.
_REFUSAL_WINDOW: Final = 200

# Markers that identify a refusal, checked only against text that already failed to
# parse as JSON (see `classify`). Thai entries are here because the corpus is Thai
# call content: a model that declines in Thai is declining just as much as one that
# declines in English, and an English-only list would silently file those as
# `not_json`. Deliberately short. This list refines a failure into a more specific
# failure; it can never turn a failure into a success, so a miss costs nothing that
# matters and a false positive cannot inflate coverage.
_REFUSAL_MARKERS: Final[tuple[str, ...]] = (
    "i'm sorry",
    "i am sorry",
    "i cannot",
    "i can't",
    "i won't",
    "i'm unable",
    "i am unable",
    "unable to assist",
    "unable to provide",
    "cannot assist",
    "cannot comply",
    "as an ai",
    "as a language model",
    "ขออภัย",
    "ขอโทษ",
    "ไม่สามารถ",
)

_BOM: Final = "﻿"


@dataclass(frozen=True)
class Classified:
    """One model response, judged once.

    `payload` is populated whenever a JSON **object** was recovered, including on
    `schema_violation`, so a run log can show what the model actually said instead of
    only that it was wrong. Read it for scoring only when `parse_ok` is True; that is
    the whole point of there being a single flag.

    `repairs` lists the transformations that were applied, in the order applied, and
    is empty when the raw content parsed as it arrived.

    `truncated` mirrors `finish_reason == "length"` and is independent of the outcome:
    output can be truncated *after* a complete JSON object, which is still usable, and
    the flag records the risk without discarding the item.
    """

    outcome: Outcome
    payload: dict | None
    repairs: tuple[str, ...]
    truncated: bool

    @property
    def parse_ok(self) -> bool:
        """The only definition of "this response counted".

        Anything other than `ok` is a failure, and every failure is scored rather
        than dropped, per the deviation listed in CLAUDE.md ("Parse failures are
        scored rather than dropped"). Dropping them is what makes an arm's
        denominator easier than its opponent's.
        """
        return self.outcome == "ok"


def transport_error() -> Classified:
    """The response that never arrived.

    Built by the caller when the HTTP call raised, so that a request failure and a
    bad response are recorded in the same vocabulary and land in the same coverage
    accounting. It lives here rather than in `client.py` because this module is the
    only place an `Outcome` is chosen.

    `client.OpenRouterClient` raises `client.TransportError` rather than returning
    one of these, so that a caller can decide between recording the failure and
    aborting the run; only the caller knows which. Note what is *not* offered: a
    retry. See `classify`.
    """
    return Classified(outcome="transport_error", payload=None, repairs=(), truncated=False)


def _strip_bom(text: str) -> str:
    """Remove a leading byte-order mark. Loss-free: a BOM carries no content."""
    return text[len(_BOM):] if text.startswith(_BOM) else text


def _strip_markdown_fence(text: str) -> str | None:
    """Unwrap ```json ... ``` fencing. Returns None when there is no opening fence.

    Loss-free: the fence is presentation the model added around its answer, and the
    language tag ("json") is not data. Only a fence at the very start is unwrapped;
    JSON that follows prose is left to `_first_balanced_object`, which is the honest
    tool for that case.

    An unterminated fence still yields its body, because a response cut off mid-fence
    has usually already emitted the object, and `json.loads` remains the arbiter of
    whether what came out is actually parseable.
    """
    stripped = text.strip()
    if not stripped.startswith("```"):
        return None
    newline = stripped.find("\n")
    if newline == -1:
        # A lone "```json" with no body. Nothing to recover.
        return None
    body = stripped[newline + 1:]
    close = body.rfind("```")
    if close != -1:
        body = body[:close]
    return body.strip()


def _first_balanced_object(text: str) -> str | None:
    """Return the first balanced top-level `{...}`, or None if none is complete.

    String-aware on purpose: it tracks quoting and backslash escapes, so a brace
    inside a JSON string value cannot shift the depth count. Thai text is no special
    case here, but a transcript field quoting a brace would be.

    Loss-free in the sense that matters: it selects a complete object and discards
    only the prose around it. It never repairs the object's own syntax, so truncated
    output never balances and is correctly reported `not_json` rather than being
    silently completed into something the model did not say.
    """
    start = text.find("{")
    if start == -1:
        return None

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start:index + 1]
    return None


def _looks_like_refusal(text: str) -> bool:
    """Whether unparseable text reads as the model declining the task.

    Applied only after JSON parsing has already failed, so valid JSON containing the
    word "sorry" in a transcript field is never mistaken for a refusal.

    **Narrowed to the opening of the response, because the corpus fights back.** The
    Thai markers here -- `ขออภัย`, `ขอโทษ`, `ไม่สามารถ` -- are ordinary call-centre
    politeness and capability phrases, and this harness sends the model a Thai
    call-centre transcript and asks it to quote from it into `keyword`. A substring
    search over the whole response therefore matches the corpus, not the model.

    MEASURED, on RET-01 with the un-transformed schema: `gemini-2.5-flash` degenerated
    into 23,529 characters of repetition inside `network_issue.sub_reason`, which
    echoed the agent's own `ต้องขอโทษด้วยนะคะ`, and the whole event was reported as the
    model REFUSING the task. It was a token-budget and grammar pathology
    (`decoding.py`, deviation 1) and the headline was wrong.

    Two conditions, and both are about where a decline happens rather than which words
    it uses: a model that declines does so up front, and a model that opened a JSON
    object did not decline. Text that begins with `{` is never a refusal, and the
    markers are searched only in the first `_REFUSAL_WINDOW` characters. The marker
    list is unchanged -- the fix is where they are allowed to match, not which ones.
    """
    stripped = text.strip()
    if stripped.startswith("{"):
        return False
    head = stripped[:_REFUSAL_WINDOW].lower()
    return any(marker in head for marker in _REFUSAL_MARKERS)


def _repair_candidates(content: str) -> list[tuple[str, tuple[str, ...]]]:
    """The raw text, then progressively repaired variants, each with its record.

    Repairs accumulate rather than compete: a BOM in front of a fenced object needs
    both. A step that changes nothing records nothing, so `repairs` counts defects
    that were actually present, which is what makes a repair rate meaningful.
    """
    candidates: list[tuple[str, tuple[str, ...]]] = [(content, ())]

    text = content
    repairs: tuple[str, ...] = ()

    without_bom = _strip_bom(text)
    if without_bom != text:
        text, repairs = without_bom, repairs + (REPAIR_STRIP_BOM,)
        candidates.append((text, repairs))

    unfenced = _strip_markdown_fence(text)
    if unfenced is not None and unfenced != text:
        text, repairs = unfenced, repairs + (REPAIR_STRIP_FENCE,)
        candidates.append((text, repairs))

    extracted = _first_balanced_object(text)
    if extracted is not None and extracted != text.strip():
        text, repairs = extracted, repairs + (REPAIR_FIRST_OBJECT,)
        candidates.append((text, repairs))

    return candidates


def classify(
    content: str | None,
    finish_reason: str | None,
    *,
    required_keys: tuple[str, ...] = (),
) -> Classified:
    """Judge one model response. The only place `parse_ok` is decided.

    **This function never re-prompts and never retries.** There is deliberately no
    parameter that would let it. A harness that re-rolls a model until it emits valid
    JSON reports a coverage number it did not earn, and the arm that needed three
    attempts scores identically to the arm that needed one. If a model cannot produce
    parseable output for an item, that is the measurement.

    Order of decision, and why:

    1. **The provider's own failure first.** `finish_reason == "error"` means the
       generation was abandoned by the router or the upstream, not by the model.
       See `_classify_provider_error`.
    2. **Empty content next.** An empty response is not malformed JSON; calling it
       `not_json` would file a budget problem under a capability problem.
    3. **Parse, with loss-free repairs, before refusal.** Valid JSON is valid JSON
       whatever words it contains, so refusal detection only ever sees text that
       already failed to parse.
    4. **Schema last.** A response can only violate a schema once it is an object.

    `required_keys` is checked for presence, not for type or value. Presence is what
    can be judged without knowing the app; the Retention label vocabulary is
    validated downstream against `evalharness.labelspaces`, which is where the class
    lists transcribed from production live.
    """
    truncated = finish_reason == "length"

    if finish_reason == "error":
        return _classify_provider_error(truncated)

    if content is None or not content.strip():
        return _classify_empty(truncated)

    for text, repairs in _repair_candidates(content):
        try:
            parsed: Any = json.loads(text)
        except ValueError:
            continue
        return _classify_parsed(parsed, repairs, truncated, required_keys)

    # Nothing parsed under any loss-free repair.
    if _looks_like_refusal(content):
        return Classified(outcome="refusal", payload=None, repairs=(), truncated=truncated)
    return Classified(outcome="not_json", payload=None, repairs=(), truncated=truncated)


def _classify_provider_error(truncated: bool) -> Classified:
    """`finish_reason == "error"`: the generation was abandoned, not answered badly.

    MEASURED, not anticipated. On 2026-08-04, `google/gemini-2.5-flash` returned HTTP
    **200** with `choices[0].finish_reason == "error"`, 86 characters of a half-written
    JSON object, `completion_tokens=0`, `cost=$0.00000`, and `choices[0].error` reading
    `{"code": 429, "message": "... temporarily rate-limited upstream",
    "metadata": {"error_type": "rate_limit_exceeded"}}`. A 429 delivered inside a 200.

    Without this branch it lands in `not_json`: the content is non-empty so
    `_classify_empty` never fires, it does not parse, and no refusal marker matches. A
    provider-side rate limit is then charged to the model's Thai JSON competence, and
    the arm that happened to be throttled looks worse at the task. `runner._is_retryable`
    never sees it either, because nothing raised.

    `payload` stays None even though a fragment arrived. Half an object is not an
    answer, and the fragment is still in the run log via `Completion.raw`.

    This is a fact about the route, not about the model, which is why `runner` treats a
    zero-token one as retryable in the way a 5xx is -- and why that retry does not
    violate the rule against re-rolling a sample: no sample was drawn.
    """
    return Classified(
        outcome="provider_error", payload=None, repairs=(), truncated=truncated
    )


def _classify_empty(truncated: bool) -> Classified:
    """Empty content, split by whether the token budget ran out.

    MEASURED, not anticipated. Both current candidates are reasoning models: they
    spend completion tokens thinking before emitting a single visible one. On a
    ONE-WORD prompt ("Reply with exactly one word: OK"), the run recorded in commit
    9e29034 measured `google/gemini-3.6-flash` at 86 completion tokens of which **85
    were reasoning and 1 was visible**, and `qwen/qwen3.7-flash` at 155 of which
    **149 were reasoning and 6 visible**. A later run against the current defaults
    (scripts/openrouter-smoketest/README.md:122-126) measured `qwen/qwen3.6-27b` at
    105 reasoning tokens for 16 visible, and found `gemini-2.5-flash` is not a
    reasoning model at all -- so this failure mode is per-model, not universal, which
    is precisely why it must be classified rather than assumed.

    The consequence is the reason this outcome exists. With `max_tokens=10` the smoke
    test reported PASS on both models while content was empty and `finish_reason` was
    `"length"`: the entire budget went to reasoning and nothing was left to answer
    with (README.md:128-131, and the sizing note at smoketest.py:82-88). A small
    `max_tokens` does not risk empty output from a reasoning model, it guarantees it.
    Filed as `empty_length`, that is a run-configuration bug, visible and fixable.
    Filed as `not_json` or silently dropped, it looks like the model failed the task.

    `empty_other` is empty content that did **not** hit the budget, which is a
    genuinely different fact about the model. It is also observed, not hypothetical:
    `qwen3.6-27b` returned an empty response on run 2 of three identical Thai
    round-trips at `MAX_TOKENS = 2000` (README.md:140-148).

    A provider-side `content_filter` finish also lands in `empty_other`. That is
    deliberate: it has not been observed on this workload, and a branch written
    against a signal nobody here has seen is a guess. The same discipline keeps
    `adapters/retention.py::load_workbook` refusing to invent a header layout.
    """
    if truncated:
        return Classified(outcome="empty_length", payload=None, repairs=(), truncated=True)
    return Classified(outcome="empty_other", payload=None, repairs=(), truncated=False)


def _classify_parsed(
    parsed: Any,
    repairs: tuple[str, ...],
    truncated: bool,
    required_keys: tuple[str, ...],
) -> Classified:
    """Something parsed as JSON. Decide whether it is the object that was asked for.

    A top-level array or scalar is valid JSON and still the wrong shape, so it is a
    `schema_violation` rather than `not_json`: the model could produce JSON and chose
    a different structure, which is a different diagnosis and points at the prompt
    rather than at the decoder. `payload` stays None because there is no object.

    A missing required key keeps its payload, because "which keys did it actually
    emit" is the question a reviewer asks next, and `parse_ok` is already False so it
    cannot leak into a score.
    """
    if not isinstance(parsed, dict):
        return Classified(
            outcome="schema_violation", payload=None, repairs=repairs, truncated=truncated
        )

    missing = [key for key in required_keys if key not in parsed]
    if missing:
        return Classified(
            outcome="schema_violation", payload=parsed, repairs=repairs, truncated=truncated
        )

    return Classified(outcome="ok", payload=parsed, repairs=repairs, truncated=truncated)
