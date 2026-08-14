# Experiment 17 — results

**Run 2026-08-14. Plan: [`experiment17-plan.md`](./experiment17-plan.md). Three arms, 1,242
calls, `retention_v3` (138 items / 150 scored rows), prompt `v9_16_base`, 3 replicates,
`scoring_code_sha 9b4afc95…` on all three.**

## The finding is about the incumbent

Gemini 2.5 Flash stopped being deterministic between 2026-08-10 and 2026-08-14.

| `google/gemini-2.5-flash`, provider pinned `Google` | raw-unstable / 138 | scored-unstable | `N_flip` |
|---|---:|---:|---:|
| **2026-08-10** (`e10-gemini-base`) | **0** | **0** | **0** |
| **2026-08-14** (`e17-gemini`) | **111** | **29** | **34** |

Same model id. Same provider pin. Same prompt, byte for byte — `prompt_tokens` is 1,237,746 in
both runs and the per-item `prompt_token_spread` is identical entry for entry. Same decoding
(`temperature 0`, `top_p 1.0`, `seed 0`, `max_tokens 8000`, `reasoning_effort none`), same
`max_attempts 3`, same concurrency 8, zero resumed cells, zero truncation, 414/414 `ok` both
times. `system_fingerprints` is `<not reported>` on both, so it cannot discriminate.

On 2026-08-10 the model returned **byte-identical text on all three replicates for every one of
the 138 calls**. Four days later it returned different text on 111 of them, and on 29 the
difference moved a label the scorer reads.

**This is not a scorer artifact, and that was checked before the arms ran rather than argued
afterwards.** `scoring_code_sha` moved from `cefd4ae9…` to `9b4afc95…` on 2026-08-12 when
`apps.py` joined the digest, so "the scorer changed" and "the model changed" were confounded.
Re-scoring the *2026-08-10 outputs* with *today's* code — legal, because both E10 runs carry the
old digest — reproduces its numbers exactly:

| dimension | printed 2026-08-10 | same outputs re-scored today |
|---|---|---|
| `call_result` | 0.955 | **0.955** |
| `reason` | 0.823 | **0.823** |
| `product` | 0.960 | **0.960** |

and `N_flip = 0` still. The digest moved; the arithmetic did not. What moved is Google's
serving.

### Why it matters more than the model ranking

Experiment 7's decision turned on stability. Qwen led Gemini on `call_result` F1 (0.969 vs
0.955) and was disqualified because it was not stable. The reference point for that judgement
was a Gemini that never varied. **That reference point no longer exists**, and on this run the
incumbent is the *least* stable of the three arms by the measure the scorer can see:

| arm | `N_flip` | raw-unstable / 138 | **scored-unstable** | cosmetic share |
|---|---:|---:|---:|---:|
| `e17-gemini` | 34 | 111 | **29** | 73.9% |
| `e17-tf-gemma` | 25 | 133 | **15** | 88.7% |
| `e17-tf-qwen` | 15 | 129 | **8** | 93.8% |

All three arms churn their raw text; they differ in whether the churn reaches a scored label.
Qwen's does least often. Note also what the "cosmetic" column is *not* saying: the unscored
`recommendation` field moved on 80–120 calls per arm, and nothing here measures what reads it
downstream.

## Quality

Weighted F1, replicate 1, production scorer grain. E10's Gemini is shown for continuity; it is
the same workload and the same scorer, four days earlier.

| dimension | `e17-gemini` | `e17-tf-gemma` | `e17-tf-qwen` | *(e10-gemini, 08-10)* |
|---|---:|---:|---:|---:|
| `call_result` | 0.955 | 0.929 | **0.962** | *0.955* |
| `reason` | **0.838** | 0.792 | 0.821 | *0.823* |
| `product` | **0.960** | 0.933 | 0.932 | *0.960* |

Gemini's `call_result` and `product` land exactly on their 08-10 values; `reason` rose 0.823 →
0.838. Two of three reproduced to three decimals while the model's determinism collapsed, which
is worth stating plainly: **aggregate F1 was nearly blind to a change this large.** Only the
stability columns saw it.

Denominators differ by arm on `reason` (157 / 164 / 160) because they count predicted reason
labels; joined rows were 145 / 142 / 141 of 150 on `call_result`. Parse failures: **zero on all
three arms.**

## The verdict the harness actually gives

One Bernoulli pair per call cluster, exact band at alpha 1/64 per side.

**`gemma-4-12b-it` (fp8) — BEHIND on all three dimensions.**

| dimension | both right | both wrong | Gemini only | gemma only | net | band | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `call_result` | 118 | 8 | 11 | 1 | −10 | ±10 | **BEHIND** |
| `reason` | 61 | 48 | 21 | 8 | −13 | ±13 | **BEHIND** |
| `product` | 124 | 5 | 9 | 0 | −9 | ±9 | **BEHIND** |

**`qwen3.6-27b-fp8` — not distinguishable from Gemini where the data can tell.**

| dimension | both right | both wrong | Gemini only | qwen only | net | band | verdict |
|---|---:|---:|---:|---:|---:|---:|---|
| `call_result` | 125 | 6 | 4 | 3 | −1 | ±7 | **INDISTINGUISHABLE** |
| `reason` | 67 | 42 | 15 | 14 | −1 | ±13 | **INDISTINGUISHABLE** |
| `product` | 129 | 5 | 4 | 0 | −4 | — | **UNDERPOWERED** |

UNDERPOWERED on `product` means **not measured** — four discordant clusters, and the threshold
needs six. It is not a tie and must not be reported as one.

