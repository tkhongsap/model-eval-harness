"""The testset loader and its validator.

The tests that matter here are the ones where `validate` **rejects** something. A
validator that has only ever been watched to pass is indistinguishable from
`return []`, and this file's whole subject is a check whose failure mode is silence:
an evidence span that drifts away from its transcript breaks nothing, prints nothing,
and leaves a testset that still scores, still reports, and no longer measures what it
says it measures.

So every rule gets a pair — the valid item passes, the corrupted one is named — and
each corruption is the kind that actually happens: a real production keyword pasted
onto the wrong item, one wrong tone mark, a transcript edited after the labels were
written.

The three fixture items are real, in the sense that matters: synthetic ids, spoken-
register Thai, and every label carrying a verbatim span plus a production citation.
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.testsets import (  # noqa: E402
    PHONE_PATTERN,
    TestsetError,
    load_testset,
    testset_sha,
    validate,
)

TESTSETS = ROOT / "tests" / "fixtures" / "testsets"

# ---------------------------------------------------------------------------
# Fixtures: three items, each a claim with evidence and a citation.
# ---------------------------------------------------------------------------

# Explicit network complaint, explicit porting-code request. The agent's counter-offer
# arrives AFTER the request, which is the trap: it is an offer, not the customer's
# decision (prompt.py:4335 makes the same point for the reason dimension).
ITEM_NETWORK_CHURN = {
    "item_id": "ret-0001-network-churn",
    "call_id": "5001",
    "family": "single-reason",
    "transcript_th": "\n".join(
        [
            "ลูกค้า: คือเอ่อ... พี่ใช้รายเดือนอยู่เนี่ย เน็ตช้ามากอ่ะ",
            "พนักงาน: ต้องขออภัยด้วยนะคะ รบกวนขอเบอร์ที่ใช้งานหน่อยค่ะ",
            "ลูกค้า: ใช้มาสามปีแล้วมั้ง เอ่อ... ไม่ใช่ สี่ปี",
            "ลูกค้า: ไม่ไหวแล้วอ่ะ ขอรหัสย้ายค้ายเลยคะ",
            "พนักงาน: เดี๋ยวมีโปรใหม่เสนอให้ได้มั้ยคะ ลองฟังก่อนได้ไหมคะ",
            "ลูกค้า: ไม่เอาแล้วค่ะ พอแล้ว",
        ]
    ),
    "phone_number": "0810000001",
    "mechanism": (
        "One reason, stated plainly, followed by an explicit porting-code request. The "
        "agent's retention offer lands after the decision and is refused."
    ),
    "why_it_matters": (
        "The floor case for the reason and call_result dimensions. If a model misses "
        "this one, nothing further about it is worth measuring."
    ),
    "gt": [
        {
            "product": "postpaid",
            "call_result": "churn",
            "main": "network",
            "secondary": None,
            "third": None,
        }
    ],
    "evidence": {
        "ev_product:postpaid": "รายเดือน",
        "ev_call_result:churn": "ขอรหัสย้ายค้าย",
        "ev_reason:network": "เน็ตช้า",
    },
    "rules": {
        "rule_product:postpaid": "prompt.py:4321; main.py:1037",
        "rule_call_result:churn": "prompt.txt:59; prompt.py:4390-4392",
        "rule_reason:network": "prompt.txt:4; prompt.py:4330",
    },
    "expected_failure": (
        "A model that reads the agent's counter-offer as the outcome and returns save, "
        "or that treats staying-with-True as save when no such thing was said."
    ),
}

# Postpaid to prepaid. The customer never leaves True, and the outcome is still churn
# (prompt.py:4392) because the postpaid contract is lost. The single most citable
# outcome rule in the app, and the one a model reasoning from first principles gets
# wrong every time.
ITEM_POST_TO_PRE = {
    "item_id": "ret-0002-post-to-pre",
    "call_id": "5002",
    "family": "outcome-rule",
    "transcript_th": "\n".join(
        [
            "ลูกค้า: คือ เอ่อ... หนูอยากเปลี่ยนเป็นเติมเงินอ่ะค่ะ",
            "พนักงาน: รายเดือนของพี่มีโปรดีอยู่นะคะ ลองฟังก่อนได้มั้ยคะ",
            "ลูกค้า: ไม่เอาค่ะ พี่อยากใช้เติมเงิน จ่ายรายเดือนแล้วมันเกินตลอดเนี่ย",
            "พนักงาน: เดี๋ยวลดให้ได้ไหมคะ เอ่อ... ลดได้ประมาณนึงค่ะ",
            "ลูกค้า: ไม่ล่ะคะ ตัดสินใจแล้ว",
        ]
    ),
    "phone_number": "0810000002",
    "mechanism": (
        "The customer stays with the brand and the label is still churn, because the "
        "postpaid contract is what was lost."
    ),
    "why_it_matters": (
        "Separates a model applying prompt.py:4392 from one reasoning about customer "
        "loyalty. Both produce a confident answer; only one is right."
    ),
    "gt": [
        {
            "product": "postpaid",
            "call_result": "churn",
            "main": "post to pre",
            "secondary": None,
            "third": None,
        }
    ],
    "evidence": {
        "ev_product:postpaid": "รายเดือน",
        "ev_call_result:churn": "ไม่เอาค่ะ พี่อยากใช้เติมเงิน",
        "ev_reason:post to pre": "อยากเปลี่ยนเป็นเติมเงิน",
    },
    "rules": {
        "rule_product:postpaid": "prompt.py:4321; main.py:1037",
        "rule_call_result:churn": "prompt.txt:59; prompt.py:4392",
        "rule_reason:post to pre": "prompt.txt:32; prompt.py:4367-4369",
    },
    "expected_failure": (
        "A model that returns save because the customer remains a True subscriber, "
        "missing that postpaid-to-prepaid is defined as churn."
    ),
}

# Not a retention call at all: undefined, not unknown. `unknown` means retention was in
# play and never settled; `undefined` means it was never in play (prompt.py:4399).
# Null phone on purpose — production's product groupby drops it (fact_checker.py:1080).
ITEM_NOT_RETENTION = {
    "item_id": "ret-0003-not-retention",
    "call_id": "5003",
    "family": "out-of-scope",
    "transcript_th": "\n".join(
        [
            "ลูกค้า: เอ่อ สวัสดีค่ะ คือหนูอยากถามว่า สาขาเปิดกี่โมงคะ",
            "พนักงาน: สาขาไหนดีคะ เอ่อ... สาขาที่พี่จะไปน่ะค่ะ",
            "ลูกค้า: แถวๆ บ้านอ่ะ เซ็นทรัลน่ะ เอ่อ... ไม่ใช่ โลตัสมากกว่ามั้ง",
            "พนักงาน: เปิดสิบโมงถึงสองทุ่มค่ะ",
            "ลูกค้า: โอเคค่ะ ขอบคุณคะ",
        ]
    ),
    "phone_number": None,
    "mechanism": (
        "No porting code, no cancellation, no downgrade, so the agent had nothing to "
        "save and no product was ever identified."
    ),
    "why_it_matters": (
        "undefined and unknown are the pair most often collapsed. A model that reaches "
        "for unknown whenever no decision was stated will fail exactly here."
    ),
    "gt": [
        {
            "product": "unknown",
            "call_result": "undefined",
            "main": None,
            "secondary": None,
            "third": None,
        }
    ],
    "evidence": {
        "ev_product:unknown": "สาขาเปิดกี่โมง",
        "ev_call_result:undefined": "สาขาเปิดกี่โมง",
    },
    "rules": {
        "rule_product:unknown": "prompt.py:4324; main.py:1040",
        "rule_call_result:undefined": "prompt.py:4399; main.py:1018",
    },
    "expected_failure": (
        "A model that returns unknown because the customer stated no decision, instead "
        "of undefined because there was no decision to state."
    ),
}

ALL_ITEMS = (ITEM_NETWORK_CHURN, ITEM_POST_TO_PRE, ITEM_NOT_RETENTION)


def write_testset(tmp_path: Path, items, name: str = "retention-v1.jsonl") -> Path:
    """Write records as UTF-8 JSONL with LF endings and no BOM.

    `write_bytes`, not `write_text`: on Windows the text path would translate every
    \\n into \\r\\n and the loader would correctly refuse its own fixture.
    """
    path = tmp_path / name
    body = "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in items)
    path.write_bytes(body.encode("utf-8"))
    return path


def load(tmp_path: Path, items, *, name: str = "retention-v1.jsonl", **kwargs):
    return load_testset(write_testset(tmp_path, items, name=name), **kwargs)


def mutate(item: dict, **changes) -> dict:
    """A deep copy with fields replaced, so no test can corrupt another's fixture."""
    clone = copy.deepcopy(item)
    clone.update(changes)
    return clone


