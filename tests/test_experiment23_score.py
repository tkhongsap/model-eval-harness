"""The E23 scorer, checked against a fixture whose answer is known by hand.

This follows the rule the project already lives by for `retention_expected.csv`: work the
arithmetic out on paper FIRST, then check the code against it. A scorer verified only against
its own output is the code checking itself.

The fixture is six calls with deliberate, hand-chosen outcomes, so every count below can be
recomputed by reading the table in `_TRUTH` and `_ARMS`. It exercises the four things most
likely to be wrong in a translation layer of this kind:

  * a parse failure, which must score as WRONG and never be dropped
  * replicate disagreement, which must collapse by mode rather than average
  * a call where the arms differ, so the paired table has something in it
  * a call where an arm invents an extra product, which is a product-dimension miss
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "e23score", REPO / "scripts" / "experiment23_score.py"
    )
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


e23 = _load()


# call_id, product, call_result, main    -- six calls, one product each
_TRUTH = [
    ("7100", "0810000301", "postpaid", "churn", "network"),
    ("7101", "0810000302", "postpaid", "save", "save cost"),
    ("7102", "0810000303", "tol", "churn", "network"),
    ("7103", "0810000304", "tvs", "save", "promotion related"),
    ("7104", "0810000305", "postpaid", "unknown", ""),
    ("7105", "0810000306", "tol", "churn", "contract end"),
]

# What each arm returned. Hand-designed so the paired table is:
#   both right 3 | neither 1 | incumbent only 1 | candidate only 1  -> d=2, net 0
_ARMS = {
    "gemini-audio": {
        "ASR-001": ("postpaid", "churn", ["network"]),          # right
        "ASR-002": ("postpaid", "save", ["save cost"]),         # right
        "ASR-003": ("tol", "churn", ["network"]),               # right
        "ASR-004": ("tvs", "churn", ["promotion related"]),     # WRONG outcome
        "ASR-005": ("postpaid", "save", []),                    # WRONG outcome
        "ASR-006": ("tol", "churn", ["contract end"]),          # right  (incumbent only)
    },
    "qwen-pipeline": {
        "ASR-001": ("postpaid", "churn", ["network"]),          # right
        "ASR-002": ("postpaid", "save", ["save cost"]),         # right
        "ASR-003": ("tol", "churn", ["network"]),               # right
        "ASR-004": ("tvs", "save", ["promotion related"]),      # right  (candidate only)
        "ASR-005": ("postpaid", "churn", []),                   # WRONG outcome
        "ASR-006": None,                                        # PARSE FAILURE
    },
}


@pytest.fixture()
def pack(tmp_path):
    gt = tmp_path / "ground-truth"
    gt.mkdir(parents=True)
    lines = ["call_id,phone_number,product,call_result,main,secondary,third"]
    for call_id, phone, product, result, main in _TRUTH:
        lines.append(f"{call_id},{phone},{product},{result},{main},,")
    (gt / "business.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return tmp_path


@pytest.fixture()
def run_dir(tmp_path):
    d = tmp_path / "run"
    d.mkdir()
    rows = []
    for arm, items in _ARMS.items():
        for item, spec in items.items():
            for rep in range(3):
                if spec is None:
                    rows.append({"arm": arm, "item_id": item, "replicate": rep,
                                 "status": "parse_failed"})
                    continue
                product, outcome, reasons = spec
                # ASR-005 disagrees with itself on one replicate, so the mode collapse and
                # the instability counter both have something real to do.
                if item == "ASR-005" and rep == 2:
                    outcome = "unknown"
                rows.append({
                    "arm": arm, "item_id": item, "replicate": rep, "status": "ok",
                    "fields": {
                        "products": [product],
                        f"{product}.outcome": outcome,
                        f"{product}.reasons": sorted(reasons),
                    },
                })
    (d / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )
    return d


@pytest.fixture()
def late_parse_run(tmp_path):
    """One item whose REPLICATE 1 parse-failed and whose replicates 2 and 3 answered.

    Deliberately a separate fixture rather than a seventh entry in `_ARMS`: the tests below
    assert counts worked out by hand over exactly six calls, and quietly changing the
    denominator under them would be the kind of edit this file exists to prevent.

    Worked out by hand first, as the module docstring requires:

      replicate 1  parse_failed          -> under `first` there is nothing to score
      replicate 2  tol / churn           -> under `modal` these two agree
      replicate 3  tol / churn

    So `first` must report the item FAILED and `modal` must report tol/churn. The instability
    counter must see only the two ok replicates, which agree, so the item is NOT unstable
    under either policy -- an item cannot be "unstable" on the strength of a replicate that
    produced no fields to disagree with.
    """
    d = tmp_path / "late-run"
    d.mkdir()
    rows = [{"arm": "gemini-audio", "item_id": "ASR-001", "replicate": 0,
             "status": "parse_failed"}]
    for rep in (1, 2):
        rows.append({
            "arm": "gemini-audio", "item_id": "ASR-001", "replicate": rep, "status": "ok",
            "fields": {"products": ["tol"], "tol.outcome": "churn",
                       "tol.reasons": ["network"]},
        })
    (d / "results.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    return d


def test_a_replicate_1_parse_failure_is_not_rescued_by_a_later_replicate(late_parse_run):
    """The preregistration says two things, and together they forbid this rescue.

    `retention-e24.plan.json`: every headline figure is computed on REPLICATE 1 ALONE, and a
    parse failure is scored as INCORRECT, never dropped -- the second existing precisely so
    an arm cannot improve its score by failing to answer.

    Taking the first *parseable* replicate is neither. It is also not neutral in practice:
    on the real runs it rescues 8 items in E23 and 3 in E24, and every one of them belongs to
    the incumbent, because the incumbent is the only arm that produces parse failures at all.

    The sibling test `test_a_parse_failure_scores_wrong_and_is_never_dropped` asserts the
    same invariant and passes today, because its fixture fails on ALL THREE replicates and so
    never reaches the branch this covers.
    """
    collapsed, unstable, failures = e23.collapse(late_parse_run, "first")
    assert failures["gemini-audio"]["parse_failed"] == 1
    assert collapsed["gemini-audio"]["ASR-001"].get("__failed__") is True, (
        "replicate 1 did not parse, and the item was scored from a later replicate anyway. "
        "That is neither 'replicate 1 alone' nor 'a parse failure scores as incorrect'."
    )
    assert unstable.get("gemini-audio", 0) == 0


def test_the_modal_policy_still_uses_every_parseable_replicate(late_parse_run):
    """`modal` is a different preregistered estimator and must be left alone.

    The strict rule above belongs to `first`. A majority vote across three replicates is
    entitled to use the two that answered; that is what makes it the more robust estimator
    and why the plan keeps it available.
    """
    collapsed, _, _ = e23.collapse(late_parse_run, "modal")
    fields = collapsed["gemini-audio"]["ASR-001"]
    assert not fields.get("__failed__")
    assert fields["products"] == ["tol"]
    assert fields["tol.outcome"] == "churn"


def test_truth_loads_and_is_keyed_by_call_id(pack):
    truth, phones = e23.load_truth(pack)
    assert len(truth) == 6
    assert truth["7100"][0].call_result == "churn"
    assert phones["7100"] == "0810000301"


def test_item_id_maps_to_the_composer_s_call_id():
    assert e23.item_to_call_id("ASR-001") == "7100"
    assert e23.item_to_call_id("ASR-138") == "7237"


def test_missing_truth_refuses_rather_than_scoring_nothing(tmp_path):
    (tmp_path / "ground-truth").mkdir()
    with pytest.raises(e23.Refused) as exc:
        e23.load_truth(tmp_path)
    assert "agreement" in str(exc.value)


def test_replicates_collapse_by_mode_not_average(run_dir):
    collapsed, unstable, _ = e23.collapse(run_dir)
    # ASR-005's third replicate differs, so 2 of 3 wins and the item is flagged unstable.
    assert collapsed["gemini-audio"]["ASR-005"]["postpaid.outcome"] == "save"
    assert unstable["gemini-audio"] == 1
    assert unstable["qwen-pipeline"] == 1


def test_a_parse_failure_scores_wrong_and_is_never_dropped(pack, run_dir):
    """The most flattering bug a scorer can have is dropping the items an arm failed."""
    truth, phones = e23.load_truth(pack)
    collapsed, _, failures = e23.collapse(run_dir)

    assert collapsed["qwen-pipeline"]["ASR-006"]["__failed__"] is True
    assert failures["qwen-pipeline"]["parse_failed"] == 3

    gt, pred = e23.build_pair(truth, phones, collapsed["qwen-pipeline"])
    # Six calls in, six calls scored -- the failure is present as a row, not absent.
    assert len({r.call_id for r in gt}) == 6
    assert len({r.call_id for r in pred}) == 6
    correct = e23.call_level_correct(gt, pred, "call_result")
    assert correct["7105"] is False


def test_hand_computed_call_level_accuracy(pack, run_dir):
    """Counted by reading _ARMS: incumbent 4/6 right, candidate 4/6 right."""
    truth, phones = e23.load_truth(pack)
    collapsed, _, _ = e23.collapse(run_dir)

    got = {}
    for arm in ("gemini-audio", "qwen-pipeline"):
        gt, pred = e23.build_pair(truth, phones, collapsed[arm])
        correct = e23.call_level_correct(gt, pred, "call_result")
        got[arm] = (sum(correct.values()), len(correct))

    assert got["gemini-audio"] == (4, 6)
    assert got["qwen-pipeline"] == (4, 6)


def test_hand_computed_paired_table(pack, run_dir):
    """both 3 | neither 1 | incumbent-only 1 | candidate-only 1  ->  d=2, net 0.

    d=2 is below the six discordant calls `exact_band` needs at alpha 1/64, so the honest
    verdict is UNDERPOWERED -- which is the point of the fixture: equal accuracy must not be
    reported as evidence of equivalence.
    """
    from evalharness.compare import Disagreement, paired_verdict

    truth, phones = e23.load_truth(pack)
    collapsed, _, _ = e23.collapse(run_dir)

    verdicts = {}
    for arm in ("gemini-audio", "qwen-pipeline"):
        gt, pred = e23.build_pair(truth, phones, collapsed[arm])
        verdicts[arm] = e23.call_level_correct(gt, pred, "call_result")

    a, b = verdicts["gemini-audio"], verdicts["qwen-pipeline"]
    both = sum(1 for k in a if a[k] and b[k])
    neither = sum(1 for k in a if not a[k] and not b[k])
    only_i = sum(1 for k in a if a[k] and not b[k])
    only_c = sum(1 for k in a if b[k] and not a[k])

    assert (both, neither, only_i, only_c) == (3, 1, 1, 1)

    table = Disagreement(dimension="call_result", both_right=both, both_wrong=neither,
                         incumbent_only_right=only_i, candidate_only_right=only_c)
    assert table.total == 6
    assert table.net == 0
    verdict = paired_verdict(table)
    assert verdict.band is None
    assert verdict.verdict == "UNDERPOWERED"


def test_an_invented_extra_product_is_a_product_miss(pack):
    """A call is right only if the SET of products matches; extra rows are not free."""
    truth, phones = e23.load_truth(pack)
    fields = {"ASR-001": {"products": ["postpaid", "tvs"],
                          "postpaid.outcome": "churn", "postpaid.reasons": ["network"],
                          "tvs.outcome": "churn", "tvs.reasons": []}}
    gt, pred = e23.build_pair(truth, phones, fields)
    assert e23.call_level_correct(gt, pred, "product")["7100"] is False
    # ... and the outcome cannot be credited either, since the row set disagrees.
    assert e23.call_level_correct(gt, pred, "call_result")["7100"] is False


def test_a_run_against_the_wrong_pack_refuses(pack):
    """item_id -> call_id is only meaningful if the pack is the one that was run."""
    truth, phones = e23.load_truth(pack)
    with pytest.raises(e23.Refused) as exc:
        e23.build_pair(truth, phones, {"ASR-099": {"products": []}})
    assert "disagree about which corpus" in str(exc.value)
