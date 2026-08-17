"""Render a stratified slice of `retention_v3` to telephony audio.

WHY THIS SOURCE AND NOT `asr-eval/`. The end-to-end eval needs audio that already carries
QA ground truth. `asr-eval`'s 20 calls have exact transcripts and no labels; `retention_v3`
has 138 hand-labelled items and no audio. Labelling the asr-eval audio would mean deriving
`call_result`/`reason`/`product` from the transcripts with a model -- which is the exact
"built from the data it judges" error this repository has already retracted twice (the
`keyword` metric, and the first ASR-EXPECTATION.md, whose scratch script imported the code
it was supposed to check independently).

So the audio moves to the labels, never the labels to the audio. `gt` is copied byte-for-
byte out of `retention_v3.jsonl` and nothing here can touch it.

NUMBER EXPANSION AND WHICH TEXT IS "THE TRANSCRIPT".
A TTS voice handed `0810000001` reads it as one enormous cardinal, so digits are expanded
to spoken Thai BEFORE synthesis (same reason as `asr-eval/scripts/thai_num.py`). That makes
three different strings per item, and conflating any two of them corrupts a number:

    transcript_th   the original, with digits.  <- THE REFERENCE. A perfect ASR emits this.
    spoken_th       digits expanded.            <- fed to TTS only. An artifact of synthesis.
    <arm output>    what an ASR actually wrote. <- the candidate arm's input

The ceiling arm is given `transcript_th`, not `spoken_th`, because a real ASR writes digits
back as digits: `spoken_th` is how the sentence had to be *pronounced*, not how it would be
*transcribed*. Handing the ceiling arm `spoken_th` would make the ceiling differ from the
candidate in two ways at once -- ASR error and numeral orthography -- and the delta between
them would no longer isolate the ASR step, which is the only thing this eval exists to
measure.

STRATIFICATION. 30 items chosen to cover all four `call_result` classes, all four
`product` classes and as many `reason` classes as 30 items admit, spread across the pack's
mechanism families. It is a PILOT: 30 items is enough to prove the join and see a signal,
and is NOT enough to settle a migration. The power limit is stated in the report rather
than left for a reader to infer.
"""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(REPO / "asr-eval" / "scripts"))

import asr_common as C          # noqa: E402
import thai_num as N            # noqa: E402
import synthesize as S          # noqa: E402

SRC = REPO / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl"
OUT_DIR = ROOT / "audio"
PACK = ROOT / "pilot.jsonl"
CACHE = ROOT / "cache"

AGENT_VOICE = "th-TH-PremwadeeNeural"
CUSTOMER_VOICE = "th-TH-NiwatNeural"
AGENT_TAG = "เจ้าหน้าที่:"
CUSTOMER_TAG = "ลูกค้า:"

N_ITEMS = 30
MAX_CHARS = 4000        # excludes the handful of 15k-char long_context items: at ~13
                        # chars/sec they are 20-minute calls and would dominate the
                        # render budget without adding label coverage.


# ----------------------------------------------------------------------------- selection
def stratify(items: list[dict], n: int) -> list[dict]:
    """Greedy cover: repeatedly take the item adding the most unseen (dimension,label).

    Deterministic -- no RNG -- so the pack is reproducible from the source pack alone.
    """
    pool = [i for i in items if len(i["transcript_th"]) <= MAX_CHARS]

    def labels(it: dict) -> set[tuple[str, str]]:
        out: set[tuple[str, str]] = set()
        for g in it["gt"]:
            out.add(("call_result", g.get("call_result", "")))
            out.add(("product", str(g.get("product", "")).lower()))
            for col in ("main", "secondary", "third"):
                for part in str(g.get(col, "")).split(","):
                    part = part.strip()
                    if part:
                        out.add(("reason", part))
        out.add(("family", it["family"]))
        return out

    chosen: list[dict] = []
    seen: set[tuple[str, str]] = set()
    remaining = list(pool)
    while remaining and len(chosen) < n:
        best = max(remaining, key=lambda it: (len(labels(it) - seen), -len(it["transcript_th"])))
        gain = len(labels(best) - seen)
        if gain == 0:
            # coverage saturated; fill the rest by family balance, then by id for determinism
            counts = Counter(c["family"] for c in chosen)
            best = min(remaining, key=lambda it: (counts[it["family"]], it["item_id"]))
        seen |= labels(best)
        chosen.append(best)
        remaining.remove(best)
    return sorted(chosen, key=lambda it: it["item_id"])


