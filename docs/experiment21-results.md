# Experiment 21 — the pipeline delta: does the machine transcript change the final answer?

**Run:** `20260817-141155Z-e21` · 6 arms × 20 calls × 3 replicates = 360 label calls, 356 ok,
4 `parse_failed` (all on the audio arm) · prompt `v9_16_base` (`968a2974f0ce…`), audio arm
uses the mechanically reversed production wording (`3734461f1cee…`, each of the six
`AUDIO_TO_TRANSCRIPT` pairs un-applied exactly once) · schema `retention.json` through
`decoding_schema` · decoding pinned: temperature 0, top_p 1, seed 0 · every input's sha256
recorded before the first call; scoring verifies them.

**Design in one sentence.** One labeller, one prompt, one schema, run over six versions of
the same 20 synthetic Thai calls that differ only in how the text got there — a perfect
reference, a *format-only* degradation of that reference (zero transcription error), the two
real ASR transcripts, and the production-shaped one-call audio arm — and count where the
final JSON changes. **Every figure here is agreement, never accuracy**: no arm is scored as
right, and a labeller that is reliably wrong agrees with itself 100%.

## The headline

**The business decision never changed. Across all 351 successful label calls — every input
format, both ASR transcripts, and the raw audio — there is not one call on which any arm
disagrees with any other about the set of outcome values.** Where the ceiling says a call is
`churn`, every arm says `churn`; where `save`, every arm says `save`. The save/churn label,
the one production acts on, was invariant to the entire ASR step on this set.

What *does* change is everything around it:

| vs the ceiling (same labeller, Qwen3.8) | calls where the JSON changed | retention-shaped (of 6) | other (of 14) |
|---|---:|---:|---:|
| **format-control** — zero transcription error | **10 / 20** | 5 | 5 |
| qwen-pipeline (Qwen3-ASR transcript) | 12 / 20 | 5 | 7 |
| gemini-asr-text (Gemini's transcript) | 12 / 20 | 4 | 8 |

Read those three rows together: **formatting alone changes the emitted JSON on half the
calls, and adding real transcription error on top of it moves that by only two calls.** Of
the qwen-pipeline's 12 changed calls, 9 also changed under the format-only control; the three
that are genuinely candidate-specific are ASR-004 and ASR-011 (product attribution moved) and
ASR-012 (the known repetition-loop transcript). Gemini's ASR adds five of its own — including
ASR-019, one of its silent passage-drop calls.

And what changed is attribution, not decision. Field-level, the changes vs the ceiling are:
reason wording (18 field changes), which product key carries the outcome (11), and the set of
product keys (5). Example, ASR-004: ceiling emits `TOL: save`; the candidate pipeline emits
`Postpaid: save` — same decision, different product row.

## The second headline: the least stable component is production's own arm

Within-arm noise floor — the fraction of fields where three byte-identical requests did not
return the same answer:

| arm | labeller | noise floor | parse failures |
|---|---|---:|---:|
| ceiling | Qwen3.8 | **0.0** | 0 |
| format-control | Qwen3.8 | **0.0** | 0 |
| qwen-pipeline | Qwen3.8 | **0.0** | 0 |
| gemini-asr-text | Qwen3.8 | **0.0** | 0 |
| ceiling-gemini | Gemini 2.5 Flash | 0.056 | 0 |
| **gemini-audio** (production shape) | Gemini 2.5 Flash | **0.265** | **4 / 60** |

The four Qwen3.8 arms were perfectly deterministic across the whole run — 240 calls, not one
field moved between replicates. The production-shaped audio arm changed roughly a quarter of
its fields between identical requests at temperature 0, and returned unparseable JSON on 4 of
60 calls despite the enforced schema. Comparisons against it are dominated by its own noise:
in the candidate-vs-production table, most non-SAME verdicts are `NOISE` (production's
replicates disagree with themselves) rather than `CHANGED`.

| pair | population | SAME | CHANGED | NOISE | PARSE_FAILED |
|---|---|---:|---:|---:|---:|
| qwen-pipeline vs gemini-audio | retention-shaped | 1 | 4 | 1 | 0 |
| qwen-pipeline vs gemini-audio | other | 3 | 7 | 2 | 2 |
| ceiling-gemini vs gemini-audio | retention-shaped | 2 | 3 | 1 | 0 |
| ceiling-gemini vs gemini-audio | other | 3 | 2 | 5 | 4 |

Even Gemini labelling its own perfect transcript disagrees with Gemini labelling the audio on
5 calls with 6 more in noise/parse-failure — so "the candidate disagrees with production" and
"production disagrees with itself" are the same order of magnitude, and no verdict about the
candidate can honestly be extracted from this pairing at n=20. UNDERPOWERED is the result.

## What this says, and does not say

1. **The ASR step is not the risk this set can detect.** The decision label survived every
   transcript, including the looped one. The measurable effects of swapping transcription are
   in product attribution and reason wording — real, because production writes per-product
   rows, but second-order next to the label itself.
2. **The labeller's format sensitivity is larger than ASR quality.** A zero-error formatting
   change moved half the JSONs. Anyone comparing pipelines on this task must hold input
   format constant or they are measuring formatting, as this project has now demonstrated on
   three separate occasions.
3. **The production-shaped arm is the reliability outlier** — 26.5% field instability and a
   7% parse-failure rate under a pinned seed. The candidate pipeline was byte-stable. This is
   an argument *for* the two-stage architecture that has nothing to do with transcription
   accuracy.
4. **Not shown:** that either pipeline is *correct* (nothing here is scored against truth);
   anything about real audio (all synthetic TTS); anything at production scale (n=20, and
   only 6 calls are retention-shaped — the 14 others exercise the schema outside its design).

`RECONCILED: NO.` Synthetic audio, synthetic calls, no production data. Raw responses under
`out/runs/20260817-141155Z-e21/` (gitignored); `agreement.json` there holds every verdict this
report summarises.

## Reproduce

```bash
PYTHONPATH=src python scripts/experiment21_pipeline_delta.py --dry-run   # inputs + parity, no calls
PYTHONPATH=src python scripts/experiment21_pipeline_delta.py             # the full 360
PYTHONPATH=src python scripts/experiment21_pipeline_delta.py --score out/runs/<ts>-e21
```
