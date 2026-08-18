"""Contract tests for the ASR eval set.

These check the two things a generator bug would otherwise hide:

  1. that a filename we build parses back, under a re-implementation of production's own
     positional parser, to the fields we put in; and
  2. that the narrow Thai normalisation forgives exactly the three lossless artifact
     classes ASR-EXPECTATION.md argues for, and nothing else.

The filename shape is taken from production's own test fixture at
production-reference/sentiment-voice-analysis-develop/tests/test_tasks/sentiment_qa/
test_user_playground_task.py:363. The phone number in it is replaced with one from this
repository's sanctioned synthetic block; the phone's VALUE is irrelevant to a positional
test, and CLAUDE.md keeps non-sanctioned numbers out of fixtures.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asr_common as C  # noqa: E402

# Production's shape, sanctioned phone substituted:
#   call_id _ phone _ HHMMSS _ agent _ first _ last _ provider _ YYYYMMDD _ dur _ DIR
PRODUCTION_SHAPE = "1111_0810000301_120000_A001_jane_doe_D_20250115_60_IN.wav"


# --- the positional parse -------------------------------------------------------------


def test_production_shape_parses_to_the_documented_fields() -> None:
    got = C.parse_filename(PRODUCTION_SHAPE)
    assert got["call_id"] == "1111"
    assert got["phone_number"] == "0810000301"
    assert got["call_time"] == "120000"
    assert got["agent_id"] == "A001"
    assert got["first_name"] == "Jane"        # production capitalises, :311-314
    assert got["last_name"] == "Doe"
    assert got["provider"] == "D"
    assert got["record_date"] == "20250115"
    assert got["duration"] == "60"
    assert got["call_direction"] == "IN"


def test_every_built_filename_round_trips() -> None:
    """The set's own filenames must survive the parser production will run on them."""
    for path in sorted((ROOT / "dialogues").glob("ASR-*.json")):
        dlg = json.loads(path.read_text(encoding="utf-8"))
        meta = C.CallMeta(**dlg["meta"])
        parsed = C.parse_filename(meta.filename())
        assert parsed["call_id"] == meta.call_id
        assert parsed["phone_number"] == meta.phone_number
        assert parsed["call_time"] == meta.call_time
        assert parsed["agent_id"] == meta.agent_id
        assert parsed["provider"] == meta.provider
        assert parsed["record_date"] == meta.record_date
        assert parsed["call_direction"] == meta.call_direction
        assert parsed["duration"] == str(meta.duration)


def test_an_underscore_in_a_name_shifts_every_later_field() -> None:
    """The trap the contract exists to document, demonstrated rather than asserted.

    A three-token name gives 11 tokens where production expects 10, so every field from
    index 5 on reads its PREDECESSOR's value -- an off-by-one that never raises:

        idx     0     1          2      3     4     5     6    7        8     9    10
        tokens  1111  081...301  120000 A001  mary  jane  doe  D  20250115    60   IN
        read as call  phone      time   agent first last  prov date     dur  dir   --

    Nothing here throws. production would file this call under agent A001 with provider
    "doe", no usable record date, and a call_direction of "60" -- which is the one field
    that does eventually raise, three tasks downstream at prep_payload_task.py:335.
    """
    bad = "1111_0810000301_120000_A001_mary_jane_doe_D_20250115_60_IN.wav"
    got = C.parse_filename(bad)
    assert got["first_name"] == "Mary"
    assert got["last_name"] == "Jane"          # 'doe' has been pushed out of this slot
    assert got["provider"] == "doe"            # ...and lands here instead
    assert got["record_date"] is None          # 'D' fails production's ^\d{8}$
    assert got["duration"] == "20250115"       # the date, read as a second count
    assert got["call_direction"] == "60"       # not IN/OUT: raises downstream
    assert got["call_direction"] not in C.VALID_DIRECTIONS


# --- CallMeta.validate ----------------------------------------------------------------


def _meta(**over) -> C.CallMeta:
    base = dict(call_id="7100", phone_number="0810000301", call_time="093015",
                agent_id="A200", first_name="somying", last_name="phakdee",
                provider="D", record_date="20260803", duration=240,
                call_direction="IN")
    base.update(over)
    return C.CallMeta(**base)


def test_a_valid_meta_has_no_violations() -> None:
    assert _meta().validate() == []


@pytest.mark.parametrize(
    "over,fragment",
    [
        ({"first_name": "mary_jane"}, "shifts every later index"),
        ({"call_direction": "INBOUND"}, "prep_payload_task.py:335"),
        ({"phone_number": "0899999999"}, "sanctioned synthetic block"),
        ({"record_date": "2026-08-03"}, "^\\d{8}$"),
        ({"call_time": "0930"}, "HHMMSS"),
        ({"duration": 0}, "positive whole second"),
        ({"provider": ""}, "collapses a positional field"),
    ],
)
def test_validate_catches(over: dict, fragment: str) -> None:
    errs = _meta(**over).validate()
    assert errs, f"{over} should have been rejected"
    assert any(fragment in e for e in errs), errs


