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
- ~~22 scored rows: **one row is 4.5 points.** Verdict bands were fixed before any run:
  net `<= -2` BEHIND, `-1..+5` INDISTINGUISHABLE, `>= +6` AHEAD.~~
  **SUPERSEDED 2026-08-06 — re-derived immediately below.** Kept visible and struck
  through, not deleted, because Experiments 1-4 were each read against it. This file
  corrects in place; the precedent is the withdrawn next step 4 at the foot of
  Experiment 1 (:183-189).

### Verdict bands, re-derived from sample size and the null (2026-08-06)

This re-derivation was pre-registered as a hard prerequisite twice — in
`docs/testset-v2-plan.md` and again in `docs/eval-improvement-plan.md` — and skipped
both times. The pack has since gone 22 -> 108 scored rows and is heading to ~150, so
the bands have been carried across a 5x change in n without anyone checking what they
cost.

**What was allowed as input, and what was refused.** The derivation below uses the
*discordance rate* — how often the two arms disagree at all — and nothing else from the
runs. It does **not** use the observed nets from any experiment. The extraction that
produced the rate table sums `inc only + cand only` and discards the split before
printing, so the direction was not available to the person choosing the rule. This is
the same discipline :91-93 already applied in writing when it refused to move the AHEAD
line to admit a result; a rule chosen to fit the numbers it will judge is not a rule.

**The discordance rate, measured** from every committed comparison report in
`out/reports/compare-*.txt`. `compare-A-gemini-vs-27b.txt` is byte-identical to
`compare-v2-100items.txt` (same MD5) and was deduplicated, or the 108-row rows would
have been double-counted.

| dimension | 22-row reports | 108-row reports | pooled |
|---|---|---|---|
| `reason` | 27/132 = **0.205** | 101/432 = **0.234** | 0.227 |
| `call_result` | 6/132 = 0.045 | 33/432 = 0.076 | 0.069 |
| `product` | **0/132 = 0.000** | 14/432 = 0.032 | 0.025 |

Two things this table settles. The rate is roughly **stable across the 5x growth in n**
(0.205 -> 0.234 on `reason`), so projecting it to ~150 rows is defensible. And it differs
by **10x between dimensions**, which alone kills any single fixed band shared by all
three — `reason` discords about nine times as often as `product`, so the same net means
completely different things on the two. Caveat on the estimate itself: the six 22-row
reports are repeat passes over the same 20 items and the 108-row reports share a testset,
so these are overlapping samples, not independent ones, and the rate is also a property
of *which two arms* are being compared. It is used below only to project d for planning.
The band that governs a verdict is computed from the d that run actually produced.

**The null.** This is McNemar's setup. Concordant pairs (both arms right, both wrong)
carry no information about which arm is better and drop out. Among the **d** pairs where
exactly one arm is right, the null hypothesis "the two arms are equally good" makes each
pair a fair coin. With `X ~ Binomial(d, 1/2)` the pairs favouring the candidate:

```
net = X - (d - X) = 2X - d        E[net] = 0        sd(net) = sqrt(d)
```

`net` therefore has the same parity as `d`, and — this is the whole result — its spread
under the null grows as **sqrt(d)**, not as d and not as a constant.

**What the pre-registered bands actually cost.** `P(net >= +6)` and `P(net <= -2)` under
the null, exactly:

| d | max abs net | P(net >= +6) | P(net <= -2) | P(either fires) |
|---:|---:|---:|---:|---:|
| 3 | 3 | **0 — unreachable** | 0.1250 | 0.1250 |
| 4 | 4 | **0 — unreachable** | 0.3125 | 0.3125 |
| 5 | 5 | **0 — unreachable** | 0.1875 | 0.1875 |
| 6 | 6 | 0.0156 | 0.3438 | 0.3594 |
| 24 (n=108) | | 0.1537 | 0.4194 | **0.5731** |
| 34 (n=150) | | 0.1958 | 0.4321 | **0.6278** |

**The two bands were never the same test.** AHEAD at `>= +6` was a 1-in-64 gate when it
was reachable at all; BEHIND at `<= -2` fires **one time in three** on two identical
models. They were written on the same line as if they were a matched pair.

**AHEAD was arithmetically unreachable on most of the 22-row passes.** The 22-row
`reason` d values are 3, 3, 4, 5, 6, 6 — so on **four of the six passes** no result
whatsoever could have produced `net >= +6`. On `call_result` d was 1 on every 22-row
pass, and on `product` d was **0 on all six** — that dimension had no discordant pairs at
all at 22 rows, so its net was not a tie, it was an empty measurement.

**Carried unchanged to the current pack, the rule is worse than useless.** At n=108 the
pre-registered bands return a directional verdict on two identical models **57% of the
time**, and at n=150, **63%**. A rule that calls a coin flip 57% of the time is not a
conservative rule that occasionally misfires; it is closer to a coin flip about a coin
flip.

