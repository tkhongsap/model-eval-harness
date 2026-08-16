# Experiment 20 — results

**Run 2026-08-15/16. Two arms completed, one failed. `retention_challenge_v1` (50 items /
64 scored rows), prompt `v9_16_base`, 3 replicates, `scoring_code_sha 9b4afc95…` on both
completed arms. The pack had never been evaluated by any model.**

## What this pack is, and why it is not `retention_v3`

`retention_v3` tests label semantics one axis at a time — Thai linguistics, ASR artifacts,
code-switching, context dilation. `retention_challenge_v1` tests **interaction structure**:
every item is exactly 18 turns, and the five families are

| family | n | what it does |
|---|---:|---|
| `compound_history` | 10 | the customer has called before; the earlier contact changes the answer |
| `negotiation_reversal` | 10 | the outcome flips mid-call after an offer is made or refused |
| `multi_product` | 10 | several products in one call, reaching different outcomes |
| `interaction_noise` | 10 | holds, transfers, interruptions, returning to an earlier topic |
| `boundary_outcome` | 10 | the end state is negotiated explicitly, near a label boundary |

It is also far denser in the thing models fail at: **11 of 50 calls carry more than one
product** (three carry three), against 8 of 138 in v3. And it carries no contamination
caveat — unlike v3's holdout, it was authored as a single block with no tune/holdout split.

## The headline: this pack cannot separate the two models

| | call outcome | reason | product |
|---|---:|---:|---:|
| Gemini 2.5 Flash *(production)* | **0.951** | 0.831 | **0.976** |
| Qwen3.8 27B *(our GPU)* | 0.930 | **0.833** | 0.970 |

**The F1 gap on call outcome is not a real difference, and the paired test is what says so.**
Across 50 calls the two models disagreed on **exactly one**:

| dimension | both right | both wrong | only Gemini | only Qwen3.8 | discordant | verdict |
|---|---:|---:|---:|---:|---:|---|
| call outcome | 45 | 4 | 1 | 0 | **1** | UNDERPOWERED |
| reason | 19 | 19 | 5 | 7 | 12 | **INDISTINGUISHABLE** (net +2, band ±10) |
| product | 48 | 2 | 0 | 0 | **0** | UNDERPOWERED |

On product they never disagreed at all. UNDERPOWERED is not a tie and not a pass: at
alpha 1/64 per side it takes 6 discordant clusters to call a winner, and there were 1 and 0.
**Reading 0.951 vs 0.930 as "Gemini is better" is exactly the error the paired test exists to
prevent** — on the one call they differed, Gemini happened to be right.

The one dimension with enough disagreement to measure, `reason`, comes back
INDISTINGUISHABLE with Qwen3.8 marginally ahead on points.

### Reason is hard for both

**19 of 50 calls were failed by both models** on reason — 38%. That is a property of the
label type and this pack, not of either model.

## Gemini did not vary on this run

| Gemini arm | raw-unstable | scored-unstable | `N_flip` |
|---|---:|---:|---:|
| `e17-gemini`, 2026-08-14, `retention_v3` | 111 / 138 | 29 | 34 |
| `e20-chal-gemini`, 2026-08-15, this pack | **0 / 50** | **0** | **0** |

Byte-identical text on all three replicates of all 50 calls, under the same `Google` provider
pin and the same decoding that produced 111/138 the day before. **This is evidence the
2026-08-14 determinism collapse was an episode rather than a permanent change**, and it is the
blocking question `docs/experiment17-results.md:163-166` names.

It is **not a clean replication** of that measurement and should not be quoted as one: this is
a different pack with different, shorter transcripts. Experiment 19 — a byte-identical replay
of the E17 workload — is still the right instrument. But the direction is informative, and it
was free.

Qwen3.8 went the other way: **46 of 50 calls raw-unstable, but only 2 reaching a scored
label**. That is the vLLM continuous-batching churn seen throughout this project — the text
moves, the labels mostly do not.

## Cost, tokens, speed

| | Gemini | Qwen3.8 |
|---|---:|---:|
| input tokens / call | 2,823.3 | **2,567.9** (9.0% fewer) |
| output tokens / call | **223.0** | 241.4 |
| p50 latency | 2.109 s | 11.407 s |
| wall clock, 150 calls | 45.7 s | 483.3 s |
| cost | $0.173 | not metered |
| retries / truncation | 0 / 0 | 0 / 0 |

Speed is **not** like-for-like: Gemini ran at concurrency 8 over the public internet, Qwen3.8
at concurrency 4 against our own host. Two variables differ besides the model, which is why
the report gives Gemini's latency as context rather than as a table row.

## Gemma 4 12B did not run, and that is recorded rather than hidden

The Gemma arm was attempted and **every one of its 150 calls returned HTTP 500**:

