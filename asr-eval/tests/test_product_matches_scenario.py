"""The product a call is labelled with must be a product that call could be about.

WHY THIS FILE EXISTS. `choose_product` drew from a global mix while `choose_call_result`,
eight lines below it, took the scenario and constrained on it. Nothing failed. The corpus
generated a customer saying their TV box was slow while the agent talked them through
restarting their router, wrote `tvs` in the ground truth, and every model that answered
`tol` was marked wrong.

A blind audit found it from the outside: three frontier models shown only the transcript and
the written spec agreed with the corpus on 93.3% of control cases and went against it on 68%
of disputed product cases, substituting `tvs -> postpaid` nine times -- the same substitution
the models under test were losing points for. `docs/reports/audit-result.json`.

These tests exist so the next such contradiction fails a build instead of costing a run.
"""

from __future__ import annotations

import random
import sys
from collections import Counter
from pathlib import Path

import pytest

# Matching every sibling in this directory rather than relying on PYTHONPATH, which differs
# between the CI step and a local shell.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import business_labels as B
import compose_dialogues as CD
import thai_corpus as T
from reason_lines import PRODUCT_PHRASE

# The five scenarios whose dialogue names the product outright, and what it names.
# Read off the PROBE pools rather than restated from the source table, so a test that
# disagrees with `business_labels` is a test that noticed something.
FORCED = {
    "net_slow": "tol",
    "coverage_issue": "postpaid",
    "sim_replace": "postpaid",
    "mnp": "postpaid",
    "device_promo": "postpaid",
}


def test_every_scenario_has_a_product_rule():
    """A scenario the composer can draw but the product table has never heard of."""
    assert set(B.PRODUCT_BY_SCENARIO) == set(CD.SCENARIOS), (
        "PRODUCT_BY_SCENARIO and SCENARIOS disagree; a missing key is a KeyError at "
        "generation time and an extra one is a rule nothing applies"
    )
    assert set(CD.SCENARIO_WEIGHTS) == set(CD.SCENARIOS)


def test_every_product_drawn_is_a_label_space_value():
    for scenario, mix in B.PRODUCT_BY_SCENARIO.items():
        for product, weight in mix:
            assert product in B.PRODUCT_MIX, (
                f"{scenario} can draw {product!r}, which is not a product the scorer knows"
            )
            assert weight > 0, f"{scenario} carries a zero weight for {product!r}"


@pytest.mark.parametrize("scenario,product", sorted(FORCED.items()))
def test_the_forced_scenarios_admit_exactly_one_product(scenario, product):
    """No weighting here. A router is not a mobile number.

    Softening one of these back into a mix is the defect returning, so it fails rather than
    being caught by a reviewer noticing a percentage moved.
    """
    mix = B.PRODUCT_BY_SCENARIO[scenario]
    assert [p for p, _ in mix] == [product], (
        f"{scenario} calls are about {product}; the dialogue says so in every PROBE line. "
        f"Drawing anything else puts a label on the audio that contradicts the audio."
    )


def test_the_free_scenarios_are_the_ones_that_name_no_product():
    free = set(B.PRODUCT_BY_SCENARIO) - set(FORCED)
    for scenario in free:
        assert len(B.PRODUCT_BY_SCENARIO[scenario]) > 1, (
            f"{scenario} is not in the forced set but admits one product; either it belongs "
            "in FORCED with the reason written down, or the constraint is undocumented"
        )


def test_the_induced_mix_reproduces_the_measured_one():
    """FREE_PRODUCT_MIX is a derivation, and this is the derivation.

    PRODUCT_MIX was measured off the two packs carrying hand-computed keys. Constraining
    five scenarios could easily have moved the corpus's product balance as a side effect --
    which would make business accuracy on this set incomparable with the text packs for a
    reason nobody would think to look for. It does not, and that is checked rather than
    asserted.
    """
    total = sum(CD.SCENARIO_WEIGHTS.values())
    induced: Counter[str] = Counter()
    for scenario, weight in CD.SCENARIO_WEIGHTS.items():
        mix = B.PRODUCT_BY_SCENARIO[scenario]
        mass = sum(w for _, w in mix)
        for product, w in mix:
            induced[product] += (weight / total) * (w / mass)

    assert abs(sum(induced.values()) - 1.0) < 1e-9
    for product, target in B.PRODUCT_MIX.items():
        assert induced[product] == pytest.approx(target, abs=0.005), (
            f"{product}: the scenario-constrained draw induces {induced[product]:.3f} "
            f"against the measured target {target:.3f}. Either FREE_PRODUCT_MIX needs "
            "re-deriving against the current SCENARIO_WEIGHTS, or the target moved and "
            "the reason belongs in PRODUCT_MIX's comment."
        )


def test_a_generated_corpus_never_contradicts_its_own_scenario():
    """End to end, through the composer, not through the tables the composer reads.

    The tables could be right and the wiring wrong -- `compose_dialogues` passing the wrong
    argument, or an rng shared in a way that decouples them. This draws real calls.
    """
    plan, _ = CD.build_design(138)
    rng = random.Random(20260821)
    seen: Counter[str] = Counter()
    for scenario in (s for _, s in plan):
        product = B.choose_product(rng, scenario)
        seen[scenario] += 1
        if scenario in FORCED:
            assert product == FORCED[scenario], (
                f"a {scenario} call came out labelled {product!r}"
            )
    assert sum(seen.values()) == 138


def test_the_spoken_product_phrase_agrees_with_the_forced_label():
    """The label has to match the words the customer says, not just the scenario.

    `PRODUCT_PHRASE` is what puts the product in the transcript. If it drifted so that `tol`
    stopped meaning home internet, the forced mapping above would be labelling correct audio
    with the wrong key and this file would still pass.
    """
    assert set(PRODUCT_PHRASE) == set(B.PRODUCT_MIX)
    # Thai substrings, kept minimal so rewording a phrase does not fail this spuriously.
    assert any("บ้าน" in phrase for phrase in PRODUCT_PHRASE["tol"]), (
        "no `tol` phrase says 'home' any more; net_slow's router dialogue may no longer "
        "match the product it is forced to"
    )
    assert any("ทีวี" in phrase for phrase in PRODUCT_PHRASE["tvs"])
    assert any("รายเดือน" in phrase for phrase in PRODUCT_PHRASE["postpaid"]), (
        "no `postpaid` phrase says 'monthly' any more; mnp/sim_replace/coverage_issue are "
        "all forced to postpaid on the strength of that reading"
    )


def test_the_brand_named_can_carry_the_product_labelled():
    """A TrueVisions call labelled `postpaid` would be the same class of defect, one layer up."""
    for product, indices in B.BRAND_INDICES_BY_PRODUCT.items():
        assert indices, f"{product} has no brand that can carry it"
        for i in indices:
            assert 0 <= i < len(T.BRANDS)
    # The generic group brand is the only one allowed to carry `unknown`.
    assert B.BRAND_INDICES_BY_PRODUCT["unknown"] == (1,)
