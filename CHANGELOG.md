# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Canon-aligned documentation set: `AGENTS.md`, `CLAUDE.md`, `DEVLOG.md`,
  `TESTING.md`, `CONTRIBUTING.md`, and this changelog.
- CI workflow running the test suite on push and pull request.
- **`--provider NAME` pins an arm to one OpenRouter backend**, sending
  `{"order": [NAME], "allow_fallbacks": false, "require_parameters": true}` and
  recording it in `run.json` as `provider_requested`. `allow_fallbacks: false` is the
  load-bearing key: `order` alone is a preference the router abandons whenever the
  named endpoint is busy, and a run that fell through looks identical in the log.
- **`RunResult.prompt_token_spread` / `split_items`**, the check that an arm was one
  backend. Every replicate of an item sends a byte-identical request, so
  `prompt_tokens` is a pure function of the backend's tokenizer and two values means
  two builds. This is the evidence a pin held; the `provider` field is the router
  describing its own routing and cannot serve as its own verification.
- `observed_providers` histogram, in `run.json` and on the console, beside
  `observed_models`.
- **`src/evalgen/evidence.py`, a diagnostic that cannot become a verdict.**
  Deterministic, no network, imported by nothing and absent from `render()`'s verdict
  path. It reports `keyword` verbatim rates at two grains, whole-field and comma-split,
  because the two answers disagree and the disagreement is the finding. `fact_checker.py`
  never reads `keyword`, so this is not and must not become a fourth scored dimension.
- **Performance section in the text report** -- section 6, cost, tokens and latency,
  placed after the aggregates. Cost is labelled a lower bound and carries the count of
  rows that reported none. TTFT is deliberately absent: the client is non-streaming.
- **`retention_v2` test set: 100 items, 108 scored rows.** Its first 20 lines are v1's
  verbatim, and `retention_v1.jsonl` / `retention_v1.gt.csv` are frozen byte-identical so
  Experiments 1 and 2 stay reproducible. Reason-class support goes from six classes at
  n=1 to a minimum of 6 each, which is what stopped per-class metrics separating two
  models. The synthetic phone block is now exhausted: `^08100000[0-9]{2}$` admits exactly
  100 numbers and all 100 are used, so a 101st item is a reviewed change to one of the
  identifier controls. Objections to the expansion, and four known limitations, are kept
  in `docs/testset-v2-plan.md` rather than discarded.
- **"Side by side" sheet in `scripts/export_xlsx.py`**: one row per item, every arm's
  whole answer against the ground truth, OK/MISMATCH per arm and a flips marker.
  Registered in `EXPECTED_SHEETS`, so the export's own verifier now requires it. Scoring
  is whole-item all-or-nothing and that caveat is written on the sheet itself, so it
  travels with the file.
- **`scripts/run_index.py` generates `RUNS.md`, an index of every run.** `out/` is
  gitignored on purpose, so every `run_id` cited in `EXPERIMENTS.md` pointed into a
  directory a reader who clones this repository does not have. The index reads `run.json`
  provenance only and never `run.jsonl`, where the model's text lives, which is why it
  can be committed. `--check` exits 1 when it is stale, so the index is verified rather
  than trusted, and dry runs are footnoted rather than silently dropped. 20 tests.
- **`production-reference/`, True's Gemini-calling production source across four apps
  (603 files), is now tracked on purpose.** It is what every `rule_*` citation in
  `tests/fixtures/testsets/` and every `file:line` reference in `src/evalgen/` resolves
  against, so committing it turns those citations into something a reviewer can open.
  Scanned before commit: no `.env`/`.pem`/`.key`/credentials files, no live-API-key-shaped
  strings, no nested `.git`. This reverses the block added the day before (see Security),
  and the repository stays private because it now holds this.
- **Experiment 2: five replicates on both arms** -- 400 calls, $0.4507, pin held 20/20.
  Raised on both arms rather than the candidate only, because `report.py` warns on
  unequal replicate counts. The finding: the candidate is nondeterministic at temperature
  0 and the incumbent is not, N_flip 0 vs 8 and 0 vs 4 over 200 calls per arm on
  byte-identical requests. Three replicates had shown 0 on both. `reason` net on e1 came
  out +6 against a pre-registered `>= +6` AHEAD band and is recorded as still
  INDISTINGUISHABLE, with the three reasons written out. Narrative in `EXPERIMENTS.md`.
- **Experiment 3: the same comparison over 100 items** -- 600 calls, $0.7254, both arms
  300/300 ok, pin held 100/100, no parse failures either side. The finding: the
  candidate's `reason` advantage does not survive five times the data. At 22 rows it read
  +3, +5, +4, +6 across four passes; at 108 rows it is -1, and weighted F1 puts the
  incumbent ahead on that dimension, 0.787 vs 0.759. Narrative in `EXPERIMENTS.md`.
