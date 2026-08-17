# Reports

Rendered reports — the things you open in a browser or send to somebody. Everything here
is a **finished artifact**, not an analysis: the written analyses live one level up in
`docs/`, and the raw evidence lives in `out/`, which is gitignored.

**The boundary is deliberate.** A file belongs here when it is a rendered deliverable
whose numbers came from somewhere else. It does not belong here if it carries model
output verbatim — that is the rule `.gitignore` enforces at `out/`, and this folder is
inside the repository, so nothing with a transcript or a completion in it can live here.

Which comparison a report covers is a **config**, not a code change:
`configs/comparison/<pack>.json` names the runs, the arms, the expected shape and the output
paths. `--config` selects one. Adding a pack or a model is a new config file, not an edit to
the generator.

| Report | What it answers | Regenerate |
|---|---|---|
| **[model-comparison-combined.html](./model-comparison-combined.html)** | **Both evaluation sets in one document — the one to send.** What is being tested, what gets scored, what the two sets contain, and the results per set. Covers `retention_v3` and `retention_challenge_v1` side by side, never pooled. | `PYTHONPATH=src python scripts/combined_report.py` |
| [model-comparison.html](./model-comparison.html) | Four models on `retention_v3` alone — the per-set detail behind the combined page. | `PYTHONPATH=src python scripts/model_comparison_report.py` |
| [model-comparison-fragment.html](./model-comparison-fragment.html) | The same page without the document shell, for publishing as an Artifact. | same command — both come from one computation |
| [model-comparison-metrics.json](./model-comparison-metrics.json) | Every figure on that page, machine-readable. Read this rather than scraping the HTML. `scripts/case_explorer.py` consumes it. | same command |
| [model-comparison-challenge.html](./model-comparison-challenge.html) | **The harder pack.** Gemini vs Qwen3.8 vs Gemma 4 on `retention_challenge_v1` — 50 calls built to stress interaction *structure* (prior-contact history, mid-call reversal, competing issues, interruption and topic return), with 11 of 50 calls carrying more than one product. Same layout as the page above. | `PYTHONPATH=src python scripts/model_comparison_report.py --config configs/comparison/retention-challenge-v1.json` |
| `model-comparison-challenge-fragment.html`, `-metrics.json` | As above, for that pack. | same command |
| [asr-comparison.html](./asr-comparison.html) | **The voice track.** Gemini 2.5 Flash against Qwen3-ASR 1.7B on our own GPU, transcribing 20 synthetic Thai calls. Measures the speech-to-text step that a text-only candidate needs and production does not have — CER, entity recovery, and the two figures that need a caveat before reading. | `python scripts/asr_comparison_report.py` |
| `asr-comparison-fragment.html` | The same page without the document shell. | same command — both come from one computation |
| [soak-test-report.html](./soak-test-report.html) | The 5-hour GPU soak: concurrency ramp, latency by phase, error rate, and the recommended operating concurrency. | `python scripts/soak_report_html.py out/soak/<run> --standalone --out docs/reports/soak-test-report.html` |
| [experiment17-report.html](./experiment17-report.html) | Experiment 17 — the internal-GPU arms against Gemini, and the run where Gemini's determinism collapsed. | **Hand-written.** No script regenerates it; edit the file. |

## The one that is not here

The **case explorer** — 138 cases with transcripts, ground truth and all four models'
answers — is generated to `out/case-explorer.html` and is **deliberately not committed**.
It embeds raw completions and full transcripts, which is exactly what `out/` exists to
keep out of git. Regenerate it in about a minute:

```
PYTHONPATH=src python scripts/case_explorer.py                 # retention_v3  -> out/case-explorer.html
PYTHONPATH=src python scripts/case_explorer.py \
    --config configs/comparison/retention-challenge-v1.json    # challenge pack -> out/case-explorer-challenge.html
```

See [docs/case-explorer.md](../case-explorer.md) for what it shows and how it checks
itself.

## Where the numbers come from

Every report here is downstream of a run directory under `out/runs/`, which is gitignored.
[RUNS.md](../../RUNS.md) is the committed index of those runs — provenance only, no
payloads — so a `run_id` quoted in a report can always be resolved to what it was pinned
to, even on a clone that has none of the runs.

The four runs behind the current comparison:

| Model | run_id |
|---|---|
| Gemini 2.5 Flash *(production)* | `20260814-132425Z-e17-gemini` |
| Qwen3.8 27B | `20260815-124600Z-e18-tf-qwen38` |
| Qwen3.6 27B | `20260814-134803Z-e17-tf-qwen` |
| Gemma 4 12B | `20260814-132642Z-e17-tf-gemma` |

All four share one `testset_sha`, `prompt_sha`, `scoring_code_sha`, `repeats` and `items`
— the generator refuses to build a comparison out of runs that do not.
