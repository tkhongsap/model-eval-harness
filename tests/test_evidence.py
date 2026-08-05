"""Gates on `evidence.evidence_rates`, starting with the table that was computed first.

The hand computation came before the module. It was derived from the run logs and
`retention_v1.jsonl` alone, by someone who had no implementation to read because none
existed. That is what makes it an independent check, and it is why the numbers below are
written out as literals rather than recomputed: a test that recomputes the thing under
test agrees with it by construction.

    incumbent-base  google/gemini-2.5-flash   a40 b27 c63 d63 e63 f0 g0 h13
    candidate-base  qwen/qwen3.6-27b          a44 b42 c44 d42 e42 f0 g0 h0

    a spans_whole   b verbatim_whole  c spans_split  d verbatim_split
    e customer_speech  f agent_only   g near_miss    h comma_fields

The implementation reproduced every one of them on the first run, in both arms and in
all three replicates, along with both non-vacuity controls, the set equality between
Gemini's comma fields and its whole-field failures, and the RET-15 join reconstruction.
**No disagreement was found**, so nothing here was adjudicated and no expectation moved.

The rest of the file is about the ways this measurement is wrong while still printing a
number. In rough order of how badly each would mislead:

  * reading `verbatim_whole` as fidelity when it is format compliance (the retracted
    metric earlier in this session was exactly this error, so it gets the most tests);
  * letting `near_miss` and `hard_miss` collapse into one "violations" figure, which
    would score a model that corrected Thai orthography identically to one that made
    text up;
  * a detector that returns 0 because it is broken rather than because the models are
    clean -- both zeros in the table are load-bearing, so both get a control that
    proves the branch fires;
  * counting blank keyword fields, which hands one free "verbatim" to every blank;
  * scoring against a transcript file the model never saw.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from evalgen.evidence import (  # noqa: E402
    AGENT_PREFIX,
    ASR_SUBSTITUTIONS,
    CUSTOMER_PREFIX,
    EvidenceError,
    asr_normalise,
    evidence_rates,
)
from evalgen.testsets import load_testset, testset_sha  # noqa: E402

TESTSET = ROOT / "tests" / "fixtures" / "testsets" / "retention_v1.jsonl"
INCUMBENT = ROOT / "out" / "runs" / "20260805-005641Z-incumbent-base"
CANDIDATE = ROOT / "out" / "runs" / "20260805-005643Z-candidate-base"

# sha256 over `item_id \t transcript_th` for the 20 items, in file order. This is the
# ONLY part of the testset `evidence_rates` reads, and pinning it is what lets the
# baseline tests below waive the whole-file sha check honestly: a label-side edit to
# `gt`/`evidence`/`rules` cannot move a count here and correctly does not fail, while a
# transcript-side edit fails here first and tells the reader the pinned counts are void.
TRANSCRIPT_DIGEST = "9fa94ea9b51cf4bbfd9ce5a14b30bcfcb070327a5cd4be554b3025e4066d6ce8"

# The sha the two base runs record for the testset they were shown.
RECORDED_TESTSET_SHA = "6e761141c4096a066585b2643ca5510a67bce6b9baa9f7159d6e3edb6b52d79b"

# The runs are working artifacts, not fixtures: `out/` is gitignored and CI has no runs.
# Skipping is honest; synthesising a stand-in and calling the baseline reproduced is not.
needs_runs = pytest.mark.skipif(
    not (INCUMBENT / "run.jsonl").exists() or not (CANDIDATE / "run.jsonl").exists(),
    reason="the 2026-08-05 base run directories are not present (out/ is gitignored)",
)

# The hand-computed table, per replicate. Read as (a, b, c, d, e, f, g, h).
GEMINI = {
    "spans_whole": 40,
    "verbatim_whole": 27,
    "spans_split": 63,
    "verbatim_split": 63,
    "customer_speech": 63,
    "agent_only": 0,
    "near_miss": 0,
    "comma_fields": 13,
}
QWEN = {
    "spans_whole": 44,
    "verbatim_whole": 42,
    "spans_split": 44,
    "verbatim_split": 42,
    "customer_speech": 42,
    "agent_only": 0,
    "near_miss": 0,
    "comma_fields": 0,
}

REPLICATES = (1, 2, 3)


def _rates(run_dir: Path) -> dict:
    """The baseline call. See `TRANSCRIPT_DIGEST` for why the file sha is waived."""
    return evidence_rates(run_dir, TESTSET, require_recorded_sha=False)


def _transcripts() -> dict[str, str]:
    return {item.item_id: item.transcript_th for item in load_testset(TESTSET).items}


# ------------------------------------------------------- the transcripts have not moved


def test_the_transcripts_are_the_ones_the_table_was_computed_from() -> None:
    """Guards every literal in this file. If it fails, they are all void.

    `evidence_rates` reads `transcript_th` and nothing else, so this digest -- not the
    whole-file sha -- is what the pinned counts actually depend on. Splitting the two
    apart is deliberate: `retention_v1.jsonl` carries the label side as well, that side
    is edited as the pack is refined, and a whole-file sha reports those edits as if a
    transcript had moved.

    MEASURED, and the reason this test exists in this shape: on 2026-08-05 RET-11 gained
    a `dissatisfied service` secondary label. That edit changed `gt`, `evidence` and
    `rules`, moved the file sha off the one both runs recorded, and left all 20
    transcripts byte-identical and all eight counts below unchanged.

    Do NOT update this digest to make a test pass. A changed transcript means the runs
    were produced against text that no longer exists, and the fix is a new run or a
    re-derived table, never a new literal here.
    """
    items = load_testset(TESTSET).items
    assert len(items) == 20
    blob = "\n".join(f"{item.item_id}\t{item.transcript_th}" for item in items)
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == TRANSCRIPT_DIGEST


# ------------------------------------------------------------------------ the baseline


@needs_runs
@pytest.mark.parametrize("replicate", REPLICATES)
def test_reproduces_the_gemini_table(replicate: int) -> None:
    """a40 b27 c63 d63 e63 f0 g0 h13, in every replicate.

    Every count is asserted, not a chosen few: a partial check would let a bug move two
    numbers in compensating directions and keep the identities below true.
    """
    result = _rates(INCUMBENT)
    assert result["model_requested"] == "google/gemini-2.5-flash"
    assert result["prompt_id"] == "v9_16_base"
    assert {k: result["by_replicate"][replicate][k] for k in GEMINI} == GEMINI


@needs_runs
@pytest.mark.parametrize("replicate", REPLICATES)
def test_reproduces_the_qwen_table(replicate: int) -> None:
    """a44 b42 c44 d42 e42 f0 g0 h0, in every replicate."""
    result = _rates(CANDIDATE)
    assert result["model_requested"] == "qwen/qwen3.6-27b"
    assert result["prompt_id"] == "v9_16_base"
    assert {k: result["by_replicate"][replicate][k] for k in QWEN} == QWEN


@needs_runs
@pytest.mark.parametrize(("run_dir", "table"), [(INCUMBENT, GEMINI), (CANDIDATE, QWEN)])
def test_the_replicates_agree_and_the_totals_are_three_times_one(
    run_dir: Path, table: dict
) -> None:
    """All six run x replicate cells agree, so the table is not a replicate-1 accident.

    Asserted rather than assumed, because "stable across replicates" is the claim that
    licences quoting one replicate's numbers as the run's. Three replicates of one item
    are not three independent observations, which is why `by_replicate` exists at all
    and why the totals are reported beside them rather than instead of them.
    """
    result = _rates(run_dir)
    blocks = [result["by_replicate"][r] for r in REPLICATES]
    for key in table:
        assert len({block[key] for block in blocks}) == 1, f"{key} moved between replicates"
        assert result[key] == table[key] * 3
    assert result["records_read"] == 60
    assert result["records_skipped_not_parse_ok"] == 0


# ----------------------------------------- the two rates, and what the gap between them is


@needs_runs
def test_both_grains_are_reported_side_by_side() -> None:
    """Neither rate is returned alone, and there is no blended third one.

    A single number here would have to pick a grain, and the grain changes the answer by
    0.325 on Gemini. The absence of a headline is the design.
    """
    for run_dir in (INCUMBENT, CANDIDATE):
        result = _rates(run_dir)
        for key in ("spans_whole", "verbatim_whole", "whole_field_rate",
                    "spans_split", "verbatim_split", "segment_rate", "format_gap"):
            assert key in result
        assert result["format_gap"] == pytest.approx(
            result["segment_rate"] - result["whole_field_rate"], abs=1e-12
        )

    gemini, qwen = _rates(INCUMBENT), _rates(CANDIDATE)
    assert gemini["whole_field_rate"] == pytest.approx(27 / 40, abs=1e-9)  # 0.675
    assert gemini["segment_rate"] == pytest.approx(1.0, abs=1e-9)
    assert qwen["whole_field_rate"] == pytest.approx(42 / 44, abs=1e-9)  # 0.955
    assert qwen["segment_rate"] == pytest.approx(42 / 44, abs=1e-9)


@needs_runs
def test_every_gemini_whole_field_failure_is_a_comma_join_and_nothing_else() -> None:
    """The claim the format-vs-fidelity reading rests on, checked as SET equality.

    27 + 13 = 40 would also hold if some other 13 fields failed and 13 unrelated fields
    carried commas. So the two sets are compared directly: every comma-bearing field
    fails whole, every comma-free field passes whole, zero exceptions either way.

    This is why `verbatim_whole` must not be reported as fidelity. Gemini's whole-field
    score is low *because it obeyed* "Use comma separation" (`schemas/retention.json:35`,
    production `main.py:977`); the transcript contains no comma the model could have
    matched. Ranking the arms on it would repeat the error retracted earlier this
    session, where a format difference was reported as a fidelity difference.
    """
    result = _rates(INCUMBENT)
    transcripts = _transcripts()

    comma_slots = {
        (s["item_id"], s["product"], s["slot"]) for s in result["comma_field_slots"]
    }
    whole_failures = set()
    for record in _records(INCUMBENT):
        transcript = transcripts[record["item_id"]]
        for product, block in record["payload"]["product"].items():
            for slot in ("main", "secondary", "third"):
                nested = block.get(slot)
                if not isinstance(nested, dict):
                    continue
                keyword = nested.get("keyword")
                if isinstance(keyword, str) and keyword.strip() and keyword not in transcript:
                    whole_failures.add((record["item_id"], product, slot))

    assert len(whole_failures) == 13
    assert whole_failures == comma_slots
    assert result["verbatim_whole"] + result["comma_fields"] == result["spans_whole"]


@needs_runs
def test_qwens_two_rates_are_the_same_measurement_written_twice() -> None:
    """Qwen emitted no commas, so splitting is a no-op and b and d measure one thing.

    Pinned because it is the other half of the format story: the arm that "wins" on
    whole-field did not do better at quoting, it declined to use the separator. Comparing
    the two arms on `whole_field_rate` compares one model's compliance with another's.
    """
    result = _rates(CANDIDATE)
    assert result["comma_fields"] == 0
    assert result["spans_split"] == result["spans_whole"]
    assert result["verbatim_split"] == result["verbatim_whole"]
    assert result["format_gap"] == 0.0


@needs_runs
def test_the_denominators_are_not_comparable_across_arms() -> None:
    """40 vs 44 is emission policy, not evidence volume, and the counts say so.

    Qwen fills all three slots and leaves 22 of 66 keyword fields blank; Gemini omits
    slots it has nothing for and leaves 6 of 46. Returned so nobody reads `spans_whole`
    as "how much evidence the model gave".
    """
    gemini, qwen = _rates(INCUMBENT), _rates(CANDIDATE)
    assert (gemini["keyword_slots_present"], gemini["blank_keyword_slots"]) == (46 * 3, 6 * 3)
    assert (qwen["keyword_slots_present"], qwen["blank_keyword_slots"]) == (66 * 3, 22 * 3)
    assert qwen["product_blocks"] == gemini["product_blocks"] == 22 * 3


# --------------------------------- near-miss and violation are two counters, permanently


@needs_runs
@pytest.mark.parametrize("run_dir", [INCUMBENT, CANDIDATE])
def test_near_miss_and_hard_miss_are_separate_and_never_netted(run_dir: Path) -> None:
    """The two counters cannot collapse into one, and neither can their listings.

    A near miss is a model that normalised Thai orthography the transcript did not; a
    hard miss is text that is not there in any orthography. Summing them into
    "violations" would score a model for correcting an ASR artifact exactly as it scores
    one for inventing a quote -- and on this data the correcting model would look worse,
    because it would lose the byte-exact match it currently gets for reproducing the
    defect.
    """
    result = _rates(run_dir)
    assert "near_miss" in result and "hard_miss" in result
    assert result["near_miss_segments"] is not result["hard_miss_segments"]
    assert len(result["near_miss_segments"]) == result["near_miss"]
    assert len(result["hard_miss_segments"]) == result["hard_miss"]

    # Nothing in the result is a combined violations figure under another name.
    banned = ("violation", "total_miss", "miss_total", "misses", "not_verbatim")
    offenders = [k for k in result if any(word in k.lower() for word in banned)]
    assert not offenders, f"{offenders} reads as a netted violations count"

    # The partition: every segment is verbatim, a near miss, or a hard miss.
    assert (
        result["verbatim_split"] + result["near_miss"] + result["hard_miss"]
        == result["spans_split"]
    )


def test_near_miss_and_hard_miss_stay_distinct_when_both_are_nonzero(tmp_path: Path) -> None:
    """Two of one, one of the other, in a single record. Netting shows up immediately.

    The zeros on the baseline runs cannot prove the two counters are really two: 0 and 0
    net to 0. So this drives both above zero and to DIFFERENT values, where any collapse
    -- summing them, assigning to the wrong bucket, or making one an alias of the other
    -- produces a wrong number rather than a coincidence.
    """
    run_dir = _write_run(tmp_path, [_record(
        "RET-11",
        main="แจ้งไปสามรอบแล้วคะ",  # normalises -> near miss
        secondary="มันใช้งานไม่ค่อยได้แล้วอ่ะคะ",  # normalises -> near miss
        third="ยกเลิกทุกอย่างพรุ่งนี้",  # in no orthography -> hard miss
    )])
    result = evidence_rates(run_dir, TESTSET)

    assert result["near_miss"] == 2
    assert result["hard_miss"] == 1
    assert result["verbatim_split"] == 0
    assert result["spans_split"] == 3

    near = {s["segment"] for s in result["near_miss_segments"]}
    hard = {s["segment"] for s in result["hard_miss_segments"]}
    assert len(near) == 2 and len(hard) == 1
    assert not (near & hard), "a segment cannot be both a near miss and a hard miss"


def test_a_near_miss_is_not_counted_as_a_violation(tmp_path: Path) -> None:
    """One record, one span, normalising: it lands in near_miss and nowhere else.

    The mutation that would break the split is making `asr_normalise` part of the
    verbatim test, or adding near misses to hard misses. Either shows up here.
    """
    run_dir = _write_run(tmp_path, [_record("RET-11", main="แจ้งไปสามรอบแล้วคะ")])
    result = evidence_rates(run_dir, TESTSET)
    assert result["near_miss"] == 1
    assert result["hard_miss"] == 0
    assert result["verbatim_split"] == 0
    assert result["customer_speech"] == 0  # placement is only asked of exact matches
    assert [s["segment"] for s in result["near_miss_segments"]] == ["แจ้งไปสามรอบแล้วคะ"]


def test_a_hard_miss_is_not_counted_as_a_near_miss(tmp_path: Path) -> None:
    """Text that is in no orthography of the transcript is a hard miss, full stop."""
    run_dir = _write_run(tmp_path, [_record("RET-11", main="ลูกค้าบอกว่าจะยกเลิกทุกอย่างพรุ่งนี้")])
    result = evidence_rates(run_dir, TESTSET)
    assert result["hard_miss"] == 1
    assert result["near_miss"] == 0
    assert result["near_miss_segments"] == []


# --------------------------------------------------- both zeros are real zeros: controls


def test_the_near_miss_detector_is_not_vacuous() -> None:
    """`near_miss = 0` on both arms is a finding about the models, not a broken branch.

    MEASURED: RET-11 is the only item carrying the artifacts -- 12 occurrences of `เเ`
    and one U+200B, zero of both everywhere else -- and neither model normalised either
    one. Both reproduced the artifact byte-for-byte, matched exactly, and never reached
    the near-miss branch.

    These three spans are those same RET-11 phrases with the artifact removed by hand:
    what a correctly-normalising model would have emitted. All three fail the exact test
    and all three fire the near-miss branch. Without it, the model that got Thai right
    would be the one scored as having made the text up.
    """
    transcript = _transcripts()["RET-11"]
    normalised = asr_normalise(transcript)
    for span in (
        "แจ้งไปสามรอบแล้วคะ",
        "มันใช้งานไม่ค่อยได้แล้วอ่ะคะ",
        "ประมาณสองเดือนแล้วอ่ะคะ",  # this one also drops the U+200B
    ):
        assert span not in transcript, "control span must not match exactly"
        assert asr_normalise(span) in normalised, "control span must match after asr_normalise"

    assert transcript.count("เเ") == 12
    assert transcript.count("​") == 1
    others = [t for iid, t in _transcripts().items() if iid != "RET-11"]
    assert sum(t.count("เเ") + t.count("​") for t in others) == 0


def test_the_agent_only_detector_is_not_vacuous() -> None:
    """`agent_only = 0` on both arms is likewise a finding, not a dead branch.

    `prompt.py:4382-4387` requires the reason phrase to be customer speech, so a span
    quoted only from an agent turn is a real defect and this branch has to be able to
    fire. The three spans are the opening 17 characters of a real agent turn: absent from
    every customer turn, present in an agent turn.
    """
    transcripts = _transcripts()
    for item_id, span in (
        ("RET-01", "สวัสดีค่ะ ทรูคอร์"),
        ("RET-11", "สวัสดีคะทรูคอลเซ็"),
        ("RET-17", "สวัสดีค่ะ ทรูนะคะ"),
    ):
        transcript = transcripts[item_id]
        agent = [ln[len(AGENT_PREFIX):] for ln in transcript.split("\n") if ln.startswith(AGENT_PREFIX)]
        customer = [ln[len(CUSTOMER_PREFIX):] for ln in transcript.split("\n") if ln.startswith(CUSTOMER_PREFIX)]
        assert any(span in turn for turn in agent)
        assert not any(span in turn for turn in customer)


def test_an_agent_only_span_is_counted_and_listed(tmp_path: Path) -> None:
    """End to end, through `evidence_rates` rather than through the turn split alone."""
    run_dir = _write_run(tmp_path, [_record("RET-01", main="สวัสดีค่ะ ทรูคอร์")])
    result = evidence_rates(run_dir, TESTSET)
    assert result["agent_only"] == 1
    assert result["customer_speech"] == 0
    assert result["verbatim_split"] == 1  # it IS in the transcript; only the speaker is wrong
    assert [s["item_id"] for s in result["agent_only_segments"]] == ["RET-01"]


def test_every_transcript_line_is_attributed_to_a_speaker() -> None:
    """366 lines, 184 agent, 182 customer, 0 unprefixed -- so the split has no residue.

    MEASURED rather than assumed. If unprefixed lines existed, their text would be
    unattributable and the placement counts would be quietly incomplete.
    """
    lines = agent = customer = 0
    for transcript in _transcripts().values():
        for line in transcript.split("\n"):
            lines += 1
            agent += line.startswith(AGENT_PREFIX)
            customer += line.startswith(CUSTOMER_PREFIX)
    assert (lines, agent, customer) == (366, 184, 182)
    assert agent + customer == lines


# ------------------------------------- the two Qwen failures are joins, not fabrications


@needs_runs
def test_the_qwen_hard_misses_are_turn_boundary_joins() -> None:
    """Both are RET-15 Postpaid, and every character of both is transcript-derived.

    This is where a reader draws the wrong conclusion, so it is pinned rather than left
    to prose. `main` is customer turn 3 followed by customer turn 4; `secondary` is
    customer turn 6 followed by a prefix of customer turn 7. The reconstruction from the
    turn bodies is character-exact -- 124 characters for `main`, assembled from two turns
    of 55 and 68 plus the single space that already sits after `ลูกค้า:`.

    They count as `hard_miss` because the metric is byte-exact substring, and they are
    correctly NOT `near_miss` because the near-miss rule covers ASR orthography only. But
    they are not fabrication, and if a `join` category is ever wanted it arrives with its
    own hand-computed expectation, never by widening `asr_normalise` until this goes away.
    """
    result = _rates(CANDIDATE)
    assert result["hard_miss"] == 2 * 3  # two per replicate
    assert {(s["item_id"], s["product"], s["slot"]) for s in result["hard_miss_segments"]} == {
        ("RET-15", "Postpaid", "main"),
        ("RET-15", "Postpaid", "secondary"),
    }

    transcript = _transcripts()["RET-15"]
    customer = [ln[len(CUSTOMER_PREFIX):] for ln in transcript.split("\n") if ln.startswith(CUSTOMER_PREFIX)]
    by_slot = {s["slot"]: s["segment"] for s in result["hard_miss_segments"]}

    assert by_slot["main"] == (customer[1] + customer[2]).strip()
    assert len(by_slot["main"]) == 124

    joined = (customer[3] + customer[4]).strip()
    assert joined.startswith(by_slot["secondary"])
    assert joined != by_slot["secondary"]  # stops before turn 7's trailing phrase

    # Neither sits inside any single turn, which is exactly why they miss.
    for segment in by_slot.values():
        assert segment not in transcript
        assert not any(segment in turn for turn in customer)


# ------------------------------------------------ blank is absent, and case is preserved


def test_a_blank_keyword_is_absent_rather_than_a_zero_length_span(tmp_path: Path) -> None:
    """`""` is a substring of every transcript, so counting blanks hands out free marks.

    Both arms write `""` for "no keyword" -- there are no JSON nulls under
    `payload.product` in either run -- so a literal "non-null keyword" selector would be
    vacuous. This follows the repo's own normalisation instead (`records.py:29`
    `return text or None`, `records.py:59` `if cleaned:`) and treats blank-after-strip as
    absent.

    MEASURED cost of the alternative on the base runs: Gemini would read 46/33 and Qwen
    66/64, i.e. `whole_field_rate` 0.97 for the arm that left two thirds of its slots
    empty. The counters that would move are exactly these two; a blank field yields no
    segment and holds no comma.
    """
    run_dir = _write_run(
        tmp_path, [_record("RET-01", main="", secondary="   ", third="เน็ตช้า")]
    )
    result = evidence_rates(run_dir, TESTSET)
    assert result["keyword_slots_present"] == 3
    assert result["blank_keyword_slots"] == 2
    assert result["spans_whole"] == 1
    assert result["verbatim_whole"] == 1
    assert result["spans_split"] == 1
    assert result["whole_field_rate"] == 1.0


def test_segments_are_not_lowercased(tmp_path: Path) -> None:
    """Byte-exact means byte-exact. `records.py:58` lowercases; this deliberately does not.

    The transcripts contain uppercase ASCII (`PIN`, in RET-11), so folding only the model
    side would manufacture failures against text that is really there. MEASURED impact on
    the two base runs: 0 segments either way, because no segment in either arm contains
    an ASCII uppercase letter -- it costs nothing today and would bite the first time a
    model quotes `PIN`.
    """
    assert "PIN" in _transcripts()["RET-11"]
    matching = _write_run(tmp_path / "a", [_record("RET-11", main="ขอรหัส PIN")])
    assert evidence_rates(matching, TESTSET)["verbatim_split"] == 1

    lowered = _write_run(tmp_path / "b", [_record("RET-11", main="ขอรหัส pin")])
    result = evidence_rates(lowered, TESTSET)
    assert result["verbatim_split"] == 0
    assert result["hard_miss"] == 1


def test_a_comma_split_field_counts_as_several_segments(tmp_path: Path) -> None:
    """The whole field misses, each segment hits -- the format gap, in one record."""
    run_dir = _write_run(tmp_path, [_record("RET-01", main="เน็ตช้า, ใช้ไม่ได้เรื่องเลย")])
    result = evidence_rates(run_dir, TESTSET)
    assert result["spans_whole"] == 1
    assert result["verbatim_whole"] == 0
    assert result["comma_fields"] == 1
    assert result["spans_split"] == 2
    assert result["verbatim_split"] == 2
    assert result["whole_field_rate"] == 0.0
    assert result["segment_rate"] == 1.0
    assert result["format_gap"] == 1.0


def test_a_non_string_keyword_is_not_a_span(tmp_path: Path) -> None:
    """Mirrors `flatten._reason`: null, list and dict yield nothing.

    Stringifying a list would manufacture a span the model never claimed and then count
    it as a hard miss -- a fabrication the model did not commit.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _write_records(run_dir, [{
        "item_id": "RET-01", "call_id": "5001", "replicate": 1, "parse_ok": True,
        "payload": {"product": {"Postpaid": {
            "main": {"reason": "network", "keyword": None},
            "secondary": {"reason": "other", "keyword": ["เน็ตช้า"]},
        }}},
    }])
    result = evidence_rates(run_dir, TESTSET)
    assert result["spans_whole"] == 0
    assert result["spans_split"] == 0
    assert result["keyword_slots_present"] == 0
    assert result["nonstring_keyword_slots"] == 1  # the list; the null is simply absent


