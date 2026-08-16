# `asr-eval/` — a Thai call-centre audio eval set for the ASR track

**Created 2026-08-16.** 20 synthetic Thai call-centre recordings, 3.6–9.5 minutes each,
shaped to match what production's voice pipeline actually handles, with exact ground-truth
transcripts, entity annotations, validation tooling, graph tools and a scorer.

Nothing outside this directory was modified to build it.

---

## Why this exists

`.env.example:27-29` states the migration problem in one line:

> production sends audio, the candidate cannot receive it, so a separate ASR step and a
> transcript artifact have to exist that do not exist today.

Production (`production-reference/sentiment-voice-analysis-develop/`) hands a `.wav` and a
prompt to Gemini 2.5 Flash in **one** call and gets QA JSON back. A text-only candidate
cannot do that. Splitting it into ASR → text → QA introduces a component this repository
has never measured, and everything downstream inherits its errors.

This set measures that component. It is the audio analogue of `retention_v3`: the same
mechanism-family discipline, the same insistence on a hand-derived expectation written
before any number is produced.

---

## What was reverse-engineered from production

Every constant in `scripts/asr_common.py` carries the production file and line that fixes
it. The load-bearing findings:

| Finding | Source |
|---|---|
| `.wav` is the **only** audio extension that survives the upload filter, and the only one with a mime mapping | `tasks/sentiment_telesale/upload_voice_task.py:352`, `tasks/sentiment_qa/prep_payload_task.py:207-211` |
| Call metadata is parsed **positionally out of the filename**, in three places that must agree | `get_batch_result_task.py:302-319`, `fact_check_task.py:754-767`, `prep_payload_task.py:328` |
| A `call_direction` that is not exactly `IN`/`OUT` **raises**, it does not degrade | `prep_payload_task.py:330-338` (raise at `:335`) |
| `record_date` must match `^\d{8}$` or production falls back to the path, then to `99991231` | `get_batch_result_task.py:286-300` |
| The prompt **branches on call direction**, so a single-direction set exercises half of production's prompt surface | `prep_payload_task.py:330-338` |
| Language is Thai, industry telecom (True / dtac), rubric is retention / downsell / MNP | `config/sentiment_qa/system_prompt/system_prompt.txt` |

### The filename contract

```
{call_id}_{phone}_{HHMMSS}_{agent_id}_{first}_{last}_{provider}_{YYYYMMDD}_{duration}_{IN|OUT}.wav
     0        1        2         3        4       5        6          7          8         9
```

Worked example, the shape taken from production's own test fixture
(`tests/test_tasks/sentiment_qa/test_user_playground_task.py:363`):

```
7100_0810000301_093015_A200_somying_phakdee_D_20260803_204_IN.wav
```

**The agent name occupies two fields (4 and 5).** A name containing an underscore, or a
single-token name, shifts every later index — production then reads the wrong record date
and a `call_direction` that raises three tasks downstream. Nothing warns.
`tests/test_contract.py::test_an_underscore_in_a_name_shifts_every_later_field` demonstrates
it rather than asserting it in prose.

---

## The 20 calls

Ten mechanism families × 2 calls each, paired so that the two calls in a family take
different scenarios and opposite speaker-gender assignments — a family effect can therefore
never be a voice effect.

| Family | What it stresses | Maps to |
|---|---|---|
| `clean_baseline` | control: clear speech, good line | — |
| `telephony_noise` | 13 dB SNR, 50 Hz hum, crackle, dropouts | RET-119, RET-113 |
| `code_switch` | dense Thai↔English telecom vocabulary | RET-116 |
| `numeric_dense` | phone numbers, amounts, dates read aloud | RET-115 |
| `proper_nouns` | TrueMove H, dtac, TrueVisions, TrueID | RET-116 |
| `disfluency` | stutters, fillers, false starts, self-repair | RET-118 |
| `overlap_crosstalk` | both parties speaking at once | RET-120 |
| `long_context` | the two longest calls, both 9.5 min | `long_context` in `retention_v3` |
| `far_field_low_gain` | speakerphone: −34 dBFS, real reverb, 11 dB SNR | RET-119 |
| `hold_ivr` | hold music, IVR prompts, dead air | insertion / hallucination probe |

The "Maps to" column points at `tests/fixtures/testsets/ASR-EXPECTATION.md`, which already
enumerated ten Thai ASR artifact classes **as text** and argued, class by class, which are
safe to forgive. Seven families here are the acoustic *cause* of one of those classes, so a
failure can be traced to a decision that was already argued rather than re-litigated.

Scenarios span `retention`, `downsell`, `mnp`, `billing_dispute`, `net_slow`,
`coverage_issue`, `sim_replace`, `payment_arrange`, `device_promo`, `telesale_offer`.
Both directions are present: **17 `IN`, 3 `OUT`**. The split is uneven because only the two
telesales scenarios are outbound; three calls is enough to exercise the `OUT` prompt branch
at `prep_payload_task.py:330-338`, but it is not enough to compare `IN` against `OUT`
statistically, and no such comparison should be read off this set.

