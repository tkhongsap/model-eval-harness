# Experiment 5 results — enterprise Retention baseline and robustness

**Executed:** 2026-08-06

**Locked plan SHA:**
`2823d3359f6ca6dee601f27b84672ef100971b609bdf38368a56990f2e323c8e`

**Decision:** neither Qwen candidate qualifies; do not migrate on Experiment 5 evidence

**Reconciliation:** `RECONCILED: NO`

Experiment 5 ran the same prompt, schema, decoding configuration and explicit
reasoning-off regime across Gemini 2.5 Flash/Google, Qwen3.6 27B/Morph and Qwen3.6
35B-A3B/AkashML. Each full arm received all 138 items three times. The nine fixed load
profiles used the same 12 items twice at concurrency 1, 4 and 8.

The result is a clear rejection of both Qwen candidates under this locked synthetic-text
workload. It is not a final production migration verdict: production consumes audio,
the synthetic Thai has no native-speaker sign-off, and the results have not been
reconciled against the live Gemini fact-check report.

## Execution and approval accounting

The user approved 1,458 full/load logical calls with a conservative US$50.13 ceiling.
The harness made exactly 1,458 calls, with one API attempt per logical call and no
recovery retries:

| Stage | Runs | Calls | OpenRouter-reported cost lower bound |
|---|---:|---:|---:|
| Full quality and stability | 3 | 1,242 | US$1.250308220 |
| Fixed load profiles | 9 | 216 | US$0.257152717 |
| **Gate 2 total** | **12** | **1,458** | **US$1.507460937** |
| Gate 1 qualification | 18 provider probes | 108 | US$0.109184588 |
| **All paid work** |  | **1,566** | **US$1.616645525** |

Reported cost is a lower bound because missing provider cost is never converted to
zero. The approval record and the safe execution ledger are committed as
`experiments/evidence/retention-e5/gate2-approval.json` and
`experiments/evidence/retention-e5/execution.json`.

## Primary decision

Quality is paired against Gemini on replicate one. A negative net means the candidate
lost more discordant items than it won; the preregistered exact band is recalculated per
dimension from the observed number of discordant pairs. Operations cannot rescue a
quality or reliability failure.

| Candidate | Parse valid | Call result | Reason | Product | Stability | Decision |
|---|---:|---|---|---|---|---|
| Qwen3.6 27B / Morph | 359/414 (86.7%) | BEHIND, net -19 / band 13 | BEHIND, net -19 / band 17 | UNDERPOWERED, net 0 | BEHIND, net -121 / band 25 | **FAIL** |
| Qwen3.6 35B-A3B / AkashML | 414/414 (100%) | BEHIND, net -11 / band 11 | BEHIND, net -24 / band 16 | BEHIND, net -10 / band 8 | BEHIND, net -131 / band 27 | **FAIL** |

The 27B arm fails the 99% reliability gate, call-result quality, reason quality and
replicate stability. The 35B-A3B arm is fully parse-valid but is statistically behind
on all three scored dimensions and stability. The reports retain 56 and 55 item-level
regressions respectively instead of reducing them to aggregate percentages.

Phase-one and phase-two slice tables remain in the machine report. The full 138-item
pack is the preregistered primary decision; slice-level `UNDERPOWERED` results are
reported as inconclusive, never as ties or passes.

## What happened to Morph

Morph is not accurately described as permanently broken. Its qualification probe
returned 6/6 valid object-root responses with the unchanged two-message prompt and zero
reasoning tokens, so the earlier HTTP 400 did not reproduce.

The full run exposed a different problem: 359 successful calls, 54 HTTP 429 transport
failures and one empty response. With no retries, that is 359/414 parse-valid calls,
far below the required 410. The load profiles were also inconsistent: 22/24 at
concurrency 1, 24/24 at concurrency 4 and 23/24 at concurrency 8. This is endpoint
capacity/reliability evidence for the exact arm, not a reason to collapse the prompt to
one message, weaken the schema, or substitute a reasoning-enabled runtime.

If Morph were revisited, the fix belongs at the serving/contract level: obtain a
capacity or rate-limit commitment for the pinned endpoint, verify the exact request
again, and preregister a new experiment that measures any production retry/backoff
policy explicitly. Retrying these recorded failures after the fact would change the
experiment and is not allowed.

## Operational observations

These figures are diagnostic because neither candidate passed the quality-first gate.

| Arm | Full parse valid | p50 | p95 | Full reported cost lower bound |
|---|---:|---:|---:|---:|
| Gemini 2.5 Flash / Google | 413/414 | 2.140 s | 3.875 s | US$0.434660 |
| Qwen3.6 27B / Morph | 359/414 | 4.140 s | 8.141 s | US$0.485823, 55 calls missing cost |
| Qwen3.6 35B-A3B / AkashML | 414/414 | 2.938 s | 5.625 s | US$0.329825 |

At concurrency 8, observed throughput was 2.423 calls/s for Gemini, 0.726 calls/s for
27B/Morph and 1.958 calls/s for 35B-A3B/AkashML. These small fixed-load samples describe
the tested OpenRouter provider/runtime combinations, not internal-GPU capacity.

## Reporting correction after execution

The first offline report exposed a harness defect: the runtime gate required token and
reasoning metadata on every logical call. A permitted provider/transport failure has no
such metadata, so the check silently converted the preregistered 99% reliability rule
into a 100% rule and counted one failure twice.

The correction checks model/provider identity wherever it is reported and requires
positive prompt usage plus exactly zero reasoning on every successful response. Failed
calls still remain in the reliability denominator. A regression test pins this rule.
No model call was rerun; the same content-addressed raw logs were reported again
offline. Two successive report generations with the same local HMAC key produced
byte-identical JSON and Markdown. The committed summary hashes are:

- `summary.json`:
  `9b47d26deefc488f229cf12cf238f3d4f3b721a6f991e601da2f7dbd4bb5eb9d`
- `summary.md`:
  `475bef8dfcc4891d89d4ab8e56f4258c628de335cfedeeb67d19bb92d60645bc`

## Evidence and recommendation

The safe report set is committed under
`experiments/evidence/retention-e5/report/`: per-arm JSON/Markdown, both paired
comparison reports, and the JSON/Markdown/XLSX summary. It contains HMAC item keys and
scored labels, but no prompt, transcript, raw model response, phone number, API key or
OpenRouter account metadata. Raw `run.jsonl` files remain gitignored; their SHA-256
identities are recorded in the execution ledger.

**Recommendation:** retain Gemini for this Retention workload and do not migrate to
either tested Qwen arm. The next decision-bearing work is production-shaped audio
ground truth, native-speaker review and live-report reconciliation—not selecting the
cheapest candidate that already failed the quality gate. Phase 3 can rerun the exact
locked workload on internal GPUs as a new runtime arm when that environment is ready.