**Absolute counts or a proportion of discordant pairs? Neither — and that is the answer.**

- **A fixed absolute count** (`>= +6` forever) holds the *threshold* constant while
  `sd(net) = sqrt(d)` grows underneath it. Measured cost: alpha inflates 0.0156 -> 0.1537
  -> 0.1958 across d = 6, 24, 34. The test silently loosens every time the pack grows.
- **A fixed proportion** (`net/d >= c`) holds the *effect size* constant while the
  evidence needed to establish it gets cheaper. Measured cost at c = 0.5: alpha collapses
  0.109 -> 0.0113 -> 0.0015 across the same d. The test silently tightens every time the
  pack grows — you pay for rows and lose the power to spend them. At c = 1.0 it demands a
  clean sweep of 34 pairs, `P = 5.8e-11`, which nothing will ever satisfy.

Both are wrong in the same way and in opposite directions: each holds fixed a quantity
that is not the one the null constrains. The band must scale as **sqrt(d)**. In practice
it is still an integer count — `net` is a count and the verdict reads on it — but the
count is a **function of the d that run produced**, recomputed at scoring time, not a
constant carried between runs. d is observed, not projected, so this rule is
self-calibrating and immune to the rate drifting between arm pairs.

**The invariant being held: alpha = 1/64 = 0.0156 per side.** That is the false-verdict
rate the AHEAD gate actually enforced at the only d where it could fire at 22 rows.
Reasons for choosing that one of the two available rates:

1. It is the gate that would **authorise the migration** — the verdict with a cost
   attached. The rate that guards the expensive action is the rate worth preserving.
2. It is the only one of the two that was ever enforced against data rather than merely
   written down.
3. It is the stricter, so holding it makes **both** sides at least as strict as the
   strictest thing the pre-registered rule ever did.

**The BEHIND rate is deliberately not preserved, and this is a change, stated as one.**
Holding 0.34 constant would mean deliberately reproducing a one-in-three false BEHIND
forever. A 34% false-alarm rate is not an invariant; it is the defect this re-derivation
was pre-registered to find. The bands below are symmetric, which is itself the correction:
there is no argument for the evidence needed to say "worse" being weaker than the evidence
needed to say "better" when the null is symmetric. Two-sided, the new bands sit at
0.006-0.031 depending on where d lands on the parity lattice.

**The bands.** `band(d)` = the smallest k, matching the parity of d, with
`P(net >= k) <= 1/64`:

| d | 6 | 7 | 8 | 9 | 10 | 12 | 16 | 20 | 24 | 26 | 30 | 34 | 40 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **band** | ±6 | ±7 | ±8 | ±9 | ±8 | ±10 | ±10 | ±12 | ±12 | ±12 | ±14 | ±14 | ±16 |
| alpha | .0156 | .0078 | .0039 | .0020 | .0107 | .0032 | .0106 | .0059 | .0113 | .0145 | .0081 | .0122 | .0083 |

Verdict: `net >= +band(d)` AHEAD, `net <= -band(d)` BEHIND, otherwise INDISTINGUISHABLE.
The band is not monotone in d — it steps on the parity lattice — which is why the table
governs and not a formula. If d falls outside the table, `ceil(2.4 * sqrt(d))` lifted to
the parity of d was measured never to be looser than exact over d = 6..200, at the cost
of being one step stricter than necessary at about two thirds of values. Reproduce with:

```python
from math import comb
def p_ge(d, k):                       # P(net >= k) under H0, k >= 0
    return sum(comb(d, x) for x in range((d + k + 1) // 2, d + 1)) / 2 ** d
def band(d, alpha=1/64):              # None => no verdict is available at this d
    k = d % 2
    while k <= d:
        if p_ge(d, k) <= alpha: return k
        k += 2
    return None
```

**Design points at the measured rates**, for planning only — the governing band is always
computed from the run's own d:

| n | `reason` | `call_result` | `product` |
|---|---|---|---|
| 22 | d~5, **no verdict possible** | d~1, **no verdict possible** | d~1, **no verdict possible** |
| 108 | d~24, ±12 | d~7, ±7 | d~3, **no verdict possible** |
| 150 | d~34, ±14 | d~10, ±8 | d~4, **no verdict possible** |

**There is a floor below which no band exists.** The strongest possible evidence at a
given d is a clean sweep, `P = 2^-d`, so alpha = 1/64 requires **d >= 6** before any
AHEAD or BEHIND verdict is available at all. Below it the correct output is not
INDISTINGUISHABLE — that word claims a measurement was made and came out level — but
**UNDERPOWERED: NO VERDICT**.