Hold music appears **only** in the `hold_ivr` family. It is deliberately kept out of
`long_context` so that a `long_context` result cannot be caused by non-speech audio rather
than by length.

**Measured:** 20 files, 123.6 minutes, 215.2 s – 572.8 s, 118.6 MB, 8 kHz mono PCM 16-bit.
Validation: **608 checks, 0 failures.**

---

## Ground truth is authored, not transcribed

This is the design decision that everything else rests on, and it is the reverse of how
ASR sets are usually built:

```
normal:  record real audio  ->  a human transcribes it  ->  that transcript is truth
here:    author the text    ->  synthesise audio from it ->  the text is truth
```

The normal route puts a human transcriber between the audio and the reference, and that
human has an error rate of their own. Here the reference is exact **by construction**. The
audio was generated from the text, so the text is not an estimate of what was said — it is
what was said. Reference error is removed entirely as a term in every WER reported.

Numbers are expanded to their spoken form *before* synthesis (`scripts/thai_num.py`): a
Thai speaker reads a mobile number digit by digit, and a TTS voice handed `0810000301`
would read it as one enormous cardinal, leaving the reference disagreeing with its own
audio. The canonical digit form is kept beside it as the entity's `value`.

### What this costs — read before quoting a number

- **The audio is synthetic.** It has none of the acoustic messiness of a real Verint
  recording beyond what the degradation chain explicitly models. **A WER measured here is
  not a production WER estimate.**
- **The dialogue is compositional.** `scripts/thai_corpus.py` is a hand-written library of
  Thai call-centre turns; a seeded composer assembles each call from it. Register and
  vocabulary are realistic; the *distribution* of what customers actually say is not
  claimed to be. Unique substantive lines run 100% on the short calls and 72–80% on the two
  longest, where a real agent repeats themselves too.
- **What it IS good for:** arm-against-arm comparison on byte-identical audio, and one arm
  compared across the ten families. That is the comparison this repository is built to make.

---

## What leaves this machine

Regenerating audio sends the **dialogue text** to Microsoft's public Edge TTS service
(`edge-tts`, voices `th-TH-NiwatNeural` and `th-TH-PremwadeeNeural`). This was explicitly
approved on 2026-08-16 before any call was made.

Everything sent is synthetic: invented agent and customer names, and phone numbers from
this repository's sanctioned block. **No customer data, and no production text, is
transmitted.** Scoring, validation and plotting make no network calls at all — only
`synthesize.py` does, and only on a cache miss.

### Phone numbers

`CLAUDE.md` fixes the synthetic block as `^0810000[0-9]{3}$` and records the three spent
sub-ranges. This set reserves **`0810000301`–`0810000320`**, one per call — inside the
block, clear of all three spent ranges. `asr_common.phone_for_index` raises rather than
walking past 320, and `test_contract.py` asserts the reservation does not collide.

---

## What is inferred rather than cited

Stated separately so it is never mistaken for a production citation:

- **8 kHz mono.** Production passes the file through untouched and states no sample rate
  anywhere. 8 kHz mono is an inference from the source system being Verint, a
  call-recording platform carrying narrowband telephony. If real Verint exports turn out to
  be 16 kHz, change `DELIVERY_SAMPLE_RATE` and re-render from the 24 kHz masters — no TTS
  calls are re-spent.
- **The degradation profiles.** The SNRs, hum levels and dropout rates in
  `synthesize.PROFILES` are plausible telephony values chosen to separate the families.
  They are not measured from real recordings, because no real recording is available here.
  What *is* asserted, and tested in `tests/test_dsp.py`, is that each constant produces the
  effect it names — `add_noise_at_snr(x, 20)` really lands within 1 dB of 20 dB SNR, the
  declared hum level really is the injected hum level, and the reverb really is audible.
  Two of those started out false: the hum was synthesised only at 50 Hz and 150 Hz and was
  then removed entirely by the 300–3400 Hz band-limit, and the reverb impulse response was
  L1-normalised, which put the wet signal ~35 dB down. Both families were labelled harder
  than they measured until that was fixed.

---

## Reproducibility

`manifest.json` records a sha256, duration and profile per file. The committed 8 kHz audio
is the frozen artifact — freeze it, and any two arms are compared on identical bytes.

**Regenerating is not bit-reproducible.** Neural TTS output varies between calls, so
deleting `cache/` and re-running `synthesize.py` produces perceptually identical audio with
different sha256 values. This is recorded rather than worked around: the audio is committed
precisely so that nobody has to reproduce it.

Re-running `synthesize.py` **with** the cache intact is deterministic apart from the TTS
step, so the whole degradation chain can be re-tuned and every file rebuilt in ~45 seconds
with no new network calls.

---

## Layout