## Mechanism table

Every row is FAIL/FAIL on every arm: the verdict requires every item correct on *every*
replicate, and at 138 items nothing survives that. The always-correct rates are the signal.

| mechanism | n | Gemini | gemma | qwen |
|---|---:|---:|---:|---:|
| clear | 30 | 16 | 13 | 16 |
| thai_linguistic | 30 | 15 | 13 | **16** |
| tiebreak | 17 | 9 | 7 | 9 |
| multislot | 10 | 4 | 5 | **5** |
| escape | 13 | 6 | 5 | **7** |
| long_context | 12 | 9 | 7 | **11** *(FLAKY, not FAIL)* |
| asr_noise | 10 | 5 | 5 | 4 |
| code_switch | 10 | 3 | 2 | **5** |
| regression | 6 | 4 | 5 | **5** |

Qwen matches or beats Gemini on 8 of 9 and loses only `asr_noise` (4 vs 5). Gemma is at or below
Gemini on 7 of 9. `long_context` is the one cell in the whole table that is not FAIL — qwen at
11/12, FLAKY — which is notable given those are the 12–18k-character dilated items.

## Cost, tokens, latency

| arm | calls | attempts | prompt tok | completion tok | cost USD | p50 | p95 | max | wall | throughput |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `e17-gemini` | 414 | 416 | 1,237,746 | 86,449 | **0.5616** | 2.06 s | 2.80 s | 11.36 s | 1.9 min @ c8 | 3.64 calls/s |
| `e17-tf-gemma` | 414 | **414** | 1,245,198 | 104,839 | 0 (internal) | 12.00 s | 16.63 s | 22.59 s | 21.3 min @ c4 | 0.324 calls/s |
| `e17-tf-qwen` | 414 | **414** | 1,131,948 | 95,337 | 0 (internal) | 11.38 s | 17.61 s | 23.55 s | 20.5 min @ c4 | 0.336 calls/s |

**Throughput is the real gap, and it is ~11×.** Both GPU arms land on 0.32–0.34 calls/s, the
same figure the shakedown measured on a different pack — the gateway, not the model, is the
bottleneck, and the shakedown found c8 at 0.96× c4, so there is no headroom above concurrency 4.
Per-call latency tells the same story: ~12 s median against Gemini's 2 s. Cost reads 0 because
Token Factory reports none; that is "not metered here", not "free".

One point the other way, and it is the only column where Token Factory beats OpenRouter
outright: **both GPU arms took 414 API attempts for 414 calls — zero retries.** Gemini took 416.
`max_attempts` was 3 on all three arms, so the retry budget was there and the internal endpoint
did not need it. Two transport blips in 414 is a fine result for Gemini too; the point is that
the gateway's slowness is not accompanied by flakiness.

## What this says

**`gemma-4-12b-it` is not a candidate.** BEHIND on all three dimensions, at or beyond the band
on each, and below Gemini on 7 of 9 mechanisms.

**`qwen3.6-27b-fp8` is the one worth pursuing.** Statistically indistinguishable from Gemini on
both powered dimensions, ahead on `call_result` F1 (0.962 vs 0.955), the most stable of the
three by scored instability (8 vs Gemini's 29), and the only arm to produce a non-FAIL mechanism
row. Against that: `product` is unmeasured, its `reason` F1 trails (0.821 vs 0.838), and
throughput is ~11× worse than the incumbent's.

**Neither result is a migration verdict, and the reason is not caution — it is that the
comparison rests on a reference point that moved during the experiment.** Gemini's determinism
collapsed between 08-10 and 08-14 with no change on our side. Until that is understood, "Qwen is
indistinguishable from Gemini" describes Gemini as it behaved on 2026-08-14, not a stable
incumbent.

`RECONCILED` stays **NO**. `retention_v3` is synthetic and authored in-project, so every number
here — Gemini's included — is an upper bound.

## Next

1. **Re-run the Gemini arm on consecutive days** to establish whether 08-14's instability is a
   permanent change or an episode. This is the blocking question; it is ~2 minutes and $0.56.
2. **Get `product` powered** for qwen. Four discordant clusters out of 138 items; a larger pack
   or the holdout would settle it.
3. **Ask why the gateway caps at 0.33 calls/s.** Both models hit the identical figure on two
   different packs, which points at LiteLLM or the vLLM scheduler, not the models.
4. `qwen3-asr-1.7b` remains untested — a speech model, and this repository has no audio fixtures.

## Provenance

| | |
|---|---|
| runs | `20260814-132425Z-e17-gemini`, `20260814-132642Z-e17-tf-gemma`, `20260814-134803Z-e17-tf-qwen` |
| reports | `out/reports/compare-e17-{gemma,qwen}.txt`, `out/reports/control-e10-rescored-at-head.txt` |
| `scoring_code_sha` | `9b4afc95…`, unchanged start to finish, all three arms |
| `workload_sha` | `6b1ab3ed…` on all three, and equal to `e10-gemini-base`'s |
| refusals | none. Both compares exited 1 — the harness ran and found problems, which is a result. Exit 2 is a refusal. |
| deviations | `top_p 1.0` (endpoint rejects 0, inert at greedy decode, and the value E10's Gemini already used); `reasoning_effort` `none` vs `provider-default`; fp8. All three declared in the plan, all recorded and non-blocking. |

Run artifacts stay under `out/`, uncommitted: raw completions and per-item records do not go to
git. Only the aggregate summary under `experiments/evidence/retention-e17/` does.
