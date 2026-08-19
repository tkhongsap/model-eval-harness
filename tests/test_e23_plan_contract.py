"""The E23 audio contract validator, watched firing in every direction it can fire.

WHY THIS FILE EXISTS. `verify.py` globs `experiments/*.plan.json` and runs
`experiment-check` on each match. Before this validator existed, `retention-e23` was
graded against the E5/E7 TEXT contract -- which expects `RET-*` item ids, three
OpenRouter arms and a provider qualification pass -- and reported 27 defects for a plan
that was entirely well formed. A gate that cries wolf on a good file is worse than no
gate: the next reader learns to skim past it.

The fix was a per-contract validator, not a relaxed one. So each test below breaks
exactly one preregistered decision and proves the validator names it. The three that
matter most cross-reference CODE rather than a restated constant -- alpha, the failure
taxonomy, and the budget arithmetic -- because those are the ones that can drift while
every literal in the plan still looks right.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evalgen.experiments import load_plan, validate_plan

ROOT = Path(__file__).resolve().parents[1]
PLAN = ROOT / "experiments" / "retention-e23.plan.json"


@pytest.fixture
def plan() -> dict:
    """A fresh deep copy, so a mutation in one test cannot leak into another."""
    return json.loads(json.dumps(load_plan(PLAN)))


def test_the_committed_plan_validates_as_it_stands(plan):
    """The baseline. If this fails, every refusal test below is meaningless."""
    assert validate_plan(plan, root=ROOT) == []


def test_alpha_is_checked_against_the_scorer_rather_than_a_restated_literal(plan):
    """The plan's alpha must equal `compare.exact_band`'s default.

    This is the check that survives a change nobody remembers to mirror. A plan still
    reading 0.015625 after the scorer moved to a looser alpha would widen every band in
    the experiment while looking untouched.
    """
    from evalharness.compare import exact_band

    assert plan["quality_gates"]["alpha_per_side"] == (
        exact_band.__kwdefaults__["alpha_per_side"]
    )

    plan["quality_gates"]["alpha_per_side"] = 0.05
    problems = validate_plan(plan, root=ROOT)
    assert any("alpha_per_side" in problem for problem in problems), problems


def test_the_failure_taxonomy_must_match_the_runners_own_literal(plan):
    """A preregistered taxonomy that has fallen behind the code is how a failure gets
    quietly reclassified into a friendlier bucket after it happens."""
    from evalgen.outcomes import Outcome

    assert plan["failure_categories"]["label_stage"] == list(Outcome.__args__)

    plan["failure_categories"]["label_stage"] = ["ok", "not_json"]
    problems = validate_plan(plan, root=ROOT)
    assert any("failure_categories.label_stage" in p for p in problems), problems


def test_the_declared_budget_must_match_its_own_components(plan):
    """The plan spells its arithmetic out in prose; this checks the prose against the
    numbers sitting beside it."""
    budget = plan["call_budget"]
    assert budget["total_logical_calls"] == (
        budget["asr_transcription"]
        + budget["asr_stability_probe"]
        + budget["label_calls_incumbent"]
        + budget["label_calls_candidate"]
    )

    plan["call_budget"]["total_logical_calls"] = 999
    problems = validate_plan(plan, root=ROOT)
    assert any("total_logical_calls" in problem for problem in problems), problems


def test_the_primary_metric_and_dimension_cannot_be_swapped_after_the_fact(plan):
    """E23 resolved on `call_result` alone; `reason` and `product` were secondary and
    both came back INDISTINGUISHABLE. Promoting a secondary dimension afterwards is
    exactly how a null result becomes a headline, so both are pinned."""
    for path, value in (
        (("metrics", "primary", "id"), "business_f1_reason"),
        (("quality_gates", "primary_dimension"), "reason"),
    ):
        mutated = copy.deepcopy(plan)
        target = mutated
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = value
        problems = validate_plan(mutated, root=ROOT)
        assert problems, f"changing {'.'.join(path)} was accepted"


def test_the_headline_replicate_is_pinned(plan):
    """Three replicates were run and the headline is replicate 1 alone. Without this,
    the point estimate could be chosen from whichever replicate read best."""
    plan["workload"]["point_estimate_replicate"] = 2
    problems = validate_plan(plan, root=ROOT)
    assert any("point_estimate_replicate" in p for p in problems), problems


def test_the_item_set_must_be_the_full_contiguous_138(plan):
    """Dropping items after seeing them is the oldest way to move a result. The full
    slice must be ASR-001..ASR-138 with nothing missing and nothing reordered."""
    plan["slices"]["full"]["item_ids"] = plan["slices"]["full"]["item_ids"][:100]
    problems = validate_plan(plan, root=ROOT)
    assert any("slices.full.item_ids" in problem for problem in problems), problems


def test_the_bands_must_keep_citing_their_production_source(plan):
    """The thresholds are production's own, copied verbatim. A band with no cited source
    is a band that can be moved to fit a result."""
    plan["success_thresholds"]["source_path"] = ""
    problems = validate_plan(plan, root=ROOT)
    assert any("source_path" in problem for problem in problems), problems


def test_reconciled_no_cannot_be_dropped(plan):
    """E23 is synthetic audio with generator-authored labels. It is a screening eval and
    the plan has to keep saying so."""
    plan["decision"]["reconciliation"] = "Fully reconciled against production."
    problems = validate_plan(plan, root=ROOT)
    assert any("reconciliation" in problem for problem in problems), problems


def test_asset_hashes_are_allowed_to_be_null_before_the_corpus_freeze(plan):
    """Deliberately NOT an error, and the reason is worth keeping.

    The plan's `sha256_stamping_policy` stamps each hash exactly once, from the rendered
    bytes, at corpus freeze. A validator demanding them earlier would force someone to
    invent a hash certifying bytes nobody has produced -- the precise failure the policy
    exists to prevent.
    """
    assets = plan["assets"]
    nulls = [
        name
        for name, value in assets.items()
        if isinstance(value, dict) and value.get("sha256") is None
    ]
    assert nulls, "expected at least one un-stamped asset in the committed draft"
    assert validate_plan(plan, root=ROOT) == []
