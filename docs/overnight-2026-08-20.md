# Overnight run, 2026-08-20: Typhoon ASR, the token cost, and a fragility correction

**Status:** complete. `RECONCILED: NO` stands.
**Spend:** $3.74 OpenRouter (cap was $12). Token Factory calls are unmetered.
**Runs:** `out/runs/20260820-e23-with-typhoon` (2,046 records, five arms).

---

## 1. The headline, and the thing that undercuts it

**Typhoon Whisper large-v3 transcribes Thai retention calls 2.6x better than Qwen3-ASR and
eliminates the runaway failure mode entirely — and it does not measurably change the business
outcome.**

Both halves of that sentence are load-bearing. The transcription win is large, clean and
reproducible. The end-to-end win is not there.

### The ASR stage — measured on the same 138-call corpus

| metric | Qwen3-ASR 1.7B | Typhoon Whisper large-v3 |
|---|---:|---:|
| CER (normalised) | 0.1120 | **0.0438** |
| WER (normalised) | 0.1691 | **0.0774** |
| calls transcribed | 136 of 138 | **138 of 138** |
| calls scoreable after runaway exclusion | 120 | **138** |
| catastrophic runaways | **16 (11.8%)** | **0** |
| entity accuracy | 95.95% | 95.25% |

Two details matter. Qwen's 0.1120 **excludes its 16 runaways** — the true gap is wider than
2.6x, because Typhoon has nothing to exclude. And Qwen never produced transcripts for ASR-082
and ASR-089 after 8 attempts each; Typhoon transcribed both.

This is the first time the ASR stage has stopped being the pipeline's embarrassing half.

### The end-to-end stage — where the win disappears

Business F1 on `call_result`, scored on **replicate 1 alone as preregistered**, 138 calls:

| arm | call_result F1 | accuracy | reason F1 | product F1 |
|---|---:|---:|---:|---:|
| `format-control` — ASR-shaped text, zero mishearing | 0.663 | 85/136 | 0.239 | 0.804 |
| `ceiling` — perfect transcript | 0.645 | 84/136 | 0.228 | 0.808 |
| **`typhoon-pipeline`** | **0.597** | 76/138 | 0.230 | **0.791** |
| `qwen-pipeline` | 0.588 | 74/136 | 0.207 | 0.762 |
| `gemini-audio` — production today | 0.495 | 60/136 | 0.267 | 0.727 |

Paired sign test at alpha 1/64, at the call grain:

| comparison | call_result | reason | product |
|---|---|---|---|
| typhoon vs **gemini** | **AHEAD** (+15, band ±15) | INDISTINGUISHABLE (+4) | INDISTINGUISHABLE (+5) |
| qwen vs **gemini** | **AHEAD** (+14, band ±14) | INDISTINGUISHABLE (−1) | INDISTINGUISHABLE (+4) |
| **typhoon vs qwen** | **INDISTINGUISHABLE** (+1, d=13) | INDISTINGUISHABLE (+5, d=17) | INDISTINGUISHABLE (+1, d=11) |

**Halving the character error rate and removing a 12% catastrophic failure rate moved the
business outcome by one call out of 138.** The two pipelines disagreed on only 13 calls at all.

The reason is visible in the same table: `ceiling` — a labeller reading the *perfect*
transcript — scores 0.645. The pipeline is already within 0.05 of its own ceiling. There is
almost nothing left for a better transcriber to recover. The binding constraint is the
labelling step and the corpus, not the audio.

**What this changes.** Typhoon should still replace Qwen3-ASR: it is better on every
transcription metric, it removes an entire failure class, and it costs nothing extra on the
same gateway. But it should be adopted as a **reliability** improvement, not as a way to win
the migration argument. Nobody should expect the business numbers to move.

---

## 2. A correction to the published E23 headline

Two things were wrong with how the 2026-08-19 result was reported. Neither changes the
verdict; both change how much weight it can carry.

### 2a. It was scored the wrong way, and now it is scored both ways

`experiments/retention-e23.plan.json` preregistered, in its own words:

> Every headline figure is computed on replicate 1 alone. Replicates 2 and 3 feed ONLY the
> stability / noise-floor metric. Preregistered because Experiment 2 showed a metric crossing
> a decision band on a single draw from an arm that flips; **choosing the replicate after
> seeing three of them is choosing the answer.**

`scripts/experiment23_score.py` collapsed all three replicates by **modal vote** instead, and
the published figures were produced that way. `--replicate-policy` now exists, defaults to
`first`, and stamps the policy into the output JSON.

