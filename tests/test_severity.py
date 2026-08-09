"""Gates on `evalgen.severity`, starting with the table computed before the module existed.

`tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md` fixes the decision table, eleven
constructed cases, eleven real units read out of the e6c judge report, and a seven-row
majority table -- all by hand, before `severity.py` was written. The tests below reproduce
that document rather than the code's own behaviour, which is the only way a fixture can
disagree with an implementation instead of ratifying it.

The rest of this file checks the claims `severity.py`'s docstring makes about itself:

  * "seven of the eight branches are set arithmetic and cost nothing" -- checked by a
    client that raises if it is ever called.
  * "`fabricated_class` sits above the subset tests as a hard cap" -- checked on the one
    unit (fixture case c11) where a subset-first implementation silently disagrees.
  * "two byte-exact evidence gates, both required" -- checked one gate at a time, so a
    test passing because the other gate fired would be visible.
  * "diagnostic, never a scored dimension, never imported by the verdict path" -- checked
    by parsing the AST of every scoring module, not by trusting the docstring.
"""

from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.severity import (  # noqa: E402
    MIS_CLASSIFICATION,
    MIS_SCOPING,
    SEVERITY_CATEGORIES,
    SeverityError,
    SeverityUnit,
    build_severity_prompt,
    classify_error,
    family_aggregation,
    parse_severity_response,
    run_severity,
    severity_profile,
    severity_unit_id,
    severity_units,
)
from evalharness.compare import comparison_units  # noqa: E402
from evalharness.labelspaces import CALL_RESULT_CLASSES, REASON_CLASSES  # noqa: E402
from evalharness.records import Record  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "judge" / "SEVERITY-HAND-COMPUTED.md"
RULE_ROOT = ROOT / "production-reference"
VOCAB = frozenset(REASON_CLASSES)


def test_the_hand_computed_fixture_exists_and_is_not_a_stub():
    """If this file is missing, every expectation below is unmoored from the document
    that is supposed to govern it rather than the other way round."""
    assert FIXTURE.exists()
    assert FIXTURE.stat().st_size > 2000
    text = FIXTURE.read_text(encoding="utf-8")
    assert "fabricated_class" in text and "hard cap" in text


# ------------------------------------------------------------ the deterministic table


def _rec(call_id, phone, product, call_result, reasons=(), parse_ok=True):
    return Record(
        call_id=call_id,
        phone=phone,
        product=product,
        call_result=call_result,
        reasons=frozenset(reasons),
        parse_ok=parse_ok,
    )


_OK = _rec("1", "0810000001", "postpaid", "save")
_BAD = _rec("1", "0810000001", "postpaid", "save", parse_ok=False)

# Cases c1..c11 from section 2 of the fixture, in the fixture's order.
_CONSTRUCTED = [
    ("c1", {"save cost"}, set(), None, True, ("missing_output", None)),
    ("c2", {"save cost"}, {"save cost"}, _BAD, True, ("invalid_output", None)),
    ("c3", {"save cost"}, {"save cost", "กลัวแพง"}, _OK, True, ("fabricated_class", "outside_vocabulary")),
    ("c4", {"save cost"}, {"save cost", "other"}, _OK, True, ("over_labelling", None)),
    ("c5", set(), {"other"}, _OK, True, ("unsupported_claim", "empty_ground_truth")),
    ("c6", set(), {"other"}, _OK, False, ("unsupported_claim", "no_ground_truth_row")),
    ("c7", {"network", "save cost"}, set(), _OK, True, ("no_answer", None)),
    ("c8", {"network", "save cost"}, {"network"}, _OK, True, ("under_labelling", None)),
    ("c9", {"network"}, {"save cost"}, _OK, True, ("substitution", None)),
    ("c10", set(), {"กลัวแพง"}, _OK, True, ("fabricated_class", "outside_vocabulary")),
    (
        "c11",
        {"save cost"},
        {"save cost", "other", "กลัวแพง"},
        _OK,
        True,
        ("fabricated_class", "outside_vocabulary"),
    ),
]


@pytest.mark.parametrize(
    "name,truth,answer,record,present,expected",
    _CONSTRUCTED,
    ids=[case[0] for case in _CONSTRUCTED],
)
def test_classify_error_reproduces_the_hand_computed_decision_table(
    name, truth, answer, record, present, expected
):
    assert (
        classify_error(
            frozenset(truth),
            frozenset(answer),
            record=record,
            vocabulary=VOCAB,
            ground_truth_present=present,
        )
        == expected
    )


def test_the_hard_cap_beats_over_labelling_which_is_the_whole_point_of_the_ordering():
    """Fixture case c11 in isolation. Every true label is present and one extra is legal,
    so a subset-first implementation returns `over_labelling` and the invented class
    disappears from the report. This is the assertion that fails if the order is ever
    'simplified'."""
    truth = frozenset({"save cost"})
    answer = frozenset({"save cost", "other", "กลัวแพง"})
    assert truth < answer  # the over_labelling condition genuinely holds
    category, sub_reason = classify_error(
        truth, answer, record=_OK, vocabulary=VOCAB, ground_truth_present=True
    )
    assert category == "fabricated_class"
    # NOT the invented label: that string was written by a model and `sub_reason` ships
    # in the shareable export. Fixture section 6 records the correction and the reason.
    assert sub_reason == "outside_vocabulary"


def test_a_parse_failure_is_never_reported_as_a_fabrication():
    """Row 2 sits above row 3 for a reason: a failure skeleton can carry any text at all,
    and calling that a fabricated class would invent a decoding finding out of a transport
    one."""
    category, _ = classify_error(
        frozenset({"save cost"}),
        frozenset({"not a class at all"}),
        record=_BAD,
        vocabulary=VOCAB,
        ground_truth_present=True,
    )
    assert category == "invalid_output"


def test_classify_error_refuses_an_answer_that_equals_the_truth():
    """The population comes from the scorer's own correctness decision, so an equal pair
    reaching the classifier means the caller built the population somewhere else. Raising
    is the point: silently returning a category would hide it."""
    with pytest.raises(SeverityError):
        classify_error(
            frozenset({"save cost"}),
            frozenset({"save cost"}),
            record=_OK,
            vocabulary=VOCAB,
            ground_truth_present=True,
        )


def test_call_result_uses_its_own_vocabulary_not_the_reason_classes():
    category, _ = classify_error(
        frozenset({"save"}),
        frozenset({"save cost"}),  # a REASON class, illegal in call_result
        record=_OK,
        vocabulary=frozenset(CALL_RESULT_CLASSES),
        ground_truth_present=True,
    )
    assert category == "fabricated_class"


# ------------------------------------------------------- the real hand-classified units


