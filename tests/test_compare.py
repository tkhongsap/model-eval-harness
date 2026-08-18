"""Paired comparison, the 2x2 table, coverage refusal, and the PII guard.

The last third of this file reaches into `evalgen.cli`, which the rest of it does not.
That is deliberate: `check_exact_coverage` spent its whole life defined, tested and
documented "New decision paths should call this helper" while no production code called
it, and a guard nobody calls is a comment with a test suite. The tests that matter now
are the ones that fail if the wiring is removed again, and those have to run the CLI.
The two `run_arm` fixtures come from `test_cli` rather than being rebuilt here, because
a second way of building a run directory is a second thing to keep in step.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen import cli  # noqa: E402
from evalgen.cli import EXIT_REFUSED, main  # noqa: E402
from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.compare import (  # noqa: E402
    CoverageMismatch,
    check_coverage,
    check_exact_coverage,
    disagreement,
    regressions,
)
from evalharness.keys import ForbiddenColumn, assert_shareable, item_key  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import (  # noqa: E402
    score_call_result,
    score_product,
    score_reason,
)
from evalharness.records import Record  # noqa: E402

# Fixtures, imported for pytest to resolve by name. `run_arm` needs `env` and `testset`
# in this module's namespace, and `perfect` needs `testset`, so all four are imported
# even though only `run_arm` and `perfect` are named in a signature below.
from tests.test_cli import (  # noqa: E402,F401
    answer,
    env,
    payload_for,
    perfect,
    run_arm,
    testset,
)

FIX = ROOT / "tests" / "fixtures"
KEY = "test-key-not-a-real-secret"

SCORERS = {
    "call_result": (score_call_result, RETENTION.call_result),
    "reason": (score_reason, RETENTION.reason),
    "product": (score_product, RETENTION.product),
}
DIMENSIONS = tuple(SCORERS)


@pytest.fixture(scope="module")
def arms():
    return (
        load_csv(FIX / "retention_gt.csv"),
        load_csv(FIX / "retention_arm_incumbent.csv"),
        load_csv(FIX / "retention_arm_candidate.csv"),
        load_csv(FIX / "retention_arm_empty.csv"),
    )


# --- the 2x2 must account for every item --------------------------------------

@pytest.mark.parametrize("dimension", ["call_result", "reason", "product"])
def test_disagreement_counts_sum_to_items(arms, dimension):
    gt, inc, cand, _ = arms
    d = disagreement(gt, inc, cand, dimension)
    # Hand-counted from the fixture.  The additional unit in every dimension is
    # an orphan prediction; excluding it is the false-positive blind spot this
    # table now closes. All three inference tables are clustered once per call;
    # product drops the null-phone call exactly as the production scorer does.
    scorable = {"call_result": 10, "reason": 10, "product": 9}[dimension]
    assert d.total == scorable, (
        f"{dimension}: 2x2 accounts for {d.total} items but {scorable} were scorable. "
        "An item that falls out of the table is an item nobody reviews."
    )


def test_disagreement_finds_both_directions(arms):
    """The fixture is built so each arm wins some items. A comparison that only
    ever shows one arm winning is usually a bug, not a result."""
    gt, inc, cand, _ = arms
    d = disagreement(gt, inc, cand, "call_result")
    assert d.incumbent_only_right > 0, "expected the incumbent to win at least one item"
    assert d.candidate_only_right > 0, "expected the candidate to win at least one item"
    assert d.net == d.candidate_only_right - d.incumbent_only_right


# --- coverage refusal ---------------------------------------------------------

def test_comparable_arms_pass_coverage(arms):
    gt, inc, cand, _ = arms
    check_coverage(
        score_call_result(gt, inc, RETENTION.call_result),
        score_call_result(gt, cand, RETENTION.call_result),
    )


def test_empty_arm_is_refused_not_scored(arms):
    """The guard that matters: an arm whose output was unparseable has a smaller,
    easier denominator, so any accuracy comparison would favour it."""
    gt, inc, _, empty = arms
    with pytest.raises(CoverageMismatch) as exc:
        check_coverage(
            score_reason(gt, inc, RETENTION.reason),
            score_reason(gt, empty, RETENTION.reason),
        )
    assert "coverage differs" in str(exc.value) or "zero items" in str(exc.value)


def test_exact_coverage_rejects_equal_counts_with_different_identities():
    gt = [Record("call-a", "one", "postpaid", "churn")]
    incumbent = [Record("call-a", "one", "postpaid", "churn")]
    candidate = [Record("call-b", "two", "postpaid", "churn")]

    with pytest.raises(CoverageMismatch, match="exact coverage"):
        check_exact_coverage(gt, incumbent, candidate, "reason")


def test_exact_coverage_accepts_the_same_identity_set_in_any_order():
    a = Record("call-a", "one", "postpaid", "churn")
    b = Record("call-b", "two", "tol", "unknown")

    check_exact_coverage([a, b], [b, a], [a, b], "reason")


def test_duplicate_prediction_keys_refuse_in_every_paired_path():
    gt = [Record("call-a", "one", "postpaid", "churn")]
    incumbent = list(gt)
    candidate = [
        Record("call-a", "one", "postpaid", "churn"),
        Record("call-a", "one", "postpaid", "save"),
    ]

    with pytest.raises(CoverageMismatch, match="duplicate"):
        disagreement(gt, incumbent, candidate, "call_result")
    with pytest.raises(CoverageMismatch, match="duplicate"):
        regressions(gt, incumbent, candidate, "call_result", KEY)
    with pytest.raises(CoverageMismatch, match="duplicate"):
        check_exact_coverage(gt, incumbent, candidate, "call_result")


def test_extra_product_and_reason_are_paired_regressions_not_invisible():
    expected = Record(
        "call-a", "one", "postpaid", "churn", frozenset({"network"})
    )
    extra = Record(
        "call-a", "one", "tol", "churn", frozenset({"other"})
    )
    gt = [expected]
    incumbent = [expected]
    candidate = [expected, extra]

    product = disagreement(gt, incumbent, candidate, "product")
    reason = disagreement(gt, incumbent, candidate, "reason")

    assert product == product.__class__("product", 0, 0, 1, 0)
    assert reason == reason.__class__("reason", 0, 0, 1, 0)
    product_rows = regressions(gt, incumbent, candidate, "product", KEY)
    reason_rows = regressions(gt, incumbent, candidate, "reason", KEY)
    assert len(product_rows) == len(reason_rows) == 1
    assert product_rows[0].candidate_label == "postpaid, tol"
    assert product_rows[0].error_type == "extra_output"
    assert reason_rows[0].gt_label == "<no ground truth>"
    assert reason_rows[0].error_type == "extra_output"


@pytest.mark.parametrize("dimension", ["call_result", "reason", "product"])
def test_parse_failure_never_receives_correctness_credit(dimension):
    gt = Record(
        "call-a", "one", "postpaid", "churn", frozenset({"network"})
    )
    failed = Record(
        "call-a",
        "one",
        "postpaid",
        "churn",
        frozenset({"network"}),
        parse_ok=False,
    )

    table = disagreement([gt], [gt], [failed], dimension)

    assert table.incumbent_only_right == 1
    assert table.candidate_only_right == 0
    row = regressions([gt], [gt], [failed], dimension, KEY)[0]
    assert row.error_type == "invalid_output"


# --- the regression list ------------------------------------------------------

def test_regression_list_carries_no_identifier(arms):
    gt, inc, cand, _ = arms
    rows = regressions(gt, inc, cand, "call_result", KEY)
    assert rows, "expected at least one regression in this fixture"
    for r in rows:
        assert "081" not in r.item_key, "a phone number leaked into the item key"
        assert r.item_key.isalnum() and len(r.item_key) == 16


def test_item_key_is_stable_and_distinct(arms):
    gt, _, _, _ = arms
    a, b = gt[0], gt[1]
    assert item_key(a, KEY) == item_key(a, KEY), "item key must be deterministic"
    assert item_key(a, KEY) != item_key(b, KEY), "distinct items must not collide"
    assert item_key(a, KEY) != item_key(a, "other-key"), "key must actually be used"


# --- the PII guard raises, it does not warn -----------------------------------

def test_shareable_writer_refuses_customer_columns():
    assert_shareable(["item_key", "dimension", "gt_label"])  # fine
    for bad in ("phone_number", "msisdn", "customer_name", "raw_output"):
        with pytest.raises(ForbiddenColumn):
            assert_shareable(["item_key", bad])


def test_shareable_guard_is_case_insensitive():
    with pytest.raises(ForbiddenColumn):
        assert_shareable(["item_key", "Phone_Number"])


# --- the strict gate, and the decision path that finally runs it ---------------
#
# `check_exact_coverage` was written, tested and documented as the helper "new decision
# paths should call" -- and then called by nothing. The only gate wired into `compare`
# was the loose one, whose 2% tolerance on 188 items is about four items that can be
# scored in one arm and not the other. The tests below are the ones that fail if that
# wiring is ever removed again.


def _calls(count: int) -> list[Record]:
    """`count` single-product calls, all scorable in every dimension.

    Built here rather than taken from `retention_gt.csv` because these tests are about
    HOW MANY items are present, and that fixture is deliberately ragged: it carries a
    null-phone call, a two-product call and a missing item, each of which is the point
    somewhere else and noise here. The phone values are not phone-shaped on purpose --
    the synthetic block is accounted for number by number in
    `tests/test_phone_block_allocation.py`, and a test that needs fifty placeholders
    has no business spending fifty numbers out of it.
    """
    return [
        Record(
            call_id=f"call-{index:03d}",
            phone=f"line-{index:03d}",
            product="postpaid",
            call_result="churn",
            reasons=frozenset({"network"}),
        )
        for index in range(count)
    ]


def test_the_loose_gate_cannot_see_vanished_items_and_the_strict_one_can():
    """The reason the strict gate had to be wired in, measured rather than argued.

    The loose gate's documented limit is a 2% count tolerance, and that is not its real
    limitation. It compares `Coverage.items_scored` between the arms, and every one of
    those counts is derived from the GROUND TRUTH: `score_call_result` counts gt rows
    whose label is present, `score_reason` counts the whole outer join, `score_product`
    unions the gt groups. Predictions that vanished from one arm therefore move it by
    nothing at all -- not by 2%, by nothing. Here 45 of the candidate's 50 items are
    gone, and the loose gate reads 50 against 50 and passes in all three dimensions.
    """
    gt = _calls(50)
    incumbent = list(gt)
    candidate = gt[:5]

    for dimension in DIMENSIONS:
        scorer, classes = SCORERS[dimension]
        inc_result = scorer(gt, incumbent, classes)
        cand_result = scorer(gt, candidate, classes)

        assert inc_result.coverage.items_scored == 50
        assert cand_result.coverage.items_scored == 50
        check_coverage(inc_result, cand_result)  # the loose gate sees nothing

        with pytest.raises(CoverageMismatch, match="45 missing"):
            check_exact_coverage(gt, incumbent, candidate, dimension)


def test_the_strict_gate_does_not_refuse_a_product_named_wrong_in_a_scored_call():
    """The grain that makes the strict gate usable instead of merely present.

    `Record.key` carries the product, so at merge grain a candidate that answers TOL
    where the truth says Postpaid looks like one missing identity plus one extra one --
    identical, to a coverage check, to an item that vanished. It is not a coverage
    fact. It is the product dimension's finding, and it is scored and printed as one.
    Pooled product accuracy over packs A+B is 181/188 and 180/188, so roughly seven
    calls per arm do this, and a merge-grain gate would have refused to produce the
    Experiment 7 comparison at all rather than reporting those seven calls.
    """
    gt = [Record("call-a", "line-a", "postpaid", "churn", frozenset({"network"}))]
    incumbent = list(gt)
    candidate = [Record("call-a", "line-a", "tol", "churn", frozenset({"network"}))]

    for dimension in DIMENSIONS:
        check_exact_coverage(gt, incumbent, candidate, dimension)

    # And the mis-named product is still a finding, not something the gate swallowed.
    table = disagreement(gt, incumbent, candidate, "product")
    assert (table.incumbent_only_right, table.candidate_only_right) == (1, 0)
    row = regressions(gt, incumbent, candidate, "product", KEY)[0]
    assert row.error_type == "extra_output"


def test_compare_defaults_to_the_strict_gate():
    """Defaulted to strict, with loose reachable. A gate that must be asked for is off."""
    parser = cli.build_parser()
    default = parser.parse_args(["compare", "--incumbent", "a", "--candidate", "b"])
    chosen = parser.parse_args(
        ["compare", "--incumbent", "a", "--candidate", "b", "--coverage", "loose"]
    )

    assert default.coverage == "strict"
    assert chosen.coverage == "loose"
    assert set(cli.COVERAGE_GATES) == {"strict", "loose"}


@pytest.fixture
def candidate_loses_a_call(monkeypatch):
    """Delete one whole call from the CANDIDATE arm between flatten and scoring.

    No run reaches this state on its own, which is exactly why it has to be staged:
    `_require_run_matrix` refuses a run missing an item long before the scorer sees it,
    and `flatten.to_rows` emits a ground-truth skeleton for every failure. The coverage
    gate is the tripwire for the day one of those two guarantees stops holding, and a
    tripwire nobody has watched fire is a comment.

    `cmd_compare` builds `per_replicate` from a single dict comprehension over
    `(incumbent, candidate)`, so the second call belongs to the candidate. The tests
    assert the call count, so a reordering fails there rather than quietly staging the
    shortfall in both arms and proving something weaker than it claims.
    """
    real = cli.replicate_records
    calls: list[str] = []

    def fake(result, items, *, bound):
        per_replicate = real(result, items, bound=bound)
        calls.append("called")
        if len(calls) != 2:
            return per_replicate
        doomed = per_replicate[0][0].call_key
        return [
            [row for row in rows if row.call_key != doomed] for rows in per_replicate
        ]

    monkeypatch.setattr(cli, "replicate_records", fake)
    return calls


def test_the_decision_path_refuses_a_coverage_shortfall(
    run_arm, perfect, candidate_loses_a_call, capsys
):
    """The wiring, end to end. Without it this run prints a full, plausible report."""
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    captured = capsys.readouterr()

    assert len(candidate_loses_a_call) == 2, "one call per arm, the candidate second"
    assert code == EXIT_REFUSED
    assert "were not scored over the same items" in captured.err
    assert "1 missing" in captured.err
    assert "--coverage loose" in captured.err, "a refusal must say what the way out is"
    assert "PAIRED DISAGREEMENT" not in captured.out, (
        "the refusal comes instead of the report, not beside it"
    )


def test_the_exploratory_gate_still_runs_and_the_report_says_it_is_exploratory(
    run_arm, perfect, candidate_loses_a_call, capsys
):
    """Exploratory runs over partial data are legitimate, and stay possible.

    What they may not do is look like a decision-grade run, so the escape hatch labels
    itself in the same footer the report is read from.
    """
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    code = main([
        "compare", "--incumbent", str(incumbent), "--candidate", str(candidate),
        "--coverage", "loose",
    ])
    report = capsys.readouterr().out

    assert len(candidate_loses_a_call) == 2
    assert code != EXIT_REFUSED, "loose is the documented way to score partial data"
    assert "PAIRED DISAGREEMENT" in report, "the report was produced"
    assert "COVERAGE GATE: loose" in report
    assert "EXPLORATORY" in report
    assert "must not be quoted in a decision" in report


def test_the_report_names_the_gate_that_ran(run_arm, perfect, capsys):
    """Which gate ran is a property of every run, so it is printed on every run.

    A footer that named the gate only when it was the loose one would be a footer whose
    silence the reader has to interpret, and the six retracted numbers in
    `docs/harness-tightening-plan.md` were every one of them a reader interpreting.
    """
    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", perfect, repeats=1)
    capsys.readouterr()

    main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    report = capsys.readouterr().out

    assert "COVERAGE GATE: strict (compare.check_exact_coverage)" in report
    assert "the gate a decision may be read off" in report
    assert "EXPLORATORY" not in report


def test_an_invented_product_warns_loosely_and_is_not_refused_strictly(
    run_arm, perfect, testset, capsys
):
    """Both gates on the one behaviour every committed comparison report has shown.

    An arm that names a product the ground truth does not is the only thing the loose
    gate CAN see: an orphan merge key grows `score_reason`'s outer join, so the counts
    diverge. It is also the thing the strict gate must not refuse. `flatten.to_rows`
    takes the call id from the testset item and never from the payload, so the call
    population is untouched, and the invented product is already scored and printed as
    a false positive.

    Get this wrong -- gate at merge grain -- and the report does not appear at all. The
    two arms below differ on two of twenty items, and the pooled packs show real models
    doing it on roughly seven calls in 188.
    """
    by_item = {item.item_id: item for item in testset.items}

    def invents_a_product(item_id, _nth):
        item = by_item[item_id]
        payload = payload_for(item)
        if item_id in {"RET-01", "RET-02"}:
            payload["product"]["TVS"] = {
                "main": {"reason": "other", "keyword": ""},
                "secondary": {"reason": "", "keyword": ""},
                "third": {"reason": "", "keyword": ""},
                "retention_outcome": "churn",
            }
        return answer(item, payload=payload)

    incumbent = run_arm("incumbent", perfect, repeats=1)
    candidate = run_arm("candidate", invents_a_product, repeats=1)
    capsys.readouterr()

    code = main(["compare", "--incumbent", str(incumbent), "--candidate", str(candidate)])
    report = capsys.readouterr().out

    assert code != EXIT_REFUSED, (
        "an invented product is a finding about the model, not a reason to refuse to "
        "compare the two arms"
    )
    assert "COVERAGE GATE: strict" in report, "the strict gate ran and passed"
    assert "LOOSE COVERAGE GATE, NOT BLOCKING" in report, (
        "the loose gate still runs under strict, and its heading says nothing was "
        "refused -- a full report printed under the word REFUSALS is the publish-"
        "boundary defect this harness keeps retracting numbers for"
    )
    assert "reason: coverage differs by" in report
