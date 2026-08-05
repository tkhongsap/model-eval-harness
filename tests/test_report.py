"""The mechanism table, the FLAKY verdict, N_flip, and what `render` must print.

Every test here is built so that it FAILS on the obvious wrong implementation, not just
on a broken one. The three that matter most, and the plausible mistake each one catches:

  * a mechanism whose item is wrong on every replicate reports FAIL -- catches an
    implementation that averages replicates and calls 0/3 "mostly wrong";
  * an item that flips reports FLAKY -- catches the far more likely mistake of majority
    voting the replicates, which turns 2/3 into PASS and deletes the nondeterminism the
    replicates were run to find;
  * `n_flip` counts CELLS -- catches counting rows, which reports a model unstable on
    both its outcome and its reasons as no worse than one unstable on either.

`render` is asserted on its stamps and its section ORDER rather than its wording. The
order is the argument the report makes (provenance, then verdicts, then the aggregate
numbers last), and it is the part a well-meaning edit breaks first.

Section 6 -- cost, tokens and latency -- is asserted on the three things that make a
cost figure honest rather than merely present, because a cost table is the part of a
report that gets quoted with no context at all:

  * the total is labelled a LOWER BOUND and prints how many calls it could not see,
    which catches the plausible implementation that sums `cost or 0` and reports a
    confident number covering a third of the run;
  * `cost per correct answer` names the dimension it divided by, which catches a ratio
    whose definition of correct nobody can reproduce;
  * an arm with no reported cost prints UNKNOWN, not 0.000000 -- the one output that
    would let a run with no cost accounting read as a free model.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.report import (  # noqa: E402
    ArmSummary,
    ReportError,
    mechanism_table,
    n_flip,
    render,
)
from evalgen.testsets import TestItem, load_testset  # noqa: E402
from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.compare import Disagreement, RegressionRow  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import score_call_result, score_product, score_reason  # noqa: E402
from evalharness.records import from_row  # noqa: E402

TESTSETS = ROOT / "tests" / "fixtures" / "testsets"


# --- builders -----------------------------------------------------------------
# Records go through `from_row` rather than the Record constructor so these fixtures
# get the same normalisation the real adapter applies. A test that hand-builds a
# Record can assert on a value the loader would never produce.


def rec(call_id, product, call_result, reasons="", *, phone="0810000001", parse_ok=True):
    return from_row(
        {
            "call_id": call_id,
            "phone_number": phone,
            "product": product,
            "call_result": call_result,
            "main": reasons,
            "secondary": "",
            "third": "",
            "parse_ok": "true" if parse_ok else "false",
        }
    )


def item(item_id, call_id, family="clear", expected_failure="predicted miss"):
    return TestItem(
        item_id=item_id,
        call_id=call_id,
        family=family,
        transcript_th="ลูกค้า: ทดสอบ",
        phone_number="0810000001",
        mechanism=f"mechanism prose unique to {item_id}",
        why_it_matters="why",
        gt=({"product": "postpaid", "call_result": "save", "main": "network",
             "secondary": None, "third": None},),
        evidence={},
        rules={},
        expected_failure=expected_failure,
    )


def arm(name, **overrides):
    base = dict(
        arm=name,
        model=f"vendor/{name}-model",
        prompt_sha="a" * 64,
        observed_models={f"vendor/{name}-model": 20},
        outcome_counts={"ok": 20},
        n_flip=0,
        dimensions={},
        replicates=3,
        decoding={"temperature": 0.0, "top_p": 1.0},
    )
    base.update(overrides)
    return ArmSummary(**base)


# --- the verdict ternary ------------------------------------------------------


def test_all_correct_on_every_replicate_is_pass():
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    replicates = [list(gt), list(gt), list(gt)]

    (row,) = mechanism_table(gt, replicates, items)

    assert row.verdict == "PASS"
    assert row.failing_items == ()
    assert row.item_ids == ("RET-01",)


def test_an_item_wrong_on_every_replicate_is_fail():
    """0/3 is FAIL, not 'mostly wrong'. This is the verdict an averaging
    implementation gets wrong, and it is the one that stops a migration."""
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    wrong = [rec("5001", "postpaid", "churn", "network")]
    replicates = [list(wrong), list(wrong), list(wrong)]

    (row,) = mechanism_table(gt, replicates, items)

    assert row.verdict == "FAIL"
    assert row.failing_items == ("RET-01",)
    assert "wrong on every replicate: RET-01" in row.detail


def test_an_item_that_flips_is_flaky():
    """2/3 is FLAKY, not PASS. Majority-voting the replicates would report PASS and
    delete the only thing three replicates were run to find."""
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    right = [rec("5001", "postpaid", "save", "network")]
    wrong = [rec("5001", "postpaid", "churn", "network")]
    replicates = [list(right), list(wrong), list(right)]

    (row,) = mechanism_table(gt, replicates, items)

    assert row.verdict == "FLAKY", (
        "an item correct on 2 of 3 replicates is FLAKY. Reporting PASS here would let a "
        "coin flip be recorded as a capability."
    )
    assert row.failing_items == ("RET-01",)
    assert "RET-01 (2/3)" in row.detail


def test_fail_outranks_flaky_in_the_same_mechanism():
    """One item that never works plus one that sometimes works is FAIL. Reporting
    FLAKY would suggest another replicate might clear it; nothing will."""
    gt = [rec("5001", "postpaid", "save", "network"), rec("5002", "tol", "churn", "network")]
    items = [item("RET-01", "5001"), item("RET-02", "5002")]
    hard_wrong = rec("5002", "tol", "save", "network")
    right_1 = rec("5001", "postpaid", "save", "network")
    wrong_1 = rec("5001", "postpaid", "churn", "network")
    replicates = [[right_1, hard_wrong], [wrong_1, hard_wrong], [right_1, hard_wrong]]

    (row,) = mechanism_table(gt, replicates, items)

    assert row.verdict == "FAIL"
    assert set(row.failing_items) == {"RET-01", "RET-02"}
    assert "wrong on every replicate: RET-02" in row.detail
    assert "RET-01 (2/3)" in row.detail


def test_hard_failure_carries_the_items_predicted_failure_verbatim():
    """The reviewer's next question after 'it failed' is 'the way we predicted, or a
    new way'. Only the second is news, and only the verbatim prose answers it."""
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001", expected_failure="churn จากคำว่า ย้ายค่าย")]
    wrong = [[rec("5001", "postpaid", "churn", "network")]]

    (row,) = mechanism_table(gt, wrong, items)

    assert "RET-01 expected failure: churn จากคำว่า ย้ายค่าย" in row.detail


