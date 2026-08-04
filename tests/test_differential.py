"""Differential test: this harness vs True's REAL production scorer.

Runs the same fixtures through both implementations and asserts they agree, class
by class, integer by integer. The two implementations share nothing: production is
pandas, the harness is pure Python, and the expectations in retention_expected.csv
were hand-computed from a third reading. Agreement across all three is the
correctness claim.

Where they must NOT agree is equally load-bearing: the permitted deviations in the
goal contract are asserted as differences, so nobody can quietly "fix" the harness
into inheriting a defect.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402
from evalharness.metrics import (  # noqa: E402
    score_call_result,
    score_product,
    score_reason,
)
from tests import production_ref  # noqa: E402

FIX = ROOT / "tests" / "fixtures"

# The label column production uses per dimension (it is not "label").
LABEL_COL = {"call_result": "call_result", "reason": "reason", "product": "product"}


def _raw(name: str) -> list[dict]:
    with open(FIX / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def _require_production():
    try:
        production_ref.load_production_factchecker()
    except Exception as exc:  # pragma: no cover
        pytest.skip(f"production source unavailable: {exc}")


def _require_pandas_pin():
    pd = pytest.importorskip("pandas")
    if pd.__version__ != "2.3.3":  # pragma: no cover
        pytest.skip(
            f"pandas {pd.__version__} != production pin 2.3.3. Under 3.x the "
            "dtype=='object' guard in pre_process is False, normalisation never "
            "fires, and this test would compare against a broken production."
        )


@pytest.fixture(scope="module")
def both():
    _require_pandas_pin()
    _require_production()

    gt_raw, pred_raw = _raw("retention_gt.csv"), _raw("retention_arm_incumbent.csv")
    prod = production_ref.production_scores(gt_raw, pred_raw)

    gt, pred = load_csv(FIX / "retention_gt.csv"), load_csv(FIX / "retention_arm_incumbent.csv")
    mine = {
        "call_result": score_call_result(gt, pred, RETENTION.call_result),
        "reason": score_reason(gt, pred, RETENTION.reason),
        "product": score_product(gt, pred, RETENTION.product),
    }
    return dict(zip(("call_result", "reason", "product"), prod)), mine


def test_source_region_unchanged():
    """A SHA pin over fact_checker.py:685-1105. If True edits the scored region,
    fail loudly rather than silently comparing against something that moved."""
    _require_production()
    assert production_ref.region_sha256() == production_ref.PINNED_SHA256, (
        "production's scored region changed. Re-read the diff, re-derive the "
        "hand computation, and only then update PINNED_SHA256."
    )


@pytest.mark.parametrize("dimension", ["call_result", "reason", "product"])
def test_confusion_counts_agree(both, dimension):
    prod, mine = both
    df = prod[dimension]
    col = LABEL_COL[dimension]
    prod_rows = {
        str(r[col]): (int(r["TP"]), int(r["FP"]), int(r["FN"]), int(r["TN"]))
        for _, r in df.iterrows()
        if str(r[col]) != "weighted_avg"
    }
    for c in mine[dimension].classes:
        assert c.label in prod_rows, f"{dimension}: production has no class {c.label!r}"
        assert (c.tp, c.fp, c.fn, c.tn) == prod_rows[c.label], (
            f"{dimension}/{c.label}: harness {(c.tp, c.fp, c.fn, c.tn)} != "
            f"production {prod_rows[c.label]}"
        )


@pytest.mark.parametrize("dimension", ["call_result", "reason", "product"])
def test_weighted_average_agrees(both, dimension):
    prod, mine = both
    df = prod[dimension]
    col = LABEL_COL[dimension]
    row = df[df[col].astype(str) == "weighted_avg"].iloc[0]
    res = mine[dimension]
    assert abs(res.weighted("accuracy") - float(row["accuracy"])) < 1e-9
    assert abs(res.weighted("precision") - float(row["precision"])) < 1e-9
    assert abs(res.weighted("recall") - float(row["recall"])) < 1e-9
    assert abs(res.weighted("f1") - float(row["f1_score"])) < 1e-9


def test_production_really_does_inflate_the_empty_arm():
    """Permitted deviation 1, proven against the real code rather than asserted.

    An all-empty prediction set scores high accuracy with ZERO recall, because true
    negatives dominate: every class the arm never predicted is scored correct on
    every row where the ground truth did not mention it either.

    Measured here: accuracy 0.8246, recall 0.0000, a gap of 0.82.

    **The absolute number is distribution-dependent and must not be quoted as a
    constant.** It rises with the proportion of single-label items and the number
    of never-occurring classes; on a 100-item single-label set it reaches ~0.909,
    which clears both the `acceptable: 85` and `good: 90` bands in
    retention.yml:76-79. On this 10-row fixture it lands at 0.82, which does not.
    Whether the defect clears a given gate depends on the data. That it makes
    accuracy blind to total failure does not.

    So the assertion is on the RELATIONSHIP, not on a threshold: an arm that got
    literally everything wrong must not be distinguishable from a good one by
    accuracy alone.
    """
    _require_pandas_pin()
    _require_production()
    prod = production_ref.production_scores(_raw("retention_gt.csv"), _raw("retention_arm_empty.csv"))
    reason = prod[1]
    row = reason[reason["reason"].astype(str) == "weighted_avg"].iloc[0]
    accuracy, recall = float(row["accuracy"]), float(row["recall"])

    assert recall == 0.0, f"an empty arm must have zero recall, got {recall}"
    assert accuracy - recall > 0.75, (
        f"expected accuracy to be blind to total failure: accuracy {accuracy:.4f} "
        f"vs recall {recall:.4f}. This gap IS the defect."
    )
    assert int(row["TP"]) == 0 and int(row["FP"]) == 0, (
        "an empty arm predicts nothing, so it can have neither true nor false positives"
    )


def test_harness_coverage_exposes_the_empty_arm():
    """The other half of deviation 1: the harness must make the failure visible.

    Production reports a number and says nothing about the 9 unparseable items.
    The harness counts them, so a reader can see the arm produced no usable output.
    """
    gt = load_csv(FIX / "retention_gt.csv")
    empty = load_csv(FIX / "retention_arm_empty.csv")
    res = score_reason(gt, empty, RETENTION.reason)
    assert res.coverage.parse_failures == 9, (
        "every row of the empty arm is a parse failure and must be counted"
    )
    assert all(c.tp == 0 for c in res.classes), "an empty arm has no true positives"
    assert res.weighted("recall") == 0.0