# ---------------------------------------------------------------------------
# The baseline. Everything below is "and now break it".
# ---------------------------------------------------------------------------


def test_valid_testset_has_no_problems(tmp_path):
    """The three fixtures are clean. Every rejection test below depends on this."""
    problems = validate(load(tmp_path, ALL_ITEMS))
    assert problems == [], "\n".join(problems)


def test_load_round_trips_every_field(tmp_path):
    ts = load(tmp_path, ALL_ITEMS)
    assert ts.name == "retention-v1"
    assert ts.app == "retention"
    assert len(ts.items) == 3

    item = ts.items[0]
    assert item.item_id == "ret-0001-network-churn"
    assert item.call_id == "5001"
    assert item.phone_number == "0810000001"
    assert item.gt == (
        {
            "product": "postpaid",
            "call_result": "churn",
            "main": "network",
            "secondary": None,
            "third": None,
        },
    )
    assert ts.items[2].phone_number is None


def test_thai_survives_the_round_trip_byte_for_byte(tmp_path):
    """If the encoding path mangled anything, the evidence check would be checking a
    corrupted transcript against a corrupted span and could still pass."""
    ts = load(tmp_path, ALL_ITEMS)
    for loaded, source in zip(ts.items, ALL_ITEMS):
        assert loaded.transcript_th == source["transcript_th"]
        assert loaded.evidence == source["evidence"]