def test_a_partly_right_multi_row_item_is_wrong():
    """RET-16 is one call with three products reaching three outcomes. An arm that
    gets two of the three rows right has still failed the mechanism the item exists
    to test, so there is no partial credit."""
    gt = [
        rec("5016", "postpaid", "save", "save cost"),
        rec("5016", "tol", "churn", "save cost"),
        rec("5016", "tvs", "unknown", ""),
    ]
    items = [item("RET-16", "5016", family="multislot")]
    partial = [gt[0], gt[1]]  # the tvs row never emitted

    (row,) = mechanism_table(gt, [partial], items)

    assert row.verdict == "FAIL"


def test_a_duplicate_row_is_wrong_rather_than_silently_collapsed():
    """metrics.outer_join builds a dict off the merge key, so a duplicated
    (call_id, phone, product) loses one of its two answers before scoring. This
    table is the last place that is visible."""
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    duplicated = [
        rec("5001", "postpaid", "save", "network"),
        rec("5001", "postpaid", "churn", "network"),
    ]

    (row,) = mechanism_table(gt, [duplicated], items)

    assert row.verdict == "FAIL"


def test_an_unparseable_row_is_never_correct():
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    unparsed = [rec("5001", "postpaid", "save", "network", parse_ok=False)]

    (row,) = mechanism_table(gt, [unparsed], items)

    assert row.verdict == "FAIL", (
        "the labels match but the response never parsed. parse_ok is the only "
        "definition of 'this response counted' (outcomes.py:100-109)."
    )


