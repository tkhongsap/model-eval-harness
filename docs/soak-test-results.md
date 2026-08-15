# 5-hour soak and stress test — results

**`qwen3.6-27b-fp8` on True's internal GPU serving (Token Factory). Run 2026-08-14 21:40Z to
2026-08-15 02:27Z. Plan and pre-registered criteria: [`soak-test-plan.md`](./soak-test-plan.md).**

# Verdict: PASS WITH ISSUES — on an INCOMPLETE metric set

Two separate judgements, and collapsing them would misrepresent the run:

| | |
|---|---|
| **Endpoint behaviour** | **PASS WITH ISSUES** — measured, against criteria fixed before the run. |
| **Requirement coverage** | **INCOMPLETE** — 9 of the 13 required metrics were collected. Four were not, and could not be from any client. |

## Requirement coverage

| Required metric | Collected | Where |
|---|---|---|
| TTFT | **yes** | §3, §4 — per request, from SSE first content delta |
| End-to-end latency | **yes** | §3, §4 — p50/p95/p99/max |
| Tokens/sec | **yes** | §3 — per request and aggregate |
| Requests/sec | **yes** | §4 — per concurrency level |
| Input tokens | **yes** | §3 — 2,369,948, server-reported |
| Output tokens | **yes** | §3 — 1,597,854, server-reported |
| Error rate | **yes** | §3, §6 — raw per-attempt and post-retry |
| Server restarts | **yes** | §5 — 1,137 health polls, zero failures |
| Max / recommended concurrency | **yes** | §4, §7 |
| Begin-vs-end comparison | **yes** | §5 — both paired phases |
| OOMs | **partial** | No direct OOM signal is exposed. Inferred absent from zero 5xx, zero truncation increase and flat latency; the direct signal would be `vllm:num_preemptions_total`, which needs host access. |
| **GPU utilization** | **NO** | Unobtainable from a client — see §3 |
| **VRAM usage** | **NO** | Unobtainable from a client — see §3 |
| **Temperature** | **NO** | Unobtainable from a client — see §3 |
| **Power** | **NO** | Unobtainable from a client — see §3 |

### How to close this — two commands, no file transfer

On `10.94.154.102`, during a run:

```bash
python3 gpu_telemetry.py --out gpu.jsonl --duration 5400 --vllm http://127.0.0.1:8000/metrics
```

Then, because the only open port on that host serves the chat API and a 240&nbsp;KB file
generally cannot leave it:

```bash
python3 gpu_telemetry.py --summarise gpu.jsonl     # ~23 lines: paste them back
```

That prints utilization, VRAM, temperature and power as min/mean/p95/max plus KV-cache
occupancy, queue depth and preemptions, in five-minute buckets stamped with epoch time — the
stamps are what let it be joined to the load phases afterwards without the raw file. Dropping
the full `gpu.jsonl` into the run directory instead gives a finer join and needs no paste at
all; either path closes the gap.

**This test is not finished.** The four GPU metrics are a stated requirement and they are absent.
They are blocked on one command being run on `10.94.154.102`, which no client-side work can
substitute for — established exhaustively in §3, not assumed. `scripts/gpu_telemetry.py`
collects them and the merge is implemented and verified; the moment that file exists this
section closes and the coverage row flips.



287 minutes, **3,674 requests, 3,714 attempts, post-retry error rate 0.00%**, task correctness
99.5%, zero availability gaps, and no measurable degradation over five hours. Three issues, all
bounded and explained, none of them a server fault.

| | |
|---|---|
| **Passes** | No unexplained errors. Post-retry error rate 0.00% at every concurrency level (bar: <1%). p95 latency drift over five hours **+1.3%** (bar: <20%). 1,137 health polls, zero non-200, zero availability gaps. No restart, no OOM signature, no truncation increase. |
| **Issues** | (1) Determinism decays with load — 28 of 84 byte-identical probes diverged. (2) `short_qa` truncates 63% at its 160-token cap. (3) 40 requests exceeded the 240 s client read timeout, all recovered on retry. |
| **Not measured** | GPU utilization, VRAM, temperature, power. Not obtainable from any client: vLLM is bound to `127.0.0.1:8000` on the GPU host and only port 443 is open. `scripts/gpu_telemetry.py` collects them in one command from that host &mdash; see §3. |

