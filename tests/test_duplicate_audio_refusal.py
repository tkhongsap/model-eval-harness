"""One .wav per call, or the run refuses.

WHY THIS EXISTS. `synthesize.py` writes the MEASURED duration into the wav filename
(`..._214_IN.wav`), so re-rendering a call whose script changed produces a file with a NEW
name **beside** the old one rather than replacing it. On 2026-08-20 a corpus regeneration
left the pack holding **185 wavs for 138 calls** — 47 calls with two each, differing only in
that duration field.

Every consumer maps wav → call by the phone token:

    wavs = {p.stem.split("_")[1]: p for p in AUDIO.glob("*.wav")}

A dict built that way silently keeps whichever file the filesystem returned last. So an arm
could have transcribed **yesterday's audio while being scored against today's labels**, and
nothing downstream could have caught it: the transcript would be a faithful transcription of
the wrong words, every count would be right, and the F1 would be wrong. That is the exact
failure class this repository keeps finding, and the only one where the number looks healthy.

Refusing beats picking the newest. "Newest" would paper over a pack in an unexpected state,
and the operator would never learn that a stale render was sitting there.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
E21 = REPO / "scripts" / "experiment21_pipeline_delta.py"
TRANSCRIBE = REPO / "asr-eval" / "scripts" / "transcribe.py"


def test_the_label_runner_refuses_a_pack_with_two_wavs_for_one_call():
    source = E21.read_text(encoding="utf-8")
    assert "_by_phone_files" in source, "the duplicate-wav guard is gone from experiment21"
    assert "more than one .wav" in source
    # The guard must sit BEFORE the map it protects, or it protects nothing.
    assert source.index("_by_phone_files") < source.index(
        'wavs = {p.stem.split("_")[1]: p for p in AUDIO.glob("*.wav")}')


def test_the_transcriber_refuses_the_same_thing():
    source = TRANSCRIBE.read_text(encoding="utf-8")
    assert "more than one audio file" in source, "the duplicate-wav guard is gone"
    assert source.index("_dupe") < source.index("wavs = sorted(C.AUDIO_DIR.glob")


def test_both_guards_explain_the_consequence_not_just_the_rule():
    """A refusal nobody understands gets deleted by the next person in a hurry.

    The message has to say what would have happened, because "duplicate file" sounds
    harmless and "transcribing old audio against new labels" does not.
    """
    for path in (E21, TRANSCRIBE):
        source = path.read_text(encoding="utf-8")
        assert re.search(r"stale render|old audio|whichever the filesystem", source), (
            f"{path.name}'s guard does not explain why a duplicate is dangerous")


def test_the_guard_names_an_example_so_it_is_actionable():
    """138 calls is too many to eyeball; the refusal has to point at one."""
    for path in (E21, TRANSCRIBE):
        source = path.read_text(encoding="utf-8")
        assert "example" in source.lower(), f"{path.name}'s guard names no example file"