# --- grouping -----------------------------------------------------------------


def test_items_group_by_family_in_first_appearance_order():
    gt = [rec(str(5000 + i), "postpaid", "save", "network") for i in range(1, 4)]
    items = [
        item("RET-01", "5001", family="clear"),
        item("RET-07", "5002", family="thai_linguistic"),
        item("RET-02", "5003", family="clear"),
    ]

    rows = mechanism_table(gt, [list(gt)], items)

    assert [r.mechanism for r in rows] == ["clear", "thai_linguistic"]
    assert rows[0].item_ids == ("RET-01", "RET-02")


def test_group_by_is_overridable_for_the_per_item_view():
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]

    rows = mechanism_table(gt, [list(gt)], items, group_by=lambda i: i.item_id)

    assert [r.mechanism for r in rows] == ["RET-01"]


# --- the refusals -------------------------------------------------------------


def test_zero_replicates_is_refused_not_scored_as_pass():
    """all([]) is True, so an empty run would report PASS on every mechanism."""
    gt = [rec("5001", "postpaid", "save", "network")]
    with pytest.raises(ReportError) as exc:
        mechanism_table(gt, [], [item("RET-01", "5001")])
    assert "no replicates" in str(exc.value)


def test_an_item_with_no_ground_truth_is_refused():
    """Empty truth versus empty prediction compares equal and scores PASS."""
    gt = [rec("5001", "postpaid", "save", "network")]
    with pytest.raises(ReportError) as exc:
        mechanism_table(gt, [list(gt)], [item("RET-99", "5099")])
    assert "RET-99" in str(exc.value) and "no ground-truth row" in str(exc.value)


# --- N_flip counts cells, not rows --------------------------------------------


def test_n_flip_counts_cells_not_rows():
    """One row whose call_result AND reason both move is TWO flips. Counting rows
    would report it as one and rank a model unstable on both no worse than a model
    unstable on either."""
    a = [rec("5001", "postpaid", "save", "network")]
    b = [rec("5001", "postpaid", "churn", "save cost")]

    assert n_flip([a, b]) == 2


def test_n_flip_counts_one_when_only_one_dimension_moves():
    a = [rec("5001", "postpaid", "save", "network")]
    b = [rec("5001", "postpaid", "churn", "network")]

    assert n_flip([a, b]) == 1


def test_n_flip_is_zero_when_the_replicates_agree():
    a = [rec("5001", "postpaid", "save", "network")]
    assert n_flip([a, list(a), list(a)]) == 0


def test_n_flip_sees_a_product_change_that_row_grain_cannot():
    """product is part of the merge key, so a product change MOVES the row instead of
    changing a value inside it. The product cell sits on the call key for that reason:
    without it the change would register only as two rows appearing and disappearing."""
    a = [rec("5001", "postpaid", "save", "network")]
    b = [rec("5001", "tol", "save", "network")]

    flips = n_flip([a, b])

    assert flips == 5, (
        "expected the product cell on (call_id, phone) plus call_result and reason "
        f"on each of the two now-distinct row keys, got {flips}"
    )


def test_n_flip_counts_a_missing_row_as_a_change():
    """An arm that returns the row on one run and not the other is exactly as
    unusable as one that returns a different label."""
    a = [rec("5001", "postpaid", "save", "network")]

    assert n_flip([a, []]) == 3  # call_result, reason, and the product set for the call


def test_n_flip_is_zero_for_a_single_replicate():
    a = [rec("5001", "postpaid", "save", "network")]
    assert n_flip([a]) == 0, "one sample cannot vary; this is arithmetic, not stability"


# --- render -------------------------------------------------------------------