class _FakeItem:
    def __init__(self, item_id, call_id, phone_number, transcript_th, rules):
        self.item_id = item_id
        self.call_id = call_id
        self.phone_number = phone_number
        self.transcript_th = transcript_th
        self.rules = rules


class _FakeTestSet:
    def __init__(self, items):
        self.items = items


# Section 3 of the fixture: (item, truth, incumbent answer, candidate answer).
_REAL = [
    ("RET-02", {"promotion related"},
     {"down sell not success", "other", "promotion related"},
     {"promotion related", "save cost"}),
    ("RET-06", {"post to pre"},
     {"dissatisfied service", "post to pre", "save cost"},
     {"post to pre", "save cost"}),
    ("RET-09", {"promotion related"},
     {"device promotion related", "promotion related"},
     {"promotion related"}),
    ("RET-13", {"down sell not success", "promotion related"},
     {"dissatisfied service", "promotion related", "save cost"},
     {"promotion related", "save cost"}),
    ("RET-14", {"network", "promotion related"}, {"network"}, {"promotion related"}),
    ("RET-19", set(), {"other"}, set()),
    ("RET-29", {"promotion related"}, {"promotion related"},
     {"promotion related", "save cost"}),
]

_EXPECTED_REAL = {
    ("RET-02", "incumbent"): "over_labelling",
    ("RET-02", "candidate"): "over_labelling",
    ("RET-06", "incumbent"): "over_labelling",
    ("RET-06", "candidate"): "over_labelling",
    ("RET-09", "incumbent"): "over_labelling",
    ("RET-13", "incumbent"): "substitution",
    ("RET-13", "candidate"): "substitution",
    ("RET-14", "incumbent"): "under_labelling",
    ("RET-14", "candidate"): "under_labelling",
    ("RET-19", "incumbent"): "unsupported_claim",
    ("RET-29", "candidate"): "over_labelling",
}


# One call carrying three product rows, the shape of call 5016 in
# `tests/fixtures/testsets/retention_v3.gt.csv`. Without it the row-grain-versus-cluster
# -grain gap is invisible: every other call here has exactly one product, so a test
# comparing the two populations compares equal numbers and cannot fail.
_MULTI_PRODUCT = ("RET-90", ("postpaid", "tol", "tvs"))


def _fixture_world():
    """Exactly the eleven units section 3 of the fixture classifies by hand -- nothing
    else. The hand-total assertions must run against the fixture's own population, or
    they stop being a check on the fixture and become a check on whatever this file
    happens to construct."""
    gt, inc, cand, items = [], [], [], []
    for index, (item_id, truth, incumbent, candidate) in enumerate(_REAL, start=1):
        call_id, phone = str(index), f"08100000{index:02d}"
        gt.append(_rec(call_id, phone, "postpaid", "save", truth))
        inc.append(_rec(call_id, phone, "postpaid", "save", incumbent))
        cand.append(_rec(call_id, phone, "postpaid", "save", candidate))
        items.append(_FakeItem(item_id, call_id, phone, f"transcript {item_id}", {}))
    return gt, inc, cand, _FakeTestSet(items)


def _real_world():
    gt, inc, cand, items = [], [], [], []
    for index, (item_id, truth, incumbent, candidate) in enumerate(_REAL, start=1):
        call_id, phone = str(index), f"08100000{index:02d}"
        gt.append(_rec(call_id, phone, "postpaid", "save", truth))
        inc.append(_rec(call_id, phone, "postpaid", "save", incumbent))
        cand.append(_rec(call_id, phone, "postpaid", "save", candidate))
        items.append(_FakeItem(item_id, call_id, phone, f"transcript {item_id}", {}))
    # A wrong `call_result` on each arm, so the coverage test has a non-zero denominator on
    # that dimension too. Truth `save`; incumbent says `churn`, candidate says `unknown`.
    gt.append(_rec("80", "0810000080", "postpaid", "save", {"network"}))
    inc.append(_rec("80", "0810000080", "postpaid", "churn", {"network"}))
    cand.append(_rec("80", "0810000080", "postpaid", "unknown", {"network"}))
    items.append(_FakeItem("RET-80", "80", "0810000080", "transcript RET-80", {}))
    # The multi-product call: both arms wrong on every row.
    item_id, products = _MULTI_PRODUCT
    for product in products:
        gt.append(_rec("90", "0810000090", product, "save", {"network"}))
        inc.append(_rec("90", "0810000090", product, "save", {"network", "other"}))
        cand.append(_rec("90", "0810000090", product, "save", {"network", "other"}))
    items.append(_FakeItem(item_id, "90", "0810000090", "transcript RET-90", {}))
    return gt, inc, cand, _FakeTestSet(items)


def _item_ids(testset):
    return {
        (str(item.call_id), str(item.phone_number)): item.item_id
        for item in testset.items
    }


def test_real_units_match_the_categories_classified_by_hand_in_the_fixture():
    gt, inc, cand, testset = _fixture_world()
    units = severity_units(
        gt,
        {"incumbent": inc, "candidate": cand},
        "reason",
        item_ids=_item_ids(testset),
    )
    observed = {(unit.item_id, unit.arm): unit.category for unit in units}
    assert observed == _EXPECTED_REAL


def test_real_unit_hand_totals_and_rates_match_the_fixture():
    """The totals table at the end of fixture section 3, including the denominator rule:
    rates are over that arm's WRONG units, never over all units."""
    gt, inc, cand, testset = _fixture_world()
    units = severity_units(
        gt, {"incumbent": inc, "candidate": cand}, "reason", item_ids=_item_ids(testset)
    )
    profile = severity_profile(
        [
            {"arm": unit.arm, "dimension": unit.dimension, "category": unit.category}
            for unit in units
            # `substitution` is a routing state and is not a reported category; the
            # fixture counts it separately, so the profile is checked without it.
            if unit.category != "substitution"
        ]
    )
    assert len(units) == 11
    assert sum(1 for u in units if u.category == "over_labelling") == 6
    assert sum(1 for u in units if u.category == "under_labelling") == 2
    assert sum(1 for u in units if u.category == "substitution") == 2
    assert sum(1 for u in units if u.category == "unsupported_claim") == 1
    assert sum(1 for u in units if u.arm == "incumbent") == 6
    assert sum(1 for u in units if u.arm == "candidate") == 5
    # 3 of 6 and 3 of 5, exactly as hand-computed; the substitution units are excluded
    # from this profile so the denominators are 5 and 4 here, not 6 and 5.
    assert profile["incumbent"]["counts"]["over_labelling"] == 3
    assert profile["candidate"]["counts"]["over_labelling"] == 3
    # The fixture's own two rates, over every wrong unit including the substitutions.
    for arm, expected_rate in (("incumbent", 0.5), ("candidate", 0.6)):
        wrong = [unit for unit in units if unit.arm == arm]
        over = [unit for unit in wrong if unit.category == "over_labelling"]
        assert len(over) / len(wrong) == pytest.approx(expected_rate)


