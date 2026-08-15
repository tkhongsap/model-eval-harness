# 5-hour soak and stress test — Token Factory, `qwen3.6-27b-fp8`

**Preregistered 2026-08-14, before the first call.** Methodology document: this is written to be
re-run against any other model or GPU configuration by changing one CLI flag.

## Goal

Establish whether True's internal GPU serving stays stable and performant under five hours of
sustained and escalating load, and produce the baseline numbers every future model or GPU
configuration gets measured against.

Two questions, and the second is the one prior work left open:

1. **Does it hold?** Latency, throughput, error rate and correctness over 5 hours, and does
   anything drift between the first hour and the last.
2. **Does determinism decay under load?** The shakedown caught `gemma-4-12b-it` flipping on
   byte-identical requests once in six runs on an idle box, and hypothesised vLLM continuous
   batching: batch composition changes floating-point reduction order, so identical requests can
   decode differently. That hypothesis **predicts the flip rate rises with load**
   (`docs/gpu-shakedown-plan.md:174-176`), and its own follow-up action was to repeat the probe
   under deliberate load. This test does that.

## Scope boundary — what cannot be measured, and why

**GPU utilization, VRAM usage, temperature and power will not be in the report.** This is not an
omission; it is not obtainable. Measured 2026-08-14 against `https://10.94.154.102`:

| Route | No auth | With our key | Meaning |
|---|---|---|---|
| `/metrics` | 401 | **401** | Exists. Refused. |
| `/health` | 401 | **403** | Exists. Refused. |
| `/model/info` | 401 | **403** | Exists. Refused. |
| `/health/readiness` | **200** | 200 | `{"status":"healthy","db":"connected"}` |
| `/health/liveness` | **200** | 200 | `"I'm alive!"` |

The refusal is explicit about the cause:

> `Virtual key is not allowed to call this route. Only allowed to call routes:
> ['llm_api_routes']. Tried to call route: /metrics. To allow unauthenticated access, set
> `litellm_settings.require_auth_for_metrics_endpoint: false` in your proxy_config.yaml.`

Two separate gaps follow, and they need different asks:

1. **Gateway metrics** — one config line on the LiteLLM proxy, quoted above. Would give
   request/failure/latency counters and spend.
2. **Actual GPU telemetry** — *not available at any LiteLLM permission level.* LiteLLM is a
   gateway; it does not see a GPU. Utilization, VRAM, temperature and power require either a
   DCGM exporter / node-exporter on the GPU host, or vLLM's own `/metrics` (which carries
   `vllm:gpu_cache_usage_perc`, `num_requests_running`, `num_requests_waiting` and preemption
   counters — the best available VRAM-pressure proxy).

**Located 2026-08-15:** the response header `x-litellm-model-api-base: http://127.0.0.1:8000/v1`
shows vLLM runs on loopback on this same host, so `10.94.154.102` IS the GPU box. A port sweep
finds only 443 open and no nginx path proxies to it, so no client can reach it &mdash; but one
command on that host can. `scripts/gpu_telemetry.py` collects nvidia-smi and vLLM loopback
metrics into `gpu.jsonl`, which `scripts/soak_report.py` time-joins automatically.

The driver also polls for both on every cycle and records them the moment either appears, so acquiring
access later is a config change and not a rewrite. Until then the report substitutes **inferred**
signals and labels them as inferred: queue pressure from the latency-vs-concurrency curve,
memory pressure from output-length truncation and error taxonomy, and restarts from availability
gaps in unauthenticated health polling.

## Test environment

| | |
|---|---|
| Endpoint | `https://10.94.154.102/v1` (LiteLLM over vLLM), TLS verified against the pinned `configs/token-factory.crt.pem` |
| Model | `qwen3.6-27b-fp8` |
| Auth | LiteLLM virtual key from `TOKEN_FACTORY_API_KEY`, scoped `['llm_api_routes']` |
| Client | Python `httpx`, HTTP/1.1, one connection pool, SSE streaming enabled |
| Driver | `scripts/soak_test.py` (this repo) |
| Decoding | `temperature 0`, `top_p 1.0`, `max_tokens` per prompt class |

`top_p = 1.0` rather than production's `0`: the endpoint returns `400 top_p must be in (0, 1]`
regardless of what `openapi.yaml` declares. Greedy at temperature 0, so inert.

**Only documented fields are sent.** `Token_Factory_API_Guide.md:389` lists `model`, `messages`,
`stream`, `max_tokens`, `temperature`, `top_p` and warns "do not depend on an unlisted field even
if one request returns 200". `seed` and `response_format` are measured-working but undocumented,
so the soak sends neither: a 5-hour test is the wrong place to also be testing whether an
unsupported field keeps working. JSON-shaped output is requested in the prompt instead, which
makes structured-output *compliance* a measured result rather than a server guarantee.