"""Performance figures for the shared fixture, chosen so every printed number can be
checked by eye against the one beside it.

Two items over three replicates is six calls per arm. The candidate is shaped like the
reasoning arm this section exists to make visible: four times the cost, a real reasoning
count inside its completion tokens, a tail latency 12x its median, and two of its six
calls reporting no cost at all -- which is the case where a bare total lies.

`scored_replicate_cost_usd` is deliberately a THIRD of the run total, because the ratio
divides one replicate's cost by one replicate's correct answers while the table above it
covers all three. A test that gave them the same value would pass against an
implementation that had confused the two.
"""
INCUMBENT_PERF = dict(
    calls=6,
    prompt_tokens=7200,
    completion_tokens=1080,
    reasoning_tokens=0,
    cost_usd_lower_bound=0.006,
    calls_without_cost=0,
    latency_median_s=1.25,
    latency_max_s=2.5,
    cost_per_correct_usd=0.001,
    cost_per_correct_dimension="call_result",
    correct_answers=2,
    scored_replicate_cost_usd=0.002,
)

CANDIDATE_PERF = dict(
    calls=6,
    prompt_tokens=15600,
    completion_tokens=8700,
    reasoning_tokens=6600,
    cost_usd_lower_bound=0.024,
    calls_without_cost=2,
    latency_median_s=4.2,
    latency_max_s=51.4,
    cost_per_correct_usd=0.008,
    cost_per_correct_dimension="call_result",
    correct_answers=1,
    scored_replicate_cost_usd=0.008,
)


@pytest.fixture
def report_text():
    gt = [rec("5001", "postpaid", "save", "network"), rec("5002", "tol", "churn", "network")]
    items = [item("RET-01", "5001"), item("RET-07", "5002", family="thai_linguistic")]
    good = [list(gt), list(gt), list(gt)]
    bad = [[gt[0], rec("5002", "tol", "save", "network")]] * 3

    incumbent = arm("incumbent", dimensions={
        "call_result": score_call_result(gt, gt, RETENTION.call_result),
        "reason": score_reason(gt, gt, RETENTION.reason),
        "product": score_product(gt, gt, RETENTION.product),
    }, **INCUMBENT_PERF)
    candidate = arm("candidate", n_flip=4, dimensions={
        "call_result": score_call_result(gt, bad[0], RETENTION.call_result),
        "reason": score_reason(gt, bad[0], RETENTION.reason),
        "product": score_product(gt, bad[0], RETENTION.product),
    }, **CANDIDATE_PERF)

    return render(
        incumbent,
        candidate,
        {
            "incumbent": mechanism_table(gt, good, items),
            "candidate": mechanism_table(gt, bad, items),
        },
        [Disagreement("call_result", both_right=1, both_wrong=0,
                      incumbent_only_right=1, candidate_only_right=0)],
        [RegressionRow("0123456789abcdef", "call_result", "churn", "churn", "save",
                       "wrong_label")],
    )


def test_render_prints_the_provenance_stamps(report_text):
    """The two stamps that stop a report being read as a migration verdict. Neither
    has a code path that can turn it off (AGENTS.md, 'Project-Specific Notes').

    Asserted as whole LINES inside the header, not as substrings of the report. Both
    strings also appear further down in the paragraph that explains them, so a
    substring check stays green while the stamp itself has been deleted -- which is
    what a mutation run of this file caught before this test was trusted.
    """
    head = report_text.splitlines()[:8]
    assert "RECONCILED: NO" in head, f"stamp missing from the header block: {head}"
    assert "PROMPT: RECONSTRUCTED" in head, f"stamp missing from the header block: {head}"
    assert "RECONCILED: YES" not in report_text


def test_render_puts_the_sections_in_the_mandated_order(report_text):
    """The order is the argument: provenance, verdicts, per-item disagreement, what the
    models returned, instability -- and the quotable numbers LAST."""
    order = [
        "RECONCILED: NO",
        "1. MECHANISM TABLE",
        "2. PER-ITEM DISAGREEMENT",
        "3. WHAT THE MODELS ACTUALLY RETURNED",
        "4. N_flip",
        "5. AGGREGATE METRICS",
        "6. COST, TOKENS AND LATENCY",
        "NOT OBSERVABLE BY THIS PACK",
    ]
    found = [report_text.index(marker) for marker in order]
    assert found == sorted(found), (
        "sections are out of order. A report that leads with an aggregate percentage at "
        f"n=22 has lost the argument in its first line. Got: {list(zip(order, found))}"
    )