def test_ret09_is_over_labelling_and_never_reaches_a_model():
    """The fixture's worked example: the extra label is the NEIGHBOURING class, which is
    the near-family shape, but it was added rather than substituted. A design that judged
    similarity instead of set relations would spend a call here."""
    gt, inc, cand, testset = _fixture_world()
    units = severity_units(
        gt, {"incumbent": inc, "candidate": cand}, "reason", item_ids=_item_ids(testset)
    )
    unit = next(u for u in units if u.item_id == "RET-09" and u.arm == "incumbent")
    assert unit.category == "over_labelling"
    assert unit.extra == frozenset({"device promotion related"})
    assert unit.missing == frozenset()


def test_every_wrong_unit_is_classified_exactly_once_against_the_scorers_own_population():
    """Done criterion 4 of the goal contract. The population is `compare`'s, so this
    asserts the two never drift: not a count this module chose, the count the scorer's
    own correctness decision produces."""
    gt, inc, cand, testset = _real_world()
    for dimension in ("reason", "call_result"):
        units = severity_units(
            gt,
            {"incumbent": inc, "candidate": cand},
            dimension,
            item_ids=_item_ids(testset),
        )
        expected = comparison_units(gt, inc, cand, dimension)
        wrong_incumbent = sum(1 for u in expected if not u.incumbent_right)
        wrong_candidate = sum(1 for u in expected if not u.candidate_right)
        assert sum(1 for u in units if u.arm == "incumbent") == wrong_incumbent
        assert sum(1 for u in units if u.arm == "candidate") == wrong_candidate
        assert len(units) == wrong_incumbent + wrong_candidate


def test_severity_units_refuses_anything_other_than_two_arms():
    gt, inc, cand, testset = _real_world()
    for arms in ({"only": inc}, {"a": inc, "b": cand, "c": cand}, {}):
        with pytest.raises(SeverityError):
            severity_units(gt, arms, "reason", item_ids=_item_ids(testset))


def test_product_is_out_of_scope_and_says_so_rather_than_scoring_it_wrong():
    gt, inc, cand, testset = _real_world()
    with pytest.raises(SeverityError):
        severity_units(
            gt, {"incumbent": inc, "candidate": cand}, "product",
            item_ids=_item_ids(testset),
        )


def test_unit_ids_separate_the_two_arms():
    """Without the arm in the identity, the two arms' units for one row collide and one
    silently overwrites the other in every by-id aggregation."""
    a = severity_unit_id("RET-01", "postpaid", "reason", "incumbent")
    b = severity_unit_id("RET-01", "postpaid", "reason", "candidate")
    assert a != b
    assert a == severity_unit_id("RET-01", "postpaid", "reason", "incumbent")


# --------------------------------------------------------------- the two evidence gates


_RULE_LINES = ["    CRITICAL: if the customer says X, use save cost", "    otherwise use other"]
_TRANSCRIPT = "ลูกค้า: ราคาแพงไป\nพนักงาน: มีโปรลดราคาครับ"


