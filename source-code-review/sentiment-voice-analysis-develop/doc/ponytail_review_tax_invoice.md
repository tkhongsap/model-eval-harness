# Complexity Review — `ocr_tax_invoice_pipeline` & `tax_invoice_reconcile`

**Scope:** all net-new code on `feature/tax_invoice` vs `develop`, in
`tasks/ocr_tax_invoice_pipeline/` and `tasks/tax_invoice_reconcile/`
(8,261 lines, 66 files — both packages absent on `develop`).
**Review type:** ponytail / over-engineering pass — *complexity only*. Correctness,
security, and performance were explicitly out of scope and are **not** covered here.
**Estimated removable:** ~**189 lines** (~2.3%), ~**-101** OCR / ~**-88** reconcile.

> **Status (2026-07-14): APPLIED.** A follow-up ponytail pass re-verified every item against
> current code and applied the checklist below (plus new findings the original review predates —
> the fact-check task family, the cross-package `init_conn` duplication, and the
> `extraction_report_builder` SQL shrinks; ~470 lines total). Exceptions: **O15 withdrawn**
> (the "unreachable" `raise` is what satisfies ruff `RET503`, which this repo enables);
> **B2/B3 skipped** (`pull_job_detail` is the retrieve path's mock seam; `_normalize_job_state`
> names a fiddly coercion — both keeps the original review itself called defensible). The
> line numbers below are historical; `git log` has the applied diff.
> Same-day follow-ups (user request): `canonical_validate` became a `@staticmethod` on
> `ReportSourceLoader`, and `emit_fact_check_logs` (the class→function conversion) moved from
> `module/fact_check_log_emitter.py` to `helper/fact_check_log_emitter.py` — `module/` now holds
> only processing classes; `helper/` holds the stateless functions.
> **Follow-up 3 (2026-07-14, user request): R9/item-1 deliberately reversed.** The packages must
> not depend on each other beyond the `OCRResult`/status contract, so `init_sharepoint`/`init_gcs`
> were duplicated back into `tasks/tax_invoice_reconcile/helper/init_conn.py` (the original
> "self-contained packages" premise below is restored, at +~55 lines), and the pure `validate()`
> collectors (`missing_string_errors`/`int_castable_errors`) moved from the OCR package to
> `src/utils/common.py`. The remaining sanctioned reconcile→OCR imports are exactly the hand-off
> surface: `OCRResult`, the status enums, `unwrap_ocr_result`, and `LogExporter` (log-artifact
> reading, kept per user decision).

## What was deliberately *not* flagged

To avoid churning intentional design, the review excluded (these are correct as-is):

- **Self-contained packages** — each package keeps its own `helper/` (`init_conn`,
  `task_context`, `messages`, `constant`). Cross-package duplication is by design; not flagged.
- **~30-line/function rule** — most single-caller private methods exist to satisfy this
  convention and were kept. Only wrappers that *also* fail to earn their name were flagged.
- **Per-scenario named blocks/CTEs** over a consolidated CASE-ladder (reconcile).
- **Lenient OCR gate** (nullable date, no VAT-required validator) and **strict never-sum**
  of split VAT/exempt amounts.
- **5-case VAT match**, buyer-match tax-id `dtype=str`+lpad-13, Z45 dual date-parse,
  duplicate Z45 output headers.
- **Pydantic/pandera field definitions and load-bearing validators** — the data contract.
- **DuckDB SQL-string building** (no bind params in `CREATE VIEW`, CAST-before-trim) — known quirks.
- **f-strings in log calls** — the custom `Logger` wrapper takes no lazy `%s` args.

---

## Implementation checklist

