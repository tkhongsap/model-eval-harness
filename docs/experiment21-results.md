# Experiment 21 — four open-weight ASR arms through OpenRouter

**Run 2026-08-17.** Four open-weight speech-to-text models scored on `asr-eval`'s 20 Thai
call-centre recordings, against the Gemini 2.5 Flash incumbent and the internal-GPU
Qwen3-ASR arm from Experiment 20.

**80/80 transcriptions succeeded. 0 failures. Total spend $0.16.**

---

## Why this run exists, and what it overturned first

`.env.example:44-51` stated that the Thai ASR track *"cannot be tested through
OpenRouter"*, on the evidence that searching all 338 catalogue models for
`asr/speech/transcribe/whisper/audio` returned exactly two, both irrelevant. The search was
run correctly. The conclusion was still wrong:

> **OpenRouter excludes speech-to-text models from `GET /api/v1/models` entirely.**

They appear only under an explicit filter. Measured 2026-08-17:

```
curl "https://openrouter.ai/api/v1/models?output_modalities=transcription"   -> 19 models
curl "https://openrouter.ai/api/v1/models"                                   ->  0 of them
```

`qwen/qwen3-asr-flash-2026-02-10`, named in that note as proof of absence, is served. So is
`qwen/qwen3-asr-1.7b` — **the same weights the internal GPU runs**, which is what made the
headline finding below possible. Of the 19, **8 are open-weight**; 4 of those 8 support
Thai. Note the shape of the error: nothing was dropped by a summariser, the API itself does
not return those rows unless asked. An absence proof needs the query that *would* have
found the thing.

Corrected in `.env.example`. `transcribe.py` gained `--body-mode json` for OpenRouter's
`input_audio.data` base64 shape; the multipart path for vLLM / Token Factory is unchanged.

---

## 1. The business view

| Model | Transcript accuracy | Key facts captured | Invents content | Cost / 1000 calls | Weights |
|---|---:|---:|---|---:|---|
| Gemini 2.5 Flash *(incumbent)* | **95.6%** | 67.5% | no | n/a | closed |
| Qwen3-ASR 1.7B (OpenRouter) | 88.7% | **89.9%** | no | $2.78 | Apache-2.0 |
| Qwen3-ASR 0.6B (OpenRouter) | 84.8% | 79.6% | no | $1.23 | Apache-2.0 |
| Whisper large-v3 | 81.4% | 83.0% | no | $2.78 | Apache-2.0 |
| Whisper large-v3-turbo | 77.8% | 66.0% | some | $1.23 | MIT |
| Qwen3-ASR 1.7B (our GPU, E20) | 32.7% | 92.9% | **yes** | n/a | Apache-2.0 |

- **Transcript accuracy** — share of Thai characters correct (100% − CER).
- **Key facts captured** — phone numbers, amounts, dates, package names, IDs recovered.
  These are the values production writes into fields.
- **Invents content** — surplus words on non-speech audio (hold music, IVR). Proxy, not
  a direct measurement; see `README.md` on why.
- **Cost** — OpenRouter's per-audio-second rate × this set's mean call (370.75 s). Gemini
  bills audio per *token*, so its cell is `n/a` rather than a fake-precise conversion.

---

## 2. The technical view

| Model | CER | CERn | WER | WERn | Entity | ins/min | RTF |
|---|---:|---:|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash | **0.0443** | **0.0443** | **0.1031** | **0.1031** | 0.675 | 33.1 | — |
| Qwen3-ASR 1.7B (OR) | 0.1127 | 0.1127 | 0.1714 | 0.1713 | **0.899** | **20.9** | 0.040 |
| Qwen3-ASR 0.6B (OR) | 0.1521 | 0.1520 | 0.2313 | 0.2312 | 0.796 | 30.5 | 0.040 |
| Whisper large-v3 | 0.1863 | 0.1861 | 0.2582 | 0.2581 | 0.830 | 31.0 | 0.057 |
| Whisper large-v3-turbo | 0.2217 | 0.2215 | 0.3800 | 0.3800 | 0.660 | 82.5 | 0.042 |
| Qwen3-ASR 1.7B (our GPU) | 0.6731 | 0.6730 | 0.7887 | 0.7885 | 0.929 | 1073.5 | — |