Both policies agree: AHEAD on `call_result`, INDISTINGUISHABLE on `reason` and `product`. The
conclusion survives; it is now verified rather than assumed.

### 2b. "AHEAD" is far weaker than it reads

The verdict clears its decision band by **exactly zero** under both policies. `scripts/e23_fragility.py`
quantifies what that means, using 20,000 cluster bootstrap resamples over **calls, not rows**:

| comparison | net vs band | calls whose removal alone flips it | bootstrap still AHEAD |
|---|---|---:|---:|
| qwen vs gemini | +14 vs ±14 | **25** | **52.6%** |
| typhoon vs gemini | +15 vs ±15 | 0 | **61.0%** |

**A 52.6% bootstrap is a coin flip.** The preregistered sign test says AHEAD and that remains
the decision rule — but the honest sentence is: *the internal pipeline beats production on the
business outcome about as often as not, and 25 individual calls could each flip the Qwen
result on their own.*

By contrast the INDISTINGUISHABLE findings are robust (97.9% and 94.1% bootstrap agreement).
The null results are the sturdy ones here.

**This should not be presented to a decision-maker as "the internal pipeline wins."** It
should be presented as: production's audio arm is clearly weaker on `call_result`, the internal
pipelines are clearly better than it, and the margin is too thin to bank.

---

## 3. Tar's token issue: measured, and the cause is one lever

**Correction, made later on 2026-08-20: we did not reproduce 30-40k OUTPUT tokens. The
number that does land in that range is the INPUT.**

| what | measured over 120 calls | in the reported range? |
|---|---:|---|
| **input (prompt) tokens** | **30,985 - 32,825**, median 31,926 | **yes -- on every call** |
| output (completion) tokens | 8,564 - 17,054, median 11,961 | **no -- zero of 120 reached 30,000** |
| total | ~35,400 - 44,056 | close |

Three candidate explanations for a 30-40k *output* were tested and ruled out: raising the
reasoning budget explicitly changes nothing (byte-identical result), output does not scale
with call length (correlation 0.12 across 2.8k-6.7k chars), and Gemini 2.5 **Pro is lower than
Flash** (9,232 vs 13,008). Nothing was truncated -- no call ended `finish_reason: length`.

The input sits in the range on every call and barely varies, which is also how a stable
"30,000 to 40,000" gets quoted. It is almost entirely `user_config.xlsx`: **94,174 characters
= ~31,400 tokens** of Thai field definitions, re-sent in full every time, with
`prompt_tokens_details.cached_tokens: 0` -- nothing cached. If that is the number Tar read,
the lever is **context caching or a smaller prompt**, not the thinking budget.

**Where every token goes, measured against the production code.** The output IS a long
extended structured JSON -- 118 keys, 9 blocks -- and it is *not* where the tokens are:

| component | measured | approx tokens |
|---|---:|---:|
| `user_config.xlsx` field definitions | 94,174 chars | **~31,400** |
| `response_schema` in `generationConfig` | 13,457 chars | ~4,500 |
| `system_prompt.txt` | 7,012 chars | ~2,300 |
| the transcript | ~3,100 chars | ~1,000 |
| **input subtotal** | | **~35,000-38,000** |
| **the filled 118-key answer** | **8,353 chars** | **~2,800** |
| thinking, unlimited | | ~9,800 |
| **output subtotal** | | **~12,600** |

`service_quality` is 4,258 of the answer's 8,353 characters -- 51% of the output is 24
`{evaluation, reason}` blocks with a mandatory Thai free-text reason each. Enforcing the real
`response_schema` *reduced* output (2,862 vs 3,440 tokens) while still returning all 118 keys;
schema plus unlimited thinking failed outright with `finish_reason: error`.

Which counter Tar read decides which fix matters most, and `docs/sentiment-qa-token-ask.md`
now requests all three numbers so it cannot stay ambiguous. The thinking finding below is
true either way.

Control, already on disk from 1,632 retention calls: **245** median completion tokens,
`reasoning_tokens` **0**. Same model. The difference is entirely configuration.

Measured tonight — production's real `system_prompt.txt` and the real 94k-character
`user_config.xlsx` field definitions, 24 calls per arm, valid responses only:

| arm | median completion | of which thinking | share | valid JSON |
|---|---:|---:|---:|---:|
| **baseline** (production's regime) | 11,961 | 8,774 | **73%** | 14/24 |
| `reasoning: low` | 13,160 | 9,668 | 73% | 14/24 |
| **`reasoning` off** | **3,440** | **0** | **0%** | **22/24** |
| `temperature 0 / top_p 0` | 11,159 | 8,213 | 74% | 17/24 |
| "think 1…5" block deleted | 10,839 | 8,028 | 74% | 11/24 |

**Three quarters of the output is thinking, not answer** — confirming a DEVLOG estimate that
had never been tested. Turning thinking off cuts output tokens **71%** and still returns the
complete **118-key** object on every successful call.

**The three things that do not work are as useful as the one that does:**
- `reasoning: low` does nothing — "low" is not low.
- Retention's decoding (`temperature 0`, `top_p 0`) does nothing.
- **Deleting the "think 1…5" prompt block does nothing.** Prompt surgery is not the lever;
  the budget is.

### An unexpected reliability signal

`reasoning-off` was also the *only* arm with a tight output range (3,083–3,938 tokens) and the
best JSON validity (22/24 vs 11–17/24). Every failure across all arms was `finish_reason:
error` with long outputs.

**Read this cautiously.** Those are OpenRouter provider errors on very long responses, not a
property of Vertex, and production presumably does not see a 40% failure rate. What transfers
is the mechanism — shorter outputs fail less — not the absolute rates.

### The one number that would settle it, which we cannot produce

Production runs on **Vertex**; this ran on **OpenRouter**, which has no `thinkingBudget`. Our
arms test the prompt and schema faithfully and the budget only by proxy. A single real batch
response's `usageMetadata.thoughtsTokenCount` settles it outright, costs nothing, and contains
no customer data. Drafted, EN + TH: **`docs/sentiment-qa-token-ask.md`**.

### What to change, in order

1. **`thinkingBudget: -1` → a finite value.** Three call sites, and the highest-volume is the
   daily batch, not the fact-check path usually quoted:
   `qa_pipeline_tasks.yml:76`, `qa_pipeline_fact_check.yml:27`, `qa_pipeline_user_playground.yml:27`.
2. **Fix the self-contradicting prompt.** `system_prompt.txt:18–24` demands five numbered
   reasoning steps; the Thai line at `:26` says the reason must *not* be step by step. Free to
   fix, though measured to save nothing on its own.
3. **`maxOutputTokens: 65535` is not a backstop** — it is the API maximum, so nothing caps a
   runaway today.

**Not claimed:** that capping thinking preserves accuracy. It cannot be tested here — there is
no labelled sentiment_qa batch in this repo. What is observed is that the capped arm returned
valid, complete 118-key objects more reliably than the uncapped one.

---

## 4. Defects found and fixed while running

1. **`typhoon-whisper-large-v3` was reported as unavailable, and was not.** `list_gpu_models.py`
   probes `/v1/chat/completions`; Typhoon is an ASR model and answers `/v1/audio/transcriptions`.
   The catalog check produced a confident false negative. `scripts/typhoon_watch.py` now probes
   the correct endpoint with real audio.
2. **`transcribe.py` sent unauthenticated requests.** It reads `os.environ` and never loads
   `.env`, so a missing key produced a bare nginx 401 on every item, reported as
   "transcription failed after 3 attempts". Now refuses up front and names the variable.
3. **The retry loop discarded the error it retried on.** "failed after N attempts" reached the
   operator with the cause only in `__cause__`. It now names the failure.
4. **The arm→transcript-directory map was duplicated** in `experiment21_pipeline_delta.py`.
   Two copies of that mapping is the single worst place for a copy-paste to drift: one arm's
   calls would be labelled with another arm's transcripts and every downstream number would be
   internally consistent and wrong. Now one `HYP_DIRS` dict, with a test asserting each
   directory name appears exactly once in the source.
5. **An unknown `--arms` value was silently dropped** — a typo ran zero arms and exited 0.
   Now refused.
6. **The ASR test suite could not be collected at all** (`soundfile` missing). The repo
   documents a separate `.venv-asr` — the root pins numpy 2.3.4, the ASR pins 2.5.2, and
   `requirements-asr.txt` says they "must never merge". Created it; the full suite now runs.

---

## 5. Verification

| check | result |
|---|---|
| root suite, standalone | **1034 passed, 12 skipped** |
| root suite, differential (`TRUE_SOURCE_ROOT`) | **1045 passed, 1 skipped** |
| asr-eval suite, full, in `.venv-asr` | **152 passed, 2 xfailed** |
| new tests added | 17 (`test_replicate_policy.py`, `test_arm_wiring.py`) |

The pin gate correctly refused the global interpreter (numpy 2.4.6 / pandas 3.0.5 against the
pinned 2.3.4 / 2.3.3). Everything above ran in `.venv`.

---

## 6. Limits that still stand

- **Synthetic TTS audio**, two voices; speaker variety is prosody only.
- **Generator-authored labels.** `RECONCILED: NO`. `docs/ask1-email-draft.md` remains the only
  thing that retires it, and it still needs a recipient.
- **Every F1 here is an upper bound.** The corpus states its labels in ~100% of calls;
  `leak_probe.py` reports lift 1.00 on the outcome channel rather than hiding it.
- **No arm exceeds ~0.66 on `call_result`** because no labeller ever emits `unknown` or
  `undefined` — 21 of 136 calls are unwinnable for every arm, production included.
  **CORRECTED 2026-08-20: this is wrong.** Those calls are mislabelled against the prompt's
  own spec, not unwinnable. `retention_v9_16_body.txt:80` rules indecision to be `save`, and
  the corpus's `unknown` pool is made of exactly those indecision phrases, so a spec-obeying
  model must answer `save`. The same models emit both classes freely on the text packs (33
  `unknown`, 19 `undefined` in `20260814-132425Z-e17-gemini`). Relabelling only those 15 calls
  lifts every arm ~+0.13 with no prediction changed. The repair belongs in the corpus.
- Typhoon's end-to-end arm scored **138** items against the others' 136, because Qwen never
  produced two transcripts. The paired tests use only calls both arms answered.

## 7. What is still open

1. **Send `docs/sentiment-qa-token-ask.md`** — one Vertex `usageMetadata` block converts the
   token finding from directional to settled.
2. **Send `docs/ask1-email-draft.md`** — the only path off `RECONCILED: NO`.
3. **Adopt Typhoon for the ASR stage** on reliability grounds. Expect no business-metric change.
4. **The real ceiling problem.** `ceiling` scores 0.645. Until the labeller can emit
   `unknown`/`undefined`, no upstream improvement can move the headline much. That, not
   transcription, is where the next experiment belongs.

---

## 8. Follow-up, same day: the corpus was fixed and the ceiling moved

Section 7's item 4 above is now out of date — it recommends teaching the labeller to emit
`unknown`/`undefined`. That was the wrong repair. This section records the right one and what
it measured.

**Three edits. No prompt change, no model change.**

1. `asr-eval/scripts/thai_corpus.py` — the `unknown` pool now expresses an interrupted call,
   which is what `body:81` defines, instead of indecision, which `body:80` explicitly calls a
   `save`.
2. `asr-eval/scripts/compose_dialogues.py` — `unknown` calls now actually end there. Before,
   a "dropped call" still received a polite six-turn farewell. The scenario wrap-up is also no
   longer drawn on non-`save` calls, where it narrated a completed save and contradicted the
   label on 7 calls.
3. `asr-eval/scripts/business_labels.py` — `undefined` calls no longer carry a cancellation
   reason, which had made every one of them read as retention-relevant.

**Measured on the `ceiling` arm — perfect transcript, 138 calls, replicate 1:**

| class | F1 before | F1 after |
|---|---:|---:|
| `save` | 0.532 | **0.787** |
| `churn` | 0.624 | **0.850** |
| `unknown` | **0.000** | **0.966** |
| `undefined` | 0.000 | 0.000 |
| **weighted** | **0.645** | **0.794** |

`unknown` went from unreachable to **14 of 15 correct at precision 1.000**. The labeller could
do this the whole time; the corpus was asking it for something its own spec forbade.

### `undefined` is NOT fixed, and both attempts are recorded

Rewriting the closing line to *"I didn't call about the package"* scored 0.222 — but it is
incoherent: **6 of the 8 `undefined` calls are outbound**, so the customer never called
anyone. The direction-neutral replacement is coherent and scores **0.000**; the labeller
answers `save` on 7 of 8.

**It is right to.** These calls are 68–86 turns of a genuine retention conversation, and
`body:82` requires the *focus* of the call to be out of scope — not just its last sentence.

So `undefined` cannot be repaired in the closing pool at all. It needs a scenario whose body
is not a retention call: a new generator branch, plus a decision about what an out-of-scope
call even looks like in an outbound sales pack. Left as a known **8-row (5.8%) limitation**.

The coherent lines were kept over the higher-scoring incoherent ones deliberately. Shipping
dialogue that is nonsense for three quarters of the calls that use it, to gain 0.016 weighted
F1 from a single lucky match, is optimising the metric against the truth — the same failure
this whole section exists to correct.

### What this does not change

The published arm ordering and every paired verdict stand: the correction lifts all arms
together. The end-to-end arms have **not** been re-run on the corrected corpus — that needs
fresh audio for the changed calls (`resume_render.py`, ~1 h) then a re-run, and it is the
natural next step.