# --- phone reservation ----------------------------------------------------------------


def test_phone_index_maps_into_the_reserved_range() -> None:
    """The first twenty are unchanged by the 2026-08-18 widening -- it is a superset.

    That property is the reason the widening is safe to make: no committed value moved, so
    every number in the frozen 20-call set still means what it always meant.
    """
    assert C.phone_for_index(0) == "0810000301"
    assert C.phone_for_index(19) == "0810000320"
    assert C.phone_for_index(137) == "0810000438"


def test_phone_index_refuses_to_walk_past_the_reservation() -> None:
    """Widening the block is a reviewed data-safety change, so this must raise, not wrap.

    The ceiling moved 320 -> 438 on 2026-08-18 to carry a 138-call set. The refusal itself
    is the control and must survive the widening: index 138 is one past the new reservation
    and has to raise exactly as index 20 used to.
    """
    with pytest.raises(ValueError, match="reviewed change to a data-safety control"):
        C.phone_for_index(138)


def test_reserved_range_does_not_collide_with_the_spent_ranges() -> None:
    """CLAUDE.md records three spent sub-ranges, measured 2026-08-12.

    Checks the FULL claim, filenames and spoken pool together. Checking only
    ASR_PHONE_FIRST..ASR_PHONE_LAST would miss the second in-call number entirely, and
    spoken_phone_pool derives that from ASR_PHONE_LAST without any bound of its own -- so a
    collision there would go unnoticed exactly the way the 2026-08-17 undercount did.
    """
    spent = set(range(0, 100)) | set(range(101, 139)) | set(range(201, 251))
    claimed = {int(p[-3:]) for i in range(C.ASR_PHONE_LAST - C.ASR_PHONE_FIRST + 1)
               for p in C.spoken_phone_pool(i)}
    claimed |= set(range(C.ASR_PHONE_FIRST, C.ASR_PHONE_LAST + 1))
    assert not (claimed & spent), sorted(claimed & spent)
    assert max(claimed) <= 999, "the sanctioned block is 0810000000-0810000999"


# --- normalisation --------------------------------------------------------------------
# ASR-EXPECTATION.md forgives exactly three classes and refuses seven. These check both
# directions, because a normaliser that forgives too much cannot tell a correct inference
# from a fabrication (that file, :99-112).


def test_normalisation_forgives_thai_digits() -> None:                # RET-115
    assert C.normalise_thai("๕๙๙ บาท") == C.normalise_thai("599 บาท")


def test_normalisation_forgives_double_sara_e() -> None:              # RET-121 control
    assert C.normalise_thai("เเพ็กเกจ") == C.normalise_thai("แพ็กเกจ")


def test_normalisation_forgives_zero_width_characters() -> None:      # RET-122 control
    assert C.normalise_thai("แพ็ก​เกจ") == C.normalise_thai("แพ็กเกจ")


@pytest.mark.parametrize(
    "a,b,why",
    [
        ("เน็ตชา", "เน็ตช้า", "RET-113 tone mark: repairing it is guessing"),
        ("คอนเซ็นเตอร์", "คอลเซ็นเตอร์", "RET-116 proper noun: repairing it is guessing"),
        ("สันยา", "สัญญา", "RET-117 homophone: repairing it is guessing"),
        ("ค่าใช้จ่า", "ค่าใช้จ่าย", "RET-119 truncation: repairing it is guessing"),
    ],
)
def test_normalisation_refuses_the_lossy_classes(a: str, b: str, why: str) -> None:
    assert C.normalise_thai(a) != C.normalise_thai(b), why


def test_digits_only_reads_through_thai_numerals() -> None:
    assert C.digits_only("โทร ๑๒๓๑ นะคะ") == "1231"


# --- set-level shape ------------------------------------------------------------------


def test_ten_families_are_declared_and_each_names_what_it_maps_to() -> None:
    assert len(C.FAMILIES) == 10
    for name, spec in C.FAMILIES.items():
        assert spec["purpose"].strip(), name
        assert spec["maps_to"].strip(), name


def test_composed_set_covers_every_family_twice() -> None:
    index_path = ROOT / "dialogues" / "index.json"
    if not index_path.exists():
        pytest.skip("dialogues not composed yet")
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    from collections import Counter
    fam = Counter(r["family"] for r in rows)
    assert set(fam) == set(C.FAMILIES)
    # Was `== 2` when the set was fixed at twenty. The invariant that actually matters is
    # BALANCE, not the literal count: no family may be starved relative to another, or a
    # per-family metric stops being comparable across families.
    assert len(rows) == len(json.loads(index_path.read_text(encoding="utf-8")))
    assert min(fam.values()) >= 2, dict(fam)
    assert max(fam.values()) - min(fam.values()) <= 1, dict(fam)


def test_composed_set_has_both_call_directions() -> None:
    index_path = ROOT / "dialogues" / "index.json"
    if not index_path.exists():
        pytest.skip("dialogues not composed yet")
    rows = json.loads(index_path.read_text(encoding="utf-8"))
    dirs = {r["direction"] for r in rows}
    assert dirs == {"IN", "OUT"}, (
        "prep_payload_task.py:330-337 selects a different prompt per direction, so a "
        "single-direction set exercises half of production's prompt surface"
    )