```
asr-eval/
├── scripts/
│   ├── asr_common.py        the production contract: paths, filename, families, entities
│   ├── thai_num.py          Thai spoken forms for numbers, money, dates, phone numbers
│   ├── thai_corpus.py       hand-authored Thai call-centre turn library
│   ├── compose_dialogues.py builds the 20 dialogues + ground truth      (no network)
│   ├── synthesize.py        TTS + assembly + telephony chain -> .wav    (network on cache miss)
│   ├── validate_audio.py    608 contract checks, exit 1 on any failure  (no network)
│   ├── plot_audio.py        graph tools: per-call and set-level         (no network)
│   ├── score_asr.py         CER / WER / entity / insertion scorer       (no network)
│   └── transcribe.py        runner for an OpenAI-compatible ASR endpoint
├── dialogues/               structured dialogue + index.json            [committed]
├── ground-truth/            .txt reference, .entities.json, .timeline.json [committed]
├── audio/                   the 20 delivered 8 kHz wavs               [committed, 118.6 MB]
├── masters/                 24 kHz masters                              [gitignored]
├── cache/                   TTS response cache                          [gitignored]
├── hypotheses/<arm>/        model output under test                     [gitignored]
├── reports/                 validation.json, set-overview.png, plots/   [partly gitignored]
├── tests/                   test_thai_num / test_contract / test_tooling / test_dsp
├── manifest.json            sha256, duration and profile per file
└── requirements-asr.txt     separate pins; must never merge into the root requirements.txt
```

---

## Running it

Use a **separate** virtualenv. The root `requirements.txt` is pinned to production's own
versions and its header explains why those pins change what the scorer computes.

```bash
python -m venv .venv-asr
.venv-asr/bin/pip install -r asr-eval/requirements-asr.txt

cd asr-eval/scripts

# 1. rebuild the dialogues and ground truth            (no network)
python compose_dialogues.py

# 2. render audio                                      (network only on a cache miss)
python synthesize.py
python synthesize.py ASR-007                           # one call

# 3. check it                                          (exit 1 on any failure)
python validate_audio.py

# 4. look at it
python plot_audio.py                                   # 20 per-call plots + overview
python plot_audio.py --overview-only

# 5. prove the scorer moves before trusting it on an arm
python score_asr.py --self-test

# 6. run an arm and score it
python transcribe.py --base-url http://HOST:8000/v1 --model qwen3-asr \
                     --arm qwen3-asr-internal --chunk-seconds 120
python score_asr.py --hyp-dir ../hypotheses/qwen3-asr-internal \
                    --arm qwen3-asr-internal --json ../reports/qwen3-asr.json

# tests
python -m pytest ../tests/ -q
```

---

## The four metrics, and why not one

`score_asr.py` reports each of these **raw** and **after normalisation**; the gap between
the two is how much of an arm's error is lossless orthographic difference rather than real
mishearing.

- **CER** — the primary metric. Thai is written without word spaces, so a character
  distance is the only figure that does not depend on a tokeniser's opinion.
- **WER** — over pythainlp `newmm` tokens. Reported because it is always asked for, and
  flagged as tokeniser-dependent. Read CER first.
- **ENTITY** — did the phone number, amount, date or package survive? WER treats a wrong
  digit in a mobile number exactly like a wrong final particle. Production does not: it
  writes that number into a field. Reported per type, and counted as recovered whether the
  arm returns the Thai spoken form or the digits.
- **INSERT** — a hallucination *proxy*: total word insertions per minute of non-speech
  audio. An arm that invents dialogue over hold music can look acceptable on WER while
  being unusable. It is explicitly a proxy: a plain transcript has no timestamps, so this
  cannot prove an insertion landed *during* the hold music, only that the arm produced
  surplus words on a file containing that much non-speech. Making it a direct measurement
  needs word-level timings from the arm — the obvious next improvement.

Normalisation is deliberately narrow, for the reason `ASR-EXPECTATION.md:52-59` gives:
*forgive where the mapping is unambiguous; do not forgive where the model had to guess.*
Only the three lossless classes are forgiven — Thai numerals, doubled SARA E, zero-width
characters. `score_asr.py --self-test` proves both directions: those three normalise to
zero error, and tone-mark loss does not.

---

## Known gaps

- **No real-audio anchor.** Until at least one real (consented, de-identified) Verint
  recording is scored beside these, the relationship between a CER here and a CER in
  production is unknown. This is the single most valuable next addition.
- **Two voices.** Edge TTS offers exactly two Thai neural voices, so all 20 calls share one
  male and one female speaker. Speaker variability is therefore **not** tested; an arm that
  happens to suit these two voices will look better here than it is.
- **Overlap is synthetic.** Overlapping turns are mixed at the waveform level, which is not
  the same as two people talking over each other in a real acoustic space.
- **`RECONCILED: NO` still applies.** Nothing here changes that. This is a screening
  instrument built on synthetic data, exactly like everything else in this repository so
  far.
