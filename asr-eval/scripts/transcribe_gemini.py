"""Send the eval set to Gemini 2.5 Flash for a TRANSCRIPTION-ONLY pass, via OpenRouter.

This is deliberately not the same call production makes. Production hands Gemini audio
plus the full retention-QA prompt and gets labelled JSON back in one shot -- .env.example
calls that "production's one-call audio->JSON shape". Here Gemini is asked to do nothing
but transcribe, so its output lands in the same shape transcribe.py produces for the
internal ASR arm and both can be scored by the identical score_asr.py. Mixing in the QA
task would make a CER difference impossible to attribute to transcription versus labelling.

Content-part schema confirmed against OpenRouter's own multimodal/audio docs
(openrouter.ai/docs/guides/overview/multimodal/audio): `input_audio` with base64 `data`
and a `format` field, proxied straight through to the underlying model. Nothing in this
repository sent real audio anywhere before this script -- src/evalgen/prompts.py sends a
Thai TEXT transcript and explicitly repairs every prompt sentence that claims otherwise,
because this harness could not previously test the audio-input path at all.

Deliberately stdlib-only for HTTP (urllib; no requests, no openai package), matching
transcribe.py's reasoning: requirements-asr.txt exists so this track cannot drag a
dependency into the pinned root environment, and src/evalharness/ must never gain a
reason to import a model client.

Run:
    python asr-eval/scripts/transcribe_gemini.py --arm gemini-2.5-flash-audio
    python asr-eval/scripts/transcribe_gemini.py --arm gemini-2.5-flash-audio --items ASR-001 ASR-002
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import asr_common as C

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Transcription only. No QA, no labelling, no commentary -- see module docstring for why
# mixing tasks here would make the ASR-vs-ASR comparison unreadable. Matches the register
# ground truth is authored in: flowing spoken-form Thai, numbers/dates/amounts spoken as
# heard (not digit form), no speaker labels, no timestamps.
TRANSCRIBE_PROMPT = (
    "Transcribe this Thai phone call recording exactly as spoken. Output ONLY the "
    "transcript text, nothing else -- no preamble, no speaker labels, no timestamps, no "
    "commentary, no markdown formatting. Write numbers, dates, amounts and phone numbers "
    "exactly as the speaker said them (spoken Thai words), not converted to digits. Write "
    "brand and product names in Thai script, transliterated the way a Thai call-centre "
    "transcript would render them, never in Latin letters -- for example "
    "ทรู (True), ทรูมูฟ (TrueMove), "
    "ทรูวิชั่นส์ (TrueVisions), "
    "ทรูออนไลน์ (True Online), "
    "ทรูไอดี (TrueID), ดีแทค "
    "(dtac), อันลิมิเต็ด "
    "(Unlimited). If a portion is inaudible, transcribe what you can and leave a gap "
    "rather than guessing."
)


def post_audio(model: str, api_key: str, wav_bytes: bytes, timeout: int, retries: int) -> str:
    b64 = base64.b64encode(wav_bytes).decode("ascii")
    payload = json.dumps({
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": TRANSCRIBE_PROMPT},
                {"type": "input_audio", "input_audio": {"data": b64, "format": "wav"}},
            ],
        }],
        "temperature": 0,
    }).encode("utf-8")

    last: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(OPENROUTER_URL, data=payload, method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Authorization", f"Bearer {api_key}")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = json.loads(resp.read().decode("utf-8"))
            choices = body.get("choices") or []
            if not choices:
                raise RuntimeError(f"no choices in response: {list(body)[:6]}")
            content = choices[0].get("message", {}).get("content", "")
            if not content or not content.strip():
                raise RuntimeError("empty transcript in response")
            return content.strip()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:500]
            last = RuntimeError(f"HTTP {exc.code}: {detail}")
        except Exception as exc:                                  # noqa: BLE001
            last = exc
        if attempt < retries - 1:
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError(f"transcription failed after {retries} attempts") from last


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="google/gemini-2.5-flash")
    ap.add_argument("--arm", required=True, help="output dir name under hypotheses/")
    ap.add_argument("--api-key-env", default="OPENROUTER_API_KEY")
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--retries", type=int, default=3)
    ap.add_argument("--items", nargs="*", default=[])
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key.strip():
        print(f"{args.api_key_env} is empty or unset")
        return 1

    out_dir = C.ROOT / "hypotheses" / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    wavs = sorted(C.AUDIO_DIR.glob(f"*{C.AUDIO_EXT}"))
    if not wavs:
        print("no audio; run synthesize.py first")
        return 1

    # Map each wav back to its item id through the phone number, same technique
    # transcribe.py uses, so both arms' _run.json can be cross-referenced by item_id.
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

        wav_bytes = wav.read_bytes()
        t0 = time.time()
        try:
            text = post_audio(args.model, api_key, wav_bytes, args.timeout, args.retries)
        except Exception as exc:                                  # noqa: BLE001
            # A failed call is a failure, never an empty transcript -- see transcribe.py's
            # identical discipline and why (Experiment 20's Gemma confusion).
            print(f"{item:9s} FAILED: {exc}")
            runlog.append({"item_id": item, "status": "failed", "error": str(exc)})
            failures += 1
            continue

        elapsed = time.time() - t0
        (out_dir / f"{item}.txt").write_text(text + "\n", encoding="utf-8")
        runlog.append({"item_id": item, "status": "ok", "chars": len(text),
                       "seconds": round(elapsed, 1), "audio_bytes": len(wav_bytes)})
        print(f"{item:9s} ok  {len(text):6d} chars  {elapsed:6.1f}s")

    meta = {
        "arm": args.arm, "model": args.model, "endpoint": OPENROUTER_URL,
        "prompt": TRANSCRIBE_PROMPT, "items": runlog,
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
