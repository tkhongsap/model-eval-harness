"""Send the eval set to an OpenAI-compatible ASR endpoint and save the transcripts.

Deliberately stdlib-only for HTTP (urllib; no requests, no openai package). Two reasons:

  1. requirements-asr.txt exists so this track cannot drag a dependency into the pinned
     root environment, and the fewer packages it names the smaller that risk is.
  2. src/evalharness/ imports no model client and tests/test_boundary.py fails the build
     if that ever changes. Nothing here is imported by the scoring package, but keeping the
     ASR runner dependency-free means it can never become the reason someone relaxes that
     boundary.

Endpoint shape assumed: POST {base}/audio/transcriptions, multipart/form-data with `file`
and `model`, returning {"text": ..., "usage": {"type": "duration", "seconds": N}}. The
usage block is the stage's BILLING UNIT -- audio seconds, not tokens; this endpoint has no
prompt/completion split. That is what vLLM and the Token Factory gateway
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
import http.client
import io
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

import numpy as np
import soundfile as sf

import asr_common as C


# --------------------------------------------------------------------------------------
# The one output normalisation this runner applies
# --------------------------------------------------------------------------------------
# Qwen3-ASR served through LiteLLM emits a serving-layer control token in its transcripts:
# `language Thai<asr_text>`. It is not speech. The audio contains no such words and no
# reference has them, so every character of it scores as an insertion against the reference.
#
# The language slot varies -- `language None<asr_text>` is recorded at
# `scripts/token_factory_bench.py:124` and `docs/gpu-shakedown-plan.md:85` -- so a literal
# string removal would under-fire and leave the token in on some items but not others.
#
# WHY THIS IS NOT AN AMENDMENT TO THE CLOSED LIST.
# `tests/fixtures/testsets/ASR-EXPECTATION.md` enumerates ten Thai ASR artifact classes and
# says at :114 that a run may change nothing in it. That rule governs METRIC normalisation:
# `asr_common.normalise_thai`, applied symmetrically to reference and hypothesis at
# `score_asr.py:308`, where forgiving an inference would hide a fabrication. This strip is
# ARM OUTPUT normalisation -- one arm's raw bytes, removing something that was never spoken
# and that no reference contains. It forgives no mishearing and changes no verdict, so
# ASR-EXPECTATION.md is not amended. The nearest row there, class 8 RET-120 "speaker label
# leaked into text", is REFUSED -- correctly, because a leaked speaker label is real text
# the model emitted about the audio. A serving control token is not about the audio at all.
#
# IT IS NOT A PREFIX. Measured on the first full-length transcription (ASR-001, 2,948 chars):
# the token appears EIGHT times -- once at the start and seven more at roughly 400-character
# intervals, i.e. it is a segment delimiter. The 30-second probe used to characterise it was
# too short to contain a second one. An anchored strip left ~161 characters of scaffolding in
# place, about 5% of the transcript, every character an insertion against the reference.
#
# The token also lands INSIDE words -- "...แจ้งไปหลาย" + token + "รอบก็ไม่มี..." -- so it is
# removed without consuming the whitespace around it, rejoining the halves with nothing
# between them. That is also consistent with this model emitting no word spaces anywhere
# else. Spacing is otherwise left exactly as the model produced it; `normalise_thai` applies
# whitespace policy symmetrically to both sides at scoring time, which is where it belongs.
#
# Applied at the return boundary of `post_audio`, NOT where the transcript is written. With
# --chunk-seconds one call becomes N POSTs, each carrying its own tokens; a strip at the
# write site would clean the first response and leave the rest embedded mid-transcript.
CONTROL_TOKEN = re.compile(r"language\s+\S+?\s*<asr_text>")


def strip_control_tokens(text: str, counter: dict[str, int]) -> str:
    """Remove every serving-layer control token, and record how many there were.

    `counter` accumulates {"responses": n, "responses_with_tokens": n, "tokens": n} across a
    whole run, so the caller can refuse a run where the token appeared on some responses and
    not others -- which would mean the serving layer changed shape part way through and half
    the corpus was normalised differently from the other half.
    """
    counter["responses"] = counter.get("responses", 0) + 1
    cleaned, n = CONTROL_TOKEN.subn("", text)
    if n:
        counter["responses_with_tokens"] = counter.get("responses_with_tokens", 0) + 1
        counter["tokens"] = counter.get("tokens", 0) + n
    cleaned = cleaned.strip()
    if not cleaned:
        # The model returns bare control tokens and nothing else when it finds no speech.
        # Writing that as an empty transcript would score as a total deletion and read as a
        # catastrophic model, which is the confusion the failure discipline below exists to
        # prevent. Fail the item instead.
        raise RuntimeError(
            "response contained no transcript, only control tokens "
            f"({text[:60]!r}) -- recorded as a failure, not as an empty transcript"
        )
    return cleaned


# --------------------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------------------


class _PinnedConnection(http.client.HTTPSConnection):
    """HTTPS to a fixed address, with SNI and cert verification against the real hostname.

    Needed because `token-fac-api.truecorp.co.th` resolves publicly to an address that does
    not serve this API -- measured 2026-08-17: DNS returns 111.84.54.29, which times out,
    while 10.94.154.102 answers in ~70 ms. The endpoint's certificate is self-signed and its
    SAN covers both the hostname and that address, so pinning it and connecting by address
    works with verification fully ON, and is STRONGER than the public-CA default: exactly one
    certificate is trusted, so a substituted host fails even holding a CA-signed cert. Same
    argument as `scripts/token_factory_probe.py:29-36`.

    Stdlib only, deliberately -- see the module docstring on why this track adds no packages.
    """

    def __init__(self, host, *, sni_host, **kw):
        super().__init__(host, **kw)
        self._sni_host = sni_host

    def connect(self):
        import socket
        sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self._sni_host)


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


def _send(url: str, body: bytes, headers: dict[str, str], timeout: int,
          cacert: str | None, connect_host: str | None) -> bytes:
    """POST and return the raw response body.

    Plain urllib unless the caller pinned a certificate or fixed the address, because the
    common case needs neither and the simplest path that works should stay the default.
    """
    if not cacert and not connect_host:
        req = urllib.request.Request(url, data=body, method="POST")
        for k, v in headers.items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()

    parts = urllib.parse.urlsplit(url)
    ctx = ssl.create_default_context(cafile=cacert) if cacert else ssl.create_default_context()
    conn = _PinnedConnection(connect_host or parts.hostname, port=parts.port or 443,
                             sni_host=parts.hostname, timeout=timeout, context=ctx)
    try:
        path = parts.path + (("?" + parts.query) if parts.query else "")
        conn.request("POST", path, body=body, headers={**headers, "Host": parts.hostname})
        resp = conn.getresponse()
        payload = resp.read()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {payload.decode('utf-8', 'replace')[:300]}")
        return payload
    finally:
        conn.close()


def post_audio(base_url: str, model: str, api_key: str, filename: str, blob: bytes,
               language: str, timeout: int, retries: int,
               prefix_counter: dict[str, int] | None = None,
               cacert: str | None = None, connect_host: str | None = None
               ) -> tuple[str, float | None]:
    """(transcript, billed_seconds). The second value is what the SERVER says it charged for.

    WHY IT IS NOT TOKENS, AND CANNOT BE. This endpoint has no prompt/completion split. A
    Whisper-family transcription is metered by how much AUDIO went in, and the response says
    so in its own words:

        {"text": "...", "usage": {"type": "duration", "seconds": 30.0}}

    Probed 2026-08-22 against typhoon-whisper-large-v3. Asking this stage for an input-token
    count is asking for a number that does not exist -- and `POST /v1/chat/completions` with
    this model returns 404, so there is no other route that would produce one either.

    This used to return a bare `str`, so the usage block was discarded by the type signature
    before anyone could decide whether to keep it. That made the pipeline's first stage look
    unmetered in every report, when in fact it is metered, just in a different unit.

    `None` rather than 0.0 when the server sends no usage: a missing measurement and a
    measured zero are different facts, and summing them as if they were the same is how a
    cost table ends up confidently wrong.

    NOTE `response_format` stays "json". The `verbose_json` shape DROPS the usage block --
    it returns `duration`/`words`/`segments` instead -- so switching for richer output would
    silently cost the billing figure.
    """
    url = base_url.rstrip("/") + "/audio/transcriptions"
    fields = {"model": model, "response_format": "json"}
    if language:
        fields["language"] = language
    body, content_type = build_multipart(fields, filename, blob)
    counter = prefix_counter if prefix_counter is not None else {}

    headers = {"Content-Type": content_type}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    last: Exception | None = None
    for attempt in range(retries):
        try:
            payload = json.loads(
                _send(url, body, headers, timeout, cacert, connect_host).decode("utf-8"))
            # Accept the two shapes servers actually return. Every path goes through
            # strip_control_tokens, which is what makes the strip chunk-count-invariant.
            if isinstance(payload, dict):
                usage = payload.get("usage") or {}
                billed = usage.get("seconds") if isinstance(usage, dict) else None
                billed = float(billed) if isinstance(billed, (int, float)) else None
                for key in ("text", "transcription"):
                    if key in payload:
                        return strip_control_tokens(str(payload[key]), counter), billed
                if "results" in payload:
                    joined = " ".join(r.get("text", "") for r in payload["results"])
                    return strip_control_tokens(joined, counter), billed
            raise RuntimeError(f"unrecognised response shape: {list(payload)[:6]}")
        except Exception as exc:                                  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(2.0 * (attempt + 1))
    # Name the last failure in the message, not only in __cause__. The caller prints
    # `str(exc)` into the per-item runlog and nothing else, so a bare "failed after N
    # attempts" is what reaches the operator -- true, and useless. It cost a full
    # diagnostic detour on 2026-08-20 to rediscover an HTTP status the server had
    # already reported three times.
    raise RuntimeError(
        f"transcription failed after {retries} attempts: "
        f"{type(last).__name__}: {last}"
    ) from last


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
    ap.add_argument("--cacert", default=None,
                    help="verify TLS against this certificate ONLY, instead of the public "
                         "trust store. For an endpoint serving a self-signed certificate.")
    ap.add_argument("--connect-host", default=None,
                    help="connect to this address instead of resolving --base-url's "
                         "hostname, which is still used for SNI and the Host header. For an "
                         "endpoint whose public DNS does not point at the serving host.")
    args = ap.parse_args()

    api_key = os.environ.get(args.api_key_env, "")
    if not api_key:
        # Refuse rather than send an unauthenticated request. Without this the run sends no
        # Authorization header at all, the gateway's nginx answers a bare
        # "401 Authorization Required", and the retry loop reports "transcription failed
        # after 3 attempts" for every item -- which reads like a broken model, not a missing
        # variable. Cost a real diagnostic detour on 2026-08-20.
        #
        # Note this script deliberately does NOT read .env (it keeps its dependency surface
        # tiny, see the module docstring), so the variable must be exported by the caller:
        #     export TOKEN_FACTORY_API_KEY=...    # or run it via scripts/typhoon_watch.py
        print(f"no ${args.api_key_env} in the environment. This script does not read .env; "
              f"export the variable first, or invoke it through a wrapper that does. "
              f"Refusing rather than sending an unauthenticated request that would fail "
              f"as a confusing 401 on every item.")
        return 2
    if args.cacert and not Path(args.cacert).is_file():
        print(f"--cacert {args.cacert} does not exist")
        return 2
    # Counts every response and every strip across the whole run, so the two can be compared
    # at the end. A per-item count could not detect the case this is here to catch.
    prefix_counter: dict[str, int] = {}
    out_dir = C.ROOT / "hypotheses" / args.arm
    out_dir.mkdir(parents=True, exist_ok=True)

    # One wav per call, enforced. See the note in experiment21_pipeline_delta.py: a
    # regenerated pack can leave a stale render beside a fresh one, because the measured
    # duration is part of the filename. The phone->item map below would then transcribe
    # whichever the filesystem returned last, and a faithful transcription of the wrong
    # audio is indistinguishable downstream from a correct one.
    _dupe: dict[str, list[str]] = {}
    for _w in C.AUDIO_DIR.glob(f"*{C.AUDIO_EXT}"):
        _dupe.setdefault(_w.stem.split("_")[1], []).append(_w.name)
    _multi = {k: v for k, v in _dupe.items() if len(v) > 1}
    if _multi:
        example = sorted(_multi.items())[0]
        print(f"REFUSING: {len(_multi)} call(s) have more than one audio file in "
              f"{C.AUDIO_DIR}. A stale render is sitting beside a fresh one and this run "
              f"would transcribe whichever the filesystem returned last. Example: phone "
              f"{example[0]} has {example[1]}.")
        return 2

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
            returned = [
                post_audio(args.base_url, args.model, api_key, wav.name,
                           to_wav_bytes(p, sr), args.language, args.timeout, args.retries,
                           prefix_counter=prefix_counter, cacert=args.cacert,
                           connect_host=args.connect_host)
                for p in parts
            ]
            texts = [t for t, _ in returned]
            # Summed across chunks, because at --chunk-seconds N one logical call is N POSTs
            # and the server bills each. None if ANY chunk reported nothing: a partial sum
            # would understate the bill and look like a measurement.
            billed = [b for _, b in returned]
            billed_total = round(sum(billed), 1) if all(
                b is not None for b in billed) else None
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
        # `seconds` here is WALL CLOCK and has always been. `billed_audio_s` is what the
        # server charged for. Two different quantities that both want the name "seconds";
        # keeping them apart matters because one is latency and the other is cost.
        runlog.append({"item_id": item, "status": "ok", "chunks": len(parts),
                       "chars": len(text), "seconds": round(elapsed, 1),
                       "audio_s": round(x.size / sr, 1),
                       "billed_audio_s": billed_total,
                       "rtf": round(elapsed / (x.size / sr), 3)})
        print(f"{item:9s} ok  chunks={len(parts):2d}  {len(text):6d} chars  "
              f"{elapsed:6.1f}s  RTF={runlog[-1]['rtf']:.3f}")

    seen = prefix_counter.get("responses", 0)
    stripped = prefix_counter.get("responses_with_tokens", 0)
    tokens = prefix_counter.get("tokens", 0)
    meta = {
        "arm": args.arm, "model": args.model, "base_url": args.base_url,
        "language": args.language, "chunk_seconds": args.chunk_seconds,
        "connect_host": args.connect_host, "cacert": args.cacert,
        "control_tokens": {"responses": seen, "responses_with_tokens": stripped,
                           "tokens_removed": tokens},
        "items": runlog,
        "ok": sum(1 for r in runlog if r["status"] == "ok"), "failed": failures,
    }
    (out_dir / "_run.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
    print(f"\n{meta['ok']} ok, {failures} failed -> {out_dir}")
    if stripped:
        print(f"control tokens removed: {tokens} across {stripped}/{seen} responses")
    if failures:
        print("Do NOT score a partial run against a full one without saying so.")

    # All-or-nothing. A prefix on some responses and not others means the serving layer
    # changed shape mid-run, and the corpus is then half-normalised -- which scores as a
    # model difference and is not one. Loud, and non-zero exit, rather than a footnote.
    if stripped and stripped != seen:
        print(f"\nREFUSING to call this run clean: control tokens were present on "
              f"{stripped} of {seen} responses.\nA partially-normalised corpus scores as a "
              f"model difference that does not exist. Investigate before scoring.")
        return 1
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