## Load profile — 300 minutes

Closed-loop: each concurrency level runs N workers, and a worker issues its next request as soon
as its previous one returns. This is what "concurrency" means operationally and it matches how
every prior number in this repo was measured.

| # | Phase | Conc. | Minutes | Purpose |
|---|---|---:|---:|---|
| 1 | Baseline A | 1 | 20 | Uncontended reference. Compared directly against phase 7. |
| 2 | Sustained normal | 4 | 70 | The measured knee. The main stability window. |
| 3 | Stress ramp | 8 → 16 → 32 → 64 | 60 (15 each) | Find where it breaks. |
| 4 | Stress hold | highest survivor | 40 | Does the ceiling hold, or decay once found? |
| 5 | Long-context | 4 | 45 | 12–18k-char inputs and long generations. Memory-heavy. |
| 6 | Return to normal | 4 | 45 | Same config as phase 2 — the degradation comparison. |
| 7 | Baseline B | 1 | 20 | Same config as phase 1 — the drift comparison. |

Phases 2↔6 and 1↔7 are the degradation tests: identical configuration, ~4 hours apart. Any
difference is drift, not workload.

**Ramp-to-failure is the instruction, and stopping after failure is part of it.** The ramp climbs
to 64. It aborts the *climb* on a sustained error storm (>25% errors or >50% timeouts over a
2-minute window) and records that level as the ceiling — the aim is to locate the breaking point,
not to keep hammering a shared box after locating it. Token Factory reserves the right to "apply
immediate throttling or suspension when security, abuse, or capacity risk requires it"
(`Token_Factory_API_Guide.md:422`), and getting the key suspended would end the test.

**Retries follow the vendor's documented policy, not the harness's.** `runner.py:95-99` warns its
own retry path has no jitter and ignores `Retry-After`, and that it should be revisited before
concurrency grows — this driver does that: honour `Retry-After`, exponential backoff with jitter,
never retry 4xx except 429. Every attempt is recorded, so the report can state **both** the raw
per-attempt error rate (what the server did) and the post-retry error rate (what a client would
see). The harness default of 3 silent attempts would launder exactly the thing under test.

## Workload — 64 prompts across 7 classes

`tests/fixtures/soak/prompts_authored.jsonl`, plus 8 long-context items built at runtime from `retention_v3`. Synthetic throughout; no customer identifiers. Each prompt
carries `class`, `max_tokens`, and an `expect` block the checker uses, so correctness is measured
under load rather than assumed.

| Class | n | Shape | What it stresses |
|---|---:|---|---|
| `short_qa` | 12 | one-line factual | round-trip floor, TTFT |
| `summarize` | 8 | 600–1200 words in, ~150 out | balanced |
| `extract` | 8 | text in, named fields out | prefill-heavy |
| `json_struct` | 10 | strict JSON object out | output validity under load |
| `reason` | 10 | multi-step arithmetic/logic with a checkable answer | correctness under load |
| `long_context` | 8 | 12k–18k chars in, short out | KV cache, prefill, VRAM |
| `long_gen` | 8 | short in, 1500–2000 tokens out | decode throughput, truncation |

Phase 5 draws only from `long_context` and `long_gen`. Every other phase draws from the full set
in a seeded rotation, so each concurrency level sees the same mix and the levels stay comparable.

**A fixed determinism probe rides along.** Three prompts are re-sent byte-identically every 10
minutes throughout, and their outputs hashed. Flip rate against concurrency at the time of
sending is the direct test of the batching hypothesis.

## What is recorded

Per request, one JSONL row, `out/soak/<run>/requests.jsonl`:

`ts_start, ts_first_token, ts_end, phase, concurrency, worker, prompt_id, class, attempt,
http_status, error_type, ttft_s, e2e_s, prompt_tokens, completion_tokens, output_tokens_per_s,
finish_reason, truncated, output_sha256, valid (per the prompt's expect block), retry_after_s`

Per 30-second bucket, `out/soak/<run>/timeline.jsonl`: in-flight count, completed, errors by
class, p50/p95/p99 TTFT and e2e, tokens/s, requests/s, plus health-poll state and any scraped
metrics.

Health and metrics are polled every 15 s on a separate thread that never blocks the load, so an
availability gap is dated to the second.

## Analysis and deliverables