def _response(**overrides):
    payload = {
        "family": "near",
        "cited_span": "ราคาแพงไป",
        "cited_rule_line": _RULE_LINES[0],
        "rationale": "adjacent under the quoted clause",
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_a_clean_response_passes_both_gates():
    verdict = parse_severity_response(
        _response(), transcript=_TRANSCRIPT, rule_lines=_RULE_LINES
    )
    assert verdict.family == "near"
    assert verdict.parse_error is False
    assert verdict.evidence_status == "exact"
    assert verdict.rule_evidence_status == "exact"


def test_gate_one_rejects_a_span_that_is_not_in_the_transcript():
    verdict = parse_severity_response(
        _response(cited_span="a span the customer never said"),
        transcript=_TRANSCRIPT,
        rule_lines=_RULE_LINES,
    )
    assert verdict.family == "unclear"
    assert verdict.parse_error is True
    assert verdict.evidence_status == "not_in_transcript"
    # the OTHER gate still passed, so this test cannot pass for the wrong reason
    assert verdict.rule_evidence_status == "exact"


def test_gate_two_rejects_a_paraphrased_rule_line():
    """'Demonstrated, not claimed': a judge that paraphrases a rule it was handed has not
    shown the boundary it is asserting."""
    verdict = parse_severity_response(
        _response(cited_rule_line="the rule says to use save cost when the customer says X"),
        transcript=_TRANSCRIPT,
        rule_lines=_RULE_LINES,
    )
    assert verdict.family == "unclear"
    assert verdict.parse_error is True
    assert verdict.rule_evidence_status == "not_in_quoted_rules"
    assert verdict.evidence_status == "exact"  # gate one passed


def test_a_decisive_answer_must_carry_both_citations():
    missing_rule = parse_severity_response(
        _response(cited_rule_line=""), transcript=_TRANSCRIPT, rule_lines=_RULE_LINES
    )
    assert missing_rule.parse_error is True
    assert missing_rule.rule_evidence_status == "missing"
    missing_span = parse_severity_response(
        _response(cited_span=""), transcript=_TRANSCRIPT, rule_lines=_RULE_LINES
    )
    assert missing_span.parse_error is True
    assert missing_span.evidence_status == "missing"


def test_unclear_may_omit_both_citations_because_that_is_itself_a_result():
    verdict = parse_severity_response(
        _response(family="unclear", cited_span="", cited_rule_line=""),
        transcript=_TRANSCRIPT,
        rule_lines=_RULE_LINES,
    )
    assert verdict.family == "unclear"
    assert verdict.parse_error is False


def test_unusable_responses_are_scored_unclear_and_never_raise():
    for raw in (
        "not json at all",
        "[]",
        json.dumps({"family": "sideways", "cited_span": "x", "cited_rule_line": "y", "rationale": "z"}),
        json.dumps({"family": "near", "cited_span": "x", "cited_rule_line": "y"}),
        json.dumps({"family": "near", "cited_span": "x", "cited_rule_line": "y", "rationale": "  "}),
    ):
        verdict = parse_severity_response(raw, transcript=_TRANSCRIPT, rule_lines=_RULE_LINES)
        assert verdict.family == "unclear"
        assert verdict.parse_error is True


# ------------------------------------------------------------------- majority collapse


def test_family_aggregation_reproduces_the_hand_computed_majority_table():
    """The seven-row table in fixture section 4, row for row."""
    table = [
        ("s1", ["near", "near", "near"]),
        ("s2", ["near", "near", "cross"]),
        ("s3", ["cross", "near", "cross"]),
        ("s4", ["near", "cross", "unclear"]),
        ("s5", ["near", "unclear", "near"]),
        ("s6", ["near", "cross"]),
        ("s7", ["unclear", "unclear", "near"]),
    ]
    records = [
        {"severity_unit_id": unit_id, "replicate": index, "family": family}
        for unit_id, families in table
        for index, family in enumerate(families, start=1)
    ]
    aggregated = family_aggregation(records)
    categories = {unit: value["category"] for unit, value in aggregated.items()}
    assert categories == {
        "s1": "near_family",
        "s2": "near_family",
        "s3": "cross_family",
        "s4": "unclear_family",
        "s5": "near_family",
        "s6": "unclear_family",
        "s7": "unclear_family",
    }
    assert sum(1 for v in aggregated.values() if v["category"] == "near_family") == 3
    assert sum(1 for v in aggregated.values() if v["category"] == "cross_family") == 1
    assert sum(1 for v in aggregated.values() if v["category"] == "unclear_family") == 3
    assert sum(1 for v in aggregated.values() if v["flipped"]) == 6
    assert sum(1 for v in aggregated.values() if v["no_majority"]) == 2
    assert aggregated["s6"]["no_majority"] is True   # a tie, never broken by picking
    assert aggregated["s7"]["no_majority"] is False  # a real majority, for `unclear`


# ------------------------------------------------------------------------ run_severity


class _ExplodingClient:
    """Fails the test loudly if the deterministic path ever reaches a model."""

    def complete(self, **kwargs):  # pragma: no cover - the assertion is that it is unused
        raise AssertionError("a model was called on a path that must make zero calls")


class _FakeCompletion:
    def __init__(self, content):
        self.content = content
        self.observed_model = "google/gemma-4-31b-it"
        self.provider = "CoreWeave"
        self.reasoning_tokens = 0
        self.cost = 0.0001
        self.latency_s = 1.0


class _FakeClient:
    def __init__(self, family="unclear", **fields):
        self.calls = []
        self._payload = {
            "family": family,
            "cited_span": "",
            "cited_rule_line": "",
            "rationale": "fixed test response",
        }
        self._payload.update(fields)

    def complete(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeCompletion(json.dumps(self._payload, ensure_ascii=False))


def _ruled_world():
    """The RET-13 substitution shape, with real citations into the tracked rule tree."""
    gt = [_rec("1", "0810000001", "postpaid", "save", {"down sell not success"})]
    inc = [_rec("1", "0810000001", "postpaid", "save", {"save cost"})]
    cand = [_rec("1", "0810000001", "postpaid", "save", {"down sell not success"})]
    testset = _FakeTestSet(
        [
            _FakeItem(
                "RET-13", "1", "0810000001", "บทสนทนา",
                {"rule_reason:down sell not success": "prompt.py:4375-4376"},
            )
        ]
    )
    return gt, inc, cand, testset


def test_deterministic_only_makes_zero_calls_and_still_sizes_the_remainder():
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_ExplodingClient(),
        deterministic_only=True,
        rule_source_root=RULE_ROOT,
    )
    assert report["judged"]["calls_made"] == 0
    # The number the mode exists to produce: counted from the classification, not from
    # what reached a call, which would report zero in exactly this mode.
    assert report["judged"]["substitution_units_found"] == 1
    assert report["judged"]["units_sendable"] == 1
    assert report["units"][0]["category"] == "unclear_family"
    assert report["units"][0]["sub_reason"] == "not_judged"


def test_a_substitution_with_no_rule_text_anywhere_is_never_sent():
    """Gate 2 cannot pass with nothing quoted, so the call could only ever return
    `unclear` at a cost. Classified without one, and counted so the gap stays visible."""
    gt, inc, cand, _ = _ruled_world()
    testset = _FakeTestSet([_FakeItem("RET-13", "1", "0810000001", "บทสนทนา", {})])
    client = _ExplodingClient()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
    )
    assert report["judged"]["calls_made"] == 0
    assert report["judged"]["substitution_units_found"] == 1
    assert report["judged"]["units_sendable"] == 0
    assert report["units"][0]["sub_reason"] == "no_rule_text"
    assert len(report["rule_text"]["units_without_rule_text"]) == 1


def test_the_prompt_quotes_the_rule_for_the_asserted_class_not_only_the_reference_one():
    """Defect 4 from the 2026-08-09 judge review, in its severity form -- and here it is
    load-bearing rather than merely helpful, because the whole question is about the class
    the answer asserted. The citation for `save cost` is never in this item's own rules
    dict; it can only come from the pack-level union."""
    gt, inc, cand, _ = _ruled_world()
    testset = _FakeTestSet(
        [
            _FakeItem(
                "RET-13", "1", "0810000001", "บทสนทนา",
                {"rule_reason:down sell not success": "prompt.py:4375-4376"},
            ),
            # a different item is where `save cost` is asserted in this pack
            _FakeItem(
                "RET-37", "2", "0810000002", "อื่น",
                {"rule_reason:save cost": "prompt.py:4342"},
            ),
        ]
    )
    client = _FakeClient()
    run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=1,
    )
    assert len(client.calls) == 1
    prompt = json.dumps(client.calls[0]["messages"], ensure_ascii=False)
    source = (RULE_ROOT / "sentiment-batch-retention-main" / "src" / "prompt.py").read_text(
        encoding="utf-8"
    ).splitlines()
    asserted_class_rule = source[4341]  # prompt.py:4342, the `save cost` line
    reference_class_rule = source[4374]  # prompt.py:4375
    assert json.dumps(asserted_class_rule, ensure_ascii=False)[1:-1] in prompt
    assert json.dumps(reference_class_rule, ensure_ascii=False)[1:-1] in prompt


def test_repeats_make_one_call_per_replicate_and_collapse_to_one_unit():
    gt, inc, cand, testset = _ruled_world()
    client = _FakeClient()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=3,
    )
    assert len(client.calls) == 3
    assert report["judged"]["units_judged"] == 1
    assert report["judged"]["calls_made"] == 3
    assert report["judged"]["flipped"] == 0  # the fake client is deterministic
    assert sorted(call["replicate"] for call in report["calls"]) == [1, 2, 3]