- **`docs/model-comparison-qwen-vs-gemini.md`**, with a pandoc `.docx` of identical
  content: what Artificial Analysis and the published model cards say about the two arms,
  retrieved 2026-08-05, plus a benchmark-by-benchmark table. Recorded together with the
  reasons it should not decide the migration -- no component of either source tests Thai,
  and AA compares Qwen in reasoning mode against Gemini in non-reasoning mode while
  production runs `thinkingBudget: 0`.

### Changed

- **The suite moved 82 -> 451 passed standalone across this work**, 11 skipped
  unchanged; 462 passed / 0 skipped with `TRUE_SOURCE_ROOT` set. Both numbers are from a
  run, not copied from another document -- several documents in this set were carrying
  counts stale by hundreds of tests.
- **`source-code-review/` renamed `production-reference/`.** The old name read as a
  temporary review copy, which it deliberately is not; `git mv` preserved history on all
  603 files. Seven files naming the old path were updated, two of which (`prompts.py`
  and `prompts/PORT-NOTES.md`) carried a now-false "gitignored and absent from CI" claim
  about the extracted prompt assets, rewritten to the reason that still holds: they are a
  reviewed, sha256-pinned snapshot, not a live read. The default `TRUE_SOURCE_ROOT` is
  unchanged and still resolves outside the repo, so standalone runs keep skipping the
  differential tests as documented.

### Deprecated

### Removed

### Fixed

- **An arm could be served by two backends with nothing in the log to show it.**
  MEASURED: a 60-call `qwen/qwen3.6-27b` run split across two builds -- one with
  `reasoning_tokens=0`, 2538-2643 prompt tokens, a 5.8s median and 10 of 31 rows in
  `schema_violation`; the other with `reasoning_tokens>0`, 3583-3931 prompt tokens, a
  71.7s median and 0 of 29. `observed_models` reported `60 x qwen/qwen3.6-27b` and the
  split was invisible, because the guard watched `response.model` and the model id was
  never what changed. 14 of 20 items returned two distinct `prompt_tokens` values for
  a byte-identical request; the incumbent returned 0 of 20.
- **`raw_content`, `finish_reason`, `generation_id` and `provider` are now persisted
  per row.** `client.Completion.raw` documented these as "still recoverable from the
  run log"; they were not -- `runner._result_from_completion` never touched `raw`, so
  nothing in it ever reached disk. The cost was measured: all ten `schema_violation`
  rows of the candidate run were undiagnosable, because the model's own text was
  discarded at exactly the boundary where the harness decided it was wrong. The
  docstring that claimed otherwise is corrected rather than deleted.
- **The keyword-fabrication claim was built, measured and RETRACTED.** `EXPERIMENTS.md`
  called Gemini's `keyword` field "a comma-stitched fabrication that does not appear
  verbatim" and ranked acting on it as a next step. It is false. Both
  `schemas/retention.json:35` and production `main.py:977` end that field's description
  with "Use comma separation", sent to both arms on every call; comma-split, all 63 of
  Gemini's segments are byte-exact. The incumbent was complying with an instruction this
  harness sent it. Retracted in place rather than deleted, per the convention in
  `client.py:77-87`. The real asymmetry runs the other way -- Qwen emits 0 commas across
  257 `keyword` fields -- and is recorded as a diagnostic, never a scored dimension.
- **RET-11's ground truth was missing a label**, and gains `dissatisfied service`:
  `prompt.py:4361` licenses that class by the example "the agent didn't follow up", and
  RET-11's customer says as much ("ไม่มีใครตามเรื่องเลย") on a `ลูกค้า:` line, satisfying
  `prompt.py:4384`'s customer-speech requirement. `transcript_th` is byte-identical; only ground truth,
  evidence and rules changed. `test_fabrication.py`'s expectations moved 42/18 -> 39/15
  and 30/19 -> 29/18 as a consequence. That is the shape of the foremost prohibition, so
  it was checked rather than trusted: the UNCHANGED fabrication code run against the OLD
  ground truth still reproduces the OLD numbers exactly, so the numbers moved because the
  ground truth was corrected, not because an expectation was fitted to output.
- **`core.autocrlf` silently corrupted a committed fixture.**
  `tests/fixtures/testsets/retention_v1.jsonl` picked up CRLF in the working tree during a
  merge, with no `.gitattributes` to override it. The committed blob was untouched and
  git's own autocrlf-aware diff showed nothing, but `load_testset` refused the on-disk
  file -- correctly, since a CR inside `transcript_th` would silently break every verbatim
  evidence-span match. `.gitattributes` now forces `eol=lf` on all detected text and
  exempts real binaries (png, xlsx, pdf) from any conversion, which the tracked
  production source carries.