### `ocr_tax_invoice_pipeline`
- [x] O1 `module/document_processor.py:L167` — delete `_failed_row` twin
- [x] O2 `module/tracing_builder.py:L141` — inline `_metadata`
- [x] O3 `helper/init_conn.py:L9` — inline `make_sharepoint_from_config`
- [x] O4 `module/log_exporter.py:L44` — merge twin log-loaders into `load_log`
- [x] O5 `module/source_loader.py:L154` — build `errors_by_path` map once
- [x] O6 `module/payload_builder.py:L75` — make `dt_suffix` required
- [x] O7 `module/page_processor.py:L94` — inline `_enrich_child_path`
- [x] O8 `module/gcs_router.py:L59` — simplify `prefix_for` with `partition`
- [x] O9 `module/source_loader.py:L194` — drop redundant `os.path.basename`
- [x] O10 `retrieve_task.py:L149` — inline `_export_tracing`
- [x] O11 `module/batch_job_client.py:L88` — delete dead `pull_job_status` + test
- [x] O12 `helper/error_notify.py:L27` — drop unused `case` param
- [x] O13 `helper/task_context.py:L37` — drop `source_site_access` projection
- [x] O14 `schema/model_response.py:L64` — inline `_round_float`
- [ ] O15 `module/log_exporter.py:L123` — delete unreachable `raise`
- [x] O16 `submit_task.py:L196` — delete redundant bare `return`
- [x] O17 `helper/constant.py:L13` — collapse multi-line enum value

### `tax_invoice_reconcile`
- [x] R1 `reconcile_task.py:L248` — dedupe `_recipients`/`_subject` onto context
- [x] R2 `module/export_logging.py:L195` — unify `_storage_path`/`_model_map` lookup
- [x] R3 `module/report_source_loader.py:L52` — extract `_load_master`
- [x] R4 `module/export_logging.py:L132` — vectorize `_dedup_pages`
- [x] R5 `module/report_exporter.py:L21` — collapse wrapper class to a function
- [x] R6 `module/reconciliation.sql:L102,136,172,216,252` — delete dead `Z_*` aliases
- [x] R7 `module/export_logging.py:L329` — inline `_merge_existing`/`_prepare_for_upload`
- [x] R8 `module/export_logging.py:L387` — inline strftime wrappers
- [x] R9 `helper/init_conn.py:L11` — inline `make_sharepoint_from_config`
- [x] R10 `module/source_rejecter.py:L79` — replace dedup loop with `drop_duplicates`
- [x] R11 `helper/output_layout.py:L26` — delete `ARCHIVE_ETAX`/`ARCHIVE_PAPER`
- [x] R12 `module/report_source_loader.py:L46` — `max(default=None)`
- [x] R13 `module/reconciliation_builder.py:L86` — drop `_to_aliased` `model` param
- [x] R14 `reconcile_task.py:L306` + `module/export_logging.py:L88` — delete dead `site_path_prefix`
- [x] R15 `helper/output_layout.py:L47` — inline `dated_subpath`
- [x] R16 `helper/constant.py:L8` — delete `ExtractionStatus.FAILED`
- [x] R17 `module/extraction_report_builder.py:L20,L32` — delete unused `logger`
- [x] R18 `schema/report_output.py:L72` — one-line the `astype/where`
- [x] R19 `schema/master_vendor.py:L8-10` — drop per-field `coerce=True`
- [x] R20 `precheck_task.py:L102` — delete bare `return`

### Borderline (verify first)
- [x] B1 `module/document_processor.py:L117` — collapse `_process_image` guards
- [ ] B2 `module/batch_job_client.py:L84` — drop `pull_job_detail` passthrough
- [ ] B3 `retrieve_task.py:L181` — inline `_normalize_job_state`

---

# `ocr_tax_invoice_pipeline` findings

### O1 · `module/document_processor.py:L167` · `delete` · ~-15
- **Problem:** `_failed_row` is a full copy of `_scored_row` with every score forced to
  `0.0` — a 24-line redundant twin that produces the identical dict.
- **Solution:** At its only call site (L80) call
  `self._scored_row(job_id, pipeline_name, file_name, {"page_no": 0}, 0, "", QualityStatus.REJECTED, f"PDF scoring failed: {file_name}")`
  and delete `_failed_row`. No test calls `_failed_row` directly.

### O2 · `module/tracing_builder.py:L141` · `yagni` · ~-15
- **Problem:** `_metadata` is a one-caller intermediate builder that re-types its ~10 kwargs
  as dict keys — every field name is spelled twice (param + key).
- **Solution:** Inline the dict literal directly into `line_to_record` (which already computes
  `source_uri`) and delete `_metadata`.

