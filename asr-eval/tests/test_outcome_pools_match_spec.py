"""Each `call_result` pool must express what the LABELLING SPEC says that value means.

WHY THIS FILE EXISTS. On 2026-08-20 the corpus and the spec it is scored against disagreed
about `unknown`, and the disagreement was invisible to every other check. `leak_probe`
passed — the label was stated. `validate_audio` passed. The scorer ran clean. And every arm
scored ~0.13 lower than it should have, on a corpus whose own comments argued the labels were
right.

The mechanism: `src/evalgen/prompts/retention_v9_16_body.txt:80` rules that a client who
"expresses indecision or asks for time to think" is a **`save`**, and quotes the Thai phrases
`ขอเวลาคิดก่อน` and `ยังตัดสินใจไม่ได้` while doing it. The corpus's `unknown` pool was built
out of exactly those phrases. A labeller obeying its instructions therefore HAD to answer
`save` on all 15 of those calls, and did, on 174 of 186 blocks. The label was not
unrecoverable; it was anti-recoverable — the better the model followed the spec, the more
reliably it was marked wrong.

`undefined` had the mirror fault: the spec defines it as a call that was never about
retention, and the corpus built it from polite declines of a retention offer, on calls that
also carried a cancellation reason.

So these tests read the SPEC TEXT ITSELF and assert the pools do not contradict it. A future
edit that reintroduces an indecision line into `unknown` fails here rather than silently
costing 0.13 F1 and being explained away as a model limitation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

import thai_corpus as T

REPO = Path(__file__).resolve().parents[2]
SPEC = REPO / "src" / "evalgen" / "prompts" / "retention_v9_16_body.txt"

# The phrases the spec's `save` rule quotes verbatim. Any of these appearing in a pool other
# than `save` means the corpus is labelling against its own scorer.
SAVE_RULE_PHRASES = ("ลังเล", "ขอเวลาคิด", "ยังตัดสินใจไม่ได้")

# Stems that mean the same speech act as the quoted phrases. The spec quotes three examples,
# not an exhaustive list, and "let me think it over" is the same act however it is worded.
INDECISION_STEMS = (
    "ขอคิดดู", "ยังไม่ตัดสินใจ", "ขอปรึกษา", "ยังไม่แน่ใจ", "ขอเวลาคิด",
    "ขอดูรายละเอียดก่อน", "ยังตอบไม่ได้", "ขอเก็บไว้พิจารณา", "ขอเวลา",
)


def test_the_spec_still_says_indecision_is_a_save():
    """Anchors every other assertion here to the real text.

    If the spec is ever revised so indecision is no longer a `save`, these tests should fail
    loudly rather than keep enforcing a rule that has been withdrawn.
    """
    spec = SPEC.read_text(encoding="utf-8")
    line = next((l for l in spec.splitlines() if "indecision" in l), "")
    assert line, f"no indecision rule found in {SPEC}"
    assert "'save'" in line or "`save`" in line, line
    for phrase in SAVE_RULE_PHRASES:
        assert phrase in line, f"spec no longer quotes {phrase!r}: {line}"


@pytest.mark.parametrize("outcome", ["unknown", "undefined", "churn"])
def test_no_pool_but_save_uses_an_indecision_phrase(outcome):
    """The regression that cost 0.13 F1 on every arm.

    Checked against `churn` too: the same lines lived there before 2026-08-16 and moving them
    to `unknown` is what created the defect. They belong in `save`, and nowhere else.
    """
    for template in T.CUSTOMER_CLOSE[outcome]:
        for stem in INDECISION_STEMS:
            assert stem not in template, (
                f"{outcome!r} pool line {template!r} contains {stem!r}, which the labelling "
                f"spec (retention_v9_16_body.txt:80) rules to be a `save`. A call built from "
                f"this line cannot be labelled {outcome!r} by any spec-obeying reader."
            )


def test_unknown_expresses_an_interrupted_call():
    """`body:81` defines `unknown` as the call ENDING, not the customer hesitating.

    Every line must read as an interruption. The trailing ellipsis is the marker: these are
    sentences that do not finish, which is the whole point.
    """
    for template in T.CUSTOMER_CLOSE["unknown"]:
        assert template.rstrip().endswith("..."), (
            f"{template!r} does not read as an interrupted line. `unknown` means the "
            "conversation ended before a decision, not that the customer was undecided."
        )


def test_the_dropped_call_pools_exist_and_are_agent_side():
    """The truncation needs the agent's unanswered follow-ups, or the call just stops.

    A transcript that simply ends is indistinguishable from a truncated file. Two unanswered
    agent turns are what a real dropped call looks like from the side still connected.
    """
    assert len(T.LINE_LOST) >= 3
    assert len(T.LINE_LOST_RETRY) >= 3


def test_undefined_lines_do_not_presuppose_an_inbound_call():
    """6 of the 8 `undefined` calls are OUTBOUND, so the customer never called anyone.

    The first fix used "I did not CALL about the package", which is incoherent for those six
    and measured `undefined` F1 0.222. The replacement says there is nothing to retain, which
    is true whoever dialled.

    KNOWN LIMITATION, recorded here so it is not mistaken for a regression: `undefined` still
    scores 0.000. The calls are 68-86 turns of a genuine retention conversation and one
    closing line cannot -- and should not -- override that. Fixing it needs a scenario whose
    BODY is out of scope, not another sentence. See the note in `thai_corpus.py`.
    """
    for template in T.CUSTOMER_CLOSE["undefined"]:
        assert "โทรมา" not in template, (
            f"{template!r} presupposes the customer placed the call; most `undefined` calls "
            "in this corpus are outbound")


def test_undefined_says_the_call_was_not_about_retention():
    """`body:82`: "the focus of the call is completely outside the scope of retention".

    The old pool was six ways of declining an offer, which is a retention conversation. Each
    line must now state that the caller wanted something else.
    """
    decline_stems = ("ไม่สนใจ", "ไว้โอกาสหน้า", "ยังไม่ต้องการ", "ไว้ค่อยว่ากันใหม่")
    for template in T.CUSTOMER_CLOSE["undefined"]:
        for stem in decline_stems:
            assert stem not in template, (
                f"`undefined` pool line {template!r} contains {stem!r}, which declines a "
                "retention offer. Declining an offer IS a retention conversation; `undefined` "
                "is for calls that were never about retention at all."
            )


def test_undefined_calls_carry_no_cancellation_reason():
    """A cancellation reason makes a call retention-relevant by definition.

    Before the fix every `undefined` call was assigned one from `REASON_BY_SCENARIO`, so its
    own transcript argued against its own label.
    """
    import business_labels as B

    rng_calls = [B.choose_reasons(__import__("random").Random(seed), "telesale_offer",
                                  "undefined")
                 for seed in range(40)]
    assert all(r == ("", "", "") for r in rng_calls), (
        "`undefined` calls must not be assigned a cancellation reason")


def test_every_outcome_still_has_a_usable_pool():
    """The fix must not have emptied anything -- an unrenderable label is the earlier bug."""
    from evalharness.labelspaces import RETENTION

    assert set(T.CUSTOMER_CLOSE) == set(RETENTION.call_result)
    for label, pool in T.CUSTOMER_CLOSE.items():
        assert len(pool) >= 4, f"{label} pool is too small to vary: {len(pool)}"


def test_the_generated_corpus_truncates_only_unknown_calls():
    """End-to-end on whatever pack ASR_EVAL_ROOT points at, if it has been generated.

    Skips rather than fails when the pack is absent: the pack is gitignored and CI has no
    copy, but when it IS present this is the check that proves the composer branch fired.
    """
    import asr_common as C

    business = C.GROUND_TRUTH_DIR / "business.csv"
    if not business.exists():
        pytest.skip(f"no generated corpus at {business}")

    import csv
    last_speaker: dict[str, set[str]] = {}
    with business.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            item = f"ASR-{int(row['call_id']) - 7099:03d}"
            path = C.DIALOGUE_DIR / f"{item}.json"
            if not path.exists():
                continue
            turns = json.loads(path.read_text(encoding="utf-8"))["turns"]
            last_speaker.setdefault(row["call_result"], set()).add(turns[-1]["speaker"])

    if "unknown" in last_speaker:
        assert last_speaker["unknown"] == {"agent"}, (
            "an `unknown` call ended on the customer, so it received the polite farewell "
            "instead of being cut off")
    for outcome in ("save", "churn", "undefined"):
        if outcome in last_speaker:
            assert last_speaker[outcome] == {"customer"}, (
                f"{outcome} calls should end on the customer's closing reply")
