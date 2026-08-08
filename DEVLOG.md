# Development Log

## 🔴 Active Task

**Current focus (2026-08-08): turn Experiment 7 into a production-shaped, internal-GPU
decision.** The synthetic repeat itself completed successfully: all three arms returned
414/414 parse-valid calls, and the advisory judge completed 360 opinions with no
transport or identity failures. The decision remains to retain Gemini as the reference.
Qwen3.6 27B failed stability (129/138 calls changed exact answer across replicates);
Qwen3.6 35B-A3B failed all three paired quality gates and stability (130/138 unstable).
See `docs/experiment7-results.md` and
`experiments/evidence/retention-e7/summary.json`.

The active blocker has not changed: `RECONCILED: NO`. This repository still lacks an
approved production-shaped labelled batch, the two-row workbook header contract, and an
execution on the company GPU. Experiment 7 is mock-data screening evidence, not a
migration approval.

- [ ] Retention domain owners review the 38 Experiment 7 possible-ground-truth-error
      flags in the restricted judge bundle; commit decisions, never cited transcript text.
- [ ] Run the committed Experiment 7 application contract on the internal GPU runtime,
      preserving testset, prompt, schema, repeats and decision policy.
- [ ] Reconcile one approved real labelled batch against the application's existing
      Gemini fact-check report and record the discrepancy analysis.
- [ ] Implement `load_workbook()` only after the first two real workbook header rows arrive.

**Historical context retained below.** Experiment 5's Morph endpoint qualified at small
scale but returned 54 HTTP 429 failures and one empty response at full scale. Do not
rewrite the prompt, weaken the schema, or post-hoc retry those recorded rows; a new
retry/backoff or capacity regime must be preregistered as a new experiment.

**~~Blocking the Qwen candidate arm (2026-08-05).~~ RESOLVED the same day, before
Experiment 1 closed.** The arm was never blocked. One endpoint was broken, and the entry
below read that as a property of the model and of production's decoding regime. It is
kept in place and corrected, because the wrong inference is the useful part:

- **Stands.** `out/runs/20260804-224050Z-candidate` is **INVALID and must not be
  scored**: it was served by two backends under one model id. Backends are now pinnable
  (`--provider`) and a split is now visible (`prompt_token_spread`). The run stays on
  disk and stays unscored. It is also **unattributable**: its regime-A rows scored 21 of
  31 `ok` against 0 of 20 under the pin probe, but it recorded no `provider` field, so
  the difference cannot be assigned to a backend. That gap is exactly what the pin
  closes.
- **Stands.** Production runs `thinkingBudget: 0`
  (`config/model_setting/retention.yml`), a NON-REASONING regime. Pinned to **Alibaba**,
  20 of 20 items returned `schema_violation` (plus 9 of 9 on the 3x3 stability probe).
  Every one is a bare JSON *number literal* -- e.g. `-1.1000000000000001e-05` followed
  by ~500 digits -- where the schema's root type is `object`. `finish_reason: stop`, no
  truncation.
- **Wrong: that this said anything about the candidate.** It is a broken constrained
  decoder on one endpoint. The identical request returns a well-formed object from
  **Morph**, Chutes and CoreWeave. Run 1.4 re-baselined the candidate on Morph at
  **60/60 ok**, and across Experiment 1 schema violations went 10 -> 0.
- **Wrong: "of nine `qwen/qwen3.6-27b` endpoints, exactly one is non-reasoning".** The
  census missed Morph, which this entry never named and which has served the candidate
  arm ever since. Experiment 2's cost table records **no reasoning tokens on either
  arm**, so Morph runs the candidate in production's own `thinkingBudget: 0` regime --
  the regime this entry said was unobtainable.
- **Wrong: the "open decision, for a human".** It offered a choice between measuring the
  candidate in a reasoning regime production does not run and reporting that
  `qwen/qwen3.6-27b` has no usable non-reasoning endpoint for constrained decoding.
  Neither branch was real and nothing was waiting on a human. Experiments 2 and 3 then
  put 200 and 300 candidate calls through Morph and reached a verdict: **no accuracy
  case for migrating, and a reliability case against it**, every margin within 2 items
  (`EXPERIMENTS.md`, Experiment 3).

- [ ] Receive the **first two header rows** of the Retention ground-truth workbook
      (no data rows). This settles the two-row header layout that
      `adapters/retention.py::load_workbook` currently refuses to guess, and contains
      no customer record. Cheapest unblock available.
