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
  Experiment 1 (:391-397).

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
the same discipline :298-301 already applied in writing when it refused to move the AHEAD
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

## Experiment 5 — two parallel v3 evaluations, disambiguated after merge

Two branches used the Experiment 5 label independently on 2026-08-06. The merged
history calls the earlier reasoning-enabled robustness run **5A** and the preregistered
explicit-reasoning-off enterprise run **5B**. Their original run ids, machine experiment
id (`retention-e5`) and committed evidence remain unchanged. The distinction is
load-bearing: 5A measured reasoning-enabled Qwen arms, while 5B measured provider-pinned
reasoning-off arms and reached the opposite quality result.

## Experiment 5A — reasoning-enabled v3 robustness and a model-specific length failure

**Date:** 2026-08-06
**Question:** With `retention_v3` (138 items: the 100-item v2 pack byte-identical, plus 38
new items across four families — `long_context`, `asr_noise`, `code_switch`,
`regression`) and the √d bands re-derived above, what do the three arms actually look
like under a properly-powered paired test, and does context length degrade labelling?

### What was run

| # | Run | Model | Provider | Result |
|---|---|---|---|---|
| 5.1 | v3-gemini | gemini-2.5-flash | Google | 414/414 ok, `scorer_sha a7aff2f` |
| 5.2 | v3-qwen27b | qwen3.6-27b | CoreWeave | 413/414 ok, 1 `empty_length`, `scorer_sha e0462c9` |
| 5.3 | v3-qwen35a3b | qwen3.6-35b-a3b | AkashML | 400/414 ok, 14 `empty_length`, `scorer_sha e0462c9` |
| 5.4 | v3-gemini-rescored | gemini-2.5-flash | Google | 414/414 ok, `scorer_sha e0462c9` — **5.1 superseded, see harness note below** |

138 items x 3 replicates, `retention_v3`, prompt `v9_16_base`, decoding unchanged
(`temperature=0, top_p=0, seed=0, max_tokens=8000`). Every run's `prompt_tokens`
fingerprint returned exactly one value per item — no split arm.

### Harness note: the `scorer_sha` gate fired again, and this time it cost a re-run

Run 5.1 finished at `scorer_sha a7aff2f`. Before 5.2 and 5.3 finished, `e0462c9` (the
hand-derived ASR-expectation doc, a pure `.md` addition — `git diff a7aff2f e0462c9 --
stat` touches exactly one file, zero lines of code) landed on this branch, because it was
committed mid-run-sequence. `evalgen compare` refused 5.1 against 5.2 on
`scorer_sha` mismatch. This is the identical shape of Experiment 4's harness defect #2
("`scorer_sha` is repo HEAD, so any commit invalidates comparability... a docs-only
commit moved the sha"), observed for a second time. **The fix was the same as the
principle demands: do not weaken the gate, make the runs actually comparable.** 5.1 was
re-run as 5.4 at current HEAD ($0.4937 spent on 5.1 bought nothing). Two occurrences in
five experiments makes this a standing cost of the current design, not a one-off — see
recommended next steps.

### Result

**Governed reading: the paired verdicts under the bands derived above, not the raw
percentages.** `d` is the discordant-pair count each comparison actually produced;
`band(d)` is read off the table, not assumed from a previous run's size.

| Comparison | dimension | d | net | band | verdict |
|---|---|---:|---:|---:|---|
| Gemini vs Qwen27B | call_result | 4 | +2 | — | **UNDERPOWERED: NO VERDICT** (d<6) |
| Gemini vs Qwen27B | reason | 40 | **+26** | ±16 | **AHEAD — Qwen27B** |
| Gemini vs Qwen27B | product | 0 | 0 | — | **UNDERPOWERED: NO VERDICT** (nothing discordant) |
| Gemini vs Qwen35B | call_result | 13 | -7 | ±9 | INDISTINGUISHABLE |
| Gemini vs Qwen35B | reason | 41 | **+17** | ±15 | **AHEAD — Qwen35B** |
| Gemini vs Qwen35B | product | 5 | -1 | — | **UNDERPOWERED: NO VERDICT** (d<6) |
| Qwen27B vs Qwen35B | call_result | 11 | -9 | ±9 | **BEHIND — Qwen35B** |
| Qwen27B vs Qwen35B | reason | 29 | -9 | ±13 | INDISTINGUISHABLE |
| Qwen27B vs Qwen35B | product | 5 | -1 | — | **UNDERPOWERED: NO VERDICT** (d<6) |

**Both Qwen arms are AHEAD of Gemini on `reason` at alpha=1/64 — the first dimension in
this project to clear an AHEAD band without a repeat-pass caveat.** Neither win is free:
both were bought in a reasoning regime (see below). `call_result` cannot distinguish
Gemini from either Qwen arm (underpowered or INDISTINGUISHABLE) but **can** distinguish
the two Qwen arms from each other — Qwen35B is BEHIND Qwen27B on `call_result`, exactly
at the ±9 boundary. `product` returned **zero informative verdicts across all nine
cells** — every comparison landed d<6. This mirrors the `product` pattern already
recorded above (measured d across four 108-row runs: 6, 0, 6, 2): the dimension the two
models already agree on most stays too low-discordance to test, and that gets worse, not
better, as agreement improves.

For orientation only — not a verdict, and not interpretable at this n per the reasoning
above the bands table — whole-item correctness on replicate 1:

| family | n | Gemini | Qwen27B | Qwen35B |
|---|---:|---:|---:|---:|
| clear | 30 | 47% | 73% | 77% |
| thai_linguistic | 30 | 43% | 77% | 57% |
| tiebreak | 17 | 47% | 65% | 65% |
| multislot | 10 | 40% | 50% | 20% |
| escape | 13 | 46% | 62% | 54% |
| long_context | 12 | 67% | 83% | 75% |
| asr_noise | 10 | 60% | 80% | 40% |
| code_switch | 10 | 60% | 70% | 70% |
| regression | 6 | 67% | 83% | 83% |
| **overall** | 138 | **50%** | **72%** | **62%** |

### `long_context` — the dilation family, read properly

The mechanism table (always-correct-on-all-3-replicates, the metric Experiment 3
established as the one that does not saturate) gives all three arms the same headline
**9/12**, but the *shape* of the 3 misses differs completely, and only the shape
survives a length claim:

| Level (n=6 each) | Gemini | Qwen27B | Qwen35B |
|---|---|---|---|
| 3x | 6/6 always-correct | 4/6 always-correct, **2 FLAKY** (RET-109, RET-111) | 5/6 always-correct, **1 FLAKY** (RET-105) |
| 10x | 3/6 always-correct, **3 FAIL** (RET-104, RET-108, RET-110) | 5/6 always-correct, **1 FLAKY** (RET-110) | 4/6 always-correct, **2 FLAKY** (RET-106, RET-110) |

**Gemini's three misses are all at 10x, all `FAIL` — wrong on every single replicate,
the same wrong answer three times.** Neither Qwen arm has a single `FAIL` item anywhere
in this family; every Qwen miss is `FLAKY` — right on some replicates, wrong on others,
roughly evenly split between 3x and 10x. That is a real difference in *kind*, not just
rate: Gemini's failure at length looks like a deterministic misread that a fourth
replicate will not fix; the Qwen arms' imperfection at any length looks like ordinary
decoding noise.

**Correction to a live read I gave mid-run.** After only the Gemini arm had finished, I
reported the replicate-1 curve (83% at 3x, 50% at 10x) as "the first evidence in this
project that context length degrades labelling." That was premature and said so at the
time. With all three arms in and scored properly: **length degrades Gemini. It does not
degrade Qwen27B or Qwen35B on this pack.** The corrected claim is about a model, not
about length — which is a materially different thing to tell the migration decision.

### The confound that governs how every AHEAD verdict above should be read

| | reasoning tokens | regime |
|---|---:|---|
| Gemini (5.4) | **0** | non-reasoning — matches `config/model_setting/retention.yml`'s `thinkingBudget: 0` |
| Qwen27B (5.2) | 2,379,369 | reasoning (CoreWeave — Morph, the only non-reasoning endpoint, still returns HTTP 400, per Experiment 4) |
| Qwen35B (5.3) | 2,620,339 | reasoning (AkashML) |

Both Qwen AHEAD verdicts on `reason` are therefore **"Qwen with ~2.4-2.6M tokens of
reasoning beats Gemini with none,"** not "Qwen labels Thai better." This is exactly
Experiment 4's finding restated at the new pack size, still unresolved: there is
currently no working non-reasoning endpoint for `qwen/qwen3.6-27b` at all, so the
regime confound cannot be removed with the endpoints available today, only disclosed.

### Cost, tokens and latency (5.4 + 5.2 + 5.3, the comparable set)

| arm | calls | reason tok | cost USD | latency median | latency max | empty_length |
|---|---:|---:|---:|---:|---:|---:|
| Gemini (5.4) | 414 | 0 | $0.4830 | 2.02s | 5.22s | 0 |
| Qwen27B (5.2) | 414 | 2,379,369 | $6.5531 | 40.62s | 85.94s | 1 |
| Qwen35B (5.3) | 414 | 2,620,339 | $1.9626 | 28.75s | 91.70s | **14** |

Qwen27B costs **13.6x** Gemini per arm and **3.3x** Qwen35B, for a `reason` verdict that
is statistically real but regime-confounded. Qwen35B's 14 `empty_length` rows (it
exhausted the 8,000-token budget on reasoning and returned nothing 3.4% of the time) is
the same failure mode Experiment 4 saw at smaller n, now large enough to be a real
contributor to its lower `call_result` and `product` scores rather than a rounding
artifact.

`N_flip` (replicate instability, every dimension, every row): Gemini **0**, Qwen27B
**43**, Qwen35B **70**. Both reasoning arms are far less stable than the incumbent on
byte-identical `temperature=0` requests, consistent with every prior experiment.

### Recommended next steps

