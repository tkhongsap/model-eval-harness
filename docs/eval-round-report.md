# Evaluation round report — Retention labelling migration

**Period covered:** 2026-08-14 → 2026-08-18 · **Question:** can Gemini 2.5 Flash be replaced
by an internally-hosted pipeline for Retention call labelling? · **Status:** screening
decision reached · `RECONCILED: NO`

---

## 1. Scope in one paragraph

Production labels Retention calls today by sending audio to Gemini 2.5 Flash in a single call
and receiving structured JSON. The proposal is to replace that with two internal models on
True's own GPU: Qwen3-ASR 1.7B for transcription, Qwen3.8-27B for labelling. This round built
the evidence to decide, across three stages — transcription quality, labelling quality in
isolation, and the end-to-end pipeline — and reached a split recommendation. It also corrected
three previously published numbers, all of which had been wrong in the incumbent's favour.

---

## 2. How many evaluations, and what each one was

**22 numbered experiments exist in this project.** This round is the last five plus the voice
track, which was new work.

| # | What it asked | Scale | Status |
|---|---|---|---|
| E1–E9 | earlier prompt/scorer development; three retracted headline claims | — | historical |
| E10–E16 | head-to-head baselines; the holdout; Kimi K3 and GLM 5.2 as frontier comparators | — | historical |
| **E17** | the internal GPUs' first decision-grade run | 3 models × 414 rows | executed |
| **E20** | the challenge pack — what 50 harder items can settle | 2 models × 150 rows | executed |
| **E21** | the pipeline delta: does the machine transcript change the final answer? | 6 arms × 20 calls × 3 | executed |
| **E22** | the ASR runaway: model property or our configuration? | 20 calls × 6 configs | executed |
| **this round's analysis** | pooling E17+E20; production's own thresholds; business accuracy | 0 model calls | executed |

**Volume actually run in this round:** **1,956** labelling calls against hand-computed ground
truth (414 rows × 4 models on pack A, 150 × 2 on pack B), **360** pipeline calls in E21, and
**89** ASR transcriptions — 40 for the two baseline arms plus 49 across E22's configuration
sweep, replicates and two full re-runs of the set. Cost on the hosted side: **$0.73** total
($0.5616 + $0.1730). The internal GPU side is unmetered.

---

## 3. The datasets

Three, none containing production data.

| pack | items | labels | what it is |
|---|---:|---|---|
| `retention_v3` | 138 | **hand-computed** | the main text pack |
| `retention_challenge_v1` | 50 | **hand-computed** | deliberately harder cases |
| `asr-eval` | 20 | hand-authored reference transcripts + entity lists | synthetic Thai audio, 123.6 min, 2,086 turns |

**The labels are the important part.** `tests/fixtures/retention_expected.csv` holds 23
integers derived on paper from production's semantics *before any metric code existed*, and
the two text packs carry ground truth in that lineage. Three independent derivations agree:
the arithmetic, our clean-room implementation, and True's real production scorer.

**This is why the plan to use a frontier model as a weak labeller was dropped.** The original
design was to synthesise 60 new calls and have a frontier model produce reference labels,
frozen before any candidate ran. That would have been sound method — but it would have
*downgraded* the evidence, because 188 items with hand-computed keys already exist and a
hand-computed key beats a model-generated one for exactly this question. The frontier-labeller
design remains the right approach for any *new* corpus.

**The audio set** decomposes as 10 acoustic families × 2 calls each (clean, telephony noise,
code-switching, numeric-dense, proper nouns, disfluency, crosstalk, long context, far-field,
hold/IVR) across 10 call scenarios. Its known limitation: Edge TTS offers exactly two Thai
neural voices, so the set varies channel and prosody but **not speaker identity**. It cannot
answer "does this generalise across Thai speakers."

---

## 4. Methodology

### The instrument that decides

Every verdict is a **paired sign test on discordant items**, at α = 1/64 per side.

Only items where *exactly one* of the two models was right carry information about which is
better; items both got right, or both got wrong, carry none. So the sample size that counts is
**d**, the discordant count, not **n**. Below 6 discordant items no result is possible at this
α even on a clean sweep, and the verdict is **UNDERPOWERED** — deliberately *not* reported as a
tie. The four possible verdicts are AHEAD, BEHIND, INDISTINGUISHABLE, UNDERPOWERED.

### What is held constant across arms

Identical prompt (sha-pinned), identical JSON schema, identical decoding (temperature 0,
top_p 1, seed 0), 3 replicates per item, and every input's sha256 recorded before the first
call and re-verified at scoring.

### Three stages

- **Stage 1 — ASR.** Normalised CER (the contract metric; Thai has no word spaces so WER is
  tokeniser-dependent) plus entity recovery and a hallucination proxy.
