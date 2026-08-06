# Experiment 5 provider qualification

**Gate:** 1 — provider qualification only

**Executed:** 2026-08-06

**Qualification-time draft SHA:**
`49ae4874e0d503fdfb66e1440830190ae46ab8276996053380bd7d7b0e865ea2`

**Locked plan SHA:**
`2823d3359f6ca6dee601f27b84672ef100971b609bdf38368a56990f2e323c8e`

The user authorized at most 400 calls and US$15 for qualification. The tighter
preregistered bound remained operative: 18 provider names × three items × two
replicates = **108 calls**, with a conservative US$3.47 ceiling. The run made exactly
108 calls. OpenRouter-reported cost was **US$0.109184588**; this is recorded as a lower
bound because the harness never turns missing cost into zero.

Every logical call had one API attempt. No failure was retried or replaced. The exact
request pinned one provider, disabled fallback, set `reasoning.effort=none`, retained
the two-message prompt, and required the object-root structured-output contract.

## Results

| Arm | Provider | Result | Parsed | Reasoning | HTTP | Reported cost lower bound (USD) | Wall time (s) |
|---|---|---|---:|---:|---:|---:|---:|
| Gemini 2.5 Flash | Google | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.007150460 | 22.699 |
| Gemini 2.5 Flash | Google AI Studio | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.009808400 | 15.270 |
| Qwen3.6 27B | Alibaba | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.013165200 | 43.173 |
| Qwen3.6 27B | Chutes | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.008646040 | 98.128 |
| Qwen3.6 27B | CoreWeave | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.017939280 | 24.904 |
| Qwen3.6 27B | DeepInfra | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.013984640 | 38.951 |
| Qwen3.6 27B | Io Net | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.048 |
| Qwen3.6 27B | Morph | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.009146208 | 29.350 |
| Qwen3.6 27B | Phala | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.108 |
| Qwen3.6 27B | SiliconFlow | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.335 |
| Qwen3.6 27B | Venice | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.022 |
| Qwen3.6 35B-A3B | AkashML | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.005190680 | 27.908 |
| Qwen3.6 35B-A3B | AtlasCloud | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.156 |
| Qwen3.6 35B-A3B | CoreWeave | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.008653000 | 15.460 |
| Qwen3.6 35B-A3B | DeepInfra | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.004355600 | 98.716 |
| Qwen3.6 35B-A3B | Parasail | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.004461700 | 27.838 |
| Qwen3.6 35B-A3B | Phala | `QUALIFIED` | 6/6 | 0 on 6/6 | — | 0.006683380 | 15.082 |
| Qwen3.6 35B-A3B | SiliconFlow | `REQUEST_INCOMPATIBLE` | 0/6 | unavailable | 404 × 6 | 0 | 1.128 |

Totals: **12 qualified providers**, **6 request-incompatible providers**, **108 calls**,
and **108 recorded single attempts**. Every qualified provider also returned positive,
stable prompt-token usage for all three repeated items and the expected model/provider
identity.

The six failures all returned HTTP 404 `No endpoints found` with the exact provider pin
and required-parameter set. They are request incompatibilities: no backend ran, so the
absence of an observed provider is not an identity mismatch. The live result exposed
and fixed that precedence defect in the classifier. `qualification-report` then
reclassified the already-recorded rows offline; no endpoint was called again.

## What changed about Morph and Alibaba

The earlier failures were real, but they were not reproduced:

- Morph previously returned HTTP 400 `Multi-turn conversations are not supported` for
  the same two-message layout. It now returned 6/6 valid object-root responses with
  zero reasoning tokens.
- Alibaba previously returned scalar JSON where the root contract required an object.
  It now returned 6/6 valid object-root responses with zero reasoning tokens.

This is evidence that those endpoint implementations changed. It does not make catalog
status a sufficient health check; both providers had to pass the exact request.

## Locked provider selection

Selection did not inspect quality labels or choose whichever provider happened to
answer the three probe items best. The rule was historical continuity if the historical
provider qualified:

| Arm | Selected provider | Continuity basis |
|---|---|---|
| Gemini 2.5 Flash | Google | Experiment 4 incumbent |
| Qwen3.6 27B | Morph | Experiments 1–3 production-like non-reasoning arm |
| Qwen3.6 35B-A3B | AkashML | Experiment 4 candidate arm |

The selected, self-hashed artifacts are referenced by the locked machine plan. All 18
safe qualification artifacts are committed under
`experiments/evidence/retention-e5/qualification/`. They contain no prompts,
transcripts, model output, API key, or account metadata. The validator requires exact
candidate coverage, verifies every self-hash and qualification contract, totals all 108
calls and status counts, and checks that each selected provider is `QUALIFIED`.

CoreWeave also accepted explicit reasoning off in this probe, but it is not substituted
for Morph. The historical CoreWeave 27B result used extensive reasoning and remains a
separately labelled diagnostic, not evidence for the locked Morph arm.

## Gate 2 remains closed

The selected-provider conservative ceiling is **US$50.13 for 1,458 full and load
calls**: US$28.32 Gemini/Google, US$15.12 Qwen 27B/Morph and US$6.69 Qwen
35B-A3B/AkashML. The machine plan is locked so those choices cannot drift, but its Gate
2 approval record remains `PENDING`. No full or load call is authorized by Gate 1.
