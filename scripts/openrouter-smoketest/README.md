# OpenRouter smoke test

**Exploratory tooling. Not part of the scoring library, and not scored output.**

This answers one question: does an OpenRouter API key work end to end through the
OpenAI SDK. It exists here, clearly separated from `src/evalharness/`, because that
package's own `AGENTS.md` states it makes no model calls. This script is the reason
that boundary needed writing down.

## Why the model names differ from the eval plan

The plan named Gemini 2.5 Flash, Qwen3.6-27B, and Gemma-4-12B-it. **None of those three
exist on OpenRouter's live catalog**, checked directly against
`https://openrouter.ai/api/v1/models` on 2026-08-04:

| Planned | What's actually there |
|---|---|
| Gemini 2.5 Flash | Not listed. Current generation: `google/gemini-3.5-flash`, `google/gemini-3.6-flash` |
| Qwen3.6-27B | Not listed. Only `qwen/qwen3.7-{flash,plus,max}`, `qwen/qwen3.8-max` — hosted commercial tiers, no parameter count in the name, not the raw open-weight checkpoint the plan meant |
| Gemma-4-12B-it | **Zero Gemma models of any generation.** |

Two consequences:

- **This isn't actually a gap for Gemini.** Production's real Gemini access is direct
  via Vertex AI Batch, not OpenRouter, so OpenRouter never needed to carry it. This
  smoke test uses `gemini-3.6-flash` purely to prove the pipe works, not as a
  production-equivalent reference.
- **Gemma is a real gap.** If the raw 12B checkpoint specifically matters, check
  Together.ai, Fireworks, or DeepInfra — providers that typically carry open-weight
  checkpoints with parameter counts in the name, which OpenRouter's current Qwen
  listing suggests it does not right now.

`.env.example` defaults to `google/gemini-3.6-flash` and `qwen/qwen3.7-flash` as the
closest real substitutes. Override with `OPENROUTER_MODEL_INCUMBENT` /
`OPENROUTER_MODEL_CANDIDATE` to try others; re-verify against the live `/models`
endpoint before trusting a name, this list moves.

## Run it

```bash
pip install -r requirements.txt
python smoketest.py
```

The key comes from a `.env`. See [`.env.example`](../../.env.example) at the repo root.

### Where the .env can live, and what the key may be called

**Either location works.** The script searches both, in this order:

1. `scripts/openrouter-smoketest/.env` (script-local)
2. `<repo root>/.env`

First definition wins, so script-local overrides root, and a real shell environment
variable beats both. Both paths are covered by the bare `.env` rule in `.gitignore`,
which matches at any depth, so neither can be committed.

**Three key names are accepted**, checked in this order:
`OPENROUTER_API_KEY`, `OPEN_ROUTER_API`, `OPENROUTER_KEY`.

More than one is supported because the obvious name is not the only obvious name.
Failing with "not set" while the key sits in the file, spelled slightly differently,
is a worse outcome than accepting a synonym. The script prints **which file** it
loaded and **which variable** the key came from, so a run reading the wrong source is
visible immediately rather than mysterious.

> **Do not `cp .env.example .env` if you already have a `.env`.** It overwrites.
> Append the lines you need instead.

Prints, per model: latency, the model actually observed to respond (not just the one
requested — useful since a provider can silently route to a different checkpoint),
token counts, and the raw response text. Exits non-zero if either call fails.

## Correction, 2026-08-04

An earlier version of this file claimed OpenRouter carried **no** Gemini 2.5 Flash,
**no** Qwen 27B, and **no** Gemma models at all. **All three claims were wrong.**

They came from scraping the `/models` page through a summarising fetch, which silently
dropped entries from a 338-model list. Querying the API directly with a real key
returns all of them. The lesson is recorded here because it nearly redirected the whole
model selection: **query the endpoint, do not scrape and summarise it.**

```bash
curl -H "Authorization: Bearer $OPENROUTER_API_KEY" \
     https://openrouter.ai/api/v1/models | jq -r '.data[].id' | grep -i gemma
```

## Models, verified live 2026-08-04

| Role | Model | Context | Input modalities | $/Mtok in/out |
|---|---|---|---|---|
| incumbent | `google/gemini-2.5-flash` | 1,048,576 | file, image, text, **audio**, video | 0.300 / 2.500 |
| candidate | `qwen/qwen3.6-27b` | 262,144 | text, image, video | 0.289 / 2.400 |
| candidate_2 | `google/gemma-4-26b-a4b-it` | 262,144 | image, text, video | 0.070 / 0.340 |

