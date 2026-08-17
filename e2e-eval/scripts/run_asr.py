"""Transcribe the pilot audio through one OpenRouter ASR model.

Separate from `asr-eval/scripts/transcribe.py` only because that script maps wavs back to
item ids through asr-eval's own phone-number index, which this pack does not share. The
request shape, the whole-file (unchunked) regime and the failure discipline are the same:
a failed item is recorded as a failure and NEVER written as an empty transcript, because an
empty file scores as a total deletion and reads as a catastrophic model rather than a
broken call.
"""
from __future__ import annotations

import argparse
import base64
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
REPO = ROOT.parent
ENDPOINT = "https://openrouter.ai/api/v1/audio/transcriptions"
PACK = ROOT / "pilot.jsonl"

MODELS = {
    "e2e-qwen3-asr-1.7b": "qwen/qwen3-asr-1.7b",
    "e2e-whisper-large-v3": "openai/whisper-large-v3",
    "e2e-qwen3-asr-0.6b": "qwen/qwen3-asr-0.6b",
    "e2e-whisper-large-v3-turbo": "openai/whisper-large-v3-turbo",
}


def api_key() -> str:
    for p in (REPO / ".env",
              pathlib.Path(r"C:/Users/te90056471/my-github/model-eval-harness/.env")):
        if p.exists():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                if line.strip().startswith("OPENROUTER_API_KEY="):
                    return line.split("=", 1)[1].strip()
    raise SystemExit("OPENROUTER_API_KEY not found")


def transcribe(wav: bytes, model: str, key: str, retries: int = 3) -> str:
    body = json.dumps({
        "model": model,
        "input_audio": {"data": base64.b64encode(wav).decode("ascii"), "format": "wav"},
        "language": "th",
    }).encode("utf-8")
    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(
            ENDPOINT, data=body, method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"})
        try:
            with urllib.request.urlopen(req, timeout=900) as resp:
                return str(json.loads(resp.read().decode("utf-8")).get("text", "")).strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:300]
            last = RuntimeError(f"HTTP {exc.code}: {detail}")
            if 400 <= exc.code < 500 and exc.code != 429:
                raise last from exc
        except Exception as exc:                                   # noqa: BLE001
            last = exc
        time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"transcription failed after {retries}") from last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=sorted(MODELS))
    args = ap.parse_args()
    key = api_key()
    out = ROOT / "asr" / args.arm
    out.mkdir(parents=True, exist_ok=True)

    recs = [json.loads(l) for l in PACK.read_text(encoding="utf-8").splitlines()]
    log, ok, bad = [], 0, 0
    for rec in recs:
        wav_path = ROOT / "audio" / rec["audio_file"]
        if not wav_path.exists():
            print(f"  {rec['item_id']:8s} MISSING AUDIO"); bad += 1; continue
        t0 = time.time()
        try:
            text = transcribe(wav_path.read_bytes(), MODELS[args.arm], key)
        except Exception as exc:                                   # noqa: BLE001
            print(f"  {rec['item_id']:8s} FAILED {exc}", flush=True)
            log.append({"item_id": rec["item_id"], "status": "failed", "error": str(exc)})
            bad += 1
            continue
        if not text:
            print(f"  {rec['item_id']:8s} EMPTY -> recorded as failure", flush=True)
            log.append({"item_id": rec["item_id"], "status": "failed", "error": "empty"})
            bad += 1
            continue
        (out / f"{rec['item_id']}.txt").write_text(text + "\n", encoding="utf-8")
        el = time.time() - t0
        log.append({"item_id": rec["item_id"], "status": "ok", "chars": len(text),
                    "seconds": round(el, 1), "audio_s": rec["audio_seconds"]})
        ok += 1
        print(f"  {rec['item_id']:8s} ok  {len(text):6d} chars  {el:6.1f}s", flush=True)

    (out / "_run.json").write_text(json.dumps(
        {"arm": args.arm, "model": MODELS[args.arm], "chunking": "none (whole file)",
         "ok": ok, "failed": bad, "items": log}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(f"\n{args.arm}: {ok} ok, {bad} failed -> {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
