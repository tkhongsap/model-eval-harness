---
type: report
created: 2026-08-11
status: screening evidence, RECONCILED NO
tags: [work/true, project/intelligence-layer, evaluation, frontier-open]
---

# Gemini 2.5 Flash against the frontier open models

Every arm this project has measured on `retention_v3`, in one place. Written after an
eight-agent adversarial pass that flagged four claims in the first draft, three of them
genuinely wrong; a later pass withdrew the fourth and found further errors in the
corrections themselves. All of it is recorded in `EXPERIMENTS.md`.

**Answer first.** Two open models now match or beat Gemini 2.5 Flash on quality: **GLM 5.2
is AHEAD on `reason`** under a matched non-reasoning regime, and **Kimi K3 is
indistinguishable** on the one dimension with power. Neither is deployable on the evidence
here: GLM failed the 99% reliability rule and is the slowest arm measured at every
percentile, Kimi costs **22.7x** Gemini. Gemini remains the only arm that is perfectly
stable, fastest and cheapest. Synthetic data; `RECONCILED: NO`.

## Quality

Weighted F1, replicate 1, `v9_16_base`, 138 items x 3 replicates. Verdicts are paired,
per call cluster, at alpha = 1/64.

| | Gemini 2.5 Flash | Gemma 4 12B | Kimi K3 | GLM 5.2 | Qwen3.6 27B | Qwen3.6 35B-A3B |
|---|---:|---:|---:|---:|---:|---:|
| call_result F1 | 0.955 | 0.928 | **0.972** | 0.966 | 0.969 | 0.901 |
| **reason F1** | 0.823 | 0.815 | 0.806 | **0.863** | 0.774 | 0.701 |
| product F1 | **0.960** | 0.946 | 0.943 | 0.950 | 0.942 | 0.888 |
| `reason` verdict vs Gemini | reference | INDIST. | INDIST. | **AHEAD** | AHEAD* | AHEAD* |

\* Qwen's AHEADs are from Experiment 5A and were **bought inside the reasoning-regime
confound** -- 2.4-2.6M reasoning tokens against Gemini's zero. Under the production-shaped
regime (Experiment 7) both Qwen arms `FAIL`. Gemini, Gemma, Kimi and GLM columns are all
zero-reasoning; the Gemma column is the one arm whose `reasoning_effort` was
`provider-default` rather than `none`, which is a recorded imperfection.

**GLM 5.2 is the first open model to clear the AHEAD band on `reason` against Gemini under
a matched zero-reasoning regime.** +14 of 32 discordant clusters against a +/-14 band --
**exactly at the boundary**, one pair from INDISTINGUISHABLE. Both facts belong in any
quotation of it.

**Kimi K3's parity is narrower than it looks.** `reason` is a genuine no-difference result
(d=37 against +/-15). `call_result` had d=7 against a band of 7, so only a clean sweep could
have returned any verdict, and `product` lost all 3 clusters it had. Indistinguishable on
the one dimension with power; the other two say nothing.

## Stability, latency, cost

| | Gemini | Gemma 4 12B | Kimi K3 | GLM 5.2 | Qwen 27B | Qwen 35B-A3B |
|---|---:|---:|---:|---:|---:|---:|
| completed calls | **414/414** | 414/414 | 414/414 | 408/414 | 414/414 | 414/414 |
| calls retried | **0** | 0 | 0 | **33 (8.0%)** | -- | -- |
| calls that varied | **0/138** | 79/138 | 138/138 | 137/138 | 129/138 | 130/138 |
| ...changing a scored label | **0** | **8** | 25 | 27 | 31 | 44 |
| p50 latency | **1.99 s** | 9.62 s | 16.77 s | 25.77 s | 6.95 s | 2.78 s |
| p95 latency | **3.76 s** | 12.95 s | 46.31 s | **108.86 s** | 20.50 s | 9.00 s |
| max latency | **4.77 s** | 22.55 s | 71.02 s | **186.11 s** | 55.98 s | 20.65 s |
| cost, 414 calls | $0.517 | not reported | **$11.750** | ~$1.04 | $0.362 | $0.211 |
| input tokens vs Gemini | 1.00x | 1.01x | **2.59x** | 1.84x | 1.33x | 1.33x |

**Regime, because the first version of this table got it wrong.** Every column is
**zero-reasoning**; the two Qwen columns are Experiment 7 throughout (Chutes and AkashML),
which is what the F1, stability, cost and token rows here have always been.

*Corrected 2026-08-11.* **Five of the six Qwen latency cells** previously carried Experiment
**5A** figures -- the reasoning-on arms on CoreWeave and AkashML, 2.4-2.6M reasoning tokens
and $6.55/$1.96 -- while every other row in those same columns was Experiment 7. The sixth,
Qwen35B-A3B's p95, was already E7's `9.00 s`, which is how it came to sit *beneath* an E5A
`p50 28.75 s` -- a p95 below its own median, and the tell that the row was mixing two runs.
Both Qwen columns are now E7 end to end, so the table compares one regime against itself.