---

## 1. Test environment and exact configuration

| | |
|---|---|
| Endpoint | `https://10.94.154.102/v1` — LiteLLM gateway over vLLM, TLS verified against the pinned `configs/token-factory.crt.pem` |
| Model | `qwen3.6-27b-fp8` (fp8 quantised) |
| Auth | LiteLLM virtual key from `TOKEN_FACTORY_API_KEY`, scoped `['llm_api_routes']` |
| Transport | Python `httpx`, HTTP/1.1, SSE streaming, connection pool 72, connect timeout 15 s, read timeout 240 s |
| Decoding | `temperature 0`, `top_p 1.0`, `max_tokens` per prompt class |
| Retries | Max 3 attempts; honour `Retry-After`; exponential backoff 1/2/4 s **with jitter**; never retry a 4xx other than 429 |
| Driver | `scripts/soak_test.py`, seed 17 |
| Workload | 64 prompts, 7 classes (`tests/fixtures/soak/prompts_authored.jsonl` + 8 long-context built at runtime from `retention_v3`) |

**Only documented request fields were sent** — `model`, `messages`, `stream`, `max_tokens`,
`temperature`, `top_p` (`Token_Factory_API_Guide.md:389`). `seed` and `response_format` work here
but are undocumented, and a five-hour run is the wrong place to also test whether an unsupported
field keeps working. `stream_options.include_usage` is the one exception and it was **probed
before use** — the server honours it, so every token count below is the server's own, not an
estimate.

`top_p = 1.0` rather than production's `0`: the endpoint returns `400 top_p must be in (0, 1]`.
Greedy at temperature 0, so inert.

## 2. Executive summary

The endpoint is **stable and predictable, and it does not fail under load — it queues.** Across
five hours it returned zero server errors: no 5xx, no 429, no connection resets. Every failure
recorded in this run was the *client* giving up at its own 240-second read timeout while a
request sat in a queue, and every one of those 40 requests succeeded on retry.

Throughput saturates hard and early. Concurrency 8 delivers 0.386 req/s; concurrency 16 delivers
0.368. **Past c8, additional concurrency buys nothing and is paid for entirely in latency** —
time-to-first-token goes from 0.23 s at c8 to 17.4 s at c16, a 74x increase for no throughput.
This is textbook queueing, and it means the useful operating range is narrow and well defined.

Over five hours there is **no drift**: the last normal-load phase is within 1.6% of the first on
median latency and 1.3% on p95, four hours apart. No memory-growth signature, no throughput
decay, no rising error rate.

## 3. Performance and GPU metrics

| | overall |
|---|---|
| Requests / attempts | 3,674 / 3,714 |
| Post-retry error rate | **0.00%** |
| Raw per-attempt error rate | 1.08% |
| Task correctness under load | **99.5%** |
| TTFT p50 / p95 | 0.453 s / 28.27 s |
| End-to-end p50 / p95 / p99 | 18.38 s / 99.86 s / 117.44 s |
| Input tokens | 2,369,948 |
| Output tokens | 1,597,854 |
| Aggregate output throughput | 92.8 tok/s |
| Per-request decode rate | ~20.5 tok/s at c1–c8 |

### GPU utilization, VRAM, temperature and power are absent

Not an omission. They are **not obtainable through this API at any permission level**, and that
was established before the run rather than discovered after it. Measured against the endpoint:

| Route | No auth | With our key |
|---|---|---|
| `/metrics` | 401 | **401** |
| `/health` | 401 | **403** |
| `/model/info` | 401 | **403** |
| `/health/readiness` | **200** | 200 |
| `/health/liveness` | **200** | 200 |

The refusal names its own fix:

> `Virtual key is not allowed to call this route. Only allowed to call routes:
> ['llm_api_routes']… To allow unauthenticated access, set
> `litellm_settings.require_auth_for_metrics_endpoint: false` in your proxy_config.yaml.`

Flipping that config line unlocks *gateway* metrics — request and failure counters, spend. It
does **not** produce GPU telemetry: LiteLLM is a proxy and never sees a GPU.

### Where the GPU metrics actually live — established, not assumed

The endpoint's own response headers name the topology:

```
x-litellm-model-api-base:  http://127.0.0.1:8000/v1
x-litellm-version:         1.92.0
llm_provider-server:       uvicorn
server:                    nginx
system_fingerprint:        vllm-0.23.1rc1.dev245+g9037498c2-5b2fbd3a
```

**vLLM runs on loopback on the same host as the gateway.** `10.94.154.102` is the GPU box. Its
vLLM `/metrics` — carrying `vllm:gpu_cache_usage_perc`, `num_requests_running`,
`num_requests_waiting` and the preemption counters — is live at `http://127.0.0.1:8000/metrics`
*on that machine* and is simply not exposed.

Verified unreachable from a client, four ways — this was searched exhaustively, not assumed:

1. **Port sweep of the host** (443, 80, 22, 3000, 4000, 5000, 8000, 8001, 8080, 9090, 9100,
   9400, 10250 and others): **only 443 is open.** No vLLM port, no DCGM exporter, no
   node-exporter, no Prometheus, no Grafana.
2. **Every route the endpoint advertises.** `/openapi.json` is public and lists **527 routes**.
   All 141 matching `metric|health|stat|gpu|util|memor|info|model|cache|load|debug|config` were
   identified and every plausible one tested. **Exactly three answer without admin scope:**
   `/health/liveness`, `/health/liveliness`, `/health/readiness` — plus the static
   `/.well-known/litellm-ui-config` and `/get/ui_settings`. None carries a GPU counter.
3. **No nginx route to vLLM.** Seventeen candidate proxy paths tried; every `*metrics*` path
   resolves to LiteLLM's own guarded endpoint and returns the same 401.
4. **The virtual key** is scoped `['llm_api_routes']`, so `/metrics`, `/health`, `/model/info`,
   `/key/info`, `/health/services` and `/global/spend/report` are all refused.

The admin UI *is* reachable at `/ui/` and `/get/ui_settings` reveals
`allow_public_health_readiness_details: false` — a flag that would open
`/health/readiness/details`. That route reports model connectivity, **not** GPU counters, so
enabling it would not satisfy this requirement either. No attempt was made to authenticate to
the admin UI: guessing at credentials on a colleague's endpoint is not testing.

**This is architectural.** LiteLLM is a gateway and has no GPU telemetry to expose at any
permission level. The counters exist only in `nvidia-smi` and in vLLM's loopback `/metrics` on
the host itself. No client-side work reaches them.

### It is now a one-command handoff

`scripts/gpu_telemetry.py` was written to close this. Standard library only, Python 3.8+,
nothing to install. Run it **on `10.94.154.102`** while a soak runs from anywhere:

```bash
python3 gpu_telemetry.py --out gpu.jsonl --vllm http://127.0.0.1:8000/metrics
# then, back where the soak ran:
cp gpu.jsonl out/soak/<run>/gpu.jsonl && python scripts/soak_report.py out/soak/<run>
```

It samples `nvidia-smi` every 5 s for utilization, VRAM used/total, temperature and power, and
scrapes vLLM's loopback metrics for KV-cache occupancy, queue depth and preemptions. The
analysis time-joins it to the load phases automatically, producing utilization-versus-
concurrency, a start-versus-end VRAM growth figure, and KV-cache pressure at each level.
**The join is implemented and verified end to end against a synthetic sample** — the moment that
file exists the GPU sections populate with no further work.