# ------------------------------------------------------------------------ number expansion
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def expand_numbers(text: str) -> str:
    """Digits -> spoken Thai, so the TTS voice reads them the way a person would.

    Length is the discriminator, and it is the only one available without knowing what the
    number means: >= 9 digits is a phone number and is read digit by digit; anything
    shorter is read as a cardinal. A 4-digit year read as a cardinal is correct Thai, and
    a mis-read amount costs a number in one arm's ASR score, never a QA label -- `gt` does
    not depend on orthography.
    """
    def sub(m: re.Match) -> str:
        raw = m.group(0)
        digits = raw.replace(",", "")
        if "." in digits:
            whole, frac = digits.split(".", 1)
            if whole.isdigit() and frac.isdigit():
                return N.read_number(int(whole)) + " จุด " + N.read_digits(frac)
            return raw
        if not digits.isdigit():
            return raw
        if len(digits) >= 9:
            return N.read_digits(digits)
        return N.read_number(int(digits))
    return _NUM.sub(sub, text)


def parse_turns(transcript: str) -> list[tuple[str, str]]:
    """(speaker, text) in order. Speaker labels are stripped: they are not spoken."""
    turns: list[tuple[str, str]] = []
    for line in transcript.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith(AGENT_TAG):
            turns.append(("agent", line[len(AGENT_TAG):].strip()))
        elif line.startswith(CUSTOMER_TAG):
            turns.append(("customer", line[len(CUSTOMER_TAG):].strip()))
        elif turns:
            turns[-1] = (turns[-1][0], turns[-1][1] + " " + line)
        else:
            turns.append(("agent", line))
    return [(s, t) for s, t in turns if t]


# ------------------------------------------------------------------------------- rendering
_TTS_SEM = asyncio.Semaphore(6)
_TTS_RETRIES = 6

# Three things about this service, all learned by measurement, all presenting as the same
# opaque `NoAudioReceived`:
#
# 1. Omitting the explicit `rate`/`pitch` this edge-tts build wants returns zero audio
#    chunks. Always pass them.
# 2. `NoAudioReceived` is ALSO what throttling looks like. `อือ ค่ะ` failed four times in a
#    row and then synthesised fine seconds later, so the TEXT is never the diagnosis.
#    Anything that treats this as "bad input" and skips the turn would drop speech out of
#    the middle of a call while the reference transcript still claims it is there --
#    corrupting every arm on that item and reading as a model failure.
# 3. It throttles BURSTS and then recovers. Measured: 8 turns at concurrency 4 with long
#    backoff took 76.5 s; the same 8 at concurrency 8 with 0.6 s backoff took 1.3 s. The
#    cost was never the concurrency, it was stacking a patient retry on top of
#    `synthesize.tts`'s own patient retry -- one turn needing a second attempt burnt the
#    inner 15 s and then a 4-20 s outer sleep on top. Hence ONE retry layer, short backoff,
#    and `_tts_once` called directly rather than through `S.tts`.
#
# A dropped turn is never acceptable, so this raises rather than returning silence.


async def tts(text: str, voice: str) -> np.ndarray:
    from scipy import signal
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{S._cache_key(text, voice, '+0%', '+0Hz')}.mp3"
    if not path.exists():
        async with _TTS_SEM:
            last: Exception | None = None
            for attempt in range(_TTS_RETRIES):
                try:
                    path.write_bytes(await S._tts_once(text, voice, "+0%", "+0Hz"))
                    last = None
                    break
                except Exception as exc:                       # noqa: BLE001
                    last = exc
                    await asyncio.sleep(0.6 * (attempt + 1))
            if last is not None:
                raise RuntimeError(f"TTS exhausted for {text[:40]!r}") from last
    audio, sr = sf.read(str(path), dtype="float32", always_2d=False)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sr != C.MASTER_SAMPLE_RATE:
        audio = signal.resample_poly(audio, C.MASTER_SAMPLE_RATE, sr).astype(np.float32)
    return S.trim_silence(audio)