**Convention.** Medians are `statistics.median` (`src/evalgen/cli.py:1286`, what the compare
reports print); p95 and max are `operational_summary`'s `_percentile` and `max`
(`src/evalgen/experiments.py:843`). The two Qwen columns come from
`experiments/evidence/retention-e7/summary.json`, whose p50 is `_percentile` rather than
`statistics.median` -- a difference of at most 0.05 s on these runs, which changes no
ordering in this table. That file records three decimals; two of them round ambiguously at
two, and are quoted here half-up: Qwen35B-A3B's **8.995 -> 9.00** and **20.645 -> 20.65**.

**Gemini is untouched on operations.** Zero variance, zero retries, fastest on every
percentile, and cheaper than everything except the two Qwen arms it beats on quality.

**Gemma 4 12B is the steadiest open model by a wide margin** -- 8 scored-unstable calls
against 25, 27, 31 and 44 -- from the smallest model in the table.

**GLM 5.2's reliability is worse than the headline.** 6 calls failed outright, but 33 were
retried across 51 failed HTTP attempts. The 6 terminal failures cluster on four adjacent
items (RET-116 to RET-119) and each carries `http_status = 429`, `no_asap_capacity`; the 33
retried calls do **not** cluster -- they span 22 distinct items across the pack.
*(Corrected 2026-08-11 on two counts: the earlier text applied "clustered on four adjacent
items" to all 51 attempts, which is impossible at three replicates an item; and it called
all 51 confirmed 429s, when only the 6 terminal rows record a status. A retried-then-
successful call overwrites the failed attempt's fields, so the other 45 are a reasonable
inference, not a logged fact.)* **Measured under load I imposed**: Experiments 15 and 16 ran
concurrently through one OpenRouter account at concurrency 8 each, E15 entirely inside
E16's window. The 429 burst and the 108 s p95 are not separable from that contention. A
re-run at concurrency 1 on the same pin is the cheap discriminating test.

**Tokenizers are a real cost term.** Kimi K3 needs **2.59x** the input tokens Gemini needs
for identical Thai text, GLM **1.84x**. That is most of Kimi's 22.7x bill and it is
invisible in any published price-per-token comparison.

## What the prompt work found, and it held twice

Two independent tuning experiments, on two models, two dimensions, two failure modes:

- **Experiment 9** constrained Qwen's unscored free text. The model obeyed exactly -- mean
  length 217 -> 61 characters -- and instability moved **zero**. Only removing the degree
  of freedom worked.
- **Experiments 11-13** attacked Gemma's over-labelling. Removing the worked example's
  filled reason slots cut errors **37.5%** and **generalised to the holdout** (`reason`
  AHEAD, +14 of 18). Two further iterations *told* the model the rule and moved it **zero**.

> **A prompt edit works by changing what the model is SHOWN. Telling it what to do does not
> move a measured number.**

And on the holdout, the edit that worked **cost two other dimensions** (`call_result` and
`product` both BEHIND). Anyone quoting the gain must quote the price.

## Where this leaves the decision

| model | quality | blocker |
|---|---|---|
| **Gemini 2.5 Flash** | reference | none measured |
| **Gemma 4 12B** | indistinguishable on the holdout | latency 5-10x, no cost data |
| **GLM 5.2** | **AHEAD on `reason`** | **98.55% reliability, below the 99% rule**; slowest at every percentile |
| **Kimi K3** | indistinguishable where powered | **22.7x cost**, 8x latency |
| Qwen3.6 27B / 35B-A3B | `FAIL` (Experiment 7) | stability; quality for the 35B |

**Nothing here changes the standing recommendation.** `docs/migration-decision-2026-08-07.md`
is about Qwen and stands. GLM 5.2 is the first candidate whose *quality* argues for a
second look, and the honest next step is a re-run at concurrency 1 on the same pin to find
out whether its reliability failure was the provider, the contention, or me.

## Limits, stated rather than implied

- **Synthetic Thai** authored inside this project, every item shipping a written
  description of the wrong answer a model gives. All numbers are upper bounds.
- **One provider pin, one night, one run each.** These describe `kimi-k3 @ DeepInfra bf16`
  and `glm-5.2 @ Sail Research fp8`, not the models in the abstract. **Kimi-versus-GLM is
  as much a bf16-versus-fp8 comparison as a model comparison.**
- **Latency and reliability were measured under self-imposed concurrency** and are the
  numbers least safe to quote.
- **The workload contract does not cover decoding.** All four new arms share
  `workload_sha 6b1ab3ed...`, but `e10-gemma-base` ran `reasoning_effort=provider-default`
  where the others ran `none`.
- **Qwen columns come from a different decoding regime** (`top_p = 0`). The Gemini control
  returned identical F1 to three decimals under both, which is evidence the regime is not
  what separates the columns -- not proof, since a non-deterministic model could be
  `top_p`-sensitive where Gemini is not.
- **`RECONCILED: NO`.** No production data has ever been scored. `docs/ask1-email-draft.md`
  remains the cheapest thing that changes that.
