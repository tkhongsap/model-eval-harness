# Overnight report: audit, an independent judge, and Experiment 6

**Date:** 2026-08-07. Written for the morning after the standing instruction "look
through our experiments and see what else we can improve, see what bugs we missed, run
another experiment, write a complete report."

## The one-paragraph version

Before writing anything new, everything that merged in overnight from a second, parallel
session (Experiment 5B's enterprise framework -- provider qualification gates, the
decision-rule pipeline, ~2,500 lines nobody had code-reviewed) got a real audit: 21
agents, 12 candidate findings, all 12 independently verified, zero refuted, zero false
alarms reported here. Six were fixed tonight, two are recorded as the top of the next
session's list, two were trivial documentation corrections, and one was found by hand
while testing rather than by the audit workflow. Then a genuinely new capability was
built and used: an independent third model adjudicates every place this project's own
scorer disagrees with a model's answer, built with the same hand-computed-expectation
discipline every other metric here has, and architected so it can never quietly become a
fourth scored dimension. It reviewed 262 real disagreement items and found four
candidates -- cross-validated by three independent comparisons agreeing with each other
-- worth a human's attention, the same way RET-11 was originally found. Both suite modes
are green (546 passed / 11 skipped standalone, 557 / 0 differential, in this
environment), and everything below is committed.

## What "look through our experiments" turned up

The honest starting point: I had never actually read `experiments.py`, the new parts of
`cli.py`, or the changes to `evalharness/compare.py` and `evalharness/manifest.py` line
by line before tonight. I had checked that the merge didn't corrupt any Markdown file and
that the test suite was green, and reported that as verification -- which it was, for
what it checked. It never asked whether the new *logic* was right.

