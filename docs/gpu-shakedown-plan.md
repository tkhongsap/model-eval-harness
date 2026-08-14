---
type: plan
created: 2026-08-14
status: EXECUTED 2026-08-14; results below the plan
tags: [work/true, project/intelligence-layer, gpu, shakedown]
---

> **Executed 2026-08-14, 7.5 minutes, 7 commands, zero refusals.** Results are in
> "As executed" at the foot of this document. Headline: the harness drives the endpoint
> end to end over verified TLS with no code change, one backend serves each model, and
> **Gemma flipped once in six determinism runs** — which is the one finding that could
> corrupt the full comparison, and it is not yet explained.

# GPU shakedown: can Token Factory serve a decision-grade arm?

## Goal

**Decide whether True's internal Token Factory can serve an evaluation arm, and produce the
numbers needed to size the full comparison — before spending the full comparison.**

This is a shakedown, not an evaluation. It is deliberately run *before* the real
Qwen-vs-Gemini-vs-GPU comparison, because every way that run can fail expensively is a
thing this run can find cheaply. Nothing it produces is a migration verdict, and its
quality numbers are too small to be one.

## Why now

`DEVLOG.md` roadmap item 3 has read *"Not done: an actual company-GPU execution"* since
2026-08-08. The endpoint now exists and answers. What is not established is whether the
**harness** can drive it under the decoding every committed plan pins — and one thing
already says it cannot.

## Authoritative source

In precedence order:

1. The endpoint itself, driven through **the harness's own commands** (`evalgen check`,
   `stability`, `baseline`, `compare`) rather than a bespoke client. A shakedown that
   probed a different request shape than a run sends would qualify a call nobody makes —
   the argument `scripts/provider_probe.py` already makes.
2. `configs/runtime.token-factory.json` — the reviewed runtime contract, hashed into every
   run.
3. `Token_Factory_API_Guide.md` and `openapi.yaml` as vendored, for what is *claimed*.
   Where claim and behaviour differ, behaviour wins and the difference is recorded.

## The five questions, in the order they can kill the full run

**Q1 — Can the harness reach it at all, over verified TLS?**
Settled during setup and recorded here because the answer changed the design. The endpoint
presents a **self-signed** certificate, so the public trust store rejects it. The first
probes used `verify=False`; that was the wrong fix. The certificate's SAN covers **both**
the hostname and `10.94.154.102`, so pinning it at `configs/token-factory.crt.pem` and
pointing `SSL_CERT_FILE` at it gives **fully verified TLS with the stock client** — no
custom transport, no Host-header override, and stronger verification than the public-CA
default, because exactly one certificate is trusted. Confirmed working end to end.

**Q2 — Does the endpoint accept the request an arm actually sends?**
The real prompt, the real 531-line retention schema as `response_format`, a real Thai
transcript, at the pinned decoding. Already known to fail on one parameter: every model
rejects `top_p = 0` with `400 top_p must be in (0, 1]`. That is the blocking finding, and
Q2 establishes whether anything *else* also fails once the request is the real one.

**Q3 — Is it deterministic at `temperature = 0`?**
The one that decides whether an evaluation is possible at all. The harness's binding gate
is stability: Qwen 27B failed Experiment 7 on stability alone. If the endpoint returns
different answers to byte-identical requests, model instability and sampling noise are
indistinguishable and the gate measures nothing. Measured with `evalgen stability`, the
real N_flip probe, not a hand-rolled loop.

**Q4 — Is one arm one backend?**
`prompt_tokens` must return exactly one value per item across replicates. MEASURED
2026-08-04 on OpenRouter: a 60-call run was served by *two* builds under one model id, and
`observed_models` saw nothing because the id never changed. The token fingerprint cannot be
faked by a router echoing its own routing. `evalgen stability` prints this line.

**Q5 — What does it cost in wall-clock, and does it hold under concurrency?**
Single-stream rate is already known (42 tok/s Gemma, 21 tok/s Qwen, R²=1.00 against output
length). Unknown, and the number that actually sizes a run: throughput at concurrency 4 and
8, and whether either produces errors. Concurrency levels match those the committed plans
already declare.

