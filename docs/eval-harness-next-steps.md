# Next steps: making the harness and the eval sets ready for future work

**Written:** 2026-08-22
**Status:** Tier 0 blocked on data. Tiers 1–3 executable, in the order below.
**Supersedes:** the priority ranking in `docs/reports/eval-inventory-reference.md` §6 — see
"the finding that reframes everything" below for why.

## Context

The inventory (`docs/reports/eval-inventory.html`, PR #51) established the shape of the
problem: 8 eval sets and 4 experiments, all Retention;
<!--claim:eval-inventory.json:production.tasks_covered:int-->1<!--/--> of
<!--claim:eval-inventory.json:production.tasks_total:int-->6<!--/--> production tasks
scoreable; <!--claim:eval-inventory.json:app_bindings.registered:int-->1<!--/--> app binding
registered, so multi-app support is designed and unexercised.

While planning this, `DEVLOG.md`'s roadmap and `docs/eval-improvement-plan.md` (2026-08-05)
were re-read, and **two claims in production's own code were verified**. Both hold, and one is
worse than the roadmap recorded.

---

## The finding that reframes everything

**Two of the four production apps compute quality metrics that cannot fail.**

### sentiment_qa

`production-reference/sentiment-voice-analysis-develop/tasks/sentiment_qa/fact_check_task.py:1004-1013`
builds its confusion matrix as `tp = exact matches; fp = mismatches; fn = 0; tn = 0`. Feed that
to `_compute_eval_metrics` (line 1096) and:

| reported metric | what it actually is |
|---|---|
| `accuracy` = (TP+TN)/total | = TP/(TP+FP) |
| `precision` = TP/(TP+FP) | **the same number as accuracy** |
| `recall` = TP/(TP+FN) | **100%, always** |
| `f1` | a monotone transform of precision |

Four reported metrics, one independent measurement, and a constant. Against the configured
bands (`qa_pipeline_fact_check.yml`: accuracy 80/85/90, precision 75/80/90, recall 75/80/90,
f1 80/85/90) **recall can never fail.**

### rtr-fraud-validation

`production-reference/rtr-fraud-validation-main/app/modules/fact_checker.py:312-331` has the
mirror defect. It sorts ground truth and predictions independently, aligns them **by position**
(its own comment says so), truncates to `min_len` rather than refusing, then sets
`y_true = np.ones(min_len)` — "expected = always match". With `y_true` all ones, `FP` and `TN`
are structurally zero in `confusion_metrics` (line 260), so **precision is 100% by
construction** and accuracy ≡ recall. A single missing row also silently misaligns every
comparison after it.

### Why this matters for us, not only for them

`DEVLOG.md`'s roadmap item 1 is a reconciliation run — *"score one real labelled batch and
confirm the numbers match the app's existing Gemini fact-check report."* For at least two apps
**that target is wrong**: matching a degenerate scorer would validate our instrument against a
broken one.

Reconciliation must compare against production's **raw ground truth** using metrics that can
fail, and treat divergence from their published report as an expected finding rather than an
error to be reconciled away.

---

## The sequence

### Tier 0 — the only thing that turns any of this into a verdict

**Reconciliation, with the corrected target above.** `RECONCILED: NO` stands on every artifact
this repository has produced. Blocked on data this repo does not hold;
`docs/ask1-email-draft.md` is the unsent ask, and it should now also carry the scorer finding.

### Tier 1 — cheap, no new data, executable now

**1. MNP adapter.** The cheapest possible test of whether the harness is really multi-app.
`src/evalgen/apps.py` registers exactly one binding, and `binding()`'s own refusal enumerates
what a new app needs: adapter, prompt, schema, testset reference, decision units. Concretely:

- `src/evalharness/adapters/mnp.py`, mirroring `adapters/retention.py`
- `src/evalgen/schemas/mnp.json`
- a prompt under `src/evalgen/prompts/` with a `manifest.json` entry
- a `BINDINGS` entry in `src/evalgen/apps.py`
- a validator branch in `src/evalgen/experiments.py`

`MNP` is already declared at `src/evalharness/labelspaces.py:56` and differs from `RETENTION`
by exactly one reason class. **The adapter must be resolved through
`src/evalharness/adapters/registry.py` from the hashed contract string**, never imported
directly — that is what makes the choice provenance rather than a second table that can
disagree with the hash.

**2. Close the orphan-process hole.** On 2026-08-21 a `transcribe.py` orphaned by a stopped
shell overwrote 23 already-scored transcripts four hours after scoring. The runner catches it
at score time (`experiment21_pipeline_delta.score`'s input-drift refusal) and
`pipeline_scorecard.input_drift` now prints a banner — but **nothing prevents the write**.
A `FROZEN` marker written by `scripts/freeze_corpus.py` and checked by `transcribe.py` before
writing into a corpus root would close it.

**3. Diagnose the `reason` dimension.** Every arm scores between
<!--claim:e24-figures.json:arms.format_control.reason_f1:f3-->0.264<!--/--> and
<!--claim:e24-figures.json:arms.ceiling.reason_f1:f3-->0.277<!--/--> — including the
perfect-transcript ceiling. A dimension where the ceiling arm scores 0.277 is not
discriminating between models; it is measuring something else. Establish which before building
anything on it: the 11-class multi-label set match, the `secondary`/`third` grain, or the
corpus's reason pools.

### Tier 2 — needs authoring or data

**4. Corpus realism: the stated-label ceiling.** `asr-eval/scripts/leak_probe.py` already
reports that every label is spoken aloud, so the corpus measures *fact extraction under
transcription noise* and not *inferential labelling* — every score is an upper bound. The fix
is **not** to hide the labels: the previous build did, and the labeller scored 0.277 with a
perfect transcript. Author a **stated/inferred split** so the gap is measurable rather than
asserted.

**5. sentiment_qa accuracy eval.** We advised production to turn reasoning off on token count
and JSON-validity evidence alone (`docs/reports/token-ab.json`). Their own gate cannot catch an
accuracy regression in three of its four indicators. Blocked on labelled data.

**6. Human panel on the 68 blind cases.** The audit's stated limit is that its reviewers were
models. `scripts/audit_packet.py`, `tests/test_audit_packet_is_blind.py` and
`scripts/audit_score.py` all exist, so this is now cheap to run.

### Tier 3 — new modalities, in build-cost order

**7. Tax invoice extraction** — the easiest shape here: per-field exact match, thresholds
already specified in `fact_check_uat_baseline.yml`. No new statistics.

**8. RTR fraud (vision)** — needs image handling in the runner *and* a scorer that joins on
`RTR_Code` instead of aligning by position. Report the alignment defect regardless of whether
we build this.

**9. Telesale rubric** — genuinely new metric code: additive negative points with per-section
caps is not classification, so `evalharness.metrics` does not apply.

---

## What to send production, independent of any build

Both scorer findings, with the file and line references above. Neither is our code and neither
blocks us, but both mean their fact-check reports give more assurance than they can support.
Pair with the Token Factory asks already drafted (spend-route grant, and whether vLLM's
streaming transcription can be exposed with `stream_include_usage`).

## Verification

From `CLAUDE.md`'s Build and Verification Contract, applied to every item:

1. **Both suite modes** — standalone and `TRUE_SOURCE_ROOT` — never one alone.
2. `scripts/verify.py` — 10 gates; currently 9 pass, 1 pre-existing skip.
3. `scripts/doc_claims.py --check` — currently 193 figures across 3 documents.
4. A new metric gets a **hand-computed expectation authored before the implementation**;
   `tests/fixtures/WORKED-COMPUTATION.md` is the pattern.
5. **No expectation is edited to make a test pass, and no gate is weakened.**

## What this does not do

`RECONCILED: NO` stands until Tier 0 completes. Everything in Tiers 1–3 raises the precision of
an instrument whose accuracy against production is still unmeasured — the same framing
`docs/eval-improvement-plan.md` used on 2026-08-05, and still the honest one.
