"""The replicate collapse policy, which decides what the headline number even is.

WHY THIS FILE EXISTS. `experiments/retention-e23.plan.json` preregistered, in its own
words: "Every headline figure is computed on replicate 1 alone. Replicates 2 and 3 feed
ONLY the stability / noise-floor metric. Preregistered because Experiment 2 showed a metric
crossing a decision band on a single draw from an arm that flips; choosing the replicate
after seeing three of them is choosing the answer."

`experiment23_score.py` shipped computing the MODAL vote over all three instead, and the
published E23 figures were produced that way. Nobody noticed because both are defensible
estimators -- which is precisely why a test, rather than a reading, is what catches it.

They were not interchangeable here. The arm disagrees with itself on 37 of 138 items and
the verdict cleared its band by exactly one call, so the two policies could have disagreed
about the headline. (Re-scored 2026-08-20: both say AHEAD. That is a fact about this run,
not a reason to stop pinning the policy.)

These tests pin three things: `first` really means replicate 1, instability is measured
over ALL replicates whatever is being scored, and an unknown policy is refused rather than
quietly treated as one of the two.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def _load():
    spec = importlib.util.spec_from_file_location(
        "e23score", REPO / "scripts" / "experiment23_score.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


E23 = _load()


def _run_dir(tmp_path: Path, records: list[dict]) -> Path:
    run = tmp_path / "run"
    run.mkdir()
    (run / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records),
        encoding="utf-8", newline="\n")
    return run


def _rec(replicate: int, outcome: str, *, arm: str = "arm-a", item: str = "ASR-001") -> dict:
    """One record whose only varying content is the call outcome.

    Carries BOTH the runner's raw `response` and the derived `fields`, on purpose.

    `collapse` derives `fields` from `response` only when `fields` is absent, and it does so
    by exec'ing `experiment21_pipeline_delta` -- which imports `httpx` at module scope
    because it calls models. `requirements.txt` pins production's scoring dependencies
    exactly and contains no HTTP client, so on CI that path raises ModuleNotFoundError.
    Supplying `fields` keeps these tests on the collapse policy, which is what they are
    about, instead of on the runner's import graph.

    The `response` is still the runner's real shape, with the real `retention_outcome` key,
    so the two stay honest about what a record looks like. The response -> fields derivation
    itself is covered by `tests/test_experiment23_score.py`.
    """
    return {
        "arm": arm, "item_id": item, "replicate": replicate, "status": "ok",
        "response": {"product": {"TOL": {"main": {"reason": "network", "keyword": "x"},
                                         "retention_outcome": outcome}}},
        "fields": {"products": ["TOL"], "TOL.outcome": outcome,
                   "TOL.reasons": ["network"]},
    }


def test_first_scores_replicate_one_and_ignores_the_other_two(tmp_path):
    """Replicate 1 says `save`; the majority says `churn`. `first` must say `save`.

    Written so the two policies cannot agree by accident: if the collapse silently used the
    mode, this asserts the opposite value and fails.
    """
    run = _run_dir(tmp_path, [_rec(0, "save"), _rec(1, "churn"), _rec(2, "churn")])

    collapsed, _unstable, _failures = E23.collapse(run, "first")
    fields = collapsed["arm-a"]["ASR-001"]
    assert "save" in json.dumps(fields), fields
    assert "churn" not in json.dumps(fields), fields


def test_modal_scores_the_majority(tmp_path):
    run = _run_dir(tmp_path, [_rec(0, "save"), _rec(1, "churn"), _rec(2, "churn")])

    collapsed, _unstable, _failures = E23.collapse(run, "modal")
    fields = collapsed["arm-a"]["ASR-001"]
    assert "churn" in json.dumps(fields), fields


def test_first_means_replicate_one_not_first_line_in_the_file(tmp_path):
    """The runner appends concurrently, so file order is not replicate order.

    With `--jobs 8` the writer interleaves arms and items behind a lock, so replicate 2 can
    physically precede replicate 1 in `results.jsonl`. Taking "the first record seen" would
    then sample an arbitrary replicate while reporting itself as replicate 1 -- a wrong
    number that is stable across reruns of the SCORER and moves when the RUN is repeated,
    which is the hardest kind to notice.
    """
    run = _run_dir(tmp_path, [_rec(2, "churn"), _rec(1, "churn"), _rec(0, "save")])

    collapsed, _unstable, _failures = E23.collapse(run, "first")
    assert "save" in json.dumps(collapsed["arm-a"]["ASR-001"])


def test_instability_is_measured_over_every_replicate_even_when_scoring_one(tmp_path):
    """Otherwise `first` would report every arm as perfectly stable by construction.

    Instability is the honest counterweight to a single-replicate headline: it is what tells
    the reader the arm answered three ways. Computing it over the one replicate being scored
    would delete exactly the caveat the policy makes necessary.
    """
    run = _run_dir(tmp_path, [_rec(0, "save"), _rec(1, "churn"), _rec(2, "churn")])

    for policy in ("first", "modal"):
        _collapsed, unstable, _failures = E23.collapse(run, policy)
        assert unstable.get("arm-a") == 1, f"policy {policy} reported {unstable}"


def test_a_unanimous_item_is_not_counted_unstable(tmp_path):
    run = _run_dir(tmp_path, [_rec(0, "save"), _rec(1, "save"), _rec(2, "save")])
    for policy in ("first", "modal"):
        _collapsed, unstable, _failures = E23.collapse(run, policy)
        assert unstable.get("arm-a", 0) == 0


def test_an_unknown_policy_is_refused_rather_than_defaulted(tmp_path):
    """A typo must not silently select one of the two and publish under the other's name."""
    run = _run_dir(tmp_path, [_rec(0, "save")])
    with pytest.raises(E23.Refused) as excinfo:
        E23.collapse(run, "majority")
    assert "majority" in str(excinfo.value)


def test_failure_counts_cover_all_replicates_under_either_policy(tmp_path):
    """A parse failure in replicate 3 is still a failure the arm had.

    Counting failures only within the scored replicate would let an arm hide two thirds of
    its failures behind a single-replicate headline.
    """
    records = [_rec(0, "save"),
               {"arm": "arm-a", "item_id": "ASR-001", "replicate": 1,
                "status": "parse_failed"},
               {"arm": "arm-a", "item_id": "ASR-001", "replicate": 2,
                "status": "parse_failed"}]
    run = _run_dir(tmp_path, records)

    for policy in ("first", "modal"):
        _collapsed, _unstable, failures = E23.collapse(run, policy)
        assert failures["arm-a"]["parse_failed"] == 2, failures
        assert failures["arm-a"]["ok"] == 1, failures
