"""Render each composed dialogue to a production-shaped .wav.

Chain, per call:

    per-turn neural TTS (edge-tts, 24 kHz mp3)
      -> decode, trim, per-speaker gain
      -> place on a 24 kHz master timeline honouring gaps and overlaps
      -> family degradation profile (reverb / noise / hum / crackle / dropout / clipping)
      -> telephone band-limit 300-3400 Hz
      -> G.711 mu-law companding round trip
      -> resample to 8 kHz, write 16-bit mono PCM

Why the degradation is applied at 24 kHz and the band-limit last: adding noise after
band-limiting would put energy back outside the telephone band that a real telephone
channel could never have carried, and the whole point of the profile is that the delivered
file could plausibly have come out of Verint.

TTS OUTPUT IS CACHED under asr-eval/cache/ keyed by sha256(text|voice|rate|pitch). The
audio chain can therefore be re-tuned and every file rebuilt without spending a single new
call to the TTS service. Delete the cache to force re-synthesis -- and note that neural TTS
is not bit-reproducible, so a rebuild after deleting it will NOT reproduce the committed
sha256 values. That is recorded in README.md rather than worked around.

Run:
    python asr-eval/scripts/synthesize.py            # all 20
    python asr-eval/scripts/synthesize.py ASR-003    # one
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sys
from pathlib import Path

import edge_tts
import numpy as np
import soundfile as sf
from scipy import signal

import asr_common as C

SR = C.MASTER_SAMPLE_RATE          # 24 000, the rate edge-tts returns
TTS_CONCURRENCY = 4                # polite to a free service; higher earns 429s
TTS_RETRIES = 4


# --------------------------------------------------------------------------------------
# TTS with an on-disk cache
# --------------------------------------------------------------------------------------


def _cache_key(text: str, voice: str, rate: str, pitch: str) -> str:
    h = hashlib.sha256(f"{text}|{voice}|{rate}|{pitch}".encode()).hexdigest()
    return h[:32]


async def _tts_once(text: str, voice: str, rate: str, pitch: str) -> bytes:
    comm = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
    buf = io.BytesIO()
    async for chunk in comm.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    data = buf.getvalue()
    if not data:
        raise RuntimeError("TTS returned no audio")
    return data


async def tts(text: str, voice: str, rate: str, pitch: str, sem: asyncio.Semaphore) -> np.ndarray:
    """Return mono float32 at SR for one turn, from cache when possible."""
    C.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = C.CACHE_DIR / f"{_cache_key(text, voice, rate, pitch)}.mp3"

    if not path.exists():
        async with sem:
            last: Exception | None = None
            for attempt in range(TTS_RETRIES):
                try:
                    path.write_bytes(await _tts_once(text, voice, rate, pitch))
                    last = None
                    break
                except Exception as exc:                      # noqa: BLE001
                    last = exc
                    await asyncio.sleep(1.5 * (attempt + 1))
            if last is not None:
                raise RuntimeError(f"TTS failed after {TTS_RETRIES} tries: {text[:40]!r}") from last

    audio, sr = sf.read(path, dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != SR:
        audio = signal.resample_poly(audio, SR, sr).astype(np.float32)
    return trim_silence(audio)


def trim_silence(x: np.ndarray, thresh_db: float = -45.0, pad_ms: int = 40) -> np.ndarray:
    """Strip the leading/trailing silence the TTS service pads onto every clip.

    Without this the inter-turn gaps in the composed dialogue are meaningless: each turn
    would carry several hundred ms of its own silence and the call would run long and sound
    stilted.
    """
    if x.size == 0:
        return x
    win = max(1, int(SR * 0.01))
    env = np.sqrt(np.convolve(x.astype(np.float64) ** 2, np.ones(win) / win, mode="same"))
    peak = env.max()
    if peak <= 0:
        return x
    live = np.flatnonzero(20 * np.log10(np.maximum(env / peak, 1e-9)) > thresh_db)
    if live.size == 0:
        return x
    pad = int(SR * pad_ms / 1000)
    return x[max(0, live[0] - pad): min(x.size, live[-1] + pad)]


# --------------------------------------------------------------------------------------
# Signal helpers
# --------------------------------------------------------------------------------------


def db_to_lin(db: float) -> float:
    return float(10.0 ** (db / 20.0))


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2))) if x.size else 0.0


def normalise_rms(x: np.ndarray, target_db: float) -> np.ndarray:
    cur = rms(x)
    if cur <= 0:
        return x
    return (x * (db_to_lin(target_db) / cur)).astype(np.float32)


def telephone_band(x: np.ndarray, low: float = 300.0, high: float = 3400.0) -> np.ndarray:
    """G.712-ish passband. Applied LAST so nothing re-introduces out-of-band energy."""
    nyq = SR / 2.0
    sos = signal.butter(6, [low / nyq, min(high / nyq, 0.99)], btype="band", output="sos")
    return signal.sosfilt(sos, x).astype(np.float32)


def mu_law_roundtrip(x: np.ndarray, mu: float = 255.0) -> np.ndarray:
    """G.711 mu-law encode/decode. The quantisation noise is the point, not the codec."""
    x = np.clip(x, -1.0, 1.0)
    comp = np.sign(x) * np.log1p(mu * np.abs(x)) / np.log1p(mu)
    q = np.round((comp + 1.0) * 127.5)                       # 8-bit
    comp_q = q / 127.5 - 1.0
    expanded = np.sign(comp_q) * (1.0 / mu) * ((1.0 + mu) ** np.abs(comp_q) - 1.0)
    return expanded.astype(np.float32)


def pink_noise(n: int, rng: np.random.Generator) -> np.ndarray:
    """Voss-ish pink noise via a one-pole cascade. Line hiss is not white."""
    white = rng.standard_normal(n)
    b = [0.049922035, -0.095993537, 0.050612699, -0.004408786]
    a = [1.0, -2.494956002, 2.017265875, -0.522189400]
    out = signal.lfilter(b, a, white)
    return (out / (np.abs(out).max() + 1e-12)).astype(np.float32)


def add_noise_at_snr(x: np.ndarray, snr_db: float, rng: np.random.Generator) -> np.ndarray:
    sig = rms(x)
    if sig <= 0:
        return x
    noise = pink_noise(x.size, rng)
    noise = noise * (sig / db_to_lin(snr_db) / (rms(noise) + 1e-12))
    return (x + noise).astype(np.float32)


def add_hum(x: np.ndarray, level_db: float, rng: np.random.Generator) -> np.ndarray:
    """50 Hz mains hum and its harmonic series -- Thailand runs 50 Hz.

    The harmonics are the point, not decoration. The first version synthesised only 50 Hz
    and 150 Hz, and the telephone band-limit applied later at 300-3400 Hz removed both
    COMPLETELY: every `telephony_noise` file was labelled as having hum and measurably had
    none. Real mains interference on a copper pair buzzes rather than drones precisely
    because its harmonic series reaches well into the voice band, so the series is what has
    to be generated.
    """
    t = np.arange(x.size) / SR
    amp = rms(x) * db_to_lin(level_db)
    hum = np.zeros(x.size)
    for k in range(1, 15):                       # 50 Hz .. 700 Hz
        phase = float(rng.uniform(0, 2 * np.pi))
        hum += (1.0 / k) * np.sin(2 * np.pi * 50 * k * t + phase)
    # Scale by RMS, not by peak, so `level_db` means "hum RMS this far below speech RMS"
    # -- a statement anyone can check with a measurement. Peak-scaling made the constant
    # mean nothing in particular. Note the DELIVERED in-band hum is lower than level_db,
    # because the 300-3400 Hz band-limit later removes harmonics 1-5; that is what a real
    # telephone channel does to mains hum too.
    hum *= amp / (np.sqrt(np.mean(hum ** 2)) + 1e-12)
    return (x + hum.astype(np.float32)).astype(np.float32)


def add_crackle(x: np.ndarray, per_sec: float, rng: np.random.Generator) -> np.ndarray:
    """Impulsive clicks, the way a bad copper line or a lossy handoff sounds."""
    y = x.copy()
    n_clicks = int(per_sec * x.size / SR)
    if n_clicks <= 0:
        return y
    peak = np.abs(x).max() + 1e-12
    for pos in rng.integers(0, max(1, x.size - 64), size=n_clicks):
        width = int(rng.integers(4, 40))
        env = np.exp(-np.linspace(0, 6, width))
        y[pos: pos + width] += (
            rng.choice([-1.0, 1.0]) * peak * rng.uniform(0.15, 0.55) * env
        ).astype(np.float32)
    return y


def add_dropouts(x: np.ndarray, per_min: float, rng: np.random.Generator) -> tuple[np.ndarray, list]:
    """Zero short spans, the way a dropped packet or a fading cell sounds.

    Returns the spans so validate_audio.py can prove the dropouts it finds are the ones we
    put there, rather than a bug in the assembly.
    """
    y = x.copy()
    n = int(per_min * x.size / SR / 60)
    spans = []
    for _ in range(max(0, n)):
        dur = rng.uniform(0.04, 0.22)
        w = int(dur * SR)
        if w >= x.size:
            continue
        pos = int(rng.integers(0, x.size - w))
        ramp = int(0.004 * SR)
        seg = np.ones(w, dtype=np.float32)
        seg[:ramp] = np.linspace(1, 0, ramp)
        seg[-ramp:] = np.linspace(0, 1, ramp)
        seg[ramp:-ramp] = 0.0
        y[pos: pos + w] *= seg
        spans.append({"start_s": round(pos / SR, 3), "dur_s": round(dur, 3)})
    return y, spans


def reverb(x: np.ndarray, rt60_s: float, wet: float, rng: np.random.Generator) -> np.ndarray:
    """Exponential-decay room: a unit direct path plus a scaled diffuse tail.

    `wet` is the RMS energy of the reverberant tail relative to the direct path, so
    wet = 0.30 means a direct-to-reverberant ratio of about +10 dB -- a live-ish room, not
    a cathedral.

    THIS FUNCTION HAS BEEN WRONG TWICE, IN OPPOSITE DIRECTIONS, AND BOTH WERE INVISIBLE
    WITHOUT MEASURING:

      1. L1-normalising a random-sign IR (`ir / sum(|ir|)`) scales it by roughly 1/sqrt(n),
         n being ~10,000 samples here. The wet signal came out ~35 dB down and
         `far_field_low_gain` was labelled reverberant while measuring dry.

      2. The fix for that -- L2-normalising the whole IR, direct path included -- overshot.
         The tail has thousands of samples and the direct path has one, so normalising
         their combined energy buries the direct path: the output is almost entirely
         diffuse. Measured effect on a real file: the syllable-rate envelope peak collapsed
         from 5.18 Hz to 1.07 Hz, i.e. the modulation that carries speech was destroyed.
         validate_audio's speech-rate check caught it on ASR-009.

    The direct path is therefore held at exactly 1.0 and only the TAIL is scaled. That is
    what a room impulse response actually looks like, and it keeps `wet` meaningful without
    smearing the speech into mush.
    """
    if wet <= 0:
        return x
    n = max(16, int(rt60_s * SR))
    tail = rng.standard_normal(n) * np.exp(-np.linspace(0, 6.9, n))
    tail[0] = 0.0
    tail = tail / (np.sqrt(np.sum(tail ** 2)) + 1e-12) * wet
    ir = tail
    ir[0] = 1.0
    return signal.fftconvolve(x, ir)[: x.size].astype(np.float32)


def soft_clip(x: np.ndarray, drive: float) -> np.ndarray:
    return np.tanh(x * drive).astype(np.float32) / np.tanh(drive)


def hold_music(seconds: float, rng: np.random.Generator) -> np.ndarray:
    """Synthetic on-hold music.

    Deliberately tonal and repetitive. Its job in this set is to be non-speech audio that
    an ASR arm may be tempted to hallucinate words into -- the `hold_ivr` family scores
    insertions against exactly these spans.
    """
    n = int(seconds * SR)
    t = np.arange(n) / SR
    prog = [(261.63, 329.63, 392.00), (293.66, 349.23, 440.00),
            (349.23, 440.00, 523.25), (392.00, 493.88, 587.33)]
    bar = 2.0
    out = np.zeros(n, dtype=np.float32)
    for i in range(int(np.ceil(seconds / bar))):
        chord = prog[i % len(prog)]
        s, e = int(i * bar * SR), min(n, int((i + 1) * bar * SR))
        if s >= e:
            break
        tt = t[s:e] - t[s]
        env = np.minimum(1.0, tt / 0.05) * np.exp(-tt * 0.55)
        seg = sum(np.sin(2 * np.pi * f * tt) / (k + 1.6) for k, f in enumerate(chord))
        out[s:e] += (seg * env).astype(np.float32)
    out += 0.12 * np.sin(2 * np.pi * 110 * t) * np.exp(-((t % bar)) * 1.1)
    peak = np.abs(out).max() + 1e-12
    return (out / peak * 0.28).astype(np.float32)


# --------------------------------------------------------------------------------------
# Family profiles
# --------------------------------------------------------------------------------------
# One row per mechanism family. These numbers are the experiment: they are what makes
# `telephony_noise` a different condition from `clean_baseline` rather than a different
# label on the same audio.

PROFILES: dict[str, dict] = {
    "clean_baseline":     dict(snr=34, hum=-60, crackle=0.0, dropouts=0.0, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.0),
    "telephony_noise":    dict(snr=13, hum=-30, crackle=2.2, dropouts=3.0, reverb=0.0,
                               wet=0.0, gain=-3.0, drive=1.6),
    "code_switch":        dict(snr=29, hum=-52, crackle=0.2, dropouts=0.3, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.0),
    "numeric_dense":      dict(snr=27, hum=-50, crackle=0.4, dropouts=0.5, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.1),
    "proper_nouns":       dict(snr=28, hum=-50, crackle=0.3, dropouts=0.3, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.0),
    "disfluency":         dict(snr=26, hum=-48, crackle=0.5, dropouts=0.5, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.1),
    "overlap_crosstalk":  dict(snr=25, hum=-46, crackle=0.6, dropouts=0.8, reverb=0.12,
                               wet=0.10, gain=0.0, drive=1.2),   # a small room, not a hall
    "long_context":       dict(snr=30, hum=-52, crackle=0.2, dropouts=0.6, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.0),
    # gain was -16 dB in the first build, which put the file at -44 dBFS RMS with 54% of
    # frames under the silence threshold -- close enough to the -45 dBFS validation floor
    # that the family was one tweak away from being unusable rather than hard. -11 dB keeps
    # it clearly the quietest, most reverberant thing in the set while leaving the speech
    # comfortably above the noise the profile adds.
    # wet=0.30 puts the reverberant tail 10.6 dB below the direct path -- a live room, and
    # measurably still speech: the syllable-rate envelope peak survives at 5.18 Hz. At 0.38
    # it collapses to 1.07 Hz, because past roughly 0.34 the long-term level envelope
    # overtakes the syllable peak entirely. Chosen by measuring, not by relaxing
    # validate_audio's speech-rate check, which is what caught the difference.
    "far_field_low_gain": dict(snr=11, hum=-40, crackle=0.4, dropouts=1.0, reverb=0.45,
                               wet=0.30, gain=-11.0, drive=1.0),
    "hold_ivr":           dict(snr=24, hum=-46, crackle=0.5, dropouts=0.8, reverb=0.0,
                               wet=0.0, gain=0.0, drive=1.1),
}

# Speech level before the channel. -20 dBFS RMS is a conventional broadcast/telephony
# operating level and leaves headroom for the overlap family, where two voices sum.
SPEECH_RMS_DB = -20.0
# The agent is on a headset in a contact centre, the customer on a handset somewhere else.
# Giving them different levels and slightly different channels is what stops the two voices
# being separable by level alone, which would make diarisation artificially easy.
AGENT_GAIN_DB = 0.0
CUSTOMER_GAIN_DB = -2.5
IVR_GAIN_DB = -4.0


# --------------------------------------------------------------------------------------
# Assembly
# --------------------------------------------------------------------------------------


async def render(dlg: dict, sem: asyncio.Semaphore) -> dict:
    item_id = dlg["item_id"]
    family = dlg["family"]
    prof = PROFILES[family]
    rng = np.random.default_rng(dlg["seed"])

    voices = {
        "agent": dlg["agent_voice"],
        "customer": dlg["customer_voice"],
        # The IVR uses the voice the customer is NOT using, so a listener can tell the
        # machine prompt from the human on the line.
        "ivr": dlg["agent_voice"],
    }

    # 1. Synthesise every speech/ivr turn concurrently.
    speech_turns = [t for t in dlg["turns"] if t["kind"] in ("speech", "ivr")]
    rendered = await asyncio.gather(
        *(tts(t["text"], voices[t["speaker"]], t["rate"], t["pitch"], sem) for t in speech_turns)
    )
    by_idx = {t["idx"]: a for t, a in zip(speech_turns, rendered)}

    # 2. Lay them out on a timeline.
    pieces: list[tuple[int, np.ndarray]] = []
    timeline: list[dict] = []
    cursor = 0
    lead_in = int(0.6 * SR)
    cursor += lead_in

    for t in dlg["turns"]:
        if t["kind"] == "hold":
            secs = t.get("hold_s", 8.0)
            music = hold_music(secs, rng)
            pieces.append((cursor, music))
            timeline.append({"idx": t["idx"], "kind": "hold", "start_s": round(cursor / SR, 3),
                             "dur_s": round(secs, 3), "speaker": "ivr"})
            cursor += music.size
            continue

        audio = by_idx[t["idx"]]
        if t["kind"] == "ivr":
            audio = normalise_rms(audio, SPEECH_RMS_DB + IVR_GAIN_DB)
            audio = telephone_band(audio, 350.0, 3000.0)      # prompts are pre-recorded
            audio = soft_clip(audio, 1.5)
        else:
            gain = AGENT_GAIN_DB if t["speaker"] == "agent" else CUSTOMER_GAIN_DB
            audio = normalise_rms(audio, SPEECH_RMS_DB + gain)

        gap = int(t["gap_before_s"] * SR)
        overlap = int(t["overlap_s"] * SR)
        start = max(0, cursor + gap - overlap)
        pieces.append((start, audio))
        timeline.append({"idx": t["idx"], "kind": t["kind"], "speaker": t["speaker"],
                         "start_s": round(start / SR, 3),
                         "dur_s": round(audio.size / SR, 3),
                         "overlap_s": t["overlap_s"]})
        cursor = start + audio.size

    total = cursor + int(0.8 * SR)                            # tail
    master = np.zeros(total, dtype=np.float32)
    for start, audio in pieces:
        end = min(total, start + audio.size)
        master[start:end] += audio[: end - start]

    # 3. Channel. Order matters: room -> level -> additive noise -> nonlinearity ->
    #    dropouts -> band-limit -> companding.
    if prof["wet"] > 0:
        master = reverb(master, max(prof["reverb"], 0.05), prof["wet"], rng)
    if prof["gain"]:
        master = (master * db_to_lin(prof["gain"])).astype(np.float32)
    master = add_noise_at_snr(master, prof["snr"], rng)
    if prof["hum"] > -59:
        master = add_hum(master, prof["hum"], rng)
    if prof["crackle"]:
        master = add_crackle(master, prof["crackle"], rng)
    if prof["drive"] > 1.0:
        master = soft_clip(master, prof["drive"])
    dropout_spans: list = []
    if prof["dropouts"]:
        master, dropout_spans = add_dropouts(master, prof["dropouts"], rng)

    master = telephone_band(master)
    master = mu_law_roundtrip(master)

    peak = np.abs(master).max()
    if peak > 0.98:
        master = (master * (0.98 / peak)).astype(np.float32)

    # 4. Deliver at 8 kHz, and keep the 24 kHz master for re-deriving a different profile.
    delivery = signal.resample_poly(master, C.DELIVERY_SAMPLE_RATE, SR).astype(np.float32)

    # Headroom is applied AFTER resampling, not before. The polyphase filter overshoots on
    # transients, so a signal left at 0.98 before the resample comes out above 1.0 and the
    # clip below flattens it at exactly full scale. Five of the first twenty files failed
    # validate_audio's clipping check that way, at 0.00 dBFS. Scaling down here instead
    # keeps the peak honest; it only ever attenuates, so the quiet far-field profile is not
    # normalised back up into being loud.
    DELIVERY_PEAK_DBFS = -1.0
    target = float(10.0 ** (DELIVERY_PEAK_DBFS / 20.0))
    dpeak = float(np.abs(delivery).max())
    if dpeak > target:
        delivery = (delivery * (target / dpeak)).astype(np.float32)
    delivery = np.clip(delivery, -1.0, 1.0)

    duration_s = delivery.size / C.DELIVERY_SAMPLE_RATE
    meta = dict(dlg["meta"])
    meta["duration"] = int(round(duration_s))
    call = C.CallMeta(**meta)
    errs = call.validate()
    if errs:
        raise AssertionError(f"{item_id}: {errs}")

    C.AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    C.MASTER_DIR.mkdir(parents=True, exist_ok=True)
    out = C.AUDIO_DIR / call.filename()
    sf.write(out, delivery, C.DELIVERY_SAMPLE_RATE, subtype=C.DELIVERY_SUBTYPE)
    sf.write(C.MASTER_DIR / f"{item_id}.master.wav", master, SR, subtype="PCM_16")

    (C.GROUND_TRUTH_DIR / f"{item_id}.timeline.json").write_text(
        json.dumps({"item_id": item_id, "sample_rate": C.DELIVERY_SAMPLE_RATE,
                    "segments": timeline, "dropout_spans": dropout_spans},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    sha = hashlib.sha256(out.read_bytes()).hexdigest()
    speech_s = sum(s["dur_s"] for s in timeline if s["kind"] == "speech")
    return {
        "item_id": item_id,
        "family": family,
        "scenario": dlg["scenario"],
        "direction": dlg["call_direction"],
        "filename": call.filename(),
        "duration_s": round(duration_s, 2),
        "target_s": dlg["target_duration_s"],
        "speech_s": round(speech_s, 2),
        "speech_ratio": round(speech_s / duration_s, 3),
        "hold_s": round(sum(s["dur_s"] for s in timeline if s["kind"] == "hold"), 2),
        "turns": len(dlg["turns"]),
        "bytes": out.stat().st_size,
        "sha256": sha,
        "profile": prof,
        "dropouts": len(dropout_spans),
    }


async def main() -> None:
    wanted = set(sys.argv[1:])
    files = sorted(C.DIALOGUE_DIR.glob("ASR-*.json"))
    if not files:
        raise SystemExit("no dialogues; run compose_dialogues.py first")

    sem = asyncio.Semaphore(TTS_CONCURRENCY)
    rows = []
    for path in files:
        dlg = json.loads(path.read_text(encoding="utf-8"))
        if wanted and dlg["item_id"] not in wanted:
            continue
        row = await render(dlg, sem)
        rows.append(row)
        flag = "" if 180 <= row["duration_s"] <= 600 else "   <-- OUT OF BAND"
        print(
            f"{row['item_id']}  {row['family']:19s} {row['duration_s']:6.1f}s "
            f"(target {row['target_s']:3d}s) speech={row['speech_ratio']:.2f} "
            f"{row['bytes'] / 1e6:5.2f} MB{flag}"
        )

    if not wanted:
        C.MANIFEST.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        total = sum(r["duration_s"] for r in rows)
        size = sum(r["bytes"] for r in rows)
        print(f"\n{len(rows)} files, {total / 60:.1f} min, {size / 1e6:.1f} MB")
        print(f"duration range {min(r['duration_s'] for r in rows):.1f}s "
              f"- {max(r['duration_s'] for r in rows):.1f}s")


if __name__ == "__main__":
    asyncio.run(main())
