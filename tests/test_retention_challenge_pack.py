"""Regression gates for the separate 50-call retention challenge pack.

The cumulative ``retention_v*`` packs are experiment assets and deliberately remain
under their existing discovery and prefix-chain tests.  This pack has a different
purpose and identity: longer, original multi-turn conversations named ``RTC-*``.  Its
own gates keep that separation visible while applying the same label contract.
"""

from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.testsets import load_testset, validate  # noqa: E402
from evalharness.labelspaces import RETENTION  # noqa: E402


FIXTURES = ROOT / "tests" / "fixtures" / "testsets"
TESTSET_PATH = FIXTURES / "retention_challenge_v1.jsonl"
GT_PATH = FIXTURES / "retention_challenge_v1.gt.csv"
MANIFEST_PATH = FIXTURES / "retention_challenge_v1.manifest.json"
FAMILIES = {
    "compound_history": 10,
    "negotiation_reversal": 10,
    "multi_product": 10,
    "interaction_noise": 10,
    "boundary_outcome": 10,
}


def _pack():
    return load_testset(TESTSET_PATH, app="retention")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _embedded_rows():
    rows = []
    for item in _pack().items:
        for ground_truth in item.gt:
            rows.append(
                {
                    "call_id": item.call_id,
                    "phone_number": item.phone_number,
                    "product": str(ground_truth["product"]).lower(),
                    "call_result": ground_truth["call_result"],
                    "main": ground_truth["main"] or "",
                    "secondary": ground_truth["secondary"] or "",
                    "third": ground_truth["third"] or "",
                }
            )
    return rows


def test_challenge_pack_satisfies_the_repository_label_contract():
    assert validate(_pack()) == []


def test_challenge_pack_has_the_declared_identity_and_safe_synthetic_keys():
    items = _pack().items
    assert len(items) == 50
    assert [item.item_id for item in items] == [f"RTC-{n:03d}" for n in range(1, 51)]
    assert [item.call_id for item in items] == [str(n) for n in range(5201, 5251)]
    assert [item.phone_number for item in items] == [
        f"0810000{n:03d}" for n in range(201, 251)
    ]
    assert Counter(item.family for item in items) == FAMILIES
    assert len({(item.call_id, item.phone_number) for item in items}) == 50

    frozen_ids = {
        item.item_id
        for path in FIXTURES.glob("retention_v*.jsonl")
        for item in load_testset(path, app="retention").items
    }
    assert not frozen_ids.intersection(item.item_id for item in items)


def test_every_call_is_a_substantial_bounded_customer_agent_conversation():
    for item in _pack().items:
        turns = item.transcript_th.splitlines()
        assert len(turns) == 18, item.item_id
        assert len(item.transcript_th) >= 1000, item.item_id
        assert sum(turn.startswith("เจ้าหน้าที่:") for turn in turns) == 9, item.item_id
        assert sum(turn.startswith("ลูกค้า:") for turn in turns) == 9, item.item_id
        assert max(map(len, turns)) <= 120, item.item_id
        assert len(item.mechanism) >= 40, item.item_id
        assert len(item.why_it_matters) >= 40, item.item_id
        assert len(item.expected_failure) >= 40, item.item_id


def test_evidence_is_unique_and_reason_evidence_is_customer_speech():
    for item in _pack().items:
        customer_turns = [
            turn for turn in item.transcript_th.splitlines() if turn.startswith("ลูกค้า:")
        ]
        agent_turns = [
            turn
            for turn in item.transcript_th.splitlines()
            if turn.startswith("เจ้าหน้าที่:")
        ]
        for key, span in item.evidence.items():
            assert item.transcript_th.count(span) == 1, (item.item_id, key, span)
            if key.startswith("ev_reason:"):
                assert any(span in turn for turn in customer_turns), (item.item_id, key)
                assert not any(span in turn for turn in agent_turns), (item.item_id, key)


def test_csv_is_an_exact_independent_flattening_of_embedded_ground_truth():
    with GT_PATH.open(encoding="utf-8", newline="") as handle:
        csv_rows = list(csv.DictReader(handle))

    assert list(csv_rows[0]) == [
        "call_id",
        "phone_number",
        "product",
        "call_result",
        "main",
        "secondary",
        "third",
    ]
    assert csv_rows == _embedded_rows()
    assert len(csv_rows) == 64


def test_complexity_and_label_coverage_budgets_are_met():
    items = _pack().items
    assert sum(len(item.gt) >= 2 for item in items) >= 10
    assert sum(len(item.gt) >= 3 for item in items) >= 3

    products = Counter()
    outcomes = Counter()
    reason_items: dict[str, set[str]] = defaultdict(set)
    for item in items:
        for row in item.gt:
            products[str(row["product"]).lower()] += 1
            outcomes[str(row["call_result"]).lower()] += 1
            for column in ("main", "secondary", "third"):
                for reason in str(row[column] or "").split(","):
                    reason = reason.strip().lower()
                    if reason:
                        reason_items[reason].add(item.item_id)

    assert set(products) == set(RETENTION.product)
    assert set(outcomes) == set(RETENTION.call_result)
    assert set(reason_items) == set(RETENTION.reason)
    assert min(map(len, reason_items.values())) >= 3


def test_manifest_hashes_counts_and_limits_match_the_committed_assets():
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    assert manifest["dataset_id"] == "retention_challenge_v1"
    assert manifest["testset"] == {
        "path": TESTSET_PATH.name,
        "sha256": _sha256(TESTSET_PATH),
        "items": 50,
        "turns_per_item": 18,
    }
    assert manifest["ground_truth"] == {
        "path": GT_PATH.name,
        "sha256": _sha256(GT_PATH),
        "scored_rows": 64,
        "grain": "one row per (call_id, phone_number, product)",
    }
    assert manifest["families"] == FAMILIES
    assert len(manifest["research_sources"]) == 7
    assert any("native-speaker" in limit for limit in manifest["known_limits"])
