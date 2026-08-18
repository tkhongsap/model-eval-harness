# Migration decision: can Gemini 2.5 Flash be replaced by an internal pipeline?

**Date:** 2026-08-18 · **Status:** screening decision · `RECONCILED: NO` — no production call
has been through any part of this.

## The decision

**Split the migration. Move the labelling stage internally now. Do not move the ASR stage.**

| stage | today | recommendation | why |
|---|---|---|---|
| audio → transcript | Gemini 2.5 Flash | **keep external** | 2.6× the character error rate, plus an unresolved deterministic failure mode |
| transcript → structured label | Gemini 2.5 Flash | **move to Qwen3.8-27B** | equal business accuracy on <!--claim:pooled-bands.json:items.pooled:int-->188<!--/--> hand-labelled items (no difference detectable); strictly more stable |

That is a real architectural option, not a hedge — E21 ran exactly this arm
(`gemini-asr-text`: external transcription, internal labelling) and it behaved like the
internal pipeline, not like production.

**Confidence: high on the labeller, high on the ASR, low on the end-to-end.** The reason for
the third is the honest limitation of this work and is stated in full below.

## Stage 2 first, because the business outcome is what decides

The brief says judge the business outcome first and treat ASR as a diagnostic. Doing that
inverts the usual order of presentation, and it changes the answer — leading with ASR would
have recommended against the whole migration.

**Primary metric — business accuracy**, call-level exact correctness against ground truth
hand-computed before any of this code existed, pooled over `retention_v3` (<!--claim:pooled-bands.json:items.pack_a:int-->138<!--/-->) and
`retention_challenge_v1` (<!--claim:pooled-bands.json:items.pack_b:int-->50<!--/-->):

| dimension | Gemini 2.5 Flash | Qwen3.8-27B | production band | always-majority baseline |
|---|---:|---:|---|---:|
| **call_result** (save / churn — what production acts on) | **<!--claim:pooled-bands.json:business_accuracy[0].incumbent_correct,business_accuracy[0].n:frac-->175/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[0].incumbent_accuracy:pct1-->93.1%<!--/-->** | **<!--claim:pooled-bands.json:business_accuracy[0].candidate_correct,business_accuracy[0].n:frac-->175/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[0].candidate_accuracy:pct1-->93.1%<!--/-->** | both **<!--claim:pooled-bands.json:business_accuracy[0].incumbent_band,business_accuracy[0].candidate_band:text-->excellent<!--/-->** (≥<!--claim:pooled-bands.json:bands.accuracy.excellent:int-->90<!--/-->) | 50.0% |
| product | <!--claim:pooled-bands.json:business_accuracy[2].incumbent_correct,business_accuracy[2].n:frac-->181/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[2].incumbent_accuracy:pct1-->96.3%<!--/--> | <!--claim:pooled-bands.json:business_accuracy[2].candidate_correct,business_accuracy[2].n:frac-->180/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[2].candidate_accuracy:pct1-->95.7%<!--/--> | both **<!--claim:pooled-bands.json:business_accuracy[2].incumbent_band,business_accuracy[2].candidate_band:text-->excellent<!--/-->** | 52.1% |
| reason | <!--claim:pooled-bands.json:business_accuracy[1].incumbent_correct,business_accuracy[1].n:frac-->106/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[1].incumbent_accuracy:pct1-->56.4%<!--/--> | <!--claim:pooled-bands.json:business_accuracy[1].candidate_correct,business_accuracy[1].n:frac-->100/188<!--/--> = <!--claim:pooled-bands.json:business_accuracy[1].candidate_accuracy:pct1-->53.2%<!--/--> | both **<!--claim:pooled-bands.json:business_accuracy[1].incumbent_band,business_accuracy[1].candidate_band:text-->BELOW<!--/-->** (floor <!--claim:pooled-bands.json:bands.accuracy.acceptable:int-->80<!--/-->) | 8.0% |

