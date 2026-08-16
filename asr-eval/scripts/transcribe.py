"""Send the eval set to an OpenAI-compatible ASR endpoint and save the transcripts.

Deliberately stdlib-only for HTTP (urllib; no requests, no openai package). Two reasons:

  1. requirements-asr.txt exists so this track cannot drag a dependency into the pinned
     root environment, and the fewer packages it names the smaller that risk is.
  2. src/evalharness/ imports no model client and tests/test_boundary.py fails the build
     if that ever changes. Nothing here is imported by the scoring package, but keeping the
     ASR runner dependency-free means it can never become the reason someone relaxes that
     boundary.

Endpoint shape assumed: POST {base}/audio/transcriptions, multipart/form-data with `file`
and `model`, returning {"text": ...}. That is what vLLM and the Token Factory gateway
serve, and what .env.example anticipates for a self-hosted Qwen3-ASR (".env.example:44-51":
the Thai ASR track cannot be tested through OpenRouter, so self-host it on the internal GPU
"which is the plan anyway").

CHUNKING. Most ASR endpoints cap request length well below this set's 3-10 minutes.
--chunk-seconds splits at the QUIETEST point near each boundary rather than at a fixed
offset, so a split never lands mid-word. Pieces are concatenated in order, and the chunk
count is recorded per item -- a WER must never be compared across two different chunking
regimes without that being visible.

Run:
    python asr-eval/scripts/transcribe.py \
        --base-url http://10.94.154.104:8000/v1 \
        --model qwen3-asr \
        --arm qwen3-asr-internal \
        --chunk-seconds 120
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

import asr_common as C


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


def build_multipart(fields: dict[str, str], filename: str, blob: bytes) -> tuple[bytes, str]:
    boundary = f"----asreval{uuid.uuid4().hex}"
    buf = io.BytesIO()
    for key, value in fields.items():
        buf.write(f"--{boundary}\r\n".encode())
        buf.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        buf.write(f"{value}\r\n".encode())
    buf.write(f"--{boundary}\r\n".encode())
    buf.write(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode()
    )
    buf.write(b"Content-Type: audio/wav\r\n\r\n")
    buf.write(blob)
    buf.write(f"\r\n--{boundary}--\r\n".encode())
    return buf.getvalue(), f"multipart/form-data; boundary={boundary}"


def post_audio(base_url: str, model: str, api_key: str, filename: str, blob: bytes,
               language: str, timeout: int, retries: int) -> str:
    url = base_url.rstrip("/") + "/audio/transcriptions"
    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language
    body, content_type = build_multipart(fields, filename, blob)

    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", content_type)
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            # Accept the two shapes servers actually return.
            if isinstance(payload, dict):
                for key in ("text", "transcription"):
                    if key in payload:
                        return str(payload[key])
                if "results" in payload:
                    return " ".join(r.get("text", "") for r in payload["results"])
            raise RuntimeError(f"unrecognised response shape: {list(payload)[:6]}")
        except Exception as exc:                                  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"transcription failed after {retries} attempts") from last


# --------------------------------------------------------------------------------------
# Chunking
# --------------------------------------------------------------------------------------


def split_points(x: np.ndarray, sr: int, chunk_s: float, search_s: float = 8.0) -> list[int]:
    """Sample offsets to cut at: the quietest 20 ms frame near each nominal boundary.

    Cutting at a fixed offset lands mid-syllable roughly always, and both sides of the cut
    then start or end with half a word. That shows up as a substitution in WER and gets
    blamed on the model.

    Two guards, both of which the first version lacked and a smoke test caught:

      * the search window never reaches back before the previous cut plus half a chunk.
        Without that, a long silence inside the window is re-found at every iteration and
        the function emits hundreds of 10 ms chunks instead of a handful of real ones.
      * the search radius is capped at a third of the chunk length, so a caller passing a
        radius comparable to the chunk cannot make the window swamp the chunk.
    """
    if chunk_s <= 0 or x.size <= chunk_s * sr:
        return []
    win = max(1, int(sr * 0.02))
    step = int(chunk_s * sr)
    search = int(min(search_s, chunk_s / 3.0) * sr)
    min_advance = max(win, step // 2)

    cuts: list[int] = []
    prev = 0
    pos = step
    while pos < x.size - sr:
        lo = max(prev + min_advance, pos - search)
        hi = min(x.size - win, pos + search)
        cut = pos
        if hi > lo:
            region = x[lo:hi]
            n = (region.size // win) * win
            if n > 0:
                frames = region[:n].reshape(-1, win).astype(np.float64)
                cut = lo + int(np.argmin((frames ** 2).mean(axis=1))) * win + win // 2
        if cut <= prev:                       # never go backwards or stand still
            cut = pos
        cuts.append(cut)
        prev = cut
        pos = prev + step
    return cuts


def chunks_of(x: np.ndarray, sr: int, chunk_s: float) -> list[np.ndarray]:
    cuts = split_points(x, sr, chunk_s)
    if not cuts:
        return [x]
    bounds = [0, *cuts, x.size]
    return [x[a:b] for a, b in zip(bounds, bounds[1:]) if b > a]


def to_wav_bytes(x: np.ndarray, sr: int) -> bytes:
    buf = io.BytesIO()
    sf.write(buf, x, sr, subtype=C.DELIVERY_SUBTYPE, format="WAV")
    return buf.getvalue()


# --------------------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", required=True, help="e.g. http://host:8000/v1")
    ap.add_argument("--model", required=True)
    ap.add_argument("--arm", required=True, help="output dir name under hypotheses/")
    ap.add_argument("--api-key-env", default="ASR_API_KEY")
    ap.add_argument("--language", default="th")
    ap.add_argument("--chunk-seconds", type=float, default=0.0,
                    help="0 = send each call whole")
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--items", nargs="*", default=[])
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    out_dir = C.ROOT / "hypotheses" / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    wavs = sorted(C.AUDIO_DIR.glob(f"*{C.AUDIO_EXT}"))
    if not wavs:
        print("no audio; run synthesize.py first")
        return 1

    # Map each wav back to its item id through the phone number, which is unique per call.
    by_phone = {}
    for dpath in sorted(C.DIALOGUE_DIR.glob("ASR-*.json")):
        dlg = json.loads(dpath.read_text(encoding="utf-8"))
        by_phone[dlg["meta"]["phone_number"]] = dlg["item_id"]

    runlog = []
    failures = 0
    for wav in wavs:
        phone = wav.stem.split("_")[1]
        item = by_phone.get(phone, wav.stem)
        if args.items and item not in args.items:
            continue

        x, sr = sf.read(str(wav), dtype="float32", always_2d=False)
        if x.ndim > 1:
            x = x.mean(axis=1)
        parts = chunks_of(x, sr, args.chunk_seconds)

        t0 = time.time()
        try:
            texts = [
                post_audio(args.base_url, args.model, api_key, wav.name,
                           to_wav_bytes(p, sr), args.language, args.timeout, args.retries)
                for p in parts
            ]
        except Exception as exc:                                  # noqa: BLE001
            # A failed call is recorded as a failure, never written as an empty transcript.
            # An empty file would score as a total deletion and read as a terrible model
            # rather than as a broken endpoint -- exactly the confusion that cost the Gemma
            # arm a whole run in Experiment 20.
            print(f"{item:9s} FAILED: {exc}")
            runlog.append({"item_id": item, "status": "failed", "error": str(exc),
                           "chunks": len(parts)})
            failures += 1
            continue

        elapsed = time.time() - t0
        text = " ".join(t.strip() for t in texts if t.strip())
        (out_dir / f"{item}.txt").write_text(text + "\n", encoding="utf-8")
        runlog.append({"item_id": item, "status": "ok", "chunks": len(parts),
                       "chars": len(text), "seconds": round(elapsed, 1),
                       "audio_s": round(x.size / sr, 1),
                       "rtf": round(elapsed / (x.size / sr), 3)})
        print(f"{item:9s} ok  chunks={len(parts):2d}  {len(text):6d} chars  "
              f"{elapsed:6.1f}s  RTF={runlog[-1]['rtf']:.3f}")

    meta = {
        "arm": args.arm, "model": args.model, "base_url": args.base_url,
        "language": args.language, "chunk_seconds": args.chunk_seconds,
        "items": runlog,
        "ok": sum(1 for r in runlog if r["status"] == "ok"), "failed": failures,
    }
    (out_dir / "_run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\n{meta['ok']} ok, {failures} failed -> {out_dir}")
    if failures:
        print("Do NOT score a partial run against a full one without saying so.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
