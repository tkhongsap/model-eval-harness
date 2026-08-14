# Experiment 17 — Token Factory GPU arms against Gemini, on Experiment 7's pack

**Preregistered 2026-08-14, before the first paid call. Executed the same day.**

Experiment 7 asked whether a cheaper model could replace Gemini 2.5 Flash on True Retention,
and answered it across three OpenRouter arms. Its own plan named the sequel:

> `phase_three`: "When internal GPUs are ready, rerun this locked plan unchanged except for the
> provider/runtime identity recorded as a new arm."
> — `experiments/retention-e7.plan.json`

The internal GPUs are ready. This is that run, against True's Token Factory
(LiteLLM over vLLM, `10.94.154.102`, self-signed TLS pinned at
`configs/token-factory.crt.pem`). It is *not* "unchanged" — the endpoint rejects one pinned
decoding parameter — and the single forced deviation is declared below rather than absorbed.

## The question

Do the two chat models Token Factory serves — `gemma-4-12b-it` and `qwen3.6-27b-fp8`, both fp8 —
match Gemini 2.5 Flash on `retention_v3`, under the same prompt, the same replicate count and
the same scorer, with the difference tested rather than eyeballed?

## Why this plan is a document and not `experiments/retention-e17.plan.json`

`evalgen experiment-check` does not apply here, and forcing it to would mean weakening it.
`experiments.validate_plan` is a validator for **one specific contract**, not a generic schema
check: `experiments.py:117` allows only the ids `retention-e5` and `retention-e7`, and
`:190-207` hard-pin `max_attempts = 1`, `top_p = 0`, and an `arms` map that must equal exactly
the three OpenRouter model ids E7 ran.

E17 differs on three of those by design — it has internal-GPU arms, the endpoint forbids
`top_p = 0`, and it uses `max_attempts = 3`. Adding `retention-e17` to the allowlist would make
every remaining assertion fail; relaxing the assertions would dismantle the thing that pins E7's
approved sample size and retry policy. So the contract is recorded here in prose, in the form
`docs/gpu-shakedown-plan.md` already uses for work outside that contract, and
`experiments.py` is left alone. Noted as a real gap: there is no generic preregistration
validator, only an E5/E7-specific one.

## The three arms

All three: `retention_v3` (138 items, 150 scored rows), prompt `v9_16_base`
(`968a2974…`), 3 replicates, **414 calls per arm**, `temperature 0`, `seed 0`,
`max_tokens 8000`, `max_attempts 3`.

| arm | model | runtime | concurrency | `reasoning_effort` |
|---|---|---|---|---|
| `e17-gemini` | `google/gemini-2.5-flash`, provider pinned `Google` | OpenRouter | 8 | `none` |
| `e17-tf-gemma` | `gemma-4-12b-it` (fp8) | Token Factory | 4 | `provider-default` |
| `e17-tf-qwen` | `qwen3.6-27b-fp8` | Token Factory | 4 | `provider-default` |

**Gemini is re-run rather than reused.** `20260810-165653Z-e10-gemini-base` has the identical
workload, but `scoring_code_sha` moved from `cefd4ae9…` to `9b4afc95…` when `apps.py` joined the
digest on 2026-08-12, and that field is BLOCKING in `assert_comparable`
(`manifest.py:170`). The repo's own precedent is to make the runs comparable rather than argue
with the gate (`EXPERIMENTS.md:841-853`), and at 414 calls for ~$0.56 the argument is not worth
having. Every knob is matched to the E10 run, so the re-run doubles as an invariance control.

**`max_attempts = 3` on all three arms, deliberately.** Enterprise experiments normally pin 1 so
provider reliability is measured directly. Doing that on the GPU arms alone, while Gemini kept
3, would let one transient gateway blip depress GPU F1 against a Gemini that got retries.
Reliability is read off `outcome_counts` and `attempt_count`, where it stays visible and does
not contaminate the quality comparison.

## The scorer control, run before the arms

Because the digest moved, "did the scorer change?" and "did the model change?" would otherwise
be confounded. They were separated first, for free, by re-scoring the **2026-08-10** pair with
**today's** code — legal because both those runs carry `cefd4ae9…`, so the gate passes:

| dimension | printed 2026-08-10 under `cefd4ae9…` | same outputs re-scored under `9b4afc95…` |
|---|---|---|
| `call_result` | 0.955 | **0.955** |
| `reason` | 0.823 | **0.823** |
| `product` | 0.960 | **0.960** |

The digest moved and the numbers did not. This also demonstrates something worth writing down:
**`run.json` stores no metrics at all.** Every F1 this repo prints is computed at compare time
from the run's raw rows plus the ground-truth CSV (`cli.arm_summary:1372`, called at
`cli.py:3439`). The `scorer_sha` gate is therefore a *provenance* discipline — these arms were
produced in the same era — not a numerical one. That is an argument for keeping the gate, not
for waiving it: it is the only thing standing between a report and two arms whose raw outputs
were produced under different generation contracts.

## Pre-flight, executed

