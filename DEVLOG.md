# Development Log

## 🔴 Active Task

**Current Focus**: Waiting on real ground-truth data. Nothing in the harness is
blocked on code.

**Blocking the Qwen candidate arm (2026-08-05).** `out/runs/20260804-224050Z-candidate`
is **INVALID and must not be scored**: it was served by two backends under one model
id. Backends are now pinnable (`--provider`) and a split is now visible
(`prompt_token_spread`), but the pin forces a choice the harness cannot make for
itself, and the choice that matches production is the one that currently fails:

- Production runs `thinkingBudget: 0` (`config/model_setting/retention.yml`), a
  NON-REASONING regime. Of nine `qwen/qwen3.6-27b` endpoints, exactly one is
  non-reasoning: **Alibaba** (`reasoning_tokens=0`, ~2587 prompt tokens on RET-01,
  7-11s). The other eligible backends (Chutes, DeepInfra, CoreWeave) all reason and
  all report 3691 prompt tokens on the same bytes.
- **Pinned to Alibaba, 20 of 20 items returned `schema_violation`** (plus 9 of 9 on the
  3x3 stability probe). Every one is a bare JSON *number literal* -- e.g.
  `-1.1000000000000001e-05` followed by ~500 digits -- where the schema's root type is
  `object`. `finish_reason: stop`, no truncation. That is a broken constrained decoder
  on that endpoint, not a model failing a hard task: the identical request returns a
  well-formed object from Chutes, DeepInfra and CoreWeave.
- **Open decision, for a human**: measure the candidate in a reasoning regime that
  production does not run (an upper bound nobody can deploy, at ~45-75s per call
  against production's ~7s), or report that `qwen/qwen3.6-27b` has no usable
  non-reasoning endpoint on OpenRouter for constrained decoding. Both are defensible;
  they are different questions and the harness must not pick one silently.
- Note the earlier run is now **unattributable**: its regime-A rows scored 21 of 31
  `ok`, against 0 of 20 today, and it recorded no `provider` field, so the difference
  cannot be assigned to a backend. That gap is exactly what this change closes.

- [ ] Receive the **first two header rows** of the Retention ground-truth workbook
      (no data rows). This settles the two-row header layout that
      `adapters/retention.py::load_workbook` currently refuses to guess, and contains
      no customer record. Cheapest unblock available.
- [ ] Receive ground-truth **row counts and class distribution** per app. Blocks the
      sample design.
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
3. **Candidate arm wiring.** Accept self-hosted model outputs through the same
   normalized record. No new scoring code, by design.
4. **Sentiment QA and Telesales adapters.** Hardest, and lowest information: their
   scorers hard-set `FN = TN = 0`, so recall is structurally 100% and three of four
   configured thresholds cannot fail.
5. **RTR.** Deferred: its scorer aligns ground truth and predictions **positionally**
   after independent sorts, so a single missing row silently misaligns everything after it.

## 🐛 Known Bugs

None in this repository.

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