## Scope boundary

- **Two chat models only**: `qwen3.6-27b-fp8` and `gemma-4-12b-it`. `qwen3-asr-1.7b` is a
  speech model — it answered `language None<asr_text>` to a text prompt. Testing it needs
  audio fixtures that do not exist in this repository, and inventing them here would be a
  second project. Recorded as **not tested**, not as a failure.
- **`retention_v1`** (20 items, 22 scored rows). Small on purpose: it is the frozen pack
  Experiments 1–2 used, so a quality number here is at least *comparable in kind* — while
  being far too small to decide anything.
- **No change to `src/evalharness/` or `src/evalgen/`.** If the shakedown needs a code
  change to pass, that is a finding to record, not a change to slip in.

## Deviation from the pinned decoding, declared before running

`top_p = 0` is rejected, so this run sends **`top_p = 1.0`**.

With `temperature = 0` the decode is greedy and `top_p` is inert, so `1.0` and *omitted*
are behaviourally identical here — but `1.0` is a value the CLI can already express, and
adding an "omit" path to the harness for a shakedown would be changing the decoding surface
to make an exploratory run pass.

**This is a deviation and is labelled as one.** It does not join the closed deviation list
in `decoding.py`: that list governs what a *committed experiment* deviates from production,
and this is not a committed experiment. Any real run against this endpoint must settle the
question properly first, and the honest reading is that `top_p = 0` is a **mis-specification
on our side** — it selects the token set whose cumulative probability reaches zero, which is
empty. `temperature = 0` already expresses what was wanted. Other providers accepted `0` and
nothing records what they did with it, so the decoding our plans *pin* may not be the
decoding earlier runs *used*.

## Observable done criteria

1. Every stage runs through `scripts/evalgen.py`, and each command's exit code is recorded.
2. Q3 returns a number: N_flip per model over 3 items × 5 replicates.
3. Q4 returns `N/N items returned exactly one value` — or names the items that did not.
4. Q5 returns wall-clock at concurrency 1, 4 and 8, with any non-200 counted.
5. A quality reading exists for both models on `retention_v1`, published **beside** the
   sample size, never alone.
6. Total wall-clock under 30 minutes.
7. `RECONCILED: NO` unchanged. No code path prints otherwise.

## What this cannot buy

It cannot compare these models to the hosted arms. Experiments 5/7 ran at `top_p = 0` on
different providers at different quantisation; this runs at `top_p = 1.0` on fp8 internal
hardware. Those columns are **not** comparable, and the shakedown exists partly to make that
concrete rather than to paper over it. It cannot say anything about `qwen3-asr-1.7b`. And 22
scored rows cannot decide a migration — `retention_v1`'s own README records six reason
classes sitting at support 1, where a single miss swings a class from 1.00 to 0.00.

---

# As executed — 2026-08-14

Driven by `scripts/gpu_shakedown.py`. Seven commands, 7.5 minutes, zero refusals. Every
measurement came from `scripts/evalgen.py`; nothing here re-implements a call loop or a
metric.

## Q1 — verified TLS: **yes, and the first answer was wrong**

The endpoint's certificate is **self-signed**, which is why the public trust store rejects
it. The initial probes concluded the problem was a name mismatch from connecting by IP and
reached for `verify=False`. Both halves were wrong.

The certificate's SAN covers `token-fac-api.truecorp.co.th` **and** `10.94.154.102`, so
pinning it at `configs/token-factory.crt.pem` and pointing `SSL_CERT_FILE` at it gives
verified TLS with the stock client — no Host override, no custom transport. Verification is
now **stronger** than the public-CA default, because exactly one certificate is trusted.
Proved by connecting with an unrelated CA bundle and confirming it is refused.

## Q2 — the real request shape: **accepted**

`evalgen stability` and `evalgen baseline` both ran unmodified, which means the real prompt,
a real Thai transcript and the full 531-line retention schema as `response_format` are all
accepted. `seed` is accepted too. Every call returned `ok`; truncated rate 0.000.