def test_the_cost_section_never_precedes_the_mechanism_table(report_text):
    """Cost is the most quotable number in the document and the easiest to move up.

    Asserted separately from the order test above rather than trusted to it, because
    this is the specific edit that would be argued for on its own merits -- "put the
    cost where people will see it" -- and the whole point of section 1 is that a reader
    who stops early must stop on the verdicts, not on a price.
    """
    assert report_text.index("1. MECHANISM TABLE") < report_text.index(
        "6. COST, TOKENS AND LATENCY"
    )
    assert report_text.index("5. AGGREGATE METRICS") < report_text.index(
        "6. COST, TOKENS AND LATENCY"
    ), (
        "cost per correct answer divides a count section 5's table is built from, so it "
        "cannot be read before the denominator has appeared"
    )


def test_render_shows_both_arms_verdicts_per_mechanism(report_text):
    assert "thai_linguistic" in report_text
    assert "FAIL" in report_text and "PASS" in report_text


def test_render_prints_n_flip_with_its_replicate_count(report_text):
    assert "N_flip = 4" in report_text
    assert "over 3 replicates" in report_text


def test_render_prints_the_observed_model_histogram(report_text):
    assert "vendor/incumbent-model" in report_text
    assert "vendor/candidate-model" in report_text


def test_render_prints_the_not_observable_note(report_text):
    """The three production error sources this pack cannot see. Without them a clean
    mechanism table reads as 'ready for production'."""
    lowered = report_text.lower()
    assert "misattribution" in lowered
    assert "asr error" in lowered
    assert "diarisation" in lowered


def test_render_warns_when_the_arms_ran_different_prompts(report_text):
    gt = [rec("5001", "postpaid", "save", "network")]
    items = [item("RET-01", "5001")]
    table = mechanism_table(gt, [list(gt)], items)
    text = render(
        arm("incumbent"),
        arm("candidate", prompt_sha="b" * 64),
        {"incumbent": table, "candidate": table},
        [],
        [],
    )
    assert "different prompts" in text


# --- section 6: cost, tokens and latency --------------------------------------


def test_render_prints_the_per_call_accounting_it_was_handed(report_text):
    """The gap this section closes: run.jsonl carries tokens, cost and latency per call
    and the text report printed none of them, so a reader could see which mechanisms an
    arm passed and nothing about what asking it cost."""
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "  incumbent         6        7200       1080           0" in section
    assert "  candidate         6       15600       8700        6600" in section
    assert "0.006000" in section and "0.024000" in section
    assert "median     1.25s" in section and "max     2.50s" in section
    assert "median     4.20s" in section and "max    51.40s" in section


def test_the_cost_total_is_labelled_a_lower_bound_and_says_what_it_missed(report_text):
    """A floor whose coverage is invisible is worse than no number, because it is
    quotable: 0.02 USD over 40 calls and 0.02 USD over the 3 calls that happened to
    report `usage.cost` both print as 0.02. The count travels with the total onto the
    page, not into a log."""
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "LOWER BOUND" in section
    assert "no cost" in section, "the column that says how much of the run the total saw"
    # The candidate's two uncosted calls, on the arm's own row rather than only in the
    # prose: a caveat in a paragraph below a table does not travel with a pasted row.
    candidate_row = next(
        line for line in section.splitlines() if "0.024000" in line
    )
    assert candidate_row.split()[-1] == "2", (
        f"the uncosted-call count must be on the arm's own row, got {candidate_row!r}"
    )


