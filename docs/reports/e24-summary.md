# Experiment 24 — the checked figures

Every number below is wrapped in a `doc_claims` marker and verified against
[`e24-figures.json`](./e24-figures.json), which is generated from the run by
`scripts/e24_figures.py` and never typed by hand.

**Why this file exists.** The prose write-up lives in
[`benchmark-audit-e24.html`](./benchmark-audit-e24.html). That document is HTML, and
`doc_claims.py` reads markdown — so until this file existed, *every ASR-track figure this
project published sat under no check at all*, while the text track's migration decision had 77
figures gated. That gap cost real accuracy twice in a single day: E23's Gemini F1s were
published from a scorer that did not match the preregistration, and an ASR latency was quoted
from a record an orphaned process had overwritten. Both were caught by hand, late. This is what
catches the third one.

Regenerate and verify:

```bash
PYTHONPATH=src python scripts/e24_figures.py --run out/runs/<id> --pack asr-eval-v3
PYTHONPATH=src python scripts/e24_figures.py --run out/runs/<id> --check   # stale?
PYTHONPATH=src python scripts/doc_claims.py --check
```

---

## The blind audit

<!--claim:e24-figures.json:audit.reviewers:int-->3<!--/--> independent frontier models —
none of them an arm in this evaluation — each shown only the Thai transcript and the written
spec, over <!--claim:e24-figures.json:audit.cases:int-->68<!--/--> cases.

| group | agree | against |
|---|---:|---:|
| control | <!--claim:e24-figures.json:audit.control_agree:int-->28<!--/--> | <!--claim:e24-figures.json:audit.control_against:int-->2<!--/--> |
| product_mismatch | <!--claim:e24-figures.json:audit.product_mismatch_agree:int-->8<!--/--> | <!--claim:e24-figures.json:audit.product_mismatch_against:int-->17<!--/--> |
| outcome_error | <!--claim:e24-figures.json:audit.outcome_error_agree:int-->5<!--/--> | <!--claim:e24-figures.json:audit.outcome_error_against:int-->5<!--/--> |

- control agreement **<!--claim:e24-figures.json:audit.control_agreement:pct1-->93.3%<!--/-->**
  — the reviewers could do the task, which is what makes the rest readable
- disputed against the corpus **<!--claim:e24-figures.json:audit.disputed_against_rate:pct1-->62.9%<!--/-->**
  against a preregistered threshold of
  <!--claim:e24-figures.json:audit.threshold:pct1-->20.0%<!--/-->
- <!--claim:e24-figures.json:audit.no_majority:int-->3<!--/--> cases reached no majority and
  are dropped from the scored set rather than rounded to a side

## The churn-miss sample

The sample was seeded from the churn misses. On the undisputed churn controls the reviewers
agree <!--claim:e24-figures.json:churn_sample.control_agree:int-->16<!--/--> of
<!--claim:e24-figures.json:churn_sample.control_n:int-->17<!--/-->; on the disputed ones they
go against the corpus <!--claim:e24-figures.json:churn_sample.disputed_against:int-->10<!--/-->
of <!--claim:e24-figures.json:churn_sample.disputed_n:int-->18<!--/--> —
<!--claim:e24-figures.json:churn_sample.disputed_against_rate:pct1-->55.6%<!--/-->. Eleven of
the thirteen corrections they made were to `product`, not to the outcome.

## Business figures

<!--claim:e24-figures.json:calls:int-->138<!--/--> calls, replicate
<!--claim:e24-figures.json:replicate_policy:text-->first<!--/--> as preregistered.

| arm | call_result F1 | reason F1 | product F1 |
|---|---:|---:|---:|
| ceiling | <!--claim:e24-figures.json:arms.ceiling.call_result_f1:f3-->0.839<!--/--> | <!--claim:e24-figures.json:arms.ceiling.reason_f1:f3-->0.277<!--/--> | <!--claim:e24-figures.json:arms.ceiling.product_f1:f3-->0.881<!--/--> |
| format-control | <!--claim:e24-figures.json:arms.format_control.call_result_f1:f3-->0.836<!--/--> | <!--claim:e24-figures.json:arms.format_control.reason_f1:f3-->0.264<!--/--> | <!--claim:e24-figures.json:arms.format_control.product_f1:f3-->0.836<!--/--> |
| typhoon-pipeline | <!--claim:e24-figures.json:arms.typhoon_pipeline.call_result_f1:f3-->0.730<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.reason_f1:f3-->0.272<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.product_f1:f3-->0.864<!--/--> |
| gemini-audio | <!--claim:e24-figures.json:arms.gemini_audio.call_result_f1:f3-->0.564<!--/--> | <!--claim:e24-figures.json:arms.gemini_audio.reason_f1:f3-->0.272<!--/--> | <!--claim:e24-figures.json:arms.gemini_audio.product_f1:f3-->0.798<!--/--> |

`qwen-pipeline` is **UNAVAILABLE** and therefore absent rather than reported at zero — see
[`../qwen-asr-outage-2026-08-21.txt`](../qwen-asr-outage-2026-08-21.txt).

## Reliability