def test_a_parse_failure_is_not_read(tmp_path: Path) -> None:
    """`parse_ok=False` payloads are for reading, not for scoring (outcomes.py).

    Mirrors `fabrication_rate`. A violation whose payload holds invented spans must not
    also be counted here: the run already counts it as a parse failure, and counting it
    twice makes one defect look like two.
    """
    run_dir = _write_run(tmp_path, [
        _record("RET-01", parse_ok=False, main="ข้อความที่ไม่มีอยู่ในบทสนทนาเลย"),
        _record("RET-01", main="เน็ตช้า"),
    ])
    result = evidence_rates(run_dir, TESTSET)
    assert result["hard_miss"] == 0
    assert result["spans_split"] == 1
    assert result["records_read"] == 2
    assert result["records_skipped_not_parse_ok"] == 1


# --------------------------------------------------------------- this is not a verdict


@needs_runs
def test_the_result_carries_no_ranking_verdict_or_score() -> None:
    """The module must not be readable as a fourth scored dimension.

    `evidence_rates` takes ONE run directory, so it cannot compare arms; it returns two
    rates and no blended one, so it cannot be quoted as "the" evidence number; and
    nothing in the result names a winner or a threshold. `keyword` is scored nowhere in
    production -- `src/evalharness/` never reads it -- so a change in these counts is not
    a regression in anything the harness grades.
    """
    import inspect

    from evalgen import evidence

    parameters = inspect.signature(evidence_rates).parameters
    assert [p for p in parameters if p != "self"] == [
        "run_dir", "testset_path", "require_recorded_sha",
    ]

    result = _rates(INCUMBENT)
    banned = ("verdict", "winner", "better", "rank", "pass", "fail", "score", "threshold")
    offenders = [k for k in result if any(word in k.lower() for word in banned)]
    assert not offenders, f"{offenders} reads as a verdict"

    # No comparison entry point either; adding one needs its own hand-computed table.
    assert not [
        name for name in dir(evidence)
        if not name.startswith("_") and any(w in name.lower() for w in ("compare", "rank", "verdict"))
    ]

    # `keyword` is not scored: the scoring library never mentions it.
    harness = (ROOT / "src" / "evalharness").rglob("*.py")
    assert not [p for p in harness if "keyword" in p.read_text(encoding="utf-8")]