1. Test environment and exact configuration
2. Executive summary with **Pass / Pass with Issues / Fail**
3. Performance metrics, and GPU metrics or a precise statement of why they are absent
4. Results by concurrency level
5. Stability and degradation analysis — phase 2 vs 6, phase 1 vs 7
6. Errors found, root causes, fixes applied, verification of each fix
7. Recommended operating configuration and sustainable concurrency
8. Baseline metrics for future model and GPU comparisons
9. Locations of raw logs, scripts, prompts and artifacts

Pass criteria, fixed now rather than after seeing the numbers:

- **Pass** — no unexplained errors; post-retry error rate < 1% at the recommended concurrency;
  phase 6 p95 within 20% of phase 2; no truncation increase; no availability gap.
- **Pass with issues** — the above holds but with a bounded, explained defect (e.g. a known
  ceiling, or a determinism rate that rises with load and is characterised).
- **Fail** — unexplained errors, degradation beyond 20%, a restart, or a defect that could not be
  root-caused.

## Error handling

Any error, instability or configuration issue is investigated to root cause, fixed where the fix
is ours to make, and the affected phase re-run and verified. Anything not fixable is documented
with the reason. A logged-and-ignored error is not an acceptable outcome; neither is a green run
bought by weakening a check.

## Amendments from the 2026-08-14 attempt

The first execution of this plan ran 155 valid minutes and then lost its network path. It is
kept as `out/soak/20260814-155900Z-qwen3.6-27b-fp8` and its verdict is **INCOMPLETE**. Six
changes came out of it; all were made before the re-run, so the re-run executes the amended
plan rather than this one.

**Two workload caps were wrong, found by the smoke run and fixed before the first attempt.**
`short_qa` at `max_tokens 80` truncated 21 of 21 responses — the answers were still correct but
truncation sat at 100%, which destroys it as a degradation signal. Raised to 160; now 30%.
`SK-042` failed its check on every attempt at `max_tokens 500`: the expected value is right
(C(9,4) x C(9,2) = 126 x 36 = 4536, re-derived by hand) and the model simply ran out of budget
mid-reasoning. `reason` raised to 900; now `finish_reason=stop` and valid. Expectations were not
touched — the budgets were.

**Four defects in the instrument itself, all found by the failed run and all fixed:**

1. **No watchdog for total connectivity loss.** The storm check only ran on `ramp` phases, so
   when the route dropped at +157 min the run continued for another 138 minutes recording 9,365
   connect timeouts against a disconnected laptop. There is now an infrastructure watchdog on
   *every* phase: 8 consecutive dead polls of the unauthenticated health endpoint plus a ~100%
   error rate ends the run. The distinction it encodes is the important part — a failing model
   returns 5xx or slows down and some requests still succeed; a lost network path fails to
   connect at all and takes the unauthenticated endpoint with it. The second is not a test
   result, it is the test being unable to run.
2. **The determinism probe ran inline on the poller thread.** Its calls take ~170 s at c64, so
   it blocked health polling for up to 504 s and manufactured five phantom "availability gaps"
   while the endpoint was serving normally. It now has its own thread.
3. **The analysis truncated the disconnect on `ts_start`.** Requests that began while the path
   was up and died when it dropped stayed in the sample, charging the disconnect to whatever
   concurrency was running and reporting a **false 10.4% error rate at c64**. It cuts on
   `ts_end` now, and the c64 leg reads 0.00%.
4. **"Recommended concurrency" meant "fastest level that did not error".** On this endpoint
   that selects c32 — which has a **50-second TTFT** and is not something anyone would operate.
   The recommendation is now the highest level holding TTFT p50 within 3x the uncontended
   baseline, and the peak-throughput level is reported alongside it, because on a saturating
   endpoint the two genuinely disagree.

**A gap in this plan, recorded rather than quietly patched:** the pass criteria fixed error rate
and drift but never defined "recommended sustainable concurrency", which is why defect 4 was
possible. The criterion above is derived from the run's own baseline rather than being a number
chosen after seeing results, but it was still added mid-flight and should be treated as part of
the contract from here on.

**A verdict was added.** `INCOMPLETE` — the measured portion is clean but the run did not cover
its phases. Previously such a run would have read PASS on partial data, claiming coverage it did
not have.

## Repeatability

```bash
python scripts/soak_test.py --model qwen3.6-27b-fp8 --hours 5
python scripts/soak_test.py --model gemma-4-12b-it --hours 5     # any other served model
python scripts/soak_test.py --profile smoke                      # ~6 min, same code path
python scripts/soak_report.py out/soak/<run>                     # analysis and report
```

The profile table, prompt pack and pass criteria are data, not code. Pointing the runtime manifest
at a different endpoint benchmarks a different GPU configuration with the same methodology.
