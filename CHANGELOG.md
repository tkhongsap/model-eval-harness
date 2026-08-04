# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Canon-aligned documentation set: `AGENTS.md`, `CLAUDE.md`, `DEVLOG.md`,
  `TESTING.md`, `CONTRIBUTING.md`, and this changelog.
- CI workflow running the test suite on push and pull request.

### Changed

### Deprecated

### Removed

### Fixed

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