Until then this report substitutes signals it *can* measure, labelled as inference: queue
pressure from the latency-versus-concurrency curve (§4), memory pressure from truncation and
error taxonomy, restarts from unauthenticated health polling (1,137 polls, zero failures).

**What their absence costs this report, concretely:** it cannot say whether the c8 knee is
compute-bound or KV-cache-bound, and it cannot rule out slow VRAM growth as a mechanism behind
anything. The latency evidence shows no degradation, but that is behaviour, not memory.

## 4. Results by concurrency level

| level | requests | req/s | TTFT p50 | TTFT p95 | e2e p50 | e2e p95 | tok/s per req | error | correct |
|---|--:|--:|--:|--:|--:|--:|--:|--:|--:|
| c1 | 124 | 0.007 | **0.188 s** | 0.484 s | 7.33 s | 90.88 s | 21.8 | 0.00% | 100.0% |
| c4 | 1,552 | 0.105 | **0.234 s** | 0.562 s | 7.69 s | 95.08 s | 20.9 | 0.00% | 99.3% |
| **c8** | 359 | **0.386** | **0.234 s** | 0.578 s | 7.66 s | 95.11 s | 20.8 | 0.00% | 99.7% |
| c16 | 1,542 | 0.368 | 17.39 s | 32.09 s | 27.03 s | 112.50 s | 6.5 | 0.00% | 99.6% |
| c32 | 97 | — | — | — | — | — | — | — | 100.0% |

*(c1 and c4 req/s are low because those phases are long and lightly loaded by design; compare
levels on the per-phase table in §5, where each ran alone.)*

**The knee is between c8 and c16, and it is sharp.** Doubling concurrency from 8 to 16 *reduced*
throughput slightly (0.386 → 0.368 req/s) while multiplying TTFT by 74x and cutting per-request
decode rate from 20.8 to 6.5 tok/s. Nothing above c8 is buying capacity; it is buying queue.

**c32 is reported without statistics, deliberately.** The phase aborted after 7.8 of its planned
14.2 minutes when 45% of attempts in a two-minute window exceeded the client timeout. Only 97
requests completed, and they are a survivorship sample — the ones that happened not to queue —
so their percentiles would flatter c32 rather than describe it. What c32 *does* establish: at
that concurrency, a client with a 240-second timeout fails roughly half its requests.

- **Maximum tested concurrency: 32** (aborted; 64 correctly skipped once the ceiling was located)
- **Recommended sustainable concurrency: 8** — the highest level holding TTFT p50 within 3x the
  uncontended c1 baseline of 0.188 s

### The failure mode is queueing, not rejection

Every one of the 40 failures sat at exactly the 240-second client read timeout (min 240, median
240, max 352). The server returned **no 5xx, no 429, no reset, and no `Retry-After`** at any
point in five hours. It does not shed load, throttle, or refuse — it accepts everything and
queues it. Two consequences worth acting on:

1. **Client timeout is the real capacity limit.** A caller with a 60-second timeout will start
   failing at a much lower concurrency than one with 240 seconds, on the same healthy server.
2. **There is no backpressure signal.** Because the endpoint never returns 429, a client cannot
   tell "busy" from "slow" and cannot back off intelligently. This is worth raising with the
   platform team — a queue-depth limit returning 503, or vLLM's `num_requests_waiting` exposed,
   would let callers behave correctly.

## 5. Stability and degradation