`product` is where this bites, and the honest statement of it is per run rather than per
pack. **Measured d across the four 108-row comparisons: 6, 0, 6, 2.** The pooled-rate
projection of ~3 is an average no single run realised, and an earlier draft of this
paragraph used that projection to claim `product` "cannot deliver a verdict at any alpha
this file would accept, at either size." **That was wrong**, and it was wrong in the way
this whole derivation exists to prevent — it applied a projection where the governing rule
says to use the d the run actually produced. Corrected in place rather than deleted:

- Two of the four runs reached the `d >= 6` floor and were therefore scorable. Both
  returned `net = -4`, so they land INDISTINGUISHABLE — a real measurement that came out
  level, not an absence of one.
- The other two (d = 0 and d = 2) are **UNDERPOWERED: NO VERDICT**. `d = 0` in particular
  is not a tie at all: the two arms never disagreed on a single product row, so there was
  nothing to test.

So `product` is scorable on some runs and not others, decided per run at scoring time.
Growing the pack raises the expected d but guarantees nothing for any particular pair —
which is the argument for computing the band from observed d rather than from a projection.

**A band derived for one n does not transfer to another n. This is the whole point.**
Not as a caution — as arithmetic. The threshold that holds alpha fixed moves with
sqrt(d), so quoting `±12` at 108 rows and `±12` at 150 rows are different tests, and
quoting the n=22 `+6` at 108 rows is a tenfold loosening (0.0156 -> 0.1537) performed
silently by doing nothing. **Any change to pack size, and any change to the arm pair
(which moves the discordance rate), invalidates the band and requires recomputing it.**
The same applies dimension by dimension within a single run: three dimensions with d of
24, 7 and 3 get three different bands, and one of them gets none.

**What this does not do.** It does not restate any verdict in Experiments 1-4. Those were
read against a rule now measured to be miscalibrated, and re-reading them against this one
is a separate piece of work with its own write-up — deliberately not done here, in the
same session that chose the rule, and deliberately not previewed above.

**Minimum detectable effect, so the cost of the new bands is on the record.** With p the
true probability a discordant pair favours the candidate:

| d | band | p for 50% power | p for 80% power |
|---:|---:|---:|---:|
| 6 | ±6 | 0.891 | 0.964 |
| 24 (n=108) | ±12 | 0.726 | 0.797 |
| 34 (n=150) | ±14 | 0.690 | 0.753 |

This is the honest bill. At 108 rows the design detects a real difference 80% of the time
only if roughly **4 in 5** discordant pairs genuinely favour one arm — a very large effect.
Growing 108 -> 150 rows moves that from 0.797 to 0.753, which is a small return for 39%
more rows and 39% more cost. **If the goal is to detect a modest true difference, more rows
of this pack is not the lever**; a pack with a higher discordance rate, or scoring more than
replicate 1 (Experiment 2, next step 2), buys more per row than length does.

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

## Experiment 2 — Five replicates, and the number that crossed the line without meaning it

**Date:** 2026-08-05
**Question:** Does raising both arms from 3 to 5 replicates change the verdict, and does
the candidate's nondeterminism show up when given more chances to?

### What was run

| # | Run | Model | Provider | Prompt | Result |
|---|---|---|---|---|---|
| 2.1 | incumbent-base5 | gemini-2.5-flash | Google | `v9_16_base` | 100/100 ok |
| 2.2 | candidate-base5 | qwen3.6-27b | Morph | `v9_16_base` | 97/100 ok, **3 `empty_other`** |
| 2.3 | incumbent-e1-5 | gemini-2.5-flash | Google | `v9_16_e1` | 100/100 ok |
| 2.4 | candidate-e1-5 | qwen3.6-27b | Morph | `v9_16_e1` | 99/100 ok, **1 `empty_other`** |

20 items x 5 replicates per run. Decoding identical to Experiment 1 and identical across
arms: `temperature=0.0, top_p=0, seed=0, max_tokens=8000`. Both arms pinned, and the pin
held: **20/20 items returned exactly one `prompt_tokens` value on every run.**

Both arms were raised, not just the candidate. `report.py:573-577` warns when arms carry
unequal replicate counts, because their FLAKY verdicts and `N_flip` then had unequal
chances to see instability — and next step 5 of Experiment 1 (candidate only) would have
triggered it on every future comparison.

### Result

**The candidate is nondeterministic at temperature 0. The incumbent is not.** This is the
finding, and it is what the extra replicates were for:

| arm | `N_flip`, base | `N_flip`, e1 |
|---|---|---|
| gemini-2.5-flash | **0** | **0** |
| qwen3.6-27b | **8** | **4** |

Zero versus eight, over 200 calls per arm, on byte-identical requests. Experiment 1 saw
`N_flip = 0` on both arms at three replicates; three replicates simply did not give the
instability enough chances to appear. The 4 `empty_other` responses are the same story in
the outcome vocabulary — the incumbent produced none in 200 calls.

