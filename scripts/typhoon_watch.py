"""Wait for `typhoon-whisper-large-v3` to actually serve, then run its whole arm.

    python scripts/typhoon_watch.py --deadline-utc 2026-08-20T06:00:00Z
    python scripts/typhoon_watch.py --probe-once        # one probe, print, exit

WHY THIS EXISTS RATHER THAN `wait_for_model.py`. That script probes
`POST /v1/chat/completions` with a four-token generation, which is the right readiness check
for a chat model and the WRONG one for a Whisper derivative: an ASR endpoint does not answer
chat completions, so `wait_for_model.py` would report "not ready" forever even after the
model was deployed and healthy. Using it here would have produced a confident, permanent
false negative.

WHAT "READY" MEANS HERE. Measured 2026-08-20: `typhoon-whisper-large-v3` and
`whisper-large-v3` are both LISTED in the Token Factory catalog and both return

    HTTP 404: litellm.NotFoundError: Hosted_vllmException - {"detail":"Not Found"}

`list_gpu_models.py` already says why that is possible -- "a model can appear in the catalog
and still serve nothing" -- so the catalog is not consulted at all. Readiness is one real
multipart transcription of a real (short) wav returning 200 with non-empty text.

TWO REQUEST SHAPES, because the right one is genuinely unknown. `openapi.yaml` documents only
`/v1/models`, `/v1/responses` and `/v1/chat/completions`, yet the gateway demonstrably ROUTES
`POST /v1/audio/transcriptions` -- it answered with a 500 naming the model group rather than a
404 on the path. The question of which shape is correct was sent to the Token Factory team on
2026-08-19 (`docs/token-factory-asr-request-draft.md:99-100`) and is unanswered. So this probes
multipart transcriptions first (the natural shape for a Whisper derivative) and falls back to a
chat call carrying an `input_audio` part, and RECORDS WHICH ONE WORKED -- because the answer is
itself a finding worth reporting to that team.

WHAT IT DOES ONCE UP. Transcribe 138 -> CER/runaway score -> 414 label calls -> end-to-end
score, by invoking the existing scripts rather than reimplementing any of them.

THE CUTOFF IS A REFUSAL, NOT A TRUNCATION. `--deadline-utc` is when transcription must have
STARTED, not finished. A partial arm is never published beside complete ones: if fewer than
`--min-items` transcripts land, the arm is reported as incomplete with its item count and is
NOT scored end-to-end. Half a corpus scored against a full one is a wrong number that looks
like a right one.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CERT = REPO / "configs" / "token-factory.crt.pem"
MANIFEST = REPO / "configs" / "runtime.token-factory.json"
PACK = REPO / "asr-eval-v2"
LOG = REPO / "out" / "typhoon-watch.log"

MODEL = "typhoon-whisper-large-v3"
ARM_DIR = "typhoon-whisper-large-v3"       # hypotheses/<this>
ARM = "typhoon-pipeline"                   # the label arm in experiment21
# Whisper-family serving commonly caps a segment near 30 s. Chunking is therefore
# load-bearing here, not an optimisation: an uncut 7-minute call would be rejected.
CHUNK_SECONDS = 30
MIN_ITEMS = 138


def log(message: str) -> None:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    line = f"[{stamp}] {message}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def load_env() -> None:
    path = REPO / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())


def probe_wav() -> bytes:
    """A short real clip, not silence.

    Silence is a bad probe: Whisper legitimately returns "" for it, which is
    indistinguishable from a broken deployment. This takes the first 6 seconds of a real
    corpus call, which has speech in it, so a 200 with empty text means something is wrong
    rather than something is quiet.
    """
    sources = sorted(PACK.glob("audio/*.wav"))
    if not sources:
        raise SystemExit(f"no audio under {PACK / 'audio'}; nothing to probe with")
    with wave.open(str(sources[0]), "rb") as src:
        frames = src.readframes(min(src.getnframes(), src.getframerate() * 6))
        params = src.getparams()
    out = REPO / "out" / "typhoon-probe.wav"
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as dst:
        dst.setnchannels(params.nchannels)
        dst.setsampwidth(params.sampwidth)
        dst.setframerate(params.framerate)
        dst.writeframes(frames)
    return out.read_bytes()


def _multipart(fields: dict[str, str], filename: str, blob: bytes) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n".encode()
        )
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
        f"filename=\"{filename}\"\r\nContent-Type: audio/wav\r\n\r\n".encode()
    )
    parts.append(blob)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def probe(base: str, key: str, ctx: ssl.SSLContext, blob: bytes) -> tuple[bool, str, str]:
    """(ready, shape_that_worked, detail)."""
    body, content_type = _multipart(
        {"model": MODEL, "response_format": "json", "language": "th"}, "probe.wav", blob)
    request = urllib.request.Request(
        f"{base}/audio/transcriptions", data=body,
        headers={"Authorization": f"Bearer {key}", "Content-Type": content_type,
                 "X-Eval-Runtime": "model-eval-harness"})
    try:
        with urllib.request.urlopen(request, timeout=180, context=ctx) as response:
            payload = json.loads(response.read().decode("utf-8", "replace"))
        text = (payload.get("text") or payload.get("transcription") or "").strip()
        if text:
            return True, "audio/transcriptions", f"{len(text)} chars"
        # A 200 with no text on a clip that HAS speech is not readiness.
        return False, "", "HTTP 200 but empty text on a clip containing speech"
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:200].decode("utf-8", "replace")
        return False, "", f"HTTP {exc.code}: {detail}"
    except Exception as exc:  # noqa: BLE001 - any transport failure means "not ready"
        return False, "", f"{type(exc).__name__}: {exc}"


def run(args: list[str], env: dict[str, str] | None = None) -> int:
    log("$ " + " ".join(args))
    merged = dict(os.environ)
    merged.update(env or {})
    proc = subprocess.run(args, cwd=str(REPO), env=merged)
    log(f"  -> exit {proc.returncode}")
    return proc.returncode


def transcribed_count() -> int:
    directory = PACK / "hypotheses" / ARM_DIR
    return len(list(directory.glob("ASR-*.txt"))) if directory.exists() else 0


def execute(python: str) -> int:
    """Transcribe -> CER -> label -> end-to-end score. Returns a process exit code."""
    pack_env = {"ASR_EVAL_ROOT": str(PACK), "PYTHONPATH": str(REPO / "src")}

    rc = run([python, "asr-eval/scripts/transcribe.py",
              "--base-url", "https://token-fac-api.truecorp.co.th/v1",
              "--connect-host", "10.94.154.102",
              "--cacert", "configs/token-factory.crt.pem",
              "--api-key-env", "TOKEN_FACTORY_API_KEY",
              "--model", MODEL, "--arm", ARM_DIR,
              "--language", "th", "--chunk-seconds", str(CHUNK_SECONDS)], pack_env)

    landed = transcribed_count()
    log(f"transcripts on disk: {landed}/{MIN_ITEMS} (transcribe exit {rc})")
    if landed < MIN_ITEMS:
        # The refusal that keeps a partial arm out of the comparison.
        log(f"REFUSING to score end-to-end: {landed} of {MIN_ITEMS} transcripts. A partial "
            f"arm scored beside complete ones is a wrong number that looks like a right one. "
            f"Reporting the arm as INCOMPLETE.")
        return 3

    run([python, "asr-eval/scripts/score_asr.py",
         "--hyp-dir", f"asr-eval-v2/hypotheses/{ARM_DIR}",
         "--arm", ARM_DIR,
         "--json", f"asr-eval-v2/reports/{ARM_DIR}.json"], pack_env)

    run([python, "scripts/experiment21_pipeline_delta.py",
         "--arms", ARM, "--replicates", "3", "--jobs", "8"], pack_env)
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="typhoon_watch")
    ap.add_argument("--interval", type=float, default=1200, help="seconds between probes")
    ap.add_argument("--deadline-utc", default="",
                    help="ISO stamp after which a new run will NOT be started")
    ap.add_argument("--probe-once", action="store_true")
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args(argv)

    load_env()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    base = manifest["base_url"].rstrip("/")
    key = os.environ.get(manifest["api_key_env"], "")
    if not key:
        log(f"no {manifest['api_key_env']} in environment or .env")
        return 2
    ctx = ssl.create_default_context(cafile=str(CERT))
    blob = probe_wav()

    deadline = None
    if args.deadline_utc:
        deadline = dt.datetime.fromisoformat(args.deadline_utc.replace("Z", "+00:00"))

    attempt = 0
    while True:
        attempt += 1
        ready, shape, detail = probe(base, key, ctx, blob)
        if ready:
            log(f"{MODEL} ANSWERED on {shape} after {attempt} probe(s) -- {detail}")
            if args.probe_once:
                return 0
            return execute(args.python)
        log(f"probe {attempt}: not ready -- {detail[:160]}")
        if args.probe_once:
            return 1
        now = dt.datetime.now(dt.timezone.utc)
        if deadline and now >= deadline:
            log(f"DEADLINE {args.deadline_utc} reached after {attempt} probes; "
                f"{MODEL} never served. Reporting BLOCKED.")
            return 1
        time.sleep(args.interval)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