### Security

- **`.gitignore` hardened.** Workbooks are blocked by blanket `*.xlsx` / `*.xlsm` rather
  than the old name-matched `ground_truth*.xlsx`: a real ground-truth workbook saved under
  any other filename outside `data/` or `out/` would have slipped through. Nothing tracked
  matches either pattern apart from the scoped `production-reference/**/*.xlsx` exception
  added with that tree. `.claude/` is ignored wholesale, the same treatment this file
  already gave `.idea/` and `.vscode/`, as are Windows `Zone.Identifier` markers.
- **A 635-file, 15MB accidental copy of True's production source was blocked, then
  deliberately re-admitted.** A workflow agent copied it into the repository on
  2026-08-04 trying to satisfy the differential test; it did not even work, because
  `production_ref.py` resolves `TRUE_SOURCE_ROOT` to a sibling directory and the test
  skipped regardless. The block is recorded rather than dropped: it was reversed by owner
  decision on 2026-08-05, after a credential scan, and the tree is now tracked as
  `production-reference/` (see Added).

---

## [0.1.0] - 2026-08-04

First working harness. Retention app only, verified three independent ways, with no
real data and no model calls.

### Added

- **Three scorers, three denominators.** `call_result` (after dropping rows with no
  ground truth), `reason` (no drop at all), and `product` (call-grain groups, NaN
  phone keys dropped) score over different row sets, mirroring production exactly.
  On the fixture that is 10, 11 and 9 rows.
- **Hand-computed fixture pack, committed before the metric code it checks.** 23
  exact integer expectations across three dimensions, derived on paper in
  `WORKED-COMPUTATION.md`, with ten documented cases in `CASES.md`.
- **Differential test against True's real production scorer.** Imports the actual
  `FactCheckerModule` without cloud credentials and asserts agreement class by class.
  A SHA-256 pin over the scored source region fails loudly on upstream drift.
- **Paired comparison and 2x2 disagreement table**, whose counts must sum to the
  scorable item count, plus a per-item regression list, which is the artifact a
  review actually reads.
- **Coverage accounting on every dimension**, and a comparison that refuses when two
  arms did not score the same items. An arm whose unparseable output was dropped has
  an easier denominator, so accuracy would favour the arm that failed more often.
- **Run manifest split into blocking and recorded fields.** Same items, labels and
  scorer must match; backend, model and decoding config are expected to differ and
  print as a delta. Cross-arm decoding equality is unsatisfiable, since vLLM has
  neither a thinking budget nor forced function calling.
- **`RECONCILED: NO` provenance stamp** on every report until a run is checked
  against the app's live fact-check report.
- **HMAC item keys** and a guard that raises on any customer column reaching a
  shareable artifact. `phone_number` is hashed rather than dropped, because it is
  part of both the merge key and the product groupby key.
- **Runtime refusal of unsafe data directories**: `EVAL_HARNESS_DATA_DIR` has no
  default and must resolve outside any git worktree.
- **Version pin gate**, demonstrated failing under a mismatched interpreter before
  being trusted.
- Retention adapter whose `load_workbook()` raises rather than guessing an unseen
  two-row header layout.
- `docs/data-contract.md`: what to request from the app team, with a reason attached
  to every item.

### Fixed

- Version pin gate no longer skips permanently once the harness is extracted to its
  own repository. It previously located production's `requirements.txt` by a
  hardcoded relative path. Split so the installed-versus-pinned check always runs and
  only the cross-check against production skips.

### Security

- Deny-by-default `.gitignore` committed before any other file, ignoring `data/`,
  `out/` and `build/` as directories rather than by extension, because a pandas
  pipeline emits `.parquet`, `.jsonl` and `.ipynb` as readily as `.csv` and notebook
  cell outputs embed real rows. No negation exceptions.
- No customer data has ever been committed. Fixture phone numbers are the synthetic
  `08100000xx` range.

### Known limitations

- **Not yet reconciled** against Retention's live Gemini fact-check report. Until it
  is, no number here is a migration verdict.
- Retention only. MNP, RTR, Sentiment QA and Telesales adapters are not built.
- The ground-truth workbook layout is unverified; `load_workbook()` refuses by design.
- No ASR or transcript-quality scoring, which is gated on a decision that is still open.

[Unreleased]: https://github.com/tkhongsap/model-eval-harness/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/tkhongsap/model-eval-harness/releases/tag/v0.1.0
