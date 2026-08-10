"""Gates on the tune/holdout split, including the generator test the plan calls for.

The committed `retention_v3.split.json` is data a run will be pointed at, so it needs the
same treatment as any other committed expectation: something must prove it is what the
documented rule produces, and something must prove the rule's invariants actually hold on
it. Both are here, and the leakage check is the one that matters -- a `long_context`
dilation separated from its base puts the same transcript on both sides of the split, and
nothing downstream would notice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.splits import (  # noqa: E402
    DILATION_PATTERN,
    SplitError,
    dilation_blocks,
    draw_split,
    split_problems,
)
from evalgen.testsets import load_testset, testset_sha  # noqa: E402

PACK = ROOT / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl"
SPLIT = ROOT / "tests" / "fixtures" / "testsets" / "retention_v3.split.json"


@pytest.fixture(scope="module")
def items():
    return load_testset(PACK, app="retention").items


@pytest.fixture(scope="module")
def committed():
    return json.loads(SPLIT.read_text(encoding="utf-8"))


def test_the_committed_split_is_what_the_documented_rule_produces(items, committed):
    """The generator test. If the rule changes, this fails and the committed list has to
    be redrawn deliberately rather than drifting."""
    drawn = draw_split(items)
    assert drawn["tune"] == committed["tune"]
    assert drawn["holdout"] == committed["holdout"]


def test_the_committed_split_names_the_pack_it_was_drawn_from(items, committed):
    """A split whose pack moved underneath it is a split of something else."""
    assert committed["testset_sha256"] == testset_sha(PACK)
    assert committed["counts"]["items"] == len(items)


def test_the_committed_split_holds_every_invariant(items, committed):
    assert split_problems(committed, items) == []


def test_tune_and_holdout_are_disjoint_and_cover_the_whole_pack(items, committed):
    tune, holdout = set(committed["tune"]), set(committed["holdout"])
    assert tune & holdout == set()
    assert tune | holdout == {item.item_id for item in items}
    assert len(tune) + len(holdout) == len(items)


def test_no_dilation_is_ever_separated_from_its_base(items, committed):
    """The leakage rule, checked directly on the committed lists rather than only through
    `split_problems`. RET-101..112 are the same six transcripts at 3x and 10x."""
    blocks = dilation_blocks(items)
    tune = set(committed["tune"])
    multi = {members for members in blocks.values() if len(members) > 1}
    assert len(multi) == 6, "the pack has six dilation blocks"
    for members in multi:
        sides = {member in tune for member in members}
        assert len(sides) == 1, f"{members} straddles the split"


def test_every_family_is_represented_on_both_sides(items, committed):
    """Stratification, checked as an outcome rather than assumed from the algorithm."""
    family = {item.item_id: item.family for item in items}
    tune_families = {family[i] for i in committed["tune"]}
    holdout_families = {family[i] for i in committed["holdout"]}
    all_families = set(family.values())
    assert tune_families == all_families
    assert holdout_families == all_families


def test_the_tune_slice_is_roughly_a_third_of_every_family(items, committed):
    family = {item.item_id: item.family for item in items}
    for name, counts in committed["counts"]["by_family"].items():
        total = counts["tune"] + counts["holdout"]
        assert total == sum(1 for i in family.values() if i == name)
        share = counts["tune"] / total
        assert 0.25 <= share <= 0.45, f"{name} is {share:.0%} tune, outside the band"


def test_the_committed_split_carries_its_contamination_warning(committed):
    """The figure this holdout produces is an upper bound, and the document that defines
    the holdout is where that has to be written down -- not only in a report nobody
    re-reads."""
    text = committed["contamination"].upper()
    assert "UPPER BOUND" in text
    assert "NOT CLEAN" in text
    assert "expected_failure" in committed["contamination"]


# ------------------------------------------------------------------ the drawing rules


def test_the_dilation_convention_actually_matches_the_pack(items):
    """If the `mechanism` convention ever stops matching, every block silently becomes a
    singleton and the leakage rule protects nothing. Assert it still fires."""
    matched = [item for item in items if DILATION_PATTERN.match(item.mechanism or "")]
    assert len(matched) == 12, "twelve dilations: six bases at 3x and 10x"
    assert all(item.family == "long_context" for item in matched)


def test_a_dilation_whose_base_is_absent_is_refused(items):
    """Silently dropping it would produce a block of one and reopen the leak."""
    from dataclasses import replace

    orphaned = [
        replace(items[0], item_id="RET-900", mechanism="dilation of RET-999 at 3x: x"),
        *items[1:],
    ]
    with pytest.raises(SplitError, match="not in this pack"):
        dilation_blocks(orphaned)


def test_a_tune_every_that_leaves_no_holdout_is_refused(items):
    for bad in (1, 0, -1):
        with pytest.raises(SplitError, match="not a holdout"):
            draw_split(items, tune_every=bad)


def test_the_draw_is_deterministic(items):
    assert draw_split(items) == draw_split(items)


def test_split_problems_detects_a_straddled_block(items):
    """The check must actually be able to fail, so hand it a split that violates it."""
    blocks = dilation_blocks(items)
    block = next(members for members in blocks.values() if len(members) > 1)
    all_ids = [item.item_id for item in items]
    tune = [block[0]]
    holdout = [i for i in all_ids if i not in tune]
    problems = split_problems({"tune": tune, "holdout": holdout}, items)
    assert any("straddle" in p or "split across" in p for p in problems), problems


def test_split_problems_detects_overlap_and_missing_items(items):
    all_ids = [item.item_id for item in items]
    both = split_problems({"tune": all_ids, "holdout": all_ids}, items)
    assert any("overlap" in p for p in both)
    partial = split_problems({"tune": all_ids[:5], "holdout": all_ids[5:10]}, items)
    assert any("neither slice" in p for p in partial)
    unknown = split_problems({"tune": ["RET-999"], "holdout": all_ids}, items)
    assert any("not in the pack" in p for p in unknown)
