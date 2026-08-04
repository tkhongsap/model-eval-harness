"""The manifest gate: blocks on what must match, prints what may differ."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalharness.adapters.retention import load_csv  # noqa: E402
from evalharness.manifest import (  # noqa: E402
    Manifest,
    ManifestMismatch,
    assert_comparable,
    items_hash,
    provenance_banner,
)

FIX = ROOT / "tests" / "fixtures"


def _m(**over) -> Manifest:
    base = dict(
        items_sha="a" * 64, labels_sha="b" * 64, item_count=10, scorer_sha="abc1234",
        arm="incumbent", backend="vertex-batch", model_id="gemini-2.5-flash-002",
        output_mechanism="function_call", prompt_sha="c" * 64,
        generation_config={"temperature": 0.0, "thinkingBudget": 0},
    )
    base.update(over)
    return Manifest(**base)


# --- blocking fields ----------------------------------------------------------

@pytest.mark.parametrize(
    "field,value",
    [("items_sha", "z" * 64), ("labels_sha", "z" * 64),
     ("item_count", 9), ("scorer_sha", "deadbee")],
)
def test_blocking_field_mismatch_refuses(field, value):
    with pytest.raises(ManifestMismatch) as exc:
        assert_comparable(_m(), _m(**{field: value}))
    assert field in str(exc.value)


def test_identical_manifests_compare_cleanly():
    assert assert_comparable(_m(), _m(arm="candidate")) == []


# --- recorded fields: differ on purpose, must NOT block ------------------------

def test_backend_difference_is_reported_not_blocked():
    """The gate that a previous draft got wrong. vLLM has no thinkingBudget and no
    forced function calling, so cross-arm config equality is unsatisfiable. A gate
    that fires on every real run is a gate that gets bypassed."""
    deltas = assert_comparable(
        _m(arm="incumbent"),
        _m(arm="candidate", backend="vllm", model_id="qwen3.6-27b",
           output_mechanism="guided_json",
           generation_config={"temperature": 0.0, "top_p": 1.0}),
    )
    assert deltas, "a backend difference must be reported"
    joined = " ".join(deltas)
    for expected in ("backend", "model_id", "output_mechanism", "generation_config"):
        assert expected in joined


def test_prompt_difference_is_reported_not_blocked():
    deltas = assert_comparable(_m(), _m(arm="candidate", prompt_sha="d" * 64))
    assert any("prompt_sha" in d for d in deltas)


# --- provenance ---------------------------------------------------------------

def test_banner_says_not_reconciled_by_default():
    banner = provenance_banner(_m(), _m(arm="candidate"), [])
    assert "RECONCILED: NO" in banner
    assert "NOT A MIGRATION VERDICT" in banner


def test_banner_clears_only_when_both_arms_reconciled():
    assert "RECONCILED: NO" in provenance_banner(
        _m(reconciled=True), _m(arm="candidate", reconciled=False), []
    )
    banner = provenance_banner(
        _m(reconciled=True), _m(arm="candidate", reconciled=True), []
    )
    assert "RECONCILED: YES" in banner
    assert "NOT A MIGRATION VERDICT" not in banner


def test_banner_prints_the_deltas():
    banner = provenance_banner(_m(), _m(arm="candidate"), ["backend: a=x vs b=y"])
    assert "backend: a=x vs b=y" in banner


# --- item hashing -------------------------------------------------------------

def test_items_hash_is_order_independent():
    gt = load_csv(FIX / "retention_gt.csv")
    assert items_hash(gt) == items_hash(list(reversed(gt)))


def test_items_hash_detects_a_changed_item_set():
    gt = load_csv(FIX / "retention_gt.csv")
    assert items_hash(gt) != items_hash(gt[:-1])
