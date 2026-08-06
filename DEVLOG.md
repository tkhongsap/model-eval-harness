# Development Log

## 🔴 Active Task

**Current Focus**: Waiting on real ground-truth data. Nothing in the harness is
blocked on code -- `src/evalgen/` calls both arms end to end, and Experiments 1, 2 and 3
are run and written up in `EXPERIMENTS.md`. `RECONCILED: NO` is what stands between
those numbers and a migration verdict, and no code in this repository can lift it.

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
3. ~~**Candidate arm wiring.**~~ **DONE (2026-08-05).** `src/evalgen/` calls both arms
   through OpenRouter and lands them in the same normalized record. No new scoring code
   was written, as designed, and `test_boundary.py` asserts `evalharness` never imports
   `evalgen`. Three experiments have run on it. **Not done**: a genuinely self-hosted
   endpoint. `client.py:33` fixes the base URL to OpenRouter, so pointing an arm at a
   self-hosted server is a deliberate edit, not a flag.
4. **Sentiment QA and Telesales adapters.** Hardest, and lowest information: their
   scorers hard-set `FN = TN = 0`, so recall is structurally 100% and three of four
   configured thresholds cannot fail.
5. **RTR.** Deferred: its scorer aligns ground truth and predictions **positionally**
   after independent sorts, so a single missing row silently misaligns everything after it.

## 🐛 Known Bugs

**One known limitation in this repository.** This section read "None in this repository"
until 2026-08-05, when Experiment 3 produced one:

- [ ] **The mechanism table stops discriminating as the pack grows, and at 100 items it
      carries no information.** Its verdict rule is FAIL if *any* item in a group fails
      on every replicate, which is monotone decreasing in group size: adding items can
      only push a row toward FAIL. At 20 items four of five rows read FAIL/FAIL and
      `multislot` (n=2) was the one row still separating the arms; at 100 items
      `multislot` grew to 10 items and collapsed, and all five rows now read FAIL/FAIL on
      both arms. It was predicted in writing before the pack was built
      (`docs/eval-improvement-plan.md`, finding 1; `docs/testset-v2-plan.md`, caveat 1)
      and Experiment 3 confirmed it. **Restoring it needs a different verdict rule, not
      more items** -- a group-level rule monotone in group size cannot survive growth, so
      this is an argument to have, not a patch to apply. (Priority: High. It is the
      report's designed headline, section 1. It moves no number: `render()` builds the
      mechanism section from `mechanism_table` and the per-dimension aggregates from
      `ArmSummary`, separately.)

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
