"""Business ground truth for the audio set, chosen at generation time.

WHY THIS EXISTS. The audio set has never had a business label. `asr-eval/ground-truth/`
carries reference transcripts, entity lists and timelines -- everything needed to score
transcription, and nothing needed to score the decision production actually acts on. That is
why Experiment 21 could only report AGREEMENT between arms: with no truth to score against,
"the arms produced the same JSON" was the only available statement, and a labeller that is
reliably wrong agrees with itself perfectly.

THE INVERSION THIS MODULE PERFORMS. The composer used to flip a coin --

    self.add("customer", T.CUSTOMER_ACCEPT if self.rng.random() < 0.7 else T.CUSTOMER_DECLINE)

-- and the call's outcome was then whatever the coin had said. Recovering it meant reading
the text back, which is how the outcome ended up recoverable by string match on eight lines.

Here the label is drawn FIRST, from a distribution measured off the two hand-computed packs,
and the dialogue is then rendered to express it. Two things follow that could not follow
before:

  * **The label is correct by construction.** It is not inferred from the audio, not
    recovered from the transcript, and not produced by a model. It is the input the generator
    was given. That is a stronger guarantee than a frontier weak label and stronger than the
    hand-computed keys in the text packs, which are hand-derived from text that already
    existed.
  * **The mix is a decision rather than an accident.** The old coin gave a nominal 70/30
    accept/decline that landed at 12/8 on twenty calls, and had no way to express `unknown`
    or `undefined` at all -- two of the four values production uses.

THE LABEL SPACE IS PRODUCTION'S, NOT OURS. `evalharness.labelspaces.RETENTION` defines
call_result, product and reason; the CSV columns match `retention_v3.gt.csv` exactly
(`call_id,phone_number,product,call_result,main,secondary,third`) so `evalharness.metrics`
scores this set unchanged rather than through a second implementation that could drift.

WHAT THIS MODULE DELIBERATELY DOES NOT DO. It does not make the outcome hard to guess. Making
the closing lines non-separable is a change to the TEMPLATE POOLS in `thai_corpus.py`, and it
is measured by `leak_probe.py` against the labels this module produces. Keeping the two apart
matters: a generator that both invents the answer and grades the difficulty of finding it
cannot be trusted to report that the answer is too easy to find.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

# --------------------------------------------------------------------------------------
# The mix, measured off the packs that carry hand-computed keys.
# --------------------------------------------------------------------------------------
# retention_v3 (150 rows / 138 calls) and retention_challenge_v1 (64 / 50), pooled and
# rounded. These are targets for a 138-call draw, not quotas -- the draw is random per call
# so the realised mix will sit near them, and `compose_dialogues.py` prints what it got.
#
# `unknown` and `undefined` are the two values the old coin could not produce at all. They
# are 12% of real calls between them and they are exactly the cases a save/churn binary
# cannot represent: the customer who never resolves, and the call where the outcome is not
# a retention decision.

PRODUCT_MIX = {
    "postpaid": 0.54,
    "tol": 0.22,
    "tvs": 0.20,
    "unknown": 0.04,
}

CALL_RESULT_MIX = {
    "churn": 0.53,
    "save": 0.34,
    "unknown": 0.09,
    "undefined": 0.04,
}

# Which brands can plausibly carry which product. The composer used to pick a brand at
# random and the product was never recorded; a TrueVisions call scored as `postpaid` would
# be a label nobody could defend. Indices are into `thai_corpus.BRANDS`:
#   0 TrueMove H (mobile)  1 True (group, any)  2 dtac (mobile)
#   3 TrueOnline (broadband)  4 TrueVisions (TV)  5 TrueID (TV/streaming)
BRAND_INDICES_BY_PRODUCT = {
    "postpaid": (0, 1, 2),
    "tol": (1, 3),
    "tvs": (1, 4, 5),
    "unknown": (1,),
}

# What the five product-neutral scenarios draw from. Every value here is forced by
# arithmetic rather than taste: with the constrained scenarios below fixed, this is the mix
# that makes the induced product distribution reproduce PRODUCT_MIX above. `postpaid` is
# already 30% of the set from the forced scenarios alone, so the free ones -- 60% of calls --
# must supply the remaining 24 points, and so on for the other three. Deriving it is what
# keeps a fix aimed at mislabelling from quietly reshaping the corpus as a side effect.
FREE_PRODUCT_MIX = (("postpaid", 40), ("tvs", 33), ("tol", 20), ("unknown", 7))

# Which product each scenario can be about. See `choose_product` for why five of these are
# single-valued and five are not.
PRODUCT_BY_SCENARIO = {
    # Forced -- the dialogue names the product, so any other label contradicts the audio.
    "net_slow": (("tol", 1),),             # restart the router; LAN cable or WiFi
    "coverage_issue": (("postpaid", 1),),  # cell tower, airplane mode, does it support 5G
    "sim_replace": (("postpaid", 1),),     # a lost SIM, monthly-or-prepaid, a service centre
    "mnp": (("postpaid", 1),),             # porting a mobile number to another carrier
    "device_promo": (("postpaid", 1),),    # a handset upgrade bundled with a package
    # Free -- nothing in these calls decides the product, so the mix does.
    "retention": FREE_PRODUCT_MIX,
    "downsell": FREE_PRODUCT_MIX,
    "billing_dispute": FREE_PRODUCT_MIX,
    "payment_arrange": FREE_PRODUCT_MIX,
    "telesale_offer": FREE_PRODUCT_MIX,
}

# Scenario carries most of the reason signal, which is a real property of the task rather
# than a shortcut: a coverage complaint IS a network reason. Each scenario gets a weighted
# set rather than a single value, so `reason` is not a deterministic function of `scenario`
# -- otherwise the reason dimension would measure scenario recovery and nothing else.
REASON_BY_SCENARIO = {
    "retention": (("network", 3), ("save cost", 3), ("promotion related", 2),
                  ("contract end", 2), ("dissatisfied service", 1)),
    "downsell": (("save cost", 4), ("down sell not success", 3),
                 ("promotion related", 2), ("post to pre", 1)),
    "mnp": (("network", 2), ("save cost", 2), ("promotion related", 2),
            ("customer reason", 2), ("post to pre", 2), ("contract end", 1)),
    "billing_dispute": (("save cost", 3), ("dissatisfied service", 3),
                        ("promotion related", 2), ("other", 1)),
    "net_slow": (("network", 6), ("dissatisfied service", 2), ("other", 1)),
    "coverage_issue": (("network", 6), ("dissatisfied service", 2), ("other", 1)),
    "sim_replace": (("other", 4), ("customer reason", 3), ("dissatisfied service", 1)),
    "payment_arrange": (("save cost", 4), ("customer reason", 3), ("other", 2)),
    "device_promo": (("device promotion related", 5), ("promotion related", 2),
                     ("contract end", 2), ("sale upsell problem", 1)),
    "telesale_offer": (("sale upsell problem", 4), ("promotion related", 3),
                       ("device promotion related", 2), ("other", 1)),
}

# 6% of ground-truth rows in retention_v3 carry an EMPTY main reason. That is not a gap in
# the data -- it is what production writes when a call has an outcome but states no reason,
# and a corpus without it would never exercise the empty-string branch of the scorer.
EMPTY_REASON_RATE = 0.06

# 8% of calls carry a second reason (retention_v3 has 4 such rows in 150, plus multi-product
# calls). Kept low: the packs show it is uncommon, and inflating it would make `reason` look
# harder than production makes it.
SECOND_REASON_RATE = 0.08

# 150 rows over 138 calls: about 9% of calls mention a second product.
SECOND_PRODUCT_RATE = 0.09


@dataclass(frozen=True)
class BusinessLabel:
    """One scored row. Field names and order match `retention_v3.gt.csv` exactly."""

    call_id: str
    phone_number: str
    product: str
    call_result: str
    main: str
    secondary: str
    third: str

    @staticmethod
    def header() -> tuple[str, ...]:
        return ("call_id", "phone_number", "product", "call_result", "main",
                "secondary", "third")

    def row(self) -> tuple[str, ...]:
        return (self.call_id, self.phone_number, self.product, self.call_result,
                self.main, self.secondary, self.third)


def _weighted(rng: random.Random, mix) -> str:
    """Draw one key. Accepts {key: weight} or ((key, weight), ...)."""
    pairs = tuple(mix.items()) if isinstance(mix, dict) else tuple(mix)
    keys = [k for k, _ in pairs]
    weights = [w for _, w in pairs]
    return rng.choices(keys, weights=weights, k=1)[0]


def choose_product(rng: random.Random, scenario: str) -> str:
    """Draw the product the call is about, constrained by what the dialogue actually says.

    THIS TAKES `scenario` BECAUSE A BLIND AUDIT SAID IT MUST. Until 2026-08-21 it took only
    the rng, so product and scenario were drawn independently -- while `choose_call_result`
    directly below it already took the scenario and constrained on it. The result was calls
    whose own transcript contradicted their label: `PROBE["net_slow"]` walks the customer
    through restarting a router and asks LAN-or-WiFi, and 54% of those calls were labelled
    `postpaid`. `PROBE["mnp"]` is a request to port a mobile number to another carrier, and
    20% of those were labelled `tvs`.

    That is not a subtle mislabel, because the product is SPOKEN. `PRODUCT_PHRASE`
    (`reason_lines.py:273`) puts it in the customer's mouth -- TV box for `tvs`, home
    internet for `tol`. So the corpus was generating a customer who says their TV box is slow
    while the agent troubleshoots their router, and then scoring every model against the TV
    box.

    HOW WE KNOW, rather than how it looks. 68 cases went to three frontier models shown only
    the Thai transcript and the written spec -- not the corpus answer, not each other's, and
    none of them under test. They agreed with the corpus on 93.3% of undisputed control
    cases, so they could do the task; they went AGAINST it on 68% of disputed product cases,
    and their substitutions match the ones the models under test were being marked wrong for
    (`tvs -> postpaid` 9 times against the models' 8). Recorded in
    `docs/reports/audit-result.json`.

    Note what the reviewers did NOT do: they did not read the spoken product phrase back.
    They resolved a contradiction, and resolved it toward the scenario -- five to ten lines
    of router talk outweigh one mention of a TV box. So the scenario is what product follows.

    WHY FIVE SCENARIOS ARE FORCED AND FIVE ARE NOT. `REASON_BY_SCENARIO` above gives each
    scenario a weighted SET, on the stated ground that a deterministic map would make the
    reason dimension measure scenario recovery and nothing else. That argument holds here
    too -- and for five scenarios it does not apply, because the dialogue names the product
    outright. A router is not a mobile number. Weighting those would not preserve difficulty;
    it would reintroduce the exact defect the audit found. The other five are genuinely
    product-neutral -- everything has a bill, everything can be cancelled -- and they draw
    from a mix. That is also where every `unknown` product now comes from, since the
    non-committal phrase for it ("the service you're using") is only honest on a call where
    nothing else decides the answer.

    THE OVERALL MIX IS UNCHANGED. `FREE_PRODUCT_MIX` is not chosen by eye: it is the residual
    that makes the induced distribution reproduce `PRODUCT_MIX`, which was measured off the
    packs carrying hand-computed keys. `test_product_matches_scenario.py` recomputes it from
    `SCENARIO_WEIGHTS` and fails if either table drifts, so this stays a derivation rather
    than a comment claiming to be one.
    """
    return _weighted(rng, PRODUCT_BY_SCENARIO[scenario])


def choose_call_result(rng: random.Random, scenario: str) -> str:
    """Draw the outcome, with the two scenario constraints that are semantically forced.

    `mnp` is a port-out request: it cannot be a `save` unless the port-out is abandoned, and
    a corpus where MNP calls save at the base rate would be teaching the labeller something
    false about the business. `telesale_offer` and `device_promo` are outbound sales -- there
    is no existing service being retained, so `save`/`churn` do not apply and production
    writes `undefined`.
    """
    if scenario == "mnp":
        return _weighted(rng, {"churn": 0.80, "save": 0.10, "unknown": 0.10})
    if scenario in ("telesale_offer", "device_promo"):
        return _weighted(rng, {"undefined": 0.55, "unknown": 0.25, "save": 0.20})
    return _weighted(rng, CALL_RESULT_MIX)


def choose_reasons(rng: random.Random, scenario: str,
                   call_result: str = "") -> tuple[str, str, str]:
    """(main, secondary, third). Empty strings where production would write them.

    `call_result` is consulted for one case only, and it is a correctness fix rather than a
    refinement. `undefined` means, per `retention_v9_16_body.txt:82`, that the conversation
    was "irrelevant to retention ... the client did not call to discuss changing, cancelling
    or downgrading their service". A call carrying a CANCELLATION REASON is by definition a
    retention conversation, so attaching one to an `undefined` call contradicts its own
    label -- and before 2026-08-20 every `undefined` call got one.

    That is half of why no arm ever produced `undefined` here: the transcript argued against
    the label. The other half was the closing pool, corrected in `thai_corpus.py`.
    """
    if call_result == "undefined":
        return ("", "", "")
    if rng.random() < EMPTY_REASON_RATE:
        return ("", "", "")
    main = _weighted(rng, REASON_BY_SCENARIO[scenario])
    secondary = ""
    if rng.random() < SECOND_REASON_RATE:
        alternatives = [r for r, _ in REASON_BY_SCENARIO[scenario] if r != main]
        if alternatives:
            secondary = rng.choice(alternatives)
    return (main, secondary, "")


def brand_index_for(rng: random.Random, product: str) -> int:
    return rng.choice(BRAND_INDICES_BY_PRODUCT[product])
