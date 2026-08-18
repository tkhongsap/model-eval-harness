# The ASR runaway is not a config defect — chunking moves it, not removes it

**Date:** 2026-08-18 · **Arm:** `qwen3-asr-1.7b` · **Audio:** frozen bytes, unchanged since
2026-08-16 · **No new audio was generated for any part of this.**

> **This document reverses its own working title.** The investigation was opened to test
> whether the one catastrophic transcript in the voice set was caused by a setting on our
> side. On the item that prompted the question the answer looked like a clear yes. Running
> the corrected configuration across the whole set produced a second runaway on a different
> call, and that is the finding that matters. The intermediate result is kept below because
> the reasoning is the point: a fix validated on the item that motivated it is not validated.

## What was published

One call in the twenty-item voice set, `ASR-012`, returned **58,640 characters against a
3,405-character reference** — a normalised CER of **16.28**. It dominated every pooled voice
figure, and was treated as evidence that Qwen3-ASR 1.7B is catastrophically unstable.

## What ASR-012 actually was

**The model heard the call correctly and the decoder got stuck.** The transcript is a
112-character unit repeated **495 times**, covering **94.5%** of the output, with a clean
2,832-character head and a clean 368-character tail — the model entered the loop partway
through, emitted 55,440 characters of it, then recovered and finished the call. It was never
truncated.

- **The repeated text does not appear in the reference**, before or after normalisation. It
  is invented, not real speech being repeated.
- **Deleting it recovers an ordinary transcript:**

  | ASR-012 | CER |
  |---|---:|
  | as returned | **16.2767** |
  | loop removed | **0.1413** |
  | loop collapsed to a single copy | 0.1419 |

  0.1413 is mid-range for this model — its other calls run 0.134 to 0.237. A transcription
  failure does not repair itself when you delete text; a decoding failure does.

## The config sweep — and why it was not enough

The published run posted each file whole (`--chunk-seconds 0`). The runner has always
supported splitting at the quietest frame near each boundary; it was switched off because the
longest file transcribed whole at RTF 0.066 and so did not appear to *need* it.

Re-running ASR-012's frozen bytes across chunk settings:

| `--chunk-seconds` | chunks | CER | chars |
|---|---:|---:|---:|
| **0 — the published config** | 1 | **16.2767** | 58,546 |
| 60 | 5 | 0.1087 | 3,217 |
| 120 | 3 | 0.1289 | 3,128 |
| 150 | 2 | 0.1492 | 3,078 |
| 180 | 2 | **0.0975** | 3,235 |
| 200 | 2 | 0.1319 | 3,143 |

The baseline reproduced to within one character (58,639 vs 58,640 published), and **every**
split — down to merely cutting the file in two — cleared the loop. On this item, the evidence
for "our configuration caused it" was about as clean as evidence gets.

**It did not survive contact with the other nineteen calls.** Re-running the entire set at
`--chunk-seconds 120`:

| item | unchunked | chunked 120s |
|---|---:|---:|
| ASR-012 | **16.02× reference — runaway** | 0.87× — clean |
| ASR-018 | 0.93× — clean | **4.89× reference — runaway** |
| the other 18 | 0.85–0.92× | 0.88–0.94× |

ASR-018's chunked output is 39,689 characters against an 8,124-character reference: a
**58-character unit repeated 554 times**, 81% of the output. Its repeated unit is
`กหกหกหกห…` — a degenerate two-character alternation, a more obviously broken loop than
ASR-012's, which at least resembled Thai.

**The runaway rate is 1 in 20 in both configurations.** Chunking relocated the failure. It did
not remove it.

### The failure is deterministic, not random

Three independent runs of ASR-018's frozen bytes at `--chunk-seconds 120`:

| run | chars | RTF |
|---|---:|---:|
| full-set run | 39,689 | 0.506 |
| replicate 1 | **39,678** | 0.505 |
| replicate 2 | **39,678** | 0.511 |

Byte-identical between replicates. And ASR-018 at `--chunk-seconds 180` returns **7,537
characters** — clean, 0.93x its reference.

So the loop is a **deterministic function of (audio, chunk boundaries)**, not a random event.
Each configuration has its own victim set: `cs0` breaks ASR-012, `cs120` breaks ASR-018,
`cs180` breaks neither of the two calls known to break.

### Why that is not the good news it looks like

The obvious next move is to adopt `cs180` and declare the problem solved. **That is the same
mistake this document already made once, and it would be the third time round the loop.**

`cs0` looked clean until it was run on ASR-012. `cs120` was chosen *because* it fixed ASR-012,
and broke ASR-018. Choosing `cs180` because it fixes both known victims is fitting the
configuration to the twenty calls we happen to own. At a ~5%-per-call event rate, a
twenty-item set is expected to contain about one victim per configuration, and searching
settings until one shows zero says nothing whatever about call twenty-one.

A configuration is only validated by a set it was **not** selected on. Ours is now spent: both
ASR-012 and ASR-018 have been used to pick settings, so no chunk size can be honestly
validated on these twenty calls again.

