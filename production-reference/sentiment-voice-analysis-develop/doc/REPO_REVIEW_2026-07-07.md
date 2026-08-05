# Repository Bug & Performance Review — 2026-07-07

## Summary

- **Scope reviewed**: the entire repository as of branch `feature/tax_invoice` (working tree, including uncommitted files): `main.py`, `src/` (core, modules, utils), all five task packages (`sentiment_telesale`, `sentiment_qa`, `ocr_tax_invoice_pipeline`, `tax_invoice_reconcile`, `tax_invoice_fact_check`), all of `config/`, `terraform/` (13 modules + 3 projects), `cloud_build/`, `.github/workflows/`, `.pre-commit-config.yaml`, the `.claude` hook scripts/settings, all four `.env.example` templates, and the full test suite. Binary assets and the lockfile were excluded with reasons (see Coverage Log).
- **Method**: five-phase protocol. **A** – full file inventory from `git ls-files` (+ untracked). **B** – 15 parallel deep-read area agents, each required to read every file in its area end-to-end. **C** – dedicated cross-cutting sweeps: env-var/config contract matrix, and status-lifecycle + cross-run concurrency. **D** – adversarial verification: 8 independent verifier agents prompted to refute each high-stakes finding (default REFUTED), plus main-session line-level re-reads for every finding whose verifier was lost to a session limit; two findings were verified *empirically* in the project venv (Decimal quantize behavior, pandera `Decimal(18,2)` coercion overflow). **E** – loop until dry: round 2 (two fresh discovery sweeps + a coverage closer) yielded 7 new findings (all verified); round 3 (targeted pass over the last under-analyzed regions) yielded 1 new P3; discovery converged and the loop was closed. One verifier refuted a proposed fix (the manifest-first write reorder), which is reflected in P1-3's solution.
- **Checks run** (actual results): `uv run pytest -q --no-cov` → **2046 passed**, 54 warnings, 50.93s. `uv run ruff check .` → **All checks passed**. `uv run ruff format --check .` → **247 files already formatted**. No type checker (mypy/pyright) is configured in the repo, so none was run.
- **Totals**: **84 findings (0 P0, 20 P1, 33 P2, 31 P3) — 78 Confirmed, 6 Suspected.**
- **Key themes**:
  - **The tax-invoice deploy chain is broken end-to-end and CI can't see it**: `terraform validate` fails (`match_prefix` typo), `plan/apply` fails in every environment (unset required vars, wrong/empty project IDs), and even a successful apply would launch jobs pointing at config files that don't exist — while `terraform_pr_validation.yml` only ever validates `sentiment_telesale`.
  - **Non-atomic submit/log writes with no age-out**: the OCR pipeline's three submit-side effects (Vertex submit, pre-log write, manifest write) are not atomic and nothing reconciles the gaps — crash windows produce double Gemini spend on one side and silent, permanent PENDING stranding on the other.
  - **Fail-open/fail-closed asymmetries corrupt or discard business output silently**: uncapped telesale penalties make the headline score contradict its own category cards; three Pydantic validators discard entire scored evaluations over single fields; scenario-2/4 tax documents get empty VAT workbooks despite successful mapping; the new fact-check normalizes "blank" differently per field type.
  - **Shared CSV state lacks concurrency/idempotency guards almost everywhere**: only the OCR pre-processing log uses `if_generation_match`; the reconcile/fact-check/telesale/QA audit logs are last-writer-wins read-merge-write.
  - **Coverage theater**: several test files exec detached source snippets, inject locals via `ctypes`, or even write directly into coverage data — green tests that structurally cannot fail.

## Coverage Log

| Area | Files read | Excluded (reason) | Rounds | Findings |
|---|---|---|---|---|
| Core framework & entry (`main.py`, `src/core/*`, `config/common.yml`, core tests) | 12/12 | — | 2 | P2-26, P2-27, P2-28, P3-11, P3-12 |
| Integration modules (`src/modules/**`) | 15/15 | — | 2 | P2-16, P2-17, P2-25, P3-6..P3-10 |
| Module tests (`tests/modules/**`) | 11/11 (3 completed in round 2) | — | 2 | P2-29, P3-24 |
| Utils (`src/utils/**`) | 12/12 | — | 2 | P2-18, P3-2..P3-5 |
| Utils tests (`tests/utils/**`, `tests/test_utils/**`, 2 in `tests/core/`) | 14/14 | — | 1 | P2-28, P3-23 |
| Telesale tasks/schemas/configs (`tasks/sentiment_telesale/*`, `config/sentiment_telesale/*`) | 23/23 (2 prompt .txt fully placeholder-scanned) | 2 checklist .xlsx (binary; code robustness reviewed instead) | 3 | P1-7, P1-8, P2-12, P2-22, P2-23, P3-25 |
| Telesale output_validation + telesale tests | 22/22 | — | 2 | P1-4, P1-5, P1-6, P2-13, P3-22 |
| QA package/configs/tests (`tasks/sentiment_qa/**`, `config/sentiment_qa/*`) | 24/24 | `user_config.xlsx` (binary) | 3 | P1-9, P1-10, P2-7..P2-11, P2-24, P3-13..P3-18 |
| OCR pipeline source + tax configs (`tasks/ocr_tax_invoice_pipeline/**`, `config/tax_invoice_extraction/*`) | 45/45 | — | 3 | P1-1, P1-2, P1-3, P2-5, P2-6, P3-1, P3-21, P3-31 |
| OCR pipeline tests (`tests/test_tasks/ocr_pipeline/**`) | 27/27 | — | 1 | P3-24 (fixture drift items) |
| Reconcile source (`tasks/tax_invoice_reconcile/**`) | 31/31 (SQL read line-by-line) | — | 2 | P1-11, P2-1..P2-4, P3-20 |
| Reconcile tests (`tests/test_tasks/tax_invoice_reconcile/**`) | 18/18 | — | 1 | P2-30, P3-26 |
| Fact-check package/configs/tests (`tasks/tax_invoice_fact_check/**`, fact-check YAMLs/workflow) | 22/22 | — | 1 | P1-12, P2-19, P2-20, P2-21, P3-19 |
| Terraform (`terraform/modules/**`, `terraform/projects/**`) | 93/93 | — | 2 | P1-13..P1-18, P2-33, P3-28 |
| Cloud Build / CI / meta (`cloud_build/**`, `.github/**`, `.pre-commit-config.yaml`, `.env.example` ×4, `.claude` hooks+settings, `.gitignore` ×2) | 26/26 | — | 2 | P1-19, P1-20, P2-31, P2-32, P3-29, P3-30 |
| Cross-cutting sweeps (env contract; lifecycle/concurrency) | re-reads of above | — | 1 each | P1-19, P2-1, P2-14, P2-15 |
| **Excluded** | — | `uv.lock` (lockfile); `resources/fonts/*` 16 .ttf + OFL.txt (binary/license); `doc/ponytail_review_tax_invoice.md` (prior review — read as context, not re-reviewed); `.claude/` agent/command/rule .md files (AI-assistant instructions, not runtime code — the two rule files that give coding guidance were cross-checked and produced P3-27) | — | — |

Every inventoried file is accounted for above: read end-to-end by at least one area agent (large files chunked), or listed as excluded with reason.

## Findings