1. **Batch doc/fixture commits before launching a multi-arm run, or budget for a
   re-run.** Two occurrences of the same `scorer_sha`-invalidates-comparability defect
   in five experiments (Experiment 4 finding #2; this run's harness note) is a pattern,
   not a coincidence. Falsified if a future multi-arm launch survives an in-flight
   commit without needing a re-run — it will not, under the gate as written, so the real
   fix is process discipline: land every commit before `baseline` starts, not after.
2. **Do not extend `long_context` past 10x, or add more dilation items, without a
   non-reasoning Qwen endpoint.** The one clean model-level finding here (Gemini
   degrades at length, Qwen does not) is bought entirely inside the regime confound;
   more items sharpen a number that is still not isolating the variable it claims to.
   Falsified if a non-reasoning `qwen/qwen3.6-27b` endpoint becomes available and the
   FAIL/FLAKY split above holds under it.
3. **`product` needs a different growth strategy than "more items."** Zero informative
   verdicts across nine cells, at 150 rows, is the predicted outcome of a dimension
   both arms already agree on — adding items proportionally will not raise `d` past 6
   unless new items are specifically chosen to be `product`-discordant (a call whose
   product classification is genuinely ambiguous), which today's authoring process does
   not target.
4. Everything else still waits on `RECONCILED`.

### Output files

| What | Path |
|---|---|
| Gemini vs Qwen27B | `out/reports/compare-v3-gemini-vs-27b.txt` |
| Gemini vs Qwen35B | `out/reports/compare-v3-gemini-vs-35b.txt` |
| Qwen27B vs Qwen35B | `out/reports/compare-v3-27b-vs-35b.txt` |
| ASR-noise hand-derived expectation | `tests/fixtures/testsets/ASR-EXPECTATION.md` |
| Every run, with provenance | `RUNS.md` |

**Cost of Experiment 5: ~$9.49**, of which **$0.4937 bought nothing** — run 5.1,
made incomparable by an in-flight commit and superseded by 5.4. Item keys used a local
placeholder `EVAL_HARNESS_KEY_HMAC`; they do not resolve inside True's systems.

---

## Experiment 5B — enterprise Retention baseline and robustness (EXECUTED)

**Pre-registered:** 2026-08-06

**Status:** complete; both Qwen candidates `FAIL`; `RECONCILED: NO`.

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
recorded in `docs/experiment5-qualification.md`. No full or load call had been made at
the point the plan was locked; Gate 2 approval and execution followed without changing
that reviewed plan SHA.

### Gate 2 result: neither Qwen candidate qualifies

The user approved the immutable plan SHA above for exactly **1,458 calls** and a
conservative **US$50.13** ceiling. Execution made all 1,458 calls with one attempt per
logical call. OpenRouter-reported cost was **US$1.507460937**, retained as a lower bound
because missing cost is never turned into zero.

| Candidate | Parse valid | Call result | Reason | Product | Stability | Decision |
|---|---:|---|---|---|---|---|
| Qwen3.6 27B / Morph | 359/414 | BEHIND (-19; band 13) | BEHIND (-19; band 17) | UNDERPOWERED | BEHIND (-121; band 25) | **FAIL** |
| Qwen3.6 35B-A3B / AkashML | 414/414 | BEHIND (-11; band 11) | BEHIND (-24; band 16) | BEHIND (-10; band 8) | BEHIND (-131; band 27) | **FAIL** |

Morph passed the six-call qualification but failed under the full workload: 54 HTTP
429 transport failures and one empty response. This supersedes the claim that it is
permanently broken by the old multi-turn 400. The endpoint accepted the exact request,
then proved operationally unreliable at the required scale. No failure was retried.

The first offline report also exposed a runtime-gate defect: failed calls legitimately
lack token metadata, so requiring usage on every logical call counted an allowed
reliability failure twice and silently made the 99% rule a 100% rule. The gate now
requires usage and zero reasoning on successful responses while failures remain in the
reliability denominator. A regression test pins the correction; no model call was
rerun, and the same raw logs were reported deterministically.

The result does not authorize migration. It rejects these two Qwen arms under the
locked synthetic-text workload while retaining `RECONCILED: NO`. Full evidence,
operations tables and limitations are in `docs/experiment5-results.md`; safe reports
are committed under `experiments/evidence/retention-e5/report/`.

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
  **Complete:** external self-hashed approval preserved the already-reviewed plan SHA.

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
Actual full/load reported cost was US$1.507460937 for all 1,458 authorized calls.

### What would change the decision

- A candidate that qualifies, clears 410/414, is not `BEHIND` on any quality dimension
  or stability, and has preferable operational trade-offs becomes the recommended
  Retention candidate for production-shaped validation.
- A `BEHIND` or reliability result rejects that candidate under this workload.
- An underpowered primary dimension produces no migration recommendation until a more
  informative labelled pack or production-shaped truth resolves it.
- No synthetic-text outcome overrides `RECONCILED: NO`; live-report reconciliation and
  the production audio gap remain decision blockers.

---

## Experiment 6 — an independent judge, and a night spent auditing everything that came before it

**Date:** 2026-08-07
**Question:** Two things, deliberately bundled because the second motivated the first.
(1) Does an automated, independent second opinion on scorer disagreements find anything
this project's ground truth got wrong -- the same class of defect RET-11 was, found by a
human reading a transcript by hand? (2) What did a full audit of the ~2,500 lines that
merged in overnight (Experiment 5B's enterprise framework) miss, that a fresh adversarial
pass would catch?

### Part A: the audit

A multi-agent workflow mapped `experiments.py`, `cli.py`, `report.py`,
`evalharness/compare.py` and `evalharness/manifest.py` (the two `CONTRIBUTING.md`-flagged
final layers), cross-checked every numeric claim in `docs/experiment5-results.md` and
`docs/enterprise-evaluation-framework.md` against the committed evidence JSON byte for
byte, then hunted for correctness, statistical, data-integrity and test-coverage defects
across the result. 21 agents, 12 candidate findings, all 12 adversarially verified by an
independent skeptic reading the actual cited code before counting a finding as real. Zero
refuted.

**Every numeric claim checked against raw evidence matched exactly** -- the Morph
54-transport-error count, the $1.507460937 total across 1,458 calls, every BEHIND
net/band pair in the 27B and 35B-A3B tables. `compare.exact_band(d)` was independently
re-run against `EXPERIMENTS.md`'s own `band()` reference at d=6,11,29,40 and matched at
every point. No arithmetic was wrong anywhere the audit looked.

**What was wrong was gate logic that never fires on the shipped data, and coverage gaps
around it:**

1. **`decision()` never routed an UNDERPOWERED stability verdict to INCONCLUSIVE.**
   Quality dimensions get this treatment; stability -- computed by the identical
   `paired_verdict`/`exact_band` machinery -- did not, and fell through to PASS. Confirmed
   live: three clean quality verdicts plus a stability comparison with `d=2` (genuinely
   underpowered) returned `Decision('PASS', ...)`. Did not affect Experiment 5B's actual
   FAIL verdicts -- both candidates' stability was `BEHIND` (band 25 and 27), well clear
   of the gap -- but the gap was real and untested. **Fixed**, with two regression tests
   (the failing case, and a check that a genuinely BEHIND stability verdict still wins).
2. **`role` (which arm is "incumbent" for every paired comparison) was never validated**
   -- not in the always-run schema checks, not in the locked-status deep audit that
   otherwise re-verifies `selected_provider`/`qualification_sha` against evidence. A
   transposed role flips AHEAD/BEHIND, and therefore PASS/FAIL, for every dimension with
   no error anywhere in the pipeline. The committed plan's roles are correct, so this
   never fired -- but nothing was checking. **Fixed**: `validate_plan` now requires
   exactly one `incumbent` and every other role to be `candidate`, with three regression
   tests (transposed, duplicated, and a role outside the closed set).
3. **`retention_v3.manifest.json`'s own embedded claims** (its sha256 of the testset and
   ground truth, item counts, family breakdowns) **were never recomputed and compared**
   against the actual pack files by any test -- the only existing check re-hashes the
   manifest FILE itself against the plan's pin, which proves the manifest's bytes have
   not moved, never that its claims are still true of `retention_v3.jsonl`. Currently
   correct (independently verified), but undetectable drift. **Fixed**: a new test
   recomputes both file hashes, the item count, the scored-row count and the full family
   breakdown, and asserts them against the manifest's own text.
4. **`cmd_qualify` (spends real API calls, decides QUALIFIED/INCOMPATIBLE) has zero test
   coverage.** **`cmd_experiment_run`'s three safety gates** (`--confirm-plan-sha`
   mismatch, an `UNAVAILABLE` arm, an out-of-list `--concurrency-level`) **are only ever
   exercised with a passing value** -- no test supplies a wrong sha, an unavailable arm,
   or a bad concurrency level. **`manifest.workload_sha`'s forbidden-field guard and the
   era-mixing checks in `_refuse_incomparable`** are similarly untested. **Not fixed
   tonight** -- these need either a mocked client or deliberately-broken fixtures for a
   code path that spends real money when it works correctly, and building that
   scaffolding at 2 a.m. risked being the thing that introduced a real bug. Recorded as
   the top of the next session's list, not silently left for someone to rediscover.
5. **`_disagreement_section` (the paired-verdict table a human reads to approve or
   reject a migration) has its rendered TEXT asserted by nothing** -- `test_report.py`'s
   fixture feeds it real objects but never checks the output, and `test_cli.py`'s compare
   tests literally discard everything before that section via a string split before
   asserting anything. A column-swap or mislabeled verdict here would be invisible to the
   suite. **Not fixed tonight**, same reasoning as above -- recorded.
6. **Two stale cross-references in this file's own Verdict-bands section**, both caused
   by the same ~205-line insertion shifting everything after it without updating two
   parenthetical line citations (`:183-189` and `:91-93`, both now pointing at unrelated
   passages). **Fixed** -- both now point at the content they actually describe
   (`:391-397` and `:298-301`).
7. **`tests/test_enterprise_experiments.py` had no `sys.path.insert`**, unlike every
   other test file in this repo, and only ran because an alphabetically-earlier test
   module happened to add `src/` to `sys.path` first during full-suite collection --
   `pytest tests/test_enterprise_experiments.py` in isolation raised `ModuleNotFoundError`.
   Found by trying to run it in isolation, not by the audit workflow. **Fixed.**

One resolution cost more than a line edit: fixing #1 and #2 changed `src/evalgen/cli.py`
and `src/evalgen/experiments.py`, and the committed Experiment 5B evidence self-hashes
both files (`report_code_sha256`) as tamper-evidence. Editing either file for **any**
reason -- including a brand-new, unrelated `judge` subcommand -- breaks that pin. This is
the identical shape of the `scorer_sha`-is-repo-HEAD defect Experiment 4 found and
Experiment 5A hit again: a hash pinned to "current HEAD" rather than to "the code that
actually matters" breaks on unrelated commits. Verified before touching anything, not
assumed: the cli.py diff is additive only (+155/-0, a new subcommand, nothing in the
existing call paths), and Experiment 5B's actual committed decision (`FAIL` on both
candidates via `BEHIND` stability, not the newly-guarded `UNDERPOWERED` path; a plan whose
roles were already correct) is unaffected by either fix. The two hashes and the derived
`execution_evidence_sha` were updated with a dated provenance note explaining exactly why
and what was verified first -- not silently, and not by weakening the check.

### Part B: the judge

