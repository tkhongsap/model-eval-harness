# Qwen3.6 27B vs Gemini 2.5 Flash — external benchmarks vs this harness

**Compiled:** 2026-08-05
**Source:** [Artificial Analysis](https://artificialanalysis.ai/models/), retrieved 2026-08-05
**Status:** Reference only. Nothing here is a migration verdict — see [Reading this document](#reading-this-document).

This note records what public benchmarks say about the two models in the Retention
evaluation, why those benchmarks disagree with this repository's own measurements, and
which of the two should carry weight for the migration decision.

---

## 1. Headline comparison

| Dimension | Qwen3.6 27B | Gemini 2.5 Flash | Advantage |
|---|---|---|---|
| Intelligence Index (v4.1) | **37** (reasoning) | 14 (non-reasoning, est.) | Qwen — but see §3 |
| Reasoning model? | Yes, extended CoT | No, direct response | — |
| Price, input /1M | $0.60 | **$0.30** | Gemini, 2x |
| Price, output /1M | $3.60 | **$2.50** | Gemini, 1.4x |
| Price, blended /1M | $0.90 | **$0.33** | Gemini, 2.7x |
| Output speed | 58.9 tok/s | **212.1 tok/s** | Gemini, 3.6x |
| Latency (TTFT) | 3.70s | **0.46s** | Gemini, 8x |
| Context window | 262k | **1,000k** | Gemini, 3.8x |
| Verbosity (tokens to run the index) | 140M (median 37M) | not reported | Qwen very verbose |
| Parameters | 27.8B dense | undisclosed | — |
| Licence | **Apache 2.0, open weights** | proprietary | Qwen |
| Input modalities | text, image, video | text, image, audio, video | Gemini |
| Released | 2026-04-22, Alibaba | Google | — |

**No Thai or multilingual benchmark is published for either model.**

### A discrepancy worth recording

Artificial Analysis's launch announcement put Qwen3.6 27B at **46** on the Intelligence
Index; the live model page now reads **37**. AA rebases the index periodically, so these
are almost certainly different index versions rather than a correction — but that was not
confirmable from the page. Treat the absolute number as version-dependent, and only ever
compare scores measured on the same index version.

---

## 2. What the Intelligence Index is made of (v4.1)

Nine evaluations in four weighted categories:

| Category | Weight | Benchmark | Weight | Measures |
|---|---|---|---|---|
| **Agents** | 34% | GDPval-AA v2 | 20% | Economically valuable tasks across 44 US occupations, scored pairwise against human experts |
| | | τ³-Banking | 14% | Multi-step tool use plus retrieval from banking policy documents |
| **Coding** | 24% | Terminal-Bench v2.1 | 16% | 89 terminal tasks — software engineering, sysadmin, data processing |
| | | SciCode | 8% | Scientific Python, unit-test validated |
| **Scientific Reasoning** | 24% | Humanity's Last Exam | 12% | 2,158 academic questions |
| | | GPQA Diamond | 6% | 198 graduate-level science MCQs |
| | | CritPt | 6% | 70 research-level physics problems |
| **General** | 18% | AA-Omniscience | 12% | Factual accuracy (8%) + non-hallucination rate (4%) |
| | | AA-LCR | 6% | Long-context reasoning over ~100k-token documents |

**MMLU-Pro is no longer in the index.** It was part of v3.0, alongside LiveCodeBench,
AIME 2025, IFBench and τ²-Bench Telecom, and was dropped in v4.1 — the usual reason being
saturation, where strong models cluster near the ceiling and the benchmark stops
separating them. Plain MMLU was retired earlier still. **SWE-bench is also absent**;
Terminal-Bench is AA's agentic-coding proxy.

---

## 2b. Benchmark-by-benchmark comparison

**Read the provenance warning below before using this table.**

### Composite

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash | Source |
|---|---|---|---|
| **AA Intelligence Index v4.1** | **37** | **14** | AA, independent — *the only like-for-like row* |

### Knowledge and reasoning

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash | Note |
|---|---|---|---|
| MMLU-Pro | **86.2** | 83.2 | |
| MMLU-Redux | 93.5 | — | |
| GPQA Diamond | **87.8** | 79.0 *(82.8 elsewhere)* | Sources conflict for Gemini |
| SuperGPQA | 66.0 | — | |
| Humanity's Last Exam | **24.0** | 12.1 | |
| C-Eval | 91.4 | — | Chinese |
| SimpleBench | — | 41.2 | |
| ARC-AGI / ARC-AGI-2 | — | 33.3 / 22.5 | |

### Coding and software engineering

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash | Note |
|---|---|---|---|
| **SWE-bench Verified** | **77.2** | 60.3 *(48.9 single-attempt)* | |
| SWE-bench Pro | 53.5 | — | |
| SWE-bench Multilingual | 71.3 | — | |
| Terminal-Bench | **59.3** (v2.0) | 17.1 (version unstated) | ⚠️ likely different versions |
| LiveCodeBench | **83.9** (v6) | 69.5 (version unstated) | ⚠️ likely different versions |
| SciCode | — | 39.4 | |
| NL2Repo | 36.2 | — | |

### Mathematics

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash | Note |
|---|---|---|---|
| AIME | **94.1** (AIME26) | 73.1 (2024/25) *(88.0 for 2024 elsewhere)* | ⚠️ different years |
| HMMT | 93.8 / 90.7 / 84.3 | — | Feb25 / Nov25 / Feb26 |
| IMOAnswerBench | 80.8 | — | |
| FrontierMath | — | 4.8 | |

### Agentic and tool use

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash | Note |
|---|---|---|---|
| τ²-bench | — | 31.6 | |
| QwenWebBench | 1487 | — | Qwen-authored |
| SkillsBench Avg5 | 48.2 | — | |
| AndroidWorld | 70.3 | — | |
| APEX | — | 1.8 | |

### Multilingual — the closest thing to relevant, and it is not close

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash |
|---|---|---|
| SWE-bench Multilingual | 71.3 | — |
| Global-MMLU-Lite | — | 88.4 |
| C-Eval (Chinese) | 91.4 | — |
| **Any Thai benchmark** | **none published** | **none published** |

### Vision (not used by this workload, listed for completeness)

| Benchmark | Qwen3.6 27B | Gemini 2.5 Flash |
|---|---|---|
| MMMU | **82.9** | 79.7 |
| MMMU-Pro | 75.8 | — |
| MathVista mini | 87.4 | — |
| OCRBench | 89.4 | — |

### ⚠️ Provenance warning — why most of this table is weak evidence

Only the **AA Intelligence Index** row is measured by one party under one methodology.
Everything else is **vendor-reported or third-party aggregated**, and three specific
problems apply:

1. **Different modes.** Most published Gemini 2.5 Flash figures are for the
   **thinking-enabled** variant. Production runs `thinkingBudget: 0`. The Gemini column
   therefore **overstates** what production actually gets — AA's non-reasoning score of
   14 is the production-representative figure, and it is far below what these
   vendor rows imply.
2. **Different benchmark versions.** Terminal-Bench 2.0 (Qwen) against an unstated
   Terminal-Bench version (Gemini) is not a comparison. Same for LiveCodeBench v6, and
   for AIME 2026 against AIME 2024/2025.
3. **Sources disagree with each other.** Gemini's GPQA Diamond appears as both 79.0 and
   82.8, and AIME as both 73.1 and 88.0, depending on which aggregator is consulted.
   Both readings are recorded above rather than one being silently chosen.

Qwen genuinely does lead on most rows where both models have a number — but the margins
are inflated by items 1 and 2, and **not one row in this table tests Thai-language
classification**, which is the only capability this migration depends on.

---

## 3. The comparison is not like-for-like

AA compares **Qwen in reasoning mode** against **Gemini in non-reasoning mode** — Qwen at
its most capable against Gemini at its cheapest. Two consequences:

1. **Gemini's row is the production-relevant one.** `config/model_setting/retention.yml`
   sets `thinkingBudget: 0`, so non-reasoning is what production actually runs.
2. **Qwen's row is not.** The 37 comes from reasoning mode — the regime `DEVLOG.md`
   records production as *not* running, and for which this harness found Qwen's only
   non-reasoning OpenRouter endpoint (Alibaba) returns 20 of 20 schema violations.

A 37-vs-14 gap therefore overstates Qwen's deployable advantage for this workload.

---

## 4. How much of the index is relevant to the Retention task?

The task is: read Thai call-transcript text, apply a closed rule set, emit valid JSON,
do not invent labels.

| Benchmark | Weight | Relevance |
|---|---|---|
| Terminal-Bench v2.1 | 16% | None — no terminal, no agent loop |
| SciCode | 8% | None |
| CritPt | 6% | None |
| GPQA Diamond | 6% | None |
| Humanity's Last Exam | 12% | Near-none |
| τ³-Banking | 14% | Low — no tools; slight parallel in policy adherence |
| AA-LCR | 6% | Low — the prompt is ~9.6k characters, not 100k |
| GDPval-AA v2 | 20% | Weak to moderate — professional task quality, but no Thai and no classification |
| AA-Omniscience, non-hallucination half | **4%** | **Genuinely relevant** — the same failure mode this harness measures |

**Roughly 4–10% of the Intelligence Index touches anything this workload exercises.**
Close to 60% is agentic coding and scientific reasoning, and no component tests Thai or
any non-English language.

This resolves the apparent contradiction between the two sources. AA reports a large gap
(37 vs 14); this harness reports the two arms as indistinguishable. Both are correct
measurements of different things. A 27B reasoning model can be substantially better at
graduate physics and terminal agents while being identical at "read a Thai transcript and
pick one of four outcomes".

---

## 5. Where the external numbers agree with this harness

| | Artificial Analysis | This harness (production regime) |
|---|---|---|
| Speed | Qwen 3.6x slower | Qwen 4.2s vs Gemini 2.3s median per call |
| Cost | Qwen 2.7x pricier (blended) | **Near parity — $0.078 vs $0.069** |

Direction agrees on speed, which is mild evidence both are measuring the same models
correctly. The cost divergence is explainable rather than contradictory: AA's blended rate
absorbs Qwen's reasoning verbosity (140M tokens against a 37M median), while the Retention
task emits one short JSON object, so that premium largely does not apply.

---

## 6. What to take from this

- **The Intelligence Index is close to useless as evidence for this migration.** It is
  weighted toward capabilities the Retention task does not use, and measures no Thai.
- **The one component worth tracking is AA-Omniscience's non-hallucination rate.** It is
  the same failure mode `src/evalgen/fabrication.py` already counts — models inventing
  reason labels into ground-truth-blank slots, measured here at 42% (Gemini) and 63%
  (Qwen) on the base prompt.
- **This repository's own evaluation remains the only evidence on the dimension that
  matters**, and it reports INDISTINGUISHABLE, not a Qwen win. See `EXPERIMENTS.md`.

---

## Reading this document

Two standing caveats apply to every internal number quoted above, and are reproduced here
so this file can be read on its own:

- **`RECONCILED: NO`** — no harness figure has been checked against the Retention app's
  own live Gemini fact-check report. Nothing in this repository can perform that check.
  Until it is done these are harness output, not evidence about a model.
- **Production is handed AUDIO; this harness is handed pre-tagged Thai TEXT.** Agent-speech
  misattribution, ASR error and diarisation are invisible here, and they are a large part
  of where production error actually lives. Ranking transfer to audio is untested.

The Thai in the test pack was drafted by an LLM and has no native-speaker sign-off.

---

## Sources

- [Artificial Analysis — Models](https://artificialanalysis.ai/models/)
- [AA Intelligence Benchmarking Methodology — v4.1 composition and weights](https://artificialanalysis.ai/methodology/intelligence-benchmarking)
- [AA Intelligence Index leaderboard](https://artificialanalysis.ai/evaluations/artificial-analysis-intelligence-index)
- [Qwen3.6 27B — model page](https://artificialanalysis.ai/models/qwen3-6-27b)
- [Gemini 2.5 Flash — model page](https://artificialanalysis.ai/models/gemini-2-5-flash)
- [Qwen3.6 27B vs Gemini 2.5 Flash — comparison page](https://artificialanalysis.ai/models/comparisons/qwen3-6-27b-vs-gemini-2-5-flash)
- [Artificial Analysis on X — Qwen3.6 launch figures](https://x.com/ArtificialAnlys/status/2049881951260283097)
- [Qwen/Qwen3.6-27B — official model card, full benchmark table](https://huggingface.co/Qwen/Qwen3.6-27B)
- [Qwen3.6-27B release coverage — vendor benchmarks](https://the-decoder.com/qwen3-6-27b-beats-much-larger-predecessor-on-most-coding-benchmarks/)
- [Gemini 2.5 Flash — benchmarks and specs](https://themodelbeat.com/models/gemini-2-5-flash)
- [llm-stats.com — Qwen3.6-27B vs Gemini 2.5 Flash](https://llm-stats.com/models/compare/qwen3.6-27b-vs-gemini-2.5-flash)

Internal: `EXPERIMENTS.md`, `DEVLOG.md`, `src/evalgen/fabrication.py`,
`production-reference/sentiment-batch-retention-main/config/model_setting/retention.yml`.
