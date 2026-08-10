"""Gates on `evalgen.stability`, starting with the table computed before the module existed.

`tests/fixtures/STABILITY-HAND-COMPUTED.md` fixes the decision table, six constructed
cases, three real items classified by reading their payloads by eye, and a seven-row
aggregate -- all before `stability.py` was written.

The claims this file checks, rather than trusts:

  * "the scored fingerprint comes from the scorer's own code" -- checked by permuting the
    reason slots and asserting the fingerprint does not move, which is the RET-04 property
    and the one a lookalike implementation gets wrong.
  * "identical text cannot parse two ways" -- checked by constructing that input and
    asserting it raises rather than being filed as a category.
  * "one replicate is not stable" -- checked, because calling it stable lets a
    single-replicate run report zero instability.
  * "diagnostic, never a gate" -- checked by parsing the AST of the verdict path.
"""

from __future__ import annotations

import ast
import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.stability import (  # noqa: E402
    UNSCORED_FIELDS,
    ItemStability,
    StabilityError,
    classify_item,
    decompose,
    raw_fingerprint,
    scored_fingerprint,
    unscored_fields_that_differ,
)
from evalgen.testsets import load_testset  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "STABILITY-HAND-COMPUTED.md"
PACK = ROOT / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl"


@pytest.fixture(scope="module")
def items():
    return {item.item_id: item for item in load_testset(PACK, app="retention").items}


def test_the_hand_computed_fixture_exists_and_carries_its_constraint(items):
    assert FIXTURE.exists()
    assert FIXTURE.stat().st_size > 2000
    text = FIXTURE.read_text(encoding="utf-8")
    # The honesty constraint is the reason this module is shaped as it is; if it is ever
    # dropped from the fixture, the module's docstring is quoting something that is gone.
    assert "CANNOT REPLACE" in text.upper()
    assert "RET-04" in text


def _payload(reasons=("save cost", "", ""), outcome="save", product="Postpaid",
             recommendation="rec", event="Emerging or Undefined Events", keyword="kw"):
    """One model response, shaped exactly like the committed run logs."""
    return {
        "product": {
            product: {
                "main": {"reason": reasons[0], "keyword": keyword},
                "secondary": {"reason": reasons[1], "keyword": ""},
                "third": {"reason": reasons[2], "keyword": ""},
                "retention_outcome": outcome,
            }
        },
        "call_event_detection": event,
        "recommendation": recommendation,
    }


def _rec(item_id, replicate, payload, *, raw=None, parse_ok=True):
    return {
        "item_id": item_id,
        "replicate": replicate,
        "payload": payload,
        "raw_content": raw if raw is not None else json.dumps(payload, ensure_ascii=False),
        "parse_ok": parse_ok,
    }


# ------------------------------------------------------- the constructed decision table


def test_c1_a_single_replicate_is_not_observable_and_is_never_called_stable(items):
    entry = classify_item([_rec("RET-03", 1, _payload())], items["RET-03"])
    assert entry.observable is False
    assert entry.raw_unstable is False
    assert entry.scored_unstable is False
    assert entry.cosmetic_only is False


def test_c2_identical_replicates_are_stable(items):
    payload = _payload()
    entry = classify_item(
        [_rec("RET-03", 1, payload), _rec("RET-03", 2, copy.deepcopy(payload))],
        items["RET-03"],
    )
    assert entry.observable is True
    assert entry.raw_unstable is False
    assert entry.scored_unstable is False


def test_c3_differing_text_with_an_identical_scored_fingerprint_is_cosmetic_only(items):
    entry = classify_item(
        [
            _rec("RET-03", 1, _payload(recommendation="one")),
            _rec("RET-03", 2, _payload(recommendation="a different sentence entirely")),
        ],
        items["RET-03"],
    )
    assert entry.raw_unstable is True
    assert entry.scored_unstable is False
    assert entry.cosmetic_only is True
    assert entry.unscored_changed == ("recommendation",)


def test_c4_a_changed_label_is_a_scored_change(items):
    entry = classify_item(
        [
            _rec("RET-03", 1, _payload(reasons=("save cost", "", ""))),
            _rec("RET-03", 2, _payload(reasons=("save cost", "", ""))),
            _rec("RET-03", 3, _payload(reasons=("network", "", ""))),
        ],
        items["RET-03"],
    )
    assert entry.raw_unstable is True
    assert entry.scored_unstable is True
    assert entry.cosmetic_only is False


def test_c5_a_replicate_that_failed_to_parse_is_a_scored_change_not_a_cosmetic_one(items):
    payload = _payload()
    entry = classify_item(
        [
            _rec("RET-03", 1, payload),
            _rec("RET-03", 2, copy.deepcopy(payload), raw="truncated{", parse_ok=False),
        ],
        items["RET-03"],
    )
    assert entry.raw_unstable is True
    assert entry.scored_unstable is True, "parse_ok is part of the fingerprint"
    assert entry.cosmetic_only is False