The baseline column matters: a <!--claim:pooled-bands.json:business_accuracy[0].incumbent_accuracy,business_accuracy[0].candidate_accuracy:pct1-->93.1%<!--/--> that a constant predictor could reach would be
meaningless. Always guessing the majority class scores **50.0%** at the harness's scoring
grain, so the observed <!--claim:pooled-bands.json:business_accuracy[0].incumbent_accuracy,business_accuracy[0].candidate_accuracy:pct1-->93.1%<!--/--> sits **43 points above chance**. The label space is not
degenerate — churn 110 / save 57 / unknown 13 / undefined 8, and 17 of <!--claim:pooled-bands.json:items.pooled:int-->188<!--/--> calls carry
different `call_result` values across their own product rows.

**The two models get the same NUMBER of calls right on the decision that matters — <!--claim:pooled-bands.json:business_accuracy[0].incumbent_correct,business_accuracy[0].candidate_correct:int-->175<!--/--> each.
They do not get the same CALLS right.** The pooled 2x2 is `both_right=173, both_wrong=11,
Gemini-only=2, Qwen-only=2`: they disagree on four calls, two each way, and those cancel
exactly on pooling. An earlier draft of this document said "identical, to the call"; that was
wrong and is corrected here.

**And the honest verdict on this dimension is UNDERPOWERED, not INDISTINGUISHABLE.** Four
discordant calls is below the six `exact_band` needs at alpha 1/64, so no band exists and the
harness refuses to call it either way. "We cannot tell" is not "we have shown they are the
same", and the distinction is deliberate in `paired_verdict`'s own docstring. The dimension
where we *can* tell is `reason`, and there the answer is <!--claim:pooled-bands.json:pooled_paired[1].verdict:text-->INDISTINGUISHABLE<!--/--> at d=<!--claim:pooled-bands.json:pooled_paired[1].discordant:int-->40<!--/-->.

**Secondary metrics** — graded against production's own published thresholds read at runtime
from `qa_pipeline_fact_check.yml`. **These are pack A only (<!--claim:pooled-bands.json:items.pack_a:int-->138<!--/--> items).** Unlike the accuracy
table above they are not pooled, because weighted F1 pools per-class rather than per-item and
cannot be added across packs:

| | Gemini P / R / F1 | Qwen3.8 P / R / F1 |
|---|---|---|
| call_result | <!--claim:pooled-bands.json:graded.gemini.call_result.precision.value:num1-->97.4<!--/--> / <!--claim:pooled-bands.json:graded.gemini.call_result.recall.value:num1-->94.0<!--/--> / <!--claim:pooled-bands.json:graded.gemini.call_result.f1.value:num1-->95.5<!--/--> — all <!--claim:pooled-bands.json:graded.gemini.call_result.precision.band,graded.gemini.call_result.recall.band,graded.gemini.call_result.f1.band:text-->excellent<!--/--> | <!--claim:pooled-bands.json:graded.qwen38.call_result.precision.value:num1-->98.6<!--/--> / <!--claim:pooled-bands.json:graded.qwen38.call_result.recall.value:num1-->94.7<!--/--> / <!--claim:pooled-bands.json:graded.qwen38.call_result.f1.value:num1-->96.6<!--/--> — all <!--claim:pooled-bands.json:graded.qwen38.call_result.precision.band,graded.qwen38.call_result.recall.band,graded.qwen38.call_result.f1.band:text-->excellent<!--/--> |
| product | <!--claim:pooled-bands.json:graded.gemini.product.precision.value:num1-->95.4<!--/--> / <!--claim:pooled-bands.json:graded.gemini.product.recall.value:num1-->96.7<!--/--> / <!--claim:pooled-bands.json:graded.gemini.product.f1.value:num1-->96.0<!--/--> — all <!--claim:pooled-bands.json:graded.gemini.product.precision.band,graded.gemini.product.recall.band,graded.gemini.product.f1.band:text-->excellent<!--/--> | <!--claim:pooled-bands.json:graded.qwen38.product.precision.value:num1-->94.1<!--/--> / <!--claim:pooled-bands.json:graded.qwen38.product.recall.value:num1-->96.0<!--/--> / <!--claim:pooled-bands.json:graded.qwen38.product.f1.value:num1-->95.0<!--/--> — all <!--claim:pooled-bands.json:graded.qwen38.product.precision.band,graded.qwen38.product.recall.band,graded.qwen38.product.f1.band:text-->excellent<!--/--> |
| reason | **<!--claim:pooled-bands.json:graded.gemini.reason.precision.value:num1-->76.3<!--/-->** / <!--claim:pooled-bands.json:graded.gemini.reason.recall.value:num1-->96.4<!--/--> / <!--claim:pooled-bands.json:graded.gemini.reason.f1.value:num1-->83.8<!--/--> — precision only *<!--claim:pooled-bands.json:graded.gemini.reason.precision.band:text-->acceptable<!--/-->* | **<!--claim:pooled-bands.json:graded.qwen38.reason.precision.value:num1-->79.8<!--/-->** / <!--claim:pooled-bands.json:graded.qwen38.reason.recall.value:num1-->92.8<!--/--> / <!--claim:pooled-bands.json:graded.qwen38.reason.f1.value:num1-->83.4<!--/--> — precision only *<!--claim:pooled-bands.json:graded.qwen38.reason.precision.band:text-->acceptable<!--/-->* |

