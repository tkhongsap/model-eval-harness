"""Paired comparison, the 2x2 table, coverage refusal, and the PII guard."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.compare import (  # noqa: E402
    CoverageMismatch,
    check_coverage,
    disagreement,
    regressions,
)
from evalharness.keys import ForbiddenColumn, assert_shareable, item_key  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import score_call_result, score_reason  # noqa: E402

FIX = ROOT / "tests" / "fixtures"
KEY = "test-key-not-a-real-secret"


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
    scorable = sum(
        1 for g in gt if dimension != "call_result" or g.call_result is not None
    )
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
