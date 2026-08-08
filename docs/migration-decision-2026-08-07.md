# Migration decision — Retention: Gemini 2.5 Flash vs. Qwen3.6 (27B / 35B-A3B)

**Written:** 2026-08-07, after Experiments 1–6. **Independently corroborated
2026-08-08** by Experiment 7 — run separately, on a different provider pin for the
27B arm (Chutes, not Morph or CoreWeave), reaching the same decision by a different
route. See `docs/experiment7-results.md`; the note at the foot of this memo covers
what changed and what didn't.
**Status:** Recommendation, not a final verdict — see "What this is not," below.
**Question being decided:** should True Corp migrate the Retention call-labelling
workload off `google/gemini-2.5-flash` onto a Qwen3.6 model?

## The recommendation

**No. Do not migrate to `qwen/qwen3.6-27b` or `qwen/qwen3.6-35b-a3b` on the evidence
this project has produced.** Keep Gemini. This is not a close call: under the
regime production actually runs (reasoning disabled), both Qwen candidates **FAIL** a
pre-registered, statistically powered decision rule — one on reliability and three
scored dimensions, the other on all three scored dimensions and stability. Every
apparent Qwen advantage this project ever measured came from giving Qwen a compute
budget (reasoning) that production does not give it, and disappeared the moment that
budget was taken away.

Six experiments and a large amount of infrastructure went into reaching this
conclusion. The infrastructure was worth building — it caught real defects along the
way, including in itself. But the conclusion has not moved since Experiment 4, and
nothing built since then has been the thing that would move it. That is the honest
prompt for this memo: to write the answer down rather than keep building around it.

## The evidence, in the order it actually happened

| # | What was tested | What it found |
|---|---|---|
| **1** | Baseline, 22 rows | INDISTINGUISHABLE on all three dimensions. Two harness defects fixed along the way (a broken decoder endpoint, a fabricated evidence claim) — findings about the harness, not the models. |
| **2** | Is Qwen's `reason` edge real? | Qwen is **nondeterministic at temperature 0**; Gemini is not (`N_flip` 8 vs 0). `reason` net hit +6, grazing the pre-registered AHEAD line — correctly *not* trusted, because it was one draw from an arm caught flipping. |
| **3** | Same question at 108 rows | **The edge reversed.** +5/+6 at 22 rows became **-1** at 108. This is the single clearest result in the whole project on why small-sample leads here cannot be trusted. |
| **4** | Is the cheaper 35B-A3B viable? (and: does the endpoint matter?) | 35B-A3B loses to 27B on everything. **The real finding: re-running 27B on a different endpoint after the first one broke moved `reason` net from -1 to +24**, same model, same prompt, same pack — because the new endpoint reasons and the old one didn't. This is the finding everything after it had to control for. |
| **5A** | `retention_v3` (4 new families), Qwen **reasoning-enabled** | Both Qwen arms AHEAD on `reason` at a real significance level — bought entirely on 2.3–2.6M reasoning tokens against Gemini's zero. Gemini alone degrades at 10x context length; Qwen doesn't, on this pack. |
| **5B** | Same pack, Qwen **reasoning explicitly disabled** — the regime production runs | **Both Qwen candidates FAIL.** See below. |
| **6** | Independent-model audit of the ground truth itself | 262 real scorer disagreements reviewed by a model with no stake in either arm. 62.6% confirm the ground truth; 6.9% flagged as possibly wrong, four cross-validated by independent re-derivation. **None of it changes 5B's result** — a diagnostic on data quality, not a re-vote on the migration. |

## The decision-bearing result: Experiment 5B

Same prompt, schema, decoding, and **explicit reasoning-off regime** across all three
arms — the configuration `config/model_setting/retention.yml` actually runs in
production. 138 items, three replicates, 1,242 calls, pre-registered decision rule
(reliability ≥99%, no scored dimension BEHIND, stability not BEHIND).

| Candidate | Parse valid | Call result | Reason | Product | Stability | Decision |
|---|---:|---|---|---|---|---|
| Qwen3.6 27B / Morph | 359/414 (86.7%) | BEHIND (-19/13) | BEHIND (-19/17) | UNDERPOWERED | BEHIND (-121/25) | **FAIL** |
| Qwen3.6 35B-A3B / AkashML | 414/414 (100%) | BEHIND (-11/11) | BEHIND (-24/16) | BEHIND (-10/8) | BEHIND (-131/27) | **FAIL** |

27B fails outright on reliability — 54 of 414 calls hit HTTP 429 under real load, with
no retries permitted by the locked plan. 35B-A3B parses cleanly but is statistically
behind Gemini on **every single scored axis**, including stability: its answers move
between replicates on the same byte-identical request far more than Gemini's do.

## The confound that explains the whole arc

| Regime | 27B `reason` net vs. Gemini | Reasoning tokens | Cost per call (approx.) | Latency p50 |
|---|---:|---:|---:|---:|
| Reasoning-off (Morph, Exp. 3) | **-1** | ~0 | — | — |
| Reasoning-on (CoreWeave, Exp. 4/5A) | **+24 / AHEAD** | 1.7–2.4M per 300–414 calls | 12–13× Gemini | 15–19× Gemini |
| Reasoning-off, pinned and verified (Exp. 5B) | **BEHIND (-19)** | 0 | $0.4858 (27B) vs $0.4347 (Gemini) | 4.14s vs 2.14s |

