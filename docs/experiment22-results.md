# Experiment 22 — end-to-end: can ASR + an open model replace Gemini on retention QA?

**Run 2026-08-17.** The first measurement in this repository of the *whole* migration path:
audio in, retention QA JSON out. Until now `asr-eval` measured audio→text and
`retention_v3` measured text→QA, and nobody had measured the join.

**30 calls · 36.3 min of telephony audio · 5 arms · 150 QA calls · 0 transport failures in
the final pass.**

---

## The eval set, and the one decision it rests on

`e2e_retention_pilot_v1`: 30 items taken from `retention_v3` and rendered to 8 kHz
telephony audio through `asr-eval`'s existing degradation chain.

**The audio moved to the labels; the labels never moved to the audio.** The alternative —
labelling `asr-eval`'s 20 existing calls with `call_result`/`reason`/`product` — would have
meant deriving ground truth from the transcripts with a model, and then scoring models
against it. That is the "built from the data it judges" error this repository has already
retracted twice: the `keyword` metric, and the first `ASR-EXPECTATION.md`, whose scratch
script imported the very code it existed to check. So `gt` is copied byte-for-byte out of
`retention_v3.jsonl`, hand-derived before any of this code existed, and nothing in the
render path can touch it.

Coverage, from 30 items: **all 4 `call_result` classes, all 4 `product` classes, 11 of 11
`reason` classes.**

Three strings exist per item and conflating any two corrupts a number:

| | what it is |
|---|---|
| `transcript_th` | the original, with digits. **The reference.** A perfect ASR emits this |
| `spoken_th` | digits expanded to spoken Thai. Fed to TTS only — an artifact of synthesis |
| arm output | what an ASR actually wrote |

The ceiling arms are given `transcript_th`, not `spoken_th`: `spoken_th` is how the sentence
had to be *pronounced*, not how it would be *transcribed*. Handing the ceiling `spoken_th`
would make it differ from the candidate in two ways at once — ASR error *and* numeral
orthography — and the delta would stop isolating the ASR step, which is the only thing this
design exists to measure.

---

## 1. The business view

| Arm | Accuracy (mean F1) | Valid JSON | Latency (median) | Cost / 1000 calls |
|---|---:|---:|---:|---:|
| **`gemini-audio`** *(incumbent)* | **0.909** | **30/30** | **4.7 s** | **$2.75** |
| `qwenasr-qwen` *(best open)* | 0.872 | 26/30 | 33.2 s | $6.30 |
| `qwen-text` *(open ceiling)* | 0.854 | 25/30 | 55.0 s | $6.80 |
| `whisper-qwen` | 0.828 | 24/30 | 79.7 s | $10.11 |
| `gemini-text` *(Gemini ceiling)* | 0.911 | 30/30 | 2.8 s | $1.38 |

**The incumbent wins on every axis at once.** More accurate, perfectly reliable at emitting
valid JSON, seven times faster, and less than half the cost. There is no axis on which the
open pipeline currently trades favourably.

Latency for the open arms is the sum of both calls (ASR median 4.0 s + QA). Cost is
OpenRouter's published per-token rates against this run's measured token counts, plus the
ASR step's per-audio-second rate on the 72.6 s mean call.

---

## 2. Per-dimension scores

| Arm | call_result | reason | product | mean | parse_ok |
|---|---:|---:|---:|---:|---:|
| `gemini-text` | **0.954** | 0.805 | 0.975 | **0.911** | 30/30 |
| `gemini-audio` | 0.917 | **0.834** | 0.975 | 0.909 | 30/30 |
| `qwenasr-qwen` | 0.876 | 0.765 | 0.975 | 0.872 | 26/30 |
| `qwen-text` | 0.818 | 0.769 | 0.975 | 0.854 | 25/30 |
| `whisper-qwen` | 0.801 | 0.724 | 0.958 | 0.828 | 24/30 |

Scored by `evalharness.metrics` **unmodified** — three dimensions, three denominators — so
these are on the same scale as Experiment 7.

---

## 3. Three findings that matter more than the ranking

### 3.1 Gemini's multimodality is nearly free

`gemini-audio` 0.909 against `gemini-text` 0.911: **−0.003**. Handing Gemini the raw
telephony audio instead of a perfect transcript costs essentially nothing. Whatever
transcription happens inside that one call is good enough that the downstream QA does not
notice the difference.

This is the finding that most threatens the migration. The split-pipeline architecture
exists because the candidate cannot receive audio — but the incumbent pays no penalty for
receiving it, so the split is pure added cost, added latency and added failure surface.

### 3.2 The "schema violations" are a serving fault, not a model capability limit

