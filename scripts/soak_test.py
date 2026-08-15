"""Sustained soak and stress test against an OpenAI-compatible GPU endpoint.

The plan, the load profile, the pass criteria and the scope boundary are in
`docs/soak-test-plan.md`. Read that first; this file executes it and does not restate it.

**Why this is not built on `evalgen`.** The harness cannot do this job, and the gaps are
structural rather than missing flags:

  * `client.py:265` calls `chat.completions.create` with no `stream=True`, so **there is no
    TTFT and could not be** -- `report.py:1153-1158` says so in as many words.
  * `runner.py:944-979` writes no timestamp per row, so latency drift over five hours is not
    reconstructable from a run log.
  * The task matrix is `items x repeats`, frozen before the first call (`runner.py:843-853`).
    There is no duration, no arrival shape, no ramp.
  * `runner.py:95-99` warns that its own retry path has no jitter and ignores `Retry-After`,
    and that it should be revisited before concurrency grows. This driver is that revisit.

So this is a separate instrument, and it stays out of the scoring path: it imports nothing
from `evalharness`, writes nothing a scorer reads, and produces no run artifact the compare
path could pick up.

**Only documented fields are sent.** `Token_Factory_API_Guide.md:389` lists `model`,
`messages`, `stream`, `max_tokens`, `temperature`, `top_p`, and warns not to depend on an
unlisted field even if one request returns 200. `seed` and `response_format` are known to work
here but are undocumented, and a five-hour run is the wrong place to also be testing whether
an unsupported field keeps working. `stream_options` is the one exception and it is *probed*
rather than assumed -- see `probe_usage_support`.

Usage:
    python scripts/soak_test.py --model qwen3.6-27b-fp8 --hours 5
    python scripts/soak_test.py --profile smoke          # ~6 min, same code path
    python scripts/soak_test.py --profile smoke --dry-run   # no calls; prints the schedule
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import statistics
import sys
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CERT = REPO / "configs" / "token-factory.crt.pem"
MANIFEST = REPO / "configs" / "runtime.token-factory.json"
AUTHORED = REPO / "tests" / "fixtures" / "soak" / "prompts_authored.jsonl"
RETENTION_V3 = REPO / "tests" / "fixtures" / "testsets" / "retention_v3.jsonl"
KEY_VAR = "TOKEN_FACTORY_API_KEY"

# Unauthenticated on this deployment, so polling them costs nothing and cannot be rate-limited
# against the load. /metrics and /health are refused for our virtual key but are polled anyway:
# if anyone flips `require_auth_for_metrics_endpoint` mid-run we start recording immediately.
HEALTH_PATHS = ("/health/liveness", "/health/readiness")
METRIC_PATHS = ("/metrics",)

TEMPERATURE = 0.0
TOP_P = 1.0  # the endpoint rejects production's 0; greedy at temperature 0, so inert

# Vendor retry policy, `Token_Factory_API_Guide.md:403-414`: honour Retry-After, exponential
# backoff WITH jitter, at most 3 attempts, never retry 400/401/403/404.
MAX_ATTEMPTS = 3
BACKOFF_S = (1.0, 2.0, 4.0)
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}

# Abort the CLIMB on a sustained storm. Ramp-to-failure means locating the breaking point, not
# continuing to hammer a shared endpoint after locating it -- and Token Factory reserves the
# right to throttle or suspend on capacity risk (`Token_Factory_API_Guide.md:422`), which would
# end the test outright.
STORM_ERROR_RATE = 0.25
STORM_WINDOW_S = 120

# A total-connectivity watchdog, and it is not the same thing as the storm check above.
#
# MEASURED 2026-08-14: the client lost its route to True's internal network 157 minutes into a
# five-hour run. Every subsequent request failed with a 15 s CONNECT timeout and every health
# poll failed with ConnectTimeout -- for 138 minutes, because the storm check only ran on
# `ramp` phases and nothing else was watching. The run recorded 9,365 timeouts that measured a
# disconnected laptop rather than a GPU.
#
# The distinction that matters: a failing MODEL or server returns 5xx, or slows down, or fails
# some requests -- there are still successes and the TCP layer still works. A lost NETWORK PATH
# fails to connect at all and takes the unauthenticated health endpoint down with it. The
# second condition is not a test result; it is the test being unable to run, and continuing
# past it produces data that looks like a catastrophic server failure and is not one.
INFRA_LOST_CONSECUTIVE_POLLS = 8      # 8 x 15 s = two minutes of no health response
INFRA_LOST_MIN_ERROR_RATE = 0.98


# --------------------------------------------------------------------------- profiles
def build_phases(hours: float, profile: str) -> list[dict]:
    """The load profile. Data, not code -- see the table in the plan."""
    if profile == "smoke":
        return [
            {"name": "baseline_a", "conc": 1, "minutes": 1.0, "pool": "mixed"},
            {"name": "normal", "conc": 4, "minutes": 1.0, "pool": "mixed"},
            {"name": "ramp_8", "conc": 8, "minutes": 1.0, "pool": "mixed", "ramp": True},
            {"name": "long_context", "conc": 4, "minutes": 1.0, "pool": "heavy"},
            {"name": "normal_end", "conc": 4, "minutes": 1.0, "pool": "mixed"},
            {"name": "baseline_b", "conc": 1, "minutes": 1.0, "pool": "mixed"},
        ]
    # A phase stops STARTING requests at its deadline and then drains whatever is in flight.
    # The longest prompts generate ~2000 tokens at ~21 tok/s, so the tail is ~95 s per phase.
    # Measured on the smoke profile: 6 planned minutes took 12.62, i.e. ~66 s of drain per
    # phase on a lighter mix. Budget for it, or a "5-hour" run lands closer to 5h20.
    DRAIN_PER_PHASE_MIN = 1.6
    total = hours * 60.0
    table_minutes = 300.0
    # 10 phases in the table below; scale the planned time down so planned + drain ~= total.
    share = max(0.02, (total - DRAIN_PER_PHASE_MIN * 10) / table_minutes)
    return [
        {"name": "baseline_a", "conc": 1, "minutes": 20 * share, "pool": "mixed"},
        {"name": "normal", "conc": 4, "minutes": 70 * share, "pool": "mixed"},
        {"name": "ramp_8", "conc": 8, "minutes": 15 * share, "pool": "mixed", "ramp": True},
        {"name": "ramp_16", "conc": 16, "minutes": 15 * share, "pool": "mixed", "ramp": True},
        {"name": "ramp_32", "conc": 32, "minutes": 15 * share, "pool": "mixed", "ramp": True},
        {"name": "ramp_64", "conc": 64, "minutes": 15 * share, "pool": "mixed", "ramp": True},
        {"name": "stress_hold", "conc": None, "minutes": 40 * share, "pool": "mixed"},
        {"name": "long_context", "conc": 4, "minutes": 45 * share, "pool": "heavy"},
        {"name": "normal_end", "conc": 4, "minutes": 45 * share, "pool": "mixed"},
        {"name": "baseline_b", "conc": 1, "minutes": 20 * share, "pool": "mixed"},
    ]


# --------------------------------------------------------------------------- prompts
@dataclass
class Prompt:
    prompt_id: str
    cls: str
    max_tokens: int
    text: str
    expect: dict


def load_prompts() -> tuple[list[Prompt], list[Prompt]]:
    """(mixed pool, heavy pool). `long_context` is assembled here rather than committed.

    The long-context inputs are the 12-18k-character dilated Thai transcripts already in
    `retention_v3`. Building them at runtime avoids committing a second copy of transcript text
    into a new fixture, and they are the most production-shaped long input this repo has.
    """
    if not AUTHORED.is_file():
        raise SystemExit(f"prompt pack missing: {AUTHORED}")
    authored = []
    for line in AUTHORED.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        authored.append(Prompt(row["prompt_id"], row["class"], int(row["max_tokens"]),
                               row["prompt"], row["expect"]))

    long_ctx: list[Prompt] = []
    if RETENTION_V3.is_file():
        items = [json.loads(l) for l in
                 RETENTION_V3.read_text(encoding="utf-8").splitlines() if l.strip()]
        heavy = sorted((i for i in items if i.get("family") == "long_context"),
                       key=lambda i: -len(i.get("transcript_th", "")))[:8]
        for n, item in enumerate(heavy, start=1):
            long_ctx.append(Prompt(
                f"SK-LC-{n:03d}", "long_context", 300,
                "Read the following call-centre transcript and answer in English.\n\n"
                "1. What did the customer want?\n"
                "2. What was the outcome of the call?\n"
                "3. Name any products discussed.\n\n"
                "Keep the whole answer under 120 words.\n\n"
                "--- TRANSCRIPT ---\n" + item["transcript_th"],
                {"kind": "min_words", "value": 15},
            ))

    mixed = authored + long_ctx
    heavy_pool = [p for p in mixed if p.cls in ("long_context", "long_gen")]
    return mixed, heavy_pool


# --------------------------------------------------------------------------- checking
_FENCE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.S)
_NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def check(text: str, expect: dict) -> bool:
    """Did the model do the task? Measured under load, not assumed."""
    kind = expect.get("kind")
    if not text.strip():
        return False
    if kind == "contains_any":
        low = text.lower()
        return any(str(v).lower() in low for v in expect["values"])
    if kind == "min_words":
        return len(text.split()) >= int(expect["value"])
    if kind == "exact_number":
        nums = _NUM.findall(text.replace(",", ""))
        if not nums:
            return False
        try:
            got = float(nums[-1])
        except ValueError:
            return False
        return abs(got - float(expect["value"])) <= float(expect.get("tolerance", 0))
    if kind == "json_object":
        body = _FENCE.sub("", text.strip())
        start, end = body.find("{"), body.rfind("}")
        if start < 0 or end <= start:
            return False
        try:
            obj = json.loads(body[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return False
        return isinstance(obj, dict) and all(k in obj for k in expect.get("required_keys", []))
    return True


# --------------------------------------------------------------------------- recorder
class Recorder:
    """One lock, three append-only files, flushed every write.

    Flushed rather than buffered on purpose: a five-hour run that dies at hour four must leave
    four hours of usable data behind, not an empty buffer.
    """

    def __init__(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.dir = directory
        self._lock = threading.Lock()
        self._requests = (directory / "requests.jsonl").open("a", encoding="utf-8")
        self._timeline = (directory / "timeline.jsonl").open("a", encoding="utf-8")
        self._health = (directory / "health.jsonl").open("a", encoding="utf-8")
        self.recent: deque = deque(maxlen=4000)
        self.counts: Counter = Counter()

    def request(self, row: dict) -> None:
        with self._lock:
            self._requests.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._requests.flush()
            self.recent.append((row["ts_end"], row["phase"], row["outcome"]))
            self.counts[row["outcome"]] += 1

    def timeline(self, row: dict) -> None:
        with self._lock:
            self._timeline.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._timeline.flush()

    def health(self, row: dict) -> None:
        with self._lock:
            self._health.write(json.dumps(row, ensure_ascii=False) + "\n")
            self._health.flush()

    def error_rate(self, window_s: float, phase: str) -> tuple[float, int]:
        cutoff = time.time() - window_s
        rows = [r for r in list(self.recent) if r[0] >= cutoff and r[1] == phase]
        if not rows:
            return 0.0, 0
        bad = sum(1 for r in rows if r[2] != "ok")
        return bad / len(rows), len(rows)

    def close(self) -> None:
        with self._lock:
            for handle in (self._requests, self._timeline, self._health):
                handle.close()


# --------------------------------------------------------------------------- transport
def load_key() -> str:
    value = os.environ.get(KEY_VAR, "").strip()
    if value:
        return value
    env = REPO / ".env"
    if env.is_file():
        m = re.search(rf"^{re.escape(KEY_VAR)}=(.*)$", env.read_text(encoding="utf-8"), re.M)
        if m and m.group(1).strip():
            return m.group(1).strip()
    raise SystemExit(f"{KEY_VAR} not found in the environment or .env")


def make_client(base_url: str, timeout: float, pool: int):
    import httpx

    verify: object = str(CERT) if base_url.startswith("https://10.94.") else True
    limits = httpx.Limits(max_connections=pool + 8, max_keepalive_connections=pool + 8)
    return httpx.Client(verify=verify, timeout=httpx.Timeout(timeout, connect=15.0),
                        limits=limits)


@dataclass
class Attempt:
    outcome: str
    http_status: int | None = None
    ttft_s: float | None = None
    e2e_s: float = 0.0
    text: str = ""
    chunks: int = 0
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    retry_after_s: float | None = None
    error: str = ""


def one_call(client, url: str, key: str, model: str, prompt: Prompt,
             *, want_usage: bool, timeout: float) -> Attempt:
    """One streamed request. TTFT is the wall time to the first content-bearing delta."""
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt.text}],
        "stream": True,
        "max_tokens": prompt.max_tokens,
        "temperature": TEMPERATURE,
        "top_p": TOP_P,
    }
    if want_usage:
        body["stream_options"] = {"include_usage": True}

    headers = {"Authorization": f"Bearer {key}", "Accept": "text/event-stream"}
    started = time.monotonic()
    ttft = None
    pieces: list[str] = []
    chunks = 0
    finish = None
    ptok = ctok = None

    try:
        with client.stream("POST", url, json=body, headers=headers) as response:
            if response.status_code >= 400:
                response.read()
                retry_after = response.headers.get("retry-after")
                return Attempt(
                    outcome=("http_429" if response.status_code == 429
                             else f"http_{response.status_code // 100}xx"),
                    http_status=response.status_code,
                    e2e_s=time.monotonic() - started,
                    retry_after_s=float(retry_after) if (retry_after or "").isdigit() else None,
                    error=response.text[:300],
                )
            for line in response.iter_lines():
                if not line or not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    ptok = event["usage"].get("prompt_tokens")
                    ctok = event["usage"].get("completion_tokens")
                for choice in event.get("choices") or []:
                    piece = (choice.get("delta") or {}).get("content")
                    if piece:
                        if ttft is None:
                            ttft = time.monotonic() - started
                        pieces.append(piece)
                        chunks += 1
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
    except Exception as exc:  # noqa: BLE001 - every transport failure is one outcome here
        name = type(exc).__name__
        kind = "timeout" if "Timeout" in name else "transport"
        return Attempt(outcome=kind, e2e_s=time.monotonic() - started,
                       error=f"{name}: {str(exc)[:200]}")

    text = "".join(pieces)
    elapsed = time.monotonic() - started
    if not text.strip():
        # A 200 with no content is a real failure mode, not a pass.
        return Attempt(outcome="empty", http_status=200, e2e_s=elapsed, finish_reason=finish)
    return Attempt(outcome="ok", http_status=200, ttft_s=ttft, e2e_s=elapsed, text=text,
                   chunks=chunks, finish_reason=finish, prompt_tokens=ptok,
                   completion_tokens=ctok)


def probe_usage_support(client, url: str, key: str, model: str) -> bool:
    """Does this deployment honour `stream_options.include_usage`?

    Undocumented, so it is tested rather than assumed. If it works, every streamed row carries
    exact server-side token counts. If it does not, the run falls back to counting SSE deltas,
    which is an estimate and is labelled as one in the report.
    """
    probe = Prompt("probe", "short_qa", 16, "Reply with the single word: ready.", {})
    got = one_call(client, url, key, model, probe, want_usage=True, timeout=60)
    return got.outcome == "ok" and got.completion_tokens is not None


# --------------------------------------------------------------------------- pollers
class Poller(threading.Thread):
    """Health, metrics and the determinism probe. Never blocks the load."""

    def __init__(self, client, base: str, key: str, model: str, rec: Recorder,
                 state: dict, probes: list[Prompt], every_s: float,
                 determinism_every_s: float, extra_metrics_url: str | None) -> None:
        super().__init__(daemon=True, name="poller")
        self.client, self.base, self.key, self.model = client, base, key, model
        self.rec, self.state, self.probes = rec, state, probes
        self.every_s, self.det_every_s = every_s, determinism_every_s
        self.extra = extra_metrics_url
        self.stop_flag = threading.Event()
        self.det_hashes: dict[str, str] = {}
        self.det_rows: list[dict] = []
        # Consecutive health polls that could not reach the host at all. Read by the
        # supervisor's infrastructure watchdog; see INFRA_LOST_CONSECUTIVE_POLLS.
        self.consecutive_unreachable = 0
        self.first_unreachable_ts: float | None = None

    def _get(self, path_or_url: str) -> dict:
        url = path_or_url if path_or_url.startswith("http") else self.base + path_or_url
        started = time.monotonic()
        try:
            r = self.client.get(url, headers={"Authorization": f"Bearer {self.key}"},
                                timeout=10.0)
            return {"status": r.status_code, "ms": round((time.monotonic() - started) * 1000, 1),
                    "body": r.text[:20000] if r.status_code < 400 else r.text[:200]}
        except Exception as exc:  # noqa: BLE001
            return {"status": None, "ms": round((time.monotonic() - started) * 1000, 1),
                    "error": f"{type(exc).__name__}: {str(exc)[:160]}"}

    def run(self) -> None:
        # The determinism probe makes REAL model calls, which at c64 take ~170 s each. Running
        # them inline on this thread blocked health polling for up to 504 s in the 2026-08-14
        # run and produced five phantom "availability gaps" that were the poller being busy,
        # not the endpoint being down. It gets its own thread so the health cadence holds.
        det_thread = threading.Thread(target=self._determinism_loop, daemon=True,
                                      name="determinism")
        det_thread.start()
        while not self.stop_flag.is_set():
            row = {"ts": time.time(), "phase": self.state.get("phase"),
                   "conc": self.state.get("conc"), "inflight": self.state.get("inflight")}
            for path in HEALTH_PATHS:
                row[path] = self._get(path)
            for path in METRIC_PATHS:
                got = self._get(path)
                # Only carry the body when it actually became readable; a 401 every 15s for
                # five hours would be 1200 copies of the same refusal.
                row[path] = got if got.get("status") == 200 else {"status": got.get("status")}
            if self.extra:
                row["extra_metrics"] = self._get(self.extra)

            # Liveness is unauthenticated on this deployment, so a failure here is the network
            # or the host, never a permission or a model problem.
            live = row.get("/health/liveness", {})
            if live.get("status") is None:
                self.consecutive_unreachable += 1
                if self.first_unreachable_ts is None:
                    self.first_unreachable_ts = row["ts"]
            else:
                self.consecutive_unreachable = 0
                self.first_unreachable_ts = None
            row["consecutive_unreachable"] = self.consecutive_unreachable
            self.rec.health(row)
            self.stop_flag.wait(self.every_s)

    def _determinism_loop(self) -> None:
        self.stop_flag.wait(60)
        while not self.stop_flag.is_set():
            self._determinism()
            self.stop_flag.wait(self.det_every_s)

    def _determinism(self) -> None:
        """Byte-identical requests, repeated. The direct test of the batching hypothesis."""
        for probe in self.probes:
            conc = self.state.get("conc")
            got = one_call(self.client, self.base + "/v1/chat/completions", self.key,
                           self.model, probe, want_usage=False, timeout=180)
            if got.outcome != "ok":
                continue
            digest = hashlib.sha256(got.text.encode("utf-8")).hexdigest()
            first = self.det_hashes.setdefault(probe.prompt_id, digest)
            row = {"ts": time.time(), "prompt_id": probe.prompt_id, "conc_at_send": conc,
                   "phase": self.state.get("phase"), "sha256": digest,
                   "matches_first": digest == first, "e2e_s": round(got.e2e_s, 3)}
            self.det_rows.append(row)
            self.rec.health({"determinism": row})


# --------------------------------------------------------------------------- the run
def run_phase(phase: dict, ctx: dict) -> dict:
    """Closed-loop: `conc` workers, each issuing its next request as soon as one returns."""
    rec: Recorder = ctx["rec"]
    state: dict = ctx["state"]
    conc = int(phase["conc"])
    pool = ctx["heavy"] if phase["pool"] == "heavy" else ctx["mixed"]
    deadline = time.time() + phase["minutes"] * 60.0
    state.update(phase=phase["name"], conc=conc, inflight=0)

    stop = threading.Event()
    aborted = {"why": None}
    inflight_lock = threading.Lock()
    counter = {"n": 0}

    def worker(index: int) -> None:
        rng = random.Random(ctx["seed"] * 1000 + index)
        while not stop.is_set() and time.time() < deadline:
            with inflight_lock:
                counter["n"] += 1
                seq = counter["n"]
                pick = pool[(seq * 7 + index) % len(pool)]
                state["inflight"] = state.get("inflight", 0) + 1
            # Groups every attempt of one logical request. Without it the log cannot separate
            # the raw per-attempt error rate (what the server did) from the post-retry rate
            # (what a client actually experiences), and the report promises both.
            req_id = f"{phase['name']}-w{index}-{seq}"
            try:
                for attempt in range(1, MAX_ATTEMPTS + 1):
                    ts_start = time.time()
                    got = one_call(ctx["client"], ctx["url"], ctx["key"], ctx["model"], pick,
                                   want_usage=ctx["usage_ok"], timeout=ctx["timeout"])
                    ts_end = time.time()
                    completion = (got.completion_tokens if got.completion_tokens is not None
                                  else got.chunks or None)
                    rec.request({
                        "ts_start": ts_start, "ts_end": ts_end,
                        "ts_first_token": (ts_start + got.ttft_s) if got.ttft_s else None,
                        "req_id": req_id,
                        "phase": phase["name"], "concurrency": conc, "worker": index,
                        "prompt_id": pick.prompt_id, "class": pick.cls, "attempt": attempt,
                        "outcome": got.outcome, "http_status": got.http_status,
                        "ttft_s": round(got.ttft_s, 4) if got.ttft_s else None,
                        "e2e_s": round(got.e2e_s, 4),
                        "prompt_tokens": got.prompt_tokens,
                        "completion_tokens": completion,
                        "tokens_est": got.completion_tokens is None,
                        "output_tokens_per_s": (round(completion / got.e2e_s, 2)
                                                if completion and got.e2e_s > 0 else None),
                        "finish_reason": got.finish_reason,
                        "truncated": got.finish_reason == "length",
                        "output_sha256": (hashlib.sha256(got.text.encode()).hexdigest()
                                          if got.text else None),
                        "output_chars": len(got.text),
                        "valid": check(got.text, pick.expect) if got.outcome == "ok" else False,
                        "retry_after_s": got.retry_after_s,
                        "error": got.error or None,
                    })
                    if got.outcome == "ok" or got.outcome == "empty":
                        break
                    retryable = (got.http_status in RETRYABLE_STATUS
                                 or got.outcome in ("timeout", "transport"))
                    if not retryable or attempt == MAX_ATTEMPTS:
                        break
                    # Vendor policy: Retry-After wins; otherwise 1/2/4s WITH jitter. Without
                    # jitter every worker throttled at the same instant retries in lockstep and
                    # re-creates the burst that caused the throttle.
                    wait = got.retry_after_s if got.retry_after_s else BACKOFF_S[attempt - 1]
                    stop.wait(wait * (0.5 + rng.random()))
            finally:
                with inflight_lock:
                    state["inflight"] = max(0, state.get("inflight", 1) - 1)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True,
                                name=f"load-{phase['name']}-{i}") for i in range(conc)]
    for t in threads:
        t.start()

    # Supervisor: 30s timeline buckets, and the storm check on ramp phases.
    bucket_end = time.time() + 30
    while time.time() < deadline and not stop.is_set():
        time.sleep(1.0)
        if time.time() >= bucket_end:
            bucket_end = time.time() + 30
            rate, n = rec.error_rate(30, phase["name"])
            rec.timeline({"ts": time.time(), "phase": phase["name"], "conc": conc,
                          "inflight": state.get("inflight"), "completed_30s": n,
                          "error_rate_30s": round(rate, 4)})
            print(f"    [{phase['name']:>13} c{conc:<3}] {n:>4} req/30s  "
                  f"err {rate * 100:5.1f}%  inflight {state.get('inflight')}")
        # Infrastructure watchdog, EVERY phase. Distinct from the storm check below: this one
        # fires when the host is unreachable rather than when the model is failing, and it
        # ends the whole run instead of just the climb.
        poller = ctx.get("poller")
        if poller is not None and poller.consecutive_unreachable >= INFRA_LOST_CONSECUTIVE_POLLS:
            rate, n = rec.error_rate(STORM_WINDOW_S, phase["name"])
            if n == 0 or rate >= INFRA_LOST_MIN_ERROR_RATE:
                ctx["infra_lost"] = {
                    "first_unreachable_ts": poller.first_unreachable_ts,
                    "consecutive_polls": poller.consecutive_unreachable,
                    "phase": phase["name"], "concurrency": conc,
                    "detail": "unauthenticated /health/liveness unreachable and requests are "
                              "not connecting; this is the network path or the host, not the "
                              "model. Everything after this point would be measuring a "
                              "disconnected client.",
                }
                aborted["why"] = "infrastructure unreachable"
                print(f"\n    !! ENDPOINT UNREACHABLE for "
                      f"{poller.consecutive_unreachable} consecutive health polls and requests "
                      f"are not connecting.\n    !! Aborting the run rather than recording "
                      f"hours of disconnected-client data. Data up to this point is intact.")
                stop.set()
                break

        if phase.get("ramp"):
            rate, n = rec.error_rate(STORM_WINDOW_S, phase["name"])
            if n >= 20 and rate >= STORM_ERROR_RATE:
                aborted["why"] = f"error rate {rate:.1%} over {n} requests"
                print(f"    !! storm at c{conc}: {aborted['why']} -- stopping the climb")
                stop.set()
                break

    stop.set()
    for t in threads:
        t.join(timeout=ctx["timeout"] + 30)
    state["inflight"] = 0
    return {"phase": phase["name"], "concurrency": conc, "aborted": aborted["why"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="soak_test")
    ap.add_argument("--model", default="qwen3.6-27b-fp8")
    ap.add_argument("--hours", type=float, default=5.0)
    ap.add_argument("--profile", choices=("full", "smoke"), default="full")
    ap.add_argument("--base-url", default=None, help="override; default reads the manifest")
    # Max e2e measured on the smoke run was ~95 s (a 2000-token generation at ~21 tok/s).
    # 240 s is 2.5x headroom and bounds how long a phase can spend draining.
    ap.add_argument("--timeout", type=float, default=240.0)
    ap.add_argument("--seed", type=int, default=17)
    ap.add_argument("--metrics-url", default=None,
                    help="a vLLM/DCGM Prometheus URL, if one becomes reachable")
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    base = args.base_url or json.loads(MANIFEST.read_text(encoding="utf-8"))["base_url"]
    base = base.rstrip("/")
    if base.endswith("/v1"):
        base = base[:-3]
    url = base + "/v1/chat/completions"

    phases = build_phases(args.hours, args.profile)
    mixed, heavy = load_prompts()
    if not mixed:
        raise SystemExit("no prompts loaded")
    if not heavy:
        print("WARNING: no long_context/long_gen prompts; the heavy phase will use the mixed pool")
        heavy = mixed

    planned = sum(p["minutes"] for p in phases)
    print(f"model      {args.model}")
    print(f"endpoint   {base}")
    print(f"prompts    {len(mixed)} mixed ({Counter(p.cls for p in mixed)}), {len(heavy)} heavy")
    print(f"profile    {args.profile}, {planned:.0f} min planned over {len(phases)} phases")
    for p in phases:
        print(f"  {p['name']:>13}  c{str(p['conc']):<5} {p['minutes']:6.1f} min  {p['pool']}")
    if args.dry_run:
        print("\ndry run: no calls made")
        return 0

    key = load_key()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
    outdir = Path(args.out) if args.out else REPO / "out" / "soak" / f"{stamp}-{args.model}"
    rec = Recorder(outdir)
    print(f"out        {outdir}")

    client = make_client(base, args.timeout, pool=72)
    usage_ok = probe_usage_support(client, url, key, args.model)
    print(f"usage      stream_options.include_usage {'HONOURED' if usage_ok else 'NOT honoured'}"
          f" -- token counts are {'exact' if usage_ok else 'estimated from SSE deltas'}")

    det_probes = [p for p in mixed if p.cls in ("short_qa", "reason", "json_struct")][:3]
    state: dict = {"phase": "startup", "conc": 0, "inflight": 0}
    poller = Poller(client, base, key, args.model, rec, state, det_probes,
                    every_s=15.0, determinism_every_s=600.0,
                    extra_metrics_url=args.metrics_url)
    poller.start()

    meta = {
        "schema_version": 1, "started_utc": stamp, "model": args.model, "endpoint": base,
        "profile": args.profile, "planned_minutes": planned, "seed": args.seed,
        "timeout_s": args.timeout, "decoding": {"temperature": TEMPERATURE, "top_p": TOP_P},
        "usage_reported_by_server": usage_ok,
        "prompt_classes": dict(Counter(p.cls for p in mixed)),
        "determinism_probes": [p.prompt_id for p in det_probes],
        "phases_planned": phases,
        "gpu_telemetry": "unavailable: LiteLLM virtual key scoped to llm_api_routes; "
                         "/metrics 401, /health 403. GPU util/VRAM/temp/power need DCGM or "
                         "vLLM /metrics on the GPU host. See docs/soak-test-plan.md.",
    }
    (outdir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    ctx = {"client": client, "url": url, "key": key, "model": args.model, "rec": rec,
           "state": state, "mixed": mixed, "heavy": heavy, "seed": args.seed,
           "timeout": args.timeout, "usage_ok": usage_ok, "poller": poller,
           "infra_lost": None}

    results = []
    ceiling = 4
    ceiling_found = False
    reclaimed = 0.0
    began = time.time()
    try:
        for phase in phases:
            # Once the breaking point is located, the remaining climb has nothing left to
            # learn and would only keep hammering a shared endpoint. Its minutes are handed
            # to the phases that do have something to measure.
            if phase.get("ramp") and ceiling_found:
                reclaimed += phase["minutes"]
                print(f"\n-- skipping {phase['name']}: ceiling already found at c{ceiling} "
                      f"({phase['minutes']:.1f} min reclaimed) --")
                results.append({"phase": phase["name"], "concurrency": phase["conc"],
                                "skipped": "ceiling already located"})
                continue
            if phase["conc"] is None:      # stress_hold runs at the highest survivor
                phase = dict(phase, conc=ceiling)
            if reclaimed and phase["name"] in ("stress_hold", "normal_end"):
                phase = dict(phase, minutes=phase["minutes"] + reclaimed / 2)
            print(f"\n== {phase['name']}  c{phase['conc']}  {phase['minutes']:.1f} min ==")
            outcome = run_phase(phase, ctx)
            results.append(outcome)
            if ctx.get("infra_lost"):
                print("\nEndpoint unreachable -- ending the run. Reconnect and re-run the "
                      "phases that did not complete; the data already on disk is valid up to "
                      "the disconnect.")
                break
            if phase.get("ramp"):
                if outcome["aborted"]:
                    ceiling_found = True
                    print(f"   ceiling: c{phase['conc']} stormed out; last clean level "
                          f"was c{ceiling}")
                else:
                    ceiling = phase["conc"]
    except KeyboardInterrupt:
        print("\ninterrupted -- partial data is on disk and is still analysable")
    finally:
        poller.stop_flag.set()
        poller.join(timeout=20)
        meta.update({
            "ended_utc": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ"),
            "elapsed_minutes": round((time.time() - began) / 60, 2),
            "phases_executed": results,
            "max_concurrency_sustained": ceiling,
            "outcome_counts": dict(rec.counts),
            "determinism_rows": poller.det_rows,
            # Present only when the run ended because the endpoint became unreachable. The
            # analysis truncates at this timestamp: everything after it measures a
            # disconnected client, not the model.
            "infrastructure_lost": ctx.get("infra_lost"),
        })
        (outdir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        rec.close()

    print(f"\ndone in {meta['elapsed_minutes']:.1f} min. outcomes: {dict(rec.counts)}")
    print(f"analyse with:  python scripts/soak_report.py {outdir}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