### O3 · `helper/init_conn.py:L9` · `yagni` · ~-15
- **Problem:** `make_sharepoint_from_config` has exactly one production caller (`init_sharepoint`)
  and just forwards resolved args to `SharePointModule(...)`, re-resolving `site_domain`.
- **Solution:** Build `SharePointModule(...)` directly inside `init_sharepoint`'s `try`, reusing
  the already-resolved `site` for `site_domain`; drop the wrapper.

### O4 · `module/log_exporter.py:L44` · `yagni` · ~-10
- **Problem:** `load_pre_processing_log` and `load_page_manifest_log` have byte-identical bodies
  (`return self._load_csv(gcs_path)[0]`).
- **Solution:** Collapse to one `def load_log(self, gcs_path)`; update the 5 call sites
  (`reject_task`, `retrieve_task`, `submit_task`).

### O5 · `module/source_loader.py:L154` · `shrink` · ~-5
- **Problem:** `upload_to_landing` builds a `failed_paths` set then re-scans `res["errors"]` with
  `next()` once per file — O(n·m) and two passes.
- **Solution:** Build the lookup once:
  `errors_by_path = {e.get("download_path"): e.get("error", "upload failed") for e in res.get("errors", [])}`,
  then classify each file by `in errors_by_path` / `not in errors_by_path` and read the message from the map.

### O6 · `module/payload_builder.py:L75` · `yagni` · ~-3
- **Problem:** The `dt_suffix is None → datetime.now()` fallback is production-dead —
  `batch_submitter` (L76) always passes a real suffix.
- **Solution:** Make `dt_suffix: str` required; drop L75-76 and the now-unused `from datetime import datetime`.
  One test (`test_payload_builder.py:78`) must pass a suffix.

### O7 · `module/page_processor.py:L94` · `yagni` · ~-4
- **Problem:** `_enrich_child_path` is a one-caller staticmethod (L60).
- **Solution:** Inline into `run()`:
  `for row in manifest_rows:` → `if row.get("child_path"): row["child_path"] = f"gs://{self._processing_gcs.bucket_name}/{row['child_path']}"`.

### O8 · `module/gcs_router.py:L59` · `shrink` · ~-4
- **Problem:** `prefix_for` re-derives the bucket via `extract_bucket` + a `startswith` guard
  just to strip `gs://bucket/`.
- **Solution:** `resolved = self.resolved_path(key)`; if not `gs://` return `resolved`;
  `_, sep, rest = resolved[len("gs://"):].partition("/")`; `return rest if sep else resolved`.

### O9 · `module/source_loader.py:L194` · `stdlib` · ~-2
- **Problem:** `os.path.basename(name)` is called twice on a SharePoint item `name` that is
  already a leaf filename.
- **Solution:** Use `name` directly in both spots; drop the now-unused `import os` (L6).

### O10 · `retrieve_task.py:L149` · `yagni` · ~-3
- **Problem:** `_export_tracing` is a one-caller wrapper (only `_collect`, L139) and isn't needed
  for the 30-line rule (`_collect` stays ~19 lines inlined).
- **Solution:** Inline:
  `tracing_df = self._tracing_builder.build_tracing_log(retriever_succeeded[1] + retriever_failed[1]); self._tracing_exporter.save(tracing_df, self._router.resolve(self.ctx.control_site.get("tracing_log_path", "")))`
  and drop the method.

### O11 · `module/batch_job_client.py:L88` · `delete` · ~-3
- **Problem:** `pull_job_status` has no production caller — grep finds only its own unit test.
- **Solution:** Remove the method and `test_pull_job_status_delegates_to_gemini_module`.

### O12 · `helper/error_notify.py:L27` · `delete` · ~-3
- **Problem:** The `case` param is never overridden — all 3 prod callers and every test use the default.
- **Solution:** Drop `*, case: str = _DEFAULT_CASE`, delete the `_DEFAULT_CASE` constant (L23) and its
  Args entry (L34-35), inline `cfg = (ctx.notifications or {}).get("system_exception") or {}`.

### O13 · `helper/task_context.py:L37` · `yagni` · ~-3
- **Problem:** `source_site_access` is a strict-subset projection of `source_site` (already a field);
  the SP factory reads each key via `.get()` and ignores extras, so passing `source_site` is byte-identical.