# ------------------------------------------------------------------------- provenance


def test_a_testset_the_run_did_not_see_raises_by_default(tmp_path: Path) -> None:
    """The gate. Scoring against the wrong transcripts yields a plausible low rate.

    That number would be read as the model inventing its evidence, which is the single
    most damaging way this module can be wrong.
    """
    run_dir = _write_run(tmp_path, [_record("RET-01", main="เน็ตช้า")])
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "synthetic", "testset_sha": "0" * 64}), encoding="utf-8"
    )
    with pytest.raises(EvidenceError) as exc:
        evidence_rates(run_dir, TESTSET)
    assert "sha" in str(exc.value)

    waived = evidence_rates(run_dir, TESTSET, require_recorded_sha=False)
    assert waived["testset_sha_verified"] is False
    assert waived["testset_sha_actual"] == testset_sha(TESTSET)
    assert waived["testset_sha"] == "0" * 64  # the mismatch travels with the numbers


def test_a_matching_sha_is_reported_as_verified(tmp_path: Path) -> None:
    """The waiver must not be the only path that works, or the gate proves nothing."""
    run_dir = _write_run(tmp_path, [_record("RET-01", main="เน็ตช้า")])
    (run_dir / "run.json").write_text(
        json.dumps({"run_id": "synthetic", "testset_sha": testset_sha(TESTSET)}),
        encoding="utf-8",
    )
    result = evidence_rates(run_dir, TESTSET)
    assert result["testset_sha_verified"] is True


