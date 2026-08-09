# LLM-judge direction: what SciCode's judge does, why ours is different, and the one next step worth building

**Written:** 2026-08-09, after reading
`_pegasus_001/_archive-bin/scicode-agentic/task-initial-50-dev/T481__fast_monte_carlo/tests/`
(`llm_judge.py`, `rubric.json`, `check_programmatic.py`, `check_structural.py`) side by
side with this repository's `src/evalgen/judge.py`.
**Status:** recommendation, nothing implemented.

## What the SciCode judge actually is

T481 grades an agent's open-ended scientific investigation with a **three-tier stack**,
and the tiers are ordered by what can be checked deterministically:

| Tier | File | Grades | How |
|---|---|---|---|
| 1 | `check_structural.py` | files exist, sections ordered, schema valid, enums legal | deterministic |
| 2 | `check_programmatic.py` | the agent's numbers vs golden constants; coherence between the agent's verdict and its own test outcomes | deterministic |
| 3 | `llm_judge.py` | the *quality of the scientific reasoning* — did the report distinguish the terminal from the stationary distribution, did it find the load-bearing flaw in Theorem 1 | Claude + a weighted rubric |

The judge's score **is part of the task's grade** there, and that is correct *for that
problem*, because tier 3 grades something no answer key can exist for: there is no
golden constant for "explained the eigenvector-perturbation failure well." The rubric
is heavily engineered to survive that responsibility: weighted criteria (5/5/4/…),
**hard caps** ("if the answer omits the T=9 reproduction, score ≤ 0.55"), and explicit
anti-credulity instructions ("award points only for demonstrated correctness, not
claimed correctness… Restating the prompt is not evidence").

## Why this repository's judge is deliberately the opposite

The discriminating question is: **does an answer key exist for the thing being graded?**

| | SciCode T481 | model-eval-harness |
|---|---|---|
| Output being graded | open-ended report + code + figures | closed-vocabulary labels (11 reasons, 4 outcomes, 4 products) |
| Answer key possible? | no — tier 3 is unkeyable by nature | yes — every label has a hand-checked answer, a byte-exact evidence span, and a `file:line` production-rule citation |
| Correct role for an LLM judge | **scorer of last resort** for the unkeyable residual | **auditor of the key itself** (`judge.py`: advisory, `changes_model_scores: false`, `selects_a_winner: false`) |
| What carries the decision | rubric score is a large share of the grade | deterministic paired scoring at alpha = 1/64; the judge feeds nothing |

Both projects made the same underlying choice — *deterministic wherever a key exists,
judgment only where it cannot* — and landed on opposite judge roles because the
problems sit on opposite sides of that line. Using SciCode's judge-as-scorer pattern
to grade this repo's model comparison would replace an existing exact-match key with a
reviewer measured (2026-08-09, 810 calls) to disagree with itself on 18.1% of units at
temperature 0. That direction is closed, and `docs/TEAM_GPU_RUNBOOK.md` already closes
it in writing.

## What genuinely transfers: grade the residual the key cannot see

This harness has exactly one decision-relevant quantity that is real, currently
invisible, and unkeyable by nature — the same shape as SciCode's tier 3:

**Error severity.** The scorer is all-or-nothing. A model that answers
`promotion related` where the truth is `save cost` (adjacent classes the production
prompt itself distinguishes with a single CRITICAL clause, `prompt.py:4345`) scores
exactly the same zero as a model that answers `network` — a different universe of
wrong. Experiment 3 measured that two-thirds of the incumbent's whole-item failures
were *over-labelling* (39 of 57: right answer plus unsupported extras), which is a
categorically milder defect than substituting a wrong class, and today's reports
cannot say so. If two candidates ever tie on exact-match, the one whose errors are
near-misses is the safer migration — and nothing in the harness can currently see
that difference.

### Recommendation: a severity rubric, built the SciCode way, scoped the harness way

One new advisory analysis (`judge severity` mode or sibling module), taking what
SciCode's file does well and discarding what this repo's philosophy forbids:

**Take:**
1. **A weighted rubric with hard caps**, in the rubric.json style, for a 4-level
   severity taxonomy per wrong answer — e.g. `over_labelling` (truth ⊂ answer),
   `near_family` (wrong class, same rule neighborhood, e.g. the `prompt.py:4333`
   vs `:4375` discount boundary), `cross_family`, `fabricated_class`. Hard caps in
   the SciCode sense: an answer containing a class the vocabulary does not license
   can never grade better than `fabricated_class`, whatever the prose argues.
2. **"Demonstrated, not claimed"** prompt discipline — the judge must quote the
   transcript span and the rule line that make an error near-family rather than
   asserting it; the existing byte-exact evidence gate already enforces the quoting.
3. **Deterministic pre-computation first** (their tier 1/2 instinct):
   `over_labelling` and `fabricated_class` are set arithmetic — computable without
   any model call. Only the near/cross-family judgment (rule-neighborhood reasoning)
   goes to the judge at all. Fewer calls, and the judge only does the part that needs
   judgment.

**Discard:**
1. **The silent fallback heuristic.** SciCode's judge quietly substitutes a keyword
   heuristic when no API key is present and the score looks identical downstream.
   That is precisely the failure mode this repo's refusal philosophy exists to
   prevent (`EvidenceError`, coverage refusal, the pin gates): here, no key → refuse,
   never a lookalike number.
2. **Score-enters-the-grade.** Severity stays a diagnostic surface like
   `evidence.py`'s rates: printed in reports, never joined to `PairedVerdict` or
   `decision()`, isolation enforced by the existing AST test.
3. **Single-shot absolute scores.** Their judge grades one artifact once. Ours pairs
   and replicates: severity judged blinded per unit, 3 replicates, strict majority,
   `no_majority` on ties — the machinery built this week.

**Cost to build and run:** the deterministic taxonomy is a fixture-first afternoon;
the judged remainder over Experiment 6c's wrong-answer units is roughly 150 units × 3
replicates ≈ 450 calls ≈ **$0.08** at Gemma 4 pricing.

**What it buys:** a per-arm severity profile next to the F1 table — "candidate X's
reason errors: 61% over-labelling, 30% near-family, 9% cross-family" — which is the
first new decision-relevant signal available from a judge here, and the only one that
does not compete with the answer key.

## Priority, stated honestly

This whole document is second-order. One real labelled batch — the `GroundTruth.input`
workbook production already reads (`fact_checker.py:494`), unlocked by the two header
rows `docs/data-contract.md` Ask 1 has been waiting to send since 2026-08-04 — retires
`RECONCILED: NO` and outranks any judge work by an order of magnitude. Build the
severity rubric while waiting on that data, not instead of asking for it.