def test_no_reference_line_carries_a_wrong_polite_particle() -> None:
    """After นะ / ล่ะ / สิ / เหรอ the female polite particle is คะ, never ค่ะ.

    This is not a stylistic preference: ค่ะ carries a falling tone and คะ a high one, so
    the wrong form is mispronounced by the TTS voice AND wrong in the reference. Both would
    be wrong in the same direction, which is exactly the kind of error no downstream metric
    can catch. 232 occurrences of the correct form exist across the set.
    """
    gt = ROOT / "ground-truth"
    files = sorted(gt.glob("ASR-*.txt"))
    if not files:
        pytest.skip("dialogues not composed yet")
    offenders = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        for wrong in ("นะค่ะ", "ล่ะค่ะ", "สิค่ะ", "เหรอค่ะ"):
            if wrong in text:
                offenders.append(f"{path.name}: {wrong}")
    assert not offenders, offenders


def test_a_male_speaker_never_uses_a_female_particle_and_vice_versa() -> None:
    """A turn's particles must match the voice that speaks it."""
    checked = 0
    for dpath in sorted((ROOT / "dialogues").glob("ASR-*.json")):
        dlg = json.loads(dpath.read_text(encoding="utf-8"))
        gender = {"th-TH-NiwatNeural": "m", "th-TH-PremwadeeNeural": "f"}
        voice = {"agent": dlg["agent_voice"], "customer": dlg["customer_voice"]}
        for turn in dlg["turns"]:
            if turn["kind"] != "speech":
                continue
            g = gender[voice[turn["speaker"]]]
            text = turn["text"]
            if g == "m":
                assert "ค่ะ" not in text and "คะ" not in text, (
                    f"{dlg['item_id']} turn {turn['idx']} is spoken by a male voice but "
                    f"uses a female particle: {text[:70]}"
                )
            else:
                assert "ครับ" not in text, (
                    f"{dlg['item_id']} turn {turn['idx']} is spoken by a female voice but "
                    f"uses ครับ: {text[:70]}"
                )
            checked += 1
    if checked == 0:
        pytest.skip("dialogues not composed yet")


def test_every_reference_line_has_a_speaker_segment() -> None:
    """The timeline and the transcript must describe the same call."""
    gt = ROOT / "ground-truth"
    checked = 0
    for tl_path in sorted(gt.glob("ASR-*.timeline.json")):
        item = tl_path.name.split(".")[0]
        ref = (gt / f"{item}.txt").read_text(encoding="utf-8")
        tl = json.loads(tl_path.read_text(encoding="utf-8"))
        speech = [s for s in tl["segments"] if s["kind"] == "speech"]
        assert len(speech) == len([ln for ln in ref.splitlines() if ln.strip()]), item
        checked += 1
    if checked == 0:
        pytest.skip("audio not synthesised yet")


def test_filename_fields_and_positions_agree() -> None:
    """The two representations of the contract must not drift apart.

    FILENAME_FIELDS shipped with nine entries in the first version -- agent_id was missing
    -- while FILENAME_TOKEN_COUNT said ten and CallMeta carried ten. Nothing failed,
    because nothing compared them. This does.
    """
    assert len(C.FILENAME_FIELDS) == C.FILENAME_TOKEN_COUNT
    assert list(C.FILENAME_FIELDS) == [
        name for name, _ in sorted(C.FILENAME_POSITIONS.items(), key=lambda kv: kv[1])
    ]
    import dataclasses
    meta_fields = {f.name for f in dataclasses.fields(C.CallMeta)}
    assert meta_fields == set(C.FILENAME_FIELDS)


def test_no_committed_artifact_contains_an_absolute_path() -> None:
    """Data-safety regression guard.

    A MAJOR finding in this repository on 2026-08-09 was an absolute path plus an OS
    account name in a shareable export. validation.json reintroduced exactly that, 40
    times, until the report started storing paths relative to asr-eval/. This asserts it
    stays that way for every committed text artifact.
    """
    import re as _re
    suspicious = _re.compile(r"(/home/[a-z]|/Users/[A-Za-z]|C:\\\\Users)")
    checked = 0
    offenders = []
    for sub in ("ground-truth", "dialogues", "reports"):
        for path in (ROOT / sub).rglob("*"):
            if not path.is_file() or path.suffix not in (".json", ".txt", ".md", ".csv"):
                continue
            if "plots" in path.parts:          # gitignored
                continue
            checked += 1
            if suspicious.search(path.read_text(encoding="utf-8", errors="replace")):
                offenders.append(str(path.relative_to(ROOT)))
    for name in ("manifest.json", "README.md"):
        p = ROOT / name
        if p.exists():
            checked += 1
            if suspicious.search(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(name)
    if checked == 0:
        pytest.skip("nothing generated yet")
    assert not offenders, offenders