- **Digest frozen.** `scoring_code_sha = 9b4afc95c3d698761cebcfd19e7c9c04fa3e5a850a015d291fc21d8e5e900db3`
  at `da1f6ca`, clean tree. No commit may touch `src/evalharness/**` or
  `src/evalgen/{apps,cli,flatten,report}.py` until the last `compare`. Moving it mid-experiment
  is what invalidated Experiment 5.1.
- **Default-path footgun avoided.** `DEFAULT_TESTSET`/`DEFAULT_GT` (`cli.py:172-173`) point at
  **`retention_v1`**. Every command passes `--testset`/`--gt` explicitly; omitting them yields a
  silent 20-item run that cannot compare to anything here.
- **Long-context smoke, both GPU models.** `retention_v3`'s longest item, RET-110, is 18,112
  chars — **4.3× the longest thing the shakedown ever sent** (`retention_v1` max: 4,184) — and
  with the 531-line response schema at `max_tokens 8000` the default 120 s timeout was the live
  risk. Measured on RET-110/112/102 at one replicate: `gemma-4-12b-it` 8.1 / 11.2 / 13.9 s,
  `qwen3.6-27b-fp8` 12.1 / 15.2 / 23.0 s. All `ok`, truncated rate 0.000. No timeout change
  needed.

## Declared deviations

1. **`top_p = 1.0`, not production's `0`.** The endpoint returns
   `400 top_p must be in (0, 1]`, raised by vLLM beneath the gateway, even though the published
   `openapi.yaml` declares `minimum: 0`. At `temperature 0` the decode is greedy, so `top_p` is
   inert. Already declared for the shakedown. Invisible to `workload_sha` (seven keys,
   `cli.py:1823-1834`; decoding is deliberately not among them) and printed as a recorded,
   non-blocking arm difference. It is also **the same value E10's Gemini used**, so this
   introduces no asymmetry between the arms here.
2. **`reasoning_effort`:** `none` on Gemini (matching E10), `provider-default` on the Token
   Factory arms, where the field is not meaningful to the gateway. Recorded, non-blocking.
3. **fp8 quantisation, and four distinct deployments.** `qwen3.6-27b-fp8` here is **not** E7's
   `qwen/qwen3.6-27b` on Chutes, and `gemma-4-12b-it` here is **not** E10's `gemma-4-12b` on
   Modellismz. E7's and E10's numbers for those names do not transfer to these arms.

None of these joins the closed deviation list. That list is about deviations from *production
semantics*; these are runtime facts about an endpoint.

## How to read the result

Per `docs/experiment7-results.md`, in this order: `N_flip` and the mechanism verdicts first,
then F1, then the paired verdict at alpha 1/64 per side. UNDERPOWERED means *not measured* and
is never a tie.

The order is not a formality. **E7's headline finding was that F1 alone would have selected
Qwen, and the stability gate is what disqualified it.** The shakedown then caught
`gemma-4-12b-it` flipping on byte-identical requests once in six determinism runs on this
endpoint (`docs/gpu-shakedown-plan.md:158-176`), unexplained, hypothesised vLLM continuous
batching. A GPU arm that wins on F1 and flips is not a candidate.

## What this cannot buy

It cannot say these models are good enough for production Retention. `retention_v3` is
synthetic and authored in-project, so every number here is an upper bound — Gemini's included.
It does not move `RECONCILED: NO`; only a reconciliation run against real labelled data does.

And it measures the models **as served by Token Factory today** — fp8, this gateway, this
batching. Because of the shakedown's unexplained flip, endpoint variance and model variance are
not yet separable on this endpoint. If a GPU arm flips here, that ambiguity is the finding, not
a verdict on the model.

## As executed

All three arms ran 2026-08-14, 414/414 `ok`, zero parse failures, zero truncation, zero resumed
cells. `scoring_code_sha` was `9b4afc95…` at the start and unchanged at the end. Both compares
exited 1 — the harness ran and found problems, which is a result — and **neither refused**.

| arm | run | wall | throughput | cost |
|---|---|---:|---:|---:|
| `e17-gemini` | `20260814-132425Z-e17-gemini` | 1.9 min @ c8 | 3.64 calls/s | $0.5616 |
| `e17-tf-gemma` | `20260814-132642Z-e17-tf-gemma` | 21.3 min @ c4 | 0.324 calls/s | — |
| `e17-tf-qwen` | `20260814-134803Z-e17-tf-qwen` | 20.5 min @ c4 | 0.336 calls/s | — |

Two things this plan did not anticipate:

- **The incumbent moved.** Gemini's determinism collapsed between 2026-08-10 and this run —
  raw-unstable 0/138 → 111/138 under a byte-identical workload. The plan treated the Gemini
  re-run as a routine invariance control and said the experiment would stop if it did not
  reproduce. Two of the three F1 figures reproduced *exactly*, so the trip-wire as written would
  not have fired; the stability columns are what caught it. Recorded as a lesson: an invariance
  control stated only in terms of F1 is too coarse.
- **Timing was pessimistic.** Estimated ~28 min per GPU arm, measured 21.3 and 20.5. The
  shakedown's 0.32 calls/s held on a pack whose mean item is 1.31× longer, which is itself
  evidence the gateway is the bottleneck rather than the models.

Full reading: [`experiment17-results.md`](./experiment17-results.md).