`src/evalgen/judge.py` -- an independent model adjudicates every item where the harness's
own scorer says an arm disagrees with ground truth. Built the way every metric in this
project is built: a hand-computed expectation
(`tests/fixtures/judge/HAND-COMPUTED.md`, ten constructed raw responses and their exact
aggregate, fixed before the module existed) governs the parsing/aggregation arithmetic,
checked byte-for-byte with no network call. 20 tests, including one this project's other
diagnostics do not have: an AST-based check that `report.py` and `evalharness/compare.py`
never import `judge` at all, enforcing "diagnostic, never a scored dimension" rather than
only asserting it in a docstring the way `evidence.py` does today.

**Judge model:** `google/gemma-4-31b-it`, reasoning disabled, pinned to CoreWeave. Neither
arm's family -- an independent third model, to avoid the judge favouring its own outputs
or a rival's. A pre-implementation probe found the identical temperature-0 request
returned different verdicts from CoreWeave/Novita versus DeepInfra -- the judge is not
immune to the endpoint-changes-the-answer lesson Experiment 4 taught about the primary
arms, so it gets the same discipline: one provider, pinned, recorded.

**What it reviewed.** Experiment 5B's raw run logs are not on this machine -- `out/` is
gitignored and that run executed in a different environment; only its safe, no-payload
evidence JSON is committed. Experiment 6 therefore ran against Experiment 5A's raw data
instead (still on disk locally): all three pairwise comparisons across the reasoning-
enabled `retention_v3` runs. A rerun against 5B once its logs are reachable is a natural
next step, not a design preference.

### Result

262 items adjudicated across three independent pairings, one call per item at
temperature 0, **zero parse failures**:

| Pairing | items | ground_truth_correct | defensible_disagreement | ground_truth_error | unclear |
|---|---:|---:|---:|---:|---:|
| Gemini vs Qwen27B | 83 | 48 (57.8%) | 29 (34.9%) | 6 (7.2%) | 0 |
| Gemini vs Qwen35B | 100 | 66 (66.0%) | 28 (28.0%) | 6 (6.0%) | 0 |
| Qwen27B vs Qwen35B | 79 | 50 (63.3%) | 23 (29.1%) | 6 (7.6%) | 0 |

**None of this is a verdict.** `ground_truth_error_rate` is not a headline the way
`net`/`band` is -- there is no code path anywhere that lets this module's output move a
`PairedVerdict` or a `Decision`, checked by the AST test above, not only claimed here.

**Deduplicated across all three pairings, nine distinct (item, dimension) flags, four
cross-validated by all three independent comparisons reaching the same conclusion about
the same ground-truth cell**: `RET-85 [call_result]`, `RET-94 [call_result]`,
`RET-100 [call_result]`, `RET-59 [reason]`. The other five surfaced in only one pairing
each, which is expected -- an item only becomes a disagreement to review when the two
arms being compared don't already agree, so a flag's absence from a pairing is not
evidence against it.

~~**The strongest candidate, on inspection: `RET-85`.** All three pairings, same argument,
independently reasoned each time: ground truth is `save`, but the customer cancels the
home internet and TV box outright and only the mobile line is left undecided -- "the
agent did not save any service that was being cancelled." This is the same shape of catch
RET-11 was: a plausible, specific, transcript-grounded objection to a label, worth a
human reading it before anything about the fixture moves.~~ **WRONG, and hand-checked as
wrong on 2026-08-08 -- see the addendum at the foot of this experiment.** RET-85's `save`
sits on the Postpaid row of a three-row `multislot` call, and `prompt.py:4397` counts
indecision as `save` in as many words; the judge quoted the exact indecision phrase and
concluded the opposite, because it was shown the rule's *citation* and never its *text*.
Kept struck through rather than deleted, per this file's convention, because "the flag
three independent pairings agree on" turning out to be the same mistake made three times
is precisely the lesson. **Nothing was changed on the
strength of this** -- that is the entire point of keeping the judge a diagnostic.

**`RET-59 [reason]`'s flag looks like a judge miss, not a ground-truth defect.** Ground
truth is `customer reason`; the judge argues the customer explicitly declines to give one
so the label is wrong. But `customer reason` reads, from every other place it appears in
this pack's vocabulary, as the residual class for exactly a customer who declines to
state a specific reason -- the judge has no access to `VOCABULARIES.md`'s class-boundary
arbitrations and re-derived the boundary from the transcript alone, incorrectly. Recorded
as a concrete limitation: **this judge knows the transcript and the cited production
rule, and nothing else about this pack's own settled conventions.**

**The judge is not perfectly self-consistent, and that is visible in the raw output, not
hidden.** `RET-129`'s rationale reasons through the transcript, writes *"the ground truth
correctly identifies that the attempt to downsell to prevent cancellation failed to
close. Therefore, the ground truth is correct,"* and then reports
`verdict: ground_truth_error` anyway -- the enum field and the model's own prose disagree
within one response. `RET-98`'s rationale is similarly tangled, arguing both that the
ground truth is "the most erroneous" and that a rival label "might be incorrect" in the
same breath. **The lesson: read the rationale before trusting the verdict field.** A
future version that scored the judge only on its enum output would silently launder this
kind of self-contradiction into a clean-looking count.

### Recommended next steps

1. **Have a human read `RET-85` against the transcript and `prompt.py:4392`/`:4394-4395`
   before anything is decided.** Falsified if a native reading finds the mobile line was
   already at risk of cancellation too, which would make `save` defensible after all.
2. **Build the mocked-client test coverage for `cmd_qualify` and `cmd_experiment_run`'s
   three untested gates** before either is relied on again for a real qualification run.
   Falsified if a deliberately-broken gate (inverted lock check, wrong sha) is shown to
   already fail loudly some other way this audit missed.
3. **Add a content assertion to `test_report.py` for `_disagreement_section`'s rendered
   text**, not just that `render()` does not crash. Falsified if the existing tests are
   shown to already cover this some other way this audit missed.
4. **Re-run the judge against Experiment 5B once its raw logs are reachable.** The
   reasoning-off, production-shaped comparison is the more decision-relevant one; tonight's
   run against 5A's reasoning-enabled data is what was available, not what was preferred.
5. Everything else still waits on `RECONCILED`.

### Output files

| What | Path |
|---|---|
| Judge module, hand-computed fixture, 20 tests | `src/evalgen/judge.py`, `tests/fixtures/judge/HAND-COMPUTED.md`, `tests/test_judge.py` |
| Judge reports, all three pairings | `out/reports/judge-e6-gemini-vs-27b.{json,txt}`, `judge-e6-gemini-vs-35b.{json,txt}`, `judge-e6-27b-vs-35b.{json,txt}` |
| Full audit findings and verification transcripts | this file, and the workflow journal referenced in the session log |
| Regression tests for every fixed audit finding | `tests/test_enterprise_experiments.py`, `tests/test_testset_pack.py` |

**Cost of Experiment 6: ~$0.02** (262 judge calls at $0.00005-0.00008 each). The audit
itself spent no API budget -- 21 agents reading and re-deriving from the committed
repository, zero model calls against OpenRouter.

Item keys used a local placeholder `EVAL_HARNESS_KEY_HMAC`; they do not resolve inside
True's systems.

### Addendum (2026-08-09): the flags were hand-checked, most were wrong, the cause was found, fixed, and measured

**The hand-check (2026-08-08).** The four cross-validated flags above were read against
the actual ground truth and the actual production rule text. Three of the four were
judge errors, and their three-pairing agreement was not corroboration -- it was one
mistake made three times:

| Flag | Hand ruling | Why |
|---|---|---|
| `RET-85 [call_result]` | judge error | `save` sits on the Postpaid row of a 3-row multislot call; `prompt.py:4397` counts indecision as `save` in as many words. The judge quoted the exact indecision phrase and concluded the opposite. |
| `RET-59 [reason]` | judge error | The judge argued `customer reason` was wrong *because* the customer refused to give a reason; `prompt.py:4372` defines the class as exactly that refusal. Backwards. |
| `RET-94 [call_result]` | judge error | The judge read `undefined` as "unresolved"; `prompt.py:4399` defines it as out-of-retention-scope. RET-94 is a service call, correctly `undefined`. |
| `RET-100 [call_result]` | genuinely arguable | Agent says "may I call back tomorrow", customer agrees. `prompt.py:4397` lists agent-will-contact-later under `save`; `:4398` covers no-final-decision as `unknown`. A real, unarbitrated boundary. |

**The root cause was this experiment's own design defect**: `build_judge_prompt` sent
the rule *citation* (`customer reason: prompt.py:4372`) and never the rule *text*, so
the judge re-derived every class boundary from common sense -- and this vocabulary is
deliberately counterintuitive exactly where disagreements concentrate. The limitation
paragraph above ("the judge knows the transcript and the cited production rule") was
itself too generous: the judge never knew the cited rule either, only its address.

**The fix (2026-08-09).** `judge.py` now resolves every cited `file:line` against the
tracked `production-reference/` tree and quotes the text verbatim in the prompt
(`resolve_rule_text`, hand-computed expectation in
`tests/fixtures/judge/HAND-COMPUTED.md`'s addendum, written before the code; default ON
in `evalgen judge`, `--no-rule-text` reproduces the old prompt; unresolved fragments are
counted in the report, never dropped).

**The measurement.** Because the judge module had also been rewritten in the interim
(stricter response validation, call-cluster units), the old numbers above are not
comparable to any new run. So the re-run is an A/B under today's code with the prompt as
the only variable: all three pairings, pointer-only vs rule-text, 270 units each,
$0.080 total.

| | pointer-only | rule-text |
|---|---:|---:|
| decisive opinions | 249 | 230 |
| ground_truth_correct | 122 (49.0%) | 135 (58.7%) |
| defensible_disagreement | 95 (38.2%) | 77 (33.5%) |
| **possible ground-truth error** | **32 (12.9%)** | **18 (7.8%)** |
| invalid responses (byte-exact evidence gate) | 21 | 40 |
| distinct flagged (item, dimension) | 17 | 8 |
| rule parts resolved / unresolved | — | 524 / 0 |

95 of 270 rows changed verdict. The largest flag-affecting move is
`ground_truth_error -> ground_truth_correct` (14 rows).

### The above was over-claimed. A four-reviewer adversarial pass found four defects in it (2026-08-09)

Every tabulated number survives; the **inferences drawn from them do not**. Recorded
here in full rather than quietly amended, because "I fixed the judge and measured that it
worked" is exactly the kind of self-assessment this file exists to distrust.