| phase | conc | requests | req/s | TTFT p50 | e2e p50 | e2e p95 | error | correct |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| baseline_a | 1 | 62 | 0.053 | 0.188 s | 7.27 s | 89.33 s | 0.00% | 100.0% |
| normal | 4 | 788 | 0.195 | 0.219 s | 7.63 s | 94.24 s | 0.00% | 99.0% |
| ramp_8 | 8 | 359 | 0.386 | 0.234 s | 7.66 s | 95.11 s | 0.00% | 99.7% |
| ramp_16 | 16 | 385 | 0.412 | 16.13 s | 25.84 s | 111.94 s | 0.00% | 99.7% |
| ramp_32 | 32 | 97 | — | — | — | — | — | 100.0% |
| stress_hold | 16 | 1,157 | 0.415 | 17.89 s | 27.45 s | 112.59 s | 0.00% | 99.5% |
| long_context | 4 | 183 | 0.069 | 0.266 s | 90.55 s | 95.53 s | 0.00% | 100.0% |
| normal_end | 4 | 581 | 0.191 | 0.219 s | 7.75 s | 95.50 s | 0.00% | 99.5% |
| baseline_b | 1 | 62 | 0.052 | 0.188 s | 7.38 s | 90.95 s | 0.00% | 100.0% |

### No degradation, on either paired comparison

Identical configuration, roughly four hours apart:

| | normal → normal_end | baseline_a → baseline_b |
|---|--:|--:|
| e2e p50 | **+1.6%** | **+1.5%** |
| e2e p95 | **+1.3%** | **+1.8%** |
| TTFT p50 | **+0.0%** | **+0.0%** |
| TTFT p95 | −8.2% | −6.4% |
| throughput | −2.1% | −1.7% |
| error rate | 0.00% → 0.00% | 0.00% → 0.00% |
| truncated | 199 → 137 | 14 → 14 |

Every figure is inside noise and far inside the 20% bar. **No memory growth, no latency creep,
no throughput decay, no rising error rate** after five hours including a 38-minute sustained
stress hold at c16. TTFT p50 is identical to the millisecond at both ends of the run.

**Availability: 1,137 health polls, zero non-200, zero gaps, zero restarts.** Health was polled
every 15 s on a dedicated thread against the unauthenticated liveness endpoint throughout.

### `long_context` held up

183 requests of 12–18k-character Thai transcripts at c4: **100% correct, 0% error, 0%
truncation**, TTFT p50 0.266 s. Long inputs cost end-to-end time (p50 90.5 s) but do not
destabilise the server or inflate time-to-first-token — prefill is not where this endpoint hurts.

### Determinism decays with load

Three prompts re-sent byte-identically every ten minutes and hashed. The shakedown hypothesised
vLLM continuous batching — batch composition changing floating-point reduction order — which
predicts divergence *rising* with concurrency. It does:

| concurrency at send | probes | diverged | rate |
|---|--:|--:|--:|
| c1 | 12 | 1 | 8.3% |
| c4 | 51 | 17 | 33.3% |
| c8 | 3 | 0 | 0.0% |
| c16 | 18 | 10 | **55.6%** |
| **total** | **84** | **28** | **33.3%** |

The trend from c1 (8.3%) to c16 (55.6%) supports the hypothesis. Two honest caveats: c8's 0% is
three probes and means nothing, and the probe count per level is uneven because probes fire on a
clock while phases have different lengths. **This is directional evidence, not a settled
measurement** — a dedicated experiment holding everything else fixed would settle it.

It matters beyond curiosity: at temperature 0 with a fixed prompt, a third of responses were not
reproducible. Anything downstream that assumes byte-stable output — caching, diffing, golden
tests — cannot assume it here.

## 6. Errors found, root causes, fixes, and verification

### 6a. In the endpoint: none

**No server error of any kind in five hours.** No 5xx, no 429, no reset, no restart. The 40
client timeouts (§4) are a queueing consequence, not a server fault, and all 40 recovered on
retry.

### 6b. In the first attempt: the client lost its network path

The first execution (`out/soak/20260814-155900Z-qwen3.6-27b-fp8`) ran 155 valid minutes and then
recorded 9,365 connect timeouts. **This was not the endpoint.** Diagnosis:

| Evidence | Server crash | Lost network path |
|---|---|---|
| ICMP to host failed | ✗ a hung service still answers ping | ✓ |
| Port 22 also dead, plus 80/8000/4000 | ✗ LiteLLM crashing cannot kill SSH | ✓ |
| Ramp to c64 was 100% clean immediately before | ✗ a dying server degrades first | ✓ instant cliff |
| Host had no 10.x interface; machine on 192.168.1.x, no VPN | — | ✓ |
| General internet fine | — | ✓ |
| TIME_WAIT sockets: 13 | rules out client port exhaustion | ✓ |

**Fix:** reconnect the corporate network; re-run in full. **Verified:** the re-run completed all
ten phases with zero infrastructure loss. That run is retained and marked **INCOMPLETE**, not
FAIL — the endpoint did not fail, the test could not run.

### 6c. In the instrument: six defects, all fixed and re-verified

Found by the smoke runs and the failed attempt. Each was fixed before the run that produced this
report, so these results come from the amended harness.

| # | Defect | Consequence | Fix | Verified by |
|---|---|---|---|---|
| 1 | `short_qa` capped at 80 tokens | 21/21 truncated — saturated, useless as a signal | cap → 160 | 63% here, not 100% |
| 2 | `reason` capped at 500 tokens | `SK-042` failed its check every time; the model ran out of budget mid-working. Expected value re-derived by hand and confirmed correct: C(9,4)×C(9,2) = 126×36 = **4536** | cap → 900 | `reason` truncation **0/520**, correctness **100%** |
| 3 | No watchdog for total connectivity loss | Burned 138 minutes recording a disconnected laptop | Infrastructure watchdog on every phase: 8 dead health polls + ~100% errors ends the run | `infrastructure_lost: None`; run completed |
| 4 | Determinism probe ran on the poller thread | Its ~170 s calls at c64 blocked health polling, faking five "availability gaps" | Dedicated thread | **1,137 polls, 0 gaps, 0 artifacts** |
| 5 | Analysis cut the disconnect on `ts_start` | Charged the disconnect to c64, reporting a false **10.4%** error rate | Cut on `ts_end` | c64 re-read 0.00% |
| 6 | "Recommended concurrency" = fastest level that didn't error | Would have recommended **c32 at a 50-second TTFT** | Latency-knee criterion; peak-throughput level reported separately | Recommends c8, basis stated |

None of these was fixed by weakening a check. Defects 1 and 2 raised token budgets and left every
expectation untouched.

### 6d. Open issues, documented rather than closed

1. **`short_qa` truncates 63%** (421/666) at its 160-token cap. Correctness holds at 97.2%, so the
   answers are right, but the class no longer purely measures "answer a short question". A future
   run should raise it to ~250. Not fixed mid-run: changing the workload between phases would
   have invalidated the degradation comparison, which is the point of the test.
2. **`long_gen` truncates 96%** at 2,000 tokens. This is by design — the prompts ask for long
   documents and the cap is the stop condition — and correctness is 100%. Excluded from
   truncation-as-degradation reasoning.
3. **Determinism at 33%** overall (§5). Characterised and directional; not root-caused, because
   confirming continuous batching needs `vllm:num_requests_running` at the moment of each probe,
   which is behind the metrics access we do not have.
4. **The ceiling moved between runs.** c32 ran clean for 14 minutes on the first attempt
   (0% error, TTFT p50 50 s) and stormed out after 7.8 minutes on the second. Same client, same
   config. Most likely other traffic on a shared endpoint, but unproven — with no server-side
   metrics there is no way to see concurrent load from other callers. **Treat the recommended
   concurrency as the reliable number and the ceiling as approximate.**

## 7. Recommended operating configuration