**Do not read the call_result F1 gap as a Qwen lead — it reverses on the other pack:**

| pack | Gemini F1 | Qwen3.8 F1 | who leads |
|---|---:|---:|---|
| A (<!--claim:pooled-bands.json:items.pack_a:int-->138<!--/-->) | <!--claim:pooled-bands.json:graded.gemini.call_result.f1.value:f3-->0.955<!--/--> | <!--claim:pooled-bands.json:graded.qwen38.call_result.f1.value:f3-->0.966<!--/--> | Qwen **+0.011** |
| B (<!--claim:pooled-bands.json:items.pack_b:int-->50<!--/-->) | 0.951 | 0.930 | Gemini **+0.021** |

A quantity that changes sign between two samples of the same population is noise, and this is
exactly what the paired test reports independently as UNDERPOWERED. The reason weighted F1 can
move while accuracy does not is that it is computed over a different population (214
ground-truth product rows, not <!--claim:pooled-bands.json:items.pooled:int-->188<!--/--> call clusters), at a different grain, and weighted by class
support — so a single miss on a rare class swings it hard. On pack B, Qwen's one extra miss
lands on `undefined` (support 3), which alone accounts for most of that pack's F1 gap while
costing one call out of fifty in accuracy. **Error placement, not error count, drives weighted
F1.**

**Is the difference real? No.** Paired sign test at α=1/64, pooled:

| dimension | discordant | net | band | verdict |
|---|---:|---:|---:|---|
| call_result | <!--claim:pooled-bands.json:pooled_paired[0].discordant:int-->4<!--/--> | <!--claim:pooled-bands.json:pooled_paired[0].net:int-->0<!--/--> | <!--claim:pooled-bands.json:pooled_paired[0].band:pm-->—<!--/--> | <!--claim:pooled-bands.json:pooled_paired[0].verdict:text-->UNDERPOWERED<!--/--> |
| **reason** | **<!--claim:pooled-bands.json:pooled_paired[1].discordant:int-->40<!--/-->** | **<!--claim:pooled-bands.json:pooled_paired[1].net:int-->−6<!--/-->** | <!--claim:pooled-bands.json:pooled_paired[1].band:pm-->±16<!--/--> | **<!--claim:pooled-bands.json:pooled_paired[1].verdict:text-->INDISTINGUISHABLE<!--/-->** |
| product | <!--claim:pooled-bands.json:pooled_paired[2].discordant:int-->1<!--/--> | <!--claim:pooled-bands.json:pooled_paired[2].net:int-->−1<!--/--> | <!--claim:pooled-bands.json:pooled_paired[2].band:pm-->—<!--/--> | <!--claim:pooled-bands.json:pooled_paired[2].verdict:text-->UNDERPOWERED<!--/--> |

