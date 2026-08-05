# Experiments

Running log of the Qwen-vs-Gemini migration evaluation. **One section per experiment,
appended, never rewritten.** The point is to see across experiments what improved, what
did not, and what a change actually bought.

Every experiment records four things: what was run, what came out, what to do next, and
**where the output files are**. A result with no output file is not a result.

**Standing caveats that apply to every experiment below**, so they are not repeated each time:

- `RECONCILED: NO` — no number here has been checked against the Retention app's own live
  Gemini fact-check report. Nothing in this repository can perform that check.
- `PROMPT: RECONSTRUCTED` — the prompt is reassembled from committed assets, not read from
  the running system. Production fetches it from SharePoint at run time.
- **Production sends AUDIO. This sends pre-tagged Thai TEXT.** Agent-speech misattribution,
  ASR error and diarisation are invisible here.
- The Thai was **drafted by an LLM** and has no native-speaker sign-off.
- 22 scored rows: **one row is 4.5 points.** Verdict bands were fixed before any run:
  net `<= -2` BEHIND, `-1..+5` INDISTINGUISHABLE, `>= +6` AHEAD.

---

## Experiment 1 — Baseline, and two defects that made the first answer wrong

**Date:** 2026-08-04 to 2026-08-05
**Question:** Does `qwen/qwen3.6-27b` match `google/gemini-2.5-flash` on True's Retention
labelling task, using production's own prompt?

### What was run

| # | Run | Model | Provider | Prompt | Result |
|---|---|---|---|---|---|
| 1.0 | first baseline | gemini-2.5-flash | *unpinned* | `v9_16_base` | 60/60 ok |
| 1.1 | first baseline | qwen3.6-27b | *unpinned* | `v9_16_base` | 50/60 ok, **INVALID** |
| 1.2 | pin probe | qwen3.6-27b | Alibaba | `v9_16_base` | **20/20 schema_violation** |
| 1.3 | re-baseline | gemini-2.5-flash | Google | `v9_16_base` | 60/60 ok |
| 1.4 | re-baseline | qwen3.6-27b | Morph | `v9_16_base` | 60/60 ok |
| 1.5 | example fixed | gemini-2.5-flash | Google | `v9_16_e1` | 60/60 ok |
| 1.6 | example fixed | qwen3.6-27b | Morph | `v9_16_e1` | 60/60 ok |
| 1.7 | consistency repeat | both | pinned | both | 4 runs, 60/60 each |

20 items x 3 replicates per run. Decoding `temperature=0.0, top_p=0, seed=0, max_tokens=8000`,
identical on both arms. Both arms constrained by the same JSON schema.

### Result

**The first answer was wrong, and the reason was not the model.**

**Defect 1 — the candidate arm was two backends.** Run 1.1 was served by two different builds
under one model id. 14 of 20 items returned **two distinct `prompt_tokens` values for a
byte-identical request**; the incumbent returned 0 of 20. Splitting on reasoning tokens:
regime A (no reasoning, ~2,590 prompt tokens, 5.8s) carried **10 of 31 schema violations**;
regime B (reasoning, ~3,690 prompt tokens, 71.7s) carried 0 of 29. `observed_models` reported
`60 x qwen/qwen3.6-27b` and missed it entirely, because the guard watched `response.model`
and the model id was never what changed.

So the headline of run 1.1 — "Qwen `N_flip` 18 against Gemini 0" — **was not measuring Qwen.**
It was measuring a 52/48 blend of two systems against one. Of those 18 flips, 12 traced to
violation replicates, 4 to the two backends disagreeing, and only **2** to genuine
same-backend nondeterminism.

**Defect 2 — the prompt poisoned its own output.** The worked example fills all three reason
slots with `network` / `save cost` / `dissatisfied service`. Of labels invented into slots the
ground truth leaves blank, **81% of Qwen's and 42% of Gemini's were example values**. False
positives outnumbered false negatives 4:1 and 2:1. On 5 of the 9 items that defeated both
models, both emitted every *correct* label and failed only by adding copied ones. True had
already made exactly this fix for the `keyword` field at prompt v9_3 and never did it for
`reason`.

**After both fixes:**

| dimension | | Gemini F1 | Qwen F1 | net |
|---|---|---|---|---|
| call_result | base | 0.910 | **0.976** | +1 |
| call_result | e1 | 0.910 | **0.976** | +1 |
| reason | base | 0.777 | **0.799** | +3 |
| reason | **e1** | 0.797 | **0.840** | **+5** |
| product | both | 0.933 | 0.933 | 0 |

