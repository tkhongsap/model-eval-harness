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
    })
    candidate = arm("candidate", n_flip=4, dimensions={
        "call_result": score_call_result(gt, bad[0], RETENTION.call_result),
        "reason": score_reason(gt, bad[0], RETENTION.reason),
        "product": score_product(gt, bad[0], RETENTION.product),
    })

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
    models returned, instability -- and the quotable aggregate numbers LAST."""
    order = [
        "RECONCILED: NO",
        "1. MECHANISM TABLE",
        "2. PER-ITEM DISAGREEMENT",
        "3. WHAT THE MODELS ACTUALLY RETURNED",
        "4. N_flip",
        "5. AGGREGATE METRICS",
        "NOT OBSERVABLE BY THIS PACK",
    ]
    found = [report_text.index(marker) for marker in order]
    assert found == sorted(found), (
        "sections are out of order. A report that leads with an aggregate percentage at "
        f"n=22 has lost the argument in its first line. Got: {list(zip(order, found))}"
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