- **Solution:** Drop the field, the `_SITE_ACCESS_KEYS` constant (L15) and the L80 comprehension; call
  `init_sharepoint("Source", self.ctx.source_site)`. Couples to `test_submit_task.py:210`.

### O14 · `schema/model_response.py:L64` · `shrink` · ~-3
- **Problem:** `_round_float` is a one-use helper (only caller `_round_line_floats`, L118-120); the parallel
  `_quantize_money` earns its keep with 2 callers, this one doesn't.
- **Solution:** Inline — the validator body becomes `return None if v is None else round(v, 2)`; delete the def.

### O15 · `module/log_exporter.py:L123` · `delete` · ~-2
- **Problem:** `raise RuntimeError("unreachable: loop always returns or raises")` after a loop whose last
  iteration always returns or raises — dead defensive line already tagged `# pragma: no cover`.
- **Solution:** Delete the line.

### O16 · `submit_task.py:L196` · `delete` · ~-1
- **Problem:** Redundant bare `return` at the end of `execute_task`; the function already returns `None`
  implicitly (the early `return` at L189-190 is the meaningful one).
- **Solution:** Remove the line.

### O17 · `helper/constant.py:L13` · `shrink` · ~-2
- **Problem:** The `SUCCESS_WITH_FAILURE` enum value is paren-wrapped across multiple lines.
- **Solution:** `SUCCESS_WITH_FAILURE = "SUCCESS_WITH_FAILURE"  # post-proc complete, some pages/lines failed validation`.

---

# `tax_invoice_reconcile` findings

### R1 · `reconcile_task.py:L248` · `shrink` · ~-12
- **Problem:** `_recipients`/`_subject` (plus the `_CASE_SYSTEM` / `_PROCESSING_FAILED_TEMPLATE` /
  `_SUBJECT_PROCESSING_FAILED` consts) are byte-identical to `precheck_task.py:L144,L153`.
- **Solution:** Both tasks already share `ReconcileTaskContext` (holds `notifications`, `execution_dt`,
  `timezone`) — move them onto it as `ctx.recipients(case)` / `ctx.subject(template)`; delete both copies.
  No new file.

### R2 · `module/export_logging.py:L195` · `native` · ~-10
- **Problem:** `_storage_path` filters `pre_log_df` per row (`df[df[...] == file_path]`) while `_model_map`
  builds a dict once — two lookups into the same frame, two different ways.
- **Solution:** Collapse both into one `_pre_log_map(value_col)` (the `_model_map` body, parametrized);
  build the storage map once in `_build_transaction_log` and look it up per row.

### R3 · `module/report_source_loader.py:L52` · `native` · ~-10
- **Problem:** `load_master_buyer` and `load_master_vendor` are ~90% identical (differ only in schema,
  dtype column, and labels).
- **Solution:** Extract one `_load_master(schema, path_key, file_key, label, dtype_col)`; each public method
  becomes a one-line call.
- **Post-apply note (2026-07-14):** the shared alias-rename helper `canonical_validate` now lives on
  `ReportSourceLoader` as a `@staticmethod` (user request); `FactCheckSourceLoader` calls it via the class.

### R4 · `module/export_logging.py:L132` · `native` · ~-9
- **Problem:** `_dedup_pages` hand-rolls a groupby-accumulate loop (`pages = []; for … pages.append(dict)`)
  plus a `_page_status` helper.
- **Solution:** Vectorize and drop the helper:
  ```python
  df = ocr_df.copy()
  df["_usage_key"] = df["USAGE_METADATA"].map(self._usage_json)
  failed = df.groupby(_PAGE_KEYS, dropna=False, sort=False)["STATUS"].transform(
      lambda s: s.eq(OCROutputStatus.FAILED.value).any())
  df["PAGE_STATUS"] = failed.map({True: OCROutputStatus.FAILED.value, False: OCROutputStatus.SUCCESS.value})
  return df.drop_duplicates(subset=_PAGE_KEYS)
  ```