**One headline number crossed the pre-registered AHEAD threshold, and it should not be
read as AHEAD.** `reason` net on `e1` came out **+6**, against a band fixed before any
data existed (`>= +6` AHEAD). Three reasons not to call it:

1. **Five replicates did not make this number more reliable.** The aggregate metrics are
   scored on **replicate 1 alone** (`cli.py:25-31`; stated in the report's own footer),
   because `metrics.outer_join` keys predictions by `(call_id, phone, product)` and
   pooling replicates silently keeps the last. So `+6` is one draw, exactly as `+5` was.
   What five replicates improved is `N_flip` and the mechanism verdicts — not this.
2. **The same measurement has now produced +5, +4 and +6** across three passes. The
   spread is as wide as the margin by which it crossed.
3. **It is a draw from the arm that flips.** The candidate moved 4 cells between
   replicates on this very run; the incumbent moved none.

`EXPERIMENTS.md` already said it: *"a single Qwen run should not be quoted without a
repeat."* That applies to this run too. **Verdict unchanged: INDISTINGUISHABLE.**

Other dimensions, both prompts: `call_result` net **+1**, `product` net **0**. `reason`
net on base fell to **+2** (from +3 at three replicates).

**Mechanism table.** Four of five rows remain FAIL/FAIL on both arms. `multislot` is
PASS/PASS on base and FAIL/PASS on e1 — the single row still carrying discriminating
signal, and the reason `docs/eval-improvement-plan.md` argues against growing the pack
in a way that would swamp it.

**Cost and latency** (now printed in the report, section 6):

| arm | prompt tok | completion tok | cost USD | latency median |
|---|---|---|---|---|
| gemini base | 282,160 | 21,170 | 0.0903 | 4.73s |
| qwen base | 251,273 | 22,489 | 0.1266 *(3 calls unpriced)* | **9.55s** |
| gemini e1 | 281,360 | 22,809 | 0.1074 | 5.60s |
| qwen e1 | 255,789 | 21,889 | 0.1265 *(1 call unpriced)* | 5.44s |

Qwen costs ~18-40% more. Latency is **not** stable between its own runs — 9.55s median on
base against 5.44s on e1, same model, same pin, same decoding, minutes apart. Read the
latency figures as provider variance, not as a model property. Neither arm reported any
reasoning tokens on these runs.

### Recommended next steps

1. **Do not quote `reason` net e1 = +6 as AHEAD** without a repeat pass. If it must be
   quoted, quote all three draws (+5, +4, +6) and `N_flip` beside it.
2. **Score the aggregate on more than replicate 1.** This is the real limitation the
   extra replicates exposed: five replicates cost five times as much and improved only
   two of six report sections. Fixing it means changing the merge key or aggregating
   across replicates properly — inside `evalharness`, which `CONTRIBUTING.md:59` calls
   final. Argue it before doing it.
3. **The remaining Experiment 1 next steps 1-3 are done** (RET-11 corrected, both class
   boundaries arbitrated in `VOCABULARIES.md`); next step 4 was **withdrawn as false**,
   see the retraction above.
4. Everything else still waits on `RECONCILED`.

### Output files

| What | Path |
|---|---|
| Comparison, base | `out/reports/compare-base-5rep.txt` |
| Comparison, e1 | `out/reports/compare-e1-5rep.txt` |
| Raw run data | `out/runs/20260805-12*Z-{incumbent,candidate}-{base5,e1-5}/` |

**Cost of Experiment 2: $0.4507 across 4 runs / 400 calls.**

Item keys in these reports were generated with a **local placeholder**
`EVAL_HARNESS_KEY_HMAC`, not True's key. They do not resolve inside True's systems. The
pack is entirely synthetic (call ids 5001-5020, phones `08100000xx`), so the HMAC here
pseudonymises invented identifiers only.

---

## Experiment 3 — 100 items, and the lead that turned out to be noise

**Date:** 2026-08-05
**Question:** Does the candidate's advantage on `reason` survive five times the data?

**Prediction, stated before the run** (per the standing instruction at the foot of
Experiment 1): if the `reason` net of +5/+6 were real, it should hold or grow at ~110
scored rows. If it were small-sample noise, it should shrink toward zero.

### What was run

| # | Run | Model | Provider | Prompt | Result |
|---|---|---|---|---|---|
| 3.1 | v2-incumbent | gemini-2.5-flash | Google | `v9_16_base` | 300/300 ok |
| 3.2 | v2-candidate | qwen3.6-27b | Morph | `v9_16_base` | 300/300 ok |

`retention_v2.jsonl`: 100 items, 108 scored rows. 3 replicates. Decoding unchanged and
identical across arms. Pin held: **100/100 items returned exactly one `prompt_tokens`
value** on both arms. No parse failures, no truncation, no empty responses on either side.

### Result

**The candidate's `reason` lead was noise. It reversed.**

| dimension | net at 22 rows | **net at 108 rows** | Gemini F1 | Qwen F1 |
|---|---|---|---|---|
| call_result | +1 | **+1** | 0.937 | **0.957** |
| reason | +2 … +6 | **-1** | **0.787** | 0.759 |
| product | 0 | **-2** | **0.945** | 0.915 |

At 22 rows `reason` net read +3, then +5, then +4, then +6 — brushing the pre-registered
AHEAD threshold. At 108 rows it is **-1**, and weighted F1 puts the incumbent ahead on
that dimension. Every margin is now within 2 items. **The prediction that distinguishes
signal from noise came out on the noise side**, and the number that would have been
quoted in a migration memo was measuring nothing.

This is the finding. It is also the clearest available argument for having expanded, and
against having trusted the 22-row figures — including in this file, where they were
reported with caveats but reported.

**The mechanism table died, exactly as predicted.** All five rows are now FAIL/FAIL on
both arms; the table carries zero information. `docs/eval-improvement-plan.md` set this
out in advance: the verdict rule is FAIL if *any* item in a group fails on every
replicate, which is monotone decreasing in group size. `multislot` went 2 -> 10 items and
collapsed from FAIL/PASS to FAIL/FAIL. The headline this pack was designed to produce no
longer separates the arms, and restoring it needs a different verdict rule, not more items.

**Both arms are less stable at scale.** `N_flip` over 3 replicates: incumbent **12**,
candidate **26**. The incumbent was perfectly stable across 20 items in Experiment 2; at
100 it is not. The candidate remains roughly twice as unstable.

**Whole-item correctness, and why it disagrees with F1.** Scoring all-or-nothing per item
on replicate 1: incumbent **43/100**, candidate **35/100** — far harsher than F1 of
0.76-0.96 on the same data. The gap is over-labelling, and it is measurable:

| why the incumbent's 57 failures failed | n |
|---|---:|
| **extra reasons added** | **39** |
| different reasons | 7 |
| wrong product | 5 |
| wrong outcome | 4 |
| reasons missing | 2 |

Two thirds of all failures are a correct answer with unsupported reasons bolted on
(RET-02: truth `promotion related`, answer `down sell not success + other + promotion
related`). That is one fixable behaviour, not a comprehension failure, and it is what
`v9_16_e1` exists to address. **43% must not be quoted as accuracy** — it is the strictest
reading available, and the per-dimension figures are the fairer one.

**Cost and speed:** incumbent $0.3363, candidate $0.3891 over 300 calls each — the
candidate ~16% dearer.

### Verdict

**No accuracy case for migrating, and a reliability case against it.** The incumbent wins
`reason` and `product`, the candidate wins `call_result`, all by <= 2 items. On stability
the incumbent is ahead by roughly 2x. On cost the incumbent is ~16% cheaper.

`RECONCILED: NO` still stands, and production still reads audio while this pack reads
text. This remains a comparison on one controlled dimension, not a production verdict.

### Recommended next steps

1. **Do not quote any 22-row figure from Experiments 1-2 again.** Experiment 3 supersedes
   them on every dimension. They stay in this file as the record of how the answer moved.
2. **Fix the `other` class before quoting it.** 8 of its 10 items are flood calls, so a
   model can score the class by learning "flood -> other" (`docs/testset-v2-plan.md`).
3. **Repair or replace the mechanism table.** It is the pack's designed headline and it is
   now uninformative at 100 items. A group-level rule that is monotone in group size
   cannot survive growth; this needs arguing, not patching.
4. **Run `v9_16_e1` over the 100 items.** Over-labelling is two thirds of all failures and
   e1 is the variant built to reduce it. ~$0.73.

### Output files

| What | Path |
|---|---|
| Comparison | `out/reports/compare-v2-100items.txt` |
| Per transcript, per model | `out/reports/v2-100items-per-transcript.xlsx` |
| Raw run data | `out/runs/20260805-140652Z-v2-incumbent/`, `out/runs/20260805-140947Z-v2-candidate/` |

**Cost of Experiment 3: $0.7254 across 2 runs / 600 calls.**

Item keys were generated with a local placeholder `EVAL_HARNESS_KEY_HMAC`, not True's;
they do not resolve inside True's systems.

---

## Experiment 4 — A third arm, and the discovery that the endpoint moves the answer more than the model does

**Date:** 2026-08-05
**Question:** Is `qwen/qwen3.6-35b-a3b` — the MoE sibling, 35B total / 3B active, priced
at $0.098/$0.95 per M tokens against the 27B's $0.60/$3.60 — good enough to change the
migration economics?

**Prediction, stated before the run:** the model is ~6x cheaper per input token and ~3.8x
cheaper per output token. If it scores comparably it is the cheapest credible candidate
seen so far. If it scores worse, the cheaper token price is irrelevant. **The prediction
that turned out to matter was one nobody made: that per-token price would predict
per-call cost.** It does not.

### What was run

| # | Run | Model | Provider | Result |
|---|---|---|---|---|
| 4.0a | probe | qwen3.6-35b-a3b | DeepInfra / AkashML / CoreWeave | all 3 honour the schema |
| 4.1 | first attempt | qwen3.6-35b-a3b | DeepInfra, concurrency 8 | **110/300 `transport_error`** |
| 4.2 | retry, lower concurrency | qwen3.6-35b-a3b | DeepInfra, concurrency 3 | **221/300 `transport_error`** |
| 4.3 | endpoint probe | qwen3.6-35b-a3b | AkashML, CoreWeave | 10/10 ok each |
| 4.4 | **the arm** | qwen3.6-35b-a3b | AkashML | 293/300 ok, 7 `empty_length` |
| 4.5 | incumbent, re-run | gemini-2.5-flash | Google | 299/300 ok, 1 `provider_error` |
| 4.6 | 27B, re-run | qwen3.6-27b | **Morph** | **300/300 HTTP 400** |
| 4.7 | 27B, re-run | qwen3.6-27b | CoreWeave | 300/300 ok |

100 items x 3 replicates. `retention_v2`, prompt `v9_16_base`, decoding unchanged. Runs
4.4-4.7 all share `scorer_sha ea3c952` and `testset_sha 9c91b036`, which is what makes
them comparable; 4.5 and 4.7 exist **only** because that gate refused a comparison
against the Experiment 3 runs, which were made at `96afee3`.

### Result

**The 35B-A3B is worse than the 27B on every dimension, and much worse than the
incumbent on stability.**

| vs incumbent (gemini) | call_result | reason | product | `N_flip` |
|---|---|---|---|---|
| qwen3.6-27b (CoreWeave) | +1 | **+24** | 0 | 22 |
| qwen3.6-35b-a3b (AkashML) | **-7** | +14 | **-4** | **62** |
| *gemini itself* | — | — | — | **2** |

Head to head, 27B against 35B-A3B: `call_result` **-8**, `reason` **-10**, `product`
**-4**. The cheaper model loses on all three.

Weighted F1 against the incumbent: `call_result` 0.937 vs 0.899, `reason` 0.796 vs 0.803,
`product` 0.945 vs 0.914. One near-tie and two losses.

**`N_flip = 62` against the incumbent's 2.** Thirty-one times less stable on
byte-identical requests at `temperature=0`. The `reason` net of +14 is a single draw from
that arm and should not be quoted on its own.

### THE FINDING: the endpoint changes the answer more than the model does

The 27B was re-run because Morph broke. CoreWeave served it **in a reasoning regime**;
Morph had served it non-reasoning. Same model id, same prompt, same decoding, same pack:

| qwen3.6-27b | Morph (Exp 3) | CoreWeave (Exp 4) |
|---|---|---|
| `reason` net vs incumbent | **-1** | **+24** |
| reasoning tokens | ~0 | **1,731,272** |
| latency median | ~9.5s | **40.31s** |
| cost, 300 calls | **$0.389** | **$4.712** |

A 25-point swing on the headline dimension, a 12x cost increase and a 4x latency
increase, from changing nothing but the endpoint. **Production runs
`thinkingBudget: 0`** (`config/model_setting/retention.yml`), so the `+24` describes a
regime production does not deploy. Experiment 1 already found one endpoint defect
(Alibaba's broken decoder); this is the stronger version of the same lesson — the pin is
not a detail of the method, it is a term in the result.

### Cost and latency, measured

| arm | prompt tok | completion tok | reasoning tok | cost | latency median |
|---|---|---|---|---|---|
| gemini-2.5-flash | 839,078 | 61,312 | **0** | **$0.350** | **2.25s** |
| qwen3.6-27b (CoreWeave) | 1,099,650 | 1,130,422 | 1,731,272 | $4.712 | 40.31s |
| qwen3.6-35b-a3b (AkashML) | 1,099,650 | 1,239,886 | 1,852,965 | $1.394 | 33.55s |

**The cheaper-per-token model is 4x dearer per call than the incumbent**, because it
spends 1.85M reasoning tokens reaching the same answers. Per-token price did not survive
contact with per-call cost.

### Three provider failures, all in one day

- **DeepInfra rate-limited the 35B-A3B upstream.** 110 then 221 failures, every one
  `429 ... 'qwen/qwen3.6-35b-a3b is temporarily rate-limited upstream'`. **Lowering
  concurrency 8 -> 3 made it worse**, which is how the initial diagnosis ("my concurrency
  setting") was shown to be wrong: a longer run stayed inside the throttle window longer.
- **Morph now returns HTTP 400 `'Multi-turn conversations are not supported'`** on all
  300 calls. Morph served this exact two-message request throughout Experiments 1-3. The
  endpoint changed under us, mid-evaluation.
- **CoreWeave costs 12x Morph** for the same model, because it reasons and Morph did not.

### Two harness defects found, recorded not patched

1. **`prompt_token_spread` false-positives on a failed row.** The incumbent was flagged
   `SPLIT 99/100` on RET-23 with values `[0, 2791]`. The `0` is the single
   `provider_error` row, which carries no usage; `prompt_token_spread` skips `None` but
   not `0`, so a failed call manufactures a phantom second tokenizer. The pin-proof
   signal is the one that must not cry wolf.
2. **`scorer_sha` is repo HEAD, so any commit invalidates comparability.** Runs 4.5 and
   4.7 were paid for because a docs-only commit moved the sha; `git diff 96afee3 HEAD --
   src/` is **empty**. The gate did its job as written and was not weakened
   (`CLAUDE.md`), but a scorer hash that changes when a README changes is coarser than
   its purpose requires.

Both belong to layers `CONTRIBUTING.md:57` calls final. They are written down for
argument, not patched mid-experiment.

**Addressed before Experiment 5:** zero/non-usage rows no longer enter the tokenizer
fingerprint, and run provenance now hashes the classification contract, scoring surface
and common workload separately instead of using repository HEAD. Experiment 4 remains
unchanged because those fixes were not present when it ran.

### Verdict

**No. `qwen/qwen3.6-35b-a3b` is not a viable candidate.** It loses to the 27B on all
three dimensions, loses to the incumbent on two, is 31x less stable than the incumbent,
15x slower, and 4x dearer despite the cheaper token price.

**And the 27B's apparent `+24` is not a reason to revisit it**, because it was bought in a
reasoning regime production does not run. In the regime production *does* run, Experiment
3 measured that same dimension at **-1**.

`RECONCILED: NO`. Production reads audio; this pack reads text.

### Recommended next steps

1. **Pin the regime, not just the provider.** The `provider` field and even
   `prompt_token_spread` agree across two endpoints that differ by 1.7M reasoning tokens.
   Record `reasoning_tokens` in `_refuse_incomparable`'s blocking set, or at minimum warn
   loudly when two arms differ by an order of magnitude on it. Falsified if a regime
   change can be shown not to move a score — Experiment 4 shows it moves it 25 points.
2. **Fix the `prompt_token_spread` zero.** One-line: ignore rows with no usage rather
   than treating `0` as a token count. Falsified if a real split ever reports `0`.
3. **Re-check Morph.** If `'Multi-turn conversations are not supported'` is permanent,
   every Experiment 1-3 number came from an endpoint that no longer exists in that form,
   and their reproducibility is gone.
4. Everything else still waits on `RECONCILED`.

### Output files

| What | Path |
|---|---|
| Gemini vs 35B-A3B | `out/reports/compare-gemini-vs-35b-a3b.txt` |
| Gemini vs 27B (CoreWeave) | `out/reports/compare-gemini-vs-27b-cw.txt` |
| 27B vs 35B-A3B | `out/reports/compare-27b-vs-35b.txt` |
| Every run, with provenance | `RUNS.md` |

**Cost of Experiment 4: ~$7.73**, of which **$1.29 bought nothing** — two DeepInfra runs
killed by upstream throttling and one Morph run killed by a 400. Recorded because a
harness that reports only the runs that worked is a harness that under-reports what
evaluation costs.

Item keys used a local placeholder `EVAL_HARNESS_KEY_HMAC`; they do not resolve inside
True's systems.

---

## Experiment 5 — enterprise Retention baseline and robustness (PRE-REGISTERED)

**Pre-registered:** 2026-08-06

**Status:** DRAFT; no Experiment 5 model calls have been made.

**Machine plan:** `experiments/retention-e5.plan.json` (the CLI prints its current SHA).
**Question:** Can either Qwen candidate match Gemini on Retention under an explicitly
non-reasoning, provider-pinned regime, then remain reliable and operationally viable on
production-robustness cases and under load?

### Arms and common workload

| Arm | Model | Provider | Reasoning | Prompt |
|---|---|---|---|---|
| incumbent | `google/gemini-2.5-flash` | Google | explicit `none` | `v9_16_base` |
| candidate | `qwen/qwen3.6-27b` | Morph | explicit `none` | `v9_16_base` |
| candidate | `qwen/qwen3.6-35b-a3b` | AkashML | explicit `none` | `v9_16_base` |

Full run: `retention_v3`, 138 items / 150 scored rows, three identical replicates,
concurrency four, temperature/top-p/seed `0/0/0`, 8,000 maximum tokens, and **one API
attempt per logical call**. Phase one is the frozen `RET-01..RET-100` prefix; phase two
is `RET-101..RET-138` (long context, ASR-shaped noise, Thai-English code switching and
regressions). Primary quality is the full pack; both slices are reported.

### Qualification before selection

For each current provider candidate, run `RET-01`, `RET-109`, and `RET-138` twice with
the exact prompt/schema request, fallback disabled and `reasoning.effort=none`. Pass is
6/6 parsed object-root responses, expected observed model/provider, zero reasoning
tokens, positive usage and one prompt-token fingerprint per item.

A repeated Morph HTTP 400 is `REQUEST_INCOMPATIBLE`, not a transient retry target. A
bare scalar from Alibaba is `SCHEMA_INCOMPATIBLE`. Neither is fixed by changing the
message layout or weakening the schema, because that defines a new workload. If no 27B
provider qualifies with reasoning disabled, the production-like 27B arm is
`UNAVAILABLE`; reasoning-enabled CoreWeave may be a separately labelled diagnostic and
cannot stand in for it.

### Gate 1 result: qualification complete

Gate 1 was approved for at most 400 calls / US$15; the tighter preregistered bound
controlled execution. All 18 provider names received exactly three items × two
replicates with one attempt, for **108 calls** and **US$0.109184588** reported cost.
Twelve providers qualified and six returned 404 `No endpoints found` under the exact
required-parameter request. Those six are `REQUEST_INCOMPATIBLE`, not identity
mismatches: no endpoint ran and therefore none could report an identity.

Morph and Alibaba both qualified 6/6 with zero reasoning tokens. Their earlier failures
were real but did not reproduce: Morph no longer rejected the two-message prompt and
Alibaba no longer returned a scalar root. This is evidence of endpoint change, not a
reason to trust catalog status without probing.

Provider selection used historical continuity, not the three probe outputs: Google for
the incumbent, Morph for 27B and AkashML for 35B-A3B. The plan is locked at SHA
`2823d3359f6ca6dee601f27b84672ef100971b609bdf38368a56990f2e323c8e`.
All self-hashed results, selection rationale and the failure-classifier correction are
recorded in `docs/experiment5-qualification.md`. Gate 2 remains pending; no full or
load call has been made.

### Pre-registered decision rule

1. Reliability: at least **410/414** calls must be `parse_ok` (unrounded rate ≥99%).
2. Quality: call result, reason and product remain separate. On replicate one, compute
   the exact directional threshold from observed discordant pairs at alpha `1/64` per
   side. A candidate must not be `BEHIND` in any dimension. `UNDERPOWERED` means
   `INCONCLUSIVE`, never a tie or a pass.
3. Stability: pair items by whether their classified payload is identical across all
   three replicates. Candidate must not be `BEHIND` Gemini.
4. Operations: only quality-eligible arms are ranked on cost and load. No weighted score
   trades cheaper calls for a failed quality gate.

Load uses the 12 ids fixed in the machine plan, twice each at concurrency 1, 4 and 8:
72 calls per arm. Report throughput, p50/p95/p99/max latency, reliability, token usage,
reported cost and missing-cost calls.

### Approval and budget

- Gate 1: review current provider inventory and maximum bounded probe cost before any
  qualification call.
- Gate 2: record qualification artifacts, select providers, add their hashes, lock the
  plan, rerun offline checks, and review exact projected cost before full/load calls.

Full plus load budget is **1,458 calls** across three arms (1,242 + 216). The 18 eligible
provider names currently listed add at most 108 qualification calls, for a current
maximum of **1,566**. At prices checked 2026-08-06, the deliberately conservative
qualification ceiling is **$3.47** (twice the UTF-8 content bytes for input and every
call spending all 8,000 output tokens). Refreshing inventory or price changes that
maximum and requires draft review.

After provider selection, the deliberately extreme full/load bound is **$50.13**:
Gemini/Google $28.32, 27B/Morph $15.12 and 35B-A3B/AkashML $6.69. Including the original
$3.47 qualification ceiling gives a grand planning maximum of **$53.60**.
`evalgen experiment-budget` recalculates it from the locked plan. This is not expected
spend; it is the conservative planning ceiling under recorded prices and token caps.

### What would change the decision

- A candidate that qualifies, clears 410/414, is not `BEHIND` on any quality dimension
  or stability, and has preferable operational trade-offs becomes the recommended
  Retention candidate for production-shaped validation.
- A `BEHIND` or reliability result rejects that candidate under this workload.
- An underpowered primary dimension produces no migration recommendation until a more
  informative labelled pack or production-shaped truth resolves it.
- No synthetic-text outcome overrides `RECONCILED: NO`; live-report reconciliation and
  the production audio gap remain decision blockers.
