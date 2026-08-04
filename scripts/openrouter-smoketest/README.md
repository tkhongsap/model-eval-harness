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
cp .env.example .env
# edit .env: set OPENROUTER_API_KEY
pip install -r requirements.txt
python smoketest.py
```

Prints, per model: latency, the model actually observed to respond (not just the one
requested — useful since a provider can silently route to a different checkpoint),
token counts, and the raw response text. Exits non-zero if either call fails.

**Not yet run.** This was written before a key was available. Run it and confirm both
models PASS before relying on it for anything.

## What this is not

It does not produce scoring input for `src/evalharness/`. If OpenRouter becomes the
actual candidate-generation path for real evaluation runs (rather than a pipe-testing
tool), that is new, separate work: a script that runs a real sample through a chosen
model and emits the normalized record shape `records.py` expects, with its own
fixtures and its own review, not an extension of this file.