### R5 · `module/report_exporter.py:L21` · `yagni` · ~-9
- **Problem:** A single-method I/O wrapper class — `export_csv` just does `df.to_csv().encode()` +
  `sp.upload_file()` + try/except; instantiated-and-called exactly once (`reconcile_task.py:157`).
  Its docstring admits speculative "…Output Report / enriched Z45 later" reuse that never came.
- **Solution:** Drop the class; make it a module-level `def export_csv(sp, df, path, *, label="report")`.
  Removes the `__init__`/class scaffolding.

### R6 · `module/reconciliation.sql:L102,136,172,216,252` · `delete` · ~-5
- **Problem:** `Z_REF_DOC_INV` and `Z_COMPANY` are aliased in every scenario `base` CTE but referenced
  nowhere — the key predicates (`K_COMPANY`, `K_INVOICE`) read the raw `zz.company` / `zz.ref_doc_inv`,
  and the builder never selects the `Z_` versions. (`Z_VAT_AMOUNT`/`Z_VENDOR_NAME` *are* used — keep those.)
- **Solution:** Drop both aliases from each of the 5 lines, leaving `, zz._z_id`.

### R7 · `module/export_logging.py:L329` · `yagni` · ~-6
- **Problem:** `_merge_existing` and `_prepare_for_upload` are each called exactly once, both inside
  `_append_and_upload`.
- **Solution:** Inline both — the merged method stays under ~24 lines.

### R8 · `module/export_logging.py:L387` · `yagni` · ~-6
- **Problem:** `_load_dt_str` / `_run_date_str` are one-line, one-caller `strftime` wrappers.
- **Solution:** Resolve `self.execution_dt` once in `__init__` (param is a required `datetime`, so drop the
  `or get_current_datetime()` fallback) and inline the two `strftime` calls at their sites.

### R9 · `helper/init_conn.py:L11` · `yagni` · ~-5
- **Problem:** `make_sharepoint_from_config` has one production caller (`init_sharepoint`, L43) and just
  forwards resolved args to the constructor. (Same pattern as OCR O3.)
- **Solution:** Inline into `init_sharepoint` (also drops its standalone test).

### R10 · `module/source_rejecter.py:L79` · `native` · ~-6
- **Problem:** A hand-rolled dedup loop (`targets`/`seen` set, manual `zip` + `startswith`) reimplements
  `drop_duplicates`.
- **Solution:** Replace L79-87 with:
  ```python
  j = joined.assign(_c=joined["child_path"].astype("string").str.strip())
  j = j[j["_c"].str.startswith("gs://")].drop_duplicates(["FILE_PATH", "_c"])
  return list(zip(j["FILE_PATH"], j["_c"], strict=True))
  ```

### R11 · `helper/output_layout.py:L26` · `delete` · ~-3
- **Problem:** `ARCHIVE_ETAX` / `ARCHIVE_PAPER` are dead duplicates of `INPUT_TYPE_ETAX` /
  `INPUT_TYPE_PAPER` (same values); `classify()` already returns exactly those strings.
- **Solution:** Delete both constants; L54 collapses to `folder = classify(file_path)`.

### R12 · `module/report_source_loader.py:L46` · `stdlib` · ~-2
- **Problem:** `safe_list_get(sorted(matches, reverse=True), 0, None)` is exactly `max(matches, default=None)`.
- **Solution:** Replace with `max(matches, default=None)`; drops the sort and the `safe_list_get` import
  (its only use).

### R13 · `module/reconciliation_builder.py:L86` · `yagni` · ~-4
- **Problem:** `_to_aliased(df, model)` has a single caller passing `model=ReportOutput`; the `model` param
  is speculative generality.
- **Solution:** Inline into `build()`, or at minimum hardcode `ReportOutput` and drop the param.

### R14 · `reconcile_task.py:L306` + `module/export_logging.py:L88` · `delete` · ~-2
- **Problem:** `site_path_prefix` is written into `_logging_cfg` (`reconcile_task.py:L306`) but never read by
  `ExportLogging` — only named in its docstring (`export_logging.py:L88`).
- **Solution:** Delete the config key at the writer and the stale docstring reference at the reader.

### R15 · `helper/output_layout.py:L47` · `shrink` · ~-3
- **Problem:** `dated_subpath(datadate)` is a docstringed wrapper for `str(datadate)`.
- **Solution:** Delete it; inline `str(datadate)` at its 3 call sites (L56, L62, L74).

