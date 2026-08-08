# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- **Experiment 7 reproducible handoff.** Added a machine-validatable, zero-call
  Retention v3 reproduction plan, a synthetic aggregate evidence record, and a detailed
  team handoff covering three-model F1, paired quality/stability gates, operations,
  token/cost accounting, the 360-opinion advisory judge, limitations and next gates.
  The executed locked plan remains with ignored runtime evidence; its SHA is recorded.
  Run independently of Experiment 5B, on a different provider pin for the 27B arm
  (Chutes, not Morph or CoreWeave), and reached the same decision: retain Gemini.
- **Canon-aligned repository governance.** Added CODEOWNERS and a project-specific pull
  request template with evidence, privacy, decision-grain and reconciliation checks.
- **`docs/migration-decision-2026-08-07.md`: the migration recommendation, written
  down.** Synthesizes Experiments 1-6 into one answer to the question this whole
  project exists to decide: **do not migrate** to `qwen/qwen3.6-27b` or
  `qwen/qwen3.6-35b-a3b`. Every apparent Qwen advantage measured across this project's
  history traces to the reasoning-regime confound Experiment 4 found; with reasoning
  off (Experiment 5B, production's actual `thinkingBudget: 0` regime), both candidates
  **FAIL** a pre-registered decision rule. Experiment 6's independent-judge review of
  the ground truth (6.9% of 262 disagreements flagged) does not change this -- nine
  possibly-wrong labels on one synthetic pack cannot outweigh a result reproduced
  across four experiments and two candidate models. Explicitly a recommendation, not
  `RECONCILED: YES`; no code path in this repository prints that, and the memo does
  not either. Independently corroborated the next day by Experiment 7 (above).
- **Committed Experiment 5 execution and decision evidence.** Gate 2 ran the three
  full arms and nine fixed load profiles: exactly 1,458 approved one-attempt calls for
  a US$1.507460937 reported-cost lower bound. A self-hashed ledger records raw-log
  identities without committing model text; safe per-arm, paired and summary
  JSON/Markdown/XLSX reports retain both Qwen `FAIL` decisions and item regressions.
- **Committed Experiment 5 qualification evidence.** Gate 1 exercised all 18 named
  providers in 108 one-attempt calls, retained every outcome as a safe self-hashed
  artifact, selected Google/Morph/AkashML by historical continuity, and locked the
  plan while leaving full/load approval pending. `qualification-report` deterministically
  reclassifies recorded qualification rows without a key or model call.

- **Experiment 5 enterprise evaluation workflow.** A machine-checkable draft plan pins
  Retention v3, three arms, fixed prompt, explicit reasoning-off regime, three
  replicates, one attempt, exact paired decision rule, 99% reliability gate, and the
  fixed concurrency 1/4/8 load slice. `experiment-check`, `qualify`, `experiment-run`
  and `experiment-report` enforce draft/qualification/lock approval stages;
  `experiment-budget` calculates the no-network current-inventory cost ceiling.
- **Provider qualification evidence instead of provider blacklists.** Six-call probes
  classify request, schema, regime, identity and provenance incompatibility. A repeated
  Morph 400 and Alibaba scalar JSON have distinct, reviewable outcomes; neither triggers
  a prompt-layout workaround or schema weakening.
- **Versioned enterprise assets.** `retention_v3.manifest.json` describes phase-one and
  robustness slices, while the prompt manifest records prompt version, parent, target
  models, SHA, decoding regime and the controlled phase-two tuning protocol.
- **Quality-first experiment reports** in JSON, Markdown and XLSX, with phase-one,
  phase-two and full paired verdicts, item-level replicate stability, parse reliability,
  missing-cost counts and concurrency load results.
- **Exact discordance-calibrated paired verdicts:** `AHEAD`, `BEHIND`,
  `INDISTINGUISHABLE`, and `UNDERPOWERED`, recomputed per dimension from observed
  discordant pairs at alpha 1/64 per side.
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
  models. It also spent the synthetic phone block: `^08100000[0-9]{2}$` admits exactly 100
  numbers and all 100 went into this pack, which is what forced the widening recorded
  under Security below. Objections to the expansion, and four known limitations, are kept
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
- **Experiment 4: a third arm, `qwen/qwen3.6-35b-a3b`, and the discovery that the
  endpoint moves the answer more than the model does.** Not viable: loses to the 27B on
  all three dimensions, loses to the incumbent on two, is 31x less stable (`N_flip` 62 vs
  2), 15x slower, 4x dearer despite a cheaper token price. The headline finding is about
  the harness, not either model: re-running the 27B after Morph started returning HTTP
  400 (it had served every prior experiment) moved `reason` net from **-1 to +24**, same
  model id, prompt and pack, because CoreWeave serves it in a reasoning regime and Morph
  did not. Production runs `thinkingBudget: 0`; the `+24` describes a regime it does not
  deploy. Two harness defects recorded, not patched: `prompt_token_spread` false-positives
  on a failed row with no usage (`0` treated as a token count), and `scorer_sha` is repo
  HEAD, so a docs-only commit invalidates a comparison already in flight. Narrative in
  `EXPERIMENTS.md`.
- **`tests/test_testset_pack.py`, 16 tests, plus a CI step running `evalgen check` on
  every tracked pack.** No pytest test loaded `retention_v2.jsonl` and CI ran only
  `pytest`, so the 100-item pack Experiments 3 and 4 were scored on had zero automated
  validation. Asserts span uniqueness (`transcript.count(span) == 1`, with RET-85's known
  v2 violation on a **dated** allowlist compared in both directions, so a fix or a new
  violation both fail loudly), customer-speech-only evidence, the 120-char turn cap
  (asserted nowhere in code before this), label-space coverage floors, identifier
  uniqueness, and the v1->v2 prefix-sha invariant.
- **`retention_v3` test set: 138 items, four new families.** The first 100 lines are
  `retention_v2` byte-identical (asserted invariant, sha `9c91b036...`); 38 new items add
  `long_context` (12: six Experiment-3 items both arms got right on every replicate,
  each dilated to 3x and 10x by inserting label-inert filler screened against the
  reason-trigger vocabulary at `prompt.py:4330-4381` -- every licensing turn stays
  byte-identical, so every `ev_*` span and `rule_*` citation carries over free), `asr_noise`
  (10: one artifact class per item -- tone-mark loss, missing spaces, Thai/Arabic
  numerals, proper-noun mangling, homophones, stutter, mid-turn truncation, speaker-label
  leakage, plus RET-11's two artifacts as controls), `code_switch` (10: English
  product/package terms mid-Thai at varying density), and `regression` (6: tripwires
  including the RET-11 shape, two `other` routes that do not depend on a frozen byte, and
  the discount boundary now unblocked by `VOCABULARIES.md` Rule A). Identifiers
  `RET-101`-`RET-138`, `call_id = 5000+n`, `phone = "0810000" + f"{n:03d}"`, asserted as
  an invariant. A budget overrun against the pre-registered `+8 to 12` in
  `docs/eval-improvement-plan.md:158`, recorded as one: the different sizing rule (four
  uncovered dimensions, not more depth on covered ones) is written down before authoring,
  not argued after the fact.
- **`tests/fixtures/testsets/ASR-EXPECTATION.md`**, the hand-derived expectation for the
  ten `asr_noise` classes, read directly from committed Unicode codepoints with nothing
  from `src/evalgen/evidence.py` imported or run. Written twice: the first version was
  produced by a scratch script that, despite its own docstring claiming otherwise, had
  imported and run `asr_normalise`/`ASR_SUBSTITUTIONS` from the module it existed to
  check independently, and was lost in an over-broad cleanup before the defect was found.
  Recorded as a process failure in the document itself, not quietly redone: an assertion
  that a number is hand-derived is not evidence that it is.
- **Experiment 5: `retention_v3` under the re-derived bands.** Both Qwen arms are
  **AHEAD** of the incumbent on `reason` at alpha=1/64 (Qwen27B net +26/±16 at d=40,
  Qwen35B net +17/±15 at d=41) -- the first dimension in this project to clear an AHEAD
  band without a repeat-pass caveat, and still entirely inside the reasoning-regime
  confound Experiment 4 found (both Qwen arms spent 2.3-2.6M reasoning tokens; the
  incumbent spent none). `call_result` separates the two Qwen arms (Qwen35B **BEHIND**
  Qwen27B, net -9/±9) but cannot separate either from the incumbent. `product` returned
  zero informative verdicts across all nine cells scored -- every comparison landed
  `d < 6`. The `long_context` family, read on the always-correct-across-3-replicates
  metric, shows the incumbent failing consistently at 10x (3 items wrong on every
  replicate, 0 at 3x) while both Qwen arms are merely flaky at either length (0 `FAIL`
  items, `FLAKY` only) -- a real difference in kind, correcting a premature live read
  ("context length degrades labelling") given after only the incumbent's arm had
  finished. The `scorer_sha`-invalidates-comparability defect from Experiment 4 fired a
  second time, mid-run-sequence, and cost a real re-run rather than only a footnote this
  time. Narrative in `EXPERIMENTS.md`.
- **`src/evalgen/judge.py`: an independent model adjudicates scorer disagreements.**
  Diagnostic only, matching `evidence.py`'s "never a scored dimension" constraint but
  enforced rather than only claimed: `tests/test_judge.py` parses the AST of `report.py`
  and `evalharness/compare.py` and fails if either ever imports `judge`, which is a
  stronger guarantee than either existing diagnostic carries today. Hand-computed
  expectation (`tests/fixtures/judge/HAND-COMPUTED.md`) fixes ten constructed raw
  responses and their exact aggregation, written before the module, per this project's
  standing rule for every new metric. 20 tests. Wired into the CLI as `evalgen judge`.
  Judge model `google/gemma-4-31b-it`, reasoning disabled, pinned to CoreWeave after a
  probe found the identical request returns different verdicts from CoreWeave/Novita
  versus DeepInfra -- the same endpoint-changes-the-answer lesson Experiment 4 taught
  about the primary arms.
- **Experiment 6: the judge's first real run, 262 items across three pairings, zero
  parse failures.** 62.6% `ground_truth_correct`, 30.5% `defensible_disagreement`, 6.9%
  `ground_truth_error`. Four flags reached independently by all three pairings:
  `RET-85`, `RET-94`, `RET-100` (`call_result`) and `RET-59` (`reason`). `RET-85` is the
  strongest candidate on inspection -- ground truth `save` against a call where two of
  three services are cancelled outright -- the same shape of catch RET-11 was. Nothing
  was changed on the strength of any flag; that is the point of a diagnostic. Also
  recorded: `RET-59`'s flag looks like the judge missing a settled class-boundary
  convention in `VOCABULARIES.md`, and two responses (`RET-98`, `RET-129`) have rationale
  text that reverses itself and contradicts their own `verdict` field -- read the
  rationale, not just the enum, is now stated in both the module and the write-up.
  Narrative in `EXPERIMENTS.md`; full report in
  `docs/overnight-audit-and-experiment-6-report.md`.
- **A full audit of the ~2,500 lines that merged in from Experiment 5B's enterprise
  framework**, never previously code-reviewed line by line. 21 agents, 12 candidate
  findings, all independently verified against the real code, zero refuted. Every
  numeric claim checked against committed evidence matched exactly; `compare.exact_band`
  matched `EXPERIMENTS.md`'s own reference table at every spot-checked value. Six fixes
  landed (see Fixed), four gaps recorded rather than patched at 2 a.m. (see DEVLOG.md),
  two stale line-number citations in this file corrected.

### Changed

- **The core handoff set now reflects the 2026-08-08 state.** README, AGENTS, CLAUDE,
  DEVLOG, TESTING, CONTRIBUTING and EXPERIMENTS identify Experiment 7 as the latest
  evidence, distinguish setup readiness from migration approval, document the generic
  internal-GPU runtime path, and point teammates to one safe source of truth.
- **Experiment reporting accepts the current experiment identifier and completed full
  arms without load artifacts.** Experiment 7 intentionally excluded load probes; the
  report now labels the active experiment rather than hard-coding Experiment 5.

- **Verdict bands re-derived from sample size and the null, replacing the n=22 absolute
  counts.** Pre-registered as a hard prerequisite twice (`docs/testset-v2-plan.md`,
  `docs/eval-improvement-plan.md`) and skipped both times. The old bands (`<= -2` BEHIND,
  `>= +6` AHEAD) were measured broken and asymmetrically so: BEHIND fired on two identical
  models **one time in three**, AHEAD was arithmetically unreachable on four of six 22-row
  passes, and both would have silently loosened or tightened under any change to pack size
  or arm pair. The new band scales as `sqrt(d)` (`d` = discordant pairs the comparison
  actually produced), holding alpha = 1/64 per side constant -- the rate the old AHEAD gate
  enforced at the only `d` where it could fire. `d >= 6` is now a hard floor: below it the
  correct output is `UNDERPOWERED: NO VERDICT`, not `INDISTINGUISHABLE`, because that word
  claims a measurement came out level rather than that none was possible. Derived from the
  arithmetic alone, before any v3 item existed, so it could not be fit to an observed
  result. Full derivation, including a correction made in review, in `EXPERIMENTS.md`.
- **`MechanismRow` gains `always_correct` and a derived, non-monotone `rate`.** The
  PASS/FLAKY/FAIL letter is kept -- it is the only signal that separates "needs a prompt
  or model change" (FAIL) from "needs decoding discipline and more replicates" (FLAKY) --
  but the letter alone saturates as a group grows: `P(item correct on all replicates) ≈
  0.35-0.43` measured, so `P(family of n reads PASS) ≈ 0.43ⁿ` and any family of size >= 3
  was arriving FAIL/FAIL regardless of how the two arms actually compared. `rate` has no
  such ceiling: one more correct item raises it, one more wrong item lowers it, so two
  all-FAIL rows still separate (e.g. 9/10 from 1/10). Resolves the Known Bug recorded
  2026-08-05 predicting the mechanism table would carry zero information by 100 items --
  it did, and `retention_v3`'s 9-family table (up from 5) is the first pack this fix was
  load-bearing for.
- **The suite moved 82 -> 491 passed standalone across this work**, 11 skipped
  unchanged; 502 passed / 0 skipped with `TRUE_SOURCE_ROOT` set. Both numbers are from a
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

- **Shareability validation no longer mistakes cryptographic identifiers for phone
  numbers.** Exact SHA-256 and HMAC-shaped fields are validated as identifiers before
  phone-like content scanning, so safe evidence can retain integrity hashes without
  weakening checks on arbitrary text.

- **`decision()` never routed an UNDERPOWERED stability verdict to INCONCLUSIVE** --
  quality dimensions got that treatment, stability (computed by the identical
  `paired_verdict`/`exact_band` machinery) did not, and fell through to PASS with no
  statistical evidence that stability held. Found by an audit of the merged enterprise
  framework, reproduced live before fixing: three clean quality verdicts plus a
  genuinely underpowered stability comparison (`d=2`) returned `PASS`. Did not affect
  Experiment 5B's actual FAIL verdicts (its stability was `BEHIND`, not `UNDERPOWERED`,
  on both candidates). Two regression tests added.