For completeness, the full set at `--chunk-seconds 180` runs **20 / 20 with no runaway**,
hypothesis-to-reference ratios 0.88-0.95, zero items flagged by the signature scan. That is
reported as *cs180's victim set on the data it was chosen from*, which is the empty set by
construction, and not as a fix.

## The corrected voice-track numbers

With a configuration that produces no runaway, the arm can finally be scored without one item
dominating every aggregate:

| arm | CER (all 20) | entity recovery |
|---|---:|---|
| Gemini 2.5 Flash | **0.0443** | 450/465 (96.8%) |
| Qwen3-ASR, `cs0` (published) | 0.6730 | 450/465 (96.8%) |
| **Qwen3-ASR, `cs180` (no runaway)** | **0.1147** | 450/465 (96.8%) |

**The honest transcription gap is 2.6x, not the 15x implied by the published 0.673.** Every
voice-track figure published for this arm was distorted by a single decoder failure, in the
direction that flattered the incumbent.

### And it changes nothing about the verdict

| arm | paired sign test vs Gemini |
|---|---|
| `cs0` (published, with runaway) | Gemini better on 17, Qwen on 3 -> d=20 net=-14 band=+/-12 **BEHIND** |
| `cs180` (no runaway) | Gemini better on 17, Qwen on 3 -> d=20 net=-14 band=+/-12 **BEHIND** |

Identical, down to the split. Removing the catastrophic item and re-transcribing every call
under a different configuration moved the pooled CER by a factor of six and the decision not
at all, because the decision was never carried by one item — it is carried by Gemini winning
17 of 20 calls on ordinary margins.

This is the clearest available argument for why the paired test is the instrument that should
be quoted and the pooled mean is not. One item moved the headline number by 6x. It moved the
verdict by zero.

## What this means

1. **The loop is a property of the model, not of our settings.** The correct description is a
   roughly 5%-per-call catastrophic decoding failure that is *deterministic* given the audio
   and the request boundaries, whose incidence is stable across configurations, and whose
   victim changes when the boundaries do. Chunk sizes shuffle which call is hit; nothing
   tried so far reduces how often some call is hit.
2. **The published ASR-012 number was still misleading, in a way that no longer helps the
   incumbent's case.** CER 16.28 is a decoder artifact, not a hearing failure, so quoting it
   as "the ASR mangled the call" was wrong. But replacing it with 0.14 and declaring the
   problem fixed would be a worse error, because the failure simply reappears elsewhere.
3. **It is a genuine migration blocker, and a sharper one than before.** A failure that a
   config change could remove is a tuning problem. A failure that survives every configuration
   tried, lands on an unpredictable call, and produces 5–16× the reference length is a
   production risk that needs a *detector*, not a setting.
4. **Gemini shows nothing comparable.** Scanning all forty committed hypotheses for the
   signature (a fixed-length unit repeated 3+ times, or output >1.5× its reference):
   `qwen3-asr-1.7b` 1/20, `gemini-2.5-flash-audio` **0/20**.

## What it does not change

**The Stage 1 verdict.** It never rested on this call, and now survives three separate ways of
removing it:

```
all 20 calls        Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
excluding ASR-012   Gemini better on 16, Qwen on 3  ->  d=19 net=-13 band=+/-11  BEHIND
cs180, no runaway   Gemini better on 17, Qwen on 3  ->  d=20 net=-14 band=+/-12  BEHIND
```

Qwen3-ASR is behind on per-call CER at alpha 1/64 with the runaway, without it, and with the
whole set re-transcribed so it never occurs.

## The diagnostic already existed and nothing acted on it

The scorer recorded 55,154 insertions and **20,972 insertions per non-speech minute** against
a set mean of 1,073 — a 20× outlier on a metric built precisely to catch hallucination. It was
computed, serialised and published, and CER 16.28 still flowed into every pooled figure
unchallenged.

**Recommended, not yet built:** a runaway refusal in `score_asr.py` — if a hypothesis exceeds
its reference by more than ~1.5×, or a repeated fixed-length unit covers more than ~20% of the
output, refuse to pool the item and name it. Pooling an arm containing a 16.28 alongside
nineteen 0.1s produces a mean that describes no call in the set. This is a scoring-gate
change and belongs in its own reviewed commit, not appended to this investigation.

## Reproduce

```bash
cd asr-eval
# the original loop, and its disappearance under any split
python scripts/transcribe.py --base-url <gateway>/v1 --model qwen3-asr-1.7b \
  --arm probe-cs0   --chunk-seconds 0   --items ASR-012
python scripts/transcribe.py --base-url <gateway>/v1 --model qwen3-asr-1.7b \
  --arm probe-cs120 --chunk-seconds 120 --items ASR-012
# the finding that matters: the whole set at the corrected setting
python scripts/transcribe.py --base-url <gateway>/v1 --model qwen3-asr-1.7b \
  --arm qwen3-asr-1.7b-chunked120 --chunk-seconds 120
python scripts/score_asr.py --hyp-dir hypotheses/qwen3-asr-1.7b-chunked120 --json chunked.json
```

Transcripts live under `asr-eval/hypotheses/` and are gitignored, as every arm's are.
`RECONCILED: NO` — synthetic TTS audio; no production call has been through any of this.
