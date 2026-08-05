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

### Changed

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

### Security

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