- [ ] Receive ground-truth **row counts and class distribution** per app. Blocks the
      sample design. **Partly overtaken (2026-08-05)**: a sample was designed and shipped
      without them, on synthetic data -- `retention_v2` is 100 items / 108 scored rows,
      with `retention_v1.*` frozen so Experiments 1-2 stay reproducible. The need did not
      go away. That pack holds v1's family proportions rather than production's
      (`docs/testset-v2-plan.md`), so it still cannot say whether a class that matters in
      production is under-tested here. That pack also consumed the synthetic phone block
      whole: all 100 numbers `08100000xx` could spell. The block was widened to
      `0810000xxx` on 2026-08-06 (`src/evalgen/testsets.py:135`) so the next item has a
      number to take -- 100 in use, 900 free, and no existing number moved.
- [ ] Receive the count of rows whose `phone_number` is null, blank or `0`. That
      number is the size of a blind spot in the current product metric.
- [ ] Implement `load_workbook()` once the header layout is known.

## 🟡 Roadmap

1. **Reconciliation run.** Score one real labelled batch and confirm the numbers match
   the app's existing Gemini fact-check report. Until this passes, every report is
   stamped `RECONCILED: NO` and no number is a migration verdict. This is the single
   most important outstanding item.
2. **MNP adapter.** Cheapest second app: same pure metric functions, and the label
   space differs by exactly one reason class, already declared in `labelspaces.py`.
3. ~~**Candidate arm wiring.**~~ **DONE (2026-08-08).** `src/evalgen/` calls every arm
   through one OpenAI-compatible client boundary and lands them in the same normalized
   record. `runtime.py` supports a reviewable internal-GPU manifest without changing
   scoring code, while `test_boundary.py` asserts `evalharness` never imports `evalgen`.
   **Not done:** an actual company-GPU execution. Follow `docs/TEAM_GPU_RUNBOOK.md`.
4. **Sentiment QA and Telesales adapters.** Hardest, and lowest information: their
   scorers hard-set `FN = TN = 0`, so recall is structurally 100% and three of four
   configured thresholds cannot fail.
5. **RTR.** Deferred: its scorer aligns ground truth and predictions **positionally**
   after independent sorts, so a single missing row silently misaligns everything after it.

## 🐛 Known Bugs

**Four remaining coverage/configuration gaps, refreshed 2026-08-08.** These do not
invalidate Experiment 7's recorded output, but they belong in the next harness-hardening
change before a production-data run:

- [ ] `cmd_qualify` (spends real API calls, decides QUALIFIED/INCOMPATIBLE) has zero test
      coverage anywhere in the suite. (Priority: High -- it is the gate between an
      unvetted provider and a paid qualification run. Needs a mocked client.)
- [ ] `cmd_experiment_run`'s three safety gates -- `--confirm-plan-sha` mismatch, an
      `UNAVAILABLE` arm, an out-of-list `--concurrency-level` -- are each only ever
      exercised with a value that passes. No test supplies a wrong sha, an unavailable
      arm, or a bad concurrency level, so a broken gate would not be caught.
      (Priority: High -- this is the human-approval gate stopping a stale or tampered
      plan from spending real, paid calls.)
- [ ] `manifest.workload_sha`'s forbidden-field guard and `_refuse_incomparable`'s
      era-mixing/mismatch checks for `outcome_contract_sha`/`workload_sha` are untested.
      (Priority: Med -- these are the checks stopping two genuinely incompatible runs
      from being silently compared.)
- [ ] `reliability_gate`'s 0.99 threshold is a hardcoded Python default, not read from
      the plan's own `quality_gates.minimum_parse_valid_rate` field that `validate_plan`
      computes and displays as authoritative. Currently harmless (both are 0.99).
      (Priority: Low today, real if a future plan sets a different value.)

**Two gate-logic gaps found by the same audit were fixed the same night** (both were
mechanical, well-scoped, and safe to verify before merging): `decision()` silently
passing a candidate whose stability comparison was UNDERPOWERED, and `validate_plan()`
never checking which arm's `role` was `incumbent`. See CHANGELOG.md, Fixed.

**Resolved earlier, kept for the record:**