@needs_runs
def test_the_base_runs_record_the_sha_they_were_shown() -> None:
    """Both arms name the same testset, so the two tables are over identical transcripts.

    Comparing counts computed against different transcript files would be meaningless,
    and nothing in the numbers themselves would show it.
    """
    for run_dir in (INCUMBENT, CANDIDATE):
        meta = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        assert meta["testset_sha"] == RECORDED_TESTSET_SHA


# ------------------------------------------------------------------- loud failures


def test_a_missing_run_log_raises(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError) as exc:
        evidence_rates(tmp_path, TESTSET)
    assert "run.jsonl" in str(exc.value)


def test_an_empty_run_log_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.jsonl").write_text("", encoding="utf-8")
    with pytest.raises(EvidenceError):
        evidence_rates(run_dir, TESTSET)


def test_a_malformed_run_log_line_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "run.jsonl").write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(EvidenceError) as exc:
        evidence_rates(run_dir, TESTSET)
    assert "is not JSON" in str(exc.value)


def test_a_missing_testset_raises(tmp_path: Path) -> None:
    run_dir = _write_run(tmp_path, [_record("RET-01", main="เน็ตช้า")])
    with pytest.raises(EvidenceError):
        evidence_rates(run_dir, tmp_path / "nope.jsonl")


