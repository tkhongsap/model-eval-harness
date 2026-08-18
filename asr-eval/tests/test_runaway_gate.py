"""The runaway gate, tested against the real failure it was built for.

`score_asr.py --self-test` already exercises both rules on synthetic corruptions. These
tests point the detector at the two hypotheses that actually broke, so the thresholds are
pinned to observed data rather than to a number that felt about right:

  * `ASR-012` under `--chunk-seconds 0`  -- 58,546 chars against 3,405, a 112-char unit
    repeated 495 times. Published a corpus CER of 0.673 for the arm; the true figure with a
    working configuration is 0.1147.
  * `ASR-018` under `--chunk-seconds 120` -- 39,689 chars against 8,124, a 58-char unit
    repeated 554 times. Found while testing the fix for the first one, which is how we
    learned that chunking relocates the failure instead of removing it.

The clean-arm test is the one that matters most. A detector that fires on legitimate
transcripts silently deletes real data from the corpus rate, which is a worse failure than
the one it was built to prevent -- and a more flattering one, since the deleted items are
usually the hard ones.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import score_asr as S  # noqa: E402

HYP = ROOT / "hypotheses"
GT = ROOT / "ground-truth"


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def _pair(arm: str, item: str):
    hyp = HYP / arm / f"{item}.txt"
    ref = GT / f"{item}.txt"
    if not hyp.exists() or not ref.exists():
        pytest.skip(f"{arm}/{item} not present (transcripts are gitignored)")
    return _flat(ref), _flat(hyp)


def test_catches_the_asr012_runaway():
    ref, hyp = _pair("qwen3-asr-1.7b", "ASR-012")
    verdict = S.detect_runaway(ref, hyp)
    assert verdict is not None, "the failure the gate exists for must be caught"
    assert verdict["kind"] == "length"
    assert verdict["ratio"] > 15


def test_catches_the_asr018_runaway_that_chunking_created():
    ref, hyp = _pair("qwen3-asr-1.7b-chunked120", "ASR-018")
    verdict = S.detect_runaway(ref, hyp)
    assert verdict is not None
    assert verdict["ratio"] > 4


# The four real arms and exactly which of their items are runaways. Scratch probe
# directories from the E22 configuration sweep are deliberately absent: several of them
# exist to REPRODUCE a runaway, so including them would assert the gate stays silent on
# hypotheses generated precisely to trip it.
EXPECTED_RUNAWAYS = {
    "gemini-2.5-flash-audio": set(),
    "qwen3-asr-1.7b": {"ASR-012"},
    "qwen3-asr-1.7b-chunked120": {"ASR-018"},
    "qwen3-asr-1.7b-chunked180": set(),
}


@pytest.mark.parametrize("arm", sorted(EXPECTED_RUNAWAYS))
def test_fires_on_exactly_the_documented_runaways(arm):
    """Both directions at once, per arm.

    The false-positive half is the one that matters: a detector eating legitimate
    transcripts silently removes items from the corpus rate, and the items it would remove
    are the hard ones -- so the error flatters the arm rather than penalising it.
    """
    arm_dir = HYP / arm
    if not arm_dir.is_dir():
        pytest.skip(f"{arm} not present (transcripts are gitignored)")
    fired = set()
    checked = 0
    for hyp_path in sorted(arm_dir.glob("ASR-*.txt")):
        ref_path = GT / hyp_path.name
        if not ref_path.exists():
            continue
        checked += 1
        if S.detect_runaway(_flat(ref_path), _flat(hyp_path)) is not None:
            fired.add(hyp_path.stem)
    if checked == 0:
        pytest.skip(f"{arm} has no scoreable hypotheses on disk")
    assert fired == EXPECTED_RUNAWAYS[arm], (
        f"{arm}: expected {sorted(EXPECTED_RUNAWAYS[arm])}, fired on {sorted(fired)}"
    )


def test_refusal_is_cheaper_than_the_edit_distance():
    """The gate must run BEFORE the DP, not as a post-hoc filter.

    Scoring a 58,546-character hypothesis against a 3,405-character reference is ~200M
    pure-Python cells; it was measured at over 30 minutes before being killed. If the
    detector ever moved after the distance, this set could not be scored at all.
    """
    ref, hyp = _pair("qwen3-asr-1.7b", "ASR-012")
    row = S.score_item("ASR-012", ref, hyp, [], {"segments": []}, 263.0, "telephony_noise")
    assert "runaway" in row
    assert "cer_norm" not in row, "a refused item must carry no error rate"
    assert "cer_raw" not in row


def _varied(n: int) -> str:
    """`n` characters with no repeating unit, so only the LENGTH rule can fire.

    Two false starts are worth recording, because both produced a test that passed for the
    wrong reason or failed for one:

      * One repeated character trips the repetition rule by construction, which says
        nothing about the length threshold being probed.
      * An arithmetic walk over the alphabet (`i * 7 % 44`) is periodic -- it cycles every
        44 characters, so at 1,490 characters a 20-character window recurs often enough to
        clear the 20% coverage bar. It also tripped the repetition rule.

    A seeded PRNG has neither problem and is still reproducible.
    """
    alphabet = "กขคงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"
    rng = random.Random(4242)
    return "".join(rng.choice(alphabet) for _ in range(n))


@pytest.mark.parametrize("ratio,expected", [(1.49, None), (1.51, "length")])
def test_length_threshold_boundary(ratio, expected):
    verdict = S.detect_runaway(_varied(1000), _varied(int(1000 * ratio)))
    assert (verdict["kind"] if verdict else None) == expected


def test_a_short_hypothesis_is_never_a_runaway():
    """Truncation and silence are failures, but not THIS failure.

    They stay in the corpus rate because a deletion-dominated transcript is a real
    transcription outcome that CER describes correctly. Excluding them would delete the
    worst-performing items from the average and make the arm look better than it is.
    """
    ref = _varied(1000)
    assert S.detect_runaway(ref, "") is None
    assert S.detect_runaway(ref, _varied(100)) is None
    # Even a DEGENERATE short output stays in: one syllable repeated, but far too short to
    # be a loop. The 0.5x length guard in detect_runaway is what keeps it scoreable.
    assert S.detect_runaway(ref, "ก" * 100) is None