All six share `ground_truth_sha256 9ab5bcbb…` and `scoring_code_sha256 fad59324…`, so the
rows are directly comparable. All four new arms ran whole-file (`chunk_seconds=0`), one
chunking regime across the set.

---

## 3. The headline: the internal arm's failure is the serving layer, not the model

Experiment 20 measured `qwen3-asr-1.7b` on the internal GPU at **CER 0.673**, driven
entirely by one family:

| Family | our GPU | OpenRouter | Δ |
|---|---:|---:|---:|
| telephony_noise | **8.410** | **0.167** | **50× better** |
| every other family | 0.088 – 0.160 | 0.074 – 0.167 | comparable |

Same weights. Same audio bytes. Same ground truth. Same scorer. The only variable is who
serves the model — and the blow-up does not survive the change. Insertions collapse from
**1073.5/min to 20.9/min**.

**`telephony_noise` at 8.41 is a defect in how we host Qwen3-ASR, not a property of
Qwen3-ASR.** Nothing about Thai, and nothing about noisy telephony audio, is implicated.
Candidate causes, none yet tested: the LiteLLM/Token Factory decoding configuration
(temperature fallback, repetition penalty, `condition_on_previous_text`), absent VAD, or
8 kHz audio reaching a 16 kHz-expecting frontend without resampling.

Had we responded to E20 by swapping in a Thai-specialised model, the swap would have
"fixed" this and taught us nothing.

---

## 4. What each model gets wrong — CER by call type

| Call type | Gemini | Qwen 1.7B | Qwen 0.6B | Whisper v3 | Whisper turbo |
|---|---:|---:|---:|---:|---:|
| clean_baseline | 0.006 | 0.105 | 0.124 | 0.188 | 0.195 |
| code_switch | 0.017 | 0.131 | 0.169 | 0.182 | 0.211 |
| disfluency | 0.121 | **0.096** | 0.139 | 0.155 | 0.200 |
| far_field_low_gain | 0.113 | 0.115 | **0.106** | 0.188 | 0.247 |
| hold_ivr | 0.036 | 0.099 | 0.164 | 0.175 | 0.214 |
| long_context | 0.053 | 0.074 | 0.133 | 0.136 | 0.167 |
| numeric_dense | 0.010 | 0.128 | 0.177 | 0.174 | 0.217 |
| overlap_crosstalk | 0.015 | 0.114 | 0.151 | 0.218 | 0.289 |
| proper_nouns | 0.012 | 0.138 | 0.194 | 0.228 | 0.239 |
| telephony_noise | 0.007 | 0.167 | 0.194 | 0.261 | 0.238 |

Gemini leads on nine of ten. Qwen3-ASR 1.7B beats it on **disfluency** (0.096 vs 0.121) —
stutters, fillers, false starts, self-repair — and Qwen 0.6B beats it on
**far_field_low_gain**. Both are the families where Gemini is weakest, which suggests
Gemini's advantage is not uniform and an ensemble question is worth asking later.

---

## 5. Which facts survive — and the reversal that matters

| Entity | n | Gemini | Qwen 1.7B | Qwen 0.6B | Whisper v3 | Whisper turbo |
|---|---:|---:|---:|---:|---:|---:|
| phone | 88 | 99% | 98% | **26%** | 41% | 30% |
| amount | 118 | 86% | **100%** | 100% | 99% | 96% |
| date | 88 | **25%** | **100%** | 92% | 100% | 31% |
| months | 83 | 36% | 71% | 93% | 100% | 100% |
| id | 64 | 100% | 95% | 100% | 89% | 81% |
| speed | 9 | 44% | 67% | 78% | 56% | 67% |
| package | 15 | 40% | 0% | 0% | 0% | 0% |

