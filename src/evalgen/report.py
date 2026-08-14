"""The report: a per-mechanism verdict table, not a percentage.

**Why a table and not a number.** This pack scores 22 rows over 20 items
(`tests/fixtures/testsets/README.md`, "Ground-truth rows: 22 (not 20)"). One row is
4.5 percentage points, so the smallest thing that can change moves the headline by more
than most real model differences. Worse, the exact paired-binomial rule implemented by
`compare.paired_verdict` runs on **call clusters**, not product rows. It needs six
discordant call clusters in one direction before a clean sweep reaches the preregistered
per-side tail of 1/64 (6 of 6 is 0.015625 per side, equivalent to 0.03125 two-sided;
5 of 5 is 0.0625 two-sided). A run can therefore produce a five-point gap and evidence
that never reaches the decision band, and a report whose headline is "84.1% vs 79.5%"
invites exactly the conclusion the arithmetic does not support.

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

The verdict letter saturates as a group grows. The rate does not
-----------------------------------------------------------------
The ternary is a statement about the **worst** item in a group: FAIL if ANY item is
wrong on every replicate. That rule is monotone decreasing in group size -- adding an
item can move a row toward FAIL and can never move it back -- so the letter degrades as
the pack grows even when the arm has not.

Measured, not argued. Experiment 3 ran `retention_v2` (100 items, 108 scored rows) and
whole-item correctness on replicate 1 came out **43/100 for the incumbent and 35/100 for
the candidate** (`EXPERIMENTS.md`, Experiment 3). "Correct on EVERY replicate" is a
subset of that, so those two figures are ceilings, not estimates. Treating items as
independent -- which they are not, since a family groups items that stress the same
thing, and that correlation is why the arithmetic below is an illustration rather than a
prediction -- a group of n reads PASS with probability at most 0.43^n: 0.08 at n=3, 0.006
at n=6. What actually happened matches the direction: at 100 items **all five families
read FAIL/FAIL on both arms**, and `multislot` -- the one row still separating the arms
at 20 items, FAIL/PASS -- went from 2 items to 10 and collapsed to FAIL/FAIL. The table
that is this pack's designed headline carried zero information about either model.

So `MechanismRow.always_correct` and `MechanismRow.rate`: how many items in the group
were correct on every replicate, out of the group size. A rate is not monotone in group
size. One more correct item raises it, one more wrong item lowers it, so growing the pack
measures the same quantity more precisely instead of driving every row to one value.

**The verdict is kept, not replaced,** for two reasons that are not sentiment. At small n
the letter says something the rate does not: FLAKY and FAIL are the same fraction wrong
and a different decision -- FAIL needs a prompt or a model change, FLAKY needs decoding
discipline and more replicates before the model is argued about at all -- and a rate
cannot express that, because it counts the item that never works and the item that flips
identically. And four experiments in `EXPERIMENTS.md` quote these letters; deleting them
would silently rewrite what those records mean.

The two are printed on the same row so a reader gets both without a second table. 9/10
and 1/10 are both FAIL and the letter is right in both cases -- there is an item here
that never works -- but one arm has a single bad item and the other is broken, and until
the rate was printed the report could not tell them apart.

`EXPERIMENTS.md` next step 3 of Experiment 3 asked for this in those terms ("a
group-level rule that is monotone in group size cannot survive growth"), and
`docs/eval-improvement-plan.md` finding 1 states the monotonicity.

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
from typing import Any, Literal

from evalgen.testsets import TestItem
from evalharness.compare import Disagreement, RegressionRow, paired_verdict
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

    `always_correct` is how many items in the group were correct on every replicate. It
    is the numerator of `rate`, and it is stored rather than derived from
    `len(item_ids) - len(failing_items)` so that a reader of one row can check the
    subtraction instead of reconstructing it -- the same reason `ArmSummary` carries both
    halves of `cost_per_correct_usd`.

    Read `rate` beside `verdict`, never instead of it. See the module docstring: the
    letter saturates as a group grows and the rate does not, and the letter distinguishes
    an item that never works from one that flips, which the rate cannot.

    `detail` may be multi-line. Lines after the first carry the `expected_failure` prose
    of each item that failed on every replicate, verbatim from the testset. Verbatim, not
    summarised: a paraphrase of a prediction is not a prediction anyone can check.
    """

    mechanism: str
    item_ids: tuple[str, ...]
    verdict: Verdict
    always_correct: int
    failing_items: tuple[str, ...]
    detail: str

    @property
    def rate(self) -> float:
        """`always_correct` over the group size, in [0, 1].

        The empty group returns 0.0 and not 1.0. `mechanism_table` cannot build one -- a
        group exists only once an item has joined it -- so this branch is reachable only
        by a caller constructing the row by hand, and of the two arithmetically defensible
        answers for 0/0 the vacuous 100% is the one this table must never be able to
        print. `ReportError` exists for exactly that class of input.
        """
        if not self.item_ids:
            return 0.0
        return self.always_correct / len(self.item_ids)


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

    The performance block: what the run spent
    -----------------------------------------
    `calls` through `scored_replicate_cost_usd` are the run's own per-call accounting,
    summed. `run.jsonl` has carried tokens, cost and latency per call since the runner
    was written and the xlsx export has printed them on its "Per call" sheet, but this
    report showed none of it -- so the reader of a `compare-*.txt` could see which
    mechanisms an arm passed and not what the arm cost to ask.

    Several of them carry no figure of their own. They are here so that the ones that
    do cannot be misread, which is the whole difficulty with a cost table:

      * `calls` is the denominator of everything else here. A cost is a different fact
        over 20 calls than over 60, and the section prints NOT RECORDED when this is
        zero rather than a row of zeros that reads as a free, instant run.
      * `calls_without_cost` is how many rows the cost total could not see.
        `cost_usd_lower_bound` is a LOWER BOUND, not a total: OpenRouter reports
        `usage.cost` only for providers that supply it, `client.py:272` keeps a missing
        value as None rather than zero, and `runner.total_cost()` skips those rows
        instead of counting them free (`runner.py:411-421`). Without the count beside
        it, 0.02 USD over 40 calls and 0.02 USD over the three calls that happened to
        report are the same string.
      * `correct_answers` and `scored_replicate_cost_usd` are the two halves of
        `cost_per_correct_usd`, carried so the division can be CHECKED rather than
        believed.

    **`cost_per_correct_usd` invents no new notion of "correct".** It divides one
    replicate's cost by the true-positive count of a dimension the metrics table
    already scores -- `sum(c.tp for c in DimensionResult.classes)`, the same arithmetic
    the weighted recall in section 5 is built from. `cost_per_correct_dimension` names
    which one, and the report prints that name on the line: a cost-per-correct whose
    definition of correct is not stated is a ratio nobody can reproduce.

    Numerator and denominator are both ONE replicate, and that alignment is the point.
    `dimensions` is scored on one replicate (`cli.arm_summary`), so a numerator holding
    a three-replicate bill would report three times the number an app owner would act
    on -- production makes one call per item.
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
    # Every performance field is defaulted, and `calls` is the one the report gates on.
    # An ArmSummary built without a run log -- a test, a caller scoring records it
    # already holds -- still renders; it prints NOT RECORDED rather than a zero cost,
    # because a zero here would read as "this arm was free" and that is exactly the
    # claim a missing `usage.cost` must never be allowed to make.
    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd_lower_bound: float = 0.0
    calls_without_cost: int = 0
    latency_median_s: float | None = None
    latency_max_s: float | None = None
    cost_per_correct_usd: float | None = None
    cost_per_correct_dimension: str = ""
    correct_answers: int = 0
    scored_replicate_cost_usd: float | None = None


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

    Every row also carries `always_correct` out of `len(item_ids)`, the rate the
    verdict cannot express once a group is large. The ternary above is monotone
    decreasing in group size and the rate is not; the module docstring has the
    measurement that made this necessary.

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
    """Fold one group's item x replicate grid into a verdict, a rate and an explanation.

    Two summaries of the same grid, because neither survives alone. The verdict reports
    the worst item and saturates as the group grows; the rate counts the items and does
    not. The module docstring carries the measurement.
    """
    hard: list[TestItem] = []
    flaky: list[TestItem] = []
    always_correct = 0
    for item in group_items:
        hits = sum(correct[item.item_id])
        if hits == 0:
            hard.append(item)
        elif hits < n_replicates:
            flaky.append(item)
        else:
            always_correct += 1

    if hard:
        verdict: Verdict = "FAIL"
    elif flaky:
        verdict = "FLAKY"
    else:
        verdict = "PASS"

    # The rate leads the detail rather than following the failing item ids, so it reaches
    # every consumer that prints only the first line of `detail` (`cli.py:1286`) or that
    # renders the detail column without the table above it (the xlsx Mechanisms sheet).
    # Those two would otherwise show a FAIL/FAIL column and nothing that separates it.
    parts: list[str] = [
        f"{always_correct}/{len(group_items)} items correct on all {n_replicates} "
        f"replicate{'s' if n_replicates != 1 else ''}"
    ]
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

    lines = ["; ".join(parts)]
    # The predicted failure, verbatim, for each item that never worked. This is the only
    # place the testset's `expected_failure` reaches a reader, and it is the difference
    # between "the candidate is worse" and "the candidate is worse in the way we said".
    lines.extend(f"{i.item_id} expected failure: {i.expected_failure}" for i in hard)

    return MechanismRow(
        mechanism=mechanism,
        item_ids=tuple(i.item_id for i in group_items),
        verdict=verdict,
        always_correct=always_correct,
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
    decompositions: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    title: str = "RETENTION EVAL",
    dimension_order: Sequence[str] = _DIMENSION_ORDER,
) -> str:
    """The whole report, as one string. Print it or write it; this function does neither.

    `mechanisms` maps each arm's name to its `mechanism_table` output. Both arms are
    required and both must cover the same mechanisms; see `_check_mechanisms`.

    **The order of the sections is the argument the report is making,** which is why it is
    fixed here rather than left to the caller. Provenance first, because a reader who
    stops after the header has still learned the one thing that governs everything below
    it. Then the mechanism verdicts, then the call-cluster paired disagreements, then
    what the models actually returned, then the instability count -- and only then the
    aggregate numbers, which are the most quotable and the least interpretable at n=22.
    A report that leads with 84.1% has already lost the argument in its first line,
    whatever the rest says.

    Cost, tokens and latency come after the aggregates, and that placement follows the
    same rule rather than bending it. Nothing about section 6 moves the aggregates
    earlier; what it does is put the most quotable number in the whole document -- the
    one an app owner repeats in a meeting -- behind every caveat that qualifies it. It
    also has to sit there for a plainer reason: `cost per correct answer` divides a
    count section 5's table is built from, so a reader who met it first would be asked
    to check a division whose denominator had not appeared yet.

    `decompositions` is optional: `{arm: evalgen.stability.decompose(...)}`. When
    supplied it adds 4b, which says how much of each arm's instability the scorer can
    actually see. Optional rather than required because it needs the raw run log, which
    an aggregate-only caller does not have -- and because it is a diagnostic printed
    beside section 4's count, never a replacement for it.

    The output can contain Thai (`MechanismRow.detail` carries `expected_failure`
    verbatim). Call `console.configure_stdout()` before printing it.
    """
    _check_mechanisms(incumbent, candidate, mechanisms)
    arms = (incumbent, candidate)

    lines: list[str] = []
    lines += _header(incumbent, candidate, title=title)
    lines += _mechanism_section(incumbent, candidate, mechanisms)
    lines += _disagreement_section(disagreements, regressions)
    lines += _returned_section(arms)
    lines += _flip_section(arms, decompositions)
    lines += _metrics_section(arms)
    lines += _performance_section(arms)
    lines += _not_observable_section()
    # rstrip per line: the column padding leaves trailing spaces that show up as diff
    # noise the moment anyone commits a report or pastes one into a ticket.
    return "\n".join(line.rstrip() for line in lines) + "\n"