- **`N_flip` went 18 -> 0.** The instability was entirely the routing, not the model.
- **Schema violations went 10 -> 0.**
- **Fabrication dropped on both arms**: Gemini 42% -> 28%, Qwen 81% -> 62%; Qwen's invented
  label count fell 48 -> 27. **The example fix helped the incumbent too**, which is the
  honest kind of improvement.
- Cost and latency are near parity in production's regime: **Gemini $0.069 / 2.3s median,
  Qwen $0.078 / 4.2s median.** (The earlier "Qwen is 4.3x more expensive" was the reasoning
  backend, which production does not run.)

**Verdict against the pre-registered bands: INDISTINGUISHABLE on all three dimensions.**
Qwen **matches** Gemini. It does not beat it: `+5` sits at the top of the indistinguishable
band and `+6` was the threshold set before any data existed. Moving that line now would be
choosing the rule to fit the result.

**Two findings that are not about either model:**

- **Alibaba's constrained decoder is broken.** Pinned there, 20 of 20 returned a bare JSON
  *number literal* (`-1.1000000000000001e-05` followed by ~500 digits) where the schema root
  is `object`, `finish_reason: stop`, no truncation. The identical request returns a
  well-formed object from Morph, Chutes and CoreWeave. It is an endpoint defect.
- **Four items defeat both models on every run.** Neither model ever emits `product: unknown`
  or `call_result: undefined` in any run. That is a finding about the task, not the candidate.

**Consistency: Gemini reproduces exactly. Qwen does not, quite.**

| arm | labels agreeing | failing items |
|---|---|---|
| Gemini base | 66/66 | identical |
| Gemini e1 | 66/66 | identical |
| Qwen base | 66/66 | **DIFFER** (RET-17, RET-19) |
| Qwen e1 | **65/66** | **DIFFER** (RET-14) |

One headline number moved between passes: `reason` net on `e1` went **+5 -> +4**. Small, real,
and it means a single Qwen run should not be quoted without a repeat.

**A metric was built, measured, and retracted. The retraction is the finding.**

An "evidence fidelity" metric was drafted to score whether each `keyword` value appears
verbatim in the transcript, and it reported Qwen ahead. It never reached a table above; it
was withdrawn. It had been built from the run data it was judging, with no hand-computed
expectation written first, and it measured a **format** difference and reported it as a
fidelity one.

`keyword`'s schema description sits in `src/evalgen/schemas/retention.json`, ported from
production `main.py:977`, and `response_format` sends it to **both** arms on every call:

> "List keywords or short phrases directly from the audio that explicitly indicate or
> support the reason. **Use comma separation.** Use empty string if not applicable."

Gemini obeys the comma instruction. The retracted metric matched whole strings, so every
multi-segment cell Gemini produced was scored a miss. Comma-split first — the convention
`records.py:57-60` already applies to the sibling field `reason` — and the **same base-run
data** gives:

| arm, base run | whole-string verbatim | comma-split verbatim |
|---|---|---|
| Gemini | 81/120 = 67.5% | **189/189 = 100.0%** |
| Qwen | 126/132 = 95.5% | 126/132 = 95.5% (no commas to split) |

**The whole gap was the comma, and the direction was backwards.** Split on the separator the
schema asks for, the incumbent is at 100.0% and the candidate at 95.5%.

It could not have changed the ranking either way: `keyword` is not scored in production at
all. `decoding.py:130` and `flatten.py:243` both record that only `.reason` is read
(`fact_checker.py:607-617`).

**Diagnostic, not a scored dimension — Qwen never emits a comma.**

| run | non-empty `keyword` fields | with a comma |
|---|---|---|
| incumbent-base | 120 | 39 |
| incumbent-e1 | 123 | 45 |
| candidate-base | 132 | **0** |
| candidate-e1 | 108 | **0** |
| pin-proof-morph | 17 | **0** |

Across **257** non-empty `keyword` fields Qwen emits zero commas — it ignores an explicit
schema instruction 100% of the time, on a field both arms receive identically. Gemini uses
one on 84 of its 243. This enters no verdict, because production does not score `keyword`.
It is recorded because it is the one place in this experiment where the candidate is
measurably not doing what the schema says, and that is worth watching on a field that **is**
scored.

**How it was caught, and what changed.** Adversarial review, not the run. The metric was
built from the data it would judge and it confirmed the answer its author already held.
**A new metric now needs a hand-computed expectation written down before the code exists**,
the way `tests/fixtures/retention_expected.csv` was derived in `WORKED-COMPUTATION.md`.