- **`validate_plan()` never checked which arm's `role` was `incumbent`** -- not in the
  always-run schema block, not in the locked-status deep audit that otherwise
  re-verifies `selected_provider`/`qualification_sha` against evidence. A transposed
  role flips AHEAD/BEHIND, and therefore PASS/FAIL, for every dimension with no error
  anywhere in the pipeline. Now requires exactly one `incumbent` and every other arm
  `candidate`. Three regression tests (transposed, duplicated, and an unrecognised
  role).
- **`retention_v3.manifest.json`'s own embedded claims were never recomputed and
  compared** against the pack files they describe -- only the manifest file's own bytes
  were pinned, never its content's continued truth. A new test recomputes both file
  hashes, the item count, the scored-row count and the family breakdown against the
  manifest's own text.
- **`tests/test_enterprise_experiments.py` could not run standalone** -- it had no
  `sys.path.insert`, unlike every other test file, and only worked by accident of
  collection order in a full-suite run. Found by trying to run it in isolation.
- Two stale line-number cross-references in `EXPERIMENTS.md`'s Verdict-bands section
  (`:183-189` and `:91-93`), both pointing at the wrong passage after an earlier
  insertion shifted everything after it without updating the citations. Both now point
  at the content they actually describe.

- The full-run runtime gate no longer requires usage/reasoning metadata from failed
  calls. It validates successful responses and reported identities while reliability
  keeps every failure in its denominator, preserving the preregistered 99% rule instead
  of silently turning it into 100%.