### R16 · `helper/constant.py:L8` · `delete` · ~-1
- **Problem:** `ExtractionStatus.FAILED = "Failed"` is never referenced anywhere in `tasks/` (only
  `.COMPLETED` / `.REQUIRES_REVIEW` are used).
- **Solution:** Delete the member.

### R17 · `module/extraction_report_builder.py:L20,L32` · `delete` · ~-2
- **Problem:** `logger = Logger(__name__)` (L32) and its `from src.utils.logger import Logger` import (L20)
  are never referenced — no log call in the file.
- **Solution:** Delete both lines.

### R18 · `schema/report_output.py:L72` · `shrink` · ~-1
- **Problem:** Two lines where one does: `df = df.astype(object); return df.where(df.notna(), "")`.
- **Solution:** `return df.astype(object).where(df.notna(), "")` (the form already used in
  `reconciliation_builder._finalize_z45`).

### R19 · `schema/master_vendor.py:L8-10` · `delete` · token-only
- **Problem:** Per-field `coerce=True` (3 occurrences) is redundant with `Config.coerce = True`
  (`MasterBuyer` relies on `Config` alone).
- **Solution:** Drop the per-field `coerce=True`.

### R20 · `precheck_task.py:L102` · `delete` · ~-1
- **Problem:** Bare `return` at the end of `execute_task` is a no-op.
- **Solution:** Remove the line.

---

# Borderline — verify before cutting

These trade something away (a defensive guard, a test, or a live-usage assumption). Confirm the
premise before applying.

### B1 · `module/document_processor.py:L117` · `yagni`
- **Problem:** `_process_image` unwraps `per_page` and guards a missing-key / empty-list shape that
  `score_image_bytes` never actually returns (it always yields a 1-element `per_page`).
- **Solution:** Collapse to `score = image_utils.score_image_bytes(content, self._iqs_config)["per_page"][0]`.
- **Caveat:** This deletes two defensive tests
  (`test_missing_per_page_key_falls_back_to_result_itself`, `test_empty_per_page_list_falls_back_to_default_failed_score`).
  Safe **only** because `image_utils` is in-house and controls the shape — if that ever returns from an
  external source, keep the guard.

### B2 · `module/batch_job_client.py:L84` · `yagni`
- **Problem:** `pull_job_detail` is a pure passthrough (`return self._gemini.pull_batch_job(...)`) with an
  empty docstring; `retrieve_task` builds a whole `BatchJobClient` seemingly just to call it. Dropping it
  would also free the `from google import genai` import.
- **Solution:** Have `retrieve_task` call `gemini.pull_batch_job(...)` directly and delete the wrapper.
- **Caveat:** Reviewer notes conflicted on whether `poll_delay` (the other `BatchJobClient` field) is still
  live/config-wired. **Confirm `BatchJobClient` has no remaining use** before removing it; if `poll_delay`
  is used, keep the class and only remove the passthrough method.

### B3 · `retrieve_task.py:L181` · `yagni`
- **Problem:** `_normalize_job_state` is a one-caller staticmethod (only `_classify_jobs`, L170).
- **Solution:** Inline:
  `state = getattr(getattr(job, "state", None), "name", None) or str(getattr(job, "state", None))`.
- **Caveat:** Optional — the method names a fiddly enum-or-string coercion, so a keep is defensible. Cut only
  if you value fewer methods over the documenting name.

---

## Notes on totals

- Subtotals per reviewer summed to **~189 lines** (`-101` OCR, `-88` reconcile). The single dead concept that
  spans two files (`site_path_prefix`, R14) is two distinct lines, not a double-count.
- The top ~10 cuts (O1, O2, O3, O4, R1, R2, R3, R4, R5, R6) account for the bulk of the savings; the long tail
  is mostly token-level.
- After applying, run `uv run pytest` — several items (O6, O11, O13, B1) touch or remove tests. Note the known
  Windows log-rotation pytest flake (`WinError 32` on `logs/app.log` at the 10 MB cap): clear `logs/app.log*`
  and re-run if it trips.