```
litellm.InternalServerError: Hosted_vllmException -
Cannot connect to host 10.94.154.104:8000 ... Received Model Group=gemma-4-12b-it
```

The gateway at `10.94.154.102` could not reach Gemma's vLLM backend on `.104`. Qwen3.8, which
sits on a different backend, was unaffected and ran clean in the same session.

Three things worth carrying, and the first is the embarrassing one:

1. **The smoke caught it, and the runner ignored the signal.** `e20-smoke-gemma` came back
   `transport_error=3` at 22:28 — the same 500, the same dead backend. `scripts/experiment20.py`
   gated the smoke on the **process exit code**, and `evalgen stability` exits 0 when every one
   of its calls fails: the harness ran fine, the model did not. So the script printed "all
   three smokes passed" and went on to spend 11 minutes rediscovering the same failure 150
   times. The pre-flight had the answer ten minutes early and the gate threw it away.
   `_all_ok()` now reads `outcome_counts` from the run and refuses on anything that is not
   `ok`. **An exit code is not a result.**
2. **`/v1/models` kept listing `gemma-4-12b-it` for the entire outage**, and still did 90
   minutes later. The catalog is not a readiness signal —
   `Token_Factory_API_Guide.md:158` says not to trust it, and this is what that looks like.
   `scripts/wait_for_model.py` was written in response: it sends one real generation and
   treats only a 200 as up. It polled for 90 minutes across 58 probes; Gemma never returned.
3. **A dead arm scores 1.000 on product.** `flatten.to_rows` emits a ground-truth-shaped
   skeleton for a payload it cannot read, which keeps arm denominators equal — and means an
   arm that answered *nothing* carries the ground truth's own product names and scores a
   perfect weighted F1 on that dimension. `compare-e20-gemma-vs-gemini.txt` shows exactly
   that: 150/150 transport errors, zero correct clusters, product w-F1 **1.000**.
   `model_comparison_report.read_run` now refuses any run with a non-`ok` row, so a dead arm
   cannot reach a published table. Found by the verification pass, not by reading the code.

Run `20260815-223809Z-e20-chal-gemma` is kept as the record of the failure. Adding the arm
back to `configs/comparison/retention-challenge-v1.json` and regenerating is all that is
needed once the backend returns.

## What this says

**The challenge pack does its job as a difficulty probe and fails as a discriminator.** Both
models get most of it right (Gemini 24/50, Qwen3.8 26/50 correct on all three replicates), and
where they differ there is not enough of it to conclude anything at this alpha. At 50 items it
has roughly a third of v3's scored rows, and the paired test is unforgiving about that.

**The useful conclusion is negative and worth having:** nothing in this pack contradicts the
v3 result. Qwen3.8 is not measurably behind production Gemini on structurally harder calls
either. That is a second, independent pack agreeing — not a stronger claim on one.

**If the pack is to settle anything, it needs to be bigger.** 50 items yields 1 and 0
discordant clusters on two of three dimensions. Two to three hundred items of the same design
would put those dimensions in range.

`RECONCILED` stays **NO**. The pack is synthetic and authored in-project, so every number here
— Gemini's included — is an upper bound.

## Next

1. **Rerun the Gemma arm** when `10.94.154.104:8000` is back. ~8 minutes, no cost, one command.
2. **Experiment 19** — the byte-identical Gemini replay — is still the right way to settle the
   determinism question. This run is suggestive, not conclusive.
3. **Grow the challenge pack** to 200+ items if it is meant to separate models rather than
   probe difficulty.

## Provenance

| | |
|---|---|
| runs | `20260815-222917Z-e20-chal-gemini`, `20260815-223004Z-e20-chal-qwen38`, `20260815-223809Z-e20-chal-gemma` *(failed)* |
| smokes | `20260815-2228*-e20-smoke-{gemini,qwen38,gemma}` — 3 longest items × 1 replicate. Gemini and Qwen3.8 `ok=3`; **Gemma `transport_error=3`**, which the runner failed to gate on |
| reports | `out/reports/compare-e20-{qwen38,gemma}-vs-gemini.txt`, `compare-e20-qwen38-vs-gemma.txt` |
| `scoring_code_sha` | `9b4afc95…`, unchanged start to finish, verified at both ends |
| `workload_sha` | `141510a2…` on both completed arms |
| `testset_sha` | `a3029a70…` — appeared in no prior run |
| published | `docs/reports/model-comparison-challenge.html`, `out/case-explorer-challenge.html` |
| deviations | `top_p 1.0` (endpoint rejects 0, inert at greedy decode); `reasoning_effort` `none` (Gemini) vs `provider-default` (GPU); fp8. All as E17/E18 declared. |

Run artifacts stay under `out/`, uncommitted: raw completions and per-item records do not go
to git.
