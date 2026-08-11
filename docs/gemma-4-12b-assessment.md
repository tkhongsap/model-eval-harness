---
type: report
created: 2026-08-11
status: screening evidence, RECONCILED NO
tags: [work/true, project/intelligence-layer, evaluation, gemma]
---

# Gemma 4 12B against the field: what Experiments 10-14 found

**Answer first.** On held-out items Gemma 4 12B is **statistically indistinguishable from
Gemini 2.5 Flash on all three scored dimensions**, and it is by a wide margin the steadiest
and the best-scoring open model this project has tested. It is also **5-10x slower** on the
hardware available, and its endpoint reports no cost, so there is no cost case to make yet.
This is screening evidence on synthetic data. `RECONCILED: NO`.

## The cross-model table

Weighted F1, replicate 1, `v9_16_base` on every arm.

| | Gemini 2.5 Flash | **Gemma 4 12B** | Qwen3.6 27B | Qwen3.6 35B-A3B |
|---|---:|---:|---:|---:|
| call_result F1 | 0.955 | 0.928 | **0.969** | 0.901 |
| **reason F1** | **0.823** | **0.815** | 0.774 | 0.701 |
| product F1 | **0.960** | 0.946 | 0.942 | 0.888 |
| parse-valid | 414/414 | 414/414 | 414/414 | 414/414 |
| **unstable calls** | **0/138** | **79/138** | 129/138 | 130/138 |
| of which scored | 0 | **8** | 31 | 44 |
| p50 latency | **1.99 s** | 9.62 s | 6.95 s | 2.78 s |
| p95 latency | **3.76 s** | 12.95 s | 20.50 s | 9.00 s |
| decision vs Gemini | reference | see below | `FAIL` stability | `FAIL` quality + stability |

Gemini and Gemma are Experiment 10 (`top_p = 1.0`); both Qwen columns are Experiment 7
(`top_p = 0`, different providers). All four columns are zero-reasoning. Medians are
`statistics.median` (`src/evalgen/cli.py:1286`, what the compare reports print) and p95 is
`operational_summary`'s `_percentile`; the Qwen columns come from
`experiments/evidence/retention-e7/summary.json`, whose p50 is `_percentile` instead -- at
most 0.05 s apart on these runs and never enough to reorder a column.

**Why that mixed-regime table is nevertheless readable.** Gemini was re-run from scratch
under the new regime and produced **0.955 / 0.823 / 0.960 -- identical to three decimals to
its Experiment 7 figures**, with `N_flip = 0` in both. The reference point did not move
when `top_p` did, which is the strongest available evidence that the regime change is not
what separates these columns. It is not proof: a model that is *not* deterministic could
still be `top_p`-sensitive where Gemini is not, so the Qwen columns carry more uncertainty
than the Gemma one.

## Against the open field, Gemma 4 12B wins on the dimension that matters

- **Best `reason` score of any open model tested: 0.815**, against 0.774 and 0.701. This is
  the dimension every experiment in this project has turned on, and the only one where the
  incumbent has ever looked genuinely hard to beat.
- **Best product score**, and second-best `call_result` (Qwen 27B's 0.969 is the single
  best number any open model has posted here).
- **Far steadier than either Qwen.** 79 of 138 calls vary, against 129 and 130 -- and only
  **8** of those touch a label the scorer reads, against 31 and 44. Stability is the gate
  Qwen 27B failed outright; Gemma is not close to failing it in the same way.
- It does all of this at roughly **a fifth to a third the parameter count**.

## The holdout, which is the number to quote

89 locked items, drawn and committed before any prompt was written.

| dimension | Gemma vs Gemini | discordant |
|---|---|---|
| call_result | UNDERPOWERED | -2 of 4 |
| **reason** | **INDISTINGUISHABLE** | **+0 of 24** |
| product | UNDERPOWERED | -2 of 2 |

Dead level on the hard dimension. Experiment 10's full-pack `BEHIND` on `call_result` and
`product` did **not** reproduce -- both sat at the minimum discordance the band can resolve,
and 89 items cannot resolve a gap that size. That is a limit of the measurement, not
evidence of parity, and the two readings are reported together rather than the flattering
one alone.

## Can a prompt raise the score? Yes, once, and it costs you elsewhere

Three iterations on a 49-item tune slice, target chosen by measurement: `evalgen.severity`
put **47 of Gemma's 70 `reason` errors (67.1%) in over-labelling**, precision 0.753 against
recall 0.928.

| iteration | change | reason errors | over-labelling |
|---|---|---:|---:|
| control | `v9_16_base` | 32 | 20 |
| **1** | `v9_16_e1` -- blank the example's filled reason slots | **20** | **13** |
| 2 | + an explicit "only a distinct, client-stated reason" rule | 20 | 14 |
| 3 | + "delete any reason you cannot quote" self-check | 20 | 13 |

**The whole gain came from iteration 1.** On the holdout it **generalised**: `reason`
came out **AHEAD, +14 of 18 discordant against a +/-10 band** -- a real result at alpha =
1/64 on data the edit was never selected against, and ~~only the second~~ **the third**
AHEAD this project has recorded. *(Corrected 2026-08-11 by an adversarial verification
pass: Experiment 5A recorded **two** AHEADs on `reason`, both by Qwen arms and both bought
inside the reasoning-regime confound -- `EXPERIMENTS.md:864-873`. This one is the third,
and GLM 5.2's in Experiment 16 is the fourth.)*

**And it broke the other two dimensions**: `call_result` BEHIND (-7/7), `product` BEHIND
(-8/8). Quoting the 37.5% error reduction without those two would be a false report of a
true number.

## What this rhymes with

Experiment 9 tried to make Qwen's unscored free text deterministic. Iteration 1 there
constrained the text, was obeyed exactly -- mean length 217 -> 61 characters -- and moved
instability by **zero**. Only removing the degree of freedom worked.

Experiments 11-13 tried to make Gemma stop over-labelling. Iteration 1 removed the filled
slots from the worked example and cut errors 37.5%. Two further iterations *told* the model
the rule, in two different ways, and moved the count by **zero**.

Two experiments, two models, two dimensions, two failure modes, one finding:

> **A prompt edit works by changing what the model is SHOWN. Telling it what to do does
> not move a measured number.**

That is the most portable thing this project has learned about prompts, and it was only
visible because both experiments kept a control arm and pre-registered what would count.

## What would have to be true before this is a migration candidate

1. **Latency.** 9.62 s p50 single-arm, 19.56 s with two arms in flight, against Gemini's
   1.99 s. Production throughput is roughly 83,000 files a month. This is one small shared
   box, so the number is about the deployment and not the model -- but it is the number a
   deployment has today.
2. **Cost.** The endpoint reports none. There is no cost comparison, only an absence.
3. **The trade the prompt forces.** `v9_16_e1` buys `reason` and sells `call_result` and
   `product`. Which of those the monthly report actually depends on is a question for the
   app owners, not for this repository.
4. **Real data.** Everything above is synthetic Thai authored inside this project, on a pack
   whose every item ships a written description of the wrong answer a model gives. The
   holdout narrows the contamination; the same author wrote both slices, so it does not
   remove it. `docs/ask1-email-draft.md` remains the cheapest thing that changes this.

## Recorded limits

`top_p = 0` is rejected by this endpoint and both committed plans pin it, so Experiments
10-14 are not comparable to Experiments 5 or 7 except through the Gemini control above.
Latency was measured under self-inflicted contention. No code path prints
`RECONCILED: YES`, and none of the above is a migration verdict.