- [x] ~~**The mechanism table stops discriminating as the pack grows, and at 100 items it
      carries no information.**~~ **RESOLVED (2026-08-06).** `MechanismRow` gained
      `always_correct` and a derived, non-monotone `rate` alongside the kept
      PASS/FLAKY/FAIL letter (`report.py`). The letter still saturates by construction --
      that is what made it worth keeping the rate beside it, not instead of it -- but the
      rate does not: one more correct item raises it, one more wrong item lowers it, so
      two all-FAIL rows still separate. `retention_v3`'s 9-family table (up from 5) is
      the first pack this was load-bearing for.
      <details><summary>Original entry, kept for the record</summary>

      Its verdict rule was FAIL if *any* item in a group failed on every replicate,
      which is monotone decreasing in group size: adding items can only push a row
      toward FAIL. At 20 items four of five rows read FAIL/FAIL and `multislot` (n=2)
      was the one row still separating the arms; at 100 items `multislot` grew to 10
      items and collapsed, and all five rows read FAIL/FAIL on both arms. Predicted in
      writing before the pack was built (`docs/eval-improvement-plan.md`, finding 1;
      `docs/testset-v2-plan.md`, caveat 1) and Experiment 3 confirmed it.
      </details>

**Production defects found while building, reproduced deliberately** (not bugs here,
but the reason some code looks odd):

- [ ] Calls with a null, blank or zero `phone_number` are dropped from the product
      dimension entirely, while still being scored in the other two. A class that was
      never evaluated reports `weight = 0, accuracy = 1.0000`. (Priority: Med. Ask the
      app team how many rows are affected before deciding whether to raise it.)
- [ ] An all-empty prediction set scores accuracy 0.8246 with recall 0.0000 on the
      fixture, because true negatives dominate. Distribution-dependent and higher on
      larger single-label sets. (Priority: High for interpretation. The harness does
      **not** inherit this: coverage refusal and recall-based gating exist for it.)
- [ ] Sentiment QA and Telesales hard-set `FN = 0, TN = 0`, making recall identically
      100% and precision identically equal to accuracy. (Priority: High. Affects the
      acceptance criteria on the table for the review, whatever this harness does.)

## ✅ History

- **2026-08-08**: Experiment 7 completed on synthetic Retention v3. Provider
  qualification covered 20 advertised endpoints (120 bounded calls); the selected
  Google/Chutes/AkashML arms then completed 1,242/1,242 parse-valid full calls. Gemini
  remains the reference: Qwen3.6 27B failed stability and Qwen3.6 35B-A3B failed quality
  plus stability. The independent Gemma judge completed 360 advisory opinions and
  flagged 38 possible ground-truth errors for human review. Generation, qualification
  and judge spend was an observed lower bound of approximately US$1.215310. Raw/private
  evidence remains ignored; safe aggregate handoff committed. Standalone pinned suite:
  649 passed, 33 skipped; tracked production-reference mode: 660 passed, 22 skipped.
- **2026-08-08**: Provider-neutral runtime manifests, portable self-contained run
  snapshots, crash-resume journals, application contracts, stricter artifact identity,
  call-clustered paired inference, shareable/private judge surfaces, and decision-grade
  completeness checks made the harness ready for an internal-GPU rerun without changing
  the scoring package.

- **2026-08-07**: Full audit of the merged enterprise framework (~2,500 lines, never
  before code-reviewed line by line) -- 21 agents, 12 candidate findings, all
  independently verified, zero refuted. Two real gate-logic gaps fixed (`decision()`
  silently passing an UNDERPOWERED stability comparison; `role` never validated), four
  test-coverage gaps recorded (see Known Bugs), two stale `EXPERIMENTS.md` line
  citations corrected, one `sys.path` bug in `test_enterprise_experiments.py` found by
  hand and fixed. Every numeric claim checked against committed evidence matched
  exactly -- no arithmetic was wrong anywhere the audit looked.
- **2026-08-07**: Experiment 6. `src/evalgen/judge.py` -- an independent model
  (`google/gemma-4-31b-it`, reasoning off, pinned to CoreWeave) adjudicates every scorer
  disagreement, diagnostic only, isolation from the verdict path enforced by an AST test
  rather than only claimed in a docstring. 262 items across three pairings, zero parse
  failures: 62.6% ground-truth-correct, 30.5% defensible, 6.9% flagged as a possible
  ground-truth error. Four flags cross-validated by all three independent comparisons;
  `RET-85` is the strongest candidate, same shape as the original RET-11 catch. Nothing
  changed on the strength of any flag. Full report:
  `docs/overnight-audit-and-experiment-6-report.md`.