Gemma sizing note: **`gemma-4-12b-it` does not exist.** Gemma 4 comes in `26b-a4b` and
`31b`; the only 12B is Gemma *3* (`google/gemma-3-12b-it`, and the cheapest of the lot
at 0.050/0.150).

### ASR is not available here

`qwen/qwen3-asr-flash-2026-02-10` is **not in the catalog**, and neither is any other
Qwen ASR model. Searching all 338 entries for `asr|speech|transcribe|whisper|audio`
returns exactly two, both OpenAI: `openai/gpt-audio` and `openai/gpt-audio-mini`.

So the Thai ASR track cannot be exercised through OpenRouter. Self-hosting Qwen3-ASR on
the internal GPU was the plan regardless.

## The modality gap, in one row of that table

**`gemini-2.5-flash` accepts audio. `qwen3.6-27b` does not.**

That is the migration problem stated precisely. Production sends a `.wav` to Gemini and
gets structured JSON back in one call. The candidate cannot receive audio at all, so a
separate ASR step and a transcript artifact have to exist, and neither exists today.
The source-code review reached the same conclusion from the other direction; this is
the first time it has been confirmed against the serving APIs themselves.

## Findings from the runs

**1. Both `qwen3.6-27b` and the earlier `3.7-flash` are reasoning models; `gemini-2.5-flash`
is not.** On a one-word prompt, qwen3.6-27b spent 105 reasoning tokens for 16 visible
ones. gemini-2.5-flash returned 1 token total and cost $0.000005, against $0.000233 for
qwen: **the incumbent was ~47x cheaper than the candidate here**, the opposite of the
usual assumption, entirely because of reasoning overhead.

**2. The first version of this script reported PASS on empty responses.** With
`max_tokens=10`, reasoning consumed the whole budget, `finish_reason` came back
`"length"`, and no content was ever emitted. It passed because its only criterion was
that the HTTP call did not raise. Now `MAX_TOKENS = 2000` and **PASS requires non-empty
content**.

**3. The script crashed on non-ASCII output.** A model returned an emoji and Windows'
cp874 console encoding raised `UnicodeEncodeError`. **The real evaluation data is Thai**,
so a script that dies on non-ASCII is useless for this project. Fixed by reconfiguring
stdout to UTF-8, and a Thai round-trip check was added, because a suite that only ever
sends ASCII proves nothing about the workload that matters.

**4. `qwen3.6-27b` returned three different results for the same Thai prompt across
three runs.** Default temperature, identical input:

| run | result |
|---|---|
| 1 | **corrupted**: `ลลูกค้าตึงการยกเลิกบรการเพราะสญญญาณไม่ดี` — dropped and duplicated Thai vowel marks (`ต้องการ`→`ตึงการ`, `บริการ`→`บรการ`, `สัญญาณ`→`สญญญาณ`) |
| 2 | **empty response** |
| 3 | exact match |

`gemini-2.5-flash` and `gemma-4-26b-a4b-it` were exact on every run.

**This is n=3 at default temperature, so it is a signal to investigate, not a
benchmark.** But a candidate that cannot reliably echo a 41-character Thai sentence is
worth measuring properly before it scores call transcripts, and it is exactly the kind
of variance the evaluation framework's noise-floor work exists to quantify.

**5. Exactness must mean equality, not containment.** The first version of the Thai
check used `expected in returned`, which passed the corrupted run above: the original
is a substring of `ลลูกค้า...`. Substring containment is not a transcription check.

## What this is not

It does not produce scoring input for `src/evalharness/`. If OpenRouter becomes the
actual candidate-generation path for real evaluation runs (rather than a pipe-testing
tool), that is new, separate work: a script that runs a real sample through a chosen
model and emits the normalized record shape `records.py` expects, with its own
fixtures and its own review, not an extension of this file.

**That happened on 2026-08-05, and the paragraph above is left standing because it is
the condition the change was measured against.** `src/evalgen/` is the real
candidate-generation path: its own package, its own `requirements.txt`, its own tests,
and no import in either direction — `config.py` re-implements this script's key handling
rather than importing it, precisely so this script stays standalone. This file is still
what it says it is: a pipe test for a key. See `AGENTS.md`, "Service layers do not
apply", for the full accounting.