**1. The n is inflated 2.5x, and the effect does not clear this repo's own alpha.** The
270 rows per mode are **107 distinct judgment units**, each re-judged in 2 or 3 pairings
(56 units x3, 51 x2). Treating replicates as independent is the error `cli.py:25-31`
already refuses for the aggregate metrics table. Collapsed to one verdict per distinct
unit by majority: **15 flags -> 7**, discordant `d=12`, exact two-sided **p = 0.0386**.
Against this project's own `alpha = 1/64 = 0.0156`, and its own band rule (`band(12) =
±10`, observed `net = -8`), the prompt A/B reads **INDISTINGUISHABLE**. The earlier
`p = 0.0094` was computed on the inflated row count and is withdrawn.

**2. The run contains an accidental placebo arm that undercuts "prompt was the only
variable."** 8 of 270 rows received a **byte-identical request in both modes** (equal
`request_sha256`): their labels have no `rule_<dim>:<label>` key, so `bool([])` at
`judge.py`'s `with_text` falls back to the pointer-only prompt. **4 of those 8 flipped
verdict anyway**, at `temperature=0, seed=0`, same provider. A ~50% flip rate on
identical input means an unknown share of the 32 -> 18 delta is resampling noise rather
than the prompt, and it is the first measurement this project has of judge
self-inconsistency -- previously listed only as an unmeasured limitation.

**3. "All three drop to zero flags" was false.** Counted per item across all dimensions
and pairings: `RET-94` **4 -> 2**, `RET-19` **3 -> 2**, `RET-59` **4 -> 1**. None
reached zero. Worse, the fix *created* flags on two items an independent re-derivation
ruled ground-truth **correct**: `RET-98` **0 -> 3** and `RET-129` **2 -> 3**. The
corrected queue below is therefore not a clean improvement over the old one.

**4. The prompt only ever quotes the rule for the ground-truth label, never the
competing one.** `_rule_entries_for` looks up `rule_<dimension>:<label>` in the *item's
own* `rules` dict, which is authored per ground truth, so a label the model proposed but
the pack does not assert has no entry and gets no text. The judge is shown why the
reference label might be right and nothing about why the alternative is wrong -- a
structural asymmetry that plausibly explains defect 3. Related: `#2` second-citation
keys (97 across the packs, 22 citing lines the base citation does not cover) are never
matched, so `RET-37` is quoted `prompt.py:4342` while the pack also cites `:4345`, the
CRITICAL clause that would settle it.

**What survived the review**, verified independently: the invalid-response confound is
clean (of 27 rows that went decisive -> invalid, **zero** were pointer flags, against a
12.9% base rate); the resolver's arithmetic is correct (79 distinct file:line fragments
across all three packs, all parse, all resolve, no off-by-one, fixture lines byte-exact);
`RULE_SOURCE_FILES` is complete; and the judge remains isolated from every verdict path.
A separate MAJOR data-safety finding -- `rule_text.root` put the operator's absolute path
and OS account name into the *shareable* export, which `assert_shareable_payload` does
not reject -- was **fixed** with a regression test.

**What the fix costs, stated rather than hidden**: invalid responses rose 21 -> 40.
Longer prompts make Gemma's `cited_span` discipline worse, and the byte-exact evidence
gate correctly refuses those responses rather than counting them.

**Honest status of the fix.** Quoting the rule text is still right on the merits --
defect 4 is an argument for quoting *more* rule text, not less, and the three hand-ruled
false flags do fall. But **the claim that this run demonstrates the fix works is
withdrawn**: at the correct n it is INDISTINGUISHABLE, and a placebo arm inside the run
flips half the time on identical input. Demonstrating it needs the asymmetry in defect 4
fixed first, then replicates per unit so judge instability can be separated from the
prompt.

**Human-review queue, with the caveat that it is now contested**: `RET-100
[call_result]` (3/3 both modes; the `prompt.py:4396`-vs-`:4398` callback boundary, and
one reviewer argues `:4396` puts it squarely under `save`, making it a possible real
ground-truth error rather than merely arguable), `RET-98 [call_result]` and `RET-129
[reason]` (both 3/3 in rule-text mode, both independently ruled ground-truth **correct**
on re-derivation -- i.e. probably false flags created by defect 4). Nothing was changed
in any fixture on the strength of any of this.

**Consequence for Experiment 7's judge numbers**: its 38 flags were produced by
pointer-only prompts under the same rewritten module. Given the review above, the
honest statement is weaker than "inflated ~2x": the flag count is **unreliable in an
unquantified direction**, since rule text both removes false flags (RET-94/19/59) and
creates them (RET-98, RET-129) under defect 4. Re-deriving them is still worth doing --
after defect 4 is fixed, not before. Experiment 7's raw run directories are not on this
machine (gitignored `out/`, executed elsewhere), so that re-derivation has to happen
where that data lives.

Re-run outputs: `out/reports/judge-e6b-<pairing>-{pointer,ruletext}.{json,txt}` (+ raw
call journals). Cost of the addendum's runs: **$0.080**.

### Second addendum (2026-08-09, later): defect 4 fixed, replicates added, decision-grade re-run clean

All four review defects were fixed the same day, with the arithmetic hand-computed
first (fixture, second addendum): every label in play now gets rule text -- the item's
own citations *including the previously-invisible `#N` keys*, else the pack-level
citation union, else a visibly marked no-text line; the silent pointer-fallback placebo
path is structurally impossible (`rule_texts is not None`, and a regression test proves
zero request-sha overlap between modes); and the judge gained `--repeats` with
strict-majority unit verdicts where a tie reports `no_majority` rather than picking.

The decision-grade run: 3 pairings x 3 replicates, rule text on -- **270 units, 810
calls, $0.148**. Gate: **zero transport errors, zero identity mismatches, zero
silent-fallback units, all 1,395 rule parts resolved.**

| Unit-majority result | count |
|---|---:|
| ground_truth_correct | 154 |
| defensible_disagreement | 64 |
| **ground_truth_error (flagged)** | **12 across pairings, 5 distinct** |
| unclear (majority-invalid) | 39 |
| no_majority | 1 |
| flipped units (any disagreement across replicates) | 49 (18.1%) |

The 18.1% unit flip rate independently reproduces the placebo arm's ~50%-of-8 estimate
at proper scale, and is now measured rather than accidental. Invalid responses ran
14.8% of records; majority aggregation absorbed them (only 1 unit lost to no_majority).

**The flag queue, replicated and majority-voted** (this supersedes every earlier queue):

| Flag | Pairings | Reading |
|---|---|---|
| `RET-100 [call_result]` | 3/3 | The known `prompt.py:4396-4397` vs `:4398` callback boundary. Survives every prompt design and replication. The strongest candidate for a real ground-truth review. |
| `RET-129 [reason]` | 3/3, unanimous votes | Persists with full rule text quoted. The judge engages `prompt.txt:43`'s refused-or-couldn't-provide clause against the granted discount. Contested by the 2026-08-09 review's own re-derivation (Rule A) -- precisely the kind of item the queue exists for. |
| `RET-46 [reason]` | 3/3 | **New under competing-label text**, which is the fix working as designed: the judge now sees `dissatisfied service`'s own "สาขาไม่ทำให้" example beside `down sell not success` and argues the shop refusal fits both. A genuine class-boundary question. |
| `RET-89 [call_result]` | 2/3 | Carryover from earlier runs; weaker support. |
| `RET-98 [call_result]` | 1/3 at 2/3 votes | Collapsed from 3/3 single-shot to marginal once the competing label's rule text arrived -- consistent with the review's ruling that it was a defect-4 artifact. |

`RET-94`, `RET-19`, `RET-59` -- the three hand-ruled judge errors -- produce **zero
majority flags** in this run.

Outputs: `out/reports/judge-e6c-<pairing>.{json,txt}` + raw call journals. Suites at
this state: 688 passed / 12 skipped standalone, 699 / 1 differential.

---

## Experiment 7 — decision-grade three-model repeat (EXECUTED)

**Date:** 2026-08-08
**Question:** under one fixed non-reasoning Retention workload, can either Qwen candidate
pass the paired quality and exact-repeat stability gates required to replace the Gemini
reference?

**Answer:** no. Retain Gemini as the reference for the next phase. Qwen3.6 27B is
interesting on call result and price but failed stability; Qwen3.6 35B-A3B failed all
three paired quality dimensions and stability. `RECONCILED: NO` remains binding.

### What was run

The plan fixed the 138-call/150-product-row synthetic `retention_v3` pack, prompt
`v9_16_base` (`968a2974f0ce462e0f1ad815c9434252420a677766fa23775a69a691f3db4eee`),
schema, three replicates, temperature 0, top-p 0, seed 0, 8,000 output-token cap,
reasoning off, one attempt, provider fallback off and concurrency 4. Aggregate F1 uses
replicate 1. The decision uses paired call clusters and an exact three-replicate stability
gate. Load probes were deliberately excluded.

Provider qualification made 120 bounded calls across 20 advertised provider names.
Twelve provider/model combinations qualified and eight were request-incompatible. The
selection rule chose historical full-run reliability first, then projected price; it did
not inspect qualification labels or predictions.

| Arm | Model | Selected provider | Full calls | Parse valid |
|---|---|---|---:|---:|
| reference | `google/gemini-2.5-flash` | Google | 414 | 414 |
| candidate | `qwen/qwen3.6-27b` | Chutes | 414 | 414 |
| candidate | `qwen/qwen3.6-35b-a3b` | AkashML | 414 | 414 |

The exact executed locked-plan SHA was
`ea02cfacad27aea58c486213f0cfba304ca00b902050b983ed27f9cca244d3e1`.
The committed reproduction plan is intentionally `draft`: private qualification paths
were removed so a fresh clone can validate it without ignored artifacts.

### Result

| Metric | Gemini 2.5 Flash | Qwen3.6 27B | Qwen3.6 35B-A3B |
|---|---:|---:|---:|
| Call-result weighted F1 | 0.955 | **0.969** | 0.901 |
| Reason weighted F1 | **0.823** | 0.774 | 0.701 |
| Product weighted F1 | **0.960** | 0.942 | 0.888 |
| Descriptive mean (not a gate) | **0.913** | 0.895 | 0.830 |
| Unstable calls | **0/138** | 129/138 | 130/138 |
| Generation cost | $0.475916 | $0.362492 | $0.211117 |
| Latency p50 / p95 | 1.830 / 3.227 s | 6.950 / 20.496 s | 2.779 / 8.995 s |

F1 alone would hide the binding result. Qwen3.6 27B's call-result edge was only one
net paired call among five discordances (`UNDERPOWERED`), while its stability comparison
was -129/129 (`BEHIND`). Its reason comparison was -6/36 but inside the exact band
(`INDISTINGUISHABLE`), and product was underpowered at -2/2. Overall: `FAIL_STABILITY`.

Qwen3.6 35B-A3B was `BEHIND` on call result (-11/15), reason (-27/41), product
(-10/10) and stability (-130/130). Overall: `FAIL_QUALITY_AND_STABILITY`.