*(No P0 findings survived verification. Two candidates were filed as P0 and downgraded by independent verifiers with explicit reasoning: P1-1 requires a same-run basename coincidence; P1-4's error direction is conservative. Both deserve P0-level urgency.)*

---

### [P1-1] Same-basename source files collide on GCS landing and processing keys → cross-file misattribution, duplicate Gemini spend, source-bytes loss
- **Status**: Confirmed (independent verifier; every hop re-traced)
- **Area**: `tasks/ocr_tax_invoice_pipeline`
- **Location**: `tasks/ocr_tax_invoice_pipeline/module/source_loader.py:142,171,196`; `tasks/ocr_tax_invoice_pipeline/module/document_processor.py:90-91`
- **Problem**: The SharePoint input is listed recursively over per-company/user subfolders (`source_loader.py:49`; the subfolder layout is documented at `ocr_pipeline_pre_tasks.yml:84`). Object keys keep only the basename: landing = `{landing_prefix}/{basename}` and processing = `{run_prefix}/{stem}_pNNN.pdf`, where both prefixes are per-RUN constants (`%{DATA_DATE_YYYYMM}/${JOB_ID}`, `submit_task.py:143,208`). Two files named e.g. `invoice.pdf` in different subfolders in one run: the landing upload (`gcs.py:154`, `upload_from_string`, no `if_generation_match`) silently overwrites one file's bytes; both pre-log rows point at one surviving copy; the same page URI is submitted to Gemini twice (two billed predictions); and the finalizer join (`result_finalizer.py:84-108`, keyed on `job` + `parent_path`/`child_path`) fans out 2×2 so **both** SharePoint documents receive the surviving document's extraction — the treasury output looks legitimate for a file whose real content was never read.
- **Trace**: `source_loader.py:196` `name = os.path.basename(name)` (while `sp_path` keeps the full path) → union dedupe (`:89`) and `filter_new` (`:112`) key on `sp_path`, so both files survive → `:142` upload target and `:171` `gcs_path` identical → `page_processor.py:78,91,103` same `parent_path`/`child_path` → `batch_submitter.py:70` no dedupe → `payload_builder.py:79-83,97` two JSONL lines, one URI → `pre_log_builder.py:88-108` two PENDING rows, same landing/job → `result_finalizer.py:94-108` DISTINCT map ×2 joined to 2 result rows = 4 attributed rows.
- **Ruled out**: no per-file namespacing anywhere (`${JOB_ID}`/date are per-run); dedupe keys on `sp_path` (differs, so both kept); the fan-out is deterministic from identical `child_path` — independent of GCS overwrite semantics.
- **Sibling guard**: none exists anywhere.
- **Solution**: make the object key unique per source path at the single chokepoint — `source_loader.py:196`: `name = f"{hashlib.sha1(parent_path.encode()).hexdigest()[:8]}_{os.path.basename(name)}"` (deterministic, slash-free; landing, processing, and both manifest join keys inherit uniqueness with no further edits; user-facing `FILE_NAME` comes from `sharepoint_input_path` and is untouched). Cosmetic follow-up: `result_finalizer.py:120` derives rejected-row `FILE_NAME` via `SPLIT_PART(parent_path,…)` — switch to `sharepoint_input_path`. Test: two same-basename entries with different `sp_path` produce distinct `gcs_path` and `child_path`.
- **Effort**: M | **Risk of fix**: landing and processing keys must change together or parent/child join desyncs; keep the token identical across submit and join.

### [P1-2] Vertex batch jobs are submitted before any log row is persisted → orphaned paid jobs + double submission on the next run
- **Status**: Confirmed (two independent finders + independent verifier)
- **Area**: `tasks/ocr_tax_invoice_pipeline`
- **Location**: `tasks/ocr_tax_invoice_pipeline/submit_task.py:193-195`
- **Problem**: `execute_task` submits **all** batch jobs (`:193`) before building (`:194`) and persisting (`:195`) any pre-processing-log rows. The window is wide, not theoretical: `pre_log_builder.py:159` makes a SharePoint `get_web_url` Graph call **per row**, and `get_web_url` re-raises any HTTP error (`sharepoint.py:911,923-925`). If build/persist fails after submit, the jobs run (billable) with no PENDING row; the next run's dedupe (`submit_task.py:240-246` reads only the log) re-uploads and **re-submits** the same files (2× Gemini), while the first jobs' predictions are never retrieved (`retrieve_task.py:155-161` polls only logged jobs).
- **Trace**: submit `:193` → SharePoint/GCS I/O in `:194-195` can raise → no PENDING row → next run `_in_flight` ∅ for these files → `filter_new` (`source_loader.py:98-116`) keeps them → re-submit.
- **Ruled out**: INITIAL rows don't help (built after submit, persisted in the same failing step, and not in the `{PENDING, PARTIAL}` dedupe set — `constant.py:39-47`); the generation guard protects concurrent writers, not crash-before-write; there is no GCS-landing existence check.
- **Sibling guard**: none for this window (finalize-last covers only the retrieve→business crash window).
- **Solution**: make `get_web_url` non-fatal inside `build` (blank URL on failure) and persist each payload's PENDING rows immediately after its submission succeeds (inside the `batch_submitter` loop), shrinking the blast radius to one payload. A fully correct fix is a pre-submit "claimed" status honored by the dedupe. Note the tension with P1-3(ii): the durable answer for both is the reconciliation backstop described there. Test: stub submit to succeed and persist to raise; assert next-run behavior.
- **Effort**: M | **Risk of fix**: per-payload persistence changes log-row cardinality; keep the soft-schema-validate path.

### [P1-3] In-flight files can strand in PENDING forever — three trigger paths, no age-out, no alert, run reports success
- **Status**: Confirmed (independent verifier; trigger (ii) is permanent, (i) partially self-heals, (iii) is the general form)
- **Area**: `tasks/ocr_tax_invoice_pipeline`
- **Location**: `tasks/ocr_tax_invoice_pipeline/status_finalizer.py:88-91` (dropna path), `submit_task.py:248-268` (write order), `result_retriever.py:149-183`, `retrieve_task.py:130-147`, `finalize_task.py:68-84`
- **Problem**: (i) a job that polls SUCCEEDED but yields **zero** located prediction lines (missing/empty `predictions.jsonl`) is neither dead nor running → empty frame → `resolve_terminal_statuses` returns `{}` → finalize returns without stamping (`finalize_task.py:69-71`); the file stays PENDING, the pre-run dedupe skips it forever, and the run logs "No predictions collected" and reports success. Transient GCS blips self-heal next run; a *persistently* empty output is indistinguishable and stuck forever. (ii) `_persist_logs` writes the pre-log (`:250-259`) **then** the manifest (`:260-268`); a crash between the two leaves a PENDING row with zero manifest pages — every later retrieve joins predictions against an empty manifest (`result_finalizer.py:94,108`), `FILE_PATH` comes back null, `dropna(subset=["FILE_PATH"])` (`status_finalizer.py:89`) drops it, never stamped, **permanent**, and the paid predictions are never delivered. (iii) any prediction whose `source_file_uri` misses every manifest `child_path` takes the same silent-drop path. There is no PENDING age-out, retry cap, or operator alert anywhere (grep-verified; only tracing has retention).
- **Trace**: each hop cited above was re-read by the verifier; the grep for age/timeout/max-attempt logic over the package returns only the tracing retention sweep.
- **Ruled out**: genuinely failed jobs are handled (`_force_dead` stamps FAILED, `status_finalizer.py:140-148`); BLANK/UNSUPPORTED/invalid **lines** still emit rows (`result_retriever.py:200-207,221-222`) — only zero-lines/zero-join triggers this.
- **Sibling guard**: none — no "polled-terminal-but-yielded-nothing" branch exists.
- **Solution**: **do not** reorder the two writes (a proposed manifest-first fix was refuted in verification: it converts trigger (ii) into P1-2's double-submission). Add an idempotent reconciliation backstop instead: in retrieve/status-finalize, treat a polled-terminal job whose files got no statuses as FAILED **after a cutoff** (age-out by `update_dt`, e.g. N runs/hours), stamping them terminal + notifying, so they become re-processable; optionally re-derive missing manifest rows from the GCS processing prefix. This one backstop also closes P1-2's orphaned-job half. Tests: zero-prediction SUCCEEDED job ages out to FAILED; PENDING row without manifest rows ages out.
- **Effort**: M | **Risk of fix**: age-out too aggressive converts recoverable transients into FAILED — make the cutoff ≥ a few runs.

### [P1-4] Telesale subcategory penalties are never capped at their configured `max` — the headline `total_score` contradicts the category cards on the same report row
- **Status**: Confirmed (independent verifier re-derived the arithmetic from the real YAML)
- **Area**: `tasks/sentiment_telesale`
- **Location**: `tasks/sentiment_telesale/prep_result_task.py:155-156` (uncapped return), `:161-163` (accumulation), `:201-208` (total)
- **Problem**: a subcategory's leaf penalties are summed raw and returned uncapped, then accumulated into `total_penalty`; `total_score = total_max_score + total_penalty` is floored only at 0 for the grand total. The *displayed* subcategory/category score IS clamped to ≥0 (`:113-114`, `:120`) and the export renderer renormalizes from the clamped values (`export_output_result_task.py:553,601,647,696`). Real numbers (`telesale_scoring.yml`): `compliance.compliance` penalties −15/−10/−15 = −40 against `max: 15`. A call failing all three compliance flags but perfect elsewhere renders cards 30+35+20+0 = **85** while the headline on the same row is 100−40 = **60** (`:778-782` → `:811-821`). 13 of 14 subcategories can overflow whenever ≥2 flags fail; multi-breach calls collapse toward 0, destroying score differentiation.
- **Trace**: verifier reproduced 85-vs-60 exactly; the `not_none` dual accounting does not rescue it (else-branch at `:201-204` fires when nothing is N/A and carries the same raw −40).
- **Ruled out**: the "intended: total=raw" reading — the `:113/:120` display floors are precisely the guard the accumulation path lacks; nothing documents cross-category bleed; the only related test (`test_prep_result_task.py:264-280`) uses a single subcategory where the grand-total floor coincidentally hides the bleed.
- **Sibling guard**: the display clamp at `prep_result_task.py:113-114` — the asymmetry is the proof.
- **Solution**: clamp at the single accumulation chokepoint (`:151-156` branch): `current_level_penalty = max(current_level_penalty, -this_level_max)` (and the `_not_none` twin). Add the missing test: over-penalized subcategory **alongside** a passing category, asserting `total_score == 85`.
- **Effort**: S code / M validation | **Risk of fix**: historical totals rise for over-penalized calls — coordinate with report consumers before deploying.

### [P1-5] Out-of-range `churn_risk_indicator` discards the entire scored evaluation
- **Status**: Confirmed (main-session line verification)
- **Area**: `tasks/sentiment_telesale/output_validation`
- **Location**: `tasks/sentiment_telesale/output_validation/common/customer_insight.py:21-24,30-37`
- **Problem**: `churn_risk_indicator: int = Field(default=0)` has no `ge/le`, so the response schema sent to Gemini carries **no bounds**; the after-validator raises for values outside 0–100. One out-of-range integer (e.g. 105) fails `model_validate_json` for the whole record → stamped FAILED (`get_batch_result_task.py:471-474`) → scoring skipped (`prep_result_task.py:181`). All four *scored* categories are discarded because of an *unscored* field, and the raw prediction text is not persisted (only set on success, `get_batch_result_task.py:465`), so the data is unrecoverable. The same models feed the telesale fact-check, silently shrinking its sample.
- **Trace**: schema build `prep_payload_task.py:375-381` → validator raise `customer_insight.py:35-36` → catch/FAILED `get_batch_result_task.py:463-474` → skip `prep_result_task.py:181`.
- **Ruled out**: no bound is enforced upstream (schema unbounded); `customer_sentiment_emotional` (`:26-28`, required, no default) discards the record the same way if omitted.
- **Sibling guard**: `sales_performance.py:49-87` silently normalizes invalid counts — the asymmetry is the evidence clamping is the house pattern.
- **Solution**: clamp instead of raise: `self.churn_risk_indicator = max(0, min(100, v))`; update `tests/test_tasks/sentiment_telesale/test_output_validation.py:177-181` which enshrines the raise. Also consider persisting the raw prediction text on parse failure.
- **Effort**: S | **Risk of fix**: one test's meaning changes.

### [P1-6] Invalid verification combination discards the entire scored evaluation
- **Status**: Confirmed (main-session line verification)
- **Area**: `tasks/sentiment_telesale/output_validation`
- **Location**: `tasks/sentiment_telesale/output_validation/common/operations_and_professionalism.py:63-78`
- **Problem**: with `customer_verification=True`, only 4 of the 9 possible `(invalid_verification, missing_verification)` combinations are accepted; the other five — all schema-legal (`bool|None` each) and plausible model outputs, e.g. `(True, False)` — raise, discarding the whole record exactly as in P1-5. This field IS scored, but the cost (losing all categories, unrecoverable raw output) is disproportionate.
- **Trace**: `:72-78` raise → `get_batch_result_task.py:471-474` FAILED → `prep_result_task.py:181` skipped.
- **Ruled out**: the `None`/`False` arms of the same validator (`:55-62`) normalize silently — only the `True` arm raises.
- **Sibling guard**: `operations_and_professionalism.py:55-62` (same validator's other branches).
- **Solution**: log + coerce to the nearest valid combination (product sign-off on the coercion table), or mark the subcategory N/A instead of failing the record. Update `test_output_validation.py:213-224`.
- **Effort**: S | **Risk of fix**: coercion choice needs business confirmation.

### [P1-7] Telesale can double-submit the same voice files to Vertex — no submit-time dedupe, and Cloud Run retries amplify it
- **Status**: Confirmed (mechanism traced by two agents; terraform amplifier verified by literal quotes)
- **Area**: `tasks/sentiment_telesale` + `terraform/projects/sentiment_telesale`
- **Location**: `tasks/sentiment_telesale/prep_payload_task.py:228-446`; `tasks/sentiment_telesale/execute_batch_job_task.py:158`; `terraform/projects/sentiment_telesale/main.tf:57,145` (`max_retries = 3`)
- **Problem**: dedupe rests entirely on export archival deleting the GCS input voice. If yesterday's batch is un-retrieved when the next run starts (batch >24 h, or tasks 1–3 fail), PrepPayload re-lists the same inputs, re-copies to processing, and ExecuteBatch submits an unconditional duplicate batch (double Gemini cost + duplicate predictions that GetBatchResult will double-score). Separately, `max_retries = 3` means any container failure *after* a successful submit re-runs the whole task list — same effect. The control-file "Y" stamp guards only the SharePoint→GCS upload, not payload building.
- **Trace**: `prep_payload_task.py:249` lists input → `:260` copy → `:319-408` payloads → `execute_batch_job_task.py:158` submit; nothing consults `batch_processing_log` for an in-flight submission.
- **Ruled out**: happy-path days don't duplicate (retrieval+archival deletes inputs first); the QA pipeline has the identical task shape but sets `max_retries = 0` everywhere (`sentiment_qa/main.tf:57,156,255`) — evidence 3 is an oversight, not a choice.
- **Sibling guard**: the OCR pipeline's PENDING/PARTIAL in-flight dedupe (CLAUDE.md-documented); QA's `max_retries = 0`.
- **Solution**: skip payload rows whose file already has a submitted-but-non-terminal `batch_processing_log` entry, and set telesale `max_retries = 0` (or add an app-side submitted-marker) with owner sign-off. Test: second PrepPayload run with an in-flight log row produces zero payloads.
- **Effort**: M | **Risk of fix**: over-aggressive skip could block legitimate re-processing — scope to non-terminal jobs only.

### [P1-8] Telesale archival deletes recordings whose archive copy failed — permanent loss of source audio
- **Status**: Confirmed (main-session line verification)
- **Area**: `tasks/sentiment_telesale`
- **Location**: `tasks/sentiment_telesale/export_output_result_task.py:1019-1067`
- **Problem**: `_archive_files` batch-copies SUCCESS recordings to archive (`copy_files_batch` returns `{success, failed}` and does not raise on partial failure), logs a warning if `failed > 0` (`:1038-1039`), then deletes **every** path in `to_archive` from processing (`:1042-1046` — the comment says "successfully copied files", but the aggregate result has no per-file identity so the loop cannot be selective) and separately deletes the same records from input (`:1054-1058`), unconditional on archive success. A transient GCS error during the copy phase destroys the only copies of those recordings. The predictions were already exported, so the loss is the *audio* (compliance/replay), and the only signal is one warning log line.
- **Trace**: `:1030-1035` copy → `:1037-1039` count-only handling → `:1042-1046` delete-all → `:1054-1058` input delete. A **full**-batch exception skips deletion (`:1047-1049`); only *partial* failure loses audio.
- **Ruled out**: no retry or reconciliation re-copies later (input+processing copies are gone).
- **Sibling guard**: QA's `_archive_files` uses per-file `move_file` (`tasks/sentiment_qa/export_output_result_task.py:1518,1603`) — a failed move leaves the source intact. Telesale regressed when switching to batch-copy-then-bulk-delete.
- **Solution**: delete only files confirmed copied (extend `copy_files_batch` to return per-file results, or verify existence in archive before delete), or revert to per-file `move_file` like QA. Test: copy result with `failed>0` → failed files remain in processing and input.
- **Effort**: S–M | **Risk of fix**: none material; strictly safer.

### [P1-9] QA payload prep: a processing-folder listing error either crashes the whole run or silently builds payloads from the previous folder's file list
- **Status**: Confirmed (main-session line verification; a test enshrines the crash mode)
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/prep_payload_task.py:299-311`
- **Problem**: the `except` at `:303-304` logs but neither `continue`s nor raises, and control falls into `for processing_file in on_prcoess_files:` (`:311`). Mode 1 — failure on the first (date, folder) iteration: `on_prcoess_files` is unbound → `UnboundLocalError` aborts payload prep for the whole run (no batch that day). Mode 2 — failure on a later iteration: the loop silently reuses the **previous** iteration's file list, building payload lines and processing-log rows for the wrong product/date. `tests/test_tasks/sentiment_qa/test_prep_payload_task.py` (`test_prepare_payload_raises_after_processing_list_failure`) asserts the `UnboundLocalError` as expected behavior.
- **Trace**: `:299` assign inside `try` → `:303-304` except without `continue` → `:311` iterates stale/unbound name → `:313-319` rows built against current date/folder context.
- **Ruled out**: the three sibling `except` blocks in the same loop all `continue` (`:261-263`, `:272-274`, `:292-295`) — skip-and-continue is unambiguous intent.
- **Sibling guard**: `prep_payload_task.py:272-274`.
- **Solution**: add `continue` at `:304`; replace the enshrining test with one asserting the date/folder is skipped. Effort: S | **Risk of fix**: none — error path only.

### [P1-10] Upload control marked "Y" even when file uploads failed — those calls are silently never analyzed (QA and telesale)
- **Status**: Confirmed (main-session line verification of the QA stamp order; module no-raise contract confirmed by two agents)
- **Area**: `tasks/sentiment_qa`, `tasks/sentiment_telesale`
- **Location**: `tasks/sentiment_qa/upload_voice_task.py:445-455,509-526,528-534`; `tasks/sentiment_telesale/upload_voice_task.py:429-436`
- **Problem**: QA stamps `processed_status="Y"` for a (datadate, folder) as soon as ≥1 valid file is *found* — before uploading; `upload_sharepoint_to_gcs` never raises on per-file failure (`src/modules/google/gcs.py:113,161-178` returns `{success, failed, errors}`); failures land only in an email summary table; "Y" is persisted and the next run's dedupe (`:325-332,349-356`) skips the folder. Telesale stamps after the upload but likewise ignores `failed>0`. Files that failed to upload are never in GCS, never scored, never retried — silent loss of input calls with only an email row as signal.
- **Trace**: QA `:447-455` stamp → `:509` upload → `:517-526` summary→email only → `:534` persist → next-run skip.
- **Ruled out**: nothing downstream resurfaces them (the batch-processing log only covers uploaded files); re-upload would be idempotent, so only the "Y" prevents recovery.
- **Sibling guard**: none (both products share the defect; the OCR pipeline's per-file landing bookkeeping is the closest correct analogue).
- **Solution**: stamp "Y" only when `upload_summary["failed"] == 0`, else stamp "N" with the failed count in `remark` (or track per-file). Test: upload summary with `failed>0` → status "N", next run retries.
- **Effort**: M | **Risk of fix**: partially-failed dates re-process — intended; re-upload is idempotent.

### [P1-11] Scenario-2/4 documents produce header-only VAT Report workbooks despite a successful mapping
- **Status**: Confirmed (mechanism; business intent unconfirmed — the mapping is visible elsewhere, the per-document VAT deliverable is what's empty)
- **Area**: `tasks/tax_invoice_reconcile`
- **Location**: `tasks/tax_invoice_reconcile/module/output_exporter.py:118-147`
- **Problem**: scenario-2/4 documents (no invoice number, by definition) reconcile via company+vendor+month+VAT-sum; their Output row shows `Mapping_Status=Completed` with a populated `Payment Document (VAT Report)` and the enriched Z45 marks the lines Completed. But `_export_vat` selects Z45 rows **only by invoice number**: `_invoice_numbers` (`:143-147`) drops blanks and the "No" sentinel → empty set for a scen-2/4 group → the `if ref is not None and inv_nums:` guard (`:132`) falls through to a header-only workbook (`:140-141`) with a log line falsely claiming "No Z45 rows mapped". The per-document VAT deliverable silently omits the mapped payment lines.
- **Trace**: NULL `INVOICE_NUMBER` → `reconciliation_builder.py:202` CAST → blanked by `report_output` `_blank_na` → grouping `output_exporter.py:98-100` → empty `inv_nums` → header-only.
- **Ruled out**: pandera passes both frames; the full enriched-Z45 export is correct — only the per-document slice is wrong, hence silent. Tests only exercise invoice-number selection (`test_output_exporter.py:151-197`); scen-2/4 tests assert Completed but never open the VAT workbook (`test_reconciliation_builder.py:289-328`).
- **Sibling guard**: none — scenarios 1/3 work because their invoice numbers are populated, masking the gap.
- **Solution**: in `_export_vat`, additionally select Z45 rows whose payment document ∈ the group's mapped `Payment Document (VAT Report)` values (positional lookup like `_z45_ref_column`), unioned with the invoice-number match; scope to the group's own mapped rows. Test: scen-2 doc + matching payment document → row present in the VAT workbook.
- **Effort**: M | **Risk of fix**: shared payment documents across groups in merged E-TAX workbooks — keep the selection group-scoped.

### [P1-12] Fact-check scores blank-marker ground-truth cells inconsistently per field type — text/amount fields count a labelled blank as a mismatch
- **Status**: Confirmed (finder verified empirically; contradicts the module's own documented contract)
- **Area**: `tasks/tax_invoice_fact_check`
- **Location**: `tasks/tax_invoice_fact_check/module/value_normalizer.py:55-111` (contract at `:6-8`, `helper/constant.py:21-22`)
- **Problem**: a null extraction always normalizes to `NA_SENTINEL="N/A"`, but a GT cell containing a literal blank marker (`"N/A"`, `"-"`, `"none"`) normalizes differently by compare type: TEXT → `"n/a"` (casefolded) and AMOUNT → `"n/a"` (Decimal fails → text fallback), both ≠ sentinel → counted FP; while TAXID/DATE/BOOL all fall back to the sentinel → TP. The same "field is absent" convention is scored correctly for some field types and as a mismatch for others, skewing the published accuracy per field.
- **Trace**: `:55-56` null guard; `:68-71` text; `:80-86` amount fallback; `:74-77`/`:99`/`:111` sentinel returns; exact compare at `fact_check_evaluator.py:110-112`.
- **Ruled out**: tests cover only Python `None` (not marker strings) and the taxid path that happens to work.
- **Sibling guard**: none in-package — this is the divergence itself.
- **Solution**: treat casefolded `{"", "-", "n/a", "na", "none", "null"}` as null in `normalize_value` before type dispatch. Tests: GT `"N/A"`/`"-"` vs null extraction ⇒ match for a TEXT and an AMOUNT field.
- **Effort**: S | **Risk of fix**: a field legitimately equal to `"-"` reads as blank — acceptable for these fields; keep the marker set minimal.

### [P1-13] Terraform: `match_prefix` vs `matches_prefix` typo breaks `terraform validate`/`plan` for the whole tax project
- **Status**: Confirmed (verifier quoted both sides; the `condition` object type is closed)
- **Area**: `terraform/projects/tax_invoice_extraction`
- **Location**: `terraform/projects/tax_invoice_extraction/main.tf:43` vs `terraform/modules/cloud_storage/variables.tf:161`
- **Problem**: the bucket module call sets `condition.match_prefix`, but the module's `lifecycle_rules` type declares `matches_prefix`; the object type lists 15 named optional attributes with no wildcard, so Terraform rejects the undeclared attribute. `cloud_build/tax_invoice_extraction/wf_deployment.yml` runs bare `terraform validate` as an early step → every environment's deploy halts before plan.
- **Trace**: static literal → strict object-type conversion error at validate.
- **Ruled out**: not an `optional()`-tolerated extra key (type re-read in full).
- **Sibling guard**: neither sibling project sets this field at all.
- **Solution**: rename to `matches_prefix` at `main.tf:43`. **Effort**: S | **Risk of fix**: none.

### [P1-14] Terraform: tax tfvars are wrong or incomplete in all three environments — apply blocked, and even a successful apply would launch jobs against nonexistent config files
- **Status**: Confirmed (verifier quoted every value; filenames Glob-confirmed absent)
- **Area**: `terraform/projects/tax_invoice_extraction`
- **Location**: `nprd_config.tfvars:3-10`, `release_config.tfvars:3-9`, `prod_config.tfvars:3-9` vs `variables.tf:21-24,58-66` and `main.tf:73,146,168,230,324`
- **Problem**: four distinct defects: (a) required `config_fact_check_path_pre/post` (no default; consumed at `main.tf:230,324`) are set in **no** tfvars (release/prod set an undeclared `fact_check_path` instead) → "No value for required variable" in every env; (b) required `gcp_scheduler_location` absent from release+prod tfvars → same failure; (c) `config_path_pre/post` reference files that don't exist — nprd: `ocr_tax_invoice_pipeline_{pre,post}_tasks.yml`, release/prod: `tax_invoice_{pre,post}_tasks.yml`; the real files are `ocr_pipeline_{pre,post}_tasks.yml` (the container COPYs config verbatim), so every job execution would die at startup with `FileNotFoundError` (`engine.py:84-86`); (d) `release_config.tfvars:3` carries **sentiment-telesale's** GCP project id (`gcp-noexp-wl-nprd-senti-qa-tel`) with an empty `service_account_email`, and `prod_config.tfvars` has empty project id + SA — a filled-in SA without noticing (d) would deploy tax infrastructure into the telesale project.
- **Trace**: wf_deployment passes only `-var-file` + image vars; no `-var` overrides anywhere.
- **Ruled out**: no Dockerfile rename of config files; no default values on the variables.
- **Sibling guard**: the non-fact-check `config_path_*` pair is set in every tfvars (pattern exists); QA/telesale prod tfvars carry real values.
- **Solution**: in all three tfvars set `config_path_pre/post` to `config/tax_invoice_extraction/ocr_pipeline_{pre,post}_tasks.yml`, add `config_fact_check_path_pre/post` → `ocr_pipeline_fact_check_{pre,post}_tasks.yml`, add `gcp_scheduler_location` to release/prod, fix release project id to `gcp-noexp-wl-nprd-taxinvoiceex`, and fill prod values (owner-confirmed).
- **Effort**: S | **Risk of fix**: values need owner confirmation; do a plan-only dry run first.

### [P1-15] Terraform: the fact-check block has three copy-paste defects — duplicate env var (Cloud Run rejects both jobs), image pointing at a repo that is never created, scheduler in an unsupported region
- **Status**: Confirmed (verifier quoted all lines; repo naming is three-way inconsistent)
- **Area**: `terraform/projects/tax_invoice_extraction`
- **Location**: `main.tf:110-111` and `:267-268` (byte-identical duplicate `TAX_INVOICE_FACT_CHECK_PATH` env entries in **both** job containers); `main.tf:221` (image repo `…-tax-invoice-extraction-artifact-repo`) vs `main.tf:16-23` (only repo created: `…-ai-tax-inv-reconcile-artifact-repo`) and `outputs.tf:17` (a third spelling `…-extraction-tax-invoice-artifact-repo`); `main.tf:288` (`region = var.gcp_region`) vs `:131` (`var.gcp_scheduler_location`)
- **Problem**: (a) Cloud Run v2 requires unique env names per container — the duplicate entry fails create/update for **both** the main and fact-check jobs; (b) the fact-check job's image path names a repository that no environment creates and CI never pushes to → permanent ImagePull failure; (c) the fact-check scheduler ignores the dedicated scheduler-region variable — in nprd that is `asia-southeast3`, which the project's own README (`README.md:17`) says has no Cloud Scheduler.
- **Trace/Ruled out**: `locals.tf:44-45` also lists the secret twice (harmless there via `toset`); the env blocks are hand-written literals; only one `artifact_registry_repo` module exists in the file.
- **Sibling guard**: the main job's image matches the created repo; the main scheduler uses the right variable; QA/telesale env lists have no duplicates.
- **Solution**: delete one duplicate env line in each container; change `main.tf:221`'s repo segment to `${var.environment}-ai-tax-inv-reconcile-artifact-repo` (and fix `outputs.tf:17` to match); `main.tf:288` → `var.gcp_scheduler_location` (also add the `Content-Type` header the main scheduler sets at `:142`).
- **Effort**: S | **Risk of fix**: none.

### [P1-16] Terraform: sentiment_qa spells "nprd" as "nrpd" — secret create-vs-reference logic inverted for the nprd environment
- **Status**: Confirmed (verifier quoted lines 3 and 11; siblings spell it correctly)
- **Area**: `terraform/projects/sentiment_qa`
- **Location**: `terraform/projects/sentiment_qa/main.tf:3,11`
- **Problem**: `contains(["nrpd","prod"], var.environment)` is false for the real value `"nprd"` → the secrets module creates zero secrets while the `existing` data source tries to read all 47 → a first-time (or post-teardown) nprd apply fails with "Secret … not found".
- **Trace**: `nprd_config.tfvars:2` sets `environment = "nprd"`; both `for_each` conditionals carry the typo.
- **Ruled out**: `release` matches neither spelling → accidentally unaffected; prod unaffected.
- **Sibling guard**: `sentiment_telesale/main.tf:3,11` and `tax_invoice_extraction/main.tf:3,11` spell `"nprd"`.
- **Solution**: fix both occurrences. **Effort**: S | **Risk of fix**: if the 47 secrets already exist in nprd, a corrected apply will try to create them → rely on the existing idempotent `terraform-import-secrets` step in `wf_deployment.yml`; run plan first.

### [P1-17] Terraform: both tax Eventarc trigger patterns carry a leading "/" — the post pipeline may never auto-trigger
- **Status**: Suspected (drift vs sibling Confirmed; the exact GCP match-path-pattern failure was not executed live)
- **Area**: `terraform/projects/tax_invoice_extraction`
- **Location**: `main.tf:197` and `:353` (`value = "/projects/_/buckets/…/predictions.jsonl"`)
- **Problem**: GCS audit-log `resourceName` is `projects/_/buckets/{b}/objects/{o}` (no leading slash). If the `match-path-pattern` never aligns, the trigger never fires, the post workflow never runs, and files sit PENDING/PARTIAL with no automatic recovery (compounding P1-3's no-alert behavior).
- **Trace**: the eventarc_trigger module passes the value through verbatim.
- **Ruled out**: nothing — the unproven hop is GCP's live matching of a leading-slash pattern.
- **Sibling guard**: `sentiment_qa/main.tf:415,597` — identical trigger shape with **no** leading slash.
- **Solution**: remove the leading `/` from both values; verify with a manual object-create test in nprd after deploy.
- **Effort**: S | **Risk of fix**: none.

### [P1-18] Terraform CI validates only sentiment_telesale — the tax and QA projects (including this branch's changes) get zero validation
- **Status**: Confirmed
- **Area**: `.github/workflows`
- **Location**: `.github/workflows/terraform_pr_validation.yml:9-12` (paths trigger), `:88,97,218` (hardcoded working directories)
- **Problem**: the `on.pull_request.paths` filter and every job's working directory name only `sentiment_telesale`. A PR touching only `terraform/projects/tax_invoice_extraction/**` (exactly this branch) never triggers validation — which is why P1-13…P1-17 exist undetected.
- **Trace**: direct read of the full workflow.
- **Ruled out**: no other workflow exists.
- **Sibling guard**: none.
- **Solution**: matrix the workflow over the three project directories with per-project path filters (keep the branch→environment mapping per project), including the tfsec job.
- **Effort**: M | **Risk of fix**: low.

### [P1-19] The three CLAUDE.md-documented missing secrets are still unprovisioned while shipped configs reference them — reconcile paths silently break at runtime
- **Status**: Confirmed (pre-documented in CLAUDE.md as drift; included so the implementation plan carries the fix)
- **Area**: `terraform/projects/tax_invoice_extraction` ↔ `config/tax_invoice_extraction`
- **Location**: `TAX_INVOICE_OCR_TRACING_LOG_PATH`, `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`, `TAX_INVOICE_TAX_INVOICE_REJECTED` — absent from `locals.tf` secret list and `main.tf` env, referenced by `ocr_pipeline_post_tasks.yml:39,69,99,114` and `ocr_pipeline_pre_tasks.yml:85,114`
- **Problem**: `resolve_env` substitutes **empty string** for missing env vars silently (`src/utils/common.py:114`), so in a deployed job the master-vendor source path collapses to `${ROOT}/`, the reject destination to the root, and the tracing log path to a malformed segment — precheck/reconcile/reject then fail or write to wrong folders without a clear cause.
- **Trace**: env absent → `""` → path concatenation in the YAML → SharePoint 404 / wrong-folder writes.
- **Ruled out**: the app-side fact-check configs deliberately avoid these three (verified clean); only the main pre/post pipelines are exposed.
- **Sibling guard**: every other referenced tax secret is provisioned (`locals.tf` list verified against the configs).
- **Solution**: add the three secrets to `locals.tf`/`main.tf` env for both jobs (values via the existing secret-bootstrap process), and remove the six documented dead legacy secrets while there.
- **Effort**: S | **Risk of fix**: IAM/secret-value population goes through the IT service-request process — surface as a prerequisite.

### [P1-20] `protect_sensitive` hook never fires for PowerShell (the primary shell) and has no Grep enforcement despite matching it
- **Status**: Confirmed (main-session read of both files)
- **Area**: `.claude` (dev-tooling security control)
- **Location**: `.claude/settings.json:9`; `.claude/hooks/protect_sensitive.py:160,177,191`
- **Problem**: the PreToolUse matcher is `"Bash|Read|Write|Edit|MultiEdit|Grep"` — `PowerShell` is absent, so on this Windows setup `Get-Content .env` / `$env:…SECRET…` pass unguarded; and although `Grep` is matched, `main()` has no Grep branch, so `Grep(path=".env", pattern=".")` dumps secret contents straight through (`sys.exit(0)` fall-through at `:191`). The control is inert exactly where it matters most on this machine.
- **Trace**: full dispatch read — only Read/Write/Edit/MultiEdit (`:160`) and Bash (`:177`) are branched.
- **Ruled out**: `.claude/settings.local.json` adds no hooks; ripgrep's gitignore behavior doesn't protect an explicit `path: ".env"`.
- **Sibling guard**: Read/Write/Edit/MultiEdit correctly call `_is_sensitive_path`; Bash has pattern checks.
- **Solution**: add `PowerShell` to the matcher with PS-flavored dangerous patterns (`Get-Content`/`gc`, `Select-String`, `$env:`), and add a Grep branch mirroring Read (`_get_file_path` already parses `path`).
- **Effort**: S | **Risk of fix**: low (fail-open behavior preserved).

---

### [P2-1] Reconcile/fact-check audit logs and stage emails are not idempotent under post-pipeline re-runs (Eventarc redelivery, Cloud Run retry, concurrent triggers)
- **Status**: Confirmed (independent verifier; severity set P2 — damage confined to audit layer + duplicate emails; deliverable is an idempotent overwrite)
- **Area**: `tasks/tax_invoice_reconcile` (+ reused by `tasks/tax_invoice_fact_check`)
- **Location**: `tasks/tax_invoice_reconcile/module/export_logging.py:326,329-338`; `tasks/tax_invoice_reconcile/reconcile_task.py:161,190,193-220`
- **Problem**: `ExportLogging` appends via read-concat-`upload_file` with no etag/generation guard (SharePoint PUT is unconditional, `sharepoint.py:600-636`); the "Extraction/Mapping Success" emails send unconditionally. Re-runs are routine, not exotic: the tax post job has `timeout=7200s, max_retries=3` (a finalize failure re-runs reconcile whole), submit writes one `predictions.jsonl` **per payload** (multiple Eventarc events → concurrent post runs), and Eventarc is at-least-once. Concurrent runs with staggered job completion lose one run's transaction/performance rows (last writer wins); sequential re-runs duplicate rows; operators get duplicate success emails.
- **Trace**: verifier confirmed no conditional write anywhere in the path and confirmed the trigger wiring (`main.tf:172-204`).
- **Ruled out**: Output/VAT workbooks are deterministic-path full-content overwrites (`output_exporter.py:181-188`) — not corrupted; Gemini is not re-billed (GCS re-read).
- **Sibling guard**: the OCR pre-processing log's `if_generation_match` + reload-retry (`log_exporter.py:100-123`).
- **Solution**: route transaction/performance appends through the generation-guarded `LogExporter` (GCS-primary + SharePoint mirror) instead of `ExportLogging._append_and_upload`; gate stage emails on the file's terminal status (or accept at-least-once for emails). Same fix covers the fact-check task.
- **Effort**: M | **Risk of fix**: SharePoint has no native CAS — making GCS primary is the point.

### [P2-2] Scenario-5 VAT total includes scenario-0 (copy/issue-flag) documents — a copy of an invoice can flip its original to Incompleted
- **Status**: Confirmed (mechanism, main-session SQL read; conservative failure direction)
- **Area**: `tasks/tax_invoice_reconcile`
- **Location**: `tasks/tax_invoice_reconcile/module/reconciliation.sql:82-93` vs `:271-272`
- **Problem**: `EXT_TOTAL_VAT = SUM(VAT_AMOUNT) FILTER (WHERE _doc_first) OVER (PARTITION BY TAX_INVOICE_DATE, BUYER_TAX_ID, VENDOR_TAX_ID)` is computed over **all** rows before the scenario split; the CASE at `:82-90` routes copies/issue-flag docs to scenario 0 but nothing excludes them from the window. A copy shares (date, buyer, vendor) with its original *by definition* and `_doc_first` is per (FILE_NAME, TAX_INVOICE_NUMBER), so a copy in a separate file doubles the invoice's VAT in `EXT_TOTAL_VAT` → the scen-5 comparison (`:271-272`) fails → the real document is wrongly Incompleted (fails toward manual review, not wrong money).
- **Trace**: window has no scenario/COPY/ISSUE_FLAG predicate; the `redacted` CTE (`extraction_report_builder.py:247-272`) nulls fields only for suspicious/unsupported/failed — copies keep their VAT_AMOUNT.
- **Ruled out**: the 5-scenario VAT-hard-key design itself is intentional; this is a distinct inclusion defect in the extraction-side sum.
- **Sibling guard**: none — no `WHERE NOT (ISSUE_FLAG IS TRUE OR COPY IS TRUE)` in the FILTER.
- **Solution**: add `AND NOT (COALESCE(ext.ISSUE_FLAG, FALSE) OR COALESCE(ext.COPY, FALSE))` to the `EXT_TOTAL_VAT` FILTER. Test: scen-5 doc + its copy, same date/vendor → still Completed.
- **Effort**: S | **Risk of fix**: none material — strictly tighter sum.

### [P2-3] The shared Z45 source workbook is archived-and-deleted — a second same-day post run or `--rerun_data_dt` halts at precheck
- **Status**: Confirmed (main-session read of the copy-then-delete)
- **Area**: `tasks/tax_invoice_reconcile`
- **Location**: `tasks/tax_invoice_reconcile/module/source_archiver.py:61-67,69-78`; halt at `precheck_task.py:100,119-129`
- **Problem**: `archive_z45` applies the per-invoice copy-then-delete treatment to the run's single shared Z45 input. Any post re-run that day (redelivery, retry, manual replay) finds no Z45 → `DependencyMissingError` → business-exception email + full pipeline halt — turning P2-1's routine re-runs into hard failures.
- **Trace**: `reconcile_task.py:178` → `_copy` → `_delete_source` (`:78`; delete correctly skipped if the archive upload failed, `:75-77`).
- **Ruled out**: per-invoice deletion is the intended idempotency lever (project convention); the Z45 is not a per-document input and no guard distinguishes it.
- **Sibling guard**: invoices are legitimately one-shot; nothing marks the Z45 as shared.
- **Solution**: archive the Z45 **without** deleting the source (idempotent re-archive overwrites the same dated path), or move it to a `consumed/` folder the precheck also searches. Test: reconcile twice against the same fixture; second precheck still finds it.
- **Effort**: S | **Risk of fix**: repeated re-archive of the same file (harmless overwrite).

### [P2-4] `archive_invoices` deletes sources for every processed row regardless of document status — a system-FAILED document's only copy can be removed with no retry path
- **Status**: Suspected (mechanics Confirmed — no status filter at `source_archiver.py:53`; whether archiving failed docs is intended business behavior is unconfirmed)
- **Area**: `tasks/tax_invoice_reconcile`
- **Location**: `tasks/tax_invoice_reconcile/module/source_archiver.py:46-59`
- **Problem**: the loop archives+deletes every distinct `FILE_PATH` in `processing_df`, which includes OCR-FAILED/redacted rows (the `redacted` CTE keeps FILE_NAME/FILE_PATH). Once the source is deleted, the idempotency model (re-processing requires the source) can never retry that document.
- **Trace**: `reconcile_task.py:176` → `:53` `dropna().unique()` → copy→delete.
- **Ruled out**: the archive copy does exist (delete only after successful copy) — the loss is of the *retryability*, not the bytes.
- **Sibling guard**: none — no status gate before archive.
- **Solution**: skip delete (archive-only) for rows whose DOC_STATUS reflects a system failure so they stay in the input for retry; confirm intent with the treasury flow first.
- **Effort**: S | **Risk of fix**: retried failures re-submit → new Gemini cost (acceptable vs. permanent loss).

### [P2-5] The result finalizer is the pipeline's only hard-validating stage — one runaway Gemini amount permanently wedges every retrieve run
- **Status**: Confirmed (empirically verified in the project venv by the finder)
- **Area**: `tasks/ocr_tax_invoice_pipeline`
- **Location**: `tasks/ocr_tax_invoice_pipeline/module/result_finalizer.py:60`; `schema/ocr_output.py:12,45-57,81`; `schema/model_response.py:52-61`
- **Problem**: an integer-magnitude runaway amount (≥17 integer digits — the digit-degeneration family the code already defends against on the fractional side) passes `_quantize_money` unchanged, then `OCROutputSchema.validate` (hard; money columns `Decimal(precision=18, scale=2)`, `coerce=True`) **raises** on the whole concatenated frame → OCRRetrieveTask fails → finalize never runs → the files stay in-flight → every subsequent run re-reads the same deterministic value from GCS and crashes again. Loud (system-error email each run) but cannot self-heal. Every other stage soft-validates (`submit_task.py:274-280`, `tracing_builder.py:133-139`, `finalize_task.py:103-109`).
- **Trace**: verified: 20-significant-digit Decimal passes pydantic; `pandera` coercion of `Decimal('123456789012345678.00')` raises SchemaError (does not null).
- **Ruled out**: fractional runaways are salvaged by quantize (verified); None/negative are safe; QUANTITY/UNIT_PRICE are floats (uncapped).
- **Sibling guard**: the three soft-validate stages above.
- **Solution**: null any money value exceeding 18 digits inside `_quantize_money` (degrades the row to RequiresReview, matching the existing salvage intent), or make the finalizer validate softly like every other stage. Test: a line with a 20-digit amount → run completes, row degraded.
- **Effort**: S | **Risk of fix**: none — strictly widens survivability.

### [P2-6] Pre-processing log and page manifest grow unbounded; every run re-downloads and re-groups full history several times
- **Status**: Confirmed (two independent finders)
- **Area**: `tasks/ocr_tax_invoice_pipeline`
- **Location**: `config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml:26` (no partition/retention); `helper/log_helper.py:13-33`; `module/log_exporter.py:100-131`; `retrieve_task.py:97-102`
- **Problem**: both CSVs are append-only forever; `latest_status_per_file` re-sorts the entire history and is invoked 3–4× per run (submit `_in_flight`, retrieve `_in_flight_jobs`, status-finalizer `_force_dead`/`_exclude_running`, finalize); every append rewrites the whole file under a single generation precondition with one retry — the longer the file, the wider the race window (aborts after 2 attempts, `log_exporter.py:118-122`). Impact: O(total-history) I/O + CPU per run, growing daily; contrast the batch-processing log's 3-month retention and the tracing log's month partition.
- **Solution**: month-partition or compact (archive terminal rows, keep in-flight + latest terminal per file); interim: compute `latest_status_per_file` once per run and pass it down.
- **Effort**: M | **Risk of fix**: compaction must preserve in-flight rows and the latest terminal row per file.
- **Sibling guard / Ruled out**: tracing retention sweep (`tracing_exporter.py:71`) is the in-repo pattern; correctness is fine at current volume — this is a scaling/perf finding.

### [P2-7] QA `{date}` prompt placeholder is clobbered after the first date — every later day in the lookback window gets the first day's "Current Date"
- **Status**: Confirmed
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/prep_payload_task.py:229-240` (`system_prompt.txt:5` proves `{date}` is load-bearing)
- **Problem**: `prompt_inbound/outbound` are built once, then **reassigned in place** inside `for date in processing_dates:` (`prompt_inbound = prompt_inbound.replace("{date}", date)`) — after iteration 1 the template no longer contains `{date}`, so dates 2..N keep date 1. Manifests whenever `QA_LOOKBACK_DAYS > 1`.
- **Ruled out**: single-date runs unaffected.
- **Sibling guard**: `fact_check_task.py:596` and `user_playground_task.py:596` do a single non-looping replace on a local.
- **Solution**: per-iteration local (`dated = prompt_inbound.replace(...)`); add a two-date test asserting distinct dates in payloads.
- **Effort**: S | **Risk of fix**: none.

### [P2-8] QA `problem_statement` uses a raw `", ".join(...)` — a null/scalar from Gemini turns the whole otherwise-good record into a FAILED row
- **Status**: Confirmed (a test proves the mechanism with a scalar)
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/export_output_result_task.py:1350-1352`
- **Problem**: every sibling field goes through `safe_cast_value` (never throws); this one joins the raw value. `problem_statement: null` (or scalar) → `TypeError` → the record-level `except` (`:1395`) writes the record as FAILED, discarding all its correctly-extracted fields.
- **Sibling guard**: `issue_type` at `:1347-1349` via `safe_cast_value`.
- **Solution**: `", ".join(str(x) for x in (get_value_by_path(...) or []))`. Test the null and scalar variants.
- **Effort**: S | **Risk of fix**: none.

### [P2-9] QA upload's multi-product loop is unguarded — one product's SharePoint error aborts all products and discards the in-memory control log
- **Status**: Confirmed
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/upload_voice_task.py:339-526` (single control write at `:528-534`)
- **Problem**: a non-retryable error (e.g. HTTP 500 re-raised at `sharepoint.py:474-476`) in one (datadate, folder) iteration propagates out of `execute_task` before the end-of-run control write, losing every product's stamped progress and the summary email. Missing/empty folders are handled; *errors* are not.
- **Sibling guard**: `prep_payload_task.py:243-295` wraps each iteration in `try/except continue`.
- **Solution**: wrap the per-(datadate, folder) body; stamp "N" + error remark and continue.
- **Effort**: M | **Risk of fix**: none — strictly more resilient.

### [P2-10] QA monthly master workbook is appended with no dedup — duplicates accumulate roughly ×lookback_days
- **Status**: Confirmed (main-session read of the concat/append; consequence follows from P2-14)
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/export_output_result_task.py:2258-2358` (`concat` at `:2294`, row-append at `:2300-2303`)
- **Problem**: because the pipeline re-scores the whole lookback window every run (P2-14), the same day's records are re-appended to the customer-facing monthly master on every subsequent daily run; no key-based dedup exists anywhere in the master path.
- **Sibling guard**: the daily path dedups on `["agent_id","call_id","phone_number","department"]` (`:492`) — the master path lacks the equivalent.
- **Solution**: dedup `combined_df` on the daily key (keep last) before writing, and rebuild the sheet from `combined_df` rather than blind-appending rows.
- **Effort**: S–M | **Risk of fix**: one-time visible shrink of an already-duplicated master; consider a cleanup pass.

### [P2-11] user_playground `group_reason` is used before assignment — first all-absent group crashes the step; later ones blank the previous group's reason column
- **Status**: Confirmed (main-session line verification)
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/user_playground_task.py:336-366`
- **Problem**: `group_reason = f"{group_name}_reason"` is assigned only inside `if existing_columns:` (`:354`); the else-branch uses it (`:363`) and it's appended to the schema regardless (`:366`). A `service_quality_group` whose items are all absent from the frame (user_config drift) → `NameError` on the first such group, or — worse — silently overwrites the **previous** group's populated reason column with `""` and appends a duplicate stale schema name.
- **Sibling guard**: the QA export copy hoists the assignment above the branch (`export_output_result_task.py:2394`) — user_playground is a divergent copy that lost the fix.
- **Solution**: hoist the assignment above the `if/else`. Test: group with zero matching columns.
- **Effort**: S | **Risk of fix**: none.

### [P2-12] An all-failed batch crashes the export on `.dt.date`, masking the real "everything failed" signal (telesale and QA)
- **Status**: Confirmed (line verified; the all-None premise cross-traced to Vertex `processed_time` semantics)
- **Area**: `tasks/sentiment_telesale`, `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_telesale/export_output_result_task.py:1436`; `tasks/sentiment_qa/export_output_result_task.py:2050`
- **Problem**: `processed_time` exists only on SUCCESS lines; when every record failed, `start_time` is all-None → object dtype → `.dt.date` raises `AttributeError` *outside* the surrounding try — after the prediction .txt, transaction CSV, and GCS archival already committed. The task false-fails with an unrelated error, skipping the performance log and the AI-operation summary exactly when operators most need the real signal.
- **Sibling guard**: the same class's `post_execute` (`telesale:1648-1655`) converts with `pd.to_datetime(errors="coerce")` first.
- **Solution**: `pd.to_datetime(col, errors="coerce").dt.date.dropna().unique().tolist()` at both sites. Test: all-FAILED batch completes and uploads the failure report.
- **Effort**: S | **Risk of fix**: none.

### [P2-13] Crosssell combo validator discards the whole evaluation (narrower trigger than P1-6)
- **Status**: Confirmed (main-session line verification)
- **Area**: `tasks/sentiment_telesale/output_validation`
- **Location**: `tasks/sentiment_telesale/output_validation/common/sales_effectiveness.py:113-127`
- **Problem**: `missed_crosssell_upsell` truthy with either sub-field `None` raises (`:121-126`) → whole record FAILED+skipped, same discard path as P1-5/P1-6.
- **Sibling guard**: the `None`/`False` arms (`:115-120`) normalize.
- **Solution**: coerce the missing sub-field (e.g. `False`) with a warning instead of raising.
- **Effort**: S | **Risk of fix**: low.

### [P2-14] Telesale/QA GetBatchResult re-retrieves and re-scores every predictions file in the lookback window on every run
- **Status**: Confirmed (re-processing); downstream duplication depends on the consumer (master path: see P2-10)
- **Area**: `tasks/sentiment_telesale`, `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_telesale/get_batch_result_task.py:133-220`; `tasks/sentiment_qa/get_batch_result_task.py:138-231`
- **Problem**: the task scans `[end−lookback, end]` in GCS and processes **all** `predictions.jsonl` found, with no record of already-retrieved batches — the same batch is re-scored for `lookback_days` consecutive runs (wasted Gemini-cost accounting, log noise, and duplicate-row pressure on every append-style consumer). Also: a batch landing predictions later than `lookback_days` is missed forever.
- **Ruled out**: dead/cancelled jobs write no predictions → simply never discovered (no poll of this log).
- **Sibling guard**: none in the retrieve task.
- **Solution**: record processed batch URIs (marker file or batch_processing_log) and skip them; size `lookback_days` above worst-case batch latency.
- **Effort**: M | **Risk of fix**: confirm every consumer's append/overwrite semantics first.

### [P2-15] Telesale/QA `batch_processing_log`: rows orphaned when the post-submit status check fails; the "dedup" is a no-op; writes are last-writer-wins
- **Status**: Confirmed
- **Area**: `tasks/sentiment_telesale`, `tasks/sentiment_qa`, `src/modules/audit_log`
- **Location**: `tasks/sentiment_telesale/execute_batch_job_task.py:122-193,214-259`; `tasks/sentiment_qa/execute_batch_job_task.py:142-213,280-297`
- **Problem**: the log row is written only in `post_execute`; if `status_check` raises after `create_batch_job` succeeded, the task raises → `post_execute` skipped (`task_interface.py:152-164`) → a live billable job with no audit row (results still recoverable — retrieval is GCS-scan — so audit/cost-tracking gap, not data loss). The writer logs "before deduplication" but never calls `drop_duplicates`, and it's a plain read-concat-upload with no concurrency guard (same class of defect as P2-1).
- **Sibling guard**: the OCR pre-processing log (generation-guarded).
- **Solution**: write the row around job creation (or in `on_error`); add real dedup on `batch_job_id`; use the generation-guarded GCS write path.
- **Effort**: S–M | **Risk of fix**: low.

### [P2-16] `cal_gemini_cost` mutates the caller's `usage_detail` in place — and one caller passes a live reference into its own records
- **Status**: Confirmed (mutation + aliasing; a realized wrong output today was not found — the other two callers are safe by ordering/fresh dicts)
- **Area**: `src/modules/google`
- **Location**: `src/modules/google/gemini_batch.py:562-599`; caller alias at `tasks/sentiment_telesale/export_output_result_task.py:477,483` via `get_value_by_path` (`src/utils/common.py:187` returns the live object)
- **Problem**: the cache-deduction loop writes `usage["token_input"]["text"] = max(0, …)` back into the caller's nested dict: calling twice double-deducts (silent cost undercount), and the telesale caller's record (`rec["prediction"]["token_input"]`) is permanently reduced after the cost call — a trap for any later reader or retry.
- **Ruled out**: `export_output_result_task.py:1276-1298` sums before calling (safe by accident); the tax caller feeds a fresh dict.
- **Sibling guard**: none; no test asserts input preservation (confirmed by the module-test coverage pass).
- **Solution**: operate on a local `dict(usage.get("token_input", {}))` copy; never write back. Regression test: input unchanged; two consecutive calls identical.
- **Effort**: S | **Risk of fix**: `evaluation_output_task.py:1633` comments that deduction happened here — verify that display path after the fix.

### [P2-17] A new `storage.Client` is constructed for every single-item GCS operation — per-call ADC/token resolution inside per-record loops
- **Status**: Confirmed
- **Area**: `src/modules/google`
- **Location**: `src/modules/google/gcs.py:206-207,255-256,302-303,355-356,413-414,447-448,488-489,526-527,558-559,693-694` (+ validate-and-discard at `:72`)
- **Problem**: each call re-runs `google.auth.default()` and lazily refetches tokens (metadata-server round-trips on Cloud Run). Confirmed loops: telesale export deletes/moves per record (`:1044,1057,1115`), evaluation output (`:1326,1344`), QA export (`:1603,1621,1681`), reconcile `iqs_rejecter.py:71` per rejected page. Impact: one extra client construction + potential token fetch per record — hundreds per daily run.
- **Sibling guard**: the async batch methods hoist one client (`gcs.py:753-754,841-842,941-942,1039-1040`).
- **Solution**: lazily cache `self._client`/`self._bucket` on first use; update tests that count constructions.
- **Effort**: S | **Risk of fix**: credential errors surface on first use instead of per call.

### [P2-18] Local-mode logger attaches one RotatingFileHandler per module to the same `logs/app.log` — root cause of the known WinError 32 rotation flake
- **Status**: Confirmed
- **Area**: `src/utils`
- **Location**: `src/utils/logger.py:144-159,222-224`
- **Problem**: each module-level `Logger(__name__)` in local mode creates its own `RotatingFileHandler` on the same file; at the 10 MB cap one handler rotates while dozens still hold the old file open → `PermissionError: WinError 32` on Windows, interleaving/lost lines elsewhere.
- **Sibling guard**: prod mode funnels everything into one `LOG_NAME` logger (`:165-166`).
- **Solution**: configure console+file handlers once on a single shared logger (class-level flag), let named loggers propagate; keep `%(name)s` in the dev format. Test: two Logger instances → exactly one file handler.
- **Effort**: S | **Risk of fix**: propagation misconfig can double-log — assert handler count.

### [P2-19] Fact-check text normalization is weaker than reconcile's — Thai values the pipeline treats as equal are scored as mismatches
- **Status**: Confirmed (empirically verified: ZWSP survives `\s+`)
- **Area**: `tasks/tax_invoice_fact_check`
- **Location**: `tasks/tax_invoice_fact_check/module/value_normalizer.py:68-71` vs `tasks/tax_invoice_reconcile/helper/sql_normalize.py:22-36`
- **Problem**: `_normalize_text` only collapses `\s+` and casefolds; reconcile's `norm_text_sql` also strips zero-width characters (ZWSP/ZWNJ/ZWJ/BOM) and applies NFC. An invisible codepoint or Thai composition difference between human GT and model output produces an FP the rest of the pipeline would not.
- **Sibling guard**: `sql_normalize.py:22-36`.
- **Solution**: NFC-normalize + strip the zero-width set before casefolding; add a Thai zero-width/NFC test.
- **Effort**: S | **Risk of fix**: aligns the metric with the pipeline's own match semantics.

### [P2-20] Fact-check inner join silently drops GT documents that produced no extraction rows — metrics can read 100% while whole documents failed; a zero-match run emits nothing and still finalizes SUCCESS
- **Status**: Confirmed (join behavior; the zero-row-extraction reachability is exactly P1-3's trigger family)
- **Area**: `tasks/tax_invoice_fact_check`
- **Location**: `tasks/tax_invoice_fact_check/module/fact_check_evaluator.py:78-81,94-103`; `module/fact_check_log_emitter.py:40-42`
- **Problem**: the denominator is "docs matched by filename", not "docs in GT"; a GT doc with no extraction row vanishes from the metric. In the extreme (filename drift), `evaluate` returns `[]`, the emitter no-ops, the task returns normally and `OCRFinalizeTask` stamps SUCCESS — a silent zero-metric run.
- **Ruled out**: docs failing *with* a FAILED row join and score FP (redacted rows keep identity).
- **Sibling guard**: none for a published metric (reconcile's analogous count only feeds a notification).
- **Solution**: emit a coverage row — count of GT keys with no extraction match — and warn/fail when matched==0. Test: GT doc absent from proc ⇒ surfaced.
- **Effort**: M | **Risk of fix**: low; additive metric.

### [P2-21] Fact-check GT date cells stored as real Excel dates render as `"YYYY-MM-DD 00:00:00"` and fail all four parse formats → every date scored FP
- **Status**: Suspected (the exact `dtype=str` rendering of a date-typed cell was not reproduced against a real workbook)
- **Area**: `tasks/tax_invoice_fact_check`
- **Location**: `tasks/tax_invoice_fact_check/module/ground_truth_loader.py:52-55`; `module/value_normalizer.py:89-99`
- **Problem**: `_DATE_INPUT_FORMATS` has no time-suffixed pattern, so a date-typed GT cell → `NA_SENTINEL` vs the extraction's `"YYYY-MM-DD"` → systematic mismatch on the date metric.
- **Sibling guard**: the Z45 loader does dual date-cell parsing for exactly this reason (`report_source_loader.py:80-91`, `schema/z45_input.py`; project-documented).
- **Solution**: strip a trailing `" 00:00:00"` or add a format-first-then-general fallback (keep `%d/%m/%Y` priority to avoid month-first misreads). Test the time-suffixed string.
- **Effort**: S | **Risk of fix**: parse-order care (documented Z45 lesson).

### [P2-22] Telesale `upload_cond` with 2+ entries progressively empties the agent frame — later conditions silently upload nothing (latent)
- **Status**: Confirmed (dormant: prod config is single-entry)
- **Area**: `tasks/sentiment_telesale`
- **Location**: `tasks/sentiment_telesale/upload_voice_task.py:302-308` (config `telesale_pipeline_tasks.yml:56`)
- **Problem**: the loop reassigns `agent_list_df` to its own filtered subset per condition, so iteration 2 filters skill-1's survivors by skill-2 → empty agent list → those files skipped without a trace. The code's own docstring example (three conditions) would break.
- **Sibling guard**: none.
- **Solution**: filter a fresh subset per condition (`sub = agent_list_df[...]`). Test: two conditions, both lists non-empty.
- **Effort**: S | **Risk of fix**: none.

### [P2-23] Telesale PrepPayload re-downloads and re-parses the prompt + checklist Excel from SharePoint once per lookback date
- **Status**: Confirmed
- **Area**: `tasks/sentiment_telesale`
- **Location**: `tasks/sentiment_telesale/prep_payload_task.py:290` (inside the date loop at `:228`; backup zip re-upload `:296-305`)
- **Problem**: `_mapping_prompt_template` re-fetches `0_common_prompt.txt` + `check_list_prompt.xlsx`, re-parses 3 sheets, and rebuilds all skill prompts per date-with-files; the content is date-independent. Impact: up to `lookback_days` × (2 SharePoint downloads + Excel parse + zip upload) per run on backfills.
- **Solution**: hoist load/build (and the zip upload) out of the loop; keep the per-date file→prompt join and the `_cache_literal_checklist`/`validate_mapping` ordering (`:310-315`).
- **Effort**: M | **Risk of fix**: dynamic Literal-model rebuild ordering.

### [P2-24] QA daily export re-downloads `user_config.xlsx` twice per day-in-month per run
- **Status**: Confirmed
- **Area**: `tasks/sentiment_qa`
- **Location**: `tasks/sentiment_qa/export_output_result_task.py:2368-2376` (called from `:510-511` in the day loop)
- **Problem**: 2N identical SharePoint downloads + parses of the same control file per run (N = days in month); `_format_output:615-620` already shows the read-once pattern.
- **Solution**: fetch/parse `service_quality_group` once, pass it in or memoize on `self` (keep the local fallback).
- **Effort**: S | **Risk of fix**: none.

### [P2-25] `SharePointModule.rename_file` bypasses the TLS-pinned session — and the test fixture makes the divergence structurally invisible
- **Status**: Confirmed
- **Area**: `src/modules/microsoft`
- **Location**: `src/modules/microsoft/sharepoint.py:750,784,808,811` vs `self._session` everywhere else; mask at `tests/modules/conftest.py:6-25`
- **Problem**: `rename_file` is the only network method using bare `requests.*`, skipping the `TlsPolicy` session (TLS ≥1.2 floor + pooling, `tls.py:38-87`). The autouse fixture reroutes `TlsPolicy().session()` to the `requests` module in tests, so `self._session.patch` and `requests.patch` are the identical mock — no test arrangement can catch the divergence.
- **Sibling guard**: the seven sibling methods.
- **Solution**: switch the four call sites to `self._session.*`; add an assertion that `rename_file` used `module._session`.
- **Effort**: S | **Risk of fix**: none (transport only).

### [P2-26] `CoreEngine.run()` can never return False; the failure-summary branch is unreachable and three comments invite breaking the fail-fast invariant
- **Status**: Confirmed
- **Area**: `src/core`
- **Location**: `src/core/engine.py:175-199` (dead consumer `main.py:90-92`)
- **Problem**: all three `except` arms re-raise, so the `failed_tasks`/`return False` block is dead and the `-> bool` contract is never fulfilled — correct behavior today only via the exception path. The three `# Don't raise, continue to next task` comments (`:178,182,186`) contradict the code; "restoring" them would silently break the OCR finalize-last data-safety invariant (finalize must not run after a failed business task).
- **Sibling guard**: `tests/core/test_engine.py:304-316` tests the dead branch by exec-ing a hand-copied snippet — evidence the unreachability is known.
- **Solution**: delete the dead accounting + comments; document fail-fast; add a real engine test asserting a mid-pipeline failure aborts subsequent tasks.
- **Effort**: S | **Risk of fix**: none if fail-fast retained.

### [P2-27] `--start_data_dt`/`--end_data_dt` are silently ignored by the telesale pipeline; `main.py` never validates flag combinations
- **Status**: Confirmed (mechanism; downgrade to doc-fix if telesale backfill is explicitly out of scope)
- **Area**: `main.py` / `tasks/sentiment_telesale`
- **Location**: `main.py:72-85`; consumers: only QA (`upload_voice_task.py:172-173`, `prep_payload_task.py:157-158`) and OCR (`submit_task.py:110-121,230-235`) read the range keys — zero telesale hits
- **Problem**: an operator running a telesale backfill with `--start/--end` gets a silent no-op (today's date processed instead); contradictory combos (`--rerun` + `--start`, start without end, start>end) also pass `main.py` unchecked for every pipeline.
- **Sibling guard**: `src/utils/date_utils.py:185-232` `resolve_data_date_window` enforces exactly these combos — OCR calls it; `main.py` and telesale don't.
- **Solution**: call `resolve_data_date_window` once in `main.py` after the format checks (fail-fast for all pipelines); implement or explicitly reject range flags for telesale.
- **Effort**: M | **Risk of fix**: rejects previously-silently-ignored flag combos.

### [P2-28] Coverage-theater tests: exec'd source snippets, ctypes locals injection, and direct coverage-data mutation
- **Status**: Confirmed (three independent sightings)
- **Area**: `tests/`
- **Location**: `tests/core/test_engine.py:13-22,290-316` (exec of hand-copied engine snippets with hardcoded line offsets); `tests/utils/test_common.py` `TestCommonCoverageTraces` + `tests/utils/test_pydantic_utils.py` (`_compile_*` + `ctypes.PyFrame_LocalsToFast` on detached source strings, stale line anchors after this branch's edits); `tests/test_tasks/sentiment_qa/test_fact_check_task.py:1404-1451` (`sys.settrace` + `coverage.Coverage.current().get_data().add_lines(...)` — literally writes coverage records)
- **Problem**: these pass regardless of the real code's behavior and permanently inflate coverage on critical paths (engine failure handling, `common.py` internals, fact-check archival) — the exact places this review found live bugs.
- **Solution**: replace with direct calls to the real functions (the engine's dead branch first needs P2-26); delete the coverage-data mutation outright.
- **Effort**: M | **Risk of fix**: coverage numbers will drop — that is the point.

### [P2-29] GCS test suite: the Windows-path conversion tests and the concurrency-limit test cannot fail
- **Status**: Confirmed
- **Area**: `tests/modules`
- **Location**: `tests/modules/test_gcs.py:80-92` (no-op test: nothing called, nothing asserted), `:158-176,220-239,924-938` (assert only mock-fixed lengths), `:1384-1407` (asserts a two-backslash literal never present, inside a skippable `if`)
- **Problem**: the `.replace("\\","/")` conversions (`gcs.py:247-248,294-295,598-599`) could be deleted and all four tests stay green; the semaphore limit (`gcs.py:104-105,115`) is asserted nowhere. This repo is developed on win32 — the untested conversions are load-bearing.
- **Solution**: assert `list_blobs`/`blob` called with the converted prefix, unconditionally; replace the no-op with a high-water-mark concurrency assertion or delete it.
- **Effort**: S | **Risk of fix**: none.

### [P2-30] Reconcile suite: the load-bearing exception swallow and the source-deleting orchestration wiring are untested
- **Status**: Confirmed
- **Area**: `tests/test_tasks/tax_invoice_reconcile`
- **Location**: `test_suspicious_reject.py:36-80` (never forces `_copy_page` to raise — that swallow at `source_rejecter.py:98-99` is what keeps a post-archive failure from stranding files); `test_reconcile_contract.py:94-149` (mocks all collaborators, asserts only the returned object — `archive_invoices`/`archive_z45`/`reject_suspicious` args unasserted)
- **Problem**: a regression that removes the swallow or swaps the archived frame surfaces only in production, after sources are deleted.
- **Sibling guard**: `test_iqs_rejecter.py:232,241` and `test_source_archiver.py:84` test their swallows; `test_reconcile_contract.py:226` asserts ExportLogging kwargs.
- **Solution**: add swallow tests mirroring IqsRejecter's; assert collaborator call args in the normal-path test.
- **Effort**: S | **Risk of fix**: none.

### [P2-31] CI runs the suite only on Python 3.12; every production image ships 3.11
- **Status**: Confirmed
- **Area**: `.github/workflows`
- **Location**: `.github/workflows/unit_testing.yml:39` vs `cloud_build/*/Dockerfile:2,19` (3.11.9 ×2, 3.11.14)
- **Problem**: zero CI coverage of the shipped runtime (and the three images don't even agree on the 3.11 patch).
- **Solution**: matrix `["3.11", "3.12"]` (or at minimum 3.11); align the base-image patch versions.
- **Effort**: S | **Risk of fix**: low.

### [P2-32] Production image defects: dev dependencies shipped in all three images; CVE patching and file-ownership hardening applied inconsistently
- **Status**: Confirmed
- **Area**: `cloud_build`
- **Location**: all three `Dockerfile:16` (`uv sync` without `--no-dev` → ruff + pre-commit + transitives in prod venvs); `tax_invoice_extraction/Dockerfile:29-34` (apt/pip CVE patch present only there, QA/telesale on older unpatched 3.11.9-slim); `tax_invoice_extraction/Dockerfile:43-47` `COPY --chown=appuser` (runtime user owns/can rewrite its own code — on the image that parses untrusted PDFs) vs siblings' `root:root`
- **Solution**: add `--no-dev` ×3; backport the CVE patch block (or re-scan); change tax's `--chown` to `root:root`. Reconcile all three Dockerfiles against one template.
- **Effort**: S | **Risk of fix**: none — nothing writes to its own source tree.

### [P2-33] Tax scheduler cron says Saturdays-only; its description says daily — one of them is wrong
- **Status**: Suspected (which side is wrong is a business question)
- **Area**: `terraform/projects/tax_invoice_extraction`
- **Location**: `main.tf:134-135` (`description = "…every day at 9 AM"`, `schedule = "0 9 * * 6"`)
- **Problem**: if the cron is the mistake, the ingest pipeline runs 1/7 of the intended cadence; if the description is the mistake, dashboards/docs mislead operators.
- **Solution**: confirm the intended cadence with the owner and align both.
- **Effort**: S | **Risk of fix**: none.

---

### [P3-1] Batch-straddle: only the first submission is logged per file, and the finalizer join requires the logged job name (latent at the 100k default)
- **Status**: Confirmed mechanism; latent (default `line_limit` 100,000 — `submit_task.py:47`, `payload_builder.py:15`; no config override)
- **Location**: `tasks/ocr_tax_invoice_pipeline/module/pre_log_builder.py:95`; `module/result_finalizer.py:107`
- **Problem**: a file whose pages straddle two batch jobs gets a pre-log row for job A only; job B is never polled and its pages fail the job-name join → dropped → premature SUCCESS. Somebody tuning `batch_job_limit` down (e.g. for testing) hits this immediately.
- **Solution**: emit one PENDING row per spanned job in `pre_log_builder`; dropping the redundant join predicate is safe (child paths are run-unique) but insufficient alone.
- **Effort**: M | **Risk of fix**: log-row cardinality changes. | **Sibling guard**: none.

### [P3-2] `resolve_date` `Y` offset crashes on a Feb-29 base date
- **Status**: Confirmed (no config currently uses `Y` offsets)
- **Location**: `src/utils/common.py:74` — `.replace(year=…)`; the `M` path uses day-clamping `add_months` (`date_utils.py:26-42`)
- **Solution**: route `Y` through `add_months(val*12)`. Test `datetime(2024,2,29)` + `+1Y`. **Effort**: S.

### [P3-3] `token_utils` divides by an explicit `tokens: 0` cost-config row
- **Status**: Confirmed | **Location**: `src/utils/token_utils.py:62,74,98`
- **Problem**: default `1` applies only when the key is absent; an explicit 0 raises ZeroDivisionError, aborting cost logging. **Solution**: guard the divisor. **Effort**: S.

### [P3-4] `parse_datetime` mislocalizes naive inputs via `.astimezone()` (host-timezone dependent; latent — current callers pass aware values)
- **Status**: Suspected (no current caller produces naive values) | **Location**: `src/utils/date_utils.py:71-77`
- **Solution**: branch on `tzinfo`: naive → `replace(tzinfo=…)`, aware → `astimezone(…)`. **Effort**: S.

### [P3-5] `pdf_utils.extract_single_page` leaks the pdfium writer handle on the error path
- **Status**: Confirmed | **Location**: `src/utils/pdf_utils.py:26` (`writer.close()` not in `finally`; only `doc.close()` is)
- **Solution**: close the writer in `finally`. **Effort**: S.

### [P3-6] Throttling gaps: SharePoint never retries HTTP 429; msgraph retries 429 but ignores `Retry-After`
- **Status**: Confirmed | **Location**: `src/modules/microsoft/sharepoint.py:202-224` (only 401/503); `src/modules/microsoft/msgraph.py:29,112-119` (fixed sleep)
- **Problem**: large concurrent uploads make Graph 429s realistic; a throttled download aborts that file for the run (self-heals next run via dedupe → P3, not higher). **Solution**: add 429 to SharePoint's retryable set; honor `Retry-After` (capped) in both. **Effort**: M.

### [P3-7] `retrieve_batch_results` decodes the whole predictions file before the per-line guard — one invalid byte discards the job's results
- **Status**: Confirmed | **Location**: `src/modules/google/gemini_batch.py:266` (guarded per-line path at `:273-282` never gets the chance)
- **Solution**: decode with `errors="replace"` (the per-line JSON guard already handles a mangled line). **Effort**: S.

### [P3-8] Partial Gemini API key written to DEBUG logs
- **Status**: Confirmed | **Location**: `src/modules/google/gemini_batch.py:60` (first 10 chars)
- **Solution**: log only that key auth is configured. **Effort**: S.

### [P3-9] `resolve_job_name` rejects every string input, contradicting its signature; the full-path branch is dead (no production callers)
- **Status**: Confirmed | **Location**: `src/modules/google/gemini_batch.py:122-129`
- **Solution**: fix the inversion or delete the method. **Effort**: S.

### [P3-10] `GCSModule.move_file` is copy-then-delete — a delete failure strands the object in both locations
- **Status**: Confirmed | **Location**: `src/modules/google/gcs.py:458-459`
- **Solution**: log loudly + surface in the summary when the delete half fails (callers may re-process the stranded source). **Effort**: S.

### [P3-11] A duplicated top-level task key in a pipeline YAML silently last-wins
- **Status**: Confirmed | **Location**: `src/utils/file_utils.py:49` (`yaml.safe_load`) consumed by `engine.py:132`
- **Solution**: document the ceiling, or parse with a duplicate-key-rejecting loader. **Effort**: S | **Risk**: strict loader may reject configs relying on last-wins.

### [P3-12] `config/common.yml` loaded CWD-relative; `get_current_datetime()` re-reads it from disk on every call (including eagerly-evaluated default args)
- **Status**: Confirmed | **Location**: `src/core/engine.py:31,48`; `src/utils/date_utils.py:141`; eager default e.g. `telesale/upload_voice_task.py:258`
- **Solution**: anchor to the repo root; cache the parsed config. **Effort**: S.

### [P3-13] QA batch-log retention silently purges rows with unparseable `updated_dt`
- **Status**: Confirmed | **Location**: `tasks/sentiment_qa/execute_batch_job_task.py:256-260` (`NaT >= cutoff` is False)
- **Problem**: dropped rows shrink the FAILED-file recovery set (`export_output_result_task.py:1424-1446`). **Solution**: keep NaT rows (`isna() | >= cutoff`). **Effort**: S.

### [P3-14] Fact-check "weighted_avg" is an unweighted mean with degenerate weights; recall/f1 statuses are dead under the FN=TN=0 convention (QA and telesale)
- **Status**: Confirmed (the confusion-matrix convention itself is the house standard — this is about the derived aggregate)
- **Location**: `tasks/sentiment_qa/fact_check_task.py:1018-1046,1107`; `tasks/sentiment_telesale/fact_check_task.py:1310-1337,1384-1385,1399`
- **Problem**: weight = TP+FN = TP (FN fixed 0) → all-wrong labels drop out of the aggregate and telesale's version biases upward; recall is always 100/0 so its thresholds never fire.
- **Solution**: weight by TP+FP (constant denominator) or report accuracy-only; coordinate with metric consumers. **Effort**: S.

### [P3-15] QA fact-check: unmatched-file sets computed and discarded; a zero-match join emits an all-zero report instead of an error
- **Status**: Confirmed | **Location**: `tasks/sentiment_qa/fact_check_task.py:932-933` (dead statements), join at `:903,919,927`
- **Solution**: log/report the unmatched sets; guard `len(merged)==0` loudly. (Tax-side equivalent is P2-20.) **Effort**: S.

### [P3-16] QA upload: `before_filter_count` is always 0 and the "Y" branch logs the opposite of reality
- **Status**: Confirmed | **Location**: `tasks/sentiment_qa/upload_voice_task.py:414,445-446`
- **Solution**: drop the variable, use `if filtered_voice_files:`, fix the message. **Effort**: S.

### [P3-17] QA fact-check reads the GT Excel without `dtype=str` — numeric-looking IDs lose leading zeros and silently fall out of the join
- **Status**: Confirmed | **Location**: `tasks/sentiment_qa/fact_check_task.py:914` (`str(x)` at `:918-919` yields `"12345"`/`"12345.0"` vs pred `"0012345"`)
- **Sibling guard**: the tax GT loader uses `dtype=str` (`ground_truth_loader.py:55`); the buyer-master lpad convention is project-documented.
- **Solution**: `dtype=str` on the read. **Effort**: S.

### [P3-18] QA export caches a loop-leaked DataFrame for AI-operation logging
- **Status**: Suspected (consumer contract confirmed via the sibling; QA's own post_execute not re-read)
- **Location**: `tasks/sentiment_qa/export_output_result_task.py:2048` (`combined_df` = last date's historical+new; `:2049-2053` correctly uses `new_transaction_df`)
- **Problem**: Splunk AI-op counts computed from one date's partition, double-counting historical rows. **Solution**: cache `new_transaction_df`. **Effort**: S.

### [P3-19] Tax fact-check GT is not de-duplicated before the join — duplicate GT file names fan out and double-count
- **Status**: Confirmed (code; needs a duplicated GT key to matter) | **Location**: `tasks/tax_invoice_fact_check/module/fact_check_evaluator.py:98-103` (proc deduped, gt not)
- **Solution**: assert GT file-key uniqueness in the loader, fail loudly. **Effort**: S.

### [P3-20] Reconcile hygiene: `schema/` has no `__init__.py` (namespace-package accident); two DuckDB builders never close their connections
- **Status**: Confirmed | **Location**: `tasks/tax_invoice_reconcile/schema/` (sibling OCR package has one); `extraction_report_builder.py:56-60`, `reconciliation_builder.py:64-84` (vs `output_exporter.py:105-111` which closes)
- **Solution**: add the empty `__init__.py`; wrap connections in try/finally. **Effort**: S.

### [P3-21] OCR `domain` and retrieve's `vertexai.project_id` are never validated — silent `""` stamped into every log/tracing row
- **Status**: Confirmed | **Location**: `helper/task_context.py:74`; absent from `REQUIRED_STRING_KEYS` (submit `:53-76`, retrieve `:51-59`, finalize `:38-42`); consumed at `pre_log_builder.py:40`, `tracing_builder.py:47-48`
- **Solution**: add both to the required-keys validation. **Effort**: S.

### [P3-22] `CampaignRatio` bounds are commented out and no sum-to-1 validator exists, contradicting the field descriptions
- **Status**: Confirmed | **Location**: `tasks/sentiment_telesale/output_validation/common/campaign_ratio.py:9-17,21-25`
- **Solution**: enforce (clamp, don't raise — see P1-5) or fix the descriptions. **Effort**: S.

### [P3-23] `tests/test_utils/` is a stale subset duplicate of `tests/utils/` — both run, neither collides, one is dead weight
- **Status**: Confirmed | **Location**: `tests/test_utils/test_common.py`, `test_date_utils.py` (missing the newer coverage in `tests/utils/` twins)
- **Solution**: delete the `tests/test_utils/` duplicates (keep its unique `test_duckdb_utils/test_image_utils/test_pdf_utils`). **Effort**: S.

### [P3-24] Test-hygiene bundle: tautological/weak asserts and drifted fixtures in module + OCR suites
- **Status**: Confirmed | **Location**: `tests/modules/test_gemini_batch.py:291-303` (asserts a key that can never exist), `:167-187` (asserts only not-None); `tests/modules/test_gcs.py:1381-1382` (stale claims a fixed bug + missing test that exists at `:1742-1754`); `tests/test_tasks/ocr_pipeline/test_status_finalizer.py:12-32` + `test_finalize_task.py:24-44` (hand-copied 19-column fixture vs the real 20-column schema — derive from `PreProcessingLogSchema`); `tests/test_tasks/ocr_pipeline/test_result_retriever.py:25-31` (auto-Mock timestamps on the failed-job path)
- **Solution**: as noted per site. **Effort**: S each.

### [P3-25] Failed-file identity lost when the batch log's `error_message` is empty (NaN defeats the None-guard) — telesale and QA
- **Status**: Confirmed | **Location**: `tasks/sentiment_qa/export_output_result_task.py:1454-1483`; `tasks/sentiment_telesale/export_output_result_task.py:883-912`
- **Problem**: `read_csv` yields truthy `float('nan')` for the empty cell → `.replace()` raises AttributeError → the inner except substitutes a row with `filename: None` and a generic error; the file's identity vanishes from both the row and the log. (QA's substitute row is additionally dropped by the month grouping; telesale's survives into output.)
- **Solution**: `keep_default_na=False` on the read, or a `pd.notna` guard like the `_transaction_log` siblings. **Effort**: S.

### [P3-26] Post-pipeline order documentation is wrong everywhere except the test that pins it
- **Status**: Confirmed (main-session read of the YAML)
- **Location**: actual order `ReconcilePrecheckTask(:25) → OCRRetrieveTask(:53) → ReconcileTask(:84) → OCRFinalizeTask(:135)` vs the YAML's own header (`ocr_pipeline_post_tasks.yml:4-15`), CLAUDE.md's OCR section, the precheck docstring, the reconcile README (`:28-39`), and the test *name* `test_post_config_runs_retrieve_then_business_then_finalize_last` (`test_pipeline_config.py:30-33` — its assertion pins the correct precheck-first order)
- **Problem**: precheck-first is load-bearing (precheck returns None; placed after retrieve it would blank the OCRResult). Every prose description invites a "fix" that would break the pipeline; only the test stands in the way, under a misleading name.
- **Solution**: correct the YAML header, CLAUDE.md, README, and docstring; rename the test and add the one-line rationale. **Effort**: S.

### [P3-27] `.claude/rules` drift: python-style mandates `%s` logger calls that crash the custom Logger; yaml-config documents a config schema that doesn't exist
- **Status**: Confirmed (Logger signature verified: `info(self, message, **kwargs)` — a positional arg raises TypeError)
- **Location**: `.claude/rules/python-style.md:77,87` (and its error-handling example); `.claude/rules/yaml-config.md` (`tasks:`-list wrapper vs the real top-level-key schema)
- **Solution**: fix both rule files to match reality (f-strings; top-level task keys). **Effort**: S.

### [P3-28] README/output drift: terraform README claims fact-check resources are commented out (they're live); cloud_build README claims the env template is empty (it's 68 lines); scheduler_schedule outputs are stale copy-paste
- **Status**: Confirmed | **Location**: `terraform/projects/tax_invoice_extraction/README.md:78,128,151` vs `main.tf:206-360`; `cloud_build/tax_invoice_extraction/README.md:12-16`; `sentiment_qa/outputs.tf:57` + `tax_invoice_extraction/outputs.tf:57` (both echo telesale's schedule text)
- **Problem**: the terraform README actively invites dismissing P1-14/15 as moot.
- **Solution**: update all three; interpolate real schedules instead of hardcoding. **Effort**: S.

### [P3-29] Env-template drift bundle
- **Status**: Confirmed | **Location**: `cloud_build/sentiment_telesale/.env.example` missing `IS_MONITORING_ENABLED` + `TELESALE_RAW_PREDICTION_PATH`; `cloud_build/sentiment_qa/.env.example` missing `QA_MASTER_PATH`; root `.env.example:21-36` carries half of the tax product's vars (breaks the root-file discipline of the other products); `cloud_build/tax_invoice_extraction/.env.example:44-45` declares `TAX_INVOICE_FACT_CHECK_PATH` twice with contradictory comments — and the "legacy" framing is wrong: the live fact-check configs actively read that name (`ocr_pipeline_fact_check_pre_tasks.yml:45` + post), so whoever populates the secret must set the real fact-check folder
- **Solution**: add the missing lines; move tax product vars into the product template; delete the duplicate line and correct the comment; alert the secret-value owner that the name is live. **Effort**: S.

### [P3-30] CI/tooling nits bundle
- **Status**: Confirmed | **Location**: tfsec doubly non-blocking and telesale-only (`terraform_pr_validation.yml:216-222`); coverage gate floored at 0 in both CI and pyproject (`unit_testing.yml:21,57`, `pyproject.toml:164`) — advisory-only by construction; `auto_format.py` is a PostToolUse hook never registered (`settings.json` has no PostToolUse; contained by pre-commit); `ghcr.io/astral-sh/uv:latest` mutable tag in all three Dockerfiles; tax Dockerfile missing `--no-build` (siblings have it); `.pre-commit-config.yaml:14` exclude `\.yaml$` never matches the actual `.yml` files; `checkout@v6` vs `@v4` skew between sibling workflows
- **Solution**: one tidy pass; each item is a one-liner. **Effort**: S.

### [P3-31] SharePoint mirrors of the OCR logs and the tracing log are plain overwrites — human-facing copies can drift under concurrent runs (GCS remains authoritative)
- **Status**: Confirmed | **Location**: `tasks/ocr_tax_invoice_pipeline/module/log_exporter.py:95` (mirror), `tracing_exporter.py:58`
- **Solution**: accept (document GCS as authoritative) or reuse the guarded write for the mirror. **Effort**: S.

## Observations

Intentional or by-design behaviors verified during the review (not findings):

- **Design invariants held**: finalize-last recovery; single poll per retrieve run; PENDING/PARTIAL-only dedupe; per-bucket GCS routing; no domain validation in the generic OCR package; lenient `ReceiptExtraction` gate; strict never-sum of split VAT; 5-scenario VAT hard-key; Z45 dual date parse; duplicate Z45 output headers; all-string report frame; SharePoint-source FILE_PATH; Thai printed commas; in-house IQS (inclusive `>=` threshold verified).
- The engine treats "task returned" as success — the OCR chain's safety rests on tasks raising (not returning None) on failure. Holds today; worth keeping in mind for new tasks.
- `resolve_env` substitutes `""` for missing env vars silently (`common.py:114`) — this is what turns provisioning gaps (P1-19) into silently-wrong paths; the regex is also uppercase-only, so a lowercase `${var}` passes through verbatim.
- The commit b6fc1ef multi-invoice voucher logic was independently re-derived against `reconciliation.sql` and is **sound** (scenario-3 sums and scenario-5 header-dedup produce exactly the asserted verdicts); `update_dt` lexicographic ordering is safe (both writers stamp ISO strings in the same fixed +07:00 offset); DOC_TYPE literals match the prompt exactly; the Vertex payload JSON shape is valid.
- Fact-check design notes: multi-invoice files are documented out of scope (`keep="first"` picks a nondeterministic invoice — worth noting given b6fc1ef's focus); long-Thai-address exact-match will read pessimistic vs reconcile's Jaro-Winkler 0.80 by design; confirm with the labelling team that GT "Total Amount" means the pre-VAT subtotal (`TOTAL_AMOUNT = MAX(BEFORE_VAT_AMOUNT)`); a malformed GT workbook wedges only the isolated fact-check namespace (loud, recoverable).
- Cross-pipeline contamination between the main and fact-check OCR logs is ruled out (separate `ocr_log/` vs `fact_check/ocr_log/` namespaces on both GCS and SharePoint).
- A malformed prediction line is skipped by per-line isolation (by design) but produces **no FAILED row** — the record silently vanishes from output while its audio stays in processing; acceptable under current design, worth knowing when reconciling counts.
- `config/sentiment_telesale/resources/*.txt` are dev-reference copies; runtime pulls the prompt + checklist from SharePoint — the local copies can drift silently.
- `config/common.yml` `framework.project_id: ${QA_GCP_PROJECT_ID}` is dead (only `framework.timezone` is consumed); `DEV_EMAIL` vs `DEVELOPER_EMAIL` are two internally-consistent naming worlds (QA/telesale vs tax), not a break.
- In prod, all module loggers collapse into the single `LOG_NAME` logger, so JSON `logger_name` is always `"app"` (module attribution lost — likely intentional single-stream design).
- Transaction/performance "minutes" strings encode MM.SS as a decimal-looking value (90 s → `"1.30"`); documented and tested — downstream must not parse as float minutes.
- `doc/ponytail_review_tax_invoice.md` already tracks a ~189-line complexity-cleanup backlog for the tax packages (O1–O17, R1–R20); none of it conflicts with the fixes above, and several items touch the same files — coordinate to avoid merge friction.
- Windows log-rotation pytest flake: root-caused by P2-18 (it is not a pytest problem).

## Implementation Plan

### Phase 1 — Critical fixes (P0)
- (none — no P0 findings)

### Phase 2 — High (P1)
Deploy chain first (everything else is unreachable in cloud until these land), then data-safety code fixes.

- [ ] **[P1-13]** Rename `match_prefix` → `matches_prefix` — files: `terraform/projects/tax_invoice_extraction/main.tf:43` — verify by: `terraform validate` in the project dir
- [ ] **[P1-14]** Fix all three tax tfvars (config paths → `ocr_pipeline_{pre,post}_tasks.yml`; add `config_fact_check_path_pre/post`; add `gcp_scheduler_location` to release/prod; correct release project id; fill prod values with owner) — files: `terraform/projects/tax_invoice_extraction/{nprd,release,prod}_config.tfvars` — verify by: `terraform plan -var-file=<env>_config.tfvars` per env
- [ ] **[P1-15]** Fix the fact-check block: delete duplicate `TAX_INVOICE_FACT_CHECK_PATH` env entries (both jobs), point the image at `…-ai-tax-inv-reconcile-artifact-repo` (also `outputs.tf:17`), scheduler region → `var.gcp_scheduler_location` (+ Content-Type header) — files: `terraform/projects/tax_invoice_extraction/main.tf`, `outputs.tf` — verify by: plan + nprd apply
- [ ] **[P1-16]** Fix `"nrpd"` → `"nprd"` (both occurrences) — files: `terraform/projects/sentiment_qa/main.tf:3,11` — verify by: nprd plan (watch for secret create-vs-import)
- [ ] **[P1-17]** Remove the leading `/` from both Eventarc `matching_criteria` values — files: `terraform/projects/tax_invoice_extraction/main.tf:197,353` — verify by: nprd object-create smoke test fires the workflow
- [ ] **[P1-19]** Provision `TAX_INVOICE_OCR_TRACING_LOG_PATH`, `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`, `TAX_INVOICE_TAX_INVOICE_REJECTED` (+ drop the six dead legacy secrets) — files: `terraform/projects/tax_invoice_extraction/locals.tf`, `main.tf` — prerequisite: secret values via the IT service-request process — verify by: post-pipeline nprd run reaches reconcile with real paths
- [ ] **[P1-18]** Matrix `terraform_pr_validation.yml` over all three projects — files: `.github/workflows/terraform_pr_validation.yml` — verify by: PR touching tax terraform triggers the workflow
- [ ] **[P1-1]** Namespace GCS landing/processing keys per source path (hash prefix at `source_loader.py:196`; cosmetic `result_finalizer.py:120` follow-up) — verify by: new unit test (two same-basename files → distinct keys) + `uv run pytest`
- [ ] **[P1-2]** Persist PENDING per payload after each submit; make `get_web_url` non-fatal in `pre_log_builder` — files: `submit_task.py`, `module/batch_submitter.py`, `module/pre_log_builder.py` — verify by: new test (submit ok + persist fails → no re-submit next run)
- [ ] **[P1-3]** Add the PENDING age-out/reconciliation backstop (do **not** reorder the log/manifest writes) — files: `module/status_finalizer.py` or `retrieve_task.py` — verify by: tests for the zero-prediction and missing-manifest triggers aging out to FAILED
- [ ] **[P1-4]** Clamp subcategory penalties at `-this_level_max` in the accumulation branch — files: `tasks/sentiment_telesale/prep_result_task.py:151-156` — verify by: new test (compliance-fail-else-perfect → total 85); notify report consumers
- [ ] **[P1-5]/[P1-6]** Clamp `churn_risk_indicator`; coerce invalid verification combos (product sign-off) — files: `output_validation/common/customer_insight.py`, `operations_and_professionalism.py` + the two enshrining tests — verify by: `uv run pytest tests/test_tasks/sentiment_telesale/test_output_validation.py`
- [ ] **[P1-7]** Add submit-time dedupe against non-terminal `batch_processing_log` rows; set telesale `max_retries = 0` — files: `tasks/sentiment_telesale/prep_payload_task.py`, `terraform/projects/sentiment_telesale/main.tf:57,145` — verify by: new test (in-flight row → zero payloads)
- [ ] **[P1-8]** Delete only successfully-archived recordings (per-file results or revert to `move_file`) — files: `tasks/sentiment_telesale/export_output_result_task.py:1019-1067` (+ optionally `src/modules/google/gcs.py` per-file copy results) — verify by: new test (failed>0 → sources retained)
- [ ] **[P1-9]** Add `continue` to the processing-list except; fix the enshrining test — files: `tasks/sentiment_qa/prep_payload_task.py:303-304`, `tests/test_tasks/sentiment_qa/test_prep_payload_task.py` — verify by: `uv run pytest tests/test_tasks/sentiment_qa/`
- [ ] **[P1-10]** Gate the control "Y" stamp on `failed == 0` (QA and telesale) — files: both `upload_voice_task.py` — verify by: new test (failed>0 → "N" + retry next run)
- [ ] **[P1-11]** Extend `_export_vat` selection to the group's mapped payment documents — files: `tasks/tax_invoice_reconcile/module/output_exporter.py` — verify by: new scen-2 VAT-workbook test
- [ ] **[P1-12]** Normalize blank markers to null before type dispatch — files: `tasks/tax_invoice_fact_check/module/value_normalizer.py` — verify by: new TEXT+AMOUNT blank-marker tests
- [ ] **[P1-20]** Add PowerShell to the hook matcher (+ PS patterns) and a Grep branch — files: `.claude/settings.json`, `.claude/hooks/protect_sensitive.py` — verify by: manual `Grep path=.env` and `Get-Content .env` blocked

### Phase 3 — Medium (P2)
- [ ] **[P2-1]** Route reconcile/fact-check transaction+performance appends through the generation-guarded `LogExporter`; gate stage emails on terminal status — files: `module/export_logging.py`, `reconcile_task.py` — verify by: concurrent-append test (no lost rows)
- [ ] **[P2-2]** Exclude scenario-0 docs from `EXT_TOTAL_VAT` — files: `module/reconciliation.sql:91-93` — verify by: new copy+original scen-5 test
- [ ] **[P2-3]** Stop deleting the Z45 source on archive — files: `module/source_archiver.py` — verify by: run-twice precheck test
- [ ] **[P2-4]** Decide + implement status-gated archive/delete (owner confirmation) — files: `module/source_archiver.py`, `reconcile_task.py`
- [ ] **[P2-5]** Null >18-digit money values in `_quantize_money` (or soft-validate the finalizer) — files: `schema/model_response.py` or `module/result_finalizer.py:60` — verify by: 20-digit-amount test completes the run
- [ ] **[P2-6]** Add retention/partitioning to the pre-log + manifest; compute `latest_status_per_file` once per run — files: `module/log_exporter.py`, `helper/log_helper.py`, task call sites
- [ ] **[P2-7]** Per-iteration `{date}` replacement — `tasks/sentiment_qa/prep_payload_task.py:235-240` — verify by: two-date test
- [ ] **[P2-8]** Defensive join for `problem_statement` — `tasks/sentiment_qa/export_output_result_task.py:1350-1352`
- [ ] **[P2-9]** Guard the QA multi-product loop per iteration — `tasks/sentiment_qa/upload_voice_task.py`
- [ ] **[P2-10]** Dedup the monthly master on the daily key before writing — `tasks/sentiment_qa/export_output_result_task.py:2258-2358`
- [ ] **[P2-11]** Hoist `group_reason` above the branch — `tasks/sentiment_qa/user_playground_task.py:354`
- [ ] **[P2-12]** `pd.to_datetime(errors="coerce")` before `.dt.date` — telesale `:1436`, QA `:2050` — verify by: all-FAILED batch test
- [ ] **[P2-13]** Coerce the crosssell combo instead of raising — `output_validation/common/sales_effectiveness.py`
- [ ] **[P2-14]** Record retrieved batch URIs; skip re-processing — both `get_batch_result_task.py`
- [ ] **[P2-15]** Write batch-log rows around creation; real dedup; guarded write — both `execute_batch_job_task.py`
- [ ] **[P2-16]** Make `cal_gemini_cost` pure — `src/modules/google/gemini_batch.py:562-599` — verify by: input-preservation regression test
- [ ] **[P2-17]** Cache client/bucket on `GCSModule` — `src/modules/google/gcs.py`
- [ ] **[P2-18]** Single shared file handler in local logging — `src/utils/logger.py` — verify by: handler-count test; full `uv run pytest` no longer flakes on rotation
- [ ] **[P2-19]** NFC + zero-width strip in fact-check `_normalize_text` — `tasks/tax_invoice_fact_check/module/value_normalizer.py`
- [ ] **[P2-20]** Emit GT-coverage metric; guard zero-match — `module/fact_check_evaluator.py`, `fact_check_log_emitter.py`
- [ ] **[P2-21]** Handle time-suffixed GT date strings — `module/value_normalizer.py:89-99`
- [ ] **[P2-22]** Fresh subset per `upload_cond` — `tasks/sentiment_telesale/upload_voice_task.py:302-308`
- [ ] **[P2-23]** Hoist prompt/checklist load out of the date loop — `tasks/sentiment_telesale/prep_payload_task.py`
- [ ] **[P2-24]** Load `user_config.xlsx` once per run — `tasks/sentiment_qa/export_output_result_task.py`
- [ ] **[P2-25]** `rename_file` → `self._session.*` — `src/modules/microsoft/sharepoint.py:750,784,808,811`
- [ ] **[P2-26]** Remove the engine's dead failure branch + misleading comments; add a real abort test — `src/core/engine.py`, `tests/core/test_engine.py`
- [ ] **[P2-27]** Validate date-flag combos in `main.py` via `resolve_data_date_window`; decide telesale range support — `main.py`
- [ ] **[P2-28]** Replace coverage-theater tests with real ones; delete the coverage-data mutation — `tests/core/test_engine.py`, `tests/utils/test_common.py`, `tests/utils/test_pydantic_utils.py`, `tests/test_tasks/sentiment_qa/test_fact_check_task.py`
- [ ] **[P2-29]** Real assertions for GCS path conversion + concurrency limit — `tests/modules/test_gcs.py`
- [ ] **[P2-30]** Add the SourceRejecter swallow tests + reconcile wiring assertions — `tests/test_tasks/tax_invoice_reconcile/`
- [ ] **[P2-31]** CI python matrix 3.11+3.12; align image patch versions — `.github/workflows/unit_testing.yml`, Dockerfiles
- [ ] **[P2-32]** `--no-dev` ×3; backport CVE patch; tax `--chown=root:root` — `cloud_build/*/Dockerfile`
- [ ] **[P2-33]** Confirm intended cadence; align cron + description — `terraform/projects/tax_invoice_extraction/main.tf:134-135`

### Phase 4 — Low / cleanup (P3)
- [ ] **[P3-1]** One PENDING row per spanned batch job — `module/pre_log_builder.py:95` (do with P1-2/P1-3 work)
- [ ] **[P3-2]** `Y` offset via `add_months` — `src/utils/common.py:74`
- [ ] **[P3-3]** Guard `tokens: 0` divisions — `src/utils/token_utils.py`
- [ ] **[P3-4]** tz-aware branch in `parse_datetime` — `src/utils/date_utils.py:71-77`
- [ ] **[P3-5]** Close the pdfium writer in `finally` — `src/utils/pdf_utils.py`
- [ ] **[P3-6]** 429 handling + `Retry-After` — `sharepoint.py`, `msgraph.py`
- [ ] **[P3-7]** Per-line-safe decode — `gemini_batch.py:266`
- [ ] **[P3-8]** Drop the API-key fragment from logs — `gemini_batch.py:60`
- [ ] **[P3-9]** Fix or delete `resolve_job_name` — `gemini_batch.py:122-129`
- [ ] **[P3-10]** Surface move-delete failures — `gcs.py:458-459`
- [ ] **[P3-11]** Document or reject duplicate YAML task keys — `src/utils/file_utils.py`
- [ ] **[P3-12]** Anchor + cache `common.yml` — `engine.py`, `date_utils.py`
- [ ] **[P3-13]** Keep NaT rows in retention — `qa/execute_batch_job_task.py:256-260`
- [ ] **[P3-14]** Fix/rename the fact-check aggregate metric — both `fact_check_task.py`
- [ ] **[P3-15]** Surface unmatched files; guard zero-match — `qa/fact_check_task.py`
- [ ] **[P3-16]** Remove `before_filter_count`; fix the log message — `qa/upload_voice_task.py`
- [ ] **[P3-17]** `dtype=str` on the QA GT read — `qa/fact_check_task.py:914`
- [ ] **[P3-18]** Cache `new_transaction_df` for AI-op logging — `qa/export_output_result_task.py:2048`
- [ ] **[P3-19]** Assert GT key uniqueness — `tax_invoice_fact_check/module/ground_truth_loader.py`
- [ ] **[P3-20]** Add `schema/__init__.py`; close DuckDB connections — reconcile package
- [ ] **[P3-21]** Validate `domain` + `vertexai.project_id` — OCR task validation
- [ ] **[P3-22]** Enforce or re-document CampaignRatio bounds — `campaign_ratio.py`
- [ ] **[P3-23]** Delete the stale `tests/test_utils/` duplicates
- [ ] **[P3-24]** Test-hygiene bundle (asserts, fixtures, stale comment) — module + OCR test files
- [ ] **[P3-25]** `keep_default_na=False`/notna guard on the batch-log read — both export tasks
- [ ] **[P3-26]** Align post-pipeline order docs (YAML header, CLAUDE.md, README, docstring, test name)
- [ ] **[P3-27]** Fix `.claude/rules/python-style.md` + `yaml-config.md`
- [ ] **[P3-28]** Fix terraform/cloud_build READMEs + stale outputs
- [ ] **[P3-29]** Env-template drift bundle (add missing vars; move tax vars; dedupe key; correct the "legacy" comment)
- [ ] **[P3-30]** CI/tooling nits bundle (tfsec, coverage floor, auto_format registration, uv pin, --no-build, pre-commit exclude, checkout versions)
- [ ] **[P3-31]** Document GCS-authoritative mirrors (or guard the SharePoint copies)

### Suggested verification after each phase
- `uv run pytest` (full suite; after P2-18 the Windows rotation flake should be gone — until then clear `logs/app.log*` first)
- `uv run ruff check .` and `uv run ruff format --check .`
- `terraform validate` + `terraform plan -var-file=<env>_config.tfvars` in each of the three `terraform/projects/*` dirs (after Phase 2's terraform items)
- nprd end-to-end smoke: pre pipeline run → object-create triggers the post workflow → reconcile outputs land → finalize stamps terminal statuses; repeat once to confirm idempotency (no duplicate emails/audit rows, Z45 still found)
- fact-check nprd dry-run against the reference set; confirm per-field metrics and the new coverage row