async def render(item: dict, profile: str) -> tuple[np.ndarray, str]:
    turns = parse_turns(item["transcript_th"])
    sr = C.MASTER_SAMPLE_RATE
    spoken_parts = [expand_numbers(t) for _, t in turns]
    # Turns are independent, so synthesise them concurrently; S.tts's own semaphore is
    # what keeps the free service happy, not the sequencing here.
    rendered = await asyncio.gather(*[
        tts(spoken, AGENT_VOICE if who == "agent" else CUSTOMER_VOICE)
        for (who, _), spoken in zip(turns, spoken_parts)
    ])
    chunks: list[np.ndarray] = []
    for x in rendered:
        chunks.append(x)
        chunks.append(np.zeros(int(sr * 0.28), dtype=np.float32))
    master = np.concatenate(chunks) if chunks else np.zeros(sr, dtype=np.float32)

    p = S.PROFILES[profile]
    rng = np.random.default_rng(abs(hash(item["item_id"])) % (2**32))
    y = S.normalise_rms(master, -20.0)
    if p["reverb"]:
        y = S.reverb(y, p["reverb"], p["wet"], rng)
    y = S.telephone_band(y)
    if p["snr"] < 40:
        y = S.add_noise_at_snr(y, p["snr"], rng)
    if p["hum"] > -60:
        y = S.add_hum(y, p["hum"], rng)
    if p["crackle"]:
        y = S.add_crackle(y, p["crackle"], rng)
    if p["dropouts"]:
        # NOTE: returns (audio, spans), unlike every other function in this chain. Binding
        # the tuple to `y` fails two steps later inside soft_clip with a TypeError about
        # multiplying a sequence -- far from the cause. The dropped spans are discarded
        # here; asr-eval keeps them for its own manifest, this pack has no use for them.
        y, _dropout_spans = S.add_dropouts(y, p["dropouts"], rng)
    if p["drive"] > 1.0:
        y = S.soft_clip(y, p["drive"])
    y = S.normalise_rms(y, -20.0 + p["gain"])
    y = S.mu_law_roundtrip(y)

    n = int(len(y) * C.DELIVERY_SAMPLE_RATE / sr)
    y8 = np.interp(np.linspace(0, len(y) - 1, n), np.arange(len(y)), y).astype(np.float32)
    return y8, "\n".join(spoken_parts)


async def main() -> int:
    items = [json.loads(l) for l in SRC.read_text(encoding="utf-8").splitlines()]
    chosen = stratify(items, N_ITEMS)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # One profile per item, cycled across the pack's acoustic families so an acoustic
    # effect can never be confounded with a single QA mechanism.
    profiles = ["clean_baseline", "telephony_noise", "numeric_dense", "proper_nouns",
                "code_switch", "far_field_low_gain"]
    pack = []
    for idx, it in enumerate(chosen):
        prof = profiles[idx % len(profiles)]
        y8, spoken = await render(it, prof)
        name = f"{it['call_id']}_{it['phone_number']}_120000_A900_test_agent_D_20260817_" \
               f"{int(len(y8)/C.DELIVERY_SAMPLE_RATE)}_IN.wav"
        sf.write(str(OUT_DIR / name), y8, C.DELIVERY_SAMPLE_RATE, subtype=C.DELIVERY_SUBTYPE)
        rec = dict(it)
        rec["audio_file"] = name
        rec["audio_profile"] = prof
        rec["spoken_th"] = spoken
        rec["audio_seconds"] = round(len(y8) / C.DELIVERY_SAMPLE_RATE, 1)
        pack.append(rec)
        print(f"  {it['item_id']:8s} {prof:20s} {rec['audio_seconds']:6.1f}s  {name}",
              flush=True)

    PACK.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in pack) + "\n",
                    encoding="utf-8")
    total = sum(r["audio_seconds"] for r in pack)
    print(f"\n{len(pack)} items, {total/60:.1f} min audio -> {PACK}")

    cov = defaultdict(Counter)
    for r in pack:
        for g in r["gt"]:
            cov["call_result"][g.get("call_result", "")] += 1
            cov["product"][str(g.get("product", "")).lower()] += 1
            for col in ("main", "secondary", "third"):
                for part in str(g.get(col, "")).split(","):
                    if part.strip():
                        cov["reason"][part.strip()] += 1
    for dim, c in cov.items():
        print(f"  {dim:12s} {dict(c)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