The operational ranking points the same way for this hosted run: Gemini was fastest and
stable. These endpoint measurements do not predict internal-GPU throughput. Qwen3.6
35B-A3B was cheapest, but operations cannot rescue a failed quality gate.

### Independent advisory judge

Gemma 4 31B IT on CoreWeave reviewed every scorer disagreement across all three pairings,
with reasoning off. It returned 360 opinions, 314 usable: 141 said ground truth was
correct, 135 said both predictions were defensible, and 38 flagged a possible
ground-truth error. Forty-six responses were unusable parse results; there were zero
transport or identity errors. The judge does not alter model scores or select a winner.

| Pairing | Opinions | Usable | GT correct | Defensible | Possible GT error |
|---|---:|---:|---:|---:|---:|
| Gemini vs Qwen3.6 27B | 99 | 88 | 32 | 44 | 12 |
| Gemini vs Qwen3.6 35B-A3B | 131 | 115 | 57 | 46 | 12 |
| Qwen3.6 27B vs 35B-A3B | 130 | 111 | 52 | 45 | 14 |

### Recommended next steps

1. **Human-review the 38 possible-ground-truth-error flags.** Falsified as a priority
   if domain owners find they are all judge misunderstandings; record that result too.
2. **Rerun the same application contract on the internal GPU.** The hypothesis that
   hosted routing caused Qwen instability is falsified if exact-answer instability
   remains materially behind Gemini on the controlled internal runtime.
3. **Reconcile on one approved production-shaped labelled batch.** The synthetic result
   is falsified as representative if production class mix or transcript structure moves
   paired verdicts beyond their exact bands.
4. **Only then consider prompt/model adaptation as a new experiment.** Any change to the
   prompt, reasoning regime, schema, retries or provider is a new arm, not a repair to E7.

### Output files

| What | Path |
|---|---|
| Team-readable result and methodology | `docs/experiment7-results.md` |
| Zero-call reproduction plan | `experiments/retention-e7.plan.json` |
| Safe machine-readable aggregate | `experiments/evidence/retention-e7/summary.json` |
| Raw runs, restricted judge bundles and local HTML/XLSX | `out/experiments/retention-e7/`, ignored |

The three full arms cost $1.049524; the judge cost $0.054399; qualification reported a
$0.111387 lower bound. Observed lower-bound total: approximately **$1.215310**. No raw
model response, transcript, credential or private judge rationale is committed.

---

## Experiment 8 — error severity: what the all-or-nothing scorer cannot say (DIAGNOSTIC)

**Not a model comparison.** This is a re-read of Experiment 5A's committed run
directories through a new diagnostic, plus a judged remainder. It changes no score, joins
no verdict, and does not touch `docs/migration-decision-2026-08-07.md`. Goal contract:
`docs/severity-plan-2026-08-09.md`. Expectation hand-computed first:
`tests/fixtures/judge/SEVERITY-HAND-COMPUTED.md`.

### The question

The scorer is all-or-nothing. Answering `promotion related` where the truth is `save cost`
— two classes the production prompt separates with a single CRITICAL line — scores the
same zero as answering `network`. Experiment 3 measured by hand that 39 of the incumbent's
57 whole-item failures were *over-labelling*, a categorically milder defect, and no report
in this repository could say so. Experiment 8 attaches a category to every wrong unit.

### Design

Seven of eight branches are set arithmetic and cost nothing; only `substitution` (the
answer both dropped and asserted classes) reaches a model, which is asked one binary
question — near or cross family — behind two byte-exact evidence gates. Dimensions:
`call_result` and `reason`. `product` is out of scope with a stated reason. Unit grain is
the scored **row**. **No significance test is computed**: these units are not independent.

**The arms are the Experiment 5A runs, so the reasoning-regime confound applies.** Gemini
burned 0 reasoning tokens; Qwen 27B burned 2,379,369 and Qwen 35B-A3B 2,620,339. Each
arm's own profile stands; a cross-arm reading repeats Experiment 4's confound. The report
prints this warning above its own table rather than leaving it in a run log.

### Deterministic result (zero model calls, byte-identical across three independent runs)

Denominator is that arm's own wrong units. Replicate 1 of each arm.

| | Gemini 2.5 Flash | Qwen3.6 27B | Qwen3.6 35B-A3B |
|---|---:|---:|---:|
| **`reason` wrong units** | 65 | 38 | 49 |
| over-labelling | **45 (69.2%)** | 18 (47.4%) | 19 (38.8%) |
| under-labelling | 2 | 2 | 2 |
| substitution (judged) | 4 | 7 | 10 |
| missing row | 5 | 5 | 6 |
| unsupported claim | 9 | 6 | 6 |
| invalid output | 0 | 0 | **6** |
| **`call_result` wrong units** | 16 | 15 | 24 |
| substitution (judged) | 4 | 2 | 4 |
| missing row | 5 | 5 | 6 |
| unsupported claim | 7 | 8 | 8 |
| invalid output | 0 | 0 | **6** |
| **fabricated class (any dimension)** | **0** | **0** | **0** |

**Four findings, in order of how much they change what a reader does.**

1. **Over-labelling is the incumbent's dominant failure mode, and this reproduces
   Experiment 3 independently.** 45 of 65 `reason` errors (69.2%) are the right answer plus
   unsupported extras. Experiment 3 counted 39 of 57 by hand on the *v2* pack; this is a
   different pack, a different code path, and no shared arithmetic. Two independent
   derivations of the same dominant pattern.
2. **A quarter of what the scorer calls "wrong" is not a labelling error at all.** Across
   all three arms, 5–6 units per dimension are `missing_output` (ground truth has a product
   row the arm never emitted) and 7–8 are `unsupported_claim / no_ground_truth_row` (the
   arm emitted a row ground truth does not have). Both are **row-alignment** failures, and
   the counts are near-identical across arms — so they are a property of the pack and the
   prompt, not a discriminator between models. They are currently inside every F1 number
   this project has reported.
3. **Only Qwen3.6 35B-A3B produced unparseable output** — 6 units in each dimension. A
   decoding-reliability signal the F1 table cannot express, and consistent with the
   stability failure Experiments 5B and 7 recorded for that arm.
4. **`fabricated_class` is zero everywhere.** Constrained decoding held on all three arms.
   Reported as evidence, never as proof the check is unnecessary — the category exists
   because this repository has recorded providers that do not honour `strict: true`.

### `call_result` cannot produce a mis-scoping category

A single-label dimension's sets never hold more than one element, so `over_labelling` and
`under_labelling` are unreachable there by construction. `mis-scoping: 0` on that dimension
says nothing about the arm. Recorded in the fixture before any run was interpreted.

### Judged remainder: the near/cross layer does not work, and that is the result

**Goal-contract criterion 5 is NOT met, and no family conclusion is drawn from these
runs.** Criterion 5 required zero transport errors and zero identity mismatches. Eight
runs were executed across four code revisions and the criterion was met twice, early;
the CoreWeave endpoint serving `google/gemma-4-31b-it` then degraded and stayed degraded.
Recorded rather than retried into a pass — the pin is a term in the result, and switching
provider mid-experiment would be a new arm, not a repair.

| run | calls | transport errors | responses that arrived | failed an evidence gate |
|---|---:|---:|---:|---:|
| first pairing, first attempt | 51 | 0 | 51 | 30 (58.8%) |
| second pairing, first attempt | 66 | 0 | 66 | 42 (63.6%) |
| first pairing, after memoisation | 48 | 0 | 48 | 29 (60.4%) |
| second pairing, after memoisation | 60 | 19 | 41 | 31 (75.6%) |
| first pairing, degraded | 51 | 33 | 18 | 15 (83.3%) |
| second pairing, degraded | 64 | 38 | 26 | 15 (57.7%) |
| first pairing, final | 51 | 48 | 3 | 3 |
| second pairing, final | 66 | 43 | 23 | 13 (56.5%) |
| **total** | **457** | **181** | **276** | **178 (64.5%)** |

**The one thing measured cleanly, and it is the decisive one: the judge fails the
byte-exact evidence gates on roughly two thirds of the responses that arrive.** 178 of 276
across eight runs, four code revisions and two pairings; the per-run rate sits between
56.5% and 75.6% wherever the sample is larger than a handful. That rate is a property of
the responses, so it survives both the endpoint degradation and an aggregation defect
found mid-experiment (below). The judge is asked to quote one transcript span and one line
of the production rule text **it was handed in the same prompt**, verbatim. It paraphrases
instead, about two times in three.

**A defect found by review, mid-experiment, and what it invalidated.** The first
aggregation counted gate-rejected responses as `unclear` votes, so units were being decided
by responses that had demonstrated nothing. Under that defect the first pairing reported
11 of 17 units "flipping" across identical calls at temperature 0; that figure is
**withdrawn** — most of the apparent flipping was invalid responses alternating, not the
judge changing its mind. `judge.summarize_judgments` had always excluded both parse errors
and non-completed executions; the severity collapse excluded only the second. Fixed, tested,
and the runs regenerated rather than reinterpreted.

**Conclusion for the judged layer.** With two thirds of responses rejected before they can
vote, the near/cross question as posed does not produce a measurement, and none is claimed.
The deterministic layer above needed no model at all and is byte-identical across three
independent runs. That asymmetry is the finding: on this task the value came from the set
arithmetic, and the model-judged extension did not clear its own evidence bar.

**Cost.** 457 calls at an observed lower bound of approximately US$0.08 across every
attempt, including the abandoned and superseded ones. Raw journals, private reports and
shareable exports stay in gitignored `out/`; nothing from them is committed.

### What would have to change before asking this question again

1. **Relax gate 2 from a quoted line to a cited line number**, and check the number against
   the citations the prompt supplied. The evidence requirement stays — the judge still has
   to point at a specific rule — but copying a long Thai source line verbatim stops being
   the binding constraint. Preregister it as a change and measure the rejection rate again.
2. **A different judge model or endpoint**, chosen and pinned before the run, not after
   seeing a rate one dislikes.
3. **Do neither until the ground-truth workbook arrives.** The deterministic profile is
   already decision-relevant and cost nothing; the judged layer is second-order to
   `RECONCILED: NO`.

---

## Experiment 9 — phase-two prompt tuning, AUTHORISED (pre-registration, no run yet)

**Nothing has been tuned or run at the time this section is written.** It exists so the
target, the rule and the split are on record before any of them can be chosen to fit a
result. `docs/severity-plan-2026-08-09.md` is the plan; this is the pre-registration.

### What is authorised, and what is not

`src/evalgen/prompts/manifest.json`'s `phase_two_protocol` has stood at *"protocol only;
no model-specific tuning is authorized in Experiment 5"* since it was written. That
sentence is scoped to Experiment 5 and remains true; **Experiment 9 authorises
model-specific tuning for the first time**, under that protocol's five requirements:

| # | Requirement | How it is met |
|---|---|---|
| 1 | new prompt id with `parent_id` and `target_models` | `v9_16_q1`, parent `v9_16_base`, `target_models: ["qwen/qwen3.6-27b"]`. `Variant` gained those fields; before this they were hardcoded and the requirement was not expressible. |
| 2 | document every change before running it | the `Edit.why` on each edit, plus this section |
| 3 | keep the untuned baseline and report it beside | the base arm is re-run on the same slice, same pin, same decoding |
| 4 | development slice, locked holdout | `tests/fixtures/testsets/retention_v3.split.json` — 49 tune / 89 holdout, drawn and committed **before** the prompt was written |
| 5 | never compare tuned and untuned as though prompt identity were held | `compare --prompts-may-differ` prints a configuration-comparison banner and refuses unless the prompt is the *only* contract difference |

**The phase-one prompt library is not touched.** `manifest.json`'s sha is pinned as an
asset in the executed `retention-e5` and `retention-e7` plans, so a phase-two prompt is
catalogued in `manifest.phase_two.json` beside it. Editing the frozen file would have made
two committed plans describe something other than what was run.

### The target: stability, not score

Experiment 7's paired gate put Qwen3.6 27B at UNDERPOWERED (+1/5) on call result,
INDISTINGUISHABLE (−6/36) on reason, UNDERPOWERED (−2/2) on product, and
**BEHIND (−129/129) on stability**. It fails one gate and it is not the score. A prompt
tuned to raise F1 would optimise a variable that is already not losing.

The 2026-08-09 decomposition (section 4b of any compare report, `evalgen.stability`)
measured that **107 of Qwen 27B's 138 unstable calls — 77.5% — never change a label the
scorer reads.** `recommendation` moves on 100% of them, `keyword` on 91%, and
`call_event_detection` on 23%: free text the production schema asks for and no metric
consumes. That is the target.

### Pre-registered success criteria

Within-arm, Qwen 27B on `v9_16_base` versus `v9_16_q1`, same items, same provider pin,
same decoding, 3 replicates.

| # | Endpoint | Requirement |
|---|---|---|
| 1 | **primary** — raw instability | strictly lower on `q1` than on `base`, on the holdout |
| 2 | **guard** — scored quality | `not BEHIND` on any of the three dimensions at alpha = 1/64 |
| 3 | **guard** — parse validity | ≥ 99%, the existing reliability rule |

Endpoint 2 is the one that makes this falsifiable: a prompt that buys stability by making
the model answer more blandly would satisfy endpoint 1 and fail here.

**Tuning happens on the 49 tune items only.** The holdout is evaluated **once**. If it is
evaluated more than once, the number of times is reported next to the result.

### Recorded before the fact, because they limit what the result can mean

- **The holdout is fresh-eyes at best, not clean.** Every item ships an `expected_failure`
  string naming the exact wrong answer, and `EXPERIMENTS.md:401` records that the
  phase-one items were already used to select the `v9_16_e1` edits. Any figure here is an
  **upper bound**.
- **Synthetic Thai.** The pack was authored inside this project. A prompt tuned on it is
  tuned to this pack.
- **A configuration comparison, never a model comparison.** If `Gemini + v9_16_base` is
  later compared with `Qwen + v9_16_q1`, that is a comparison of two deployable
  configurations. Per-model prompts are operationally acceptable to the app owners, which
  is what makes the comparison meaningful — and it still may not be described as
  "Qwen beats Gemini".
- **Stability is measured by the pre-registered gate, at the raw level.** The scored-level
  decomposition is a recorded diagnostic and does **not** become the endpoint here. Its
  direction on this comparison is already known, and a measure adopted after its answer is
  known is not a measure.

### Experiment 9, tuning iterations on the development slice (2026-08-09)

Qwen3.6 27B, Chutes, reasoning off, temperature 0, 3 replicates, the **49 tune items
only**. The holdout has **not** been touched. 441 calls across three arms.

| arm | prompt | raw-unstable | scored-unstable | `recommendation` chars (mean) |
|---|---|---:|---:|---:|
| control | `v9_16_base` | **44 / 49** | 7 | 217 |
| iteration 1 | `v9_16_q1` | **44 / 49** | 5 | 61 |
| iteration 2 | `v9_16_q2` | **25 / 49** | **13** | 89 |

**Iteration 1 failed the primary endpoint, and failed it informatively.** `q1` asked for
a short, plain, repeatable sentence. The model *complied* — mean `recommendation` length
fell from 217 characters to 61 — and raw instability did not move by a single call. On
RET-01 replicates 1 and 2 returned the same sentence and replicate 3 returned a different
one. **Shortening free text only makes the variation shorter.** An instruction to be
repeatable is not a mechanism for being repeatable.

**Iteration 2 moved it, by removing the choice rather than narrowing it.** `q2` makes
`recommendation` a deterministic function of `call_event_detection`, which the model has
already selected. Raw instability fell 44 → 25 of 49, and `recommendation` stopped moving
on all but 2 of the cosmetically-unstable calls (from 36).

**And it appears to have cost something the primary endpoint cannot see.** `q2`'s
**scored** instability rose from 7 to 13 of 49 — the labels the scorer actually reads
became *less* stable while the free text became more stable. That is not one of the three
pre-registered endpoints, and it is reported here as an observation with its `n`, not as a
result: 7 against 13 on 49 items is a difference two items wide in each direction of the
kind this project has repeatedly watched evaporate.

**The guard endpoint could not be evaluated.** Paired quality on the tune slice came out
`UNDERPOWERED` on all three dimensions for both iterations (discordant counts of 0, 1 and
5 against the 6 needed at alpha = 1/64). That is the expected and correct outcome on 49
items, and it is the reason the protocol puts the decision on the holdout. **The
weighted-F1 figures printed by `compare` on these runs are diluted** — the subset ran 49
items against the full pack's 150-row ground truth — and are not quoted here.

### The holdout was deliberately NOT spent

`q2` is not yet a candidate worth a one-shot resource. It trades an unscored-stability
gain for an apparent scored-stability loss, and until that trade is understood, evaluating
it on the holdout would consume the only clean measurement available to answer a question
that is not yet well posed.

**Pre-registered now, before the holdout runs:** scored instability joins raw instability
as a reported endpoint of any holdout evaluation. It is being added *before* that data
exists, and the direction observed on the tune slice (worse under `q2`) is recorded above
so that adding it cannot later be read as choosing a metric that flattered a result.

### What the two iterations already establish

1. **A free-text field cannot be made deterministic by instruction.** Iteration 1 obeyed
   its constraint exactly and changed nothing. This is the useful negative result.
2. **It can be made deterministic by construction**, by deriving it from a field the model
   has already committed to — at the cost of the field no longer being advice.
3. **Which means the real question is not a prompting question.** The production schema
   requires `recommendation`, and the stability gate compares the exact structured
   response, so the gate is substantially measuring a field nothing scores. That is a
   schema-and-gate decision for the app owners, not something a prompt can repair.

---

## Experiments 10-14 — Gemini 2.5 Flash vs Gemma 4 12B, and whether a prompt moves the score

Plan and goal contract: `docs/experiments-10-14-plan.md`. Runs 2026-08-10/11.

### The decoding change that governs every number below

The Gemma endpoint **rejects `top_p = 0`** (`top_p must be in (0, 1]`, LiteLLM in front of
vLLM), and both committed plans pin it. So **every arm here runs at `top_p = 1.0`,
including Gemini, which was re-run rather than reused.** These figures are therefore
**not comparable to Experiments 5 or 7**; a difference against those confounds the model
with the decoding. Gemini's numbers below are a fresh reference measured under this
regime.

Endpoint qualified first with `scripts/gpu_endpoint_probe.py`: `response_format`
json_schema `strict:true` is honoured, usage is reported, cost is not, and the 10x
long-context items fit (6,251 of 8,192 tokens).

## Experiment 10 — head-to-head baseline, identical prompt, identical decoding

138 items x 3 replicates x 2 arms = 828 calls. `v9_16_base` on both.

| dimension | Gemini 2.5 Flash | Gemma 4 12B | paired verdict (Gemma vs Gemini) |
|---|---:|---:|---|
| call_result w-F1 | **0.955** | 0.928 | **BEHIND** (-9 of 9 discordant, band +/-9) |
| reason w-F1 | **0.823** | 0.815 | **INDISTINGUISHABLE** (-4 of 36, band +/-14) |
| product w-F1 | **0.960** | 0.946 | **BEHIND** (-6 of 6, band +/-6) |
| parse-valid | 414/414 | 414/414 | |
| N_flip over 3 replicates | **0** | 10 | |
| raw-unstable calls | **0/138** | 79/138 | |
| of which the scorer can see | 0 | **8** | |
| cost (414 calls) | $0.517276 | not reported | |

**Gemma 4 12B loses, narrowly, and not everywhere.** It is BEHIND on `call_result` and
`product` -- both at the minimum discordance the band can resolve, so these are the
weakest possible BEHIND verdicts rather than large gaps -- and **INDISTINGUISHABLE on
`reason`**, the hardest dimension and the one every previous experiment turned on.

**It is dramatically more stable than either Qwen.** 79 of 138 calls vary at all, against
138 of 138 for both Qwen arms, and only **8** of those touch a label the scorer reads,
against 31 for Qwen 27B. On a 12B model that is the surprise of this experiment.

### Where its errors are, measured rather than guessed

`evalgen.severity` on the same run: of Gemma's 70 wrong `reason` units, **47 (67.1%) are
over-labelling** -- the right answer plus unsupported extras. Weighted precision 0.753
against recall 0.928. Precision is the weak side and over-labelling is why. That single
number chose every prompt edit below.

## Experiments 11-13 — three prompt iterations, on the 49-item tune slice only

Tune slice from the committed `retention_v3.split.json`, drawn before any of this. The
holdout was untouched until Experiment 14. Replicate 1, ground truth scoped to the slice.

| # | prompt | what it changes | reason errors | of which over-labelling | call_result errors |
|---|---|---|---:|---:|---:|
| control | `v9_16_base` | -- | 32 | 20 | 13 |
| **E11** | `v9_16_e1` | blanks the worked example's `secondary`/`third` reason values | **20** | **13** | 15 |
| E12 | `v9_16_g1` | e1 + an explicit "only a distinct, client-stated second reason" rule | 20 | 14 | 16 |
| E13 | `v9_16_g2` | g1 + "delete any reason you cannot quote the client saying" | 20 | 13 | 16 |

**The entire gain came from iteration 1, and iterations 2 and 3 added nothing.** Removing
the two filled reason values from the worked example cut reason errors by 37.5%. Then two
successive attempts to state the rule in words -- first as a decision rule, then as a
self-check the model applies to its own draft -- moved the count by zero.