**CORRECTED 2026-08-18.** This section first read *"Qwen3.6-27B cannot reliably emit the
schema"*, on the evidence that Gemini scored 30/30 valid JSON and Qwen 24–26/30. The count
was right and the conclusion was wrong. Reading the raw content of all 13 failures — kept
precisely so this would not have to be guessed — shows not one of them is a schema
misunderstanding:

```
RET-03   -1.1000000000000001e-05000000000000000000000000000  (501 of 524 chars, 96%)
RET-08   1.200000000000000000000000000000000000000000000000  (500 of 503 chars, 99%)
RET-121  -0.00000000000000000000000000000000000000000000000  (502 of 505 chars, 99%)
```

Every one is a **degenerate repetition loop**: 95–99% of the output is a single repeated
character, with `finish_reason: stop`. Three facts identify it as a serving artifact rather
than model output:

1. **The same bytes appear for different inputs.** `-1.1000000000000001e-05` + 500 zeros is
   emitted verbatim for six different calls across three different arms. Output that does
   not vary with input is not a response to the input.
2. **The identical request succeeds on retry.** Re-run unchanged, the five `qwen-text`
   failures passed **5/5**.
3. **It is not the decoding parameters.** Tested against the failing items: baseline
   (temp 0.0) 5/5, `repetition_penalty` 5/5, no `response_format` at all 5/5, provider
   pinned to DeepInfra 5/5 — and **temperature 0.3 was the only variant that still failed
   (4/5)**. Greedy decoding is not the cause, so raising temperature is not the fix.

Measured per-attempt rate on a fresh sample (6 items × 4 identical attempts, drawn from both
previously-failing and previously-passing items so the sample is not selected on the
outcome): **1/24 = 4.2%**. At n=24 that interval is wide, but it is far below the 13–20%
this run recorded — and the original failures cluster in the same window as the
`IncompleteRead`/`getaddrinfo` collapse described in §6, so the run-time figure is most
likely a transient endpoint degradation rather than a steady-state property.

**So the 24–26/30 parse rate in §1 and §2 understates Qwen, and the accuracy numbers built
on it are correspondingly pessimistic.** They are left as measured rather than patched,
because re-rolling items until a score improves is the thing this harness refuses; the fix
is a fresh full run under the retry policy in §7, not an edit to this table.

*(A separate earlier failure was mine, not Qwen's: `max_tokens: 4096` truncated the model
mid-object. Thai costs far more tokens per character than English, so a budget sized by eye
off the English schema cuts the JSON in half. Raised to 16384 before the run.)*

### 3.3 The QA task survives bad transcription far better than expected

Evidence-span survival — did the exact span each label rests on survive the ASR?

| ASR arm | spans survived |
|---|---:|
| `qwen3-asr-1.7b` | 48/116 (**41.4%**) |
| `whisper-large-v3` | 25/116 (**21.6%**) |

Whisper destroyed **78%** of the exact evidence spans and still scored 0.828 mean F1. So the
causal story this eval was built to test — *span destroyed → label flips* — is largely
**false**. The QA model reconstructs the label from surrounding context; it does not need
the verbatim phrase.

That is good news for the migration and bad news for the diagnostic: evidence survival is a
much weaker predictor of downstream damage than expected, and should not be used as a proxy
for it. It does still rank the ASR arms in the right order (41.4% > 21.6%; 0.872 > 0.828).

---

## 4. Fault attribution

Per call, per dimension: was this arm's error also an error when the same model read the
perfect transcript?

| Dimension | Arm | both right | ASR-caused | QA-caused | recovered |
|---|---|---:|---:|---:|---:|
| call_result | `qwenasr-qwen` | 18 | 5 | 1 | 6 |
| | `whisper-qwen` | 17 | 6 | 2 | 5 |
| | `gemini-audio` | 28 | 1 | 1 | 0 |
| reason | `qwenasr-qwen` | 13 | 5 | 6 | 6 |
| | `whisper-qwen` | 10 | 8 | 8 | 4 |
| | `gemini-audio` | 13 | 3 | **11** | 3 |
| product | all arms | 28–29 | 0–1 | 1 | 0 |

**`reason` is the hard dimension for everyone**, and for `gemini-audio` its errors are
overwhelmingly **QA-caused (11) rather than ASR-caused (3)** — Gemini heard the call
correctly and still picked the wrong reason label. Better transcription cannot fix that;
only a better prompt or a better classifier can.

**`product` is solved.** Every arm is at 0.958–0.975 with essentially no ASR-caused errors.
It should not be the focus of any further work.

---

## 5. The result I do NOT believe, and why it is here anyway

`qwenasr-qwen` (0.872) scored **higher** than `qwen-text` (0.854) — the ASR pipeline beat
the same model reading a perfect transcript, by +0.018.

**Do not read this as "ASR helps."** Two confounds, either sufficient to explain it:

- The arms have **different parse-failure counts** (26/30 vs 25/30) on **different items**,
  so they are not scored on the same set of calls.
- The "recovered" column is large (6 on `call_result`, 6 on `reason`) — items the ASR arm
  got right and the ceiling arm got wrong. At n=30 that is well inside noise.

It is reported rather than suppressed because a delta this shape is exactly what a larger
run needs to resolve, and quietly dropping an inconvenient number is how a harness starts
choosing its own results. The honest statement is: **at n=30 the ASR step's cost is not
distinguishable from zero for the Qwen pipeline**, which is itself a useful finding — just
not the one the arithmetic appears to claim.

---

## 6. What this does not establish

- **n=30. A pilot, not a verdict.** Enough to prove the join and rank the arms; not enough
  to settle a migration. The differences among the three open arms are inside noise; the
  Gemini-vs-open gap is not.
- **The audio arm does not use production's voice prompt.** Production's `sentiment_qa` app
  runs a ~50-field KPI prompt this repository has no scorer for. All five arms share the
  retention prompt so that only input modality varies — which is what makes it a
  comparison, and which also means `gemini-audio` is not byte-identical to the production
  call it stands in for.
- **Synthetic audio, two TTS voices, authored ground truth.** Speaker variability is not
  tested. `RECONCILED: NO` still applies, and a real consented Verint recording remains the
  single most valuable missing thing.
- **The first pass lost 45 items to a DNS collapse** (5 arms × 6 workers = 30 concurrent TLS
  connections; `IncompleteRead` then `getaddrinfo failed`). Those were never scored. The
  final numbers come from a resumed pass at concurrency 3 with transport-only retries and
  **0 transport failures**. Retrying transport but never retrying content is the same
  distinction `runner.py` draws with `--max-attempts`.

---

## 7. Recommendation

**On this evidence, do not migrate retention QA to ASR + Qwen3.6-27B.** The open pipeline is
less accurate, materially less reliable at producing any usable output, 7× slower and
2.3× more expensive. The architectural premise — that splitting the call is necessary
because the candidate cannot hear — is undermined by §3.1: Gemini pays no measurable
penalty for receiving audio.

Where the next work has the most value:

1. **Add a degenerate-output class to `outcomes.py`, and retry only that.** Per §3.2 the
   parse failures are a serving fault, so the fix is neither a different model nor a
   different prompt. Define the class narrowly and mechanically — *a single character
   repeated ≥100 times covering ≥90% of the response* — classify it as `degenerate` rather
   than `schema_violation`, and allow bounded retry on it alone. At the measured 4.2%
   per-attempt rate, **2 retries put residual failure near 0.2%**.

   **This deliberately sits next to the rule that a parse failure is never retried, and the
   boundary has to be defended, not assumed.** The justification is evidential, not
   convenient: output that is byte-identical across different inputs, is 96–99% one
   character, and succeeds unchanged on retry is not the model answering badly — it is the
   serving layer failing to answer at all. That is the same line `transcribe.py` draws for
   the Token Factory control token ("not about the audio at all"), and the same line
   `runner.py --max-attempts` draws for a call that emitted no generation.

   Two guards, or the class becomes a laundry route for genuine failures:
   - **The retry count is reported in every run, per arm.** A rising count is an endpoint
     degrading, and it must be visible rather than absorbed.
   - **Anything that fails the mechanical test is still `schema_violation` and is still
     never retried.** If a response is malformed but carries information, it is the
     measurement.

   Do **not** raise temperature: it was the only variant that made things worse (4/5).
2. **Scale this pilot to all 138 `retention_v3` items** before any decision. The rendering
   and scoring path now exists and runs unattended; it is TTS time, not new engineering.
3. **Attack `reason`, not `product`.** `product` is solved at 0.975. `reason` is where every
   arm loses, and for Gemini the losses are QA-caused — a prompt problem, not an ears problem.
4. **Test the Thai-specialised ASR models** (Typhoon, Pathumma) in the `qwenasr-qwen` slot.
   `qwen3-asr-1.7b` already beats `whisper-large-v3` end-to-end (0.872 vs 0.828); a
   Thai-tuned model is the obvious next arm and needs the internal GPU.

---

## 8. Reproducing

```bash
cd e2e-eval/scripts
python build_pilot.py                      # 30 items -> audio + pilot.jsonl (Edge TTS)
python run_asr.py  e2e-qwen3-asr-1.7b      # audio -> transcripts
python run_qa.py   gemini-audio            # any of the 5 arms; --resume, --concurrency N
python score_e2e.py                        # all tables above
```

Artefacts: `e2e-eval/pilot.jsonl`, `e2e-eval/audio/`, `e2e-eval/asr/<arm>/`,
`e2e-eval/hypotheses/<arm>/`, `e2e-eval/reports/e2e-summary.json`.
