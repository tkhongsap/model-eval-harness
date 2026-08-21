"""The E24 contract, and the one copy error that would produce a consistent wrong answer.

WHY THIS FILE EXISTS. E24 is E23 re-run against a corrected corpus, so the two plans are
near-identical and sit next to each other in `experiments/`. The failure mode that creates is
specific and quiet: E24 scoring its corrected run against `asr-eval-v2/ground-truth/`, E23's
UNCORRECTED ground truth. Nothing would raise. Every arm would answer, the parse-valid gate
would pass, the scorecard would fill in, and every product row would be scored against the
labels the whole experiment exists to replace.

So the validator cross-checks `assets.corpus_root.path` against the text of
`metrics.primary.definition`, and these tests check that it actually refuses.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from evalgen.experiments import validate_plan

REPO = Path(__file__).resolve().parents[1]
E23 = json.loads((REPO / "experiments" / "retention-e23.plan.json").read_text(encoding="utf-8"))
E24 = json.loads((REPO / "experiments" / "retention-e24.plan.json").read_text(encoding="utf-8"))


def test_both_audio_plans_validate_clean():
    for plan in (E23, E24):
        problems = validate_plan(plan, root=REPO)
        assert problems == [], f"{plan['experiment_id']}: {problems}"


def test_an_unregistered_experiment_id_is_refused_not_passed():
    """A plan no checker claims must fail, never silently pass."""
    plan = copy.deepcopy(E24)
    plan["experiment_id"] = "retention-e99"
    problems = validate_plan(plan, root=REPO)
    assert problems, "an unregistered plan validated clean; the gate checks nothing"
    assert "no contract validator is registered" in problems[0]


def test_e24_scoring_against_e23s_corpus_is_refused():
    """THE dangerous copy error. Everything downstream of it looks healthy."""
    plan = copy.deepcopy(E24)
    plan["assets"]["corpus_root"]["path"] = "asr-eval-v2/"
    problems = validate_plan(plan, root=REPO)
    assert any("corpus_root" in p for p in problems), (
        "E24 pointed at E23's corpus and validated clean. That run would produce a "
        f"complete table of wrong product numbers. problems={problems}"
    )


def test_a_metric_definition_naming_the_wrong_corpus_is_refused():
    """The path can be right and the definition wrong; they are set in different places."""
    plan = copy.deepcopy(E24)
    plan["metrics"]["primary"]["definition"] = (
        plan["metrics"]["primary"]["definition"].replace("asr-eval-v3", "asr-eval-v2")
    )
    problems = validate_plan(plan, root=REPO)
    assert any("primary.definition" in p for p in problems), problems


def test_e24_must_say_what_it_supersedes_and_what_it_does_not_establish():
    """Two runs with different numbers and no stated reason to prefer one is not a record."""
    for field in ("relationship", "what_changed_in_the_corpus", "how_it_was_found",
                  "what_this_does_not_establish"):
        plan = copy.deepcopy(E24)
        del plan["supersedes"][field]
        problems = validate_plan(plan, root=REPO)
        assert any(field in p for p in problems), (
            f"supersedes.{field} could be dropped without failing validation"
        )


def test_e24_declares_the_five_arms_it_scores():
    plan = copy.deepcopy(E24)
    assert [a["id"] for a in plan["arms"]] == [
        "gemini-audio", "qwen-pipeline", "typhoon-pipeline", "ceiling", "format-control"]
    plan["arms"] = plan["arms"][:2]
    assert any("arms" in p for p in validate_plan(plan, root=REPO))


def test_e23s_arm_count_is_still_enforced_at_two():
    """Parameterising the validator must not have loosened E23's own contract."""
    plan = copy.deepcopy(E23)
    plan["arms"] = plan["arms"] + [{"id": "extra", "role": "control"}]
    problems = validate_plan(plan, root=REPO)
    assert any("arms" in p for p in problems), (
        "E23 accepted a third arm after the validator was shared with E24"
    )


@pytest.mark.parametrize("field,value", [
    ("quality_gates.alpha_per_side", 0.05),
    ("workload.point_estimate_replicate", 2),
    ("quality_gates.primary_dimension", "product"),
])
def test_the_decision_shaping_fields_are_still_pinned_for_e24(field, value):
    """The fields that would change the VERDICT if edited after a number existed."""
    plan = copy.deepcopy(E24)
    section, key = field.split(".")
    plan[section][key] = value
    assert any(field in p for p in validate_plan(plan, root=REPO)), (
        f"{field} could be changed to {value!r} without failing validation"
    )


def test_e24_did_not_move_productions_thresholds():
    """A corpus fix is not a reason for production's own bands to change.

    E24 was derived from E23 field by field precisely so this is checkable rather than
    promised.
    """
    assert E24["success_thresholds"] == E23["success_thresholds"]
    assert E24["quality_gates"]["alpha_per_side"] == E23["quality_gates"]["alpha_per_side"]
    assert E24["workload"] == E23["workload"]
    assert E24["failure_categories"] == E23["failure_categories"]


def test_e24_may_add_to_asr_config_but_never_change_what_e23_pinned():
    """Whole-block equality was the wrong assertion here, and it hid something true.

    E23 had no Typhoon arm, so `asr_config.chunk_seconds: 180` needed no scope -- it was
    simply the setting. E24 runs Typhoon, which ran at 30 s in E23's own 2026-08-20 run and
    runs at 30 s here. A plan stating one frozen number beside a two-ASR arm set reads as
    though both arms used it.

    Demanding the block be byte-identical would have kept this test green by leaving that
    unsaid, which is the wrong direction for a gate to push. So the pin is on the FIELDS E23
    pinned, and new keys are allowed.
    """
    for field in ("chunk_seconds", "frozen", "frozen_by", "freeze_rationale", "language",
                  "split_rule", "timeout_s", "transport_retries"):
        assert E24["asr_config"][field] == E23["asr_config"][field], (
            f"asr_config.{field} moved between E23 and E24"
        )
    assert E24["asr_config"]["chunk_seconds_by_arm"] == {
        "qwen-pipeline": 180, "typhoon-pipeline": 30}
    assert E24["asr_config"]["chunk_seconds"] == 180, (
        "the frozen Qwen setting must stay the top-level value, so a reader who checks only "
        "the field E22 froze still finds it"
    )


def test_e24_records_that_its_reviewers_were_models():
    """The audit's limit has to travel with the plan, not only with the report."""
    text = json.dumps(E24, ensure_ascii=False)
    assert "not human" in text or "models, not people" in text or "MODEL reviewers" in text, (
        "E24 does not state anywhere that the corrected labels were confirmed by model "
        "reviewers rather than a human panel"
    )