- **2026-08-06**: Experiment 5A (parallel historical reasoning-regime run).
  `retention_v3` (138 items) scored on all three arms
  under the re-derived bands, 1,242 calls, ~$9.49. Both Qwen arms are **AHEAD** of the
  incumbent on `reason` at alpha=1/64 -- the first AHEAD verdict in this project without
  a repeat-pass caveat -- but it is bought entirely inside the reasoning-regime confound
  Experiment 4 found (2.3-2.6M reasoning tokens on both Qwen arms, zero on the
  incumbent), so it reads as "Qwen with reasoning beats Gemini with none," not "Qwen
  labels Thai better." `product` returned zero informative verdicts across all nine
  comparisons scored: every one landed `d < 6`. `long_context`, read on the
  always-correct metric, showed the incumbent failing consistently at 10x (3 `FAIL`
  items, 0 `FLAKY`) while both Qwen arms were only ever `FLAKY` at either dilation --
  correcting a premature mid-run read that length degrades labelling in general; it
  degrades this one model. The `scorer_sha`-invalidates-comparability defect from
  Experiment 4 recurred mid-run-sequence (a docs commit moved HEAD between arm launches)
  and this time cost a real re-run, not just a footnote.
- **2026-08-06**: Prerequisites for `retention_v3` -- verdict bands re-derived from the
  arithmetic alone (the old n=22 bands returned a directional verdict on two identical
  models 57% of the time by 108 rows), `MechanismRow` gained a non-monotone `rate`
  (resolves the Known Bug below), 16 new pack-validation tests plus a CI step closing the
  hole where `retention_v2` had zero automated checks, and the phone block widened
  `^08100000[0-9]{2}$` -> `^0810000[0-9]{3}$` (strict superset, zero fixture numbers
  moved, 100 in use / 900 free). Suite: 483 passed / 11 skipped standalone, 494 / 0
  differential.
- **2026-08-06**: `retention_v3` authored -- 138 items, the 100-item v2 pack
  byte-identical plus 38 new across four families the pack had zero coverage of before:
  `long_context` (dilated Experiment-3 items, 3x and 10x), `asr_noise` (ten artifact
  classes, hand-derived expectation written twice after the first version's own
  verification method turned out to have imported the code it was meant to check
  independently -- recorded as a process failure in `ASR-EXPECTATION.md` itself, not
  quietly redone), `code_switch`, and `regression`. A budget overrun against the
  pre-registered `+8 to 12`, recorded as one rather than argued away.
- **2026-08-05**: Experiment 4. Third arm, `qwen/qwen3.6-35b-a3b`: not viable, loses to
  the 27B on all three dimensions. The real finding is that re-running the 27B after
  Morph started returning HTTP 400 moved `reason` net **-1 -> +24** on an unchanged
  model id, prompt and pack, because the replacement endpoint (CoreWeave) reasons and
  Morph did not -- the pin is a term in the result, not a detail of the method.
- **2026-08-06**: Experiment 5B enterprise Gate 2 completed. Exactly 1,458 approved
  full/load calls produced both candidate `FAIL` decisions. Committed a self-hashed approval and
  execution ledger plus safe per-arm, paired and summary reports; raw response logs
  remain gitignored. Corrected the offline runtime gate so missing metadata on failed
  calls does not silently replace the 99% reliability rule with 100%; no paid call was
  rerun. The same raw logs generated byte-identical JSON/Markdown reports twice.
- **2026-08-06**: Experiment 5B enterprise framework pre-registered, with zero model
  calls. Added v3 dataset and prompt manifests, the machine plan, provider qualification
  taxonomy, explicit reasoning controls, one-attempt reliability, exact paired verdicts,
  workload/scoring/classification hashes, locked approval gates, load levels 1/4/8 and
  quality-first reports. Offline verification in the isolated worktree: 493 passed / 33
  skipped; 22 skips require deliberately unshared gitignored historical `out/`
  directories, and 11 are the documented production-source checks. Pointing
  `TRUE_SOURCE_ROOT` at the tracked production reference made the differential,
  requirement-pin and boundary selection pass 18/18.
- **2026-08-05**: Run index. `scripts/run_index.py` generates `RUNS.md` from `out/runs/`,
  a committed index of every run with the provenance needed to cite one: model, provider,
  prompt sha, decoding, outcomes, pin proof, cost. `out/` is gitignored because run
  artifacts carry model output verbatim, so until now the whole run history was invisible
  to git and every report cited runs no reviewer could see. 19 runs recorded, 20 tests.
  The suite stands at **451 passed / 11 skipped** standalone and **462 passed / 0
  skipped** with `TRUE_SOURCE_ROOT` set.
- **2026-08-05**: Experiment 3. 100 items, 108 scored rows, 600 calls. **The candidate's
  `reason` lead was noise, and it reversed**: net went from +5/+6 at 22 rows to **-1** at
  108, and every margin is now within 2 items. The stated prediction that would have
  distinguished signal from noise came out on the noise side. Verdict: no accuracy case
  for migrating, and a reliability case against it. The 22-row figures from Experiments
  1-2 are superseded and are not to be quoted again.
