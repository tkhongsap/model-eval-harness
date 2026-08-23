# Review: `ai-local-eval-sentiment_project_v2`

**Date:** 2026-08-24 · **Reviewer:** this repository's maintainer session
**Subject:** `production-reference/ai-local-eval-sentiment_project_v2`, internally `model_migration`
**Verdict:** a serious, larger, independently-built evaluation of overlapping scope. It
corroborates two of our findings and beats us on a third.

## What it is

Arrived in `production-reference/` on 2026-08-24. Measured from the filesystem, not its README:

| | |
|---|---|
| task areas | sentiment, sentiment_mnp, sentiment_retention, sentiment_telesale, documents |
| model families | `google_model/` and `local_model/` |
| source | 70 files, 53,670 lines |
| tests | 30 files, 13,846 lines |

Its own `CLAUDE.md` calls the project "currently a platform scaffold" whose tracked code is a
logging library and a SharePoint hook. **That description is stale** — what is on disk is a
full dual-family evaluation across five task areas. Read the filesystem, not that file.

## It found the production scorer defect before we did, and quantified it better

We reported on 2026-08-22 that `sentiment_qa` and `rtr-fraud-validation` compute metrics that
cannot fail (`docs/eval-harness-next-steps.md`). This project had already found the same thing
in `sentiment_telesale` and written it into its code —
`src/local_model/sentiment_telesale/internal_confusion_matrix.py:13`:

> *"`FactCheckTask._evaluate` in `D:/sentiment-voice-analysis` sets `FN = 0` and `TN = 0`
> outright, so every `Confusion_Matrix_*` tab of `Human Groundtruth.xlsx` reports Recall
> 1.0000 on all 39 items."*

And then the number we did not have:

> *"Measured on the real tab, a model that catches **none** of the 25 violations scores
> 0.9813 / 0.9904 / 0.9855 / 0.9935 by category under that formula."*

**A model that detects zero violations scores 98–99%.** That is a far more forceful statement
of the defect than "recall is structurally 1.0", and it is measured on the stakeholders' actual
workbook rather than derived from the formula. Any report we send about this should quote their
number and credit the source.

They also acted on it: they deleted their own exact-match scorer after verifying its `Accuracy`
column matched to 0.0 absolute difference while its other three metrics were degenerate for the
same reason. Deleting a scorer that agrees with the survivor on the only column carrying
information is the right call and an uncomfortable one to make.

## A second finding of theirs we did not have at all

From the same docstring:

> *"25 of the 39 criteria carry a single label across all 26 calls, and 5 of the 14
> sub-categories contain no violation at all, so on those a constant answer and a perfect model
> are indistinguishable."*

That is about the **ground truth**, not the scorer, and it is a different failure from the one
we found. It is the telesale analogue of what our blind audit found in the retention corpus:
a benchmark that cannot separate a good model from a fixed answer. Worth checking whether our
own dimensions have the same property — `reason` sitting at 0.264–0.277 across every arm
including the perfect-transcript ceiling is exactly the shape of symptom this would produce.

## Where it independently agrees with us

Its metrics schema (`src/local_model/*/schema/metrics_schema.py`) carries `label_*` and
`analysis_*` token fields, a separate float `Audio Seconds` column, and **no
transcription-stage token fields** — *"in different units and scales — Whisper reports
duration, hence float `Audio Seconds`"*. It also records that *"Blank token cells mean 'not
reported', never 0"*.

Both are conclusions this repository reached independently on 2026-08-22, in different code,
without knowledge of theirs. Two teams landing on the same two answers is the strongest
evidence either of us has that the answers are right.

## Where it has something we lack

**Gemini's native `usageMetadata`.** Their `tests/test_usage_metrics.py` exercises a real
response body carrying a per-modality token split (`AUDIO`, `TEXT`), `cached_tokens` and
`thoughts_tokens` — `prompt_tokens` 50,757 of which `cached_tokens` 31,813.

We read Gemini through OpenRouter, get a flatter shape, and measured `cached_tokens: 0`. Two
things follow. Their access is better instrumented for the input-token question that started
this whole thread, and **the caching difference is worth understanding on its own** — if
production caches ~31k tokens per call and our measurements do not, our cost figures for the
incumbent are not production's cost figures.

They also independently measure *"the analysis call alone measures ~35k prompt tokens"*, which
matches the 30–40k figure reported from production and our own ~31,400.

## What we should do

1. **Quote their 98–99% number** in anything we send about the scorer defect, with attribution.
   It is more persuasive than ours and it is measured on the real workbook.
2. **Check our own dimensions for their ground-truth finding.** `reason` is the candidate.
3. **Ask why their Gemini calls cache and ours do not.** This bears directly on whether our
   published incumbent cost is representative.
4. **Settle the overlap question before building MNP here.** They have it; we do not. See
   `docs/eval-harness-next-steps.md` for the three honest options.

## What this review does not claim

The area-to-task correspondence (`sentiment` → sentiment_qa, `documents` → tax invoice
extraction) is **by name only** and has not been verified against either side's schema. Nor
have their numbers been reproduced — this is a review of what the code says it does and of the
findings written into it, not a re-run of their results.