def test_a_record_with_no_matching_item_raises(tmp_path: Path) -> None:
    """A best-effort skip would drop the spans and quietly RAISE the verbatim rate.

    That is the failure mode worth raising on: dropping unaskable spans removes them from
    the denominator, so a run against the wrong testset reports a better number, not a
    worse one.
    """
    run_dir = _write_run(tmp_path, [_record("RET-99", main="เน็ตช้า")])
    with pytest.raises(EvidenceError) as exc:
        evidence_rates(run_dir, TESTSET)
    assert "RET-99" in str(exc.value)


def test_a_duplicated_item_id_raises(tmp_path: Path) -> None:
    """Every span for that item would be checked against whichever transcript came last."""
    bad = tmp_path / "dup.jsonl"
    # newline="\n" is required, not cosmetic: `load_testset` refuses any CR, and on
    # Windows the default translation would turn every \n into \r\n and fail there first.
    bad.write_text(
        _item_line("RET-01", "ลูกค้า: หนึ่ง") + "\n" + _item_line("RET-01", "ลูกค้า: สอง") + "\n",
        encoding="utf-8",
        newline="\n",
    )
    run_dir = _write_run(tmp_path, [_record("RET-01", main="หนึ่ง")])
    with pytest.raises(EvidenceError) as exc:
        evidence_rates(run_dir, bad)
    assert "two items" in str(exc.value)