### Recommended next steps

Ordered by expected value, with what each would falsify.

1. **Fix RET-11's ground truth.** Its GT omits `dissatisfied service`, which
   `prompt.py:4361` explicitly licenses ("the agent didn't follow up") and which **both**
   models found independently. One label, one line. Falsified if the cited rule does not
   support it on re-reading.
2. **Arbitrate the two unarbitrated class boundaries** (`other` as an unbounded catch-all;
   `4333` vs `4375` overlapping on discount requests). Both currently punish correct-looking
   answers on RET-02 and RET-12.
3. **Attack the four shared failures directly.** Neither model emits `product: unknown` or
   `call_result: undefined`. A single explicit instruction that these classes are real and
   expected is one edit and targets 3 of the 4. Falsified if either class still never appears.
4. ~~**Score evidence fidelity.**~~ **WITHDRAWN — the claim under it was false.** It read:
   *"Qwen's `keyword` is verbatim in the transcript; Gemini's is a comma-stitched fabrication
   that does not appear verbatim ... Adding it may change the ranking."* Both halves are
   wrong. Gemini's commas are what the schema instructs, and split on them Gemini is verbatim
   at **100.0%** against Qwen's **95.5%** — the opposite of the claim. Production does not
   score `keyword`, so no ranking was ever available to change. See *"A metric was built,
   measured, and retracted"* above. Nothing takes this slot.
5. **Raise replicates from 3 to 5 for Qwen only.** Gemini is deterministic; Qwen is not.
   Cheap, and it makes the net numbers stable enough to quote.

**Do not** tune further on these 22 rows without a holdout. Every edit so far has been
selected using them, so the figures are an upper bound.

### Output files

| What | Path |
|---|---|
| **Spreadsheet (pass 1)** | `out/reports/qwen-vs-gemini-2026-08-05.xlsx` |
| **Spreadsheet (pass 2)** | `out/reports/qwen-vs-gemini-2026-08-05-pass2.xlsx` |
| Consistency report | `out/reports/consistency-2026-08-05.txt` |
| Text comparisons | `out/reports/compare-{base,e1}-pass{1,2}.txt` |
| Raw run data | `out/runs/20260805-0056*/`, `out/runs/20260805-0058*/`, `out/runs/20260805-01*/` |
| Invalid first run, kept | `out/runs/20260804-224050Z-candidate/` |

**Spreadsheet sheets:**

| Sheet | Contents |
|---|---|
| **READ FIRST** | The caveats, incl. the alarm that the repeat pass did not fully reproduce |
| **Per item** | One row per (item, product): Thai transcript, ground truth, each model's answer under each prompt, mismatches shaded **and** text-labelled |
| **Per call** | **One row per API call, 240 rows**: `prompt_tokens`, `completion_tokens`, `reasoning_tokens`, `total_tokens`, `cost_usd`, `latency_s`, provider, finish_reason, outcome. Plus totals per arm |
| **Comparison** | Per dimension: both-right / both-wrong / incumbent-only / candidate-only / net, and weighted precision, recall, F1 |
| **Mechanisms** | PASS / FLAKY / FAIL per mechanism per arm per prompt |
| **Runs** | Per-run provenance: model, provider, prompt sha, decoding, outcomes, `N_flip`, pin proof |

`run.jsonl` in each run directory carries the same per-call detail **plus the model's raw
response text**, which the spreadsheet omits for size. That raw capture is what made the
Alibaba defect diagnosable; before it existed, all 10 violations were undiagnosable from disk.

**Cost of Experiment 1: about $0.60 across 13 runs.**

---

## Experiment 2 — *not started*

Use this template:

```
## Experiment N — <one-line title>

**Date:**
**Question:** <what this experiment decides, phrased so it can come out either way>

### What was run
<table: run, model, provider, prompt, result>
<decoding settings, replicate count, what was held constant>

### Result
<what happened, including what did NOT work>
<the verdict against the pre-registered bands>
<anything that turned out to be about the harness or the test set rather than a model>

### Recommended next steps
<ordered, each with what would falsify it>

### Output files
<table: what, path>
<spreadsheet sheet list if one was produced>
<cost>
```

**Before running Experiment 2**, state the prediction: which items should move, in which
direction, and which must not move. An edit that improves the aggregate by moving items you
did not predict is indistinguishable from luck at n=20.