# ---------------------------------------------------------------------------
# The evidence check. This is the one that earns the module.
# ---------------------------------------------------------------------------


def test_corrupted_evidence_span_is_rejected(tmp_path):
    """A real production keyword, pasted onto an item whose transcript never says it.

    The most likely way this testset rots: someone writes labels first, borrows a span
    from `prompt.txt:4` because it fits the label, and never checks it against the
    transcript in front of them. `สัญญาณไม่เสถียร` is a legitimate `network` keyword and
    would justify the label on some other item. It is not in this one.
    """
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["evidence"]["ev_reason:network"] = "สัญญาณไม่เสถียร"

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 1, problems
    assert "ret-0001-network-churn" in problems[0]
    assert "ev_reason:network" in problems[0]
    assert "verbatim" in problems[0]


def test_one_wrong_tone_mark_is_rejected(tmp_path):
    """`เน้ตช้า` versus `เน็ตช้า` — ไม้โท where ไม้ไต่คู้ belongs.

    Indistinguishable at a glance in a review, and a substring test catches it every
    time. This is why the check is mechanical rather than editorial.
    """
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["evidence"]["ev_reason:network"] = "เน้ตช้า"

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 1, problems
    assert "ret-0001-network-churn" in problems[0]


def test_editing_the_transcript_away_from_the_span_is_rejected(tmp_path):
    """The same drift from the other direction: labels untouched, transcript reworded.

    Softening `เน็ตช้ามาก` to `เน็ตอืด` reads like an improvement to the Thai and
    silently detaches the only thing supporting the `network` label.
    """
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["transcript_th"] = corrupted["transcript_th"].replace("เน็ตช้ามาก", "เน็ตอืด")

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 1, problems
    assert "ev_reason:network" in problems[0]


