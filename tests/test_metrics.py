"""Assert the clean-room scorers against hand-computed expectations.

The expectations in retention_expected.csv were written BEFORE this code existed,
from the working in WORKED-COMPUTATION.md. When a test here fails, the first
question is whether the FIXTURE is wrong, not the code. Editing an expectation to
match the output destroys the entire value of the pack.
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

FIX = ROOT / "tests" / "fixtures"
TOL = 5e-4  # expectations are recorded to 4 decimal places


def _expected() -> dict[tuple[str, str], dict]:
    rows = {}
    with open(FIX / "retention_expected.csv", newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            rows[(r["dimension"], r["label"])] = r
    return rows


@pytest.fixture(scope="module")
def gt():
    return load_csv(FIX / "retention_gt.csv")


@pytest.fixture(scope="module")
def incumbent():
    return load_csv(FIX / "retention_arm_incumbent.csv")


@pytest.fixture(scope="module")
def results(gt, incumbent):
    return {
        "call_result": score_call_result(gt, incumbent, RETENTION.call_result),
        "reason": score_reason(gt, incumbent, RETENTION.reason),
        "product": score_product(gt, incumbent, RETENTION.product),
    }


# --- the three denominators, which is the thing most likely to be got wrong ----

@pytest.mark.parametrize(
    "dimension,expected_denominator",
    [("call_result", 10), ("reason", 11), ("product", 9)],
)
def test_denominators_differ(results, dimension, expected_denominator):
    """Same input, three denominators. See WORKED-COMPUTATION.md summary table."""
    assert results[dimension].denominator == expected_denominator


# --- exact integer confusion counts, per class --------------------------------

def _cases():
    for (dim, label) in _expected():
        if label != "weighted_avg":
            yield dim, label


@pytest.mark.parametrize("dimension,label", list(_cases()))
def test_class_counts(results, dimension, label):
    exp = _expected()[(dimension, label)]
    got = next(c for c in results[dimension].classes if c.label == label)
    assert (got.tp, got.fp, got.fn, got.tn) == (
        int(exp["tp"]), int(exp["fp"]), int(exp["fn"]), int(exp["tn"])
    ), f"{dimension}/{label}: counts disagree with the hand computation"
    assert got.weight == int(exp["weight"])


@pytest.mark.parametrize("dimension,label", list(_cases()))
def test_class_rates(results, dimension, label):
    exp = _expected()[(dimension, label)]
    got = next(c for c in results[dimension].classes if c.label == label)
    for attr, col in (("accuracy", "accuracy"), ("precision", "precision"),
                      ("recall", "recall"), ("f1", "f1")):
        assert abs(getattr(got, attr) - float(exp[col])) < TOL, (
            f"{dimension}/{label}.{attr}: got {getattr(got, attr):.4f}, "
            f"expected {float(exp[col]):.4f}"
        )


# --- weighted averages --------------------------------------------------------

@pytest.mark.parametrize("dimension", ["call_result", "reason", "product"])
def test_weighted_average(results, dimension):
    exp = _expected()[(dimension, "weighted_avg")]
    res = results[dimension]
    tp, fp, fn, tn = res.totals()
    assert (tp, fp, fn, tn) == (
        int(exp["tp"]), int(exp["fp"]), int(exp["fn"]), int(exp["tn"])
    ), f"{dimension} totals disagree"
    assert sum(c.weight for c in res.classes) == int(exp["weight"])
    for attr, col in (("accuracy", "accuracy"), ("precision", "precision"),
                      ("recall", "recall"), ("f1", "f1")):
        assert abs(res.weighted(attr) - float(exp[col])) < TOL, (
            f"{dimension} weighted {attr}: got {res.weighted(attr):.4f}, "
            f"expected {float(exp[col]):.4f}"
        )


# --- the behaviours the fixture cases exist to pin -----------------------------

def test_c4_missing_prediction_scores_fn_not_dropped(results):
    """5004 has ground truth and no prediction. The outer merge must score it."""
    unknown = next(c for c in results["call_result"].classes if c.label == "unknown")
    assert unknown.fn == 1, "a missing prediction was silently dropped"


def test_c7_reason_rank_is_discarded(gt, incumbent):
    """GT 'network, save cost' vs pred 'save cost, network' must be equal."""
    g = next(r for r in gt if r.call_id == "5007")
    p = next(r for r in incumbent if r.call_id == "5007")
    assert g.reasons == p.reasons == frozenset({"network", "save cost"})


def test_c5_null_phone_drops_from_product_only(gt, incumbent, results):
    """The reproduced production defect: call 5005 vanishes from product,
    but is still scored in call_result and reason."""
    assert any(r.call_id == "5005" and r.phone is None for r in gt)
    tvs = next(c for c in results["product"].classes if c.label == "tvs")
    assert tvs.weight == 0, "5005 should have been dropped from the product frame"
    assert results["product"].denominator == 9
    # ...but it is still present in the other two dimensions
    assert results["call_result"].denominator == 10
    assert results["reason"].denominator == 11


def test_coverage_is_reported_on_every_dimension(results):
    for dim, res in results.items():
        assert res.coverage.items_requested > 0, dim
        assert res.coverage.items_scored > 0, dim
