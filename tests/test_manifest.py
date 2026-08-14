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
    workload_sha,
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


# --- workload identity --------------------------------------------------------
#
# `workload_sha` answers "were these two arms given the same job?", which is only a
# meaningful question if the answer cannot depend on WHO did the job. The guard below is
# what enforces that, and until 2026-08-12 nothing exercised it: `workload_sha` was called
# in exactly one place in `src/` and imported by no test, so the refusal could have been
# deleted and the suite would have stayed green.
#
# The failure it prevents is specific. If `model` were allowed into the contract, every
# arm would hash to a different workload by construction, `_refuse_incomparable` would fire
# on every honest comparison, and the natural fix under deadline is to stop comparing
# workload shas at all -- losing the check that two arms ran the same items, prompt and
# schema. A contract that silently absorbs arm identity is worse than no contract.

_WORKLOAD = {
    "app": "retention",
    "testset_sha": "a" * 64,
    "gt_sha": "b" * 64,
    "prompt_sha": "c" * 64,
    "schema_sha": "d" * 64,
    "repeats": 3,
    "application_contract_sha": "e" * 64,
}


def test_workload_sha_is_stable_and_key_order_independent():
    """The same job hashes the same however the dict was built."""
    reordered = dict(reversed(list(_WORKLOAD.items())))
    assert workload_sha(dict(_WORKLOAD)) == workload_sha(reordered)
    assert len(workload_sha(dict(_WORKLOAD))) == 64


def test_workload_sha_changes_when_the_job_changes():
    """Otherwise it would certify two different jobs as the same one."""
    other = dict(_WORKLOAD, repeats=1)
    assert workload_sha(dict(_WORKLOAD)) != workload_sha(other)


@pytest.mark.parametrize(
    "field", ["model", "model_id", "provider", "provider_requested", "arm"]
)
def test_workload_sha_refuses_every_arm_specific_field(field):
    """Each of the five is refused, and the message names which one.

    Parametrised rather than checked as a set so that removing one name from `forbidden`
    fails a test that says which name was removed.
    """
    with pytest.raises(ValueError, match="workload identity cannot contain"):
        workload_sha(dict(_WORKLOAD, **{field: "x"}))


def test_the_refusal_names_every_offending_field_not_just_the_first():
    """A caller passing two arm fields should not have to fix them one run at a time."""
    with pytest.raises(ValueError) as excinfo:
        workload_sha(dict(_WORKLOAD, model="m", provider="p"))
    message = str(excinfo.value)
    assert "'model'" in message and "'provider'" in message
