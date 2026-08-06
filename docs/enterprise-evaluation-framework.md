# Enterprise LLM evaluation framework — Retention reference plan

## Outcome

Use Retention to establish a reusable evaluation platform that can answer one bounded
question now and rerun the same evidence on internal GPUs later:

> Can a Qwen-based replacement match Gemini on True's Retention call classification
> task, under production-like constraints?

The answer is quality-first. A model must qualify in the intended runtime regime, meet
the parse-reliability gate, and avoid a statistically supported regression in any of the
three independent scored dimensions. Cost and latency rank only models that remain
eligible. There is no weighted score that compensates for a quality failure with price.

This is a Retention reference implementation, not an assertion that other applications
already fit it. Adding an application means adding its adapter, label contract, dataset
manifest and prompt entries while retaining the generator/scorer boundary.

## Success criteria

Phase one and phase two are complete when all of the following exist:

1. A versioned, internally consistent 138-item / 150-row Retention pack with explicit
   phase-one and phase-two slices, evidence spans, production-rule citations and known
   limitations.
2. A versioned prompt library in which every prompt has an id, application, version,
   parent, target models, decoding regime and SHA. Phase-one arms use byte-identical
   prompt text.
3. A locked experiment plan naming models, providers, reasoning regime, schemas,
   attempts, replicates, load levels, statistical rule and decision gates before full
   model calls begin.
4. Per-arm structured output plus paired reports that retain item-level regressions and
   report quality, reliability, stability, cost and latency without blending their
   denominators.
5. A recommendation that says `PASS`, `FAIL`, `INCONCLUSIVE`, or `UNAVAILABLE` for each
   candidate and explains the quality/cost/latency trade-off.

Even if all five exist, every report remains `RECONCILED: NO` until checked against the
live Gemini fact-check report on production-shaped ground truth. Synthetic transcript
results alone are not a migration verdict.

## Durable assets

### Enterprise eval harness

`src/evalgen/` makes OpenRouter calls and records response, provider identity, reasoning
usage, HTTP status, attempt count, latency, tokens and cost. `src/evalharness/` scores the
recorded outputs deterministically and imports no model or network client. Tests enforce
that boundary.

Experiment runs add three content-addressed identities:

- the response classification contract (`outcomes.py`), which decides `parse_ok`;
- the scoring code surface, which turns classified payloads into paired results; and
- the common workload, including assets, prompt, schema, decoding, reasoning, attempts
  and experiment-plan SHA but excluding arm-specific model/provider identity.

This replaces repository-HEAD provenance: a documentation-only commit no longer makes
scoring code incomparable, while classification or scoring changes still do.

### Enterprise eval dataset

The reference pack is `retention_v3`, described by
`tests/fixtures/testsets/retention_v3.manifest.json`.

| Slice | Items | Purpose |
|---|---:|---|
| Phase one (`RET-01..RET-100`) | 100 | Fixed-prompt apples-to-apples baseline |
| Phase two (`RET-101..RET-138`) | 38 | Long context, ASR-shaped noise, Thai-English code switching and regressions |
| Primary full pack | 138 | Preregistered paired decision and three-replicate stability |

The pack is synthetic text, not production audio. Its Thai has no native-speaker
sign-off. Those are decision limits, not footnotes to remove from the final report.

### Standard prompt library

`src/evalgen/prompts/manifest.json` is the prompt catalogue; `prompts.py` is the
executable registry. Tests require their ids and hashes to agree.

- Phase one: every model receives `v9_16_base`, byte for byte, with the same schema and
  decoding settings.
- Phase two in Experiment 5: the prompt remains fixed; robustness comes from inputs,
  not tuning.
- Later controlled tuning: create a child prompt id, name its parent and target model,
  document every text/config change before running, tune only on a development slice,
  preserve a locked holdout, and report tuned results beside the fixed baseline. Never
  describe a tuned-vs-untuned comparison as model-only.

## Experiment 5 method

The executable preregistration is `experiments/retention-e5.plan.json`. Its status
sequence is `draft` → `qualified` → `locked`. A full run is refused until it is locked
and the operator re-enters the current plan SHA.

### Arms

| Arm | Role | Required regime |
|---|---|---|
| `google/gemini-2.5-flash` | incumbent | explicit reasoning off |
| `qwen/qwen3.6-27b` | candidate | explicit reasoning off |
| `qwen/qwen3.6-35b-a3b` | candidate | explicit reasoning off |

Provider, decoder, chat template, quantisation and reasoning regime are part of the
system evaluated. A model id alone is not an arm.

### Provider qualification and the Morph question

Qualification sends the exact phase-one request with fallback disabled and
`reasoning.effort=none`: three fixed items (`RET-01`, `RET-109`, `RET-138`) twice each,
one API attempt per logical call. A provider qualifies only with:

- 6/6 `parse_ok`;
- exactly one expected observed model and provider;
- exactly zero reported reasoning tokens on every call;
- positive prompt-token usage on every call; and
- one stable prompt-token fingerprint per item.

Failures are classified rather than hidden behind a blacklist:

| Classification | Meaning | Experiment consequence |
|---|---|---|
| `REQUEST_INCOMPATIBLE` | Exact request rejected, including repeatable HTTP 400 | Provider cannot host this arm |
| `SCHEMA_INCOMPATIBLE` | Response violates object-root contract, including scalar JSON | Provider cannot host this arm |
| `REGIME_INCOMPATIBLE` | Reasoning remains enabled | Not production-like; diagnostic only |
| `IDENTITY_INCOMPATIBLE` | Observed model/provider differs | Arm identity is not controlled |
| `PROVENANCE_INCOMPATIBLE` | Usage/fingerprint absent or split | Runtime identity cannot be established |
| `QUALIFIED` | Every qualification condition holds | Eligible to be selected and locked |

The team's repeated Morph 400 (`Multi-turn conversations are not supported`) therefore
does not call for a one-message workaround: that changes prompt layout and no longer
reproduces Experiments 1–3. If the exact six-call probe reproduces it, Morph is
`REQUEST_INCOMPATIBLE`. Alibaba returning a bare float for an object-root schema is
`SCHEMA_INCOMPATIBLE`. If no 27B provider qualifies with reasoning off, the
production-like 27B arm is `UNAVAILABLE`; a reasoning-enabled CoreWeave run may be
reported only as a separately labelled diagnostic.

### Full quality and stability run

Each selected arm runs all 138 items three times: 414 logical calls per arm, one API
attempt per call, concurrency four. Retrying a provider failure into success measures
recovery policy rather than endpoint reliability, so failures stay in every denominator.

The reliability gate is at least 99% parse-valid calls: at least 410 of 414. Quality is
paired on replicate one in three independent dimensions: call result, reason and
product. For each dimension the exact directional threshold is recalculated from its
observed discordant-pair count at alpha `1/64` per side:

- `AHEAD` / `BEHIND`: net crosses the exact directional threshold;
- `INDISTINGUISHABLE`: enough discordance exists, but net does not cross it;
- `UNDERPOWERED`: even a clean sweep could not cross it. This is inconclusive, not a
  tie.

Replicate stability receives the same paired treatment at item grain: an item is stable
only when its classified payload is identical across all three replicates. A candidate
must not be `BEHIND` the incumbent on this table.

### Operations under load

After quality eligibility, each arm runs the fixed 12-item load slice twice at
concurrency 1, 4 and 8: 72 calls per arm. Reports retain throughput, latency
p50/p95/p99/max, parse reliability, token components, reported cost, and the number of
calls whose cost was not reported. Missing cost is never converted to zero.

### Call budget

| Stage | Calls |
|---|---:|
| Qualification maximum for the 18 currently eligible provider names | 108 |
| Full quality/stability, 3 arms | 1,242 |
| Load, 3 arms | 216 |
| Full plus load | 1,458 |
| Maximum for current inventory | 1,566 |

Qualification is six calls per provider. Refreshing provider inventory changes its
maximum and therefore requires amending and reviewing the draft before calls.
At the 2026-08-06 inventory/prices, a deliberately conservative ceiling is **$3.47**
for all 108 probes: input tokens are estimated at twice the UTF-8 content bytes and every
call is assumed to spend all 8,000 output tokens at the highest listed provider price.
Gate 1 completed all 108 probes for US$0.109184588 reported cost. Twelve providers
qualified and six were request incompatible. After selecting Google, Morph and AkashML,
the same deliberately extreme assumption produces a **$50.13** full/load ceiling and a
**$53.60** all-stage planning ceiling (`experiment-budget`). These are approval caps,
not expected spend. Gate 2 was approved and made exactly 1,458 calls for a reported-cost
lower bound of US$1.507460937. Both Qwen candidates failed; see
`docs/experiment5-results.md`.

## Approval gates and engineer workflow

1. Draft: run `experiment-check`, review this plan together and refresh provider
   inventory. No model calls. **Complete.**
2. Qualification approval: review maximum bounded cost, then authorize only named
   six-call probes. Record every result, including failed endpoints. **Complete: 108
   calls; see `docs/experiment5-qualification.md`.**
3. Lock approval: select qualified providers, copy qualification hashes into the plan,
   set status `locked`, rerun offline checks, review exact projected cost, and approve
   full/load calls. **Complete: the user approved exactly 1,458 calls and US$50.13
   against the locked plan SHA.** The approval is recorded outside the immutable plan
   in `experiments/evidence/retention-e5/gate2-approval.json`.
4. Report review: examine item regressions and phase slices before aggregates.
   Operations rank only quality-eligible arms. **Complete: both candidates are `FAIL`,
   so load results remain diagnostic rather than a ranking.**
5. Reconciliation: compare against production-shaped truth and the live Gemini report.
   Until then, retain `RECONCILED: NO`.

## Phase three

When internal GPUs are ready, add the internal runtime as a new provider/runtime arm and
rerun the locked workload unchanged: same assets, prompt, schema, reasoning regime,
attempt policy, replicates, load subset and decision rule. Any required runtime-specific
change is a declared new experiment, not a silent accommodation.