def test_c6_identical_text_that_scores_two_ways_raises_rather_than_being_filed(items):
    """A defect to surface, not a category to report: identical text cannot parse twice."""
    payload = _payload()
    other = _payload(reasons=("network", "", ""))
    same_raw = "identical bytes on both replicates"
    with pytest.raises(StabilityError, match="not deterministic"):
        classify_item(
            [
                _rec("RET-03", 1, payload, raw=same_raw),
                _rec("RET-03", 2, other, raw=same_raw),
            ],
            items["RET-03"],
        )


# --------------------------------------------------- the property a lookalike gets wrong


def test_the_scored_fingerprint_ignores_which_slot_a_label_landed_in(items):
    """The RET-04 property. `parse_reasons` unions main/secondary/third into a SET
    (fact_checker.py:873), so an implementation comparing slots reports instability the
    scorer does not have -- and reports it in the direction that makes an arm look worse."""
    item = items["RET-04"]
    main_only = _payload(reasons=("dissatisfied service", "", ""))
    all_three = _payload(
        reasons=("dissatisfied service", "dissatisfied service", "dissatisfied service")
    )
    assert scored_fingerprint(main_only, item, parse_ok=True) == scored_fingerprint(
        all_three, item, parse_ok=True
    )
    # and the raw text really does differ, so the test is not vacuous
    assert raw_fingerprint(json.dumps(main_only)) != raw_fingerprint(json.dumps(all_three))


def test_the_scored_fingerprint_ignores_reason_order_within_a_slot(items):
    item = items["RET-04"]
    a = _payload(reasons=("network, save cost", "", ""))
    b = _payload(reasons=("save cost, network", "", ""))
    assert scored_fingerprint(a, item, parse_ok=True) == scored_fingerprint(
        b, item, parse_ok=True
    )


def test_unscored_attribution_is_independent_of_the_classification(items):
    """A cosmetic item is defined by its scored fingerprint holding still, not by which
    unscored field moved -- so attribution must still fire on a scored-unstable item, or
    it becomes unfalsifiable."""
    entry = classify_item(
        [
            _rec("RET-03", 1, _payload(reasons=("save cost", "", ""), recommendation="a")),
            _rec("RET-03", 2, _payload(reasons=("network", "", ""), recommendation="b")),
        ],
        items["RET-03"],
    )
    assert entry.scored_unstable is True
    assert "recommendation" in entry.unscored_changed


def test_keyword_is_compared_separately_from_the_scored_parts_of_its_own_block():
    a = _payload(keyword="one phrase")
    b = _payload(keyword="a different phrase")
    assert unscored_fields_that_differ([a, b]) == ("keyword",)


def test_every_unscored_field_is_detected():
    base = _payload()
    for field, changed in (
        ("recommendation", _payload(recommendation="different")),
        ("call_event_detection", _payload(event="Campaign-Drvien Events")),
        ("keyword", _payload(keyword="different")),
    ):
        assert field in unscored_fields_that_differ([base, changed])
    assert set(UNSCORED_FIELDS) == {"recommendation", "call_event_detection", "keyword"}


# ------------------------------------------------------------- the aggregate arithmetic


def test_decompose_reproduces_the_hand_computed_seven_item_table(items):
    """Section 4 of the fixture, row for row."""
    item_id = "RET-03"
    item = items[item_id]
    records = []
    plan = [
        ("a1", False, False),
        ("a2", True, False),
        ("a3", True, True),
        ("a4", True, False),
        ("a5", False, False),
        ("a6", None, None),   # one replicate -> not observable
        ("a7", True, True),
    ]
    fake_items = {}
    for name, raw_unstable, scored_unstable in plan:
        fake_items[name] = item
        if raw_unstable is None:
            records.append(_rec(name, 1, _payload()))
            continue
        first = _payload()
        if scored_unstable:
            second = _payload(reasons=("network", "", ""))
        elif raw_unstable:
            second = _payload(recommendation="a different sentence")
        else:
            second = copy.deepcopy(first)
        records.append(_rec(name, 1, first))
        records.append(_rec(name, 2, second))

    summary = decompose(records, fake_items)
    assert summary["items"] == 7
    assert summary["observable"] == 6
    assert summary["not_observable"] == 1
    assert summary["raw_unstable"] == 4
    assert summary["scored_unstable"] == 2
    assert summary["cosmetic_only"] == 2
    assert summary["raw_unstable_rate"] == pytest.approx(4 / 6)
    assert summary["scored_unstable_rate"] == pytest.approx(2 / 6)
    assert summary["cosmetic_only_rate"] == pytest.approx(2 / 6)
    assert summary["cosmetic_share_of_instability"] == pytest.approx(0.5)
    assert summary["scored_unstable_items"] == ["a3", "a7"]


def test_rates_are_zero_not_nan_on_a_perfectly_stable_arm(items):
    payload = _payload()
    records = [
        _rec("RET-03", 1, payload),
        _rec("RET-03", 2, copy.deepcopy(payload)),
    ]
    summary = decompose(records, {"RET-03": items["RET-03"]})
    assert summary["raw_unstable"] == 0
    assert summary["cosmetic_share_of_instability"] == 0.0
    assert summary["raw_unstable_rate"] == 0.0