`reason` is the only dimension carrying enough discordant items to test at all, and there the
answer is a tight no-difference: an observed gap of 6 against a noise band of <!--claim:pooled-bands.json:pooled_paired[1].band:int-->16<!--/-->.

call_result and product cannot be tested at this scale — the models disagree on <!--claim:pooled-bands.json:pooled_paired[0].discordant:int-->4<!--/--> items and <!--claim:pooled-bands.json:pooled_paired[2].discordant:int-->1<!--/-->
item out of <!--claim:pooled-bands.json:items.pooled:int-->188<!--/-->. At those rates, reaching the minimum six discordant items needs **~282 items
for call_result** and **~1,128 for product**. The first is reachable — 94 more items than we
already have — and is the single cheapest way to strengthen this decision. The second is not
realistically reachable and product should be treated as permanently unresolvable by this
method.

**Why so few disagreements? Not because the dimensions are easy — because the errors are
shared.** Each arm gets 13 call_result clusters wrong, and **11 of those are the same
clusters**; on product each gets 7-8 wrong and **7 are shared**. Co-failure runs about 12x
what independence would predict. The two models are failing on the same hard calls, which is
a more interesting finding than a tie and is also precisely why the paired test has no power
here.

**Two findings the published weighted F1 was hiding:**

1. **Every model over-predicts reasons.** Recall is excellent everywhere (90–96%); precision
   is *acceptable* at best (71–83%). F1 averages the two and conceals it. This is a property
   of the task and the prompt, not of any model, and it is the single biggest quality gap in
   the labelling stage — for the incumbent as much as the candidate.
2. **Gemma-4-12B fails production's own floor** — reason precision <!--claim:pooled-bands.json:graded.gemma.reason.precision.value:num1-->71.3<!--/--> against a floor of <!--claim:pooled-bands.json:bands.precision.acceptable:int-->75<!--/-->,
   reason F1 <!--claim:pooled-bands.json:graded.gemma.reason.f1.value:num1-->79.2<!--/--> against <!--claim:pooled-bands.json:bands.f1.acceptable:int-->80<!--/-->. It is the only <!--claim:pooled-bands.json:graded.gemma.reason.precision.band,graded.gemma.reason.f1.band:text-->BELOW<!--/--> cell in the entire grid and is excluded on
   production's published criteria, not on ours.

**Stability, which is not a tie:** across E21's 360 calls at temperature 0, the four Qwen3.8
arms were perfectly deterministic — 240 calls, not one field differing between replicates.
The production-shaped Gemini audio arm changed **26.5% of its fields between byte-identical
requests** and returned unparseable JSON on **4 of 60** despite an enforced schema. That is an
argument for the internal labeller that has nothing to do with accuracy.

## Stage 1 — ASR, the diagnostic

| arm | CER | entity recovery |
|---|---:|---|
| Gemini 2.5 Flash | **0.0443** | 450/465 (96.8%) |
| Qwen3-ASR 1.7B | **0.1147** | 450/465 (96.8%) |

**Behind, robustly.** Paired over the 20 calls, three ways of handling the one catastrophic
item, same answer every time:

```
all 20 calls          Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
excluding ASR-012     Gemini better on 16, Qwen on 3  ->  d=19 net=-13 band=+/-11  BEHIND
re-transcribed clean  Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
```

**Two corrections to previously published voice-track numbers, both against our own case:**

- The published CER of **0.673 was wrong** — it was one decoder failure dominating the mean.
  Re-transcribed with no runaway present the figure is **0.1147**, so the honest gap is 2.6×,
  not 15×.