- **Stage 2 — labelling in isolation.** Every model receives the *same reference transcript*;
  only the labeller varies. This is what separates reasoning quality from hearing quality.
- **Stage 3 — end-to-end.** Audio → ASR → LLM → structured output, six arms including a
  **format-control** arm carrying every formatting difference and *zero* transcription error.

### Thresholds are adopted, not invented

All grading uses production's own published bands, read at runtime from
`config/sentiment_qa/qa_pipeline_fact_check.yml` — accuracy 80/85/90, precision 75/80/90,
recall 75/80/90, F1 80/85/90. The tooling refuses to run if that file moves or changes shape,
rather than falling back to a remembered copy.

### Pooling, and why it is legitimate here

E17 and E20 each returned UNDERPOWERED on two of three dimensions — not because the models are
close, but because 138 items produce too few discordant items to test. The two packs share
prompt, scorer and repeat count but have different testset hashes: **same instrument, disjoint
samples**, which is exactly the condition under which a stratified sign test pools. Pooling
was the only way to buy power without generating new data, and it required **zero new model
calls** — the paired tables already existed.

`scripts/pooled_bands.py` refuses to pool across a prompt or scorer change, refuses to pool a
pack with itself (identical hashes would double-count every item and halve the apparent
noise), refuses to report a pack-A-only model under a "pooled" heading, and always prints the
band grade beside the paired verdict so neither can be quoted alone. 20 tests cover those
refusals; the full suite is **907 passed, 12 skipped**.

---

## 5. Results

### Stage 2 — labelling. The decisive stage.

**Business accuracy** — call-level exact correctness against hand-computed ground truth,
pooled over 188 items:

| dimension | Gemini 2.5 Flash | Qwen3.8-27B | production band | majority-class baseline |
|---|---:|---:|---|---:|
| **call_result** (save/churn) | **175/188 = 93.1%** | **175/188 = 93.1%** | both **excellent** | 50.0% |
| product | 181/188 = 96.3% | 180/188 = 95.7% | both **excellent** | 52.1% |
| reason | 106/188 = 56.4% | 100/188 = 53.2% | both **BELOW** floor | 8.0% |

**The identical 175 is a cancellation, not a per-call tie.** Pack A: Gemini 129, Qwen **130**.
Pack B: Gemini **46**, Qwen 45. The pooled 2x2 is `both_right=173, both_wrong=11, Gemini-only=2,
Qwen-only=2` — each is right on 175 calls, but on *different* sets of 175.

**And the verdict on this dimension is UNDERPOWERED, not INDISTINGUISHABLE.** Four discordant
calls is below the six needed at alpha 1/64. "We cannot tell" is not "we have shown they are
the same"; the harness keeps those separate on purpose.

**Secondary — precision / recall / F1** against the same bands. **Pack A only (138 items)** —
unlike accuracy these are not pooled, because weighted F1 pools per-class rather than per-item:

| | Gemini | Qwen3.8 |
|---|---|---|
| call_result | 97.4 / 94.0 / 95.5 — excellent | 98.6 / 94.7 / 96.6 — excellent |
| product | 95.4 / 96.7 / 96.0 — excellent | 94.1 / 96.0 / 95.0 — excellent |
| reason | **76.3** / 96.4 / 83.8 — precision *acceptable* | **79.8** / 92.8 / 83.4 — precision *acceptable* |

**The call_result F1 gap is not a Qwen lead — it reverses on the other pack:** pack A gives
Qwen **+0.011** (0.966 vs 0.955), pack B gives Gemini **+0.021** (0.951 vs 0.930). A quantity
that changes sign between two samples of the same population is noise, which is what the
paired test independently reports as UNDERPOWERED. Weighted F1 can move while accuracy does
not because it is computed over 214 ground-truth product rows rather than 188 call clusters,
at a different grain, weighted by class support — on pack B, Qwen's one extra miss lands on
`undefined` (support 3) and swings F1 hard while costing one call in fifty of accuracy.
**Error placement, not error count, drives weighted F1.**

**Paired verdicts, pooled:**

| dimension | d | net | band | verdict | items needed to become testable |
|---|---:|---:|---:|---|---:|
| call_result | 4 | 0 | — | UNDERPOWERED | ~282 (94 more than we have) |
| **reason** | **40** | **−6** | ±16 | **INDISTINGUISHABLE** | already testable |
| product | 1 | −1 | — | UNDERPOWERED | ~1,128 — not realistically reachable |

**The two saturated dimensions are not saturated because they are easy — the errors are
shared.** Each arm gets 13 call_result clusters wrong and **11 are the same clusters**; on
product each gets 7–8 wrong and **7 are shared**. Co-failure is running about 12× what
independence predicts. Both models fail the same hard calls, which is why the paired test has
no power there, and which is a more useful finding than a tie: the residual errors belong to
the task, not to the choice of model.