def test_empty_evidence_span_is_rejected(tmp_path):
    """`"" in text` is True, so the empty span is the one input that would slip past a
    naive substring check while asserting nothing whatsoever."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["evidence"]["ev_reason:network"] = ""

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 1, problems
    assert "empty" in problems[0]
    assert "ev_reason:network" in problems[0]


def test_whitespace_only_evidence_span_is_rejected(tmp_path):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["evidence"]["ev_reason:network"] = "   "

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 1, problems
    assert "empty" in problems[0]


def test_evidence_for_an_unscored_dimension_is_still_checked(tmp_path):
    """`issue_type` has no gt column and is not scored (fact_checker.py:1095-1099), but
    a documented span that is not in the transcript is wrong in the same way."""
    ok = mutate(ITEM_NETWORK_CHURN)
    ok["evidence"]["ev_issue_type:speed"] = "เน็ตช้า"
    ok["rules"]["rule_issue_type:speed"] = "prompt.txt:110"
    assert validate(load(tmp_path, [ok])) == []

    bad = mutate(ITEM_NETWORK_CHURN)
    bad["evidence"]["ev_issue_type:speed"] = "เน็ตหลุดบ่อย"
    bad["rules"]["rule_issue_type:speed"] = "prompt.txt:110"

    problems = validate(load(tmp_path, [bad], name="b.jsonl"))
    assert len(problems) == 1, problems
    assert "ev_issue_type:speed" in problems[0]


def test_every_corrupted_span_is_reported_not_just_the_first(tmp_path):
    """A reviewer fixing one problem per run is a reviewer who stops reviewing."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["evidence"]["ev_reason:network"] = "ไม่มีสัญญาณ"
    corrupted["evidence"]["ev_call_result:churn"] = "ขอรหัส PIN"

    problems = validate(load(tmp_path, [corrupted]))

    assert len(problems) == 2, problems
    assert {"ev_call_result:churn", "ev_reason:network"} == {
        key for key in ("ev_call_result:churn", "ev_reason:network")
        if any(key in problem for problem in problems)
    }


# ---------------------------------------------------------------------------
# Synthetic-identifier gates. No real customer data, enforced.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("call_id", ["1234", "50001", "500", "5a01", "", "6001"])
def test_call_id_outside_the_synthetic_range_is_rejected(tmp_path, call_id):
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, call_id=call_id)]))
    assert any("call_id" in problem for problem in problems), problems


@pytest.mark.parametrize("phone", ["0812345678", "08100000", "081000000", "0810000001x", ""])
def test_phone_outside_the_synthetic_block_is_rejected(tmp_path, phone):
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number=phone)]))
    assert any("phone_number" in problem for problem in problems), problems


def test_null_phone_is_allowed(tmp_path):
    """Legal, and load-bearing: a null phone drops the row out of the product metric
    (fact_checker.py:1080), which is a property worth being able to test."""
    assert validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number=None)])) == []


def test_numeric_phone_is_refused_at_load(tmp_path):
    """A JSON number would drop the leading zero before anything could check it."""
    with pytest.raises(TestsetError, match="phone_number must be a string"):
        load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number=810000001)])


# ---------------------------------------------------------------------------
# The 2026-08-06 widening: 08100000xx -> 0810000xxx.
#
# The old block was full — all 100 numbers it could spell were in use, all 100 of them
# by `retention_v2.jsonl`. The tests above are deliberately untouched by the widening:
# every negative case in `test_phone_outside_the_synthetic_block_is_rejected` still
# fails for the same reason it always did, and needing no edit there is what makes this
# change reviewable. The tests below add the new boundary, they do not move the old one.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("phone", ["0810000100", "0810000999"])
def test_the_widened_block_accepts_its_new_numbers(tmp_path, phone):
    """The 900 numbers the widening bought. `0810000100` is the first of them and
    `0810000999` the last, so between them they pin both ends of what was added.

    Asserting `== []` rather than "no phone problem": a number inside the block must
    leave the item completely clean, otherwise the widening has bought capacity that
    nothing can actually use.
    """
    assert validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number=phone)])) == []


@pytest.mark.parametrize("phone", ["0810001000", "0810000"])
def test_numbers_outside_the_widened_block_are_still_rejected(tmp_path, phone):
    """A wider block is still a block, and these are the two ways out of it.

    `0810001000` is the number immediately after the widened range and the one a
    generator that counts past 999 reaches first — it is ten digits, it starts `08100`,
    and it is outside. `0810000` is the block's own prefix with the tail missing, which
    is what a truncated write produces. Both would be accepted by a widening done as
    `^0810000[0-9]*$`, which is the mistake this test exists to catch.
    """
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number=phone)]))
    assert any("phone_number" in problem for problem in problems), problems


