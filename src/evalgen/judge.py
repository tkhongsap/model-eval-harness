"""An independent model's opinion on scorer disagreements -- diagnostic, never a verdict.

What this measures
-------------------
Every experiment in this repository trusts one thing without an automated check: that
`retention_v*.gt.csv` is right. RET-11's ground-truth defect (`EXPERIMENTS.md`,
Experiment 1) was found by a human reading the transcript against the production rule by
hand. This module automates that same question -- **is the disputed label actually
correct, or is the "wrong" answer defensible, or is the ground truth itself the
mistake?** -- for every item where the harness's own scorer says an arm disagrees with
ground truth, using a model that is not one of the arms being evaluated.

It answers a different question than `evidence.py` and `fabrication.py`. Those ask
"is this text really in the transcript" -- purely mechanical, no judgment involved. This
module asks "is this label defensible", which is a judgment call, made by a third model,
recorded as one opinion and never elevated past that.

Nothing here is scored, ranked, or joins a verdict
---------------------------------------------------
Same constraint `evidence.py` states and the same reason: a diagnostic that quietly
became a fourth dimension would let a judge's opinion overrule the pre-registered
ground truth by the back door. Enforced three ways, not just claimed in prose:

  * `test_evalharness_never_imports_evalgen` (`tests/test_boundary.py`) already makes it
    impossible for `src/evalharness/` -- where every scored dimension is computed -- to
    import this module at all, transitively, because this module lives in `evalgen`.
  * `tests/test_judge.py::test_judge_is_never_imported_by_the_verdict_path` parses the
    AST of `report.py` and checks it directly, rather than trusting this paragraph, which
    is a stronger guarantee than `evidence.py` carries today (that module's isolation is
    asserted in its own docstring and has never been checked by a test).
  * `run_judge` returns a `summary` dict with no `net`, no `verdict`, and no field named
    anything `compare.PairedVerdict` or `report.MechanismRow` would recognise. The
    closest thing to a headline number here is `ground_truth_error_rate`, and it is named
    for exactly what it is: how often a third model thinks the pack's own ground truth is
    wrong, not who won.

The verdict space, and why it is four-way rather than a yes/no
----------------------------------------------------------------
| Verdict | Means |
|---|---|
| `ground_truth_correct` | The disputed answer is simply wrong; ground truth holds. |
| `defensible_disagreement` | The disputed answer is ALSO reasonable given the transcript -- a genuine ambiguity, not a fabrication. |
| `ground_truth_error` | The judge believes the ground truth itself is the mistake. The strong claim: this is what would have caught RET-11 automatically. |
| `unclear` | Judge cannot decide, OR (see below) its response could not be classified at all. |

A response that fails to parse, or names a verdict outside this table, is **not** a
crash: it lands in `unclear` with `parse_error=True`, matching `outcomes.classify`'s
rule that a failure is scored, never dropped. The exact aggregation arithmetic this
implements is fixed in `tests/fixtures/judge/HAND-COMPUTED.md`, written before this file,
and `tests/test_judge.py` checks it byte-for-byte against ten constructed raw responses
with no network call.

Blinding, and why it is fixed per item rather than per run
------------------------------------------------------------
The judge is never told which vendor produced which answer -- "Answer A" / "Answer B"
only. Which arm sits in which slot is decided by hashing the item's own merge key
(`_blind_a_is_incumbent`), not by a run-level coin flip, so re-running the same
disagreement set blinds identically every time and a diff between two judge runs is
comparable item for item. It also means a judge with a positional bias ("always prefers
Answer A") cannot be mistaken for a judge with a vendor bias, because which vendor holds
slot A varies item by item rather than being fixed for the whole run.

The judge model itself
-----------------------
Must not be either arm under evaluation -- a model judging its own family's output, or
its rival's, is not independent. Chosen for Experiment 6: `google/gemma-4-31b-it`,
reasoning disabled (`reasoning_effort="none"`), pinned to a single provider exactly like
every other arm in this repository, for the same reason: `EXPERIMENTS.md`'s Experiment 4
measured a 25-point swing on `reason` net from an endpoint change alone, and a probe
before writing this file found the judge itself is not immune -- the identical
temperature-0 request returned `ground_truth_correct` from CoreWeave and Novita and
`defensible_disagreement` from DeepInfra. One provider, recorded, or the judge's own
verdicts are not reproducible.

What this cannot do
--------------------
It cannot resolve a disagreement -- only flag one for a human, exactly as RET-11 was
originally found by a human and not by code. A judge that always said
`ground_truth_correct` would pass every test in this file trivially while being useless;
`tests/fixtures/judge/HAND-COMPUTED.md` says so explicitly, because the aggregation being
correct is not the same claim as the judge being any good. Whether this judge's opinions
carry real signal is an empirical question, answered in Experiment 6 against real
disagreement items, not asserted here.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Sequence

from evalharness.records import Record

__all__ = [
    "JUDGE_VERDICTS",
    "JudgeError",
    "JudgeVerdict",
    "DisagreementItem",
    "find_disagreements",
    "build_judge_prompt",
    "judge_response_schema",
    "parse_judge_response",
    "summarize_judgments",
    "run_judge",
]

JUDGE_VERDICTS: tuple[str, ...] = (
    "ground_truth_correct",
    "defensible_disagreement",
    "ground_truth_error",
    "unclear",
)


class JudgeError(ValueError):
    """Raised on inputs the judge pipeline cannot make sense of.

    Never raised for a bad or unparseable MODEL response -- that is scored as
    `unclear, parse_error=True`, not an error. This is reserved for inputs the caller
    controls: an unknown dimension name, or a disagreement item whose merge key matches
    no item in the testset passed in.
    """


@dataclass(frozen=True)
class DisagreementItem:
    """One item where the ground truth and the two arms do not all three agree.

    `key` is the raw, UNHASHED `(call_id, phone, product)` merge key -- fine to hold in
    memory and to use for the deterministic blind-order hash, but never written to a
    committed file in this form. `run_judge` looks the item back up by this key and
    reports only `item_id` (already a public, documented synthetic identifier throughout
    this repository) in anything it returns.
    """

    key: tuple[str, str | None, str | None]
    dimension: str
    gt_label: str
    incumbent_label: str
    candidate_label: str
    population: str  # "both_wrong" | "incumbent_only_right" | "candidate_only_right"


@dataclass(frozen=True)
class JudgeVerdict:
    """One parsed judge response. `raw` is kept so a re-run can be replayed without a call."""

    verdict: str
    cited_span: str
    rationale: str
    parse_error: bool
    raw: str


def _is_correct(gt: Record, pred: Record | None, dimension: str) -> bool | None:
    """Item-level correctness. Deliberately mirrors `evalharness.compare._correct`.

    Not imported from there -- `compare.py` is a `CONTRIBUTING.md`-flagged final layer,
    and this module must not add a second reason to touch it. Mirroring risks the two
    definitions drifting apart silently, so `tests/test_judge.py` cross-checks this
    function's own item-level counts against `compare.disagreement()`'s aggregate totals
    on the same data and fails loudly the moment they part.
    """
    if gt is None:
        return None
    if dimension == "call_result":
        if gt.call_result is None:
            return None
        return pred is not None and pred.call_result == gt.call_result
    if dimension == "reason":
        return pred is not None and pred.reasons == gt.reasons
    if dimension == "product":
        return pred is not None and pred.product == gt.product
    raise JudgeError(f"unknown dimension {dimension!r}")


def _label(rec: Record | None, dimension: str) -> str:
    """Mirrors `evalharness.compare._label`, for the same reason as `_is_correct` above."""
    if rec is None:
        return "<no prediction>"
    if dimension == "call_result":
        return rec.call_result or "<empty>"
    if dimension == "reason":
        return ", ".join(sorted(rec.reasons)) or "<empty>"
    return rec.product or "<empty>"


def find_disagreements(
    gt: Sequence[Record],
    incumbent: Sequence[Record],
    candidate: Sequence[Record],
    dimension: str,
) -> list[DisagreementItem]:
    """Every item in `both_wrong`, `incumbent_only_right` or `candidate_only_right`.

    Deliberately excludes `both_right`: nothing to adjudicate when both arms already
    agree with the ground truth. This is exactly the population
    `evalharness.compare.disagreement()` counts under those three names --
    `tests/test_judge.py` asserts `len(find_disagreements(...))` equals
    `both_wrong + incumbent_only_right + candidate_only_right` from that function's own
    output on the same inputs, so this can never silently define a different population.
    """
    inc_by_key = {r.key: r for r in incumbent}
    cand_by_key = {r.key: r for r in candidate}
    items: list[DisagreementItem] = []
    for g in gt:
        i = inc_by_key.get(g.key)
        c = cand_by_key.get(g.key)
        i_ok = _is_correct(g, i, dimension)
        c_ok = _is_correct(g, c, dimension)
        if i_ok is None or c_ok is None:
            continue
        if i_ok and c_ok:
            continue
        if not i_ok and not c_ok:
            population = "both_wrong"
        elif i_ok:
            population = "incumbent_only_right"
        else:
            population = "candidate_only_right"
        items.append(
            DisagreementItem(
                key=g.key,
                dimension=dimension,
                gt_label=_label(g, dimension),
                incumbent_label=_label(i, dimension),
                candidate_label=_label(c, dimension),
                population=population,
            )
        )
    return items


def _blind_a_is_incumbent(key: tuple[str, str | None, str | None]) -> bool:
    """Deterministic per item: which arm the judge sees as "Answer A". See module docstring."""
    digest = hashlib.sha256(repr(key).encode("utf-8")).digest()
    return digest[0] % 2 == 0


def _rule_citations_for(rules: dict[str, str], dimension: str, labels: set[str]) -> list[str]:
    """`rule_<dimension>:<label>` lookups for every label in play, skipping misses.

    A miss is not an error: `<empty>` and `<no prediction>` are placeholder labels with
    no rule to cite, and a genuinely wrong label a model invented may not correspond to
    any class the pack's rule table names at all -- that absence is itself information
    the judge is allowed to see, not a reason to fail the lookup.
    """
    citations = []
    for label in sorted(labels):
        key = f"rule_{dimension}:{label.lower()}"
        if key in rules:
            citations.append(f"{label}: {rules[key]}")
    return citations


def build_judge_prompt(
    transcript: str, item: DisagreementItem, rule_citations: Sequence[str]
) -> list[dict[str, str]]:
    """The messages sent to the judge. Neither arm is named; see module docstring."""
    a_is_incumbent = _blind_a_is_incumbent(item.key)
    answer_a = item.incumbent_label if a_is_incumbent else item.candidate_label
    answer_b = item.candidate_label if a_is_incumbent else item.incumbent_label
    citation_block = "\n".join(f"- {c}" for c in rule_citations) or "(none on file)"
    system = (
        "You are an independent adjudicator reviewing a disputed label on a call-"
        "transcript labelling task. You did not produce either answer being compared "
        "and have no stake in which one is right. Given a transcript, the production "
        "rule(s) that govern this dimension, the pack's ground-truth label, and two "
        "models' actual answers (blinded as Answer A and Answer B, in no particular "
        "order), decide exactly one of:\n"
        "ground_truth_correct -- the compared answer(s) are simply wrong, ground truth "
        "holds.\n"
        "defensible_disagreement -- at least one compared answer is also reasonable "
        "given the transcript, alongside the ground truth.\n"
        "ground_truth_error -- a compared answer is clearly the better reading and the "
        "ground truth itself looks wrong.\n"
        "unclear -- the transcript does not give you enough to decide.\n"
        "Quote the exact transcript span your rationale rests on, verbatim, in "
        "cited_span. Respond with the JSON schema only, no other text."
    )
    user = (
        f"Dimension: {item.dimension}\n\n"
        f"Transcript:\n{transcript}\n\n"
        f"Production rule(s):\n{citation_block}\n\n"
        f"Ground truth: {item.gt_label}\n"
        f"Answer A: {answer_a}\n"
        f"Answer B: {answer_b}\n\n"
        "Adjudicate."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def judge_response_schema() -> dict[str, Any]:
    """The `response_format` sent with every judge call. `strict: True` throughout."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "judge_verdict",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "verdict": {"type": "string", "enum": list(JUDGE_VERDICTS)},
                    "cited_span": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["verdict", "cited_span", "rationale"],
                "additionalProperties": False,
            },
        },
    }