def test_decompose_refuses_a_run_log_it_cannot_match_to_the_testset(items):
    with pytest.raises(StabilityError, match="not in the testset"):
        decompose([_rec("RET-999", 1, _payload())], {"RET-03": items["RET-03"]})
    with pytest.raises(StabilityError, match="no item_id"):
        decompose([{"replicate": 1, "payload": _payload()}], items)


# --------------------------------------------------------------- the real, hand-read items


def _arm_records(directory):
    path = ROOT / "out" / "runs" / directory / "run.jsonl"
    if not path.is_file():
        pytest.skip(
            f"{path} is a gitignored local run artifact and is not on this machine; "
            "the hand-classified real items cannot be checked without it"
        )
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize(
    "item_id,expected_cosmetic,expected_unscored",
    [
        ("RET-03", True, ("recommendation",)),
        ("RET-02", False, ("recommendation", "keyword")),
        ("RET-04", True, ("recommendation", "call_event_detection", "keyword")),
    ],
)
def test_real_items_match_the_hand_read_classification(
    items, item_id, expected_cosmetic, expected_unscored
):
    """Section 3 of the fixture. These were classified by reading the payload fields out
    of the run log by eye, not by running this module."""
    records = [r for r in _arm_records("20260806-025645Z-v3-qwen27b") if r["item_id"] == item_id]
    assert len(records) == 3, "the fixture classified three replicates"
    entry = classify_item(records, items[item_id])
    assert entry.cosmetic_only is expected_cosmetic
    assert entry.scored_unstable is not expected_cosmetic
    assert entry.unscored_changed == expected_unscored


# --------------------------------------------------------------------------- isolation


def _imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


@pytest.mark.parametrize(
    "path",
    [
        ROOT / "src" / "evalgen" / "experiments.py",
        ROOT / "src" / "evalharness" / "compare.py",
        ROOT / "src" / "evalharness" / "manifest.py",
    ],
    ids=lambda p: p.name,
)
def test_the_stability_decomposition_is_never_imported_by_the_verdict_path(path):
    """The fixture's honesty constraint, enforced rather than asserted: this measure would
    flip a recorded verdict in a direction already known, so it may not become a gate."""
    assert path.exists()
    imported = _imports_of(path)
    assert not any(
        name == "evalgen.stability" or name.endswith(".stability") or name == "stability"
        for name in imported
    ), f"{path.name} imports the stability diagnostic"


def test_the_summary_carries_nothing_a_verdict_path_would_recognise(items):
    payload = _payload()
    summary = decompose(
        [_rec("RET-03", 1, payload), _rec("RET-03", 2, _payload(recommendation="x"))],
        {"RET-03": items["RET-03"]},
    )
    forbidden = {"verdict", "net", "band", "discordant", "decision", "passed", "winner"}
    assert not forbidden & set(summary)


def test_item_stability_reports_cosmetic_only_as_a_derived_property():
    entry = ItemStability(
        item_id="RET-01",
        replicates=3,
        observable=True,
        raw_unstable=True,
        scored_unstable=False,
        unscored_changed=("recommendation",),
    )
    assert entry.cosmetic_only is True
    assert ItemStability("x", 3, True, True, True, ()).cosmetic_only is False


# ------------------------------------------------------------------- the report section


def _arm_summary(name, n_flip):
    """The section reads exactly three attributes; a stub keeps this test from breaking
    every time `ArmSummary` gains a field it does not touch."""
    from types import SimpleNamespace

    return SimpleNamespace(arm=name, n_flip=n_flip, replicates=3)


def test_section_4b_is_absent_unless_a_decomposition_is_supplied():
    """`render` keeps working for callers that only have aggregates, so the diagnostic is
    additive rather than a new required input."""
    from evalgen.report import _flip_section

    lines = _flip_section([_arm_summary("inc", 0), _arm_summary("cand", 5)], None)
    assert not any("4b" in line for line in lines)


def test_section_4b_prints_beside_the_count_and_says_it_is_not_the_gate():
    from evalgen.report import _flip_section

    summary = {
        "observable": 138,
        "raw_unstable": 138,
        "scored_unstable": 31,
        "cosmetic_only": 107,
        "cosmetic_share_of_instability": 107 / 138,
        "unscored_fields_on_cosmetic_items": {"recommendation": 107, "keyword": 61},
    }
    lines = _flip_section(
        [_arm_summary("inc", 0), _arm_summary("cand", 138)], {"cand": summary}
    )
    text = "\n".join(lines)
    # the existing count is still there, above
    assert "N_flip" in text
    assert text.index("N_flip") < text.index("4b")
    assert "scored-unstable   31" in text
    assert "recommendation 107" in text
    # and the constraint is on the page, not only in a docstring
    assert "does not replace the stability gate" in text
    assert "already known" in text
    # never a verdict
    for word in ("PASS", "FAIL", "AHEAD", "BEHIND"):
        assert word not in text