**Every reading of "Qwen is competitive" in this project's history was a reading of
Qwen with a reasoning budget production does not grant it.** Take the budget away —
which is the only way to actually match what production runs — and Qwen is not just
unreasoning, it is *behind*, more expensive per call than Gemini even without the
reasoning tokens, and slower. There is currently no configuration in which Qwen is
simultaneously (a) running the regime production uses and (b) competitive with Gemini.

## What Experiment 6 adds, and what it doesn't

The independent judge reviewed every place the harness's scorer disagreed with a
model's answer and found the ground truth mostly holds up (62.6% of disagreements
confirm it outright, 30.5% are genuine ambiguity rather than a flaw). Four items —
most notably `RET-85` — are worth a human reading before anything is edited, the same
way `RET-11`'s defect was originally found. **None of this touches the migration
call.** Even if every one of the nine flagged items turned out to be a real
ground-truth defect and got corrected, that is 9 items out of 150 scored rows on one
synthetic pack — nowhere near enough to erase a result this project reproduced across
four separate experiments and two entirely different candidate models.

## What would change this recommendation

- **A Qwen endpoint that is simultaneously reliable (≥99% parse-valid) and reasoning-off
  at the required scale.** Morph qualified at 6 calls and failed at 414; no other
  provider has been shown to hold both properties together.
- **A future Qwen release that closes the reliability/quality gap in that regime** —
  this recommendation is about the two specific models tested, not about the vendor.
- **Production-shaped ground truth and a live fact-check comparison** — see below.
  This is the only thing that can turn a recommendation into a verdict.

## What this is not

- **Not `RECONCILED: YES`.** No code path in this repository ever prints that, and this
  memo doesn't either. Everything above is a comparison between models on synthetic,
  pre-tagged Thai *text*; production is handed audio and does its own transcription.
  The ranking transferring to audio is unproven.
- **Not a claim that Gemini is accurate**, only that it is measurably ahead of the two
  tested alternatives on every regime this project could construct.
- **Not permanent.** It is a recommendation against two named models under one
  workload, current as of the evidence above, revisable the moment any of the
  conditions in the previous section is met.

## On the infrastructure built to reach this point

Six experiments produced, along the way: a paired-comparison harness with
statistically calibrated verdict bands, a provider-qualification gate pipeline, an
independent-judge diagnostic, and a full audit of all of it. That tooling is real,
tested, and reusable — for the *next* model comparison, or for re-testing these same
two models if a qualifying endpoint appears. It is not, on its own, a reason to keep
running more experiments against the current pair. The recommended next spend of
effort is not more infrastructure; it's either (a) the production-shaped ground truth
this repository has needed since Experiment 1, or (b) nothing, until a new candidate
or a new endpoint gives this decision a reason to be reopened.

## Addendum: independent corroboration from Experiment 7 (2026-08-08)

Experiment 7 was run separately — different session, different day, a different
provider pin for the 27B arm (Chutes rather than Morph or CoreWeave) — and reached the
same recommendation: retain Gemini. It is worth reading as a second data point rather
than a duplicate, because the *shape* of the failure differs in a way that is itself
informative:

| | Experiment 5B (this memo's primary evidence) | Experiment 7 |
|---|---|---|
| 27B provider | Morph | Chutes |
| 27B reliability | **FAIL** — 359/414 parse-valid (86.7%, below 99% gate) | 414/414 parse-valid |
| 27B decision | FAIL on reliability, call result, reason, stability | FAIL on stability alone — call result and reason were UNDERPOWERED or INDISTINGUISHABLE, not BEHIND |
| 35B-A3B decision | FAIL on all three quality dimensions plus stability | FAIL on all three quality dimensions plus stability (same shape) |
| Judge | 262 disagreements, 6.9% flagged as possible ground-truth error | 360 opinions, 38 flagged (≈10.6% of the total, ≈12.1% of the 314 it could usably rate) |

**Read carefully, this strengthens rather than repeats the recommendation.** On a third
endpoint for the 27B arm, the specific failure mode changed — it stopped being a
reliability collapse and became a pure stability failure, with 129 of 138 items
changing their exact answer across three identical replicate calls. That is a worse
number, not a better one: it means 27B's instability is not an artifact of one flaky
endpoint (Morph), but reproduces on a second, independent one (Chutes) at even higher
magnitude. The one dimension the audit already flagged as chronically underpowered —
`product` — stayed underpowered here too, on yet another pack execution, reinforcing
Experiment 6's structural point that `product` cannot currently be strengthened by
volume alone.

The judge's higher flagged rate (≈10–12% vs. this memo's 6.9%) is also worth noting
rather than averaging away: it may reflect a different item mix, a since-updated
`judge.py`, or a real difference in how disagreement-rich the two independent runs
were. Either way, it is more reason for a human to work through the Experiment 7 flag
queue specifically, not less.

## Citation trail

| Claim | Source |
|---|---|
| Full experiment narratives | `EXPERIMENTS.md`, Experiments 1–6 |
| Experiment 5B full evidence, operations, and Morph's failure mode | `docs/experiment5-results.md` |
| Experiment 6 judge methodology and findings | `docs/overnight-audit-and-experiment-6-report.md` |
| Experiment 7 independent corroboration, different provider pin | `docs/experiment7-results.md` |
| Reasoning-regime confound, first documented | `EXPERIMENTS.md`, Experiment 4 |
| `RECONCILED` stamp and why no code path prints YES | `AGENTS.md`, "Project-Specific Notes" |
