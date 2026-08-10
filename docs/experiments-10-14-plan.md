---
type: plan
created: 2026-08-10
status: goal contract, in execution
tags: [work/true, project/intelligence-layer, evaluation, gemma]
---

# Experiments 10-14: Gemini vs Gemma 4 12B, and whether a prompt can move the score

## Goal contract

### Outcome

A defensible answer to two questions:

1. **Where does Gemma 4 12B stand against Gemini 2.5 Flash** on the three scored
   dimensions, latency and stability, on identical inputs?
2. **Can a prompt raise Gemma's score**, and by how much *that survives a holdout*?

### Authoritative source

| Question | Authority |
|---|---|
| The scores | `evalharness.metrics` and `compare.paired_verdict` at alpha = 1/64, unchanged |
| Which items may be tuned on | `tests/fixtures/testsets/retention_v3.split.json` — 49 tune, 89 holdout, committed **before** any of this |
| Where to aim a prompt edit | `evalgen.severity` on the measured run, not intuition |
| What the endpoint can honour | `scripts/gpu_endpoint_probe.py`, run first |

### Scope boundary

**In scope:** two arms, one pack, five recorded experiments, prompt edits confined to
Gemma.

**Out of scope, deliberately:**

- **Changing Gemini's prompt.** It is the reference. Production runs `v9_16_base`.
- **Re-litigating the Qwen decision.** `docs/migration-decision-2026-08-07.md` stands.
  Gemma 4 12B is a new arm, not a re-run of that comparison.
- **Any claim from the tune slice.** Iterations 1-3 measure on 49 items that carry
  `expected_failure` strings naming the exact wrong answer. Those numbers direct the next
  edit and are never the result.
- **`RECONCILED`.** Synthetic pack. Unchanged.

### The decoding change, stated once and carried everywhere

The Gemma endpoint **rejects `top_p = 0`** (`top_p must be in (0, 1]`), which both
committed plans pin. So **every arm in Experiments 10-14 runs at `top_p = 1.0`**,
including Gemini, which is re-run rather than reused. Consequences, recorded rather than
discovered later:

- These runs are **not comparable to Experiments 5 or 7**, which pinned `top_p = 0`.
  Cross-experiment F1 differences confound the model with the decoding.
- Gemini's numbers here are a **fresh reference measured under this regime**, not the
  E7 figures reused.

### Observable done criteria

1. Both arms of E10 complete on the same 138 items with the same prompt sha and the same
   decoding, and `compare` accepts them without `--prompts-may-differ`.
2. Every prompt edit is a registered `Variant` with a `why` on each edit, catalogued, and
   `validate_manifest() == []`.
3. Tuning touches the 49 tune items only. The holdout is evaluated in E14.
4. Both suite modes green at every commit.
5. Each experiment is recorded in `EXPERIMENTS.md`, committed, PR'd and merged before the
   next begins.
6. The final summary distinguishes **tune-slice** numbers from **holdout** numbers
   everywhere, and quotes the holdout number as the result.

---

## The five experiments

| # | What | Items | Calls |
|---|---|---|---|
| **E10** | Head-to-head baseline. Both arms, `v9_16_base`, identical decoding. | 138 x 3 x 2 | 828 |
| **E11** | Gemma prompt iteration 1, aimed at the failure mode E10 measured | 49 x 3 | 147 |
| **E12** | Iteration 2, directed by E11 | 49 x 3 | 147 |
| **E13** | Iteration 3, directed by E12 | 49 x 3 | 147 |
| **E14** | Winning prompt vs base vs Gemini on the **locked holdout** | 89 x 3 x 3 | 801 |

Roughly 2,070 calls. Gemma is an internal endpoint reporting no cost; Gemini is metered
and is the only real spend, roughly US$1.20 at Experiment 7's observed rate.

## How the prompt edits are chosen

Not by guessing. `evalgen.severity` classifies every wrong answer, so E10 says whether
Gemma's errors are over-labelling, under-labelling, substitution, fabrication or row
misalignment. Each iteration targets the largest measured category, and the edit's `why`
cites the number it was aimed at. That is the same method Experiment 9 used, and the
reason it produced an interpretable negative result rather than a shrug.

**The constraint from Experiment 9 carries over:** an edit may not add, remove or reword
a class definition, a rule or a worked-example label in a way that teaches the pack's
answers. What it may do is change how the task is framed, what the model is told to
prioritise, and how it is told to decide between competing classes.

## What this cannot buy

The pack is synthetic Thai authored inside this project, and every item ships a written
description of the wrong answer a model gives. A prompt tuned here is tuned to this pack.
The holdout narrows that, it does not remove it: the same author wrote both slices, and
`EXPERIMENTS.md:401` already records that phase-one items were used to select earlier
edits. Every number from E14 is an **upper bound**, and the summary says so.