def parse_judge_response(raw_text: str) -> JudgeVerdict:
    """Never raises. See `tests/fixtures/judge/HAND-COMPUTED.md` for the exact table
    this reproduces, checked by `tests/test_judge.py` with no network call.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return JudgeVerdict(
            verdict="unclear", cited_span="", rationale="", parse_error=True, raw=raw_text
        )
    if not isinstance(payload, dict):
        return JudgeVerdict(
            verdict="unclear", cited_span="", rationale="", parse_error=True, raw=raw_text
        )
    verdict = payload.get("verdict")
    if verdict not in JUDGE_VERDICTS:
        return JudgeVerdict(
            verdict="unclear",
            cited_span=str(payload.get("cited_span") or ""),
            rationale=str(payload.get("rationale") or ""),
            parse_error=True,
            raw=raw_text,
        )
    return JudgeVerdict(
        verdict=verdict,
        cited_span=str(payload.get("cited_span") or ""),
        rationale=str(payload.get("rationale") or ""),
        parse_error=False,
        raw=raw_text,
    )


def summarize_judgments(verdicts: Sequence[JudgeVerdict]) -> dict[str, Any]:
    """Pure arithmetic. See `tests/fixtures/judge/HAND-COMPUTED.md` for the fixed
    expectation this reproduces exactly, n=10, before this function existed.

    `0.0` rather than `nan` on an empty input, matching `evidence._with_rates`: a run
    that adjudicated nothing has no rate, and `nan` prints as a defect wherever it lands.
    """
    total = len(verdicts)
    counts = {v: 0 for v in JUDGE_VERDICTS}
    parse_errors = 0
    for verdict in verdicts:
        counts[verdict.verdict] += 1
        if verdict.parse_error:
            parse_errors += 1
    rates = {f"{k}_rate": (counts[k] / total if total else 0.0) for k in JUDGE_VERDICTS}
    return {
        "total": total,
        "counts": counts,
        **rates,
        "parse_error_count": parse_errors,
        "parse_error_rate": (parse_errors / total if total else 0.0),
    }


def run_judge(
    *,
    testset: Any,
    gt: Sequence[Record],
    incumbent: Sequence[Record],
    candidate: Sequence[Record],
    dimensions: Sequence[str],
    client: Any,
    model: str,
    provider: str,
    max_items: int | None = None,
) -> dict[str, Any]:
    """Call the judge once per disagreement item across `dimensions`, return a report.

    One call per item, one replicate -- a diagnostic opinion, not a scored arm, so this
    does not carry the 3-replicate discipline the primary arms do. That is a real
    limitation and is stated as one in Experiment 6, not hidden: a single judge call's
    instability is unmeasured here, the same caveat `runner.RunResult.N_flip` exists to
    make visible for the arms actually being scored.

    `max_items` truncates deterministically (first N in testset order) rather than
    sampling, and the caller is responsible for logging what was dropped -- silent
    truncation reads as "covered everything" when it did not.
    """
    by_key = {(str(i.call_id), str(i.phone_number)): i for i in testset.items}
    all_items: list[DisagreementItem] = []
    for dimension in dimensions:
        all_items.extend(find_disagreements(gt, incumbent, candidate, dimension))
    candidates_found = len(all_items)
    truncated = max_items is not None and candidates_found > max_items
    if max_items is not None:
        all_items = all_items[:max_items]

    records: list[dict[str, Any]] = []
    verdicts: list[JudgeVerdict] = []
    for disagreement_item in all_items:
        lookup_key = (str(disagreement_item.key[0]), str(disagreement_item.key[1]))
        item = by_key.get(lookup_key)
        if item is None:
            raise JudgeError(
                f"disagreement key {disagreement_item.key} matches no item in the "
                "testset passed in. Pass the same testset the runs being compared used."
            )
        labels: set[str] = set()
        for label in (
            disagreement_item.gt_label,
            disagreement_item.incumbent_label,
            disagreement_item.candidate_label,
        ):
            if label in ("<empty>", "<no prediction>"):
                continue
            if disagreement_item.dimension == "reason":
                labels.update(part.strip() for part in label.split(","))
            else:
                labels.add(label)
        citations = _rule_citations_for(item.rules, disagreement_item.dimension, labels)
        messages = build_judge_prompt(item.transcript_th, disagreement_item, citations)
        completion = client.complete(
            model=model,
            messages=messages,
            max_tokens=1000,
            temperature=0.0,
            top_p=1.0,
            seed=0,
            response_format=judge_response_schema(),
            provider=provider,
            reasoning_effort="none",
        )
        verdict = parse_judge_response(completion.content or "")
        verdicts.append(verdict)
        records.append(
            {
                "item_id": item.item_id,
                "dimension": disagreement_item.dimension,
                "population": disagreement_item.population,
                "gt_label": disagreement_item.gt_label,
                "incumbent_label": disagreement_item.incumbent_label,
                "candidate_label": disagreement_item.candidate_label,
                "verdict": verdict.verdict,
                "cited_span": verdict.cited_span,
                "rationale": verdict.rationale,
                "parse_error": verdict.parse_error,
                "observed_model": completion.observed_model,
                "observed_provider": completion.provider,
                "reasoning_tokens": completion.reasoning_tokens,
                "cost": completion.cost,
                "latency_s": completion.latency_s,
            }
        )

    return {
        "model_requested": model,
        "provider_requested": provider,
        "dimensions": list(dimensions),
        "candidates_found": candidates_found,
        "items": records,
        "summary": summarize_judgments(verdicts),
        "truncated": truncated,
    }