- The published claim that entity recovery differed (92.9% vs 67.5%) was a whitespace artifact.
  Both arms recover 450/465. **But the composition is opposite** — Gemini matches 416 entities
  by surface form and 189 by value; Qwen3-ASR matches 337 and 404. The identical total hides
  the largest behavioural difference between them, so entity F1 should *not* be promoted to a
  decision metric without splitting those paths.

**The blocker is the failure mode, not the average.** One call in twenty returns a repetition
loop — 5–16× the reference length, 80–95% of the output a single repeated unit. It is
deterministic given the audio and the request boundaries (two replicates byte-identical), and
every configuration tried **relocates** it rather than removing it: `cs0` breaks ASR-012,
`cs120` breaks ASR-018, `cs180` breaks neither. Adopting `cs180` on that basis would be
fitting the configuration to the twenty calls we own — and both of those calls have now been
spent selecting settings, so no chunk size can be honestly validated on this set again.
Gemini shows nothing comparable: 0/20.

**Throughput is a separate issue, and probably not a blocker.** Measured 0.336 vs 3.64
calls/s. Two caveats keep that from being an 11× model gap: the arms ran at different
concurrency (4 internal vs 8 hosted, over the public internet), and **both internal models hit
the identical 0.336 on two different packs** — which points at the LiteLLM gateway or the vLLM
scheduler rather than at either model (E17, "Next", item 3). It needs answering before a batch
cutover, but unlike the ASR failure mode it is infrastructure and is expected to be fixable.

## Stage 3 — end-to-end, and why it cannot decide

E21 ran the full shape: six arms, 20 calls, 3 replicates, one labeller and one prompt over
audio → ASR → LLM → structured output, including a zero-transcription-error format-control arm.

**Result: the business decision never changed. Across all 351 successful label calls, there is
not one call where any arm disagrees with any other about the outcome.**

**That number must not be quoted as evidence.** The corpus generator writes a single wrap-up
line per scenario that states the outcome verbatim — retention closes with *"keeping the
service as before"*, MNP with *"recorded the port-out request"*. Recovering the business label
is string-matching, not inference. Zero disagreement is a property of the generator, and 60
more calls from it would reproduce it exactly.

What Stage 3 *did* establish, on the same run:

- **Formatting outweighs transcription.** The candidate pipeline's JSON differed from the
  ceiling on 12 of 20 calls — but the zero-transcription-error format-control arm differed on
  **10 of 20 by itself**, and 9 of the 12 overlap. Only 3 changes are attributable to real
  transcription error, all product attribution or reason wording.
- **Production's own arm is the least reliable component** (the 26.5% / 4-of-60 figures above).

## What would make this a production gate

This is a screening result. Four things stand between it and a decision anyone should bet on:

1. **`docs/ask1-email-draft.md`, unsent since 2026-08-09.** Two header rows, no customer data,
   no privacy review needed. It is the only thing that can retire `RECONCILED: NO`, and it
   outranks every other item here.
2. **A corpus whose business label is not recoverable from a memorised sentence.** Until the
   wrap-up templates are varied, end-to-end business accuracy on synthetic data measures a
   constant.
3. **Real human-labelled calls.** The 188 items carry hand-computed ground truth, which is why
   the Stage 2 result is the strongest thing here — but they are synthetic text.
4. **A runaway detector in the scorer.** The diagnostic already fires at 20× the set mean and
   nothing acts on it: a CER of 16.28 pooled with nineteen 0.1s and reached a published report.

## Reproduce

```bash
PYTHONPATH=src python scripts/pooled_bands.py     # stages 2's numbers, zero model calls
PYTHONPATH=src python -m pytest tests/test_pooled_bands.py -q
```

Supporting write-ups: `docs/experiment21-results.md` (Stage 3),
`docs/experiment22-asr-runaway.md` (Stage 1 failure mode),
`docs/reports/pooled-bands.json` (Stage 2, machine-readable).