Two things the weighted F1 had been hiding:

1. **Every model over-predicts reasons** — recall 90–96% (excellent), precision 71–83%
   (acceptable at best). F1 averages them and conceals it. This is the largest quality gap in
   the labelling stage and it belongs to the *task*, not to any model: the incumbent has it too.
2. **Gemma-4-12B fails production's own floor** — reason precision 71.3 against 75, reason F1
   79.2 against 80. The only BELOW cell in the grid, and grounds for exclusion on production's
   published criteria rather than ours.

One caveat on the reason precision comparison specifically: the two arms are scored on
slightly **different denominators** (157 vs 159 on pack A, 65 vs 66 on pack B) because they
emit different numbers of orphan predictions. The 76.3-vs-79.8 gap is therefore across
slightly different populations and should not be read as a clean head-to-head. The business
accuracy table above is unaffected — it comes off the paired 2x2, which is identical for both
arms by construction.

**Stability is not a tie.** Across E21's 360 calls at temperature 0, the four Qwen3.8 arms were
perfectly deterministic — 240 calls, not one field differing between replicates. The
production-shaped Gemini audio arm changed **26.5% of its fields between byte-identical
requests** and returned unparseable JSON on **4 of 60** despite an enforced schema.

Independently corroborated during verification of this round: on the pack-A text run, **Qwen3.8
was byte-identical across all three replicates while Gemini's replicate 1 differed from 2 and 3
(152 vs 153 rows, 66 differing tuples)** — a separate measurement, on a different track, of the
same asymmetry. The business-accuracy result is not an artifact of which replicate was scored:
replicates 2 and 3 give the same 129 / 130 / 46 / 45 split.

### Stage 1 — ASR. The diagnostic.

| arm | CER | entity recovery |
|---|---:|---|
| Gemini 2.5 Flash | **0.0443** | 450/465 (96.8%) |
| Qwen3-ASR 1.7B | **0.1147** | 450/465 (96.8%) |

**The identical 450 is real, reproducible, and should not be read as "the arms perform the
same."** Independently re-derived from the raw hypothesis text for all 20 items in both arms —
0 mismatches across 40 item/arm records. But:

- **Only 3 of the 15 missed entities overlap.** The arms differ on 9 of 20 items and the
  per-item deltas happen to cancel to zero. It is arithmetic coincidence, not a shared
  structural limit.
- **They hit by opposite routes.** `score_entities` credits a hit if the surface form *or* the
  value form is found, and the two arms sit on opposite sides of that OR:

  | arm | surface-only | value-only | both | union |
  |---|---:|---:|---:|---:|
  | Gemini | 261 | 34 | 155 | **450** |
  | Qwen3-ASR | 46 | 113 | 291 | **450** |

  The cause is a rendering convention: Gemini emits 15 ASCII digit runs against 151
  spoken-digit runs (it spells numbers as Thai words); Qwen emits 202 against 53. The OR takes
  the union of two near-mirror-image behaviours and returns the same integer.
- **The whitespace fix is what produced the convergence.** Pre-fix the arms scored **314/465
  (67.5%)** and **432/465 (92.9%)**; the fix moved Gemini **+136** and Qwen **+18** onto the
  same number. The fix is correct and was argued before any code changed — but landing on an
  identical integer is luck, and quoting it without the decomposition invites exactly the wrong
  inference.

**Consequence: entity F1 must not be promoted to a decision metric in its current form.** It
reports "no difference" about the largest behavioural difference between the two arms.

**BEHIND, and robustly so** — three different ways of handling the one catastrophic call give
the same verdict:

```
all 20 calls          Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
excluding ASR-012     Gemini better on 16, Qwen on 3  ->  d=19 net=-13 band=+/-11  BEHIND
re-transcribed clean  Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
```

**The blocker is the failure mode, not the average.** One call in twenty returns a repetition
loop — 5–16× the reference length, 80–95% of the output one repeated unit. It is
**deterministic** given the audio and the request boundaries (two replicates byte-identical at
39,678 characters), and every configuration tried **relocates** it rather than removing it:

| chunk setting | victim set on these 20 calls |
|---|---|
| `cs0` (as published) | {ASR-012} |
| `cs120` | {ASR-018} |
| `cs180` | {} |

Adopting `cs180` on that evidence would be fitting the configuration to the twenty calls we
own. Both of those calls have now been spent selecting settings, so **no chunk size can be
honestly validated on this set again**. Gemini shows nothing comparable: 0/20.

**Throughput** is 0.336 vs 3.64 calls/s, but that is *not* an 11× model gap: the arms ran at
different concurrency, and both internal models hit the identical 0.336 on two different
packs, which points at the LiteLLM gateway or the vLLM scheduler rather than at either model.
Infrastructure, and expected to be fixable.