def test_the_widening_kept_every_number_the_old_block_could_spell():
    """The strict-superset property, enumerated rather than argued.

    The whole reason this widening was safe to make against frozen fixtures is that
    `0810000` + `001` is the same string as `08100000` + `01`. Enumerating all 100 of
    the old block proves it for every member instead of for the one example anyone
    would think to check by hand.
    """
    old_block = ["08100000%02d" % n for n in range(100)]
    rejected = [number for number in old_block if not PHONE_PATTERN.fullmatch(number)]
    assert rejected == [], (
        f"the widened pattern no longer accepts {len(rejected)} number(s) the old block "
        f"could spell, e.g. {rejected[:3]}. Every one of them is in a frozen fixture."
    )


def test_the_committed_packs_still_fit_the_block_and_leave_headroom():
    """The alarm that would have caught the exhaustion before it blocked anyone.

    MEASURED 2026-08-06: `retention_v1.jsonl` and `retention_v2.jsonl` hold 100 distinct
    phone numbers between them, which was 100/100 of the old block and is 100/1000 of
    the widened one. The point is not the exact count — packs grow — it is that running
    out is a thing that happens silently until an author cannot write the next item.
    This test says so out loud while there is still room to act on it.
    """
    used = set()
    for name in ("retention_v1.jsonl", "retention_v2.jsonl"):
        for item in load_testset(TESTSETS / name, app="retention").items:
            if item.phone_number is not None:
                used.add(item.phone_number)

    outside = sorted(n for n in used if not PHONE_PATTERN.fullmatch(n))
    assert outside == [], f"committed packs use phone numbers outside the block: {outside}"

    capacity = 1000  # 0810000000 .. 0810000999
    assert len(used) < capacity, (
        f"the synthetic phone block is EXHAUSTED: {len(used)} of {capacity} in use. "
        "Widen it the same way it was widened on 2026-08-06 — one digit of prefix "
        "traded for one digit of tail, so the change stays a strict superset and no "
        "committed fixture has to move."
    )


def test_the_rejection_message_names_the_pattern_actually_enforced(tmp_path):
    """The message is the only place a reviewer learns what a legal number looks like.

    It used to retype the regex, which meant the widening could have left it telling
    authors to write numbers the validator had already stopped requiring. It now quotes
    `PHONE_PATTERN`; this test is what keeps that true.
    """
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, phone_number="0899999999")]))

    assert len(problems) == 1, problems
    assert PHONE_PATTERN.pattern in problems[0], problems[0]
    assert "08100000[0-9]{2}" not in problems[0], "the message still quotes the old block"


def test_empty_transcript_is_rejected(tmp_path):
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, transcript_th="   ")]))
    assert any("transcript_th is empty" in problem for problem in problems), problems


def test_duplicate_item_id_is_rejected(tmp_path):
    problems = validate(load(tmp_path, [ITEM_NETWORK_CHURN, copy.deepcopy(ITEM_NETWORK_CHURN)]))
    assert len(problems) == 1, problems
    assert "duplicate item_id" in problems[0]
    assert "ret-0001-network-churn" in problems[0]


# ---------------------------------------------------------------------------
# gt shape, and the rule/evidence pairing.
# ---------------------------------------------------------------------------


def test_gt_row_missing_a_column_is_rejected(tmp_path):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    del corrupted["gt"][0]["third"]

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "missing ['third']" in problems[0]


def test_gt_row_with_an_extra_column_is_rejected(tmp_path):
    """`retention_outcome` is what the model emits; the scored column is `call_result`
    (fact_checker.py:622). Writing both is how the rename gets scored twice."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["gt"][0]["retention_outcome"] = "churn"

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "unexpected ['retention_outcome']" in problems[0]


def test_empty_gt_is_rejected(tmp_path):
    problems = validate(load(tmp_path, [mutate(ITEM_NETWORK_CHURN, gt=[])]))
    assert any("asserts nothing" in problem for problem in problems), problems


def test_two_rows_for_one_product_are_rejected(tmp_path):
    """The scored row is (call_id, phone_number, product) (fact_checker.py:1075)."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["gt"].append(copy.deepcopy(corrupted["gt"][0]))
    corrupted["gt"][1]["call_result"] = "save"

    problems = validate(load(tmp_path, [corrupted]))
    assert any("both claim product" in problem for problem in problems), problems


def test_label_without_a_rule_is_rejected(tmp_path):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    del corrupted["rules"]["rule_reason:network"]

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "rule_reason:network" in problems[0]
    assert "no rules entry" in problems[0]