def test_the_asr_table_holds_only_the_two_documented_artifacts() -> None:
    """Widening this table moves hard misses into near_miss, which is the whole hazard.

    `tests/fixtures/testsets/README.md:61` documents exactly two for the
    `thai_linguistic` family. RET-16 and RET-20 carry U+2014 and U+201C/U+201D, and those
    are deliberately absent: the pack classifies them as self-repair markers and the
    escape family's nested quotes, not ASR orthography. MEASURED: 0 segments in either
    arm contain any of the three, so excluding them costs nothing and including them
    would only ever forgive a real miss.
    """
    assert ASR_SUBSTITUTIONS == (("เเ", "แ"), ("​", ""))
    assert asr_normalise("เเล้ว") == "แล้ว"
    assert asr_normalise("ประ​มาณ") == "ประมาณ"
    for char in ("—", "“", "”"):
        assert asr_normalise(char) == char


# ------------------------------------------------------------------------- fixtures


def _records(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "run.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_records(run_dir: Path, records: list[dict]) -> None:
    (run_dir / "run.jsonl").write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _write_run(tmp_path: Path, records: list[dict]) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    _write_records(run_dir, records)
    return run_dir


def _record(item_id: str, *, parse_ok: bool = True, **slots: str) -> dict:
    """One replicate answering about `Postpaid`, with whichever keyword slots are given."""
    block: dict = {"retention_outcome": "save"}
    for slot, keyword in slots.items():
        block[slot] = {"reason": "network", "keyword": keyword}
    return {
        "item_id": item_id,
        "call_id": "5001",
        "replicate": 1,
        "parse_ok": parse_ok,
        "payload": {"product": {"Postpaid": block}},
    }


def _item_line(item_id: str, transcript: str) -> str:
    """A structurally complete testset line. `load_testset` refuses a partial one."""
    return json.dumps(
        {
            "item_id": item_id,
            "call_id": "5001",
            "family": "clear",
            "transcript_th": transcript,
            "phone_number": "0810000001",
            "mechanism": "synthetic fixture",
            "why_it_matters": "synthetic fixture",
            "gt": [{"product": "Postpaid", "call_result": "save", "main": "network",
                    "secondary": "", "third": ""}],
            "evidence": {},
            "rules": {},
            "expected_failure": "none",
        },
        ensure_ascii=False,
    )