Three findings, in order of consequence:

**Qwen3-ASR 1.7B captures the facts better than the incumbent — 89.9% against 67.5%.**
Gemini recovers only **25% of dates** and **36% of month counts** while transcribing the
surrounding speech near-perfectly. Those are contract-term values. On the metric that
determines what production writes into a field, the incumbent is the weaker model, and the
gap is not small.

**Qwen3-ASR 0.6B is disqualified for this use case despite a respectable CER.** It recovers
**26% of phone numbers** against the 1.7B's 98%. A call-centre transcript that loses three
in four callback numbers is unusable regardless of how good the prose looks. Both Whisper
variants share this weakness (41% / 30%). This is exactly the failure WER hides and the
entity metric exists to expose.

**The `package` row is partly a measurement artifact and must not be quoted as 0%.**
Verified on ASR-001: reference `แพ็กเกจ ทรู อันลิมิเต็ด`, Qwen produced
`แพ็กเกจทรูอัลลิมิต` — "package True" correct, the English loanword "Unlimited" mangled.
The matcher is exact-match over a multi-word string, so one wrong syllable scores identically
to complete absence. There *is* a real weakness on Thai-script English brand names, and
Gemini's 40% shows it is not intrinsic — but "0%" overstates it. Partial-credit scoring for
multi-word entities is the fix; `package` is 15 of 465 entities (3%), so overall entity
figures are barely affected.

---

## 6. What this does and does not establish

**Established.** The internal Qwen arm's catastrophic result is a hosting defect. Among
open-weight models on this set, Qwen3-ASR 1.7B is clearly best (CER 0.113, entity 89.9%).
Gemini still wins decisively on raw transcription (0.044) and loses decisively on fact
capture (67.5%). Whisper large-v3-turbo is the weakest arm tested and the only one flagged
for hallucination (82.5 ins/min).

**Not established.**

- **No Thai-specialised model was tested.** Typhoon (`typhoon-ai/typhoon-whisper-large-v3`,
  MIT) and Pathumma (`nectec/…`, Apache-2.0) are HuggingFace-only and still need the
  internal GPU. This run measured the generic multilingual baselines, which is the control
  that comparison will need — not the comparison itself.
- **`RECONCILED: NO` still applies.** Synthetic audio, two TTS voices, authored ground
  truth. A CER here is not a production CER. Nothing in this run changes that.
- **Cost excludes the second call.** Gemini does audio→QA JSON in one request; an ASR arm
  needs a downstream text LLM. The `$/1000 calls` column is the ASR step only.
- **The entity matcher understates multi-word recovery**, per §5.

---

## 7. Reproducing

```bash
python asr-eval/scripts/transcribe.py \
    --base-url https://openrouter.ai/api/v1 \
    --model qwen/qwen3-asr-1.7b --arm or-qwen3-asr-1.7b \
    --api-key-env OPENROUTER_API_KEY --body-mode json \
    --language th --chunk-seconds 0

python asr-eval/scripts/score_asr.py \
    --hyp-dir ../hypotheses/or-qwen3-asr-1.7b \
    --arm or-qwen3-asr-1.7b --json ../reports/or-qwen3-asr-1.7b.json
```

Reports: `asr-eval/reports/or-{whisper-large-v3,whisper-large-v3-turbo,qwen3-asr-1.7b,qwen3-asr-0.6b}.json`.

## 8. Next

1. **Diagnose the Token Factory `telephony_noise` defect** against the OpenRouter arm as
   the known-good reference. Highest value: it is a live production-path bug.
2. **Self-host Typhoon Whisper Large v3 with `whisper-large-v3` as its control** — same
   architecture, same size, one variable. `whisper-large-v3` is now measured (CER 0.186),
   so the control is already in hand.
3. **Add partial-credit entity scoring** for multi-word values.
4. **Investigate Gemini's 25% date recovery** — it is the incumbent's largest single
   weakness and it sits on a field production writes.