| | |
|---|---|
| **Concurrency** | **8** per client. This is the entire useful range: c8 is peak throughput *and* the last level with sub-second TTFT. |
| **Client read timeout** | **≥120 s** for mixed workloads; **≥240 s** if long generations are in the mix. The endpoint queues rather than rejecting, so a short timeout manufactures failures on a healthy server. |
| **Retries** | 3 attempts, exponential backoff **with jitter**. All 40 timeouts here recovered on retry. Do not retry without jitter — with no 429 signal, synchronised retries re-create the burst. |
| **Expect** | ~0.39 req/s and ~93 output tok/s aggregate per client at c8; ~20.5 tok/s per request. |
| **Do not** | exceed c16. It costs 74x TTFT for no throughput. Above c32 a 240 s client starts failing about half its requests. |
| **Plan around** | no backpressure signal and non-reproducible output at temperature 0. |

## 8. Baseline for future models and GPU configurations

```json
{
  "model": "qwen3.6-27b-fp8",
  "endpoint": "https://10.94.154.102 (LiteLLM/vLLM, fp8)",
  "test": "scripts/soak_test.py --hours 5",
  "verdict": "PASS WITH ISSUES",
  "duration_min": 287.04,
  "requests": 3674,
  "recommended_concurrency": 8,
  "max_concurrency_tested": 32,
  "requests_per_s_at_c8": 0.386,
  "ttft_p50_s_at_c8": 0.234,
  "ttft_p50_s_uncontended": 0.188,
  "e2e_p50_s_at_c8": 7.66,
  "output_tokens_per_s_per_request": 20.8,
  "aggregate_output_tokens_per_s": 92.8,
  "post_retry_error_rate": 0.0,
  "raw_attempt_error_rate": 0.0108,
  "correctness_rate": 0.995,
  "p95_drift_over_5h": 0.013,
  "determinism_divergence_rate": 0.333,
  "server_errors_5xx_or_429": 0
}
```

Re-running `scripts/soak_test.py --model <other> --hours 5` against any OpenAI-compatible
endpoint produces this block in the same shape, so comparisons are like for like. Pointing
`configs/runtime.token-factory.json` at a different host benchmarks a different GPU configuration
with the same methodology.

## 9. Artifacts

| What | Where |
|---|---|
| Plan, criteria, amendments | `docs/soak-test-plan.md` |
| This report | `docs/soak-test-results.md` |
| Visual report | `docs/soak-test-report.html` |
| Load driver | `scripts/soak_test.py` |
| **GPU telemetry collector** | `scripts/gpu_telemetry.py` &mdash; run ON the GPU host; stdlib only. Fills in utilization/VRAM/temperature/power. |
| Analysis | `scripts/soak_report.py` → `analysis.json`, `report.md` |
| HTML renderer | `scripts/soak_report_html.py` (renders `analysis.json`, so it cannot disagree with the markdown) |
| Prompt pack | `tests/fixtures/soak/prompts_authored.jsonl` (56 authored; 8 long-context built at runtime from `retention_v3`) |
| **Completed run** | `out/soak/20260814-214007Z-qwen3.6-27b-fp8/` |
| Incomplete first attempt | `out/soak/20260814-155900Z-qwen3.6-27b-fp8/` |
| Raw per-attempt log | `requests.jsonl` — 26 fields per attempt including `ts_start`, `ts_first_token`, `ts_end`, `req_id`, `ttft_s`, `output_sha256`, `valid` |
| 30-second buckets | `timeline.jsonl` |
| Health + determinism | `health.jsonl` |

Raw logs live under `out/` and are **not committed** — no completions, no transcripts. Scripts,
prompts, plan and this report are.

## What this cannot tell you

It measures the endpoint **as served on 2026-08-14/15** — one model, fp8, this gateway, this
batching configuration, and whatever other traffic shared it. It says nothing about GPU headroom,
because no GPU metric was obtainable; the endpoint may have been at 30% utilization or 95% and
this test cannot distinguish those. And the ceiling moved between two runs hours apart, so the
recommended c8 is well-evidenced while the c32 ceiling is a single observation.