def test_label_without_evidence_is_rejected(tmp_path):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    del corrupted["evidence"]["ev_call_result:churn"]

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "ev_call_result:churn" in problems[0]
    assert "no evidence entry" in problems[0]


def test_a_comma_split_cell_owes_a_rule_per_label(tmp_path):
    """`get_reasons_set` splits cells on commas (fact_checker.py:877), so one cell can
    be two labels — and a rule covering "the cell" covers neither of them."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["gt"][0]["main"] = "network, save cost"

    problems = validate(load(tmp_path, [corrupted]))

    assert any("reason:save cost" in problem and "no rules entry" in problem for problem in problems), problems
    assert any("reason:save cost" in problem and "no evidence entry" in problem for problem in problems), problems
    assert not any("reason:network" in problem for problem in problems), problems


def test_a_fully_cited_second_reason_passes(tmp_path):
    """The same multi-label cell, done properly. Proves the check above rejects for the
    stated reason and not because two reasons are unsupported in principle."""
    ok = mutate(ITEM_NETWORK_CHURN)
    ok["transcript_th"] += "\nลูกค้า: แล้วก็ เอ่อ... อยากลดค่าใช้จ่ายด้วยอ่ะค่ะ"
    ok["gt"][0]["main"] = "network, save cost"
    ok["evidence"]["ev_reason:save cost"] = "อยากลดค่าใช้จ่าย"
    ok["rules"]["rule_reason:save cost"] = "prompt.txt:16; prompt.py:4342-4345"

    assert validate(load(tmp_path, [ok])) == []


def test_key_casing_does_not_matter(tmp_path):
    """Production folds case on every string column before scoring
    (fact_checker.py:732-739), so `Postpaid` and `postpaid` name one label and neither
    spelling is a validation failure."""
    ok = mutate(ITEM_NETWORK_CHURN)
    ok["gt"][0]["product"] = "Postpaid"
    ok["evidence"]["ev_Product:Postpaid"] = ok["evidence"].pop("ev_product:postpaid")
    ok["rules"]["rule_Product:Postpaid"] = ok["rules"].pop("rule_product:postpaid")

    assert validate(load(tmp_path, [ok])) == []


# ---------------------------------------------------------------------------
# The label space is closed. A near miss is a wrong label, not a typo.
# ---------------------------------------------------------------------------


def test_undefine_without_the_d_is_rejected(tmp_path):
    """D3: `prompt.txt:63` spells it `undefine`; the schema and the scorer both spell it
    `undefined` (main.py:1018, fact_checker.py:791). Copying the prompt's spelling into
    ground truth scores as a wrong label on every row."""
    corrupted = mutate(ITEM_NOT_RETENTION)
    corrupted["gt"][0]["call_result"] = "undefine"
    corrupted["evidence"]["ev_call_result:undefine"] = corrupted["evidence"].pop(
        "ev_call_result:undefined"
    )
    corrupted["rules"]["rule_call_result:undefine"] = corrupted["rules"].pop(
        "rule_call_result:undefined"
    )

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "not in the retention label space" in problems[0]


def test_prepaid_is_not_a_product(tmp_path):
    """D5: `Prepaid` belongs to `product_type` in prompt.txt:239-250, a field the
    retention schema does not have. A customer moving to prepaid is labelled under the
    product they are leaving."""
    corrupted = mutate(ITEM_POST_TO_PRE)
    corrupted["gt"][0]["product"] = "prepaid"
    corrupted["evidence"]["ev_product:prepaid"] = corrupted["evidence"].pop("ev_product:postpaid")
    corrupted["rules"]["rule_product:prepaid"] = corrupted["rules"].pop("rule_product:postpaid")

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "not in the retention label space" in problems[0]


def test_an_invented_reason_is_rejected(tmp_path):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["gt"][0]["main"] = "billing dispute"
    corrupted["evidence"]["ev_reason:billing dispute"] = "เน็ตช้า"
    corrupted["rules"]["rule_reason:billing dispute"] = "prompt.txt:4"

    problems = validate(load(tmp_path, [corrupted]))
    assert len(problems) == 1, problems
    assert "billing dispute" in problems[0]


def test_the_twelfth_prompt_only_reason_is_rejected(tmp_path):
    """D1/D10: `true point, dtac reward` exists in prompt.txt:38-40 and in no label
    space the retention scorer measures — and it contains a comma, so the scorer's own
    cell-splitting shreds it into two labels that are in nothing."""
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["gt"][0]["main"] = "true point, dtac reward"

    problems = validate(load(tmp_path, [corrupted]))
    assert any("true point" in problem for problem in problems), problems
    assert any("dtac reward" in problem for problem in problems), problems


def test_mnp_label_space_admits_its_twelfth_reason(tmp_path):
    """The reason spaces differ by exactly one class, and that difference is data
    (labelspaces.py), so pointing the loader at the other app changes what validates."""
    item = mutate(ITEM_NETWORK_CHURN)
    item["gt"][0]["main"] = "true point"
    item["evidence"]["ev_reason:true point"] = "เน็ตช้า"
    item["rules"]["rule_reason:true point"] = "prompt.txt:40"

    assert any("retention label space" in p for p in validate(load(tmp_path, [item])))


def test_unknown_app_says_membership_was_not_checked(tmp_path):
    """Silently skipping the membership check would leave a green run that proved less
    than the last one, with nothing on screen to say so."""
    problems = validate(load(tmp_path, ALL_ITEMS, app="telesales"))
    assert len(problems) == 1, problems
    assert "was NOT" in problems[0]


# ---------------------------------------------------------------------------
# Citations have to be checkable by a human.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "citation",
    ["prompt.txt", "the network keyword list", "prompt.txt:", "line 4", "prompt.txt:4; nope"],
)
def test_a_citation_without_a_checkable_line_is_rejected(tmp_path, citation):
    corrupted = mutate(ITEM_NETWORK_CHURN)
    corrupted["rules"]["rule_reason:network"] = citation

    problems = validate(load(tmp_path, [corrupted]))
    assert any("malformed citation" in p or "is empty" in p for p in problems), problems


@pytest.mark.parametrize(
    "citation",
    ["prompt.txt:4", "prompt.py:4390-4392", "main.py:972; fact_checker.py:857-869",
     "config/system_prompt/retention.yml:12"],
)
def test_well_formed_citations_pass(tmp_path, citation):
    ok = mutate(ITEM_NETWORK_CHURN)
    ok["rules"]["rule_reason:network"] = citation
    assert validate(load(tmp_path, [ok])) == []


# ---------------------------------------------------------------------------
# File format: UTF-8, no BOM, LF.
# ---------------------------------------------------------------------------


def test_a_bom_is_rejected_explicitly(tmp_path):
    """Reading with utf-8-sig would make this work and hide that a writer is
    misconfigured; reading with plain utf-8 would name the first key `\\ufeffitem_id`."""
    path = write_testset(tmp_path, [ITEM_NETWORK_CHURN])
    path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())

    with pytest.raises(TestsetError, match="BOM"):
        load_testset(path)


def test_crlf_is_rejected(tmp_path):
    """A CR inside transcript_th changes its bytes invisibly, so an evidence span can
    stop matching for a reason no reviewer can see."""
    path = write_testset(tmp_path, [ITEM_NETWORK_CHURN])
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))

    with pytest.raises(TestsetError, match="carriage return"):
        load_testset(path)


def test_blank_lines_are_skipped(tmp_path):
    path = write_testset(tmp_path, ALL_ITEMS)
    path.write_bytes(b"\n" + path.read_bytes() + b"\n\n")
    assert len(load_testset(path).items) == 3


def test_broken_json_names_the_line(tmp_path):
    path = write_testset(tmp_path, ALL_ITEMS)
    path.write_bytes(path.read_bytes().replace(b'"item_id"', b'"item_id"', 1) + b"{nope}\n")

    with pytest.raises(TestsetError, match=r":4: not valid JSON"):
        load_testset(path)


def test_a_missing_field_is_refused_at_load(tmp_path):
    broken = mutate(ITEM_NETWORK_CHURN)
    del broken["expected_failure"]

    with pytest.raises(TestsetError, match="missing required field"):
        load(tmp_path, [broken])


def test_a_misspelled_field_is_refused_at_load(tmp_path):
    """`evidences` would drop every span on the floor and the item would validate clean,
    having proved nothing.

    A rename trips the missing-field check before the unknown-field one, so the message
    names `evidence` rather than `evidences`. Either way the file does not load, which
    is the property being tested."""
    broken = mutate(ITEM_NETWORK_CHURN)
    broken["evidences"] = broken.pop("evidence")

    with pytest.raises(TestsetError, match="missing required field.*evidence"):
        load(tmp_path, [broken])


def test_an_extra_field_is_refused_at_load(tmp_path):
    """The other half: every required field present, plus one nobody reads. Accepting it
    silently is how a field gets added in one place and consumed in none."""
    broken = mutate(ITEM_NETWORK_CHURN)
    broken["evidence_notes"] = "see the network keyword list"

    with pytest.raises(TestsetError, match="unknown field"):
        load(tmp_path, [broken])


# ---------------------------------------------------------------------------
# Hashing, and the boundary.
# ---------------------------------------------------------------------------


def test_sha_is_stable_and_content_addressed(tmp_path):
    a = write_testset(tmp_path, ALL_ITEMS, name="a.jsonl")
    b = write_testset(tmp_path, ALL_ITEMS, name="b.jsonl")
    assert testset_sha(a) == testset_sha(b)
    assert len(testset_sha(a)) == 64


def test_sha_changes_when_a_single_span_changes(tmp_path):
    """The manifest records which file produced a score. A span edit that left the hash
    alone would make a testset silently unversioned."""
    before = write_testset(tmp_path, ALL_ITEMS, name="before.jsonl")
    drifted = mutate(ITEM_NETWORK_CHURN)
    drifted["evidence"]["ev_reason:network"] = "เน็ตช้ามาก"
    after = write_testset(tmp_path, [drifted, ITEM_POST_TO_PRE, ITEM_NOT_RETENTION], name="after.jsonl")

    assert testset_sha(before) != testset_sha(after)


def test_testsets_module_imports_no_network_library():
    """`evalgen` is allowed a client — it generates. This module does not generate: it
    reads a file and checks strings, and it is imported by everything that inspects a
    testset. Reusing the boundary test's list rather than restating it, so the two
    cannot drift apart."""
    import ast

    from tests.test_boundary import NETWORK_MODULES

    source = ROOT / "src" / "evalgen" / "testsets.py"
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))

    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and not node.level:
            names = [node.module or ""]
        else:
            continue
        offenders += [f"line {node.lineno}: {n}" for n in names if n.split(".")[0] in NETWORK_MODULES]

    assert not offenders, f"src/evalgen/testsets.py imports a networking library: {offenders}"


# ---------------------------------------------------------------------------
# The fixtures are spoken Thai, not written Thai.
# ---------------------------------------------------------------------------


def test_fixture_transcripts_are_spoken_register():
    """LLM-written Thai is too clean to be a call transcript, and a testset of clean Thai
    measures a register the app never sees. Fillers and particles are mandatory."""
    fillers = ("เอ่อ", "คือ")
    particles = ("อ่ะ", "เนี่ย", "ไง", "มั้ง", "น่ะ")

    for item in ALL_ITEMS:
        transcript = item["transcript_th"]
        assert any(f in transcript for f in fillers), f"{item['item_id']}: no filler"
        assert any(p in transcript for p in particles), f"{item['item_id']}: no particle"


def test_fixtures_vary_maiy_spelling_and_confuse_kha():
    """Real speakers are not consistent. A corpus that spells มั้ย the same way every
    time, and never fumbles คะ/ค่ะ, is a corpus a model can pattern-match."""
    corpus = "\n".join(item["transcript_th"] for item in ALL_ITEMS)
    assert "มั้ย" in corpus and "ไหม" in corpus
    assert "ขอบคุณคะ" in corpus or "เลยคะ" in corpus  # คะ where ค่ะ belongs


def test_fixture_turns_stay_short():
    """Turns are capped at roughly 25 Thai words. Thai is not space-delimited, so this
    checks characters as a proxy — ~4-5 characters per word — and says so rather than
    implying a word count it cannot measure."""
    for item in ALL_ITEMS:
        for turn in item["transcript_th"].split("\n"):
            assert len(turn) <= 120, f"{item['item_id']}: turn too long ({len(turn)}): {turn}"