def test_cost_per_correct_answer_names_the_dimension_and_prints_the_division(report_text):
    """A cost-per-correct whose definition of 'correct' is not stated is a ratio nobody
    can reproduce -- and the three scored dimensions have three different denominators
    (metrics.py:11-13), so they would give three different answers.

    The division is printed, not just its result, so a reader can check it against the
    numbers two sections above rather than believe it.
    """
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "0.001000 USD  = 0.002000 / 2 correct call_result rows" in section
    assert "0.008000 USD  = 0.008000 / 1 correct call_result rows" in section


def test_cost_per_correct_answer_uses_one_replicate_on_both_sides(report_text):
    """The run cost 0.006 over three replicates and the ratio divides 0.002. Mixing a
    3-replicate bill into a 1-replicate hit count would report three times the number an
    app owner would act on, because production makes one call per item."""
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "= 0.002000 /" in section, "the numerator is the scored replicate's cost"
    assert "= 0.006000 /" not in section, "not the whole run's bill"
    assert "ONE replicate" in section, "and the report has to say so"


def test_an_arm_that_got_nothing_right_prints_no_ratio_at_all():
    """Zero correct answers is a division by zero, and 'infinite' is not a cost. The
    refusal names the reason rather than printing a blank, because a blank cell in a
    cost table reads as a number nobody bothered to fill in."""
    gt = [rec("5001", "postpaid", "save", "network")]
    table = mechanism_table(gt, [list(gt)], [item("RET-01", "5001")])
    text = render(
        arm("incumbent", calls=3, cost_usd_lower_bound=0.003,
            cost_per_correct_dimension="call_result", correct_answers=0,
            scored_replicate_cost_usd=0.001, latency_median_s=1.0, latency_max_s=1.0),
        arm("candidate", calls=3, cost_usd_lower_bound=0.003,
            cost_per_correct_dimension="call_result", correct_answers=0,
            scored_replicate_cost_usd=0.001, latency_median_s=1.0, latency_max_s=1.0),
        {"incumbent": table, "candidate": table},
        [],
        [],
    )
    section = text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "NOT COMPUTED" in section
    assert "0 call_result rows right" in section
    assert "USD  =" not in section, "no ratio may be printed when there is no denominator"


def test_an_arm_whose_provider_reported_no_cost_says_unknown_never_zero():
    """The output that must not exist. OpenRouter reports usage.cost only for the
    providers that supply it, so an arm can finish a full run with nothing to total --
    and 0.000000 USD per correct answer reads as a free model rather than as an
    unmeasured one."""
    gt = [rec("5001", "postpaid", "save", "network")]
    table = mechanism_table(gt, [list(gt)], [item("RET-01", "5001")])
    unmeasured = dict(
        calls=3, cost_usd_lower_bound=0.0, calls_without_cost=3,
        cost_per_correct_usd=None, cost_per_correct_dimension="call_result",
        correct_answers=1, scored_replicate_cost_usd=0.0,
        latency_median_s=1.0, latency_max_s=1.0,
    )
    text = render(
        arm("incumbent", **unmeasured),
        arm("candidate", **unmeasured),
        {"incumbent": table, "candidate": table},
        [],
        [],
    )
    section = text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "UNKNOWN rather than zero" in section
    assert "0.000000 USD" not in section, (
        "a zero cost per correct answer is a claim that the model was free. The "
        "numerator was never reported, which is a different fact and the report must "
        "print that one."
    )


def test_an_arm_with_no_per_call_accounting_prints_not_recorded():
    """An ArmSummary built without a run log -- a test, or a caller scoring records it
    already holds -- must not render as a free, instant run."""
    gt = [rec("5001", "postpaid", "save", "network")]
    table = mechanism_table(gt, [list(gt)], [item("RET-01", "5001")])
    text = render(arm("incumbent"), arm("candidate"),
                  {"incumbent": table, "candidate": table}, [], [])
    section = text.split("6. COST, TOKENS AND LATENCY")[1]

    assert section.count("NOT RECORDED") >= 4, (
        "both arms, in both the totals table and the latency block"
    )
    assert "0.000000" not in section