- `prompt_token_spread` no longer treats zero-usage failure rows as a second tokenizer;
  calls with absent or non-positive prompt usage are counted separately.
- Scoring provenance no longer uses repository HEAD. Classification, scoring and common
  workload have separate content hashes, so documentation-only commits do not invalidate
  a paid comparison and scoring changes still do.
- HTTP status and attempt count survive onto failure rows, making repeatable provider
  request failures distinguishable from transient transport faults.

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

- **Only aggregate Experiment 7 evidence is committed.** Raw model completions,
  transcripts, cited judge spans, credentials and private judge records remain under
  ignored `out/`; the committed evidence contains synthetic aggregate counts and hashes
  only. Documentation makes that boundary part of the team pickup checklist.

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
- **The synthetic phone block was widened, `^08100000[0-9]{2}$` → `^0810000[0-9]{3}$`**
  (`src/evalgen/testsets.py:135`). This is a reviewed change to one of the three controls
  that keep customer identifiers out of git, not a convenience: `retention_v2` used all
  100 numbers the old pattern could spell (see Added), so the next item added had no phone
  number left to draw. The new block is a **strict superset** — one digit moves from the
  fixed prefix to the variable tail, `0810000` + `001` being the same string as `08100000`
  + `01` — so **no fixture number moved**. `retention_v1.*` and `retention_v2.*` stay
  byte-identical, `validate()` returns the same empty problem list on both under either
  pattern, and every existing negative case is still rejected unedited; that last point
  was a requirement rather than a happy result, because a widening that forces an edit to
  the test guarding it cannot be reviewed. Capacity 100 → 1000,
  `0810000000`–`0810000999`, of which 100 are in use and 900 are free. What the control
  has always rested on is the `0810000` prefix, and that did not change. The reasoning is
  kept at the pattern itself (`src/evalgen/testsets.py:99-133`); every document that
  quoted the old range moved with it, with one deliberate exception — the `08100000xx` in
  the 0.1.0 Security entry below is left as written, because it records what had been
  committed at that release and every number it describes is still inside the new block.
  Rewriting a released entry to match today's pattern would edit a record rather than
  correct one.

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
