"""The report: a per-mechanism verdict table, not a percentage.

**Why a table and not a number.** This pack scores 22 rows over 20 items
(`tests/fixtures/testsets/README.md`, "Ground-truth rows: 22 (not 20)"). One row is
4.5 percentage points, so the smallest thing that can change moves the headline by more
than most real model differences. Worse, the paired test that would decide whether a
difference is real -- McNemar on the discordant cells of `compare.disagreement` -- needs
**six** discordant items in one direction before an exact two-sided p drops under 0.05
(binomial: 6 of 6 gives p = 0.031, 5 of 5 gives 0.063). Six of twenty-two. A run can
therefore produce a five-point gap and a p that never approaches significance, and a
report whose headline is "84.1% vs 79.5%" invites exactly the conclusion the arithmetic
does not support.

So the headline here is which **mechanisms** each arm passes. Every item in
`retention_v1.jsonl` carries a `mechanism` field naming the capability it isolates and an
`expected_failure` field naming how a weak model is predicted to miss it. Those two
fields are what make a per-item result readable: "the candidate fails RET-10" is noise,
"the candidate fails the item where the save-shaped phrases are all agent speech, in
exactly the way the item predicted" is a finding an app owner can act on.

Grouping, and the alternative that was rejected
-----------------------------------------------
`mechanism` is a paragraph of prose about one transcript, and all twenty are distinct.
Grouping on it yields twenty groups of one, which is the per-item list again with a wider
column -- no coarsening, no table. The pack's own coarsening is `family`
(`README.md`, "Items: 20 . Families"): `clear`, `thai_linguistic`, `tiebreak`,
`multislot`, `escape`, each documented there with what it stresses. That is the default
group key. The per-item `mechanism` and `expected_failure` prose is not discarded: the
`expected_failure` of every item that failed on **every** replicate is carried into
`MechanismRow.detail`, because the reviewer's next question after "it failed" is always
"did it fail the way we said it would, or a new way", and only the second is news.

`group_by` is a parameter so a caller can ask for the per-item view deliberately rather
than getting it by accident.

FLAKY is a verdict, not a rounding problem
-------------------------------------------
The verdict is a ternary over items x replicates, and the middle value is the point.
`empty_other` was observed on run 2 of three identical Thai round-trips against
`qwen3.6-27b` (`outcomes.py:308-311`, citing the smoke-test README): the same model, the
same prompt, the same item, a different answer. A harness that runs one replicate and
reports PASS/FAIL has silently reported a coin flip as a capability. FLAKY is where that
lands, and it is a different decision from FAIL: a mechanism that fails outright needs a
prompt or a model change, one that flips needs decoding discipline or more replicates
before anyone argues about the model at all.

`n_flip` measures the same instability without reference to the labels, so it stays
meaningful on a run where both arms are wrong.

What this module refuses to do
-------------------------------
It never computes `parse_ok` (that is `outcomes.classify`, and only there), never
re-scores (that is `evalharness.metrics`), and never prints `RECONCILED: YES`. There is
no code path that prints YES: reconciliation means checking these numbers against the
Retention app's own live Gemini fact-check report, which needs real production data this
repository does not have and must not have. `AGENTS.md` ("Project-Specific Notes") says
the stamp exists so the harness cannot launder its own provenance under schedule
pressure; a flag that nothing can set is that sentence in code.

Output encoding
---------------
`MechanismRow.detail` carries `expected_failure` prose verbatim, and that prose is Thai.
Any caller that prints this string must call `console.configure_stdout()` first, or a
Thai-locale Windows console (cp874) raises `UnicodeEncodeError` on the report itself --
after the run has been paid for. Any caller that writes it to a file must pass
`encoding="utf-8"`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Literal

from evalgen.testsets import TestItem
from evalharness.compare import Disagreement, RegressionRow
from evalharness.metrics import DimensionResult
from evalharness.records import Record

__all__ = [
    "ArmSummary",
    "MechanismRow",
    "ReportError",
    "Verdict",
    "mechanism_table",
    "n_flip",
    "render",
]

Verdict = Literal["PASS", "FLAKY", "FAIL"]

# The dimensions printed first, in this order. Extras are appended sorted, so a caller
# that adds a dimension gets it printed rather than dropped.
_DIMENSION_ORDER = ("call_result", "reason", "product")

_RULE = "=" * 78
_SECTION = "-" * 78


class ReportError(ValueError):
    """Raised when a report cannot be built honestly from what it was handed.

    Everything this raises on has the same shape: an input that would let the report
    print a clean-looking number it did not earn. An item with no ground-truth row would
    compare an empty prediction set against an empty truth set and score PASS; zero
    replicates would make `all([])` true for every item and score PASS; a mechanism table
    covering only one arm would print a paired comparison against nothing. Each is a
    vacuous success, which is the one failure mode a report cannot survive.
    """


class _Absent:
    """The value of a cell in a replicate that did not emit the row at all.

    A distinct sentinel rather than `None`, because `None` is a legitimate cell value:
    `records.norm_text` maps a blank `call_result` to `None`, so a model that returned
    the row with no outcome and a model that returned no row are two different facts and
    must not compare equal.
    """

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - diagnostic only
        return "<no row>"


_ABSENT = _Absent()


@dataclass(frozen=True)
class MechanismRow:
    """One mechanism's verdict for one arm.

    `failing_items` holds every item that was not correct on every replicate -- both the
    items wrong on all of them and the items that flipped -- because the reader's
    question is "which items do I open", and splitting that across two fields makes it
    two questions. `detail` says which is which.

    `detail` may be multi-line. Lines after the first carry the `expected_failure` prose
    of each item that failed on every replicate, verbatim from the testset. Verbatim, not
    summarised: a paraphrase of a prediction is not a prediction anyone can check.
    """

    mechanism: str
    item_ids: tuple[str, ...]
    verdict: Verdict
    failing_items: tuple[str, ...]
    detail: str


@dataclass(frozen=True)
class ArmSummary:
    """Everything the report prints about one arm.

    `observed_models` is a histogram and not a string on purpose. `manifest.Manifest`
    records `model_id` as "OBSERVED from the response, not a constant"
    (`manifest.py:69`), because a router can serve a different build, quantisation or
    provider mid-run. A single value would hide that; a histogram with two keys is the
    finding.

    `outcome_counts` is keyed by `outcomes.Outcome`. It belongs beside the scores rather
    than in a log, because the failure modes are not interchangeable: `empty_length` is a
    run-configuration bug (the token budget went to reasoning), `empty_other` is a fact
    about the model, and `not_json` is a fact about the decoder. Collapsed into one
    "errors" count, the first of those looks like the model failed the task.

    `dimensions` values are `evalharness.metrics.DimensionResult`. Typed as `object` so
    the annotation does not claim more than the report needs: it reads coverage, the
    denominator and the weighted averages, and a caller supplying a dimension this module
    has never heard of is not lying to the type system.

    `answered_nothing` is the failure `outcome_counts` cannot show, because by
    `classify`'s definition it is not one: the response parsed, it carried every
    required key, and its `product` object held nothing. `flatten.to_rows` then emits
    the ground-truth skeleton with `parse_ok=True`, so the arm collects a product true
    positive for every ground-truth product while `Coverage.parse_failures` stays at
    zero. It gets its own line because "answered nothing" and "answered wrong" reach
    the product dimension as the same number and are not the same finding.
    """

    arm: str
    model: str
    prompt_sha: str
    observed_models: dict[str, int]
    outcome_counts: dict[str, int]
    n_flip: int
    dimensions: dict[str, object]
    replicates: int = 1
    decoding: Mapping[str, object] = field(default_factory=dict)
    answered_nothing: int = 0


# --------------------------------------------------------------------------------------
# grouping
# --------------------------------------------------------------------------------------


def _family_of(item: TestItem) -> str:
    """Default mechanism group: the testset's own family.

    See the module docstring for why this and not `item.mechanism`.
    """
    return item.family


# --------------------------------------------------------------------------------------
# item-level correctness
# --------------------------------------------------------------------------------------


def _signature(rows: Iterable[Record]) -> tuple:
    """One call's answer, as a sortable multiset of its rows.

    A multiset, not a dict keyed by the merge key, and the difference is load-bearing.
    `metrics.outer_join` builds `{r.key: r for r in pred}` (`metrics.py:122`), so an arm
    that emits the same `(call_id, phone, product)` twice has one of the two silently
    dropped before it is ever scored. Comparing multisets means the duplicate changes the
    signature and the item is marked wrong here, which is the last place a human can see
    it happen.

    `None` renders as `""` only so the tuples sort. The two cannot collide: `norm_text`
    and `norm_phone` (`records.py:18-42`) already map every blank to `None`, so no
    Record reaches this function carrying an empty string.
    """
    return tuple(
        sorted(
            (
                r.phone or "",
                r.product or "",
                r.call_result or "",
                tuple(sorted(r.reasons)),
            )
            for r in rows
        )
    )


def _by_call(rows: Iterable[Record]) -> dict[str, list[Record]]:
    grouped: dict[str, list[Record]] = {}
    for row in rows:
        grouped.setdefault(row.call_id, []).append(row)
    return grouped


def _item_is_correct(gt_rows: Sequence[Record], pred_rows: Sequence[Record]) -> bool:
    """Whether an arm got the whole item right on one replicate.

    Whole item, all-or-nothing, across every row the call carries. RET-16 is one call
    with three products reaching three different outcomes (`README.md`, "Ground-truth
    rows: 22"), and an arm that collapses it to a single call-level verdict has failed
    the mechanism that item exists to test even if one of the three rows survives. Partial
    credit would score that as two-thirds right and hide the failure.

    `parse_ok` is checked separately from the labels rather than inferred from them.
    An unparseable response is not a correct answer whatever fell out of it, and
    `outcomes.Classified.parse_ok` is the only definition of "this response counted"
    (`outcomes.py:100-109`).
    """
    if not all(r.parse_ok for r in pred_rows):
        return False
    return _signature(gt_rows) == _signature(pred_rows)


# --------------------------------------------------------------------------------------
# the mechanism table
# --------------------------------------------------------------------------------------


def mechanism_table(
    gt: Sequence[Record],
    per_replicate_rows: Sequence[Sequence[Record]],
    items: Sequence[TestItem],
    *,
    group_by: Callable[[TestItem], str] = _family_of,
) -> list[MechanismRow]:
    """Verdict per mechanism for ONE arm, over items x replicates.

    `per_replicate_rows` is one list of scored rows per replicate, in replicate order.
    All of an arm's replicates go in; the two arms are compared later, in `render`.

    The ternary, and its precedence:

      * **PASS**  every item in the group correct on every replicate.
      * **FLAKY** some item correct on some replicates and wrong on others.
      * **FAIL**  some item wrong on every replicate.

    FAIL outranks FLAKY when both are present. A mechanism holding one item that never
    works and one that sometimes works is not "sometimes works": the deterministic
    failure is the finding, and reporting FLAKY would let a reader believe another
    replicate might clear it.

    Raises `ReportError` rather than scoring an item with no ground-truth row, or a run
    with no replicates. Both would score PASS by vacuity -- an empty comparison against
    an empty truth -- which is the one output this table must never be able to produce.
    """
    replicates = [list(rows) for rows in per_replicate_rows]
    if not replicates:
        raise ReportError(
            "no replicates were supplied. Every item would be 'correct on every "
            "replicate' vacuously and every mechanism would report PASS, which is the "
            "one verdict this table must never reach by accident. Run at least one "
            "replicate, and at least two before believing n_flip."
        )

    gt_by_call = _by_call(gt)
    pred_by_call = [_by_call(rows) for rows in replicates]

    ordered_groups: list[str] = []
    members: dict[str, list[TestItem]] = {}
    correct: dict[str, list[bool]] = {}

    for item in items:
        gt_rows = gt_by_call.get(item.call_id)
        if not gt_rows:
            raise ReportError(
                f"{item.item_id}: no ground-truth row for call_id {item.call_id!r}. "
                "With no truth to compare against, every arm matches an empty row set "
                "and the item scores correct on every replicate, so its mechanism would "
                "report PASS having tested nothing. Load the ground truth that covers "
                "this item, or drop the item from `items`."
            )

        group = group_by(item)
        if group not in members:
            ordered_groups.append(group)
            members[group] = []
        members[group].append(item)
        correct[item.item_id] = [
            _item_is_correct(gt_rows, replicate.get(item.call_id, []))
            for replicate in pred_by_call
        ]

    return [
        _mechanism_row(group, members[group], correct, len(replicates))
        for group in ordered_groups
    ]


def _mechanism_row(
    mechanism: str,
    group_items: Sequence[TestItem],
    correct: Mapping[str, Sequence[bool]],
    n_replicates: int,
) -> MechanismRow:
    """Fold one group's item x replicate grid into a verdict and an explanation."""
    hard: list[TestItem] = []
    flaky: list[TestItem] = []
    for item in group_items:
        hits = sum(correct[item.item_id])
        if hits == 0:
            hard.append(item)
        elif hits < n_replicates:
            flaky.append(item)

    if hard:
        verdict: Verdict = "FAIL"
    elif flaky:
        verdict = "FLAKY"
    else:
        verdict = "PASS"

    parts: list[str] = []
    if hard:
        parts.append(
            "wrong on every replicate: " + ", ".join(i.item_id for i in hard)
        )
    if flaky:
        parts.append(
            "flips: "
            + ", ".join(
                f"{i.item_id} ({sum(correct[i.item_id])}/{n_replicates})" for i in flaky
            )
        )
    if not parts:
        parts.append(
            f"all {len(group_items)} items correct on all {n_replicates} "
            f"replicate{'s' if n_replicates != 1 else ''}"
        )

    lines = ["; ".join(parts)]
    # The predicted failure, verbatim, for each item that never worked. This is the only
    # place the testset's `expected_failure` reaches a reader, and it is the difference
    # between "the candidate is worse" and "the candidate is worse in the way we said".
    lines.extend(f"{i.item_id} expected failure: {i.expected_failure}" for i in hard)

    return MechanismRow(
        mechanism=mechanism,
        item_ids=tuple(i.item_id for i in group_items),
        verdict=verdict,
        failing_items=tuple(i.item_id for i in hard + flaky),
        detail="\n".join(lines),
    )


# --------------------------------------------------------------------------------------
# replicate-to-replicate instability
# --------------------------------------------------------------------------------------


def _cells(rows: Sequence[Record]) -> dict[tuple[str, tuple, str], object]:
    """One replicate's value for every (row, dimension) cell.

    Three cell families, because the three scored dimensions do not share a grain:

      * `call_result` and `reason` sit on the merge key `(call_id, phone, product)`,
        which is what the scorer joins on (`metrics.py:116`).
      * `product` sits on the CALL key `(call_id, phone)`, because product is part of the
        merge key -- a model that changes its mind about the product moves the row rather
        than changing a value inside it, so at row grain a product flip is invisible.
        `metrics._group_products` collapses to the same grain for the same reason.

    A cell's value is the multiset of what the replicate said, normally of length one.
    Length two means the arm emitted the same key twice; `metrics.outer_join` would keep
    only the last, so recording the multiset is what keeps the duplicate visible.
    """
    call_result: dict[tuple, list[str]] = {}
    reasons: dict[tuple, list[tuple[str, ...]]] = {}
    products: dict[tuple, set[str]] = {}

    for row in rows:
        call_result.setdefault(row.key, []).append(row.call_result or "")
        reasons.setdefault(row.key, []).append(tuple(sorted(row.reasons)))
        products.setdefault(row.call_key, set())
        if row.product is not None:
            products[row.call_key].add(row.product)

    cells: dict[tuple[str, tuple, str], object] = {}
    for key, values in call_result.items():
        cells[("row", key, "call_result")] = tuple(sorted(values))
    for key, sets in reasons.items():
        cells[("row", key, "reason")] = tuple(sorted(sets))
    for key, names in products.items():
        cells[("call", key, "product")] = frozenset(names)
    return cells


def n_flip(per_replicate_rows: Sequence[Sequence[Record]]) -> int:
    """How many CELLS took more than one value across replicates.

    A cell is (scored row x dimension), not a row. A row whose `call_result` and whose
    `reason` set both moved between replicates counts **two**, because those are two
    independent decisions the model made differently and they are scored under two
    different denominators (`metrics.py:11-13`). Counting it as one row would report a
    model that is unstable on both as no worse than one unstable on either.

    A row present in one replicate and missing in another counts as a change, against the
    `_ABSENT` sentinel. That is not a technicality: an arm that returns the row on two of
    three runs is exactly as unusable as one that returns a different label, and the
    coverage machinery in `metrics.Coverage` only sees the run it was handed.

    Returns 0 for fewer than two replicates, by construction rather than by policy: a
    single sample cannot vary. `render` prints the replicate count beside this number so
    a zero at n=1 is never read as stability.
    """
    replicates = [list(rows) for rows in per_replicate_rows]
    if len(replicates) < 2:
        return 0

    per_replicate_cells = [_cells(rows) for rows in replicates]
    cell_ids = {cell_id for cells in per_replicate_cells for cell_id in cells}

    flips = 0
    for cell_id in cell_ids:
        values = {cells.get(cell_id, _ABSENT) for cells in per_replicate_cells}
        if len(values) > 1:
            flips += 1
    return flips


# --------------------------------------------------------------------------------------
# rendering
# --------------------------------------------------------------------------------------


def _fmt_decoding(decoding: Mapping[str, object]) -> str:
    if not decoding:
        return "NOT RECORDED"
    return " ".join(f"{k}={v}" for k, v in sorted(decoding.items()))


def _two_col(label: str, left: str, right: str, width: int = 30) -> str:
    return f"  {label:<12} {left:<{width}} {right}"


def _check_mechanisms(
    incumbent: ArmSummary,
    candidate: ArmSummary,
    mechanisms: Mapping[str, Sequence[MechanismRow]],
) -> None:
    """Refuse a mechanism table that does not cover both arms over the same mechanisms."""
    expected = {incumbent.arm, candidate.arm}
    if set(mechanisms) != expected:
        raise ReportError(
            f"mechanisms is keyed by {sorted(mechanisms)} but the report compares "
            f"{sorted(expected)}. This is a paired report: an arm with no mechanism "
            "table would print as a column of blanks, which reads as 'nothing failed'."
        )
    names = {arm: [row.mechanism for row in rows] for arm, rows in mechanisms.items()}
    if set(names[incumbent.arm]) != set(names[candidate.arm]):
        raise ReportError(
            f"the two arms cover different mechanisms: {incumbent.arm}="
            f"{sorted(set(names[incumbent.arm]))} vs {candidate.arm}="
            f"{sorted(set(names[candidate.arm]))}. Two arms scored against different "
            "item sets are not a paired comparison, and the arm missing the harder "
            "mechanism would look better for having skipped it."
        )


def render(
    incumbent: ArmSummary,
    candidate: ArmSummary,
    mechanisms: Mapping[str, Sequence[MechanismRow]],
    disagreements: Sequence[Disagreement],
    regressions: Sequence[RegressionRow],
) -> str:
    """The whole report, as one string. Print it or write it; this function does neither.

    `mechanisms` maps each arm's name to its `mechanism_table` output. Both arms are
    required and both must cover the same mechanisms; see `_check_mechanisms`.

    **The order of the sections is the argument the report is making,** which is why it is
    fixed here rather than left to the caller. Provenance first, because a reader who
    stops after the header has still learned the one thing that governs everything below
    it. Then the mechanism verdicts, then the per-item disagreements, then what the models
    actually returned, then the instability count -- and only then the aggregate numbers,
    which are the most quotable and the least interpretable at n=22. A report that leads
    with 84.1% has already lost the argument in its first line, whatever the rest says.

    The output can contain Thai (`MechanismRow.detail` carries `expected_failure`
    verbatim). Call `console.configure_stdout()` before printing it.
    """
    _check_mechanisms(incumbent, candidate, mechanisms)
    arms = (incumbent, candidate)

    lines: list[str] = []
    lines += _header(incumbent, candidate)
    lines += _mechanism_section(incumbent, candidate, mechanisms)
    lines += _disagreement_section(disagreements, regressions)
    lines += _returned_section(arms)
    lines += _flip_section(arms)
    lines += _metrics_section(arms)
    lines += _not_observable_section()
    # rstrip per line: the column padding leaves trailing spaces that show up as diff
    # noise the moment anyone commits a report or pastes one into a ticket.
    return "\n".join(line.rstrip() for line in lines) + "\n"


def _header(incumbent: ArmSummary, candidate: ArmSummary) -> list[str]:
    lines = [
        _RULE,
        "RETENTION EVAL - PAIRED COMPARISON",
        _RULE,
        "RECONCILED: NO",
        "PROMPT: RECONSTRUCTED",
        "",
        _two_col("", f"incumbent ({incumbent.arm})", f"candidate ({candidate.arm})"),
        _two_col("model", incumbent.model, candidate.model),
        _two_col("prompt_sha", incumbent.prompt_sha[:16], candidate.prompt_sha[:16]),
        _two_col("replicates", str(incumbent.replicates), str(candidate.replicates)),
        # Decoding gets a line per arm rather than a column. It is the longest field
        # here and the one a FLAKY verdict is argued over, so it is the last thing that
        # should be truncated to fit a table.
        f"  {'decoding':<12} {incumbent.arm}: {_fmt_decoding(incumbent.decoding)}",
        f"  {'':<12} {candidate.arm}: {_fmt_decoding(candidate.decoding)}",
        "",
    ]

    if incumbent.prompt_sha != candidate.prompt_sha:
        lines.append(
            "  WARNING: the arms ran different prompts. Any difference below confounds "
            "the prompt with the model."
        )
    if incumbent.replicates != candidate.replicates:
        lines.append(
            "  WARNING: the arms ran different replicate counts, so their FLAKY verdicts "
            "and N_flip counts had unequal chances to see instability."
        )
    if not incumbent.decoding or not candidate.decoding:
        lines.append(
            "  WARNING: a decoding config that was not recorded is a run nobody can "
            "repeat, and temperature is the first thing a FLAKY verdict is blamed on."
        )

    lines += [
        "RECONCILED: NO means these scores have NOT been checked against the Retention",
        "  app's own live Gemini fact-check report. Nothing in this repository can perform",
        "  that check -- it needs real labelled production data, which this repo does not",
        "  and must not hold -- so there is no code path here that prints YES. Until it is",
        "  done these are harness output, not evidence about a model (AGENTS.md,",
        "  'Project-Specific Notes').",
        "PROMPT: RECONSTRUCTED means the prompt was reassembled from committed assets, not",
        "  read from the running system. Production fetches the user_prompt from SharePoint",
        "  at run time (main.py:1140-1156); this is the copy that was live when the harness",
        "  was written, with three example-JSON transcription bugs fixed and every audio",
        "  reference rewritten for transcript input (prompts/PORT-NOTES.md). Production also",
        "  calls Vertex with forced function calling, which this harness cannot reproduce.",
        "See NOT OBSERVABLE at the foot for the three production error sources this pack",
        "  cannot see at all.",
        "",
    ]
    return lines


def _mechanism_section(
    incumbent: ArmSummary,
    candidate: ArmSummary,
    mechanisms: Mapping[str, Sequence[MechanismRow]],
) -> list[str]:
    inc_rows = list(mechanisms[incumbent.arm])
    cand_by_name = {row.mechanism: row for row in mechanisms[candidate.arm]}

    width = max([len("mechanism")] + [len(r.mechanism) for r in inc_rows])
    lines = [
        _SECTION,
        "1. MECHANISM TABLE - which mechanisms each arm passes",
        _SECTION,
        f"  {'mechanism':<{width}}  {'n':>2}  {'incumbent':<9}  {'candidate':<9}",
    ]

    for inc in inc_rows:
        cand = cand_by_name[inc.mechanism]
        lines.append(
            f"  {inc.mechanism:<{width}}  {len(inc.item_ids):>2}  "
            f"{inc.verdict:<9}  {cand.verdict:<9}"
        )
        for arm, row in ((incumbent.arm, inc), (candidate.arm, cand)):
            if row.verdict == "PASS":
                continue
            for index, line in enumerate(row.detail.split("\n")):
                prefix = f"      {arm}: " if index == 0 else "        "
                lines.append(prefix + line)

    lines += [
        "",
        "  PASS  every item in the mechanism correct on EVERY replicate.",
        "  FLAKY some item correct on some replicates and wrong on others.",
        "  FAIL  some item wrong on EVERY replicate. FAIL outranks FLAKY in one group.",
        "",
        "  Why verdicts and not a percentage: 22 scored rows means one row is 4.5 points,",
        "  and McNemar on the paired discordant cells needs SIX items discordant in one",
        "  direction before an exact two-sided p falls under 0.05 (6/6 -> 0.031,",
        "  5/5 -> 0.063). A five-point gap at this n is compatible with no difference at",
        "  all, so a percentage here is quotable and not interpretable.",
        "",
        "  FLAKY is a first-class result, not a rounding problem. It is where model",
        "  nondeterminism lands, and it is a different decision from FAIL: FAIL needs a",
        "  prompt or a model change, FLAKY needs decoding discipline and more replicates",
        "  before the model is argued about at all.",
        "",
    ]
    return lines


def _disagreement_section(
    disagreements: Sequence[Disagreement],
    regressions: Sequence[RegressionRow],
) -> list[str]:
    lines = [
        _SECTION,
        "2. PER-ITEM DISAGREEMENT",
        _SECTION,
        f"  {'dimension':<12} {'both right':>10} {'both wrong':>10} "
        f"{'inc only':>9} {'cand only':>9} {'net':>5}",
    ]
    for d in disagreements:
        lines.append(
            f"  {d.dimension:<12} {d.both_right:>10} {d.both_wrong:>10} "
            f"{d.incumbent_only_right:>9} {d.candidate_only_right:>9} {d.net:>+5}"
        )

    lines += [
        "",
        "  Items the incumbent got RIGHT and the candidate got WRONG:",
    ]
    if not regressions:
        lines.append("    none.")
    else:
        lines.append(
            f"    {'item_key':<18} {'dimension':<12} {'ground truth':<24} "
            f"{'incumbent':<24} {'candidate':<24} error"
        )
        for r in regressions:
            lines.append(
                f"    {r.item_key:<18} {r.dimension:<12} {r.gt_label:<24} "
                f"{r.incumbent_label:<24} {r.candidate_label:<24} {r.error_type}"
            )
        lines.append(
            "    item_key is an HMAC of the merge key, never a call id or a phone number"
        )
        lines.append(
            "    (keys.py). Resolving one back to a call happens inside True's systems,"
        )
        lines.append("    by whoever holds EVAL_HARNESS_KEY_HMAC.")
    lines.append("")
    return lines


def _returned_section(arms: Sequence[ArmSummary]) -> list[str]:
    lines = [
        _SECTION,
        "3. WHAT THE MODELS ACTUALLY RETURNED",
        _SECTION,
        "  Observed model histogram (what the router served, not what was requested):",
    ]
    for arm in arms:
        if not arm.observed_models:
            lines.append(f"    {arm.arm:<12} NOT RECORDED")
            continue
        for name, count in sorted(arm.observed_models.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {arm.arm:<12} {count:>5}  {name}")
    lines += [
        "    More than one entry for an arm means the router changed build, provider or",
        "    quantisation mid-run, and the arm is not one model. manifest.py records",
        "    model_id as OBSERVED from the response for exactly this reason.",
        "",
        "  Outcome counts (outcomes.classify; parse_ok is 'ok' and nothing else):",
    ]
    for arm in arms:
        if not arm.outcome_counts:
            lines.append(f"    {arm.arm:<12} NOT RECORDED")
            continue
        rendered = "  ".join(
            f"{name}={count}" for name, count in sorted(arm.outcome_counts.items())
        )
        lines.append(f"    {arm.arm:<12} {rendered}")
    lines += [
        "    empty_length is a run-configuration bug (the token budget went to reasoning",
        "    tokens), empty_other is a fact about the model, not_json is a fact about the",
        "    decoder, provider_error is a fact about the route and not about either.",
        "    They are counted apart because they are four different fixes.",
        "    Every failure is SCORED, never dropped: dropping them gives the arm that",
        "    failed more often the easier denominator.",
        "",
        "  Answered nothing (parsed clean, every required key present, named NO product):",
    ]
    for arm in arms:
        lines.append(f"    {arm.arm:<12} {arm.answered_nothing}")
    lines += [
        "    Not an outcome and not a parse failure: classify checks that `product` is",
        "    PRESENT, not that it holds anything. flatten.to_rows then emits the",
        "    ground-truth skeleton with parse_ok=True, so such a row scores a product",
        "    true positive on every ground-truth product of that call while",
        "    parse_failures below stays at ZERO. A non-zero number here means the",
        "    product column further down is measuring silence.",
        "",
    ]
    return lines


def _flip_section(arms: Sequence[ArmSummary]) -> list[str]:
    lines = [_SECTION, "4. N_flip - replicate-to-replicate instability", _SECTION]
    for arm in arms:
        lines.append(
            f"  {arm.arm:<12} N_flip = {arm.n_flip:<5} over {arm.replicates} replicate"
            f"{'s' if arm.replicates != 1 else ''}"
        )
    lines += [
        "",
        "  A cell is (scored row x dimension), NOT a row. A row whose call_result and",
        "  whose reason set both moved counts two: two decisions, two denominators.",
        "  call_result and reason sit on (call_id, phone, product); product sits on",
        "  (call_id, phone), because a product change moves the row rather than changing",
        "  a value inside it. A row missing from one replicate counts as a change.",
        "",
        "  N_flip = 0 with one replicate is arithmetic, not stability: a single sample",
        "  cannot vary. Read it only at two replicates or more.",
        "",
    ]
    return lines


def _metrics_section(arms: Sequence[ArmSummary]) -> list[str]:
    lines = [
        _SECTION,
        "5. AGGREGATE METRICS - read last, and only with sections 1-4 in view",
        _SECTION,
        f"  {'dimension':<12} {'arm':<12} {'denom':>6} {'joined':>7} {'parse_fail':>11} "
        f"{'w-prec':>7} {'w-recall':>9} {'w-F1':>7}",
    ]

    seen = {name for arm in arms for name in arm.dimensions}
    order = [n for n in _DIMENSION_ORDER if n in seen] + sorted(
        seen - set(_DIMENSION_ORDER)
    )

    for name in order:
        for arm in arms:
            result = arm.dimensions.get(name)
            if result is None:
                lines.append(f"  {name:<12} {arm.arm:<12} {'NOT SCORED':>6}")
                continue
            if not isinstance(result, DimensionResult):
                raise ReportError(
                    f"{arm.arm}: dimensions[{name!r}] is a "
                    f"{type(result).__name__}, not a DimensionResult. The metrics table "
                    "reports coverage and the weighted averages straight off the scorer's "
                    "own result object; anything else here is a number computed somewhere "
                    "this report cannot cite."
                )
            cov = result.coverage
            lines.append(
                f"  {name:<12} {arm.arm:<12} {result.denominator:>6} "
                f"{cov.items_joined:>7} {cov.parse_failures:>11} "
                f"{result.weighted('precision'):>7.3f} "
                f"{result.weighted('recall'):>9.3f} {result.weighted('f1'):>7.3f}"
            )

    lines += [
        "",
        "  Three dimensions, three denominators, and they are meant to differ:",
        "  call_result drops rows whose GROUND TRUTH outcome is absent, reason drops",
        "  nothing, product works on call-grain groups after the NaN-phone drop. A single",
        "  denominator across all three produces three wrong numbers (metrics.py:11-13).",
        "",
        "  Accuracy is deliberately absent from this table. It is inflated by an arm whose",
        "  unparseable items were dropped, and one-vs-rest accuracy over an 11-class",
        "  reason space is dominated by true negatives. Recall and F1 carry the gate",
        "  (compare.py:11-13, test_differential.py).",
        "",
    ]
    return lines


def _not_observable_section() -> list[str]:
    return [
        _RULE,
        "NOT OBSERVABLE BY THIS PACK",
        _RULE,
        "Production is handed an AUDIO FILE and asked to identify the speakers,",
        "transcribe them and label the call in ONE pass (prompt.py:4314-4315). This pack",
        "sends clean, pre-tagged Thai text. Three of production's error sources are",
        "therefore invisible here, and no verdict above says anything about them:",
        "",
        "  * AGENT-SPEECH MISATTRIBUTION. Every transcript turn is already tagged with",
        "    its speaker, so the model never has to decide who spoke. prompt.py:4382-4387",
        "    makes the reason depend on the phrase being CUSTOMER speech; RET-10 tests",
        "    whether a model USES the tags it was given, not whether it could recover them",
        "    from audio, which is the harder thing production actually does.",
        "  * ASR ERROR. Thai tone marks, proper nouns, code-switching and telephone-band",
        "    distortion are absent. RET-11 hand-writes ASR-shaped orthography; that is an",
        "    imitation of one failure, not a measurement of the transcription stage.",
        "  * DIARISATION. Speaker segmentation, crosstalk, holds and silence do not exist",
        "    in this text at all.",
        "",
        "Also not measurable here: issue_type, which no line of the Retention app gives",
        "semantics to, so this pack carries no issue_type label and cannot detect an",
        "issue_type regression (README.md, 'issue_type - out of scope, deliberately",
        "absent').",
        "",
        "The Thai in this pack was DRAFTED BY AN LLM and has no native-speaker sign-off.",
        "'The model handled this Thai correctly' is evidence about THIS TEXT.",
        "",
        "A result here is a comparison between two models on one controlled dimension. It",
        "is not a production accuracy estimate, and whether the ranking transfers to audio",
        "is untested.",
    ]