A multi-agent workflow did four things in sequence: mapped the new architecture (what
does `decision()` actually implement, what does the qualification gate actually check,
does `report.py`'s mechanism-rate math still work after the merge), hunted for bugs
across five categories (correctness, data integrity, statistics, test coverage, doc
consistency), then had every candidate finding independently re-verified by a second
agent instructed to try to refute it by reading the real code -- not the first agent's
description of the code. Twelve findings survived. Here is what they were and what
happened to each.

### Fixed

| # | What was wrong | Where |
|---|---|---|
| 1 | `decision()` never routed an UNDERPOWERED stability verdict to INCONCLUSIVE -- fell through to PASS with no statistical evidence that stability held | `src/evalgen/experiments.py` |
| 2 | `role` (which arm is "incumbent" for every paired comparison) was never validated anywhere, including in the locked-plan deep audit that otherwise re-verifies everything else | `src/evalgen/experiments.py` |
| 3 | `retention_v3.manifest.json`'s own embedded claims (hashes, item counts, family breakdowns) were never recomputed against the real files -- only the manifest's own bytes were pinned | `tests/test_testset_pack.py` (new test) |
| 4 | Two stale cross-references in `EXPERIMENTS.md` pointing at the wrong passage, caused by an earlier insertion shifting line numbers without updating the citations | `EXPERIMENTS.md` |
| 5 | `tests/test_enterprise_experiments.py` had no `sys.path.insert`, unlike every other test file, and only worked by accident of collection order | `tests/test_enterprise_experiments.py` |
| 6 | A minor call_id notation inconsistency between two comparison tables (no factual error, just two different ways of writing the same 100 numbers) | `tests/fixtures/testsets/README.md` |

Findings 1 and 2 are real gate-logic gaps -- the kind of thing that would silently pass
an experiment that should have been flagged. Neither actually fired on the committed
Experiment 5B evidence (verified, not assumed: its stability verdicts were `BEHIND`, not
`UNDERPOWERED`, and its plan's roles were already correct), which is exactly why nobody
noticed. Both now have regression tests that reproduce the original failure.

### Recorded, not fixed tonight

| # | What's missing | Why it waits |
|---|---|---|
| 7 | `cmd_qualify` (spends real API calls) has zero test coverage | Needs a mocked client for a code path that spends real money when it works. Building that scaffolding at 2 a.m. risked introducing the bug it was meant to catch. |
| 8 | `cmd_experiment_run`'s three safety gates (`--confirm-plan-sha` mismatch, an unavailable arm, a bad concurrency level) are only ever tested with a passing value | Same reasoning -- real negative-path tests for real safety gates deserve daylight review, not a rushed patch. |
| 9 | `manifest.workload_sha`'s forbidden-field guard and the era-mixing checks in `_refuse_incomparable` are untested | Same category as 7/8. |
| 10 | `_disagreement_section` -- the table a human reads to approve or reject a migration -- has its rendered text asserted by nothing; existing tests discard it via a string split before checking anything | A content-assertion test is the right fix, not a rewrite of the rendering, and deserves its own careful pass rather than a bolt-on tonight. |
| 11 | `reliability_gate`'s 0.99 threshold is a hardcoded Python default, not read from the plan's own `quality_gates.minimum_parse_valid_rate` field that `validate_plan` computes and displays as authoritative | Currently harmless (both values are 0.99); a real fix threads a parameter through several call sites and deserves review, not a rushed edit. |

Findings 7-11 are written down here specifically so they don't have to be rediscovered.
That is the whole reason this section exists rather than a silent to-do list.

## The new capability: an independent judge

Every experiment in this project, including tonight's audit, has trusted one thing
without an automated check: that the ground truth in `retention_v*.gt.csv` is right.
RET-11's defect -- the first and, until tonight, only ground-truth correction this
project has made -- was found by a person reading a transcript against a production rule
by hand. Nothing has re-checked the rest of the pack that way since.

`src/evalgen/judge.py` automates a version of that same question. For every item where
the harness's own exact-match scorer says an arm disagrees with the ground truth, an
independent model -- not either arm being compared -- is shown the transcript, the
production rule citation, the ground truth, and both arms' answers (blinded as "Answer A"
/ "Answer B", with which arm sits in which slot decided by a hash of the item so the
blinding is stable and unbiased), and asked to classify: is the ground truth simply
right, is the disputed answer also defensible, does the ground truth itself look wrong,
or is it genuinely unclear.

### Why this is not "just another metric"

Every metric added to this project so far has had to answer the same three questions
before being trusted, and the judge answers all three the same way:

1. **Was the expectation written before the code?** Yes --
   `tests/fixtures/judge/HAND-COMPUTED.md` fixes ten constructed raw responses and their
   exact aggregate arithmetic, by hand, before `judge.py` existed. What CAN be
   hand-computed here is not the judge's opinion (which is inherently subjective) but the
   deterministic parsing and aggregation code around it -- the same distinction
   `evidence.py` draws between the mechanical `asr_normalise` function and the model
   output it processes.
2. **Can it become a fourth scored dimension by accident?** This is where the judge goes
   further than the two diagnostics that came before it. `evidence.py` and
   `fabrication.py` both state, in their own docstrings, that they are never read by the
   scoring path -- but nothing has ever tested that claim. `tests/test_judge.py` parses
   the AST of `report.py` and `evalharness/compare.py` directly and fails if either ever
   imports `judge` at all. The claim is enforced, not just written down.
3. **Does it respect the evalgen/evalharness boundary?** Yes, and for free:
   `judge.py` lives in `evalgen` because it makes a network call, and
   `test_boundary.py`'s existing, unmodified check (`evalharness` may never import
   `evalgen`) already makes it impossible for the scoring library to reach it, transitively.

### What it actually found