### Stage 3 — end-to-end. Ran, but cannot decide.

E21's headline: **across all 351 successful label calls, not one call where any arm disagrees
with any other about the business outcome.**

**That number must not be quoted as evidence.** The corpus generator writes a single wrap-up
line per scenario stating the outcome verbatim — retention closes with *"keeping the service as
before"*, MNP with *"recorded the port-out request"*. Recovering the business label is
string-matching, not inference. Zero disagreement is a property of the generator; 60 more
calls from it would reproduce it exactly.

What Stage 3 *did* establish:

- **Formatting outweighs transcription.** The candidate pipeline's JSON differed from the
  ceiling on 12 of 20 calls — but the zero-transcription-error format-control arm differed on
  **10 of 20 by itself**, and 9 of the 12 overlap. Only 3 changes are attributable to real
  transcription error, all product attribution or reason wording.
- Production's own arm is the least reliable component (the 26.5% / 4-of-60 figures above).

---

## 6. Three published numbers corrected this round

All three had been wrong in the direction that flattered the incumbent.

| claim as published | corrected | cause |
|---|---|---|
| Qwen3-ASR entity recovery 67.5% vs Gemini 92.9% | **both 96.8%** | scorer's substring test included spaces; an arm writing identical words with different spacing scored the entity lost |
| Qwen3-ASR CER **0.673** | **0.1147** | one decoder failure dominating the mean; the honest gap is 2.6×, not 15× |
| "whitespace is inflating CER" | **retracted** | `chars()` already strips whitespace; every published CER was always whitespace-blind |

The third is a retraction of a claim *I* made mid-round, kept in the closed list rather than
deleted — a false claim in that file is worse than no claim.

---

## 7. The decision

**Split the migration. Move the labelling stage internally now. Keep ASR external.**

| stage | recommendation | basis |
|---|---|---|
| audio → transcript | **keep Gemini** | 2.6× CER, BEHIND at α=1/64, plus a deterministic failure mode no configuration removes |
| transcript → label | **move to Qwen3.8-27B** | identical business accuracy on 188 hand-labelled items; INDISTINGUISHABLE on the only testable dimension; strictly more stable |

Not a hedge — E21 ran exactly this hybrid arm (`gemini-asr-text`) and it behaved like the
internal pipeline, not like production.

**Confidence: high on the labeller, high on the ASR, low on end-to-end.**

---

## 8. What this is not

This is a **screening result**, not a production gate. Four things stand between the two:

1. **`docs/ask1-email-draft.md`, unsent since 2026-08-09.** Two header rows, no customer data,
   no privacy review required. The only thing that can retire `RECONCILED: NO`, and it
   outranks everything else here.
2. **A corpus whose business label is not recoverable from a memorised sentence.**
3. **Real human-labelled calls.** The 188 items carry hand-computed keys — the strongest
   evidence in the project — but the text is synthetic.
4. **A runaway detector in the scorer.** The diagnostic already fires at 20× the set mean
   (20,972 insertions per non-speech minute against a mean of 1,073) and nothing acts on it: a
   CER of 16.28 pooled with nineteen 0.1s and reached a published report.
5. **Two provenance defects found during verification of this round**, both worth fixing before
   these numbers are quoted externally:
   - Both ASR reports stamp `scoring_code_sha256: bed27990c1258617…`, which matches **no
     committed version** of `score_asr.py` (HEAD is `64667716d387383f…`). They were generated
     from uncommitted intermediate code and the entity fields were patched in afterwards
     without refreshing the stamp. The numbers themselves reproduce exactly at HEAD, so this is
     a broken audit trail rather than a wrong result — but a stamp that cannot identify the
     code that produced it is not a control. Re-running the scorer at HEAD fixes it.
   - `DEVLOG.md` still published the pre-fix entity figure (320/465), a *third* and even
     earlier number than the 314/465 the fix commit recorded. Corrected 2026-08-18.
6. **A dead branch in the entity scorer.** For all 15 `package` entities `value == spoken`
   (uniquely — 0 of 118 amounts, 0 of 88 dates, 0 of 88 phones), and the package value path is
   whitespace-*sensitive* while the surface path is whitespace-*insensitive*. So `value_hit`
   implies `surface_hit` and the value path can never add a package hit — measured 0 for both
   arms. The whitespace fix updated the surface line and left this branch behind.

---

## 9. Reproduce

```bash
PYTHONPATH=src python scripts/pooled_bands.py          # Stage 2, zero model calls
PYTHONPATH=src python -m pytest tests/ -q               # 907 passed, 12 skipped
```

Companions: `docs/migration-decision.md` (the decision),
`docs/experiment21-results.md` (Stage 3), `docs/experiment22-asr-runaway.md` (Stage 1 failure
mode), `docs/reports/pooled-bands.json` (machine-readable).