def test_run_severity_refuses_the_inputs_it_cannot_honour():
    gt, inc, cand, testset = _ruled_world()
    arms = {"incumbent": inc, "candidate": cand}
    common = dict(testset=testset, gt=gt, arms=arms, dimensions=["reason"])
    for bad in (0, -1, True, 1.5):
        with pytest.raises(SeverityError):
            run_severity(**common, deterministic_only=True, repeats=bad)
    with pytest.raises(SeverityError):  # judged path with no rule root
        run_severity(
            **common, client=_FakeClient(), model="m", provider="p", rule_source_root=None
        )
    with pytest.raises(SeverityError):  # judged path with no client
        run_severity(**common, rule_source_root=RULE_ROOT)
    with pytest.raises(SeverityError):  # a rule root that is not a directory
        run_severity(
            **common,
            client=_FakeClient(),
            model="m",
            provider="p",
            rule_source_root=ROOT / "does-not-exist",
        )
    with pytest.raises(SeverityError):
        run_severity(**dict(common, dimensions=["product"]), deterministic_only=True)


class _FakeTransportError(RuntimeError):
    def __init__(self, message, *, latency_s):
        super().__init__(message)
        self.latency_s = latency_s


class _TransportFailingClient:
    def complete(self, **kwargs):
        raise _FakeTransportError("endpoint unavailable", latency_s=0.25)


def test_transport_failures_are_recorded_not_dropped_and_not_called_parse_errors():
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_TransportFailingClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=2,
    )
    assert report["judged"]["transport_errors"] == 2
    assert report["judged"]["invalid_responses"] == 0  # a dead endpoint is not a bad answer
    assert all(call["parse_error"] is False for call in report["calls"])
    assert report["units"][0]["category"] == "unclear_family"


class _BrokenClient:
    """A client bug, not a transport outcome. Must surface, never be laundered."""

    def complete(self, **kwargs):
        raise TypeError("someone changed the client signature")


def test_a_client_programming_error_is_raised_rather_than_recorded_as_a_transport_error():
    gt, inc, cand, testset = _ruled_world()
    with pytest.raises(TypeError):
        run_severity(
            testset=testset,
            gt=gt,
            arms={"incumbent": inc, "candidate": cand},
            dimensions=["reason"],
            client=_BrokenClient(),
            model="google/gemma-4-31b-it",
            provider="CoreWeave",
            rule_source_root=RULE_ROOT,
        )


# ------------------------------------------------------------------ the safe surfaces


def test_the_shareable_report_drops_transcript_rule_text_and_the_absolute_path():
    """The `rule_text.root` leak found in `judge.shareable_report` on 2026-08-09 put an
    absolute path and the operator's OS account name into a shareable export. The fix is
    re-asserted here rather than assumed not to recur."""
    from evalgen.severity import shareable_severity_report

    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_FakeClient(family="near", cited_span="x", cited_rule_line="y"),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=1,
    )
    safe = shareable_severity_report(report)
    serialized = json.dumps(safe, ensure_ascii=False)
    assert str(RULE_ROOT) not in serialized
    assert "root" not in safe["rule_text"]
    assert all("cited_span" not in call for call in safe["calls"])
    assert all("rationale" not in call for call in safe["calls"])
    assert all("cited_rule_line" not in call for call in safe["calls"])
    # and the diagnostic content survives
    assert safe["profile"]["incumbent"]["wrong_units"] == 1
    assert safe["calls"][0]["family"] == "unclear"  # gates rejected the fake citations


def test_the_report_carries_nothing_a_verdict_path_would_recognise():
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        deterministic_only=True,
        rule_source_root=RULE_ROOT,
    )
    forbidden = {"net", "verdict", "winner", "band", "discordant", "decision", "passed"}
    assert not forbidden & set(report)
    assert not forbidden & set(report["judged"])
    for profile in report["profile"].values():
        assert not forbidden & set(profile)


def test_the_taxonomy_partitions_cleanly():
    assert set(MIS_SCOPING) | set(MIS_CLASSIFICATION) <= set(SEVERITY_CATEGORIES)
    assert not set(MIS_SCOPING) & set(MIS_CLASSIFICATION)
    assert "substitution" not in SEVERITY_CATEGORIES  # a routing state, not a category
    assert len(set(SEVERITY_CATEGORIES)) == len(SEVERITY_CATEGORIES)


# ------------------------------------------------------------------------- isolation