| arm | parse-valid | unstable of 138 |
|---|---:|---:|
| ceiling | <!--claim:e24-figures.json:arms.ceiling.parse_valid,arms.ceiling.label_calls:frac-->414/414<!--/--> | <!--claim:e24-figures.json:arms.ceiling.unstable_items:int-->21<!--/--> |
| format-control | <!--claim:e24-figures.json:arms.format_control.parse_valid,arms.format_control.label_calls:frac-->414/414<!--/--> | <!--claim:e24-figures.json:arms.format_control.unstable_items:int-->31<!--/--> |
| typhoon-pipeline | <!--claim:e24-figures.json:arms.typhoon_pipeline.parse_valid,arms.typhoon_pipeline.label_calls:frac-->414/414<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.unstable_items:int-->33<!--/--> |
| gemini-audio | <!--claim:e24-figures.json:arms.gemini_audio.parse_valid,arms.gemini_audio.label_calls:frac-->411/414<!--/--> | <!--claim:e24-figures.json:arms.gemini_audio.unstable_items:int-->17<!--/--> |

The gate is 410 of 414 (99%). **Every arm passes**, the incumbent at
<!--claim:e24-figures.json:arms.gemini_audio.parse_valid:int-->411<!--/-->. Instability is a
standing property of the task, not a difference between runs — E23's Typhoon was 30 of 138.

## Tokens, by stage

Both shapes are handed the same audio. The one-call arm carries it **as tokens** and is billed
for it; the pipeline sends it to a transcription endpoint that reports no usage at all, then
sends only text to the labeller. A single input-token row would compare one arm's whole job
against the other's second half.

| | Gemini | Typhoon → Qwen3.8 |
|---|---:|---:|
| stage 1 in — audio | <!--claim:e24-figures.json:arms.gemini_audio.audio_tokens_med:int-->9175<!--/--> | not token-metered |
| stage 2 in — text | no second call | <!--claim:e24-figures.json:arms.typhoon_pipeline.text_tokens_med:int-->3660<!--/--> |
| prompt + schema | <!--claim:e24-figures.json:arms.gemini_audio.text_tokens_med:int-->2440<!--/--> | included above |
| billed input, median | <!--claim:e24-figures.json:arms.gemini_audio.input_tokens_med:int-->11615<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.input_tokens_med:int-->3660<!--/--> |
| output, median | <!--claim:e24-figures.json:arms.gemini_audio.output_tokens_med:int-->233<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.output_tokens_med:int-->246<!--/--> |
| label latency, median (s) | <!--claim:e24-figures.json:arms.gemini_audio.label_latency_med_s:f3-->5.200<!--/--> | <!--claim:e24-figures.json:arms.typhoon_pipeline.label_latency_med_s:f3-->18.300<!--/--> |

Metered cost is OpenRouter only: **$<!--claim:e24-figures.json:arms.gemini_audio.metered_cost_usd:f3-->3.855<!--/-->**
across the incumbent's <!--claim:e24-figures.json:arms.gemini_audio.label_calls:int-->414<!--/-->
calls. The internal arms run on company GPU with no per-call price, which is not the same as
being free.

## What correcting the corpus changed

The fix moved exactly one field, `product`, and the audio moved on exactly the rows whose label
moved. That gives three cells:
<!--claim:e24-figures.json:corpus_fix.control:int-->57<!--/--> control,
<!--claim:e24-figures.json:corpus_fix.contradicted:int-->30<!--/--> contradicted,
<!--claim:e24-figures.json:corpus_fix.rerolled:int-->51<!--/--> re-rolled, of
<!--claim:e24-figures.json:corpus_fix.total_calls:int-->138<!--/-->.

| arm | control Δ | contradicted before → after | re-rolled Δ |
|---|---:|---:|---:|
| ceiling | <!--claim:e24-figures.json:corpus_fix.ceiling.control.delta:pct1-->0.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.ceiling.contradicted.before:pct1-->58.6%<!--/--> → <!--claim:e24-figures.json:corpus_fix.ceiling.contradicted.after:pct1-->100.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.ceiling.rerolled.delta:pct1-->-3.9%<!--/--> |
| format-control | <!--claim:e24-figures.json:corpus_fix.format_control.control.delta:pct1-->0.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.format_control.contradicted.before:pct1-->72.4%<!--/--> → <!--claim:e24-figures.json:corpus_fix.format_control.contradicted.after:pct1-->100.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.format_control.rerolled.delta:pct1-->-7.8%<!--/--> |
| typhoon-pipeline | <!--claim:e24-figures.json:corpus_fix.typhoon_pipeline.control.delta:pct1-->0.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.typhoon_pipeline.contradicted.before:pct1-->53.3%<!--/--> → <!--claim:e24-figures.json:corpus_fix.typhoon_pipeline.contradicted.after:pct1-->100.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.typhoon_pipeline.rerolled.delta:pct1-->-7.8%<!--/--> |
| gemini-audio | <!--claim:e24-figures.json:corpus_fix.gemini_audio.control.delta:pct1-->2.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.gemini_audio.contradicted.before:pct1-->53.6%<!--/--> → <!--claim:e24-figures.json:corpus_fix.gemini_audio.contradicted.after:pct1-->100.0%<!--/--> | <!--claim:e24-figures.json:corpus_fix.gemini_audio.rerolled.delta:pct1-->-6.5%<!--/--> |

The control cell is the noise floor: same audio bytes, same key, two runs. It moves ~0 while
the contradicted cell reaches 100% on every arm, and the re-rolled cell moves **down** — which
is the check that matters, because a corpus that had merely got easier would have lifted those
too.

---

`RECONCILED: NO`. The reviewers were models, not people. Correcting a synthetic corpus against
its own written spec does not move it one step closer to a production call.