## Q3 — determinism at `temperature = 0`: **intermittently not, and this is the finding**

| model | runs | N_flip |
|---|---|---|
| `qwen3.6-27b-fp8` | 1 × 5 replicates | 0 |
| `gemma-4-12b-it` | 6 runs (one × 3 reps, five × 5 reps) | **3 on one run, 0 on the other five** |

Gemma returned different answers to byte-identical requests **once in six runs**, then not
again in five consecutive attempts. Recorded as observed, not diagnosed.

**Why this matters more than its size suggests.** Stability is the binding gate here — Qwen
27B failed Experiment 7 on stability alone. A gate that occasionally counts serving
nondeterminism as model instability does not measure what it claims to. One flip in six runs
is easy to never notice and quite sufficient to move a verdict.

The candidate explanation is vLLM continuous batching: batch composition changes the
floating-point reduction order, so identical requests can decode differently. That is
consistent with it being rare on an idle box and would predict it worsening under load — but
it is a hypothesis, and the flip did not reproduce, so it is written down as one.

## Q4 — one arm is one backend: **yes**

`prompt_tokens fingerprint 3/3` on both stability runs and `20/20` on both baselines. Every
item returned exactly one token count, so one tokenizer answered, so one build did. This
endpoint reports no `provider` field at all, which makes the token fingerprint the only
identity signal available — and the stronger one, since a router cannot echo it.

## Q5 — concurrency: **4 is the ceiling; 8 buys nothing**

| concurrency | calls | wall clock | calls/s | per call |
|---:|---:|---:|---:|---:|
| 1 | 6 | 46.84 s | 0.128 | 7.81 s |
| 4 | 15 | 62.01 s | **0.242** | 4.13 s |
| 8 | 6 | 25.89 s | 0.232 | 4.32 s |

Concurrency 4 is **1.89×** concurrency 1. Concurrency 8 is **0.96×** concurrency 4 — no
gain, and per-request latency is slightly worse. Sizing the full pack from the c4 rate:
`retention_v3` at 138 items × 3 replicates = 414 calls is roughly **29 minutes for Gemma**
and **~57 minutes for Qwen**, per arm.

## A first quality reading — 22 scored rows, replicate 1, and not a verdict

| dimension | `gemma-4-12b-it` | `qwen3.6-27b-fp8` |
|---|---:|---:|
| call_result | 0.910 | **0.976** |
| reason | 0.783 | **0.830** |
| product | 0.933 | 0.933 |

**Read this only as a smoke test.** Twenty-two rows cannot decide anything, and
`retention_v1`'s own README records six reason classes sitting at support 1, where a single
miss swings a class from 1.00 to 0.00.

It is still worth flagging, because it points the opposite way to Experiments 10–14, where
Gemma 4 12B beat Qwen on `reason` (0.815 against 0.774). Here Qwen leads on both dimensions
that separate them. Those runs are **not comparable** — different providers, different
quantisation, `top_p = 0` versus `1.0`, 150 rows versus 22 — which is precisely why the full
run is needed rather than an argument about which number to believe.

## What has to happen before the full comparison

1. **Settle `top_p`.** This run deviated to `1.0` and said so. The honest reading is that
   `top_p = 0` is a mis-specification on our side: it selects the token set whose cumulative
   probability reaches zero, which is empty, and `temperature = 0` already expresses greedy
   decoding. Earlier providers accepted `0` and nothing records what they did with it, so
   the decoding the committed plans *pin* may not be the decoding earlier runs *used*.
2. **Explain or bound the Gemma flip**, ideally by repeating the determinism probe under
   deliberate load. If it is batching, it will get worse exactly when the full run is
   running.
3. **Decide what the fp8 internal arms are comparable to.** On the evidence here, not to
   Experiments 5, 7 or 10–14 directly.

`qwen3-asr-1.7b` remains **untested** — a speech model with no audio fixtures in this
repository. `RECONCILED: NO` is unchanged.
