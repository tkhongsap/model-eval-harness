"""Tests for the tooling that surrounds the set: chunking, multipart, scoring primitives.

Chunking gets the most attention here because it is the one place where a silent bug
changes a WER without changing anything visible. A splitter that drops or duplicates audio
produces a transcript that is genuinely missing words, and the arm gets the blame.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import asr_common as C          # noqa: E402
import score_asr as S           # noqa: E402
import transcribe as T          # noqa: E402

SR = 8000


def tone(seconds: float, level: float = 0.5) -> np.ndarray:
    return (np.ones(int(seconds * SR)) * level).astype(np.float32)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SR), dtype=np.float32)


# --- chunking -------------------------------------------------------------------------


def test_short_audio_is_not_chunked() -> None:
    x = tone(30)
    assert len(T.chunks_of(x, SR, 120.0)) == 1


def test_chunking_is_lossless() -> None:
    """Every sample must appear exactly once, in order. This is the one that matters."""
    rng = np.random.default_rng(3)
    x = rng.standard_normal(SR * 400).astype(np.float32) * 0.3
    parts = T.chunks_of(x, SR, 120.0)
    assert len(parts) > 1
    rejoined = np.concatenate(parts)
    assert rejoined.size == x.size
    assert np.array_equal(rejoined, x)


def test_chunks_are_near_the_requested_length() -> None:
    rng = np.random.default_rng(4)
    x = rng.standard_normal(SR * 500).astype(np.float32) * 0.3
    parts = T.chunks_of(x, SR, 120.0)
    # Every chunk but the last sits within the search radius of the target.
    for p in parts[:-1]:
        assert 110 * SR <= p.size <= 130 * SR, p.size / SR


def test_cut_lands_in_the_silence_not_at_the_nominal_offset() -> None:
    """A cut at a fixed offset lands mid-syllable; this must prefer the quiet gap."""
    x = np.concatenate([tone(115), silence(3), tone(115)])
    cuts = T.split_points(x, SR, 120.0)
    assert len(cuts) == 1
    at = cuts[0] / SR
    assert 115.0 <= at <= 118.0, f"cut at {at:.2f}s, expected inside the 115-118s silence"


def test_a_long_silence_cannot_be_re_found_forever() -> None:
    """Regression: the first version emitted hundreds of 10 ms chunks here.

    The search window reached back past the previous cut, re-found the same silence every
    iteration, and advanced by one frame at a time.
    """
    x = np.concatenate([tone(10), silence(2), tone(10)])
    parts = T.chunks_of(x, SR, 8.0)
    assert len(parts) <= 4, f"{len(parts)} chunks -- the splitter is stalling on the gap"
    assert all(p.size >= SR for p in parts), [round(p.size / SR, 3) for p in parts]
    assert np.array_equal(np.concatenate(parts), x)


def test_split_points_are_strictly_increasing() -> None:
    rng = np.random.default_rng(5)
    x = rng.standard_normal(SR * 600).astype(np.float32) * 0.2
    cuts = T.split_points(x, SR, 90.0)
    assert cuts == sorted(cuts)
    assert len(set(cuts)) == len(cuts)


# --- multipart ------------------------------------------------------------------------


def test_multipart_body_is_well_formed() -> None:
    body, ctype = T.build_multipart({"model": "qwen3-asr", "language": "th"},
                                    "call.wav", b"RIFFDATA")
    assert ctype.startswith("multipart/form-data; boundary=")
    boundary = ctype.split("boundary=")[1].encode()
    assert body.startswith(b"--" + boundary)
    assert body.endswith(b"--" + boundary + b"--\r\n")
    assert b'name="model"' in body and b"qwen3-asr" in body
    assert b'name="file"; filename="call.wav"' in body
    assert b"Content-Type: audio/wav" in body
    assert b"RIFFDATA" in body


# --- scoring primitives ---------------------------------------------------------------


def test_levenshtein_counts_the_three_error_kinds_separately() -> None:
    dist, sub, dele, ins = S.levenshtein(list("abcd"), list("abd"))
    assert (dist, sub, dele, ins) == (1, 0, 1, 0)
    dist, sub, dele, ins = S.levenshtein(list("abd"), list("abcd"))
    assert (dist, sub, dele, ins) == (1, 0, 0, 1)
    dist, sub, dele, ins = S.levenshtein(list("abc"), list("axc"))
    assert (dist, sub, dele, ins) == (1, 1, 0, 0)


def test_levenshtein_handles_empty_sides() -> None:
    assert S.levenshtein([], list("abc"))[0] == 3
    assert S.levenshtein(list("abc"), [])[0] == 3
    assert S.levenshtein([], [])[0] == 0


def test_spoken_digit_runs_recovers_a_phone_number() -> None:
    spoken = "เบอร์ ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง ครับ"
    assert "0810000301" in S.spoken_digit_runs(spoken)


def test_spoken_digit_runs_ignores_isolated_number_words() -> None:
    """'ห้า' inside ordinary prose is not a digit run and must not become '5'."""
    assert S.spoken_digit_runs("ขอบคุณครับ ห้านาที") == []


def test_entity_scoring_accepts_either_surface_or_value() -> None:
    ents = [{"type": "phone", "value": "0810000301",
             "spoken": "ศูนย์ แปด หนึ่ง ศูนย์ ศูนย์ ศูนย์ ศูนย์ สาม ศูนย์ หนึ่ง"}]
    as_words = S.score_entities(ents, "เบอร์ " + ents[0]["spoken"])
    as_digits = S.score_entities(ents, "เบอร์ 0810000301")
    wrong = S.score_entities(ents, "เบอร์ 0810000999")
    assert as_words["hit"] == 1 and as_words["per_type"]["phone"]["surface"] == 1
    assert as_digits["hit"] == 1 and as_digits["per_type"]["phone"]["value"] == 1
    assert wrong["hit"] == 0


def test_nonspeech_seconds_ignores_overlapping_segments() -> None:
    """Overlapping turns must not be double-counted as covering more than they do."""
    tl = {"segments": [
        {"kind": "speech", "start_s": 0.0, "dur_s": 10.0},
        {"kind": "speech", "start_s": 9.0, "dur_s": 10.0},   # overlaps by 1 s
    ]}
    assert S.nonspeech_seconds(tl, 30.0) == pytest.approx(11.0)


def test_normalisation_is_idempotent() -> None:
    s = "เเพ็ก​เกจ ๕๙๙  บาท"
    once = C.normalise_thai(s)
    assert C.normalise_thai(once) == once


# --- entity scoring: the digit-splice regression ---------------------------------------
# The first version compared the canonical value as a SUBSTRING of every digit in the
# hypothesis concatenated together. Scored against a junk hypothesis mentioning none of the
# call, it reported 57 of 498 entities recovered. These pin that shut.


@pytest.mark.parametrize(
    "etype,value,spoken,hyp,why",
    [
        ("months", "12", "สิบสอง เดือน", "ค่าบริการรอบนี้ 1250 บาทครับ",
         "'12' is a prefix of '1250' but the call states no contract length"),
        ("months", "3", "สาม เดือน", "แพ็กเกจนี้ 399 บาทต่อเดือน",
         "'3' is a prefix of '399'"),
        ("amount", "599", "ห้าร้อยเก้าสิบเก้าบาท", "ส่วนลด 45 บาท และใช้ได้ 99 วัน",
         "'45' and '99' splice into '4599', which contains '599'"),
        ("months", "12", "สิบสอง เดือน", "มีผลวันที่ 12 สิงหาคม",
         "a day-of-month is not a contract length: the unit word must be adjacent"),
        ("phone", "0810000301", "ศูนย์ แปด หนึ่ง", "โทร 08100003010 ครับ",
         "an 11-digit run is not the 10-digit number"),
    ],
)
def test_entity_value_match_rejects_digit_splices(etype, value, spoken, hyp, why) -> None:
    got = S.score_entities([{"type": etype, "value": value, "spoken": spoken}], hyp)
    assert got["hit"] == 0, why


@pytest.mark.parametrize(
    "etype,value,spoken,hyp",
    [
        ("months", "12", "สิบสอง เดือน", "สัญญา 12 เดือนครับ"),
        ("amount", "599", "ห้าร้อยเก้าสิบเก้าบาท", "เดือนละ 599 บาท"),
        ("amount", "599", "ห้าร้อยเก้าสิบเก้าบาท", "เดือนละ ห้าร้อยเก้าสิบเก้าบาท"),
        ("amount", "1290", "หนึ่งพันสองร้อยเก้าสิบบาท", "ยอด 1,290 บาท"),
        ("speed", "300", "สามร้อย เมกะบิต", "ความเร็ว 300 เมกะบิต"),
        ("date", "2026-08-27", "วันที่ ยี่สิบเจ็ด สิงหาคม สองพันห้าร้อยหกสิบเก้า",
         "มีผล 27 สิงหาคม 2569"),
        ("phone", "0810000301", "ศูนย์ แปด หนึ่ง", "เบอร์ 0810000301"),
    ],
)
def test_entity_value_match_still_accepts_real_recoveries(etype, value, spoken, hyp) -> None:
    got = S.score_entities([{"type": etype, "value": value, "spoken": spoken}], hyp)
    assert got["hit"] == 1


def test_a_junk_hypothesis_recovers_no_entity_anywhere_in_the_set() -> None:
    """The corpus-wide floor. Anything above zero here is the metric inventing recoveries."""
    import json as _json
    junk = "ลูกค้าโทรมาสอบถามโปรโมชัน เบอร์ 0812345678 ยอด 1234 บาท ครับ"
    files = sorted((ROOT / "ground-truth").glob("*.entities.json"))
    if not files:
        pytest.skip("ground truth not composed yet")
    hit = total = 0
    for f in files:
        got = S.score_entities(_json.loads(f.read_text(encoding="utf-8")), junk)
        hit += got["hit"]
        total += got["total"]
    assert total > 0
    assert hit == 0, f"{hit}/{total} entities 'recovered' from a transcript of nothing"


def test_every_entity_type_in_the_set_can_value_match() -> None:
    """`date` could never value-match in the first version -- it was missing from the chain.

    Scoring each entity against a hypothesis that IS its own canonical value proves every
    declared type has a working value path.
    """
    import json as _json
    files = sorted((ROOT / "ground-truth").glob("*.entities.json"))
    if not files:
        pytest.skip("ground truth not composed yet")
    seen: dict[str, bool] = {}
    for f in files:
        for e in _json.loads(f.read_text(encoding="utf-8")):
            # Feed the spoken form: every type must recover from its own audio wording.
            got = S.score_entities([e], e["spoken"])
            seen[e["type"]] = seen.get(e["type"], False) or got["hit"] == 1
    assert seen, "no entities found"
    missing = [t for t, ok in seen.items() if not ok]
    assert not missing, f"types with no working recovery path: {missing}"


# --- the serving-layer control token ----------------------------------------------------
# Qwen3-ASR through LiteLLM emits `language <X><asr_text>` inside its transcripts. It is not
# speech, so it is removed in the runner before the transcript is written. These pin the
# behaviour shut in both directions: it must be removed everywhere it appears, and it must
# not touch a transcript that merely resembles it.


def test_control_token_is_removed() -> None:
    counter: dict[str, int] = {}
    assert T.strip_control_tokens("language Thai<asr_text>สวัสดีครับ", counter) == "สวัสดีครับ"
    assert counter == {"responses": 1, "responses_with_tokens": 1, "tokens": 1}


def test_control_token_is_removed_from_the_middle_not_just_the_start() -> None:
    """The regression that made this correct.

    Measured on ASR-001: the token appears EIGHT times in one 2,948-character transcript, at
    roughly 400-character intervals -- it is a segment delimiter, not a prefix. An anchored
    `^` strip removed one and left seven, about 5% of the transcript, every character of it
    an insertion against the reference. The 30-second probe used to characterise the token
    was too short to contain a second one, so the anchored version looked right.
    """
    raw = ("language Thai<asr_text>หนึ่งlanguage Thai<asr_text>สองlanguage Thai<asr_text>สาม")
    counter: dict[str, int] = {}
    assert T.strip_control_tokens(raw, counter) == "หนึ่งสองสาม"
    assert counter["tokens"] == 3


def test_removal_rejoins_a_word_the_token_split() -> None:
    """The token lands inside words, so removal must not introduce a space.

    Real example from ASR-001: "...แจ้งไปหลาย" + token + "รอบก็ไม่มี...". Inserting a space
    would create a word boundary the speaker did not utter, which the tokeniser then counts
    as two words instead of one and charges to WER.
    """
    counter: dict[str, int] = {}
    assert T.strip_control_tokens("แจ้งไปหลายlanguage Thai<asr_text>รอบ", counter) == "แจ้งไปหลายรอบ"


def test_control_token_language_slot_varies() -> None:
    """`language None<asr_text>` is what the model returns when given no speech.

    A literal removal of the Thai form would leave this one in, and only on the items where
    it appeared -- the half-normalised corpus the runner refuses to produce.
    """
    counter: dict[str, int] = {}
    assert T.strip_control_tokens("language None<asr_text>ทดสอบ", counter) == "ทดสอบ"
    assert T.strip_control_tokens("language th <asr_text>hi", counter) == "hi"
    assert counter["tokens"] == 2


def test_control_tokens_are_removed_from_every_chunk_not_just_the_first(monkeypatch) -> None:
    """The strip lives at the return boundary of post_audio, which is what makes it work.

    With --chunk-seconds one call becomes N POSTs and each response carries its own tokens.
    A strip applied where the transcript is written would clean the first response and leave
    the rest embedded mid-transcript, where they score as substitutions against the model.
    """
    import json as _json
    bodies = [
        _json.dumps({"text": f"language Thai<asr_text>ส่วนที่{i}"}).encode("utf-8")
        for i in range(1, 4)
    ]
    calls = {"n": 0}

    def fake_send(*_a, **_kw):
        calls["n"] += 1
        return bodies[calls["n"] - 1]

    monkeypatch.setattr(T, "_send", fake_send)
    counter: dict[str, int] = {}
    returned = [
        T.post_audio("https://x/v1", "m", "k", "f.wav", b"", "th", 10, 1,
                     prefix_counter=counter)
        for _ in range(3)
    ]
    out = [text for text, _ in returned]
    assert out == ["ส่วนที่1", "ส่วนที่2", "ส่วนที่3"]
    assert counter == {"responses": 3, "responses_with_tokens": 3, "tokens": 3}
    assert "<asr_text>" not in " ".join(out)
    # These fixture bodies carry no usage block, so the billed figure must be None rather
    # than 0.0 -- "the server did not say" and "the server said nothing was billed" are
    # different facts, and a cost table that adds them is wrong without looking wrong.
    assert [billed for _, billed in returned] == [None, None, None]


def test_a_response_that_is_only_control_tokens_raises() -> None:
    """It must not become an empty transcript.

    An empty file scores as a total deletion and reads as a catastrophic model rather than
    as a response carrying no speech -- the confusion that cost the Gemma arm a whole run in
    Experiment 20, reached here by a new route.
    """
    counter: dict[str, int] = {}
    with pytest.raises(RuntimeError, match="only control tokens"):
        T.strip_control_tokens("language None<asr_text>   ", counter)


@pytest.mark.parametrize("text", [
    "language is a hard problem for ASR",     # legitimately starts with the word
    "a < b and c > d",                        # contains angle brackets
    "<asr_text> without the language part",   # the tag alone is not the token
    "สวัสดีครับ ยินดีให้บริการ",                    # ordinary Thai
])
def test_text_that_merely_resembles_the_token_is_untouched(text) -> None:
    counter: dict[str, int] = {}
    assert T.strip_control_tokens(text, counter) == text
    assert counter.get("tokens", 0) == 0