**This is Experiment 9's finding again, on a different model, a different dimension and a
different failure mode.** There, a free-text field obeyed a length instruction exactly and
its instability did not move; only removing the degree of freedom worked. Here, a scored
label obeys nothing it is told about restraint; only removing the example it was copying
worked. Two independent experiments, one lesson: **change what the model is SHOWN, not
what it is TOLD.**

`v9_16_e1` therefore goes to the holdout. It is also the least contaminated candidate
available: it was authored in an earlier phase against v1/v2 measurements and was never
tuned on this pack's tune slice.

**Cost of the search:** 441 Gemma calls, no reported cost. `call_result` drifted 13 -> 15
-> 16 across the iterations, which the holdout is there to test.

## Experiment 14 — the holdout, and the only uncontaminated numbers here

89 locked holdout items x 3 replicates x 3 arms = 801 calls. The holdout was drawn and
committed before any prompt was written and was untouched until this run.

### Gemma 4 12B against Gemini, same prompt, on items neither was tuned on

| dimension | paired verdict (Gemma vs Gemini) | discordant |
|---|---|---|
| call_result | UNDERPOWERED | -2 of 4 |
| **reason** | **INDISTINGUISHABLE** | **+0 of 24** |
| product | UNDERPOWERED | -2 of 2 |

**On the holdout, Gemma 4 12B is statistically indistinguishable from Gemini 2.5 Flash on
all three scored dimensions, at this repository's own alpha of 1/64.** The `reason`
dimension -- the hard one -- came out **dead level, +0 of 24 discordant pairs**.

Experiment 10's BEHIND verdicts on `call_result` and `product` do **not** reproduce here.
Both were at the minimum resolvable discordance on the full pack; on the 89-item holdout
the same comparison is UNDERPOWERED. The honest reading is that the full-pack BEHIND was
real but small, and that this pack cannot resolve a gap that size on a subset. It is not
evidence that Gemma caught up.

### Did the prompt edit generalise? Yes on its target, and it cost the other two

`v9_16_e1` against `v9_16_base`, same model, same items, same decoding:

| dimension | verdict | discordant | band |
|---|---|---|---|
| **reason** | **AHEAD** | **+14 of 18** | +/-10 |
| call_result | **BEHIND** | -7 of 7 | +/-7 |
| product | **BEHIND** | -8 of 8 | +/-8 |

**The tuning generalised.** `reason` clears the band on data the edit was never selected
against -- a genuine AHEAD at alpha = 1/64, which this project has recorded ~~only once~~
**twice** before. *(Corrected 2026-08-11: Experiment 5A recorded two AHEADs on `reason`,
Qwen27B at +26/40 and Qwen35B-A3B at +17/41 -- `EXPERIMENTS.md:864,867`. This one is the
third.)* And it is **not free**: `call_result` and `product` both go BEHIND.

That trade is the entire value of having pre-registered guard endpoints. A report of the
target dimension alone would have called this a clean 37.5% error reduction that
replicated. It did replicate. It also broke two other dimensions.

### Operations, measured on the same runs

| arm | calls | p50 | p95 | max | raw-unstable | scored-unstable | cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash (full pack) | 414 | **1.99 s** | **3.76 s** | 4.77 s | **0/138** | **0** | $0.5173 |
| Gemma 4 12B (full pack) | 414 | 9.62 s | 12.95 s | 22.55 s | 79/138 | 8 | not reported |
| Gemini (holdout) | 267 | **2.12 s** | **3.92 s** | 5.22 s | **0/89** | **0** | $0.3030 |
| Gemma base (holdout) | 267 | 19.56 s | 25.73 s | 33.95 s | 50/89 | 4 | not reported |
| Gemma + e1 (holdout) | 267 | 19.41 s | 25.61 s | 29.76 s | 63/89 | 4 | not reported |

**Latency is Gemma's clearest loss and it is not close**: 5-10x Gemini's p50 on the same
items, and it degraded as the two Gemma arms ran concurrently (9.6 s single-arm, 19.6 s
with two in flight) -- so this is a property of one small shared box, not of the model.
Cost is not reported by the endpoint at all, so no cost comparison is possible; the zeroes
above are absence of data, not free inference.

### What Experiments 10-14 establish, and what they do not

1. **Gemma 4 12B is a serious arm on quality.** Indistinguishable from Gemini on all three
   dimensions on held-out items, from a model roughly a fifth the size of the Qwen
   candidates this project rejected.
2. **It is far steadier than either Qwen** -- 79 of 138 calls vary against 138 of 138, and
   only 8 of those touch a scored label against 31.
3. **Prompt tuning moved the target dimension and generalised** -- AHEAD, +14 of 18, on a
   locked holdout -- **and cost two other dimensions.** Anyone quoting the `reason` gain
   must quote the other two.
4. **A prompt edit works by changing what the model is shown, not what it is told.** Two
   experiments, two models, two dimensions, same answer.

**Not established:** anything about production. Synthetic Thai, authored in this project,
every item carrying a written description of the wrong answer. The holdout narrows the
contamination; the same author wrote both slices, so it does not remove it. Latency was
measured on one shared box under self-inflicted contention. And `RECONCILED: NO`.

---

## Experiment 15 — Gemini 2.5 Flash vs Kimi K3

Plan: `docs/experiments-15-16-plan.md`. 414 calls, `v9_16_base`, temperature 0,
top_p 1.0, seed 0, 3 replicates, `moonshotai/kimi-k3` pinned to **DeepInfra (bf16)** after
a probe in which Fireworks returned 3/3 transport errors. Zero reasoning tokens, which is
production's regime. Gemini's arm is Experiment 10's, reused: same workload contract, so
`compare` accepts the pairing without an override.

| dimension | Gemini 2.5 Flash | Kimi K3 | paired verdict | discordant |
|---|---:|---:|---|---|
| call_result w-F1 | 0.955 | **0.972** | INDISTINGUISHABLE | +1 of 7 |
| reason w-F1 | **0.823** | 0.806 | INDISTINGUISHABLE | -3 of 37 |
| product w-F1 | **0.960** | 0.943 | UNDERPOWERED | -3 of 3 |
| parse-valid | 414/414 | 414/414 | | |
| raw-unstable | **0/138** | 138/138 | | |
| of which scored | **0** | 25 | | |
| p50 / p95 latency | **1.99 s / 3.76 s** | 16.77 s / 46.31 s | | |
| cost, 414 calls | **$0.517** | **$11.750** | | |

**Kimi K3 is statistically level with Gemini on quality.** No dimension separates them at
alpha = 1/64, and its `call_result` weighted precision is **1.000** -- the only perfect
precision figure any arm has posted in this project.

**Everything else is worse, and two of them by a lot.**

- **Cost: 22.7x Gemini** for the same 414 calls. Two things drive it: a higher price per
  token, and **a tokenizer that needs 2.59x the input tokens for identical Thai text**
  (3,205,227 against 1,237,746). The second is invisible in any published price comparison
  and is a property of Thai text specifically.
- **Latency: 8.4x on p50 and 12.3x on p95**, with a worst call of 71 s.
- **Stability: every one of 138 calls varied**, and 25 of them changed a label the scorer
  reads -- three times Gemma 4 12B's 8, though still below both Qwen arms.

**Read the parity claim carefully.** `reason` is INDISTINGUISHABLE on 37 discordant pairs
against a +/-15 band, which is a genuine no-difference result rather than an underpowered
one. `product` is UNDERPOWERED at d=3 and says nothing either way. So the honest summary is
**level on the two dimensions the pack can resolve, at 22.7x the cost and 8x the latency.**

## Experiment 16 — Gemini 2.5 Flash vs GLM 5.2

Same design as Experiment 15. `z-ai/glm-5.2` pinned to **Sail Research (fp8)** after a
probe in which Novita returned 3/3 transport errors. Zero reasoning tokens. Gemini's arm
is Experiment 10's, reused.

| dimension | Gemini 2.5 Flash | GLM 5.2 | paired verdict | discordant |
|---|---:|---:|---|---|
| call_result w-F1 | 0.955 | **0.966** | INDISTINGUISHABLE | +0 of 6 |
| **reason w-F1** | 0.823 | **0.863** | **AHEAD** | **+14 of 32, band +/-14** |
| product w-F1 | **0.960** | 0.950 | UNDERPOWERED | -3 of 3 |
| completed calls | **414/414** | 408/414 | | |
| raw-unstable | **0/138** | 137/138 | | |
| of which scored | **0** | 27 | | |
| p50 / p95 / max latency | **1.99 / 3.76 / 4.77 s** | 25.77 / 108.86 / 186.11 s | | |
| cost, 414 calls | **$0.517** | $1.028 | | |

### ~~The first time an open model has beaten Gemini on `reason` in this project~~ The first to do it under a matched zero-reasoning regime

*(Header corrected in place 2026-08-11. The original superlative was false -- see
correction 1 below. The corrected claim is the one to quote.)*

**AHEAD, +14 of 32 discordant pairs against a +/-14 band**, and weighted F1 0.863 against
0.823, on the dimension every experiment here has turned on. ~~Every previous candidate was
INDISTINGUISHABLE at best on this dimension.~~ That was false: Experiment 5A recorded two
Qwen arms AHEAD on `reason`, both by wider margins. What is new here is the **regime** --
GLM posted this with **zero reasoning tokens on both sides**, where both Qwen AHEADs were
bought with 2.4-2.6M.

**The margin is the narrowest one the rule can return.** `net` equals the band exactly:
one discordant pair the other way and this reads INDISTINGUISHABLE. That is a real AHEAD
under a pre-registered rule, and it is one pair from not being one. Both facts belong in
any quotation of it.

### And it failed the reliability gate

**6 of 414 calls returned a transport error: 408/414 = 98.55%, below the pre-registered
99% minimum.** Under Experiment 5B's `decision()` that is a `FAIL` on reliability
regardless of the quality result.

The failures are **transport, not schema** -- the model never returned malformed JSON, and
`response_format` was honoured on every call that arrived. So this is a property of
`glm-5.2 @ Sail Research` on the night it was measured, not evidence that the model cannot
produce valid output. It is recorded as measured rather than excused, and a re-run on a
second provider is the way to separate the two.

### Latency is the worst of any arm measured under a matched zero-reasoning regime

p50 **25.77 s** against Gemini's 1.99 s, p95 **108.86 s** against 3.76 s, and a worst call
of **186 s** -- over three minutes for one transcript. At production's roughly 83,000 files
a month this is the number that decides deployability, well before quality does.