def _header(
    incumbent: ArmSummary,
    candidate: ArmSummary,
    *,
    title: str = "RETENTION EVAL",
) -> list[str]:
    lines = [
        _RULE,
        f"{title} - PAIRED COMPARISON",
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
    """Section 1: a verdict AND a rate per mechanism, per arm.

    The columns are grouped by quantity -- both rates, then both verdicts -- rather than
    by arm, for two reasons.

    The comparison a reader actually makes is within a quantity: 9/10 against 1/10, FAIL
    against FLAKY. Grouping puts the two things being compared next to each other instead
    of one cell apart, which matters most in the case this column exists for, where every
    letter on the row is the same and only the rates differ.

    And it keeps the verdict pair as the last two whitespace-separated fields of the row.
    `tests/test_cli.py:204-210` reads a mechanism's verdicts positionally out of the
    rendered report (`line.split()[-2:]`), so a layout that interleaved rate and verdict
    would have changed what that parser returns without changing anything it asserts --
    it would have compared ["1/10", "FAIL"] against ["PASS", "FAIL"] and failed for a
    reason that has nothing to do with either model.
    """
    inc_rows = list(mechanisms[incumbent.arm])
    cand_by_name = {row.mechanism: row for row in mechanisms[candidate.arm]}

    width = max([len("mechanism")] + [len(r.mechanism) for r in inc_rows])
    # One width for every arm cell, sized off the widest group, so a 100-item pack's
    # "9/100" does not rag against a 6-item pack's "1/6" in the same column.
    digits = max([1] + [len(str(len(r.item_ids))) for r in inc_rows])
    cell = max(len("candidate"), 2 * digits + 1)

    lines = [
        _SECTION,
        "1. MECHANISM TABLE - which mechanisms each arm passes",
        _SECTION,
        # The spanning label is kept shorter than its two columns (2 * cell + 2 >= 20 for
        # any pack) so that "verdict" stays over the verdict columns. It is also the name
        # of the field it prints, `MechanismRow.always_correct`, so a reader moving
        # between the report and the code is not translating.
        f"  {'':<{width}}  {'':>{digits}}  "
        f"{'always correct':<{2 * cell + 2}}  verdict",
        f"  {'mechanism':<{width}}  {'n':>{digits}}  "
        f"{'incumbent':>{cell}}  {'candidate':>{cell}}  "
        f"{'incumbent':<{cell}}  {'candidate':<{cell}}",
    ]

    for inc in inc_rows:
        cand = cand_by_name[inc.mechanism]
        inc_rate = f"{inc.always_correct}/{len(inc.item_ids)}"
        cand_rate = f"{cand.always_correct}/{len(cand.item_ids)}"
        lines.append(
            f"  {inc.mechanism:<{width}}  {len(inc.item_ids):>{digits}}  "
            f"{inc_rate:>{cell}}  {cand_rate:>{cell}}  "
            f"{inc.verdict:<{cell}}  {cand.verdict:<{cell}}"
        )
        for arm, row in ((incumbent.arm, inc), (candidate.arm, cand)):
            if row.verdict == "PASS":
                continue
            for index, line in enumerate(row.detail.split("\n")):
                prefix = f"      {arm}: " if index == 0 else "        "
                lines.append(prefix + line)

    lines += [
        "",
        "  Two summaries of the same item x replicate grid, and they answer different",
        "  questions. Read both; neither is sound on its own.",
        "",
        "  PASS  every item in the mechanism correct on EVERY replicate.",
        "  FLAKY some item correct on some replicates and wrong on others.",
        "  FAIL  some item wrong on EVERY replicate. FAIL outranks FLAKY in one group.",
        "",
        "  The `always correct` rate is how many items in the group were correct on EVERY",
        "  replicate, out of the group size. Read it FIRST wherever a column is all FAIL,",
        "  because the letter SATURATES as a group grows and the rate does not. The letter",
        "  describes the WORST item in the group -- FAIL if ANY item is wrong on every",
        "  replicate -- so adding items can only push a row toward FAIL and never back.",
        "  Measured: whole-item correctness on one replicate ran 43/100 and 35/100 on the",
        "  100-item pack (EXPERIMENTS.md, Experiment 3), and at that size all five rows",
        "  read FAIL/FAIL on both arms. The row that still separated the arms at 20 items",
        "  went from 2 items to 10 and collapsed with the rest. A rate has no such",
        "  ceiling: one more correct item raises it, one more wrong item lowers it, so",
        "  9/10 and 1/10 stay different numbers at any pack size. Both of those are FAIL",
        "  and both letters are right -- each group holds an item that never works -- and",
        "  that is the whole reason the rate is printed beside the letter rather than",
        "  instead of it.",
        "",
        "  Why verdicts and not a percentage: 22 scored rows means one row is 4.5 points,",
        "  while the exact paired-binomial decision runs on CALL CLUSTERS, not rows.",
        "  It needs SIX discordant call clusters in one direction before a clean sweep",
        "  reaches the preregistered per-side tail (6/6 -> 1/64; equivalent two-sided",
        "  0.031, while 5/5 -> 0.063). A five-point aggregate gap at this n is therefore",
        "  quotable and not decision-grade evidence by itself.",
        "",
        "  FLAKY is a first-class result, not a rounding problem. It is where model",
        "  nondeterminism lands, and it is a different decision from FAIL: FAIL needs a",
        "  prompt or a model change, FLAKY needs decoding discipline and more replicates",
        "  before the model is argued about at all. That distinction is what the rate",
        "  cannot carry -- an item that never works and an item that flips are the same",
        "  fraction wrong and two different pieces of work -- which is why the letter",
        "  stays on the row at every pack size, and why the detail below each non-PASS",
        "  row still names which items are which.",
        "",
    ]
    return lines


def _disagreement_section(
    disagreements: Sequence[Disagreement],
    regressions: Sequence[RegressionRow],
) -> list[str]:
    lines = [
        _SECTION,
        "2. PAIRED DISAGREEMENT - one Bernoulli pair per call cluster",
        _SECTION,
        "  Statistical unit: one call cluster per dimension. If a call has several",
        "  product rows, an arm is right only when every scored unit in that call is",
        "  right; the call contributes one pair, never several correlated pairs.",
        "",
        f"  {'dimension':<12} {'both right':>10} {'both wrong':>10} "
        f"{'inc only':>9} {'cand only':>9} {'d':>4} {'net':>5} "
        f"{'exact band':>12}  verdict",
    ]
    for d in disagreements:
        verdict = paired_verdict(d)
        band = "--" if verdict.band is None else f"+/-{verdict.band}"
        lines.append(
            f"  {d.dimension:<12} {d.both_right:>10} {d.both_wrong:>10} "
            f"{d.incumbent_only_right:>9} {d.candidate_only_right:>9} "
            f"{verdict.discordant:>4} {d.net:>+5} {band:>12}  {verdict.verdict}"
        )

    lines += [
        "",
        "  The exact band is the smallest absolute net whose one-sided binomial tail is",
        "  at most 1/64, computed from discordant CALL CLUSTERS only. A net at or beyond",
        "  that threshold is AHEAD or BEHIND; a smaller net is INDISTINGUISHABLE.",
        "  Fewer than six discordant call clusters is UNDERPOWERED: the data cannot",
        "  cross the preregistered threshold even if every discordance points one way.",
        "",
        "  GRAIN MAP - these outputs answer different questions:",
        "    aggregate metrics (section 5): normalized product-row input; call_result",
        "      and reason score those rows, while product groups them into a call-level",
        "      exact product set to mirror the production scorer.",
        "    paired verdict and advisory judge: one Bernoulli unit per call cluster per",
        "      dimension; a multi-product transcript cannot inflate inferential n.",
        "    regression list below: call_result/reason stay at product-row grain; product",
        "      is one call-level exact-product-set row. These locate failures; they are",
        "      not additional independent pairs for the verdict above.",
        "",
        "  Actionable regressions the incumbent got RIGHT and the candidate got WRONG:",
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
            "    item_key is an HMAC of the applicable row/call key, never a call id or"
        )
        lines.append(
            "    phone number (keys.py). Resolving one back to a call happens inside"
        )
        lines.append(
            "    True's systems, by whoever holds EVAL_HARNESS_KEY_HMAC."
        )
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


def _flip_section(
    arms: Sequence[ArmSummary],
    decompositions: Mapping[str, Mapping[str, Any]] | None = None,
) -> list[str]:
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
    lines += _decomposition_block(arms, decompositions)
    return lines


def _decomposition_block(
    arms: Sequence[ArmSummary],
    decompositions: Mapping[str, Mapping[str, Any]] | None,
) -> list[str]:
    """How much of the raw instability the scorer can see. BESIDE the count, never instead.

    The enterprise stability gate compares the exact structured response, so it counts any
    byte that moves -- including bytes in `recommendation`, `keyword` and
    `call_event_detection`, which the production schema asks for and no metric reads. This
    block says how much of an arm's instability is of that kind.

    It is printed and never returned as a verdict, for a reason recorded in
    `tests/fixtures/STABILITY-HAND-COMPUTED.md`: it is already known that scoring
    stability at the label level would flip a recorded decision, so this measure cannot
    become the gate for that comparison without choosing the rule to fit the answer.
    """
    if not decompositions:
        return []
    lines = ["  4b. Of that instability, how much the scorer can see", ""]
    for arm in arms:
        summary = decompositions.get(arm.arm)
        if not summary:
            continue
        lines.append(
            f"  {arm.arm:<20} observable {summary['observable']:>4}  "
            f"raw-unstable {summary['raw_unstable']:>4}  "
            f"scored-unstable {summary['scored_unstable']:>4}  "
            f"cosmetic {summary['cosmetic_only']:>4} "
            f"({summary['cosmetic_share_of_instability']:.1%} of the instability)"
        )
        fields = {
            name: count
            for name, count in summary["unscored_fields_on_cosmetic_items"].items()
            if count
        }
        if fields:
            detail = ", ".join(f"{name} {count}" for name, count in fields.items())
            lines.append(f"  {'':<20} unscored fields moving on those calls: {detail}")
    lines += [
        "",
        "  `cosmetic` means the raw text changed and every label the scorer reads did",
        "  not. It is NOT a claim that the instability does not matter: whatever reads",
        "  `recommendation` downstream sees the churn, and nothing here measures whether",
        "  that text is any good.",
        "",
        "  This is a recorded diagnostic. It does not replace the stability gate, and it",
        "  must not: the direction it would move a recorded verdict is already known.",
        "",
    ]
    return lines


def _metrics_section(arms: Sequence[ArmSummary]) -> list[str]:
    lines = [
        _SECTION,
        "5. AGGREGATE METRICS - production scorer grain; read with sections 1-4",
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
        "  Aggregate grain starts from one normalized PRODUCT ROW per",
        "  (call_id, phone, product). Three dimensions, three denominators, and they are",
        "  meant to differ: call_result drops rows whose GROUND TRUTH outcome is absent,",
        "  reason drops nothing, and product groups those rows into call-level exact sets",
        "  after the NaN-phone drop. A single denominator across all three produces three",
        "  wrong numbers (metrics.py:11-13). These aggregates are not the call-cluster",
        "  Bernoulli population used by section 2 and the advisory judge.",
        "",
        "  Accuracy is deliberately absent from this table. It is inflated by an arm whose",
        "  unparseable items were dropped, and one-vs-rest accuracy over an 11-class",
        "  reason space is dominated by true negatives. Recall and F1 carry the gate",
        "  (compare.py:11-13, test_differential.py).",
        "",
    ]
    return lines


def _performance_section(arms: Sequence[ArmSummary]) -> list[str]:
    """What the run spent: tokens, cost, latency, and cost per correct answer.

    All of it is arithmetic over `ArmSummary`'s own fields. This module computes no
    metric (see the module docstring) and that rule does not soften for a division: the
    ratio, its numerator and its denominator are all handed in by `cli.arm_summary`,
    which is the only place the per-call rows exist.

    Every number here is a FLOOR rather than a total, and each is a floor for its own
    reason, which is why they are not collapsed into one caveat:

      * cost, because `usage.cost` is a provider-dependent extension and a row that did
        not report one is skipped rather than counted free (`runner.py:411-421`). The
        count of those rows is printed beside the total, not in a log.
      * reasoning tokens, because `client.py:271` reads them off
        `usage.completion_tokens_details` and a backend that sends no breakdown yields
        None, which sums as zero. Zero in that column means "none reported", not "none
        spent" -- and those tokens are already inside the completion count, so adding
        the two columns double-counts them.
      * every column, because a call that never returned carries no usage at all.
        Section 3's `transport_error` count is how many, which is why this section
        points at it rather than restating it.

    KNOWN GAP, stated here rather than filled in: there is no time-to-first-token, and
    there is nothing this harness could honestly put in that column. `client.complete`
    calls `chat.completions.create` with no `stream=True` (`client.py:234`), so the SDK
    returns once the whole body has arrived and the only interval that was ever measured
    is the full round trip. Deriving a TTFT from it would be an invented number quoted
    against a streaming production path this pack has never exercised.
    """
    lines = [
        _SECTION,
        "6. COST, TOKENS AND LATENCY - what this run spent, not what a model is worth",
        _SECTION,
        f"  {'arm':<12} {'calls':>6} {'prompt_tok':>11} {'compl_tok':>10} "
        f"{'reason_tok':>11} {'cost USD':>13} {'no cost':>8}",
    ]
    for arm in arms:
        if not arm.calls:
            lines.append(f"  {arm.arm:<12} NOT RECORDED")
            continue
        lines.append(
            f"  {arm.arm:<12} {arm.calls:>6} {arm.prompt_tokens:>11} "
            f"{arm.completion_tokens:>10} {arm.reasoning_tokens:>11} "
            f"{arm.cost_usd_lower_bound:>13.6f} {arm.calls_without_cost:>8}"
        )

    lines += [
        "",
        "  cost USD is a LOWER BOUND and `no cost` is how many of those calls the bound",
        "  could not see. OpenRouter reports usage.cost only for the providers that supply",
        "  it; client.py:272 keeps a missing value as None rather than zero and",
        "  runner.total_cost() skips those rows instead of counting them as free. Quoted",
        "  without the second column the first one is a floor whose coverage is invisible:",
        "  0.02 USD over 40 calls and 0.02 USD over the 3 calls that reported both print",
        "  as 0.02.",
        "",
        "  reason_tok is a floor for its own reason and is NOT to be added to compl_tok.",
        "  reasoning_tokens comes from usage.completion_tokens_details (client.py:271),",
        "  which is a BREAKDOWN of the completion tokens and already inside them. A",
        "  backend that sends no breakdown sums here as zero, so 0 means 'none reported',",
        "  not 'none spent' -- and a reasoning backend that spends the whole budget",
        "  thinking and returns nothing is a run-configuration bug, not a model failure",
        "  (section 3, empty_length).",
        "",
        "  A call that never returned carries no usage, so it contributes nothing to any",
        "  column above. Section 3's transport_error count is how many such calls there",
        "  were, and it is the number that says how much of this arm these totals cover.",
        "",
        "  These totals cover EVERY replicate. Cost per correct answer does not; see below.",
        "",
        "  Latency per call, over every call the run made:",
    ]
    for arm in arms:
        if arm.latency_median_s is None or arm.latency_max_s is None:
            lines.append(f"    {arm.arm:<12} NOT RECORDED")
            continue
        lines.append(
            f"    {arm.arm:<12} median {arm.latency_median_s:>8.2f}s     "
            f"max {arm.latency_max_s:>8.2f}s"
        )

    lines += [
        "    latency_s is the LAST attempt's request time with backoff sleeps excluded",
        "    (runner.py:236-238), so a retried item reports the attempt that answered and",
        "    not the wall clock it occupied. Failed calls are counted in: a 120s timeout is",
        "    120 seconds this arm cost, and a median over the successes alone would improve",
        "    as the failures got worse.",
        "    There is no time-to-first-token here and there cannot be: client.complete does",
        "    not stream (client.py:234), so the only interval ever measured is the whole",
        "    round trip. Nothing above is a streaming latency and none of it is comparable",
        "    to one.",
        "",
        "  Cost per correct answer:",
    ]
    for arm in arms:
        if arm.cost_per_correct_usd is not None:
            lines.append(
                f"    {arm.arm:<12} {arm.cost_per_correct_usd:.6f} USD  = "
                f"{arm.scored_replicate_cost_usd:.6f} / {arm.correct_answers} correct "
                f"{arm.cost_per_correct_dimension} rows"
            )
        elif not arm.calls:
            lines.append(f"    {arm.arm:<12} NOT RECORDED")
        elif arm.correct_answers == 0:
            lines.append(
                f"    {arm.arm:<12} NOT COMPUTED - this arm got 0 "
                f"{arm.cost_per_correct_dimension or 'scored'} rows right, and a cost "
                "divided by zero correct answers is not a number."
            )
        else:
            lines.append(
                f"    {arm.arm:<12} NOT COMPUTED - no provider reported a cost for the "
                "scored replicate, so the numerator is UNKNOWN rather than zero."
            )

    lines += [
        "    Numerator and denominator are ONE replicate: that replicate's cost over that",
        "    replicate's correct answers. Production makes one call per item, so a figure",
        "    dividing a 3-replicate bill by a 1-replicate hit count would be three times",
        "    the number anyone would act on. The footer names which replicate was scored.",
        "    'Correct' is not a notion invented for this line. It is the true-positive",
        "    count of the dimension named on the line, which section 5 already scores, and",
        "    the line prints the division so it can be checked rather than believed.",
        "",
        "    It inherits every caveat above it AND every caveat in section 1. The cost half",
        "    is a lower bound; at 22 scored rows one row moves the correct half by about",
        "    5%; and the two arms may have been served by different backends at different",
        "    prices on one afternoon (section 3's observed-model histogram is where that",
        "    shows). This is an order of magnitude for planning, not a price quote and not",
        "    a reason to choose a model.",
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
