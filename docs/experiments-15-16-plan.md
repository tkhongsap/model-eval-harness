---
type: plan
created: 2026-08-11
status: goal contract, in execution
tags: [work/true, project/intelligence-layer, evaluation, frontier-open]
---

# Experiments 15-16: Gemini 2.5 Flash against the frontier open models

## Goal contract

### Outcome

Where Gemini 2.5 Flash stands against the two current frontier open-weight models on this
task -- **Kimi K3** and **GLM 5.2** -- on the three scored dimensions, latency, stability
and cost, measured on identical inputs at identical decoding.

### Authoritative source

| Question | Authority |
|---|---|
| The scores | `evalharness.metrics` and `compare.paired_verdict` at alpha = 1/64, unchanged |
| The model ids | OpenRouter's `/models`, queried; **not** assumed from the request |
| The provider pin | measured, not chosen. Probe first, pin what answered |
| The reference arm | Experiment 10's Gemini run, reused (see below) |

### Scope boundary

**In scope:** two new arms against one existing reference, same pack, same prompt, same
decoding. A recorded head-to-head for each, then one comparison across the whole field.

**Out of scope, deliberately:**

- **Prompt tuning for either model.** Experiments 11-13 already established what a prompt
  edit can and cannot do here; repeating it per model would spend a lot to re-learn it.
- **Re-running Gemini.** Experiment 10's arm is reused; see the comparability note.
- **A migration recommendation.** These are new arms on synthetic data.
  `docs/migration-decision-2026-08-07.md` is about Qwen and is untouched.

### Reusing Experiment 10's Gemini arm

Both new arms run at `temperature 0, top_p 1.0, seed 0`, `v9_16_base`, `retention_v3`,
3 replicates -- **the same workload contract Experiment 10's Gemini arm carries**. Model
and provider are arm identity and are deliberately not part of that contract, so `compare`
accepts the pairing without an override, and E10, E15 and E16 are mutually comparable.

This saves 828 Gemini calls and, more importantly, means every arm in E10-E16 is measured
against *one* reference rather than three separately-drawn ones.

### What was verified before spending

Queried rather than assumed, because a wrong model id produces a full run of numbers
describing a model nobody chose:

- `moonshotai/kimi-k3` exists, 1,048,576 context, $3.00/$15.00 per M tokens.
- `z-ai/glm-5.2` exists, 1,048,576 context, $0.76/$2.42 per M tokens.
  (A first query truncated its provider list and appeared to show no 5.2. It exists.)

Provider pins chosen by probe, three items each, and the losers are recorded because a pin
chosen without measurement is the Experiment 4 mistake:

| model | provider | quant | result |
|---|---|---|---|
| kimi-k3 | **DeepInfra** | bf16 | **3/3 ok**, 0 reasoning tokens, p50 12.5 s |
| kimi-k3 | Fireworks | unknown | 3/3 transport_error |
| glm-5.2 | **Sail Research** | fp8 | **3/3 ok**, 0 reasoning tokens, p50 29.3 s |
| glm-5.2 | Novita | fp8 | 3/3 transport_error |

Both pins honour `response_format` json_schema and return **zero reasoning tokens** under
`--reasoning-effort none`, which is production's `thinkingBudget: 0` regime.

### The spend, sized before it was committed

Measured from the probe, not estimated from a price list:

| arm | prompt tok/call | $/call | 414 calls |
|---|---:|---:|---:|
| Kimi K3 | 7,261 | $0.02612 | **$10.81** |
| GLM 5.2 | 4,968 | $0.00324 | **$1.34** |

**Kimi K3 needs 2.6x the input tokens Gemini needs for the same Thai text** (7,261 against
2,844). That is a tokenizer property, not a model-quality one, and it is most of why it
costs eight times more per call. It is recorded here because it is an operational fact a
deployment would carry, and because it is invisible in any published benchmark.

Total roughly **US$12**, against about $1-6 for each previous experiment. Larger, and
sized deliberately rather than discovered afterwards.

### Observable done criteria

1. Both arms complete on all 138 items x 3 replicates with the same prompt sha as the
   Gemini reference, and `compare` accepts each pairing **without** `--prompts-may-differ`.
2. Each experiment is recorded in `EXPERIMENTS.md`, committed, PR'd and merged separately.
3. Both suite modes green at each commit.
4. The findings are independently reviewed by an adversarial pass before the summary
   claims anything.
5. The summary reports F1, latency, stability and cost for every arm this project has
   measured, and states which comparisons are confounded and why.

## What this cannot buy

Synthetic Thai authored inside this project. One provider pin per model, on a router whose
serving builds change -- these numbers describe `kimi-k3 @ DeepInfra bf16` and
`glm-5.2 @ Sail Research fp8`, not the models in the abstract. Quantisation differs between
the two pins (bf16 against fp8), which is itself a confound the summary must name. And
`RECONCILED: NO`.