def _imports_of(path: Path) -> set[str]:
    """Every module name a file imports, INCLUDING the `from package import module` form.

    Recording only `node.module` on an `ImportFrom` makes `from evalgen import severity`
    invisible, which is the form someone reaching for a diagnostic would most naturally
    write. Verified by mutation: with the original version, inserting that line into
    `report.py` left the gate passing.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{alias.name}" for alias in node.names)
    return names


# The modules that decide anything. `experiments.py` is the one that matters most and the
# one the first version of this test omitted: it is the only `evalgen` module that imports
# `paired_verdict` and computes a decision, so it is where an advisory category would do
# real damage. The `evalharness` package is covered strictly better by
# `test_boundary.py::test_evalharness_never_imports_evalgen`, which forbids the whole
# package, and is kept here only as a belt-and-braces sweep.
_VERDICT_PATH = (
    ROOT / "src" / "evalgen" / "report.py",
    ROOT / "src" / "evalgen" / "experiments.py",
    ROOT / "src" / "evalharness" / "manifest.py",
    ROOT / "src" / "evalharness" / "compare.py",
)


@pytest.mark.parametrize("path", _VERDICT_PATH, ids=lambda p: p.name)
def test_severity_is_never_imported_by_the_verdict_path(path):
    """The same guarantee `judge.py` carries, checked the same way. A severity category
    that quietly became a term in a score would let a third model's opinion about HOW an
    arm failed change WHETHER it passed."""
    assert path.exists(), f"{path} is missing -- the check would pass vacuously"
    imported = _imports_of(path)
    assert not any(
        name == "evalgen.severity" or name.endswith(".severity") or name == "severity"
        for name in imported
    ), f"{path.name} imports the severity diagnostic"


def test_the_scoring_package_still_imports_nothing_from_evalgen():
    scoring = sorted((ROOT / "src" / "evalharness").rglob("*.py"))
    assert scoring, "no scoring modules found -- the check would pass vacuously"
    for path in scoring:
        assert not any(
            name.startswith("evalgen") for name in _imports_of(path)
        ), f"{path.name} imports from evalgen"


def test_severity_never_imports_a_model_client_into_the_scoring_package():
    """`compare.py` gained two public helpers for this module. They must stay pure: the
    boundary test forbids `evalharness` importing `evalgen`, and this asserts the new
    functions did not smuggle anything in the other direction either."""
    imported = _imports_of(ROOT / "src" / "evalharness" / "compare.py")
    assert not any(name.startswith("evalgen") for name in imported)
    assert not any(name in {"openai", "httpx", "requests"} for name in imported)


def test_severity_unit_exposes_the_substituted_sets_a_reviewer_needs():
    unit = SeverityUnit(
        key=("1", "p", "postpaid"),
        item_id="RET-01",
        arm="incumbent",
        dimension="reason",
        truth=frozenset({"network", "save cost"}),
        answer=frozenset({"network", "other"}),
        category="substitution",
    )
    assert unit.missing == frozenset({"save cost"})
    assert unit.extra == frozenset({"other"})


def test_build_severity_prompt_never_names_an_arm_or_a_model():
    unit = SeverityUnit(
        key=("1", "p", "postpaid"),
        item_id="RET-01",
        arm="v3-gemini-rescored",
        dimension="reason",
        truth=frozenset({"save cost"}),
        answer=frozenset({"other"}),
        category="substitution",
    )
    messages = build_severity_prompt("transcript", unit, ["- save cost: ..."])
    blob = json.dumps(messages, ensure_ascii=False)
    for forbidden in ("gemini", "qwen", "gemma", "v3-gemini-rescored", "incumbent", "candidate"):
        assert forbidden not in blob.lower()
    # and it must keep this question away from the one `judge.py` owns
    assert "NOT asked whether the reference labelling is correct" in messages[0]["content"]


# --------------------------------------------------------------- the regime warning


def test_a_reasoning_regime_gap_is_warned_about_rather_than_left_in_a_run_log():
    """Experiment 4 measured the same model id, prompt and pack moving `reason` net from
    -1 to +24 on an endpoint change alone, because the replacement reasoned. An arm
    reasoning against an arm running production's `thinkingBudget: 0` is that confound
    exactly, so the profile prints it above itself."""
    from evalgen.severity import _regime_warning

    assert _regime_warning({"a": 0, "b": 2_379_369}) is not None
    assert _regime_warning({"a": 100, "b": 2_000_000}) is not None
    assert _regime_warning({"a": 0, "b": 0}) is None          # neither reasons
    assert _regime_warning({"a": 1000, "b": 1200}) is None    # same regime
    assert _regime_warning({"a": 1000}) is None               # nothing to compare
    assert _regime_warning({}) is None
    assert _regime_warning({"a": None, "b": 2_000_000}) is None  # unrecorded, not zero


def test_the_regime_warning_reaches_the_report_and_the_shareable_export():
    from evalgen.severity import shareable_severity_report

    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        arm_reasoning_tokens={"incumbent": 0, "candidate": 2_379_369},
        dimensions=["reason"],
        deterministic_only=True,
        rule_source_root=RULE_ROOT,
    )
    assert report["regime_warning"] is not None
    assert report["arms"]["candidate"]["reasoning_tokens"] == 2_379_369
    assert shareable_severity_report(report)["regime_warning"] == report["regime_warning"]


def test_mis_scoping_is_structurally_impossible_on_a_single_label_dimension():
    """Fixture section 6, the 2026-08-09 note. `call_result` sets never hold more than
    one element, so rows 6 and 7 of the decision table are unreachable there. A
    `call_result` profile reporting `mis-scoping: 0` is a property of the dimension and
    must never be read as a finding about the arm."""
    vocabulary = frozenset(CALL_RESULT_CLASSES)
    seen = set()
    for truth in CALL_RESULT_CLASSES:
        for answer in CALL_RESULT_CLASSES:
            if truth == answer:
                continue
            category, _ = classify_error(
                frozenset({truth}),
                frozenset({answer}),
                record=_OK,
                vocabulary=vocabulary,
                ground_truth_present=True,
            )
            seen.add(category)
    assert seen == {"substitution"}
    assert not seen & set(MIS_SCOPING)


# ------------------------------------------- gaps an adversarial review found (2026-08-09)


def test_row_grain_and_cluster_grain_differ_and_the_number_is_asserted_not_assumed():
    """Done criterion 4, on a population where it can actually fail. `_real_world` carries
    one call with three product rows, so row grain and cluster grain are 3 and 1 -- the
    criterion originally named `comparison_clusters`, and the test could not tell because
    every call in its fixture had exactly one product."""
    from evalharness.compare import comparison_clusters

    gt, inc, cand, testset = _real_world()
    units = severity_units(
        gt, {"incumbent": inc, "candidate": cand}, "reason", item_ids=_item_ids(testset)
    )
    rows = [u for u in comparison_units(gt, inc, cand, "reason") if not u.incumbent_right]
    clusters = [
        c for c in comparison_clusters(gt, inc, cand, "reason") if not c.incumbent_right
    ]
    assert len(rows) - len(clusters) == 2, "the multi-product call must create the gap"
    incumbent_units = [u for u in units if u.arm == "incumbent"]
    assert len(incumbent_units) == len(rows)  # severity is row grain, deliberately
    assert len(incumbent_units) != len(clusters)
    assert sum(1 for u in incumbent_units if u.item_id == "RET-90") == 3


def test_every_requested_dimension_and_arm_gets_a_block_even_when_it_is_all_zeros():
    """A perfect arm used to vanish from a two-arm report, indistinguishable from an arm
    that was not measured. Same for a dimension an arm never failed on."""
    gt = [_rec("1", "0810000001", "postpaid", "save", {"network"})]
    inc = [_rec("1", "0810000001", "postpaid", "save", {"network"})]  # perfect
    cand = [_rec("1", "0810000001", "postpaid", "save", {"network", "other"})]
    testset = _FakeTestSet([_FakeItem("RET-01", "1", "0810000001", "t", {})])
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason", "call_result"],
        deterministic_only=True,
    )
    assert sorted(report["profile"]) == ["candidate", "incumbent"]
    assert report["profile"]["incumbent"]["wrong_units"] == 0
    assert report["profile"]["incumbent"]["rates"]["over_labelling"] == 0.0  # not nan
    for arm in ("incumbent", "candidate"):
        assert sorted(report["profile"][arm]["by_dimension"]) == [
            "call_result",
            "reason",
        ]
    candidate = report["profile"]["candidate"]["by_dimension"]
    assert candidate["reason"]["counts"]["over_labelling"] == 1
    assert candidate["call_result"]["wrong_units"] == 0
    assert report["profile"]["candidate"]["pooled_across_dimensions"] == [
        "call_result",
        "reason",
    ]


def test_a_dead_connection_never_votes_on_a_family():
    """Two transport errors and one clean `near` used to collapse to `unclear_family`
    2/3 -- a record indistinguishable from a judge that looked three times and could not
    tell, on a unit where the only opinion ever obtained was `near`. It also corrupted the
    flip rate, which the fixture makes the mandatory honesty check."""

    class _FlakyClient:
        def __init__(self):
            self.attempt = 0

        def complete(self, **kwargs):
            self.attempt += 1
            if self.attempt <= 2:
                raise _FakeTransportError("dropped", latency_s=0.1)
            source = (
                RULE_ROOT / "sentiment-batch-retention-main" / "src" / "prompt.py"
            ).read_text(encoding="utf-8").splitlines()
            return _FakeCompletion(
                json.dumps(
                    {
                        "family": "near",
                        "cited_span": "บทสนทนา",
                        "cited_rule_line": source[4374],
                        "rationale": "adjacent",
                    },
                    ensure_ascii=False,
                )
            )

    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_FlakyClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=3,
    )
    unit = report["units"][0]
    assert unit["category"] == "near_family"
    assert unit["family_votes"] == "1/1"       # one opinion, counted once
    assert unit["flipped"] is False            # two outages are not a flip
    assert report["judged"]["transport_errors"] == 2
    assert report["judged"]["identity_not_matched"] == 0  # an outage is not a mismatch


def test_a_unit_whose_every_call_died_says_so_rather_than_borrowing_judge_language():
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_TransportFailingClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=2,
    )
    assert report["units"][0]["category"] == "unclear_family"
    assert report["units"][0]["sub_reason"] == "no_usable_response"
    assert report["units"][0]["judged"] is False


def test_no_majority_and_judged_unclear_are_distinguishable_end_to_end():
    """Both print as `unclear_family`; the sub-reason is the only thing separating a tie
    from a judge that answered `unclear` twice. Untested, this was mutation-dead."""
    records_tie = [
        {"severity_unit_id": "u", "replicate": 1, "family": "near"},
        {"severity_unit_id": "u", "replicate": 2, "family": "cross"},
    ]
    records_unclear = [
        {"severity_unit_id": "u", "replicate": 1, "family": "unclear"},
        {"severity_unit_id": "u", "replicate": 2, "family": "unclear"},
    ]
    assert family_aggregation(records_tie)["u"]["no_majority"] is True
    assert family_aggregation(records_unclear)["u"]["no_majority"] is False
    assert family_aggregation(records_unclear)["u"]["category"] == "unclear_family"


def test_an_identical_request_is_answered_once_and_the_reuse_is_recorded():
    """Three product rows from one call build a byte-identical near/cross request, and
    paying for the same question three times is the inflation `comparison_clusters` exists
    to prevent on the inference side."""
    gt = [
        _rec("1", "0810000001", product, "save", {"down sell not success"})
        for product in ("postpaid", "tol", "tvs")
    ]
    inc = [
        _rec("1", "0810000001", product, "save", {"save cost"})
        for product in ("postpaid", "tol", "tvs")
    ]
    testset = _FakeTestSet(
        [
            _FakeItem(
                "RET-13", "1", "0810000001", "บทสนทนา",
                {"rule_reason:down sell not success": "prompt.py:4375-4376"},
            )
        ]
    )
    client = _FakeClient()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": list(gt)},
        dimensions=["reason"],
        client=client,
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=2,
    )
    assert report["judged"]["units_judged"] == 3      # three rows, three units
    assert len(client.calls) == 2                     # two replicates, paid once each
    assert report["judged"]["calls_made"] == 2
    assert report["judged"]["reused_answers"] == 4
    assert report["judged"]["call_rows"] == 6
    assert len({c["request_sha256"] for c in report["calls"]}) == 1


def test_the_severity_unit_id_survives_the_shareable_guard_the_cli_applies():
    """`assert_shareable_payload` exempts `judgment_unit_id` from its phone-like check but
    had no `sv_` sibling, so ~0.6% of ids were rejected as customer phone numbers -- a
    ~47% chance of aborting a 100-unit export, raised AFTER every call was paid for."""
    from evalgen.artifacts import assert_shareable_payload
    from evalgen.severity import shareable_severity_report

    # a real digest whose hex contains a Thai-phone-like run
    assert_shareable_payload({"severity_unit_id": "sv_8468e28b824b086976767e30"})
    gt, inc, cand, testset = _real_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason", "call_result"],
        deterministic_only=True,
    )
    assert_shareable_payload(shareable_severity_report(report))


def test_no_model_written_label_text_reaches_the_shareable_export():
    """The one category whose labels are, by definition, strings a MODEL wrote."""
    from evalgen.artifacts import assert_shareable_payload
    from evalgen.severity import shareable_severity_report

    leak = "customer somchai jaidee 081-000-0123"
    gt = [_rec("1", "0810000001", "postpaid", "save", {"save cost"})]
    inc = [_rec("1", "0810000001", "postpaid", "save", {"save cost", leak})]
    testset = _FakeTestSet([_FakeItem("RET-01", "1", "0810000001", "t", {})])
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": list(gt)},
        dimensions=["reason"],
        deterministic_only=True,
    )
    assert report["units"][0]["category"] == "fabricated_class"
    assert report["units"][0]["fabricated_labels"] == [leak]  # kept, but restricted
    safe = shareable_severity_report(report)
    serialized = json.dumps(safe, ensure_ascii=False)
    assert leak not in serialized
    assert "somchai" not in serialized
    assert safe["units"][0]["fabricated_label_count"] == 1
    assert safe["units"][0]["sub_reason"] == "outside_vocabulary"
    assert_shareable_payload(safe)


def test_an_unresolvable_citation_never_carries_the_absolute_root_into_an_export():
    """Dropping `rule_text.root` was not enough: `resolve_citations_dedup` formatted the
    same absolute path into every unresolved fragment, which ships in the export AND goes
    over the wire inside the prompt."""
    from evalgen.severity import shareable_severity_report

    empty_root = ROOT / "tests" / "fixtures"  # a real directory with no mapped files
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        deterministic_only=True,
        rule_source_root=empty_root,
    )
    fragments = report["rule_text"]["unresolved_fragments"]
    assert fragments, "the test is vacuous unless something failed to resolve"
    serialized = json.dumps(shareable_severity_report(report), ensure_ascii=False)
    assert str(empty_root) not in serialized
    assert not any(str(empty_root) in fragment for fragment in fragments)


def test_an_mnp_pack_is_scored_against_the_mnp_vocabulary():
    """The 12th legal MNP reason class was reported as `fabricated_class` -- and because
    the hard cap sits above the subset tests, that also suppressed the correct
    `over_labelling`, the exact inversion the c11 ordering exists to prevent."""
    twelfth = "true point, dtac reward"
    gt = [_rec("1", "0810000001", "postpaid", "save", {"network"})]
    inc = [_rec("1", "0810000001", "postpaid", "save", {"network", twelfth})]

    class _MnpTestSet(_FakeTestSet):
        app = "mnp"

    testset = _MnpTestSet([_FakeItem("MNP-01", "1", "0810000001", "t", {})])
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": list(gt)},
        dimensions=["reason"],
        deterministic_only=True,
    )
    assert report["units"][0]["category"] == "over_labelling"


def test_an_unknown_app_refuses_rather_than_guessing_a_vocabulary():
    from evalgen.testsets import TestsetError

    class _AlienTestSet(_FakeTestSet):
        app = "not-an-app"

    gt = [_rec("1", "0810000001", "postpaid", "save", {"network"})]
    inc = [_rec("1", "0810000001", "postpaid", "save", {"network", "other"})]
    testset = _AlienTestSet([_FakeItem("X-01", "1", "0810000001", "t", {})])
    with pytest.raises(TestsetError):
        run_severity(
            testset=testset,
            gt=gt,
            arms={"incumbent": inc, "candidate": list(gt)},
            dimensions=["reason"],
            deterministic_only=True,
        )


# ------------------------------------ second review pass over the fixes (2026-08-09)


def _gate_passing_response():
    source = (
        RULE_ROOT / "sentiment-batch-retention-main" / "src" / "prompt.py"
    ).read_text(encoding="utf-8").splitlines()
    return json.dumps(
        {
            "family": "near",
            "cited_span": "บทสนทนา",
            "cited_rule_line": source[4374],
            "rationale": "adjacent",
        },
        ensure_ascii=False,
    )


def test_a_response_that_failed_an_evidence_gate_never_votes_either():
    """The first version of the usable-call filter excluded transport errors and claimed
    parity with `judge.summarize_judgments`, which excludes `parse_error` too. It mattered:
    the 2026-08-09 run rejected 57% of responses at the gates, so under the incomplete
    filter most units were decided by responses that demonstrated nothing."""

    class _MostlyGarbageClient:
        def __init__(self):
            self.attempt = 0

        def complete(self, **kwargs):
            self.attempt += 1
            if self.attempt == 1:
                return _FakeCompletion(_gate_passing_response())
            return _FakeCompletion("}{ not json at all")

    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_MostlyGarbageClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=3,
    )
    unit = report["units"][0]
    assert unit["category"] == "near_family"   # the one demonstrated opinion wins
    assert unit["family_votes"] == "1/1"
    assert unit["flipped"] is False            # two rejected replies are not a flip
    assert report["judged"]["invalid_responses"] == 2
    assert report["judged"]["calls_made"] == 3


def test_a_unit_whose_every_response_failed_a_gate_is_not_reported_as_judged():
    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_FakeClient(family="near", cited_span="never in the transcript"),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=2,
    )
    assert report["units"][0]["category"] == "unclear_family"
    assert report["units"][0]["sub_reason"] == "no_usable_response"
    assert report["units"][0]["judged"] is False
    assert report["judged"]["invalid_responses"] == 2


def test_gate_counters_count_calls_not_reused_rows():
    """Memoisation put one answer on several rows and re-created the double count the
    transport fix had just removed: one bad answer on a three-product cluster reported as
    three gate failures, checked against a goal-contract gate that reads the count."""

    class _MismatchedGarbageClient:
        def complete(self, **kwargs):
            completion = _FakeCompletion("not json at all")
            completion.observed_model = "some-other-model"
            return completion

    gt = [
        _rec("1", "0810000001", product, "save", {"down sell not success"})
        for product in ("postpaid", "tol", "tvs")
    ]
    inc = [
        _rec("1", "0810000001", product, "save", {"save cost"})
        for product in ("postpaid", "tol", "tvs")
    ]
    testset = _FakeTestSet(
        [
            _FakeItem(
                "RET-13", "1", "0810000001", "บทสนทนา",
                {"rule_reason:down sell not success": "prompt.py:4375-4376"},
            )
        ]
    )
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": list(gt)},
        dimensions=["reason"],
        client=_MismatchedGarbageClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=2,
    )
    assert report["judged"]["calls_made"] == 2        # two real HTTP calls
    assert report["judged"]["call_rows"] == 6         # six rows, four of them copies
    assert report["judged"]["invalid_responses"] == 2  # not 6
    assert report["judged"]["invalid_response_rate"] == pytest.approx(1.0)
    assert report["judged"]["identity_not_matched"] == 2  # not 6


def test_a_provenance_digest_never_aborts_a_shareable_export_after_the_calls_are_paid():
    """`assert_shareable_payload` exempted `*_sha256` but every provenance digest here is
    keyed `*_sha`, and `judge_runtime.fingerprint` is a bare digest. A 64-hex value trips
    the phone-like check 2.08% of the time and an export carries 5-11 of them, so 10-21%
    of exports raised -- after every call was paid for, outside the CliError path."""
    from evalgen.artifacts import assert_shareable_payload, ArtifactError

    phone_like = "d274bf120808ab1e1b750adc32a07811413b3580df12611dea305b0807650402"
    assert_shareable_payload({"incumbent_manifest_sha": phone_like})
    assert_shareable_payload({"fingerprint": phone_like})
    assert_shareable_payload({"testset_sha": phone_like})
    # the exemption is still exact: a malformed pseudo-digest is not waved through
    with pytest.raises(ArtifactError):
        assert_shareable_payload({"testset_sha": "0807650402"})
    with pytest.raises(ArtifactError):
        assert_shareable_payload({"testset_sha": phone_like + "extra"})
    with pytest.raises(ArtifactError):
        assert_shareable_payload({"note_sha": "call 0807650402 back"})


def test_a_model_invented_json_key_never_reaches_the_shareable_export():
    """`validation_errors` quotes the model's own key names, which is model-written text by
    the same argument that removed the label fields."""
    from evalgen.artifacts import assert_shareable_payload
    from evalgen.severity import shareable_severity_report

    invented = "note_for_customer_Somchai_Jaidee_acct_A-8891"

    class _ChattyClient:
        def complete(self, **kwargs):
            return _FakeCompletion(
                json.dumps(
                    {
                        "family": "near",
                        "cited_span": "บทสนทนา",
                        "cited_rule_line": "x",
                        "rationale": "y",
                        invented: "see above",
                    },
                    ensure_ascii=False,
                )
            )

    gt, inc, cand, testset = _ruled_world()
    report = run_severity(
        testset=testset,
        gt=gt,
        arms={"incumbent": inc, "candidate": cand},
        dimensions=["reason"],
        client=_ChattyClient(),
        model="google/gemma-4-31b-it",
        provider="CoreWeave",
        rule_source_root=RULE_ROOT,
        repeats=1,
    )
    assert any(invented in error for error in report["calls"][0]["validation_errors"])
    safe = shareable_severity_report(report)
    serialized = json.dumps(safe, ensure_ascii=False)
    assert invented not in serialized
    assert "Somchai" not in serialized
    assert safe["calls"][0]["validation_error_count"] >= 1
    assert_shareable_payload(safe)