**Worst at every percentile, not just the tail.** Against the other zero-reasoning arms in
this comparison -- Gemini 1.99 s, Qwen35B-A3B 2.78 s, Qwen27B 6.95 s, Gemma 9.62 s, Kimi
16.77 s -- GLM's 25.77 s p50 is the slowest, and so are its p95 and max. The superlative
also survives a sweep of **every** zero-reasoning arm in `out/runs`, roughly twenty of them
including the tuning and holdout arms; the nearest p50 is 19.56 s (`e14-hold-gemma-base`),
the nearest p95 46.31 s and the nearest max 71.02 s, both Kimi's. *(Correction 3 below
originally retracted this header by comparing against Experiment 5A's reasoning-on Qwen
runs. That retraction was itself wrong and is withdrawn; see the note there.)*

Cost is the mildest of its problems: **$1.028 against $0.517**, only 2x, with a tokenizer
needing **1.84x** the input tokens for the same Thai text -- 2,240,851 against Gemini's
1,220,906 **on the 408 cells both arms answered** -- markedly better than Kimi K3's 2.59x.

### Corrections to Experiments 15 and 16, from an adversarial verification pass (2026-08-11)

Eight agents across four lenses checked every claim above against the run logs before the
cross-model summary was written. **Four claims were flagged wrong and are corrected here
rather than edited away -- of which three really were wrong.** The verdict arithmetic
itself was reproduced exactly in every case; what failed was the prose wrapped around it.
*(Recount 2026-08-11: correction 3 was a false alarm and is withdrawn below, so the score
is three genuine errors, one mistaken retraction, plus corrections 5-8 which were never
part of the four.)*

> **Second pass, 2026-08-11.** A later review found that this corrections section had
> introduced errors of its own. Correction 3 was wrong outright and is withdrawn below;
> the AHEAD count in correction 1 and the verdict category in correction 2 were both off
> by one step. Those repairs are marked inline. Recomputed from
> `experiments/evidence/retention-e7/summary.json` and the raw run logs through the
> harness's own `_percentile` and `exact_band`, never a re-implementation.

**1. ~~"The first time an open model has beaten Gemini on `reason` in this project."~~
FALSE, and contradicted by this same file 1,250 lines above.** Experiment 5A recorded
**two** open models AHEAD of Gemini on `reason` at the same alpha: Qwen3.6 27B at **+26 of
40** against +/-16, and Qwen3.6 35B-A3B at **+17 of 41** against +/-15 (`EXPERIMENTS.md`
lines 864-873). Both margins are larger than GLM's.

What is genuinely new about GLM 5.2 is the **regime, not the direction**. Both Qwen AHEADs
were bought with 2,379,369 and 2,620,339 reasoning tokens against Gemini's zero, and this
file already says they must be read as "Qwen with 2.4-2.6M tokens of reasoning beats Gemini
with none". GLM 5.2 posted **zero reasoning tokens on both sides**. The correct claim is:
**the first open model to clear the AHEAD band against Gemini on `reason` under a matched
zero-reasoning regime at identical decoding.**

The same error appears in `docs/gemma-4-12b-assessment.md` ("only the second AHEAD this
project has recorded"); Experiment 14's was the ~~**fourth**~~ **third**. *(Repaired
2026-08-11: the four AHEADs on `reason` in order are Qwen27B and Qwen35B-A3B in Experiment
5A, Gemma's tuned arm in Experiment 14, then GLM 5.2 in Experiment 16. Experiment 14
precedes Experiment 16, so Gemma's is the third and GLM's is the fourth --
`EXPERIMENTS.md:864,867,2009,2106`. `docs/gemma-4-12b-assessment.md` had it right and this
line contradicted it.)*

**2. ~~"Kimi K3 is statistically level with Gemini on quality."~~ Overstated.** Only
`reason` carries power: d=37 against a +/-15 band is a real no-difference result. On
`call_result`, d=7 and `exact_band(7) = 7`, so the test could only have returned a verdict
on a 7-0 sweep -- one pair from ~~UNDERPOWERED~~ **INDISTINGUISHABLE**, ruling out nothing
short of total. *(Repaired 2026-08-11: flipping one pair of a 7-0 leaves d=7, so the band
stays 7 while net falls to 5, which is INDISTINGUISHABLE. UNDERPOWERED needs the band
itself to vanish, i.e. d dropping to 5 or below -- `exact_band(5) = None`. Verified against
`src/evalharness/compare.py`.)* On
`product`, Kimi lost all 3 discordant clusters it had. The defensible statement is
**"indistinguishable on `reason`, the one dimension with power; the other two are
underpowered and say nothing either way."**

**3. ~~"GLM 5.2's latency is the worst of any arm measured here."~~ ~~FALSE on the
median.~~ WITHDRAWN 2026-08-11 -- the original claim was right and this correction was
wrong.**

~~On the identical 138-item pack at the same prompt, Qwen3.6 27B posted p50 40.62 s and
Qwen3.6 35B-A3B 28.75 s, both worse than GLM's 25.77 s. GLM has the worst tail only: p95
108.86 s and max 186.11 s against 53.25 s and 85.94 s for the next worst arm.~~

**Why it was wrong.** Those Qwen p50s are from the **reasoning-on** arms -- CoreWeave and
AkashML, 2,379,369 and 2,620,339 reasoning tokens, $6.55 and $1.96 -- the very runs
correction 1 four paragraphs above says must not be read as a clean comparison. Citing
them to rebut a latency claim about a zero-reasoning arm repeats the confound the same
section had just finished disclosing.

Under the **matched zero-reasoning regime** (Experiment 7, `retention-e7/summary.json`),
the same two models post **p50 6.950 s** and **2.779 s** -- both far faster than GLM.

**One of those two legs also changed provider, and that is disclosed rather than buried.**
Qwen35B-A3B ran on AkashML in both experiments, so its 28.703 s -> 2.779 s is regime alone.
Qwen27B moved from CoreWeave (E5A) to Chutes (E7), so its 40.594 s -> 6.950 s is regime
*and* hardware and cannot be attributed to either by itself. Neither leg is needed for the
conclusion below: GLM is the slowest arm even against the provider-matched 35B-A3B.

Ranking all six zero-reasoning arms:

| arm | p50 | p95 | max |
|---|---:|---:|---:|
| Gemini 2.5 Flash | **1.99 s** | **3.76 s** | **4.77 s** |
| Qwen3.6 35B-A3B (E7) | 2.78 s | 9.00 s | 20.65 s |
| Qwen3.6 27B (E7) | 6.95 s | 20.50 s | 55.98 s |
| Gemma 4 12B | 9.62 s | 12.95 s | 22.55 s |
| Kimi K3 | 16.77 s | 46.31 s | 71.02 s |
| **GLM 5.2** | **25.77 s** | **108.86 s** | **186.11 s** |

**GLM is the slowest arm at every percentile, not merely in the tail.** The section header
stands as originally written, with the regime named.

**What survives from this correction:** the rounding. The p50 figures quoted as 25.78 s and
16.78 s were rounded up by 0.01 and are **25.77 s** and **16.77 s**.

**4. ~~"6 of 414 calls returned a transport error"~~ understates the instability by 5.5x,
and both write-ups omit a confound of my own making.** The 6 are only the calls that
exhausted all three attempts. The run **retried 33 of 414 calls (8.0%)** across 51 failed
HTTP attempts -- attempt histogram `{1: 381, 2: 21, 3: 12}` -- while Gemini, Gemma and Kimi
each ran `attempt_count = 1` on all 414 calls with zero retries.

~~Every failure is **HTTP 429**, `no_asap_capacity`, clustered on four adjacent items
(RET-116 rep3, RET-117 rep3, RET-118 all three reps, RET-119 rep1).~~

*Scope and evidence repaired 2026-08-11, in two parts.*

**The four-item cluster describes the 6 terminal failures, not the 51 attempts.** Those 6
are RET-116 rep3, RET-117 rep3, RET-118 all three reps and RET-119 rep1, each with
`attempt_count = 3` and `http_status = 429`. The **33 retried calls do not cluster** --
they span **22 distinct items** across the pack. The 51 failed attempts reconcile as
21x1 + 6x2 + 6x3, the last term because a call that fails all three attempts contributes
three failures, not two.

**"Every one an HTTP 429" is an inference for 45 of the 51, not a recorded fact.** Only the
6 terminal rows carry `http_status = 429` and a `TransportError`. On the 27 calls that
retried and then succeeded, the winning attempt's fields overwrite the failed ones, so they
record `http_status = null` and `error = null`, and no per-attempt journal exists. That all
51 were 429s is the natural reading of a burst confined to one arm on one night, and it is
consistent with every failure the log *does* describe -- but the run log cannot confirm it,
and the harness's own `max_retries=0` design note (`client.py:137`) is the reason the
intermediate attempts leave no trace.

And **Experiments 15
and 16 ran concurrently through one OpenRouter account at concurrency 8 each**: E15's wall
clock was 1,043.9 s inside E16's 2,156.3 s, so E15 ran entirely within E16's window.
**The 429 burst and the 108 s p95 were measured under load I imposed.** Experiment 14
disclosed exactly this contention for Gemma and these two write-ups dropped it. The cheap
discriminating test is a re-run at concurrency 1 on the same pin, not the second provider
the write-up proposed.

**5. Also corrected.** GLM's token and cost ratios divided its 408-call totals by Gemini's
414-call totals. On the **408 cells both arms answered**, GLM used 2,240,851 prompt tokens
against Gemini's 1,220,906 -- **1.84x**, not 1.81x. Its $1.028 is a lower bound over 408
paid calls; scaled to full coverage roughly **$1.04, about 2.0x**. Gemini's cost is
$0.517276, printed inconsistently as $0.517 and $0.518 in adjacent tables.

**6. "Mutually comparable" does not follow from the shared workload contract.** All four
arms do carry the identical `workload_sha 6b1ab3ed...`, single-valued `observed_models` and
`observed_providers`, one `prompt_token_spread` value per item and empty `split_items` --
verified. But the contract has seven keys and **decoding is not among them**, and the
omission bites here: **`e10-gemma-base` ran `reasoning_effort="provider-default"` while
Gemini, Kimi and GLM all ran `"none"`.** Experiment 10's own heading, "identical decoding",
is therefore not exact, and the Gemma column is the one arm whose regime was not pinned.

**7. "Pre-registered" was doing work the artifacts do not support.** The 99% reliability
minimum is real and predates these runs (`experiments.py:738`), but
`docs/experiments-15-16-plan.md` contains no reliability gate, and both runs carry
`experiment_mode=null` and `experiment_plan_sha=null`, so `decision()` never executed. The
`FAIL` is a **correct inference from a standing rule**, not a recorded verdict.

**8. The quantisation confound was named but not labelled.** Kimi ran bf16 and GLM ran fp8.
Kimi-versus-GLM is as much a bf16-versus-fp8 comparison as a model comparison, and the
summary says so.