- **2026-08-05**: `retention_v2` test set: 100 items / 108 rows, with `retention_v1.*`
  frozen so Experiments 1-2 stay reproducible. `export_xlsx.py` gained a **Side by side**
  sheet, added to `EXPECTED_SHEETS` so its presence and position are verified with the
  rest.
- **2026-08-05**: Experiment 2. Five replicates on both arms, 400 calls. **The candidate
  is nondeterministic at temperature 0 and the incumbent is not** -- `N_flip` 8 against
  0 on base, over 200 byte-identical calls per arm. `reason` net on `e1` came out **+6**,
  crossing the pre-registered AHEAD band, and was **not** called AHEAD: the aggregate
  table is scored on replicate 1 alone (`cli.py:25-31`), so `+6` is one draw from the arm
  that flips, and the same measurement has now produced +5, +4 and +6. Both arms were
  raised, not just the candidate, because unequal replicate counts give the two arms
  unequal chances to show instability (`report.py:635-639`, the `_header` warning;
  `EXPERIMENTS.md:245` cites `573-577` for the same warning and that line number is
  wrong -- it lands in `render()`'s docstring about section order, and did so when it
  was written).
- **2026-08-05**: Phase 1 of the post-Experiment-1 eval plan. RET-11's ground truth
  corrected -- it gained `dissatisfied service`, which moved `test_fabrication.py`'s
  hand-checked literals from 42/18 and 30/19 to **39/15 and 29/18**, counts updated with
  the change rather than the reverse. Both open class boundaries arbitrated in
  `tests/fixtures/testsets/VOCABULARIES.md`. `src/evalgen/evidence.py` added as a
  deterministic diagnostic that is **never a scored dimension**, feeds no verdict and
  ranks no arm, with its hand-computed expectation written before the implementation --
  the direct lesson of the retracted `keyword` metric. `report.py` gained section 6
  (cost, tokens, latency), placed after the aggregates rather than before them.
- **2026-08-05**: External benchmark comparison, Qwen3.6 27B against Gemini 2.5 Flash,
  benchmark by benchmark (`docs/model-comparison-qwen-vs-gemini.md`). Reference only, and
  it disagrees with this repository's measurements for a stated reason: the public index
  scores the candidate in its reasoning configuration, which production does not run.
- **2026-08-05**: `source-code-review/` renamed `production-reference/` and now
  **tracked**, reversing the earlier decision to block it, with `.gitignore` hardened
  against production source and stray workbooks in the same pass. `.gitattributes` now
  forces LF, after `core.autocrlf` had silently corrupted a fixture.
- **2026-08-05**: `src/evalgen/` added -- the OpenRouter model-calling pipeline, and the
  point at which this repository started running models rather than only scoring them:
  cli, runner, client, request, outcomes, flatten, prompts, decoding, report, fabrication,
  testsets, config, console. Kept out of `src/evalharness/` so the scoring library still
  imports no networking library, with `test_boundary.py` parsing the AST to assert it.
- **2026-08-04**: Version pin gate fixed to survive extraction. It had located
  production's `requirements.txt` by a hardcoded relative path, so the one gate this
  build made a point of demonstrating silently stopped running once the repo moved.
  Standalone went from 78 passed / 15 skipped with nothing enforcing the pins, to 82
  passed / 11 skipped with the gate among the passing.
- **2026-08-04**: Extracted to its own repository in 14 commits, `.gitignore` first
  and alone, fixtures committed before the metric code they check.
- **2026-08-04**: README and data contract written. The data contract asks for the
  smallest set of data that makes a defensible comparison possible, with a reason
  attached to every item.
- **2026-08-04**: Version pin gate added and demonstrated **failing** under a
  mismatched interpreter before being trusted.
- **2026-08-04**: Runtime data-directory refusals. `EVAL_HARNESS_DATA_DIR` has no
  default and must resolve outside any git worktree.
- **2026-08-04**: Run manifest split into blocking and recorded fields, after an
  earlier draft blocked on decoding-config equality that is unsatisfiable across
  backends and would have been bypassed on every real run.
- **2026-08-04**: Paired comparison, 2x2 disagreement table, coverage refusal, HMAC
  item keys and the PII guard.
- **2026-08-04**: Differential test against True's real production scorer, reaching it
  without cloud credentials via stub environment and `object.__new__`.
- **2026-08-04**: Three scorers with three denominators, plus the adapter that refuses
  to guess the workbook layout.
- **2026-08-04**: Hand-computed fixture pack committed **before** the metric code, so
  the discipline lives in the history rather than only in a README claim.
