"""How an arm was wrong, not just how often -- diagnostic, never a verdict.

What this measures
-------------------
The scorer is all-or-nothing. An answer of `promotion related` where the truth is
`save cost` -- two classes the production prompt separates with a single CRITICAL line
(`prompt.py:4345`) -- scores exactly the same zero as an answer of `network`, which is a
different universe of wrong. Experiment 3 measured that most of the incumbent's whole-item
failures were *over-labelling*: the right answer plus unsupported extras. That is a
categorically milder defect than substituting a wrong class, and no report in this
repository can currently say so.

This module attaches one **error category** to every unit the scorer already counts as
wrong, so a report can print a per-arm failure profile beside the existing dimension
table. The full specification, worked by hand before this file existed, is
`tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md`.

Deterministic first; the model answers one question and only where it must
-----------------------------------------------------------------------------
Seven of the eight branches are set arithmetic and cost nothing:

    missing_output -> invalid_output -> fabricated_class -> unsupported_claim
      -> no_answer -> over_labelling -> under_labelling -> substitution

Order is specification, not implementation detail. `fabricated_class` sits above the
subset tests as a **hard cap**: an answer carrying every true label plus one class that
does not exist in the label space is a fabrication, never over-labelling, and no model is
consulted because there is nothing to judge. A subset-first implementation returns
`over_labelling` there and the invented class disappears from the report.

Only `substitution` -- both `truth - answer` and `answer - truth` non-empty -- reaches a
model, and it is asked exactly one binary question: are the substituted-in labels the
**neighbours** of the substituted-out ones, separated by a specific clause in the quoted
production rule, or do they come from a different part of the rule set?

What the judge is not trusted with
-----------------------------------
It cannot reclassify a deterministic category, cannot see the other arm, cannot see or
question the reference labelling (that is `judge.py`'s job and its flag queue is untouched
by this module), and cannot change a score. Two byte-exact evidence gates, both required
for a decisive answer: the cited transcript span must occur verbatim in the transcript,
and the cited rule line must occur verbatim **in the rule text this prompt quoted**. A
judge that paraphrases a rule it was handed has not demonstrated the boundary it claims,
and lands in `unclear_family` -- scored, never dropped.

Borrowed from `T481__fast_monte_carlo/tests/llm_judge.py` deliberately: the weighted-rubric
instinct reduced to one hard cap in the prompt, and "award points only for demonstrated
correctness, not claimed correctness". Deliberately NOT borrowed: its silent keyword
fallback when no API key is present (here a missing key or an unreadable rule root
refuses), its score entering the grade, and its single-shot scoring -- this judge was
measured flipping 18.1% of units at temperature 0, so families are judged over replicates
with a strict majority and ties are `unclear_family`, never broken by picking.

Nothing here is scored, ranked, or joins a verdict
---------------------------------------------------
Same constraint `judge.py` and `evidence.py` state, enforced the same way:
`tests/test_severity.py::test_severity_is_never_imported_by_the_verdict_path` parses the
AST of the verdict path rather than trusting this paragraph. The report contains no `net`,
no `verdict`, and no field `compare.PairedVerdict` or `report.MechanismRow` would
recognise.

**No p-values on this output.** Severity units are scored rows, and several rows can come
from one call, so they are not independent. This repository has already been bitten once
by treating replicated rows as independent units (`EXPERIMENTS.md`, Experiment 6
correction). The profile is descriptive: counts, rates, and the unit grain stated. No test
is computed, and none should be added without a power derivation written down first.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from evalharness.compare import CoverageMismatch, comparison_units, label_set
from evalharness.labelspaces import RETENTION, LabelSpace
from evalgen.testsets import label_space_for
from evalharness.records import Record
from evalgen.runtime import RuntimeSpec, build_runtime_request

# One implementation of rule quoting, not two. These are internals of the same package,
# imported rather than re-derived because the 2026-08-09 defect-4 review found that the
# expensive mistake in this area is having two ways to answer "what does the rule say".
from evalgen.judge import (
    EVIDENCE_EXACT,
    EVIDENCE_INVALID_RESPONSE,
    EVIDENCE_MISSING,
    EVIDENCE_NOT_EVALUATED,
    EVIDENCE_NOT_IN_TRANSCRIPT,
    EVIDENCE_NOT_REQUIRED,
    EVIDENCE_UNCHECKED,
    _format_rule_entry,
    _rule_entries_for,
    label_citation_union,
    resolve_citations_dedup,
)

__all__ = [
    "SEVERITY_CATEGORIES",
    "SEVERITY_DIMENSIONS",
    "DETERMINISTIC_CATEGORIES",
    "JUDGED_CATEGORIES",
    "MIS_SCOPING",
    "MIS_CLASSIFICATION",
    "FAMILY_ANSWERS",
    "SeverityError",
    "SeverityUnit",
    "FamilyVerdict",
    "classify_error",
    "severity_units",
    "severity_unit_id",
    "build_severity_prompt",
    "severity_response_schema",
    "parse_severity_response",
    "family_aggregation",
    "severity_profile",
    "shareable_severity_report",
    "run_severity",
]

# Dimension scope, and the reason it is not all three. `product` returned zero informative
# verdicts across all nine comparisons scored in Experiment 5A (every one landed d < 6),
# it carries the reproduced null-phone drop, and its comparison grain is the call rather
# than the row, so it needs its own set arithmetic. Deferred with a reason, not forgotten.
SEVERITY_DIMENSIONS: tuple[str, ...] = ("call_result", "reason")

DETERMINISTIC_CATEGORIES: tuple[str, ...] = (
    "missing_output",
    "invalid_output",
    "fabricated_class",
    "unsupported_claim",
    "no_answer",
    "over_labelling",
    "under_labelling",
    "substitution",
)
JUDGED_CATEGORIES: tuple[str, ...] = ("near_family", "cross_family", "unclear_family")

# `substitution` is a routing state, not a reported category: every substitution unit
# leaves this module as one of the three judged families.
SEVERITY_CATEGORIES: tuple[str, ...] = tuple(
    category for category in DETERMINISTIC_CATEGORIES if category != "substitution"
) + JUDGED_CATEGORIES

# The one ordering claim (see the fixture): mis-classification asserts a class the call
# does not support; mis-scoping mis-counts a class it does. No order is asserted between
# `over_labelling` and `under_labelling`, and nothing here sums them into one number.
MIS_SCOPING: tuple[str, ...] = ("over_labelling", "under_labelling")
MIS_CLASSIFICATION: tuple[str, ...] = (
    "near_family",
    "cross_family",
    "fabricated_class",
)

FAMILY_ANSWERS: tuple[str, ...] = ("near", "cross", "unclear")
_RESPONSE_KEYS = frozenset({"family", "cited_span", "cited_rule_line", "rationale"})

RULE_EVIDENCE_EXACT = "exact"
RULE_EVIDENCE_MISSING = "missing"
RULE_EVIDENCE_NOT_IN_RULES = "not_in_quoted_rules"
RULE_EVIDENCE_NOT_REQUIRED = "not_required"
RULE_EVIDENCE_NOT_EVALUATED = "not_evaluated"


class SeverityError(ValueError):
    """Raised on inputs this module cannot make sense of.

    Never raised for a bad model response -- that is scored as `unclear_family` with the
    validation errors recorded, matching `outcomes.classify`'s rule that a failure is
    scored and not dropped. Reserved for caller mistakes: an unknown dimension, a unit
    whose key matches no item in the testset, or asking to classify an answer the scorer
    already calls correct.
    """


@dataclass(frozen=True)
class SeverityUnit:
    """One arm's wrong answer on one scored row, with its category.

    `key` is the raw merge key and stays in memory only. Everything this module returns
    is keyed by `item_id`, the shareable synthetic identifier, exactly as `judge.py` does.
    """

    key: tuple[str, str | None, str | None]
    item_id: str
    arm: str
    dimension: str
    truth: frozenset[str]
    answer: frozenset[str]
    category: str
    sub_reason: str | None = None

    @property
    def missing(self) -> frozenset[str]:
        """Labels the truth carries and the answer does not -- substituted out."""
        return self.truth - self.answer

    @property
    def extra(self) -> frozenset[str]:
        """Labels the answer carries and the truth does not -- substituted in."""
        return self.answer - self.truth


@dataclass(frozen=True)
class FamilyVerdict:
    """One locally validated near/cross response."""

    family: str
    cited_span: str
    cited_rule_line: str
    rationale: str
    parse_error: bool
    raw: str
    evidence_status: str = EVIDENCE_UNCHECKED
    rule_evidence_status: str = RULE_EVIDENCE_NOT_EVALUATED
    validation_errors: tuple[str, ...] = ()
    execution_status: str = "completed"


def _vocabulary(space: LabelSpace, dimension: str) -> frozenset[str]:
    if dimension == "reason":
        return frozenset(space.reason)
    if dimension == "call_result":
        return frozenset(space.call_result)
    raise SeverityError(f"dimension {dimension!r} is out of scope for severity")


def classify_error(
    truth: frozenset[str],
    answer: frozenset[str],
    *,
    record: Record | None,
    vocabulary: frozenset[str],
    ground_truth_present: bool,
) -> tuple[str, str | None]:
    """Return `(category, sub_reason)` for one wrong answer. Pure; no model call.

    Evaluation order reproduces the decision table in
    `tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md` exactly, including the two places
    where the order is the whole point:

      * `invalid_output` is checked BEFORE the vocabulary, so a parse failure whose
        skeleton carries copied values is never reported as a fabrication.
      * `fabricated_class` is checked BEFORE the subset tests -- the hard cap. Case c11
        in the fixture (`{save cost}` vs `{save cost, other, <invented>}`) is the unit
        that a subset-first implementation silently reports as `over_labelling`.
    """
    if record is None:
        return "missing_output", None
    if not record.parse_ok:
        return "invalid_output", None
    fabricated = answer - vocabulary
    if fabricated:
        # CLOSED VOCABULARY, never the invented text. The hand-computed fixture expected
        # the labels themselves here; an adversarial review on 2026-08-09 measured that
        # those labels are the one string in this module written by a MODEL rather than
        # drawn from a validated label space, and `_SHAREABLE_UNIT_FIELDS` shipped
        # `sub_reason` -- so a model that emitted a customer name or number into a label
        # cell published it. The labels stay recoverable in the private record as
        # `fabricated_labels`. Correction recorded in the fixture, section 6.
        return "fabricated_class", "outside_vocabulary"
    if truth == answer:
        raise SeverityError(
            "classify_error was asked about an answer that equals the truth; the "
            "scorer's own correctness decision selects this population, so an equal "
            "pair here means the caller built the population somewhere else"
        )
    if not truth:
        return (
            "unsupported_claim",
            "empty_ground_truth" if ground_truth_present else "no_ground_truth_row",
        )
    if not answer:
        return "no_answer", None
    if truth < answer:
        return "over_labelling", None
    if answer < truth:
        return "under_labelling", None
    return "substitution", None


def severity_unit_id(item_id: str, product: str | None, dimension: str, arm: str) -> str:
    """A stable, shareable id for one item x product x dimension x arm classification.

    The arm is part of the identity because severity is a property of one answer, not of
    a pair: without it the two arms' units for the same row collide silently.
    """
    if not isinstance(item_id, str) or not item_id.strip():
        raise SeverityError("item_id must be a non-empty string")
    if dimension not in SEVERITY_DIMENSIONS:
        raise SeverityError(f"dimension {dimension!r} is out of scope for severity")
    if not isinstance(arm, str) or not arm.strip():
        raise SeverityError("arm must be a non-empty string")
    payload = json.dumps(
        {"item_id": item_id, "product": product, "dimension": dimension, "arm": arm},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sv_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def severity_units(
    gt: Sequence[Record],
    arms: Mapping[str, Sequence[Record]],
    dimension: str,
    *,
    item_ids: Mapping[tuple[str, str | None], str],
    space: LabelSpace = RETENTION,
) -> list[SeverityUnit]:
    """Classify every wrong answer, for every arm, on one dimension.

    `arms` is `{role: records}` with exactly two roles, because the population and the
    correctness decision come from `evalharness.compare`, which is a paired API. Reusing
    it is deliberate: re-deriving "is this answer right" in a diagnostic is how two
    definitions of correct start to drift, and `comparison_clusters`' own docstring says
    so for the judge path.

    `item_ids` maps `(call_id, phone)` to the shareable item id, so nothing this function
    returns carries a customer identifier.
    """
    if dimension not in SEVERITY_DIMENSIONS:
        raise SeverityError(f"dimension {dimension!r} is out of scope for severity")
    roles = list(arms)
    if len(roles) != 2:
        raise SeverityError(
            f"severity_units needs exactly two arm roles, got {roles!r}; the population "
            "comes from the paired comparison code and is not defined for one arm alone"
        )
    first_role, second_role = roles
    vocabulary = _vocabulary(space, dimension)
    try:
        units = comparison_units(
            list(gt), list(arms[first_role]), list(arms[second_role]), dimension
        )
    except (CoverageMismatch, ValueError) as exc:
        raise SeverityError(str(exc)) from exc

    gt_by_key = {record.key: record for record in gt}
    by_role = {
        role: {record.key: record for record in records}
        for role, records in arms.items()
    }

    results: list[SeverityUnit] = []
    for unit in units:
        key = unit.source.key
        lookup = (str(key[0]), str(key[1]))
        item_id = item_ids.get(lookup)
        if item_id is None:
            raise SeverityError(
                f"comparison unit for product {key[2]!r} matches no item in the testset "
                "passed in. Pass the same testset the runs being compared used."
            )
        gt_record = gt_by_key.get(key)
        truth = label_set(gt_record, dimension)
        for role, right in (
            (first_role, unit.incumbent_right),
            (second_role, unit.candidate_right),
        ):
            if right:
                continue
            record = by_role[role].get(key)
            answer = label_set(record, dimension)
            category, sub_reason = classify_error(
                truth,
                answer,
                record=record,
                vocabulary=vocabulary,
                ground_truth_present=gt_record is not None,
            )
            results.append(
                SeverityUnit(
                    key=key,
                    item_id=item_id,
                    arm=role,
                    dimension=dimension,
                    truth=truth,
                    answer=answer,
                    category=category,
                    sub_reason=sub_reason,
                )
            )
    return results


# ------------------------------------------------------------------ the judged remainder


def build_severity_prompt(
    transcript: str,
    unit: SeverityUnit,
    rule_texts: Sequence[str],
) -> list[dict[str, str]]:
    """The messages sent for one near/cross question.

    The arm is never named -- there is nothing to blind, because only one answer is under
    review. What the prompt does have to do is keep this question away from the question
    `judge.py` owns: the reference labelling is assumed here, not audited, or a judge that
    thinks the truth is wrong will report every substitution as near.
    """
    system = (
        "You are an independent adjudicator measuring HOW WRONG one answer is on a "
        "call-transcript labelling task. You did not produce the answer and have no "
        "stake in it.\n"
        "You are NOT asked whether the reference labelling is correct. Assume it for "
        "this question; a different review handles that.\n"
        "The answer under review dropped one or more reference classes and asserted one "
        "or more classes in their place. Decide exactly one of:\n"
        "near -- every class the answer asserted in place of a reference class is the "
        "NEIGHBOURING class: the production rules separate the two with a specific "
        "clause, and the transcript contains a span that could trigger the asserted "
        "class under its own quoted rule.\n"
        "cross -- at least one asserted class comes from a different part of the rule "
        "set: nothing in the transcript could reasonably trigger it under its quoted "
        "rule.\n"
        "unclear -- the transcript or the quoted rules do not give you enough to "
        "decide.\n"
        "HARD RULE, overriding any argument to the contrary: if the transcript contains "
        "no span that could trigger an asserted class under its own quoted rule, the "
        "answer is cross -- however similar the two class NAMES look. Similar names are "
        "not adjacency.\n"
        "HARD RULE: if any single asserted class fails the near test, the answer for "
        "the whole unit is cross.\n"
        "Award near only for demonstrated adjacency: a specific clause you can quote "
        "that distinguishes the two classes. Asserting that two classes are related is "
        "not evidence that they are.\n"
        "Apply the quoted rule text exactly as written even where it contradicts your "
        "intuition -- these class definitions are deliberate and sometimes "
        "counterintuitive. Where a line is marked text-unavailable, treat it as a "
        "pointer only and do not invent its contents.\n"
        "In cited_span, quote the exact transcript span your reasoning rests on, "
        "verbatim. In cited_rule_line, quote ONE line of the production rule text above, "
        "verbatim and without the leading '> ' marker. Both must be copied exactly; "
        "paraphrase is rejected. Respond with the JSON schema only, no other text."
    )
    user = (
        f"Dimension: {unit.dimension}\n\n"
        f"Transcript:\n{transcript}\n\n"
        "Production rule text, quoted verbatim from the production source:\n"
        + ("\n".join(rule_texts) or "(none on file)")
        + "\n\n"
        f"Reference classes the answer dropped: {', '.join(sorted(unit.missing))}\n"
        f"Classes the answer asserted instead: {', '.join(sorted(unit.extra))}\n"
        f"Reference labelling in full: {', '.join(sorted(unit.truth)) or '(none)'}\n"
        f"Answer under review in full: {', '.join(sorted(unit.answer)) or '(none)'}\n\n"
        "Near or cross?"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def severity_response_schema() -> dict[str, Any]:
    """The `response_format` sent with every severity call. `strict: True` throughout."""
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "severity_family",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {
                    "family": {"type": "string", "enum": list(FAMILY_ANSWERS)},
                    "cited_span": {"type": "string"},
                    "cited_rule_line": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["family", "cited_span", "cited_rule_line", "rationale"],
                "additionalProperties": False,
            },
        },
    }


def _invalid_family(
    raw_text: str,
    errors: Sequence[str],
    *,
    cited_span: str = "",
    cited_rule_line: str = "",
    rationale: str = "",
    evidence_status: str = EVIDENCE_INVALID_RESPONSE,
    rule_evidence_status: str = RULE_EVIDENCE_NOT_EVALUATED,
) -> FamilyVerdict:
    return FamilyVerdict(
        family="unclear",
        cited_span=cited_span,
        cited_rule_line=cited_rule_line,
        rationale=rationale,
        parse_error=True,
        raw=raw_text,
        evidence_status=evidence_status,
        rule_evidence_status=rule_evidence_status,
        validation_errors=tuple(errors),
    )


def parse_severity_response(
    raw_text: str,
    *,
    transcript: str | None = None,
    rule_lines: Sequence[str] | None = None,
) -> FamilyVerdict:
    """Parse and locally validate one raw response; never raise on model text.

    The remote `strict: True` schema is a request, not evidence the endpoint honoured it,
    so the key set, the field types and the non-empty rationale are all re-checked here.

    Both evidence gates are byte-exact and both are required for a decisive answer:
    `cited_span` must occur in the transcript, and `cited_rule_line` must occur in the
    rule text the prompt quoted -- `rule_lines` is that text, raw, exactly as it was
    formatted into the prompt. `unclear` may omit both, because "the inputs do not settle
    it" is itself a result.
    """
    try:
        payload = json.loads(raw_text)
    except (json.JSONDecodeError, TypeError):
        return _invalid_family(raw_text, ("response is not valid JSON",))
    if not isinstance(payload, dict):
        return _invalid_family(raw_text, ("response root is not an object",))

    errors: list[str] = []
    keys = set(payload)
    missing = sorted(_RESPONSE_KEYS - keys)
    extra = sorted(keys - _RESPONSE_KEYS)
    if missing:
        errors.append(f"missing required field(s): {missing}")
    if extra:
        errors.append(f"unexpected field(s): {extra}")

    family = payload.get("family")
    cited_value = payload.get("cited_span")
    rule_value = payload.get("cited_rule_line")
    rationale_value = payload.get("rationale")

    if not isinstance(family, str):
        errors.append("family must be a string")
    if family not in FAMILY_ANSWERS:
        errors.append(f"family is not one of {list(FAMILY_ANSWERS)}")
    if not isinstance(cited_value, str):
        errors.append("cited_span must be a string")
    if not isinstance(rule_value, str):
        errors.append("cited_rule_line must be a string")
    if not isinstance(rationale_value, str):
        errors.append("rationale must be a string")

    cited_span = cited_value if isinstance(cited_value, str) else ""
    cited_rule_line = rule_value if isinstance(rule_value, str) else ""
    rationale = rationale_value if isinstance(rationale_value, str) else ""
    if not rationale.strip():
        errors.append("rationale must be non-empty")

    decisive = isinstance(family, str) and family in ("near", "cross")

    evidence_status = EVIDENCE_UNCHECKED
    if decisive and not cited_span.strip():
        evidence_status = EVIDENCE_MISSING
        errors.append("decisive answers require a non-empty cited_span")
    elif not cited_span.strip():
        evidence_status = EVIDENCE_NOT_REQUIRED
    elif transcript is None:
        evidence_status = EVIDENCE_UNCHECKED
    elif cited_span in transcript:
        evidence_status = EVIDENCE_EXACT
    else:
        evidence_status = EVIDENCE_NOT_IN_TRANSCRIPT
        errors.append("cited_span is not an exact substring of the transcript")

    rule_evidence_status = RULE_EVIDENCE_NOT_EVALUATED
    if decisive and not cited_rule_line.strip():
        rule_evidence_status = RULE_EVIDENCE_MISSING
        errors.append("decisive answers require a non-empty cited_rule_line")
    elif not cited_rule_line.strip():
        rule_evidence_status = RULE_EVIDENCE_NOT_REQUIRED
    elif rule_lines is None:
        rule_evidence_status = RULE_EVIDENCE_NOT_EVALUATED
    elif cited_rule_line in "\n".join(rule_lines):
        rule_evidence_status = RULE_EVIDENCE_EXACT
    else:
        rule_evidence_status = RULE_EVIDENCE_NOT_IN_RULES
        errors.append("cited_rule_line is not an exact substring of the quoted rules")

    if errors:
        return _invalid_family(
            raw_text,
            errors,
            cited_span=cited_span,
            cited_rule_line=cited_rule_line,
            rationale=rationale,
            evidence_status=evidence_status,
            rule_evidence_status=rule_evidence_status,
        )

    return FamilyVerdict(
        family=family,
        cited_span=cited_span,
        cited_rule_line=cited_rule_line,
        rationale=rationale,
        parse_error=False,
        raw=raw_text,
        evidence_status=evidence_status,
        rule_evidence_status=rule_evidence_status,
    )


def family_aggregation(
    records: Sequence[Mapping[str, Any]]
) -> dict[str, dict[str, Any]]:
    """Collapse replicate family answers per unit by strict majority.

    Reproduces the seven-row table in the fixture's section 4. Strict majority only
    (`top count * 2 > replicate count`); a tie is `no_majority`, never broken by picking.
    A majority for `unclear` and no majority at all both resolve to `unclear_family`,
    because both mean the same thing -- this repository does not know the family -- and
    both counts stay visible underneath.
    """
    by_unit: dict[str, list[Mapping[str, Any]]] = {}
    for record in records:
        by_unit.setdefault(record["severity_unit_id"], []).append(record)
    result: dict[str, dict[str, Any]] = {}
    for unit_id, unit_records in by_unit.items():
        families = [
            r["family"]
            for r in sorted(unit_records, key=lambda r: r.get("replicate") or 1)
        ]
        counts: dict[str, int] = {}
        for family in families:
            counts[family] = counts.get(family, 0) + 1
        top_family, top_count = max(counts.items(), key=lambda kv: kv[1])
        flipped = len(set(families)) > 1
        if top_count * 2 <= len(families):
            category, no_majority = "unclear_family", True
        elif top_family == "near":
            category, no_majority = "near_family", False
        elif top_family == "cross":
            category, no_majority = "cross_family", False
        else:
            category, no_majority = "unclear_family", False
        result[unit_id] = {
            "category": category,
            "votes": f"{top_count}/{len(families)}",
            "families": families,
            "flipped": flipped,
            "no_majority": no_majority,
        }
    return result


def _counts_block(units: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    counts = {category: 0 for category in SEVERITY_CATEGORIES}
    for unit in units:
        category = str(unit["category"])
        if category not in counts:
            raise SeverityError(f"unit carries unknown category {category!r}")
        counts[category] += 1
    total = len(units)
    return {
        "wrong_units": total,
        "counts": counts,
        "rates": {
            category: (counts[category] / total if total else 0.0)
            for category in SEVERITY_CATEGORIES
        },
        "mis_scoping": sum(counts[c] for c in MIS_SCOPING),
        "mis_classification": sum(counts[c] for c in MIS_CLASSIFICATION),
    }


def severity_profile(
    units: Sequence[Mapping[str, Any]],
    *,
    arms: Sequence[str] = (),
    dimensions: Sequence[str] = SEVERITY_DIMENSIONS,
) -> dict[str, Any]:
    """Per-arm counts and rates, broken down per dimension. Denominator is wrong units.

    Never all units: a rate over all units falls whenever an arm gets more right, which
    is not what "how it fails" means. `0.0` rather than `nan` on an empty arm, matching
    `evidence._with_rates` and `judge.summarize_judgments`.

    The per-dimension breakdown is not decoration. One missing prediction row produces a
    wrong unit in `call_result` AND in `reason`, so a pooled profile double-counts it and
    reads as two independent failures. The arm-level block is the pooled view and says so
    on the line that prints it; `by_dimension` is where a category is actually read.
    """
    # Seeded from the roster, not from the units that happen to exist: an arm that got
    # everything right produced no units, and building the index from units alone made it
    # vanish from a two-arm report -- indistinguishable from an arm that was not measured.
    # Same for a dimension an arm was perfect on. This is also what makes the documented
    # `0.0`-not-`nan` empty-arm branch reachable at all.
    by_arm: dict[str, list[Mapping[str, Any]]] = {str(arm): [] for arm in arms}
    for unit in units:
        by_arm.setdefault(str(unit["arm"]), []).append(unit)
    profile: dict[str, Any] = {}
    for arm, arm_units in sorted(by_arm.items()):
        block = _counts_block(arm_units)
        block["by_dimension"] = {
            dimension: _counts_block(
                [u for u in arm_units if u["dimension"] == dimension]
            )
            for dimension in dimensions
        }
        # The pooled arm-level block double-counts a row that is wrong in more than one
        # dimension. The printed page says so; the JSON has to carry the same caveat,
        # because `--shareable-out` ships these rates with no surrounding prose.
        block["pooled_across_dimensions"] = sorted(dimensions)
        profile[arm] = block
    return profile


_SHAREABLE_UNIT_FIELDS = (
    "severity_unit_id",
    "item_id",
    "dimension",
    "arm",
    "category",
    "sub_reason",
    # Ground-truth labels only. `answer_labels` and `extra_labels` are deliberately NOT
    # here: for `fabricated_class` they are, by definition, strings outside the validated
    # vocabulary -- text a model wrote. `assert_shareable_payload` catches a bare Thai
    # MSISDN and nothing else, so a name or a hyphenated number would ship. The count is
    # the reader-facing part (it says whether constrained decoding held); the strings are
    # not, and stay in the restricted record.
    "truth_labels",
    "missing_labels",
    "fabricated_label_count",
    "judged",
    "family_votes",
    "flipped",
    "no_majority",
    "rule_text_resolved",
    "rule_text_unresolved",
)
_SHAREABLE_CALL_FIELDS = (
    "severity_unit_id",
    "item_id",
    "dimension",
    "arm",
    "replicate",
    "family",
    "parse_error",
    "evidence_status",
    "rule_evidence_status",
    # NOT `validation_errors`: those strings quote the model's own JSON key names
    # (`unexpected field(s): [...]`), which is model-written text by the same argument
    # that removed the label fields above. The count is the shareable part.
    "validation_error_count",
    "execution_status",
    "identity_status",
    "transport_error_type",
    "observed_model",
    "observed_provider",
    "reasoning_tokens",
    "cost",
    "latency_s",
    "request_sha256",
    "raw_response_sha256",
    "reused",
)


def shareable_severity_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """The report surface safe for a no-transcript aggregate artifact.

    Drops `cited_span` and `rationale` (transcript text), `cited_rule_line` (production
    source text), and `rule_text.root` -- an absolute local path carrying the operator's
    OS account name. The root was a real leak found in `judge.shareable_report` on
    2026-08-09; the fix is copied here rather than the assumption that it cannot recur.
    """
    top_level = (
        "schema_version",
        "model_requested",
        "provider_requested",
        "dimensions",
        "arms",
        "repeats",
        "deterministic_only",
        "profile",
        "judged",
        "regime_warning",
        "truncated",
        "source_provenance",
        "judge_runtime",
    )
    safe = {key: report[key] for key in top_level if key in report}
    if isinstance(report.get("rule_text"), Mapping):
        safe["rule_text"] = {
            key: value for key, value in report["rule_text"].items() if key != "root"
        }
    safe["units"] = [
        {key: unit.get(key) for key in _SHAREABLE_UNIT_FIELDS if key in unit}
        for unit in report.get("units", [])
    ]
    safe["calls"] = [
        {key: call.get(key) for key in _SHAREABLE_CALL_FIELDS if key in call}
        for call in report.get("calls", [])
    ]
    return safe


def _regime_warning(reasoning_tokens: Mapping[str, int]) -> str | None:
    """Refuse to let two decoding regimes be read as two models. Order of magnitude.

    `EXPERIMENTS.md` Experiment 4: the same model id, prompt and pack moved `reason` net
    from -1 to +24 when the endpoint changed, because the replacement reasons and the
    original did not. Nothing in this repository records the regime in a blocking gate --
    `docs/severity-plan-2026-08-09.md` does not open one either -- so the least this
    diagnostic can do is print the confound above its own table instead of leaving a
    reader to find it in a run log.

    Zero on one arm and anything on the other is always a warning: `thinkingBudget: 0` is
    production's actual regime, so an arm reasoning against an arm that cannot is the
    exact confound, not a near miss.
    """
    values = {arm: tokens for arm, tokens in reasoning_tokens.items() if tokens is not None}
    if len(values) < 2:
        return None
    low, high = min(values.values()), max(values.values())
    if high == 0:
        return None
    if low > 0 and high / low < 10:
        return None
    detail = ", ".join(f"{arm}={tokens:,}" for arm, tokens in sorted(values.items()))
    return (
        "arms differ by an order of magnitude in reasoning tokens (" + detail + "). "
        "Each arm's own profile stands; comparing the two ACROSS this gap repeats "
        "Experiment 4's confound, where an endpoint change alone moved reason net by 25 "
        "points."
    )


def _json_sha(value: Any) -> str:
    canonical = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _rule_block_for(
    unit: SeverityUnit,
    item: Any,
    union: Mapping[str, tuple[str, ...]],
    rule_root: Path,
    cache: dict[str, list[str] | None],
) -> tuple[list[str], list[str], int, int, list[str]]:
    """Quote the rules for every label in play. Returns blocks, raw lines and counts.

    Both sides are quoted -- the classes the answer dropped AND the classes it asserted.
    Quoting only the reference side is defect 4 from the 2026-08-09 review, and it is
    even more load-bearing here: the whole question is about the asserted class.
    """
    labels = sorted(unit.missing | unit.extra)
    own = dict(
        (label.lower(), citations)
        for label, citations in _rule_entries_for(
            getattr(item, "rules", None) or {}, unit.dimension, set(labels)
        )
    )
    blocks: list[str] = []
    raw_lines: list[str] = []
    resolved = 0
    unresolved_total = 0
    unresolved_fragments: list[str] = []
    for label in labels:
        citations = own.get(label.lower()) or union.get(label.lower(), ())
        if not citations:
            blocks.append(
                f"- {label}: [no rule text on file for this label anywhere in the pack "
                "-- do not invent one]"
            )
            continue
        lines, unresolved = resolve_citations_dedup(citations, rule_root, _cache=cache)
        blocks.append(
            _format_rule_entry(label, "; ".join(citations), lines, unresolved)
        )
        raw_lines.extend(lines)
        resolved += len(lines)
        unresolved_total += len(unresolved)
        unresolved_fragments.extend(unresolved)
    return blocks, raw_lines, resolved, unresolved_total, unresolved_fragments


def run_severity(
    *,
    testset: Any,
    gt: Sequence[Record],
    arms: Mapping[str, Sequence[Record]],
    arm_models: Mapping[str, str] | None = None,
    arm_reasoning_tokens: Mapping[str, int] | None = None,
    dimensions: Sequence[str],
    client: Any = None,
    model: str | None = None,
    provider: str | None = None,
    reasoning_effort: str = "none",
    rule_source_root: str | Path | None = None,
    repeats: int = 3,
    deterministic_only: bool = False,
    max_judged_units: int | None = None,
    source_provenance: Mapping[str, Any] | None = None,
    private_record_sink: Callable[[Mapping[str, Any]], None] | None = None,
    space: LabelSpace | None = None,
) -> dict[str, Any]:
    """Classify every wrong answer, then judge only the substitutions.

    `deterministic_only=True` makes **zero** model calls and still returns a complete
    report, with every substitution left as `unclear_family / not_judged`. That mode is
    how the judged remainder gets sized before any spend is approved, which is the
    deterministic-first design working as intended rather than a debugging convenience.

    `rule_source_root` is required for the judged path and refuses if it is not a
    directory: the caller asked for rule text, and a silent pointer-only downgrade is
    exactly how the 2026-08-08 false flags happened.

    The returned `units` and `calls` are review records whose rationale, transcript span
    and quoted rule line may contain transcript or production text.
    `shareable_severity_report(result)` removes those. Raw response text and the exact
    request never enter the result at all; an authorized caller retains them through
    `private_record_sink`.
    """
    if isinstance(repeats, bool) or not isinstance(repeats, int) or repeats < 1:
        raise SeverityError("repeats must be a positive integer")
    if max_judged_units is not None and (
        isinstance(max_judged_units, bool)
        or not isinstance(max_judged_units, int)
        or max_judged_units < 0
    ):
        raise SeverityError("max_judged_units must be a non-negative integer or None")
    normalised: list[str] = []
    for dimension in [dimensions] if isinstance(dimensions, str) else dimensions:
        if dimension not in SEVERITY_DIMENSIONS:
            raise SeverityError(f"dimension {dimension!r} is out of scope for severity")
        if dimension not in normalised:
            normalised.append(dimension)
    if not normalised:
        raise SeverityError("at least one severity dimension is required")
    # Derived here rather than at each call site: a third caller cannot forget it, and an
    # unknown app refuses instead of being scored against the wrong vocabulary. Hard-wiring
    # RETENTION reported MNP's legal 12th reason class as `fabricated_class`, which -- as
    # the hard cap sits above the subset tests -- also suppressed the correct
    # `over_labelling`, the exact inversion the c11 ordering exists to prevent.
    if space is None:
        space = label_space_for(getattr(testset, "app", "retention"))

    rule_root: Path | None = None
    if rule_source_root is not None:
        rule_root = Path(rule_source_root)
        if not rule_root.is_dir():
            raise SeverityError(
                f"rule_source_root {rule_root} is not a directory. Rule-text resolution "
                "was explicitly requested; refusing to fall back to pointer-only "
                "prompts silently."
            )
    if not deterministic_only:
        if client is None or not model:
            raise SeverityError(
                "the judged path needs a client and a model; pass "
                "deterministic_only=True for the zero-call classification"
            )
        if rule_root is None:
            raise SeverityError(
                "the judged path needs rule_source_root: the near/cross question is "
                "answered against quoted production rules, and the evidence gate "
                "requires them"
            )

    item_ids: dict[tuple[str, str | None], str] = {}
    items_by_lookup: dict[tuple[str, str | None], Any] = {}
    for test_item in testset.items:
        lookup = (str(test_item.call_id), str(test_item.phone_number))
        if lookup in item_ids:
            raise SeverityError(
                f"testset items {item_ids[lookup]!r} and {test_item.item_id!r} share one "
                "call/phone lookup key; a severity result could not identify which "
                "transcript it described"
            )
        item_ids[lookup] = test_item.item_id
        items_by_lookup[lookup] = test_item

    all_units: list[SeverityUnit] = []
    for dimension in normalised:
        all_units.extend(
            severity_units(gt, arms, dimension, item_ids=item_ids, space=space)
        )

    unions = (
        {
            dimension: label_citation_union(testset.items, dimension)
            for dimension in normalised
        }
        if rule_root is not None
        else {}
    )
    rule_cache: dict[str, list[str] | None] = {}
    rule_parts_resolved = 0
    rule_parts_unresolved = 0
    rule_unresolved_fragments: set[str] = set()
    units_without_rule_text: list[dict[str, Any]] = []

    unit_records: list[dict[str, Any]] = []
    prepared: list[tuple[dict[str, Any], SeverityUnit, Any, list[str], list[str]]] = []
    # Counted from the deterministic classification, never from what reached a call.
    # `--deterministic-only` exists to size this number before any spend is approved, so
    # deriving it from the call list would report zero in exactly the mode that needs it.
    substitutions_found = 0
    for order, unit in enumerate(all_units):
        lookup = (str(unit.key[0]), str(unit.key[1]))
        item = items_by_lookup[lookup]
        vocabulary = _vocabulary(space, unit.dimension)
        unit_id = severity_unit_id(unit.item_id, unit.key[2], unit.dimension, unit.arm)
        record: dict[str, Any] = {
            # Judged units are completed after the deterministic ones, so the report
            # would otherwise list units in call order rather than pack order and two
            # runs of the same data would not diff cleanly. Stripped before returning.
            "_order": order,
            "severity_unit_id": unit_id,
            "item_id": unit.item_id,
            "product": unit.key[2],
            "dimension": unit.dimension,
            "arm": unit.arm,
            "category": unit.category,
            "sub_reason": unit.sub_reason,
            "truth_labels": sorted(unit.truth),
            "answer_labels": sorted(unit.answer),
            "missing_labels": sorted(unit.missing),
            "extra_labels": sorted(unit.extra),
            "fabricated_labels": sorted(unit.answer - vocabulary),
            "fabricated_label_count": len(unit.answer - vocabulary),
            "judged": False,
            "family_votes": None,
            "flipped": None,
            "no_majority": None,
            "rule_text_resolved": None,
            "rule_text_unresolved": None,
        }
        if unit.category != "substitution":
            unit_records.append(record)
            continue
        substitutions_found += 1
        if rule_root is None:
            # Deterministic-only: a substitution is honestly "not judged", never
            # silently folded into a family it was never asked about.
            record["category"] = "unclear_family"
            record["sub_reason"] = "not_judged"
            unit_records.append(record)
            continue
        blocks, raw_lines, resolved, unresolved, fragments = _rule_block_for(
            unit, item, unions[unit.dimension], rule_root, rule_cache
        )
        rule_parts_resolved += resolved
        rule_parts_unresolved += unresolved
        rule_unresolved_fragments.update(fragments)
        record["rule_text_resolved"] = resolved
        record["rule_text_unresolved"] = unresolved
        if resolved == 0:
            # Gate 2 cannot be satisfied with nothing quoted, so the call could only
            # ever return unclear at a cost. Classified without one, and counted.
            units_without_rule_text.append(
                {
                    "item_id": unit.item_id,
                    "dimension": unit.dimension,
                    "arm": unit.arm,
                    "labels": sorted(unit.missing | unit.extra),
                }
            )
            record["category"] = "unclear_family"
            record["sub_reason"] = "no_rule_text"
            unit_records.append(record)
            continue
        prepared.append((record, unit, item, blocks, raw_lines))

    sendable = len(prepared)  # substitutions that also have rule text to quote
    truncated = max_judged_units is not None and sendable > max_judged_units
    if deterministic_only:
        # `deterministic_only` means no model CALL, not no rule resolution: resolving is
        # free and it is what makes `units_sendable` an exact forecast of the spend
        # (sendable x repeats calls) rather than an estimate.
        for record, _unit, _item, _blocks, _lines in prepared:
            record["category"] = "unclear_family"
            record["sub_reason"] = "not_judged"
            unit_records.append(record)
        prepared = []
    if max_judged_units is not None:
        for record, unit, _item, _blocks, _lines in prepared[max_judged_units:]:
            record["category"] = "unclear_family"
            record["sub_reason"] = "not_judged_truncated"
            unit_records.append(record)
        prepared = prepared[:max_judged_units]

    calls: list[dict[str, Any]] = []
    # One call cluster can carry several product rows, and the near/cross prompt never
    # mentions the product -- so those rows build a byte-identical request. Paying for the
    # same question twice is the same inflation `comparison_clusters` exists to prevent on
    # the inference side. Answered once per (request, replicate) and reused, with the reuse
    # recorded rather than hidden. Review finding, 2026-08-09.
    answered: dict[tuple[str, int], dict[str, Any]] = {}
    for record, unit, item, blocks, raw_lines in prepared:
        messages = build_severity_prompt(item.transcript_th, unit, blocks)
        request: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0.0,
            "top_p": 1.0,
            "seed": 0,
            "response_format": severity_response_schema(),
            "provider": provider,
            "reasoning_effort": reasoning_effort,
        }
        runtime = getattr(client, "runtime", None)
        wire_request = (
            build_runtime_request(runtime, **request)
            if isinstance(runtime, RuntimeSpec)
            else request
        )
        request_sha = _json_sha(wire_request)
        base = {
            "severity_unit_id": record["severity_unit_id"],
            "item_id": unit.item_id,
            "dimension": unit.dimension,
            "arm": unit.arm,
            "request_sha256": request_sha,
        }
        for replicate in range(1, repeats + 1):
            cached = answered.get((request_sha, replicate))
            if cached is not None:
                calls.append({**cached, **base, "replicate": replicate, "reused": True})
                continue
            try:
                completion = client.complete(**request)
            except Exception as exc:  # real client TransportError carries latency_s
                if not hasattr(exc, "latency_s"):
                    # A programming error in a client implementation is not a transport
                    # outcome and must not be laundered into a plausible record.
                    raise
                if private_record_sink is not None:
                    private_record_sink(
                        {
                            "severity_unit_id": record["severity_unit_id"],
                            "replicate": replicate,
                            "request": wire_request,
                            "raw_response_text": None,
                            "transport_error": f"{type(exc).__name__}: {exc}",
                        }
                    )
                calls.append(
                    {
                        **base,
                        "replicate": replicate,
                        "family": "unclear",
                        "cited_span": "",
                        "cited_rule_line": "",
                        "rationale": "",
                        "parse_error": False,
                        "evidence_status": EVIDENCE_NOT_EVALUATED,
                        "rule_evidence_status": RULE_EVIDENCE_NOT_EVALUATED,
                        "validation_errors": [],
                        "validation_error_count": 0,
                        "execution_status": "transport_error",
                        "identity_status": "not_evaluated",
                        "transport_error_type": type(exc).__name__,
                        "observed_model": None,
                        "observed_provider": None,
                        "reasoning_tokens": None,
                        "cost": None,
                        "latency_s": getattr(exc, "latency_s"),
                        "raw_response_sha256": None,
                        "reused": False,
                    }
                )
                continue

            raw_text = completion.content or ""
            verdict = parse_severity_response(
                raw_text, transcript=item.transcript_th, rule_lines=raw_lines
            )
            observed_model = getattr(completion, "observed_model", None)
            observed_provider = getattr(completion, "provider", None)
            provider_matches = provider is None or observed_provider == provider
            if observed_model == model and provider_matches:
                identity_status = "matched"
            elif observed_model is None or (
                provider is not None and observed_provider is None
            ):
                identity_status = "unverified"
            else:
                identity_status = "mismatch"
            execution_status = (
                "completed" if identity_status == "matched" else f"identity_{identity_status}"
            )
            if private_record_sink is not None:
                private_record_sink(
                    {
                        "severity_unit_id": record["severity_unit_id"],
                        "replicate": replicate,
                        "request": wire_request,
                        "raw_response_text": raw_text,
                        "completion_raw": getattr(completion, "raw", None),
                        "transport_error": None,
                    }
                )
            calls.append(
                {
                    **base,
                    "replicate": replicate,
                    # A response that failed a gate is never counted as a family answer:
                    # `parse_severity_response` already forces it to `unclear`.
                    "family": verdict.family,
                    "cited_span": verdict.cited_span,
                    "cited_rule_line": verdict.cited_rule_line,
                    "rationale": verdict.rationale,
                    "parse_error": verdict.parse_error,
                    "evidence_status": verdict.evidence_status,
                    "rule_evidence_status": verdict.rule_evidence_status,
                    "validation_errors": list(verdict.validation_errors),
                    "validation_error_count": len(verdict.validation_errors),
                    "execution_status": execution_status,
                    "identity_status": identity_status,
                    "transport_error_type": None,
                    "observed_model": observed_model,
                    "observed_provider": observed_provider,
                    "reasoning_tokens": getattr(completion, "reasoning_tokens", None),
                    "cost": getattr(completion, "cost", None),
                    "latency_s": getattr(completion, "latency_s", None),
                    "raw_response_sha256": hashlib.sha256(
                        raw_text.encode("utf-8")
                    ).hexdigest(),
                    "reused": False,
                }
            )
            answered[(request_sha, replicate)] = {
                key: value
                for key, value in calls[-1].items()
                # Identity of the UNIT, not of the answer: a reused row keeps its own.
                if key not in {"severity_unit_id", "item_id", "dimension", "arm", "replicate"}
            }
        unit_records.append(record)

    # Only calls that actually produced an answer vote. A transport error is recorded
    # with `family="unclear"` so it is never dropped, but counting it as a vote let two
    # dead connections outvote one clean gate-passing answer and wrote the unit back as
    # `judged_unclear` -- indistinguishable from a judge that looked and could not tell.
    # It also corrupted the flip rate, which the fixture makes the mandatory honesty
    # check. `judge.summarize_judgments` filters on the same condition; severity dropped
    # that gate when it collapsed to a per-unit category. Review finding, 2026-08-09.
    #
    # A response that failed an evidence gate is not an opinion either, for exactly the
    # same reason, and the first version of this filter missed it -- a second review pass
    # caught that `judge.summarize_judgments` excludes BOTH `parse_error` and a non-
    # completed execution, and this filtered only the second. It mattered: the 2026-08-09
    # run rejected 57% of responses at the gates, so under the incomplete filter most
    # units were decided by responses that never demonstrated anything.
    usable_calls = [
        c
        for c in calls
        if c["execution_status"] == "completed" and not c["parse_error"]
    ]
    aggregated = family_aggregation(usable_calls)
    for record in unit_records:
        collapsed = aggregated.get(record["severity_unit_id"])
        if collapsed is None:
            continue
        record["category"] = collapsed["category"]
        record["judged"] = True
        record["family_votes"] = collapsed["votes"]
        record["flipped"] = collapsed["flipped"]
        record["no_majority"] = collapsed["no_majority"]
        if collapsed["category"] == "unclear_family" and record["sub_reason"] is None:
            record["sub_reason"] = (
                "no_majority" if collapsed["no_majority"] else "judged_unclear"
            )
    # A unit every one of whose calls died never received an opinion at all. It stays
    # unjudged and says why, rather than borrowing the vocabulary of a judge that ran.
    attempted = {call["severity_unit_id"] for call in calls}
    for record in unit_records:
        unit_id = record["severity_unit_id"]
        if unit_id in attempted and unit_id not in aggregated:
            record["category"] = "unclear_family"
            record["sub_reason"] = "no_usable_response"

    unit_records.sort(key=lambda record: record["_order"])
    for record in unit_records:
        del record["_order"]

    judged_units = [record for record in unit_records if record["judged"]]
    runtime = getattr(client, "runtime", None)
    judge_runtime = (
        {"manifest": runtime.manifest(), "fingerprint": runtime.fingerprint()}
        if isinstance(runtime, RuntimeSpec)
        else None
    )
    # `calls` holds one row per (unit, replicate); a reused row is a copy of an answer
    # already counted. Gate counters read the real calls, or one bad answer on a
    # three-product cluster reports as three gate failures.
    real_calls = [call for call in calls if not call.get("reused")]
    invalid_calls = sum(1 for call in real_calls if call["parse_error"])
    return {
        "schema_version": 1,
        "model_requested": model,
        "provider_requested": provider,
        "dimensions": normalised,
        "arms": {
            role: {
                "model": (arm_models or {}).get(role),
                # Recorded because Experiment 4 measured a 25-point swing on `reason`
                # net from an endpoint change alone, and the difference was the
                # reasoning regime. A severity profile is honest WITHIN an arm whatever
                # the regime; comparing two arms across regimes is comparing two
                # different systems. `regime_warning` below is derived from this.
                "reasoning_tokens": (arm_reasoning_tokens or {}).get(role),
            }
            for role in sorted(arms)
        },
        "regime_warning": _regime_warning(arm_reasoning_tokens or {}),
        "repeats": repeats,
        "deterministic_only": deterministic_only,
        "units": unit_records,
        "calls": calls,
        "profile": severity_profile(
            unit_records, arms=sorted(arms), dimensions=normalised
        ),
        "judged": {
            "substitution_units_found": substitutions_found,
            "units_sendable": sendable,
            "units_judged": len(judged_units),
            "calls_made": sum(1 for call in calls if not call.get("reused")),
            "call_rows": len(calls),
            "reused_answers": sum(1 for call in calls if call.get("reused")),
            "invalid_responses": invalid_calls,
            "invalid_response_rate": (
                invalid_calls / len(real_calls) if real_calls else 0.0
            ),
            "flipped": sum(1 for record in judged_units if record["flipped"]),
            "no_majority": sum(1 for record in judged_units if record["no_majority"]),
            "transport_errors": sum(
                1 for call in real_calls if call["execution_status"] == "transport_error"
            ),
            # Transport errors are counted once, above, as transport errors. Counting
            # them here too made one outage read as two separate gate failures against
            # the goal contract's "zero transport errors, zero identity mismatches".
            "identity_not_matched": sum(
                1
                for call in real_calls
                if call["execution_status"] != "transport_error"
                and call["identity_status"] != "matched"
            ),
        },
        "truncated": truncated,
        "rule_text": {
            "enabled": rule_root is not None,
            "root": str(rule_root) if rule_root is not None else None,
            "resolved_parts": rule_parts_resolved,
            "unresolved_parts": rule_parts_unresolved,
            "unresolved_fragments": sorted(rule_unresolved_fragments),
            "units_without_rule_text": units_without_rule_text,
        },
        "source_provenance": dict(source_provenance or {}),
        "judge_runtime": judge_runtime,
    }