def test_the_report_states_that_time_to_first_token_does_not_exist_here(report_text):
    """The number a reader will look for and this harness cannot honestly supply.
    `client.complete` calls `chat.completions.create` with no stream=True
    (client.py:234), so the SDK returns once the whole body has arrived and the only
    interval ever measured is the round trip. Stating the gap is the alternative to
    deriving a TTFT from a number that is not one."""
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "no time-to-first-token" in section
    assert "does" in section and "not stream" in section
    assert "client.py:234" in section, "the claim cites the line that makes it true"


def test_reasoning_tokens_are_flagged_as_already_inside_the_completion_count(report_text):
    """`reasoning_tokens` comes off usage.completion_tokens_details (client.py:271),
    which is a BREAKDOWN of the completion tokens. A reader adding the two columns
    double-counts them, and on a reasoning arm that is most of the bill."""
    section = report_text.split("6. COST, TOKENS AND LATENCY")[1]

    assert "NOT to be added to compl_tok" in section
    assert "already inside them" in section


def test_render_refuses_a_mechanism_table_that_covers_one_arm():
    gt = [rec("5001", "postpaid", "save", "network")]
    table = mechanism_table(gt, [list(gt)], [item("RET-01", "5001")])
    with pytest.raises(ReportError) as exc:
        render(arm("incumbent"), arm("candidate"), {"incumbent": table}, [], [])
    assert "paired report" in str(exc.value)


def test_render_refuses_arms_scored_on_different_mechanisms():
    gt = [rec("5001", "postpaid", "save", "network"), rec("5002", "tol", "churn", "network")]
    inc = mechanism_table(gt, [list(gt)], [item("RET-01", "5001")])
    cand = mechanism_table(
        gt, [list(gt)], [item("RET-07", "5002", family="thai_linguistic")]
    )
    with pytest.raises(ReportError) as exc:
        render(arm("incumbent"), arm("candidate"),
               {"incumbent": inc, "candidate": cand}, [], [])
    assert "different mechanisms" in str(exc.value)


# --- against the real pack ----------------------------------------------------


@pytest.fixture(scope="module")
def real_pack():
    ts = load_testset(TESTSETS / "retention_v1.jsonl", app="retention")
    gt = load_csv(TESTSETS / "retention_v1.gt.csv")
    return ts, gt


def test_every_item_in_the_real_pack_has_ground_truth(real_pack):
    """mechanism_table raises on an item with no truth row, so this passing is the
    coverage check: 20 items, 22 rows, RET-16 carrying three of them."""
    ts, gt = real_pack
    rows = mechanism_table(gt, [list(gt), list(gt)], list(ts.items))

    assert len(gt) == 22
    assert sum(len(r.item_ids) for r in rows) == 20
    assert [r.mechanism for r in rows] == [
        "clear", "thai_linguistic", "tiebreak", "multislot", "escape",
    ]
    assert [len(r.item_ids) for r in rows] == [6, 6, 3, 2, 3]


def test_a_perfect_arm_passes_every_mechanism_of_the_real_pack(real_pack):
    """The floor: replaying ground truth as predictions must PASS everywhere. If this
    fails, the item-correctness rule disagrees with the pack's own grain."""
    ts, gt = real_pack
    rows = mechanism_table(gt, [list(gt), list(gt)], list(ts.items))
    assert {r.verdict for r in rows} == {"PASS"}


def test_a_real_pack_item_that_never_works_fails_its_mechanism(real_pack):
    """RET-10 is the speaker-attribution item: three save-shaped phrases are agent
    politeness while the customer asks for the porting code. A model that flattens the
    turns answers `save`, which is the failure the item predicts."""
    ts, gt = real_pack
    flattened = [
        rec("5010", "postpaid", "save", "other", phone="0810000010")
        if r.call_id == "5010"
        else r
        for r in gt
    ]
    rows = mechanism_table(gt, [flattened, flattened], list(ts.items))
    thai = next(r for r in rows if r.mechanism == "thai_linguistic")

    assert thai.verdict == "FAIL"
    assert thai.failing_items == ("RET-10",)
    assert "ย้ายค่าย" in thai.detail, "the item's own predicted failure should be quoted"
