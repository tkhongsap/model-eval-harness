"""Tests for the audio chain in synthesize.py.

Both regressions here are the same shape: a profile constant claimed an effect that the
chain did not actually deliver, so a family was labelled as harder than it was and every
per-family number would have been compared against the wrong condition. Neither showed up
in listening, in validation, or in the plots -- only in measurement.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import synthesize as S  # noqa: E402


@pytest.fixture()
def noise():
    return (np.random.default_rng(11).standard_normal(S.SR * 3) * 0.1).astype(np.float32)


# --- reverb ---------------------------------------------------------------------------


def test_reverb_tail_is_audible_and_at_the_declared_level(noise) -> None:
    """Regression 1: an L1-normalised random-sign IR put the tail ~35 dB down.

    Dividing by the sum of |ir| scales a random-sign IR by roughly 1/sqrt(n), and n is
    ~10,000 samples here. `far_field_low_gain` was labelled reverberant while measuring dry.

    The direct path is unity, so the tail is exactly `out - x` and `wet` is checkable.
    """
    for wet in (0.10, 0.30):
        out = S.reverb(noise, 0.45, wet, np.random.default_rng(2))
        tail_db = 20 * np.log10(S.rms(out - noise) / S.rms(noise))
        expected = 20 * np.log10(wet)
        assert abs(tail_db - expected) < 2.0, (
            f"wet={wet} should put the tail near {expected:.1f} dB, measured {tail_db:.1f}"
        )
    # The far-field profile in particular must be plainly audible, not the ~-35 dB the
    # L1-normalised version produced.
    out = S.reverb(noise, 0.45, 0.30, np.random.default_rng(2))
    assert 20 * np.log10(S.rms(out - noise) / S.rms(noise)) > -15.0


def test_reverb_does_not_destroy_the_syllable_envelope() -> None:
    """Regression 2: the fix for the above overshot in the opposite direction.

    L2-normalising the WHOLE impulse response -- direct path included -- buries the single
    direct sample under thousands of tail samples, so the output is pure smear. Measured on
    a real file, the envelope peak fell from 5.18 Hz to 1.07 Hz: the modulation that
    carries speech was gone, while every level check still passed.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_audio import modulation_peak_hz

    # Synthetic speech-like envelope: a 4 Hz amplitude modulation on a voiced carrier.
    t = np.arange(S.SR * 8) / S.SR
    env = 0.5 * (1.0 + np.sin(2 * np.pi * 4.0 * t))
    x = (env * np.sin(2 * np.pi * 220 * t) * 0.3).astype(np.float32)
    probe = modulation_peak_hz(x, S.SR)
    assert 3.0 <= probe <= 5.0, f"the probe itself reads {probe:.2f} Hz, expected ~4 Hz"

    out = S.reverb(x, 0.45, 0.30, np.random.default_rng(2))
    got = modulation_peak_hz(out, S.SR)
    assert 2.0 <= got <= 8.0, (
        f"reverb dropped the envelope peak to {got:.2f} Hz -- the profile is smearing the "
        f"speech rather than adding a room"
    )



def test_reverb_with_zero_wet_is_a_no_op(noise) -> None:
    out = S.reverb(noise, 0.45, 0.0, np.random.default_rng(2))
    assert np.allclose(out, noise, atol=1e-6)


def test_reverb_preserves_length_and_stays_finite(noise) -> None:
    out = S.reverb(noise, 0.45, 0.38, np.random.default_rng(2))
    assert out.size == noise.size
    assert np.all(np.isfinite(out))


# --- hum ------------------------------------------------------------------------------


def test_hum_is_injected_at_the_level_the_profile_declares(noise) -> None:
    """`level_db` must mean hum RMS relative to speech RMS, checkable by measurement."""
    out = S.add_hum(noise, -30.0, np.random.default_rng(3))
    injected = out - noise
    got = 20 * np.log10(S.rms(injected) / S.rms(noise))
    assert -32.0 <= got <= -28.0, f"declared -30 dB, measured {got:.1f} dB"


def test_hum_survives_the_telephone_band_limit(noise) -> None:
    """Regression: hum was synthesised only at 50 Hz and 150 Hz.

    The 300-3400 Hz band-limit applied afterwards removed both completely, so every
    `telephony_noise` file was labelled as having hum and measurably had none.
    """
    injected = S.add_hum(noise, -30.0, np.random.default_rng(3)) - noise
    in_band = S.telephone_band(injected)

    pure_50hz = (0.05 * np.sin(2 * np.pi * 50 * np.arange(noise.size) / S.SR)).astype(np.float32)
    gone = S.telephone_band(pure_50hz)

    surviving_db = 20 * np.log10(max(S.rms(in_band), 1e-12) / S.rms(noise))
    removed_db = 20 * np.log10(max(S.rms(gone), 1e-12) / S.rms(pure_50hz))
    assert surviving_db > removed_db + 15.0, (
        f"hum survives at {surviving_db:.1f} dB while a bare 50 Hz tone is cut to "
        f"{removed_db:.1f} dB -- the harmonic series is not reaching the passband"
    )


# --- the rest of the chain --------------------------------------------------------------


def test_telephone_band_removes_out_of_band_energy() -> None:
    t = np.arange(S.SR) / S.SR
    for freq, should_survive in ((100, False), (1000, True), (3000, True), (6000, False)):
        tone = (0.2 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        kept = S.rms(S.telephone_band(tone)) / S.rms(tone)
        if should_survive:
            assert kept > 0.5, f"{freq} Hz should pass, kept {kept:.3f}"
        else:
            assert kept < 0.1, f"{freq} Hz should be rejected, kept {kept:.3f}"


def test_mu_law_roundtrip_is_lossy_but_not_destructive(noise) -> None:
    out = S.mu_law_roundtrip(noise)
    snr = 10 * np.log10(np.mean(noise ** 2) / np.mean((out - noise) ** 2))
    assert 25.0 < snr < 60.0, f"mu-law SNR {snr:.1f} dB is outside the plausible G.711 range"


def test_add_noise_hits_the_requested_snr(noise) -> None:
    for target in (10.0, 20.0, 30.0):
        out = S.add_noise_at_snr(noise, target, np.random.default_rng(4))
        got = 20 * np.log10(S.rms(noise) / S.rms(out - noise))
        assert abs(got - target) < 1.0, f"asked {target} dB, got {got:.1f} dB"


def test_dropouts_are_reported_where_they_are_made(noise) -> None:
    out, spans = S.add_dropouts(noise, 60.0, np.random.default_rng(5))
    assert spans, "no dropouts produced"
    for s in spans:
        mid = int((s["start_s"] + s["dur_s"] / 2) * S.SR)
        assert abs(out[mid]) < 1e-6, f"span at {s['start_s']}s is not actually silent"


def test_hold_music_is_not_speech_shaped(noise) -> None:
    """Hold music must be tonal. If it modulated at a syllable rate the `hold_ivr`
    insertion probe would be measuring speech the arm was right to transcribe."""
    music = S.hold_music(6.0, np.random.default_rng(6))
    assert music.size == int(6.0 * S.SR)
    assert 0.05 < float(np.abs(music).max()) <= 1.0


def test_trim_silence_keeps_the_speech(noise) -> None:
    padded = np.concatenate([np.zeros(S.SR, dtype=np.float32), noise,
                             np.zeros(S.SR, dtype=np.float32)])
    trimmed = S.trim_silence(padded)
    assert trimmed.size < padded.size
    assert trimmed.size >= noise.size * 0.9