262 disagreement items, three independent pairwise comparisons (Gemini vs Qwen27B, Gemini
vs Qwen35B-A3B, Qwen27B vs Qwen35B-A3B, all from Experiment 5A's reasoning-enabled data --
Experiment 5B's raw logs are not on this machine, see the note below), one call per item
at temperature 0, **zero parse failures across all 262 calls**.

| Verdict | Count | Rate |
|---|---:|---:|
| `ground_truth_correct` | 164 | 62.6% |
| `defensible_disagreement` | 80 | 30.5% |
| `ground_truth_error` | 18 (9 distinct items; several appear in more than one pairing) | 6.9% |
| `unclear` | 0 | 0.0% |

**Four flags were reached independently by all three pairwise comparisons**, which is a
real corroboration signal: `RET-85`, `RET-94`, and `RET-100` (all `call_result`), plus
`RET-59` (`reason`). The strongest of these on inspection is `RET-85` -- the ground truth
says `save`, but the customer cancels two of the three services outright and the judge's
argument (independently reconstructed three times) is that nothing was actually saved.
It has the same shape as RET-11: a specific, transcript-grounded objection, not a vague
complaint. It has not been applied to any fixture. That is the design working as
intended, not a limitation of it.

One flag looks like the judge's mistake rather than the pack's: `RET-59`'s ground truth
of `customer reason` is contested on the theory that a customer who refuses to give a
specific reason shouldn't get that label -- but reading this pack's own vocabulary
conventions, `customer reason` appears to be exactly the residual class for that
situation. The judge has no access to `VOCABULARIES.md`'s settled class-boundary
arbitrations; it re-derives boundaries from the transcript alone, and got at least one of
them wrong. That is now written down as a concrete, disclosed limitation rather than
discovered by whoever runs this next.

And the judge is not perfectly self-consistent, which is visible in the raw output rather
than hidden by it: one response's own rationale concludes *"the ground truth correctly
identifies... therefore the ground truth is correct,"* and then reports
`verdict: ground_truth_error` in the same response. Anyone using this tool needs to read
the rationale, not just the enum field -- and that instruction is now in both the module
docstring and the experiment write-up, not left implicit.

### Why Experiment 5B's data wasn't used

`out/` is gitignored by design (run logs carry model output verbatim), and Experiment 5B
executed in a different environment than this one. Only its safe, no-payload evidence
JSON is committed -- exactly enough to verify its claims (which the audit did), not
enough to re-adjudicate its individual items. Experiment 6 ran against Experiment 5A's
data because that is what is actually on this disk. Re-running the judge against 5B, the
more production-relevant reasoning-off comparison, is recorded as the first item in
Experiment 6's next-steps list, not silently substituted for.

## How Experiment 6 is different from Experiments 1 through 5B

Every experiment before this one measured **which model is better**. Experiment 6 is the
first to ask **whether the yardstick itself is right**, automatically, at scale, instead
of waiting for a human to notice by chance the way RET-11 was found. That is a different
kind of result: it doesn't move any net, doesn't change any verdict, and isn't trying to.
It is infrastructure this project didn't have before tonight -- a repeatable way to get a
second opinion on ground truth from a model that has no stake in either arm's ranking --
and it is now `evalgen judge`, a real CLI command with test coverage, not a one-off
script.

It's also the first experiment in this project whose own internal consistency (self-hash
provenance, deterministic prompt construction, architectural isolation from the verdict
path) is enforced by tests written specifically for it, rather than asserted in a
docstring and taken on trust -- closing a gap the audit found in the two diagnostics that
came before it.

## Current state

- Both suite modes green: **546 passed / 11 skipped** standalone, **557 passed / 0
  skipped** with `TRUE_SOURCE_ROOT` set (this environment; a fresh checkout is expected
  at 524 passed / 33 skipped, computed from 2026-08-06's 498 plus the 26 self-contained
  tests added tonight).
- `RECONCILED: NO`, unchanged. Nothing in this report is a migration verdict, and nothing
  the judge produced is either -- it is a second opinion about the yardstick, not a third
  vote on the migration.
- Full detail in `EXPERIMENTS.md`, Experiment 6. This document is the narrative and
  retrospective; that one is the pre-registered record.
