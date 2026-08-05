# Tax-Invoice Reconcile — Developer Guide

The deep companion to [README.md](README.md). The README tells you **what** is here; this file
tells you **why** it is the way it is. It is written for someone taking the package over cold,
and it deliberately carries the reasoning that will not survive in the code — the source is being
stripped down to Google-style docstrings, and [module/reconciliation.sql](module/reconciliation.sql)
in particular is being reduced to one-line section markers. **Everything the SQL comments used to
say lives in [Stage 2](#stage-2--the-reconciliation-engine) below.**

Read the [README](README.md) first for the module/schema inventory; this guide does not restate it.

---

## Table of contents

1. [Purpose & relationship to the OCR pipeline](#1--purpose--relationship-to-the-ocr-pipeline)
2. [The four tasks, step by step](#2--the-four-tasks-step-by-step)
3. [Stage 1 — the extraction report](#3--stage-1--the-extraction-report)
4. [Stage 2 — the reconciliation engine](#4--stage-2--the-reconciliation-engine)
5. [Stage 3 — output contracts](#5--stage-3--output-contracts)
6. [Matching gotchas](#6--matching-gotchas)
7. [Fact-check subsystem](#7--fact-check-subsystem)
8. [Reference & testing](#8--reference--testing)

---

## 1 — Purpose & relationship to the OCR pipeline

This package is the **business layer** for tax invoices. It owns nothing about Gemini, batches, or
GCS routing — that all belongs to the domain-agnostic OCR pipeline in
[`tasks/ocr_tax_invoice_pipeline/`](../ocr_tax_invoice_pipeline/). What this package owns is the
treasury logic: reconciling extracted receipts against the Master Buyer, the Master Vendor, and
the SAP **ZAPRPT45** ("Z45") input-VAT report, and delivering the per-document workbooks,
archives, and audit logs.

### How it plugs into the engine

`CoreEngine` ([src/core/engine.py](../../src/core/engine.py)) does not have a `tasks:` list. It
iterates the YAML's **top-level keys in insertion order**:

```python
# src/core/engine.py:132
task_list = [k for k in self.config if k not in self.RESERVED_CONFIG_KEYS]
```

and threads a single value — `pre_result` — from each task to the next, **reassigning it to
whatever the task returned**:

```python
# src/core/engine.py:165-170
if pre_result is not None:
    pre_result = task_instance.run(pre_result)
else:
    pre_result = task_instance.run()
```

`TaskInterface.run()` ([src/core/task_interface.py](../../src/core/task_interface.py)) returns
`post_execute(execute_task())` (lines 152–159), and it only *stores* the incoming value on
`self.pre_result` when that value is not `None` (lines 145–147).

The payload threaded through the post pipeline is the typed
[`OCRResult`](../ocr_tax_invoice_pipeline/schema/contracts.py) dataclass:

| Field | Meaning |
|---|---|
| `final_df` | `OCROutputSchema`-validated frame, one row per page × line item. May be **empty** when every in-flight job died. |
| `file_statuses` | Terminal `JobStatus` per `sharepoint_input_path` (SharePoint paths, never GCS URIs). Computed once in retrieve. |
| `pre_processing_log` | Append-only pre-processing-log snapshot, loaded **once** in retrieve. |
| `page_manifest_log` | Per-page manifest snapshot; carries each page's immutable GCS `child_path`. |

### The "return the OCRResult unchanged" contract, and why it is money

Both business tasks — `ReconcileTask` and `TaxInvoiceFactCheckTask` — end with
`return self.pre_result`. That is **not ceremony**. It is the load-bearing line of the whole
post pipeline.

`OCRFinalizeTask` is the only task that stamps a terminal `SUCCESS` / `SUCCESS_WITH_FAILURE` /
`FAILED` status into the pre-processing log, and it needs the `OCRResult` to do it. The ordering
gives you a free, idempotent retry:

- **If reconcile succeeds** → finalize runs → files are stamped terminal → the next run's dedupe
  skips them.
- **If reconcile raises** → the engine re-raises (`engine.py:183-185`) → finalize **never runs** →
  the files stay `PENDING`/`PARTIAL` (in-flight) → the next run re-collects the *same predictions*
  from the same GCS output path. Gemini has already been paid; re-collection costs nothing.

That is the cost rationale. A business failure must never be allowed to burn the batch.

### ⚠ Hazard: any post-pipeline task that does not return `pre_result` strands the run

Because the engine **overwrites** `pre_result` with each task's return value, a task inserted
between `OCRRetrieveTask` and `OCRFinalizeTask` that falls off the end of `execute_task()` (an
implicit `return None`) silently poisons the chain:

1. Your task returns `None`.
2. `run()` returns `post_execute(None)` → `None`.
3. The engine sets `pre_result = None`.
4. `OCRFinalizeTask` is then invoked via the `run()` (no-arg) branch — it never sees the
   `OCRResult`, and no terminal status is stamped.
5. Every file in that run stays `PENDING`/`PARTIAL` **forever**, even though Gemini was paid and
   the reconcile output was delivered. Nothing errors. Nothing alerts.

**Rule:** any task you add between retrieve and finalize MUST end with `return self.pre_result`.
A task that must run *before* the `OCRResult` exists belongs at the **top** of the YAML instead —
which is exactly where `ReconcilePrecheckTask` sits (see [§2.1](#21--reconcileprechecktask)).

---

## 2 — The four tasks, step by step

Registered names, files, and their pipelines:

| Registry name | File | Pipeline (config) | Returns |
|---|---|---|---|
| `ReconcilePrecheckTask` | [precheck_task.py](precheck_task.py) | post ([ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml)) — **first key** | `None` |
| `ReconcileTask` | [reconcile_task.py](reconcile_task.py) | post | `self.pre_result` (the `OCRResult`) |
| `TaxInvoiceRejectTask` | [reject_task.py](reject_task.py) | pre ([ocr_pipeline_pre_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml)) | `None` (pre-pipeline `pre_result` is always `None` anyway) |
| `TaxInvoiceFactCheckTask` | [fact_check_task.py](fact_check_task.py) | fact-check post ([ocr_pipeline_fact_check_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_fact_check_post_tasks.yml)) | `self.pre_result` |

All four are imported in [`__init__.py`](__init__.py) so the `@task_registry.register(...)`
decorators fire as an import side effect (via `tasks/__init__.py`).

### The REAL post-pipeline order

The shipped [ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml)
declares its keys in this order:

```
ReconcilePrecheckTask   (line 25)   ← FIRST
OCRRetrieveTask         (line 53)
ReconcileTask           (line 84)
OCRFinalizeTask         (line 135)  ← MUST stay last
```

Since key order **is** execution order, **precheck runs before retrieve**. This is correct and
deliberate, and it must not be "fixed".

**Why precheck must be first.** `ReconcilePrecheckTask.execute_task()` returns `None`
([precheck_task.py:102](precheck_task.py)). Trace what would happen if it were moved after
`OCRRetrieveTask` — the order the README's flow diagram and the repo `CLAUDE.md` both currently
claim:

1. `OCRRetrieveTask` returns the `OCRResult`; the engine sets `pre_result = OCRResult`.
2. `ReconcilePrecheckTask.run(OCRResult)` stores it, checks the files, and returns `None`.
3. The engine overwrites `pre_result = None`.
4. `ReconcileTask` is invoked with **no** `pre_result`; `self.pre_result` stays `None`.
5. `_extract_ocr_results(None)` returns `None`, the not-a-DataFrame guard fires, and reconcile
   logs a warning and returns `None` — **no reconciliation, no output, no error**.
6. `OCRFinalizeTask` gets nothing and stamps nothing.
7. Every file stays `PENDING`/`PARTIAL`. Gemini was paid; nothing was delivered; nothing alerted.

Precheck has no `OCRResult` to hand on, so it can only run where there is nothing to hand on —
at the top. That also happens to be the cheaper place: if the Master Buyer is missing, the run
aborts before retrieve does any work, and (crucially) before finalize could stamp anything.

> **Doc mismatch to fix (not in this change):** the [README](README.md) "Post-pipeline task order"
> diagram, the repo-root `CLAUDE.md`, the header comment inside
> [ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml)
> itself, and [config/tax_invoice_extraction/README.md](../../config/tax_invoice_extraction/README.md)
> all describe the order as `OCRRetrieveTask → ReconcilePrecheckTask → …`. **The YAML is right and
> the prose is wrong.** Correct the prose; do not touch the YAML.

---

### 2.1 — `ReconcilePrecheckTask`

Halts the pipeline (with a business-exception email) when a required source file is absent, so
reconcile never starts a run it cannot finish.

**Config read** (all under the task's own block; validated by `REQUIRED_STRING_KEYS`):

| Key | Purpose |
|---|---|
| `sharepoint.source_site.{site_name,site_domain,site_path,client_id,client_secret,tenant_id}` | Source-site connection |
| `sharepoint.source_site.master_buyer_path` / `master_buyer_file` | Master Buyer folder + filename regex |
| `sharepoint.source_site.z45_report_path` / `z45_report_file` | Z45 folder + filename regex |
| `sharepoint.source_site.master_vendor_path` / `master_vendor_file` | Master Vendor folder + filename regex |
| `framework.email_template_dir` | Template folder |
| `framework.notifications.business_exception.{sender_email,receiver_email}` | Missing-file email |
| `framework.notifications.system_exception.{sender_email,receiver_email}` | Crash email |

**`__init__`** builds the immutable `ReconcileTaskContext` ([helper/task_context.py](helper/task_context.py))
— config blocks + `execution_dt` package + the `control:` / `msgraph:` blocks from
`config/common.yml`. `frozen=True` is *shallow*: the dict fields are still mutable, so treat them
as read-only.

**`pre_execute`** builds the source SharePoint module and the `EmailNotifier`
([helper/init_conn.py](helper/init_conn.py)).

**`execute_task`**:

1. For each `(folder_key, pattern_key, label)` in `_DEPENDENCIES` — Master Buyer, Z45 report,
   Master Vendor (in that order) — env-resolve the folder and call
   `list_files_pattern(folder_path, pattern)`.
2. Collect the labels with no match.
3. If any are missing: send **one consolidated** `dependency_missing.txt` email (placeholder
   `{MISSING_FILES}` = a `- label` bullet list) to the `business_exception` recipients, then raise
   `DependencyMissingError`. The email is best-effort — a mail failure is logged and swallowed so
   the raise still happens.
4. Otherwise log and return `None`.

**Artifacts:** none written. **Return value:** `None` (see the ordering discussion above).

**`on_error`:** if the error *is* a `DependencyMissingError`, it just logs — the user was already
emailed, and a second (system-exception) mail would be noise. Any other exception is a genuine
system error and sends `processing_failed.txt` to the `system_exception` recipients.

---

### 2.2 — `ReconcileTask`

The main event. Orchestration only — every unit of work lives in a module.

**Config read** (`REQUIRED_STRING_KEYS`, [reconcile_task.py:68-96](reconcile_task.py)): `gcp.project_id`,
`gcp.project_name`; the whole `sharepoint.source_site` block (credentials + the three
master/Z45 path+pattern pairs); `sharepoint.destination_site` (credentials + `dest_path`,
`archive_invoice_path`, `archive_vat_path`); `sharepoint.control_site.extraction_result_path`,
`transaction_log_file`, `performance_log_file`; and `framework.email_template_dir`.

> **Soft dependency — `reject_path`.** `sharepoint.destination_site.reject_path` **is configured**
> ([ocr_pipeline_post_tasks.yml:114](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml))
> but is deliberately **not** in `REQUIRED_STRING_KEYS`. It is read with
> `dest.get("reject_path", "")` ([reconcile_task.py:108](reconcile_task.py)), and `SourceRejecter`
> no-ops when the root is blank (`if not self._reject_root: return`). So a deployment that omits
> it still reconciles cleanly — it just silently stops copying Suspicious pages to the reject
> folder. If Suspicious rejects "stop working", check this key first.

**`pre_execute`** builds three SharePoint connections (Source / Control / Destination), the
`EmailNotifier`, and the `SourceRejecter` (wired with `_gcs_for_bucket`, a per-bucket cached
`GCSModule` factory — a Suspicious page's `child_path` may live in a different bucket than the
task's default).

**`execute_task`**, numbered:

1. **Unwrap the upstream result.** `_extract_ocr_results(self.pre_result)`: if it is an
   `OCRResult`, stash `latest_status_per_file(pre_result.pre_processing_log)` on `self._run_log_df`
   (for the audit logs — **no GCS re-read**) and return `final_df`. A bare DataFrame is accepted
   for back-compat. If the frame is missing or empty, log a warning and **return `self.pre_result`
   unchanged** — so finalize can still stamp dead-job `FAILED` files.
2. **Load Master Buyer.** `ReportSourceLoader.load_master_buyer()`.
3. **Build the extraction report.** `ExtractionReportBuilder().build(ocr_results_df, master_buyer_df)`
   → the `ExtractionProcessing` frame. See [§3](#3--stage-1--the-extraction-report).
4. **Export the extraction CSV.** `to_extraction_output(processing_df)` (drops the internal
   `DATADATE`) → uploaded inline by `ReconcileTask` → control site, UTF-8-**SIG** (BOM, so Thai opens
   correctly in Excel), at `extraction_result_path` =
   `${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_CONTROL_EXTRACTION_PATH}/extraction_result_%{DATA_DATE_YYYY-MM-DD}.csv`.
   A SharePoint failure here is logged and swallowed.
5. **Email `extraction_success`** (`extraction.txt`, subject `[AI-Tax Invoice][Extraction_Success] on {date}`)
   with `_extraction_counts(...)`: `PROCESSING_NO` = distinct `FILE_NAME`s; `SUCCESS_NO` = files
   where **every** row is `DOC_STATUS == "Completed"` (one `RequiresReview` row fails the whole
   file); `FAILED_NO` = the remainder.
6. **Load Master Vendor + Z45.** `loader.load_master_vendor()`, `loader.load_z45()`.
7. **Reconcile.** `ReconciliationBuilder().build(processing_df, z45_report_df, master_vendor_df)`
   → `(report_df, z45_enriched_df, z45_link_df)`. See [§4](#4--stage-2--the-reconciliation-engine).
8. **Export the workbooks.** `OutputExporter(sp_dest, dest_path).export(...)` — per-destination
   `Extract&Mapping` + `VAT Report` workbooks. See [§5.6](#56--outputexporter-routing).
9. **Archive.** `SourceArchiver.archive_invoices(processing_df, datadate)` copies each distinct
   `FILE_PATH` into `{archive_invoice_path}/{E-TAX|Paper [Scan]}/{YYYYMMDD}/{name}` then **deletes
   the source**; `archive_z45(...)` does the same for the Z45 workbook into
   `{archive_vat_path}/{YYYYMMDD}/{name}`. Copy-then-delete, never delete-then-copy: an upload
   failure `return`s **before** the delete, so an un-archived original is never removed.
   Clearing the input folder is what makes re-runs idempotent.
10. **Reject Suspicious pages.** `SourceRejecter.reject_suspicious(...)` copies each `SUSPICIOUS`
    row's *immutable GCS chunk* (`page_manifest.child_path`) into the reject folder. It uses the
    GCS page, not a re-split of the source, because those are the exact bytes Gemini saw and
    flagged. The intact original is still archived by step 9 — the source is never page-edited.
11. **Email `mapping_success`** (`report.txt`), and **return `self.pre_result`**.

`datadate` comes from `_datadate(processing_df)` — the first non-null `DATADATE` (`YYYYMMDD` int),
falling back to the execution date in the configured timezone. Using the row's `DATADATE` (not
"today") means a replay of a past data date lands in the *right* dated folder.

**`post_execute(result)`** exports the transaction / performance / AI-operation logs via
`ExportLogging`, using `self._run_log_df` for the model version + SharePoint web URL. It is
**best-effort**: the whole call is wrapped in `try/except` and a failure is logged and swallowed.
By this point the Output Report is already on SharePoint — an audit-log hiccup must not undo
delivered business output (and must not, via a raised exception, prevent finalize from stamping).

**`on_error`:** logs, then sends `processing_failed.txt` to the `system_exception` set
(`BOT_EMAIL` → `OPER_EMAIL`, cc `USER_EMAIL, DEVELOPER_EMAIL`). Guarded with
`getattr(self, "_notifier", None)` because the error may have happened *in* `pre_execute` before
the notifier existed. The framework re-raises after `on_error` returns — which is what keeps
finalize from running.

---

### 2.3 — `TaxInvoiceRejectTask`

Runs in the **pre** pipeline, after `OCRSubmitTask`. Submit has just stamped the pre-processing
and page-manifest logs on GCS; this task reads them back, filters to **this run's `job_id`**, and
moves the IQS casualties out of the source folder.

**Config read** (`_REQUIRED_STRING_KEYS`): `gcp.project_id`, `gcs.project_id`,
`gcs.pre_processing_log_path`, `gcs.page_manifest_log_path`, the `sharepoint.source_site`
credentials, and `sharepoint.source_site.reject_path`.

**`pre_execute`** derives the bucket from the `gs://…` log path
(`self._pre_log_path[len("gs://"):].split("/")[0]`), builds a `GCSModule` for it, wraps it in the
OCR pipeline's `LogExporter` (reused as a reader), and constructs `IqsRejecter`.

**`execute_task`:** load both logs → filter each by `job_id` → `IqsRejecter.reject(...)`. The whole
call is wrapped: an exception is logged at WARNING and swallowed, because the pre pipeline's real
job (submitting the batch) is already done and must not be failed by a file-move problem.

`IqsRejecter` ([module/iqs_rejecter.py](module/iqs_rejecter.py)) splits into two paths:

- **Whole-file `REJECTED`** — every page failed IQS, *or* the extension was unsupported
  (`OCRSubmitTask` logs those `REJECTED` with `"Unsupported file type: <ext>"`; the log's `message`
  column is how you tell them apart). `copy_file` then `delete_item`; a failed copy leaves the
  source in place.
- **`PARTIAL`** — the source is left intact. Only the pages the manifest marks
  `quality_status == REJECTED` are split out with `extract_single_page` and uploaded as
  `{stem}_p{page:03d}.pdf`. The manifest→pre-log join is on
  `manifest.parent_path == pre_log.gcs_landing_path` (both are full `gs://` URIs).

Destination paths come from `helper/output_layout.reject_dest`, which — unlike the archive tree —
is **date-first with the E-TAX company/user subfolder preserved**:
`{root}/{YYYYMMDD}/{0001_username}/{name}` for E-TAX, `{root}/{YYYYMMDD}/{name}` otherwise.

**Returns** `None`. Harmless: in the pre pipeline `pre_result` is `None` throughout.

---

### 2.4 — `TaxInvoiceFactCheckTask`

A quality task, not a delivery task. It runs on its own pre/post configs against a **fixed
reference set** on the control site, and reuses `OCRSubmitTask` / `OCRRetrieveTask` /
`OCRFinalizeTask` completely unchanged. Full detail in [§7](#7--fact-check-subsystem).

**Config read** (`REQUIRED_STRING_KEYS`): `gcp.project_id`, `gcp.project_name`,
`sharepoint.control_site.ground_truth_file`, `.master_buyer_path`, `.transaction_log_file`,
`.performance_log_file`, `framework.email_template_dir`.

It shares `ReconcileTaskContext` (the source/destination blocks stay empty — the YAML decides):
everything it touches — ground truth, Master Buyer, logs — lives on the **control** site, so it
needs exactly one SharePoint connection, credentialed from `config/common.yml`'s `control:` block.

**`execute_task`:** unwrap `OCRResult` (the shared `unwrap_ocr_result`) → `load_master_buyer()`
→ `ExtractionReportBuilder().build(...)` (the *same* buyer-enrichment as reconcile) →
`load_ground_truth()` → `FactCheckEvaluator().evaluate(...)` → `emit_fact_check_logs(...)` →
**`return self.pre_result`**.

**`post_execute`** calls `ExportLogging(...).export_logs(enable_oper_log=False, p_type="AI Fact-Checker")` —
the same transaction/performance logs as reconcile, but with the batch AI-operation summary
suppressed (the fact-check emits its *own* AI-operation lines) and a distinct `type` label. Also
best-effort.

**`on_error`:** `processing_failed.txt` with subject
`[AI-Tax Invoice][Fact Check][System Exception] on {date}`, to that task's own
`system_exception` block: `BOT_EMAIL` → `DEVELOPER_EMAIL`, cc `OPER_EMAIL` (developers, not
business users — this is an internal quality job).

---

## 3 — Stage 1: the extraction report

[module/extraction_report_builder.py](module/extraction_report_builder.py) turns the OCR frame
(one row per **page × line item**) into one row per **resolved document line**, LEFT JOINs the
Master Buyer, and folds the buyer verdict into `DOC_STATUS` / `REMARK`. Output contract:
[`ExtractionProcessing`](schema/extraction_processing.py).

It is one DuckDB statement built as an f-string (message enums interpolated as SQL literals via
`_sql_literal`, which doubles single quotes). Five CTEs.

### 3.1 — `resolved_ocr` — collapse pages to documents

The window is:

```sql
WINDOW w AS (PARTITION BY FILE_PATH, TAX_INVOICE_NUMBER, CUSTOMER_TAX_ID, VENDOR_TAX_ID, DATADATE, COPY)
```

Every document-level field is resolved **window-only**:

```sql
MAX(CUSTOMER_NAME_TH) FILTER (WHERE CUSTOMER_NAME_TH IS NOT NULL) OVER w AS BUYER_NAME_TH
```

> **Why `MAX(...) FILTER(...) OVER w` and NOT `COALESCE(own_value, MAX(...) OVER w)`.**
> This is the single most important line in the file to not "improve". A multi-page invoice puts
> the header on page 1 and continuation line items on pages 2..n. A continuation page's own header
> value is blank — or worse, a stray `0.0` the model hallucinated into an empty footer. If you
> `COALESCE` the row's own value first, that stray value **survives**, and it does two kinds of
> damage: (a) it becomes a *different* `GROUP BY ALL` key in `agg_orc`, splitting one document into
> two rows; (b) it leaks into the propagated review row. Taking the window value unconditionally
> means the whole partition gets the *same* header, whatever any individual page said.

Notable derived columns in this CTE:

| Column | Rule |
|---|---|
| `TOTAL_AMOUNT` | `MAX(BEFORE_VAT_AMOUNT) FILTER (WHERE BEFORE_VAT_AMOUNT IS NOT NULL) OVER w` — i.e. the document's pre-VAT total. |
| `DOC_VAT_AMOUNT`, `NET_AMOUNT`, `WITHHOLDING_TAX` | `MAX(x) FILTER (WHERE **BEFORE_VAT_AMOUNT** IS NOT NULL) OVER w` — note the filter is on `BEFORE_VAT_AMOUNT`, not on the column itself: these are taken **from the page that carries the totals block**, so they stay mutually consistent (you never mix page 1's VAT with page 3's net). |
| `RECEIVER_SIGNATURE` | `BOOL_OR(PAYEE_SIGNATURE_FLAG OR AUTHORIZED_RECEIVER_SIGNATURE_FLAG OR AUTHORIZED_SIGNATORY_SIGNATURE_FLAG) OVER w` — any of the three signature kinds anywhere in the document. |
| `STAMP` | `BOOL_OR(STAMP) OVER w`. |
| `INVOICE_AMOUNT` | see below. |
| `VAT_INVOICE` | see below. |

**`INVOICE_AMOUNT` — the BEFORE/AFTER-VAT fallback:**

```sql
COALESCE(
      INVOICE_AMOUNT_BEFORE_VAT
    , CASE WHEN INVOICE_VAT_AMOUNT IS NULL THEN INVOICE_AMOUNT_AFTER_VAT END
  ) AS INVOICE_AMOUNT
```

"Invoice Amount" in the business spec is the **printed pre-VAT line amount**. Prefer
`INVOICE_AMOUNT_BEFORE_VAT`. Fall back to `INVOICE_AMOUNT_AFTER_VAT` **only when the line carries
no per-line VAT at all** — because in that case after == before, so the after-VAT cell is still a
valid pre-VAT value. This is a defensive net for a real failure mode: on a footer-less line the
model sometimes files its single printed amount into the AFTER slot. **Never** take AFTER when
line VAT *is* present — that would be a gross amount silently sitting in a net column.

**`VAT_INVOICE` — the per-invoice VAT:**

```sql
CASE WHEN (COUNT(TAX_INVOICE_NUMBER) OVER w = 1) AND (DOC_VAT_AMOUNT IS NOT NULL)
     THEN DOC_VAT_AMOUNT
     ELSE INVOICE_VAT_AMOUNT END AS VAT_INVOICE
```

A single-line document has no meaningful "per-line VAT" separate from the header, so its header
VAT *is* its invoice VAT. Multi-line documents use the actual per-line `INVOICE_VAT_AMOUNT`.
This column is what makes scenarios 1 and 2 possible (see [§4](#4--stage-2--the-reconciliation-engine)).

### 3.2 — `agg_orc` — group to the reporting grain

`GROUP BY ALL` over everything not aggregated. Aggregates:

- `SUM(CAST(INVOICE_AMOUNT AS DECIMAL(18,2)))`, `SUM(CAST(VAT_INVOICE AS DECIMAL(18,2)))` — sum the
  page fragments of one invoice line.
- `AVG(IQS_SCORE)`.
- Four status probes, each with its own message roll-up:
  `OCR_SUSPICIOUS` / `OCR_UNSUPPORTED` / `OCR_BLANK` / `OCR_FAILED` = `BOOL_OR(STATUS = '<x>')`,
  plus `string_agg(DISTINCT CASE WHEN STATUS = '<x>' THEN MESSAGE END, ', ')` for the first three.
- `OCR_REDACT` = `BOOL_OR(STATUS IN (SUSPICIOUS, UNSUPPORTED, FAILED))`.
- `OCR_ISSUE_FLAG` = `BOOL_OR(STATUS IN (SUSPICIOUS, UNSUPPORTED, FAILED, BLANK))` — this becomes
  `ISSUE_FLAG` in the output and is what routes a row to scenario 0.

**The pipeline-issue status-class segmentation.** This is the `IS_PIPELINE_ISSUE` key:

```sql
STATUS IN (FAILED, SUSPICIOUS, UNSUPPORTED, BLANK) AS IS_PIPELINE_ISSUE
```

It is a **grouping key** (`GROUP BY ALL` picks it up), and `redacted` drops it immediately after.
Its whole job is to *segment*: the four pipeline-issue statuses — the terminal outcomes the common
OCR pipeline emits — split into their **own** review row, separate from the invoice row. A
`SUCCESS` row (a cleanly extracted line; the OCR pipeline no longer does any domain validation of
its own) stays in the invoice group, for *this* task to validate. Without the segmentation a
single suspicious page would contaminate — and redact — the perfectly good invoice rows extracted
from the same file.

### 3.3 — `redacted` — null out the untrustworthy

Every business column is wrapped in `CASE WHEN OCR_REDACT THEN NULL ELSE col END` (booleans get
`FALSE` instead of `NULL`). If the OCR line was `SUSPICIOUS`, `UNSUPPORTED`, or `FAILED`, its
extracted content is not evidence and must not reach a report cell. Note `BLANK` is deliberately
**not** in `OCR_REDACT` — a blank line item has nothing to redact — but it *is* in
`OCR_ISSUE_FLAG`, so it still never reconciles.

### 3.4 — `master_scored` — join and score the buyer

```sql
FROM redacted ao
LEFT JOIN master_buyer mb ON norm_taxid_sql(ao.BUYER_TAX_ID) = norm_taxid_sql(mb.tax_id)
```

`LEFT`, so a buyer not in the master survives as a row with `BUYER_FOUND = FALSE`. The join key is
normalized on **both** sides (`lpad(regexp_replace(col, '[^0-9]', '', 'g'), 13, '0')`) — see
[§6.1](#61--tax-id-leading-zero) for why.

Similarity thresholds, both class constants on `ExtractionReportBuilder`
([extraction_report_builder.py:43-44](module/extraction_report_builder.py)):

| Constant | Value | Applies to |
|---|---|---|
| `BUYER_NAME_MATCH_THRESHOLD` | **0.90** | `NAME_MATCH` |
| `BUYER_ADDRESS_MATCH_THRESHOLD` | **0.80** | `ADDR_MATCH` |

The address bar is lower on purpose: addresses are long, and OCR drops or reorders sub-district
tokens far more often than it mangles a company name.

The match expression, per language, is:

```sql
(LENGTH(norm(ao.BUYER_NAME_TH)) > 0 AND COALESCE(JARO_WINKLER_SIMILARITY(norm(ao.BUYER_NAME_TH), norm(mb.company_name_th)), 0) >= 0.90)
OR (LENGTH(norm(ao.BUYER_NAME_ENG)) > 0 AND COALESCE(JARO_WINKLER_SIMILARITY(norm(ao.BUYER_NAME_ENG), norm(mb.company_name_eng)), 0) >= 0.90) AS NAME_MATCH
```

> **The `LENGTH(...) > 0` blank guard.** Do not remove it. `norm()` strips *all* whitespace, so an
> extraction column containing only spaces normalizes to `''`. Jaro-Winkler between `''` and `''`
> is **1.0** — a perfect score for two empty strings. Without the guard, a document where OCR read
> *nothing* into (say) the English buyer name, matched against a master row whose English name is
> also blank, would score a spurious `NAME_MATCH` and be stamped `Completed`. The guard requires
> the *extraction* side to actually contain something before its similarity counts. (The master
> side is not guarded — a blank master cell can only ever *lower* a real extraction's score, which
> is the correct direction.)

Either language clearing its bar is enough (`TH OR ENG`), because a given vendor's invoices may
print only one of the two.

### 3.5 — `conf_scored` — confidence and per-field remarks

**Eight confidence sub-scores**, all in `[0, 1]`:

- Binary (0 or 1): `DOC_NAME_CONF_SCORE` (non-empty), `BUYER_TAX_ID_CONF_SCORE`
  (`LEN(BUYER_TAX_ID) = 13 AND BUYER_FOUND`), `VENDOR_TAX_ID_CONF_SCORE` (`LEN = 13`),
  `TAX_INVOICE_NUMBER_CONF_SCORE` (non-empty), `TAX_INVOICE_DATE_CONF_SCORE` (non-null).
- Continuous, by exponential decay on the arithmetic residual:

  ```sql
  ROUND(EXP(-ABS(TOTAL_AMOUNT - (COALESCE(NET_AMOUNT,0) - COALESCE(VAT_AMOUNT,0) + COALESCE(WITHHOLDING_TAX,0)))), 2)
  ```

  and the two symmetric rearrangements for `VAT_AMOUNT_CONF_SCORE` and `NET_AMOUNT_CONF_SCORE`.
  The identity being tested is `NET = TOTAL + VAT - WHT`. `EXP(-|residual|)` gives **1.00** when
  the books balance to the satang and decays fast (a 1-baht error → 0.37; a 5-baht error → 0.01).
  A `NULL` amount scores 0.

`DOC_CONF_SCORE = ROUND(sum_of_8 / 8 * 100, 2)` — so a document is only "100" when every binary
check passes *and* all three amounts reconcile exactly.

**Sixteen `REMARK_*` columns**, one per field, each a small ladder of `missing → format-rule →
value-rule → NULL`, drawing text from `RequiredFieldMessage` and `ValidationMessage`
([helper/messages.py](helper/messages.py)). Example:

```sql
CASE WHEN TOTAL_AMOUNT IS NULL                THEN 'Total Amount is missing'
     WHEN TOTAL_AMOUNT < 0                    THEN 'Total Amount must be greater than or equal to 0'
     WHEN TOTAL_AMOUNT_CONF_SCORE = 0         THEN 'Total Amount is incorrect'
     ELSE NULL END AS REMARK_TOTAL_AMOUNT
```

Note `TOTAL_AMOUNT_CONF_SCORE = 0` fires only when `ROUND(EXP(-|residual|), 2)` rounds to zero,
i.e. a residual over roughly 3.9 — small rounding differences do not raise a remark.

### 3.6 — the final SELECT — `DOC_STATUS` and `REMARK`

`ExtractionStatus` ([helper/constant.py](helper/constant.py)):

| Value | Meaning |
|---|---|
| `Completed` | OCR clean **and** every field present/valid **and** the Master-Buyer name+address matched. |
| `RequiresReview` | Anything else — a human must look. |
| `Failed` | **Defined but never assigned.** See the discrepancy list in [§8](#8--reference--testing). |

The `DOC_STATUS` ladder, in order (first hit wins):

1. `OCR_FAILED` → `RequiresReview`
2. `OCR_SUSPICIOUS` → `RequiresReview`
3. `OCR_UNSUPPORTED` → `RequiresReview`
4. `OCR_BLANK` → `RequiresReview`
5. `OVERALL_CONF_SCORE != 100` → `RequiresReview`
6. `NOT (NAME_MATCH AND ADDR_MATCH)` → `RequiresReview`  ← **the Master-Buyer verdict, folded in**
7. any of the sixteen `REMARK_*` non-null → `RequiresReview`
8. else → `Completed`

The `REMARK` ladder mirrors it:

1. `OCR_FAILED` → the fixed `EXTRACTION_SYSTEM_FAILURE_REMARK` = *"Extraction failed due to a
   system error."* — **not** the raw technical cause and **not** a list of "… is missing" lines.
   A `FAILED` row has every field nulled by `redacted`, so a naive remark builder would emit
   sixteen misleading "X is missing" clauses for what is really a dead batch job. The real cause
   stays in the pre-processing log where an engineer can find it; the business user gets one
   honest line.
2. `OCR_SUSPICIOUS` → `'Suspicious: ' || OCR_SUSPICIOUS_MESSAGE`
3. `OCR_UNSUPPORTED` → `'Unsupported: ' || OCR_UNSUPPORTED_MESSAGE`
4. `OCR_BLANK` → `'Blank: ' || OCR_BLANK_MESSAGE`
5. else → `concat_ws(', ', …)` of every non-null `REMARK_*`, **plus** the four Master-Buyer
   reasons from `MappingMasterMessage`:

| Condition | Message |
|---|---|
| `BUYER_COMPANY_CODE IS NULL` | `Company code doesn't match Master Buyer` |
| `NOT BUYER_FOUND` | `Buyer Tax ID not found in Master Buyer` |
| `BUYER_FOUND AND NOT NAME_MATCH` | `Buyer Name mismatch with Master Buyer` |
| `BUYER_FOUND AND NOT ADDR_MATCH` | `Buyer Address mismatch with Master Buyer` |

The name/address mismatch messages are gated on `BUYER_FOUND` so an unknown buyer produces one
clear reason ("tax id not found") rather than three redundant ones.

### 3.7 — the full message inventory (`helper/messages.py`)

**Module constants**

| Name | Text | Used where |
|---|---|---|
| `EXTRACTION_REVIEW_REMARK` | `Extraction requires review` | `ReconciliationBuilder._extraction_columns_sql` — the fallback `Remark_AI Extract` when a non-`Completed` row somehow carries no reason, so no non-Completed row is ever left with a blank explanation. |
| `EXTRACTION_SYSTEM_FAILURE_REMARK` | `Extraction failed due to a system error.` | `ExtractionReportBuilder`, branch 1 of the `REMARK` ladder. |

**`MappingMasterMessage`** — 4 members, all used (table above).

**`RequiredFieldMessage`** — 16 members, one per required field, all of the form
`"<Field> is missing"`: `DOC_NAME`, `BUYER_NAME`, `BUYER_ADDRESS`, `BUYER_TAX_ID`,
`BUYER_BRANCH_CODE`, `BUYER_BRANCH_NAME`, `VENDOR_NAME`, `VENDOR_ADDRESS`, `VENDOR_TAX_ID`,
`VENDOR_BRANCH_CODE`, `VENDOR_BRANCH_NAME`, `TAX_INVOICE_NUMBER`, `TAX_INVOICE_DATE`,
`TOTAL_AMOUNT`, `VAT_AMOUNT`, `NET_AMOUNT`. All used.

**`ValidationMessage`** — 11 members:

| Member | Text | Used? |
|---|---|---|
| `BUYER_TAX_ID_RULE_MESSAGE` | Buyer Tax ID don't match the required format (13 digits) | ✔ |
| `BUYER_BRANCH_CODE_RULE_MESSAGE` | Buyer Branch Code don't match the required format (5 digits) | ✔ (`LEN != 5`) |
| `VENDOR_TAX_ID_RULE_MESSAGE` | Vendor Tax ID … (13 digits) | ✔ |
| `VENDOR_BRANCH_CODE_RULE_MESSAGE` | Vendor Branch Code … (5 digits) | ✔ |
| `TOTAL_AMOUNT_RULE_MESSAGE` | Total Amount is incorrect | ✔ |
| `TOTAL_AMOUNT_GT_NEGATIVE_RULE_MESSAGE` | Total Amount must be greater than or equal to 0 | ✔ |
| `VAT_AMOUNT_RULE_MESSAGE` / `..._GT_NEGATIVE_...` | VAT Amount is incorrect / must be ≥ 0 | ✔ |
| `NET_AMOUNT_RULE_MESSAGE` / `..._GT_NEGATIVE_...` | Net Amount is incorrect / must be ≥ 0 | ✔ |

**`MappingZ45Message`** — 8 members, used by `ReconciliationBuilder`:

| Group | Member | Text | Used? |
|---|---|---|---|
| Fn-3 "not match" | `COMPANY_CODE_MISMATCH_MESSAGE` | Company code does not match Z45 report | ✔ |
| | `INVOICE_NUMBER_MISMATCH_MESSAGE` | Invoice Number does not match Z45 report | ✔ (scenarios 1 & 3 only) |
| | `VENDOR_NAME_MISMATCH_MESSAGE` | Vendor Name does not match Z45 report | ✔ |
| | `VAT_AMOUNT_MISMATCH_MESSAGE` | VAT amount does not match Z45 report | ✔ |
| | `PAYMENT_DATE_MISMATCH_MESSAGE` | Payment date does not match Z45 report | ✔ |
| no candidate | `NO_MATCH_MESSAGE` | No matching record in Z45 report | ✔ |
| scenario 0 | `COPY_NOT_RECONCILED_MESSAGE` | Copy document, not reconciled with Z45 report | ✔ |
| | `ISSUE_NOT_RECONCILED_MESSAGE` | Extraction has an issue (…), not reconciled with Z45 report | ✔ |

The four Fn-4 "missing" messages are dead code today: when a row does not map, the Z45 columns are
simply left blank and the *reason* is expressed through the Fn-3 mismatch messages instead.

---

## 4 — Stage 2: the reconciliation engine

**[module/reconciliation.sql](module/reconciliation.sql) is the single source of truth for how an
extraction row is matched to Z45.** [module/reconciliation_builder.py](module/reconciliation_builder.py)
is a thin loader: it registers three frames (`extraction`, `z45`, `master_vendor`), executes the
script to create the macros + views, then runs its own **presentation** SELECTs over them. This
section is where the SQL's reasoning now lives.

### 4.1 — Why the `.sql` file is paramless

The engine SQL contains **no bind parameters** (`$name`) at all. That is forced:
**DuckDB rejects bind parameters inside `CREATE VIEW`.** So every piece of *text* — statuses,
remark messages — has to stay on the Python side, where `ReconciliationBuilder._report_params()`
can bind `MappingZ45Status` / `MappingZ45Message` values into the presentation SELECT. The split
is therefore not aesthetic:

- **`.sql`** = structure only (macros, scenario assignment, candidate views). Paramless.
- **`.py`** = presentation (the 37-column report projection, the enriched-Z45 status), with all
  user-visible strings sourced from [helper/messages.py](helper/messages.py) as bind params.

`_ENGINE_SQL` is read once at import (`Path(__file__).with_name("reconciliation.sql").read_text()`)
and executed **per `build()`** against a **fresh** `connect_decimal_safe()` connection, so views
and macros never leak between runs.

`connect_decimal_safe()` ([src/utils/duckdb_utils.py](../../src/utils/duckdb_utils.py)) sets
`pandas_analyze_sample = 1_000_000_000`, forcing a full-column scan when DuckDB infers the
width/scale of a pandas `Decimal` column. With the default 1000-row strided sample a large VAT
amount between stride points can infer a too-narrow `DECIMAL(9,2)` and the real scan overflows.
Every DuckDB connection in this package uses it.

### 4.2 — The macros

```sql
CREATE OR REPLACE MACRO norm(s) AS
    lower(regexp_replace(nfc_normalize(trim(s)), '[\s\x{200B}\x{200C}\x{200D}\x{FEFF}]', '', 'g'));

CREATE OR REPLACE MACRO vendor_sim(a, b) AS COALESCE(jaro_winkler_similarity(norm(a), norm(b)), 0);

CREATE OR REPLACE MACRO vendor_threshold() AS 0.90;

CREATE OR REPLACE MACRO vendor_match(a_eng, b_eng, a_th, b_th) AS
    vendor_sim(a_eng, b_eng) >= vendor_threshold() OR vendor_sim(a_th, b_th) >= vendor_threshold();

CREATE OR REPLACE MACRO month_match(d1, d2) AS
    COALESCE(year(d1) = year(d2) AND month(d1) = month(d2), FALSE);
CREATE OR REPLACE MACRO exact_match(d1, d2) AS COALESCE(d1 = d2, FALSE);
```

| Macro | What it does and why |
|---|---|
| `norm(s)` | Lowercase (Latin only — Thai has no case), NFC-normalize (unifies tone/vowel ordering), then strip **all whitespace and the four zero-width codepoints** ZWSP `U+200B`, ZWNJ `U+200C`, ZWJ `U+200D`, BOM `U+FEFF`. Those render identically to nothing but otherwise tank the similarity score. Written with visible `\x{...}` escapes (DuckDB uses **RE2** regex syntax). |
| `vendor_sim(a,b)` | Null-safe Jaro-Winkler over two normalized names, `0` when either side is NULL. |
| `vendor_threshold()` | **0.90.** This is the single tuning point for vendor matching — change it here and every call site moves. |
| `vendor_match(...)` | ENG-vs-ENG **OR** TH-vs-TH clears the threshold. The two sides can be *different columns*: the master has split `vendor_name_eng` / `vendor_name_th`, while Z45 has a single `vendor_name` (which may be in either script) — so both calls pass `zz.vendor_name` as the right-hand side. |
| `month_match(d1,d2)` | Same year **and** same month. Used by scenarios **1–4**. |
| `exact_match(d1,d2)` | Same day. Used by scenario **5 only**. |

**Why month, not day, for scenarios 1–4?** Because the two dates being compared are *different
things*: the extraction side is the **tax-invoice date** (when the vendor issued the document) and
the Z45 side is the **payment date** (when treasury paid it). Those are never the same day in
practice; they are the same accounting month. Scenario 5 is the exception — see
[§4.6](#46--scen_five--vendor-in-master-the-special-case).

### 4.3 — `scenario_mapping`

Every extraction row gets exactly one `SCENARIO` (0–5) and a stable `_er_id`.

**`_er_id`** — `row_number() OVER (ORDER BY FILE_NAME, TAX_INVOICE_NUMBER)`. It is the extraction
row's identity for every window in every scenario view. Row-count invariant: the final report
keeps **one row per `_er_id`**.

**`INVOICE_NUMBER` cast:**

```sql
SELECT * REPLACE (CAST(INVOICE_NUMBER AS VARCHAR) AS INVOICE_NUMBER), …
```

An all-NULL frame column arrives from DuckDB typed `INTEGER`, and `INVOICE_NUMBER` is used with
`trim()` and string equality below — which would blow up. The `* REPLACE` guard is the same
all-NULL defence the report applies to `REMARK` (see [§5.2](#52--the-all-null-column-cast-guards)).

**`IN_MASTER`** — a separate CTE, LEFT JOINing every extraction row against every master-vendor row
on `vendor_match(...)` and reducing with `BOOL_OR(mv.vendor_code IS NOT NULL)`. It answers one
question: *did this row's vendor name clear 0.90 against **any** master row?*

**The scenario ladder** (a `CASE`, so **first match wins**; the physical order of the branches is
`0 → 5 → 1 → 2 → 3 → 4`):

```sql
CASE
  WHEN ext.ISSUE_FLAG IS TRUE OR ext.COPY IS TRUE THEN 0
  WHEN im.IN_MASTER                               THEN 5
  WHEN ext.VAT_INVOICE IS NOT NULL
       AND ext.INVOICE_NUMBER IS NOT NULL
       AND trim(ext.INVOICE_NUMBER) <> ''         THEN 1
  WHEN ext.VAT_INVOICE IS NOT NULL                THEN 2
  WHEN ext.INVOICE_NUMBER IS NOT NULL
       AND trim(ext.INVOICE_NUMBER) <> ''         THEN 3
  ELSE                                                 4
END AS SCENARIO
```

| Scenario | Condition | Shape |
|---|---|---|
| **0** | `ISSUE_FLAG` **or** `COPY` | Never reconciled. Blank Z45 fields, blank `Mapping_Status`. |
| **5** | vendor is in the Master Vendor list | The "special" case — treasury pays these vendors on a schedule, so the whole `(date, buyer, vendor)` group settles as one payment document. |
| **1** | has per-invoice VAT **and** a non-blank invoice number | The richest case: both keys available. |
| **2** | has per-invoice VAT, no invoice number | |
| **3** | has an invoice number, no per-invoice VAT | |
| **4** | neither | The weakest case: only company + vendor + month. |

Note the ordering consequence: **`IN_MASTER` outranks everything except scenario 0.** A master
vendor's invoice goes to scenario 5 even when it has both a VAT and an invoice number.

**`_doc_first`** — the single subtlest line in the file:

```sql
, (row_number() OVER (
      PARTITION BY FILE_NAME, TAX_INVOICE_NUMBER, BUYER_TAX_ID, VENDOR_TAX_ID, COPY
      ORDER BY (VAT_AMOUNT IS NULL), INVOICE_NUMBER NULLS LAST
  ) = 1) AS _doc_first
```

*What it flags:* exactly **one row per document**. Every line item of a document repeats the same
header `VAT_AMOUNT`; `_doc_first` marks one of them so that summing header VAT across a group
counts each document **once** instead of once per line item.

*Why the PARTITION deliberately includes `BUYER_TAX_ID` / `VENDOR_TAX_ID` / `COPY`, not just
`(FILE_NAME, TAX_INVOICE_NUMBER)`:* the partition is the **full document identity — the same grain
the report builder groups on**. If you narrowed it, a page-level buyer↔vendor misread (the model
swaps them on one page of a file) — which the report builder *already* split into two separate
rows — would collapse back into one partition here. The arbitrary tie-break could then hand
`_doc_first` to the **header-less phantom row**, dropping the real header VAT out of
`EXT_TOTAL_VAT` on *some* runs. That is a run-to-run `Completed`/`Incompleted` flip on identical
input, and it is exactly the kind of bug that costs a week to find.

*Why the `ORDER BY` is what it is:* `(VAT_AMOUNT IS NULL)` sorts `FALSE` (0) before `TRUE` (1), so a
row that **actually carries the header VAT** wins. `INVOICE_NUMBER NULLS LAST` is the secondary
tiebreak, so the flagged row is still deterministic even when every `INVOICE_NUMBER` is NULL.

**`EXT_TOTAL_VAT`:**

```sql
, SUM(ext.VAT_AMOUNT) FILTER (
      WHERE ext._doc_first AND ext.ISSUE_FLAG IS NOT TRUE AND ext.COPY IS NOT TRUE
  ) OVER (
      PARTITION BY ext.TAX_INVOICE_DATE, ext.BUYER_TAX_ID, ext.VENDOR_TAX_ID
  ) AS EXT_TOTAL_VAT
```

The extraction-side VAT total for one `(date, buyer, vendor)` group — each document's header VAT
counted exactly once (via `_doc_first`). This is the value scenario 5 compares against a Z45
payment document's total. **Copies and issue rows are excluded** because they never reconcile: a
copy of an invoice already counted would double the extraction side of the comparison and turn a
perfectly good match into a mismatch.

### 4.4 — The shared `base → scored → verdict` shape

All five `scen_*` views are the **same three-CTE chain**, deliberately flat and self-contained so a
developer can read or edit one scenario in isolation without understanding the other four. Only
two things differ per scenario: what goes into `ALLKEYS`, and the `CAND_VAT_OK` expression.

**`base`** — LEFT JOIN each `scenario_mapping` row (filtered to `WHERE sc.SCENARIO = n`) to `z45`,
pull the ten `Z_*` columns, and compute the per-candidate key flags.

The join condition is **identical in all five views** and is deliberately **loose**:

```sql
LEFT JOIN z45 zz ON (COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)
    OR (sc.INVOICE_NUMBER IS NOT NULL AND trim(sc.INVOICE_NUMBER) <> '' AND sc.INVOICE_NUMBER = zz.ref_doc_inv))
```

It is a *candidate generator*, not the match rule. It says "same company **OR** same invoice ref" —
enough to pull in every plausible Z45 line while keeping the join from becoming a full cross
product. The **real** matching happens in the `K_*` flags. Two consequences: (a) a `LEFT` join
means a row with zero candidates still survives, with `HAS_ROW = FALSE`; (b) it is the `OR` that
lets a row with a *wrong* company code still find its invoice ref and get the precise
`Company code does not match Z45 report` remark, instead of a useless `No matching record`.

Per-candidate key flags:

| Column | Expression | Notes |
|---|---|---|
| `K_COMPANY` | `COALESCE(sc.BUYER_COMPANY_CODE = zz.company, FALSE)` | `BUYER_COMPANY_CODE` comes from the Master Buyer join in Stage 1. |
| `K_VENDOR` | `vendor_match(sc.VENDOR_NAME_ENG, zz.vendor_name, sc.VENDOR_NAME_TH, zz.vendor_name)` | Both sides against Z45's single `vendor_name`. |
| `K_INVOICE` | `COALESCE(sc.INVOICE_NUMBER = zz.ref_doc_inv, FALSE)` | Computed in **every** view, but only *in* `ALLKEYS` for scenarios 1 and 3. |
| `K_DATE` | `month_match(...)` (1–4) / `exact_match(...)` (5) | tax-invoice date vs Z45 payment date. |
| `ALLKEYS` | AND of this scenario's keys | See the per-scenario table. |
| `HAS_ROW` | `(zz._z_id IS NOT NULL)` | This candidate row actually joined a Z45 line. |

**`scored`** — three window aggregates over `PARTITION BY _er_id`:

```sql
, COALESCE(BOOL_OR(ALLKEYS) OVER (PARTITION BY _er_id), FALSE) AS ER_MATCHED
, COALESCE(BOOL_OR(HAS_ROW) OVER (PARTITION BY _er_id), FALSE) AS HAS_CANDIDATE
, <scenario-specific CAND_VAT_OK>
```

- **`ER_MATCHED`** — *some* candidate for this extraction row matched **all** of the scenario's
  keys. The keys aligned; the VAT may still be wrong.
- **`HAS_CANDIDATE`** — this extraction row joined *any* Z45 line at all. This is what
  distinguishes "we found the payment but the company code is wrong" from "this document does not
  appear in Z45 at all", and it gates every mismatch remark in `_mapping_tail_sql`.
- **`CAND_VAT_OK`** — this *candidate* satisfies both `ALLKEYS` and the scenario's VAT rule.

**`verdict`** — `ER_VAT_OK = BOOL_OR(CAND_VAT_OK) OVER (PARTITION BY _er_id)`, then:

```sql
SELECT *, (ER_MATCHED AND ER_VAT_OK) AS ER_MAPPED FROM verdict;
```

**`ER_MAPPED` is the whole verdict.** A row is `Completed` only when the keys aligned **and** the
VAT verified. On a VAT mismatch the row is `Incompleted` and the Z45 fields are left blank — the
`_mapping_tail_sql` wraps every Z45 column in `CASE WHEN ER_MAPPED THEN … END`. That is
intentional: the business must not see a payment document attached to a row whose amount does not
tie out.

### 4.5 — Why the views stay candidate-grain

A `scen_*` view is **candidate-grain**: one row per (extraction row × Z45 candidate). It is *not*
reduced to one row per extraction row, and that is on purpose. The enriched-Z45 sheet needs to
know, for **every** participating Z45 line, whether it fed a Completed or an Incompleted match —
so it needs all the candidate rows, not just the representative one.

The `QUALIFY` that picks a single representative row lives in **Python**
(`ReconciliationBuilder._report_pick`, see [§5.3](#53--_report_pick-and-the-tie-break)), applied
only to the report projection. Do not push it down into the views.

### 4.6 — The five scenarios

| # | Name | `ALLKEYS` | Date rule | VAT rule (`CAND_VAT_OK`) |
|---|---|---|---|---|
| 1 | Vat Invoice + Invoice Number | `K_COMPANY AND K_VENDOR AND K_DATE AND K_INVOICE` | `month_match` | `VAT_INVOICE = Z_VAT_AMOUNT` (per line, exact) |
| 2 | Vat Invoice, no Invoice Number | `K_COMPANY AND K_VENDOR AND K_DATE` | `month_match` | `VAT_INVOICE = SUM(Z_VAT_AMOUNT) FILTER (ALLKEYS) OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT)` |
| 3 | Invoice Number, no Vat Invoice | `K_COMPANY AND K_VENDOR AND K_DATE AND K_INVOICE` | `month_match` | `VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (ALLKEYS) OVER (PARTITION BY FILE_NAME, TAX_INVOICE_NUMBER)` |
| 4 | neither | `K_COMPANY AND K_VENDOR AND K_DATE` | `month_match` | `VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (ALLKEYS) OVER (PARTITION BY _er_id, Z_PAYMENT_DATE, Z_VENDOR_NAME)` |
| 5 | special (vendor in master) | `K_COMPANY AND K_VENDOR AND K_DATE` | **`exact_match`** | `EXT_TOTAL_VAT = SUM(Z_VAT_AMOUNT) FILTER (ALLKEYS) OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT)` |
| 0 | copy / issue | — (never joined to Z45) | — | — |

Note `K_INVOICE` is **computed** in all five views but only appears in `ALLKEYS` for 1 and 3 — and
correspondingly, the `Invoice Number does not match Z45 report` remark is emitted only for
`SCENARIO IN (1, 3)`.

#### `scen_one` — per-line exact VAT

```sql
, (ALLKEYS AND COALESCE(VAT_INVOICE = Z_VAT_AMOUNT, FALSE)) AS CAND_VAT_OK
```

The strongest case. Both the invoice ref and the per-line VAT are present, so each extraction line
should tie one-to-one to exactly one Z45 line, amount included. No summing, no windows — a plain
scalar equality.

#### `scen_two` — sum by payment document, scoped to `_er_id`

```sql
, (ALLKEYS AND COALESCE(VAT_INVOICE = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
      OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT), FALSE)) AS CAND_VAT_OK
```

We have a per-invoice VAT but no invoice ref to pin it to a single Z45 line, so we compare it
against the **whole payment document's** matched VAT total. `Z_PAYMENT_DOCUMENT` in the partition
groups the Z45 lines that were paid together; `FILTER (WHERE ALLKEYS)` restricts the sum to lines
that actually matched the keys (so an unrelated same-company line in the same payment run doesn't
inflate it).

**`_er_id` is in the partition on purpose.** In a candidate-grain view a single Z45 line can appear
under *several* extraction rows. Without `_er_id` in the partition, that line's VAT would be summed
once per extraction row that touched it — double-counting. Scoping the window to `_er_id` keeps
each extraction row's sum to *its own* view of the payment document.

#### `scen_three` — header VAT vs a document-wide sum

```sql
, (ALLKEYS AND COALESCE(VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
      OVER (PARTITION BY FILE_NAME, TAX_INVOICE_NUMBER), FALSE)) AS CAND_VAT_OK
```

The only scenario whose window is **not** partitioned by `_er_id`, and the reason is worth
understanding.

Here there is no per-line VAT — only the document's single header `VAT_AMOUNT`, which a
multi-invoice voucher **repeats on every line item** (each line item is its own `_er_id`). A
per-`_er_id` sum would collapse to that one line's own matched Z45 line, whose VAT is a *fraction*
of the header — so it would never equal the header and every such voucher would fail. Partitioning
by the **document** (`FILE_NAME, TAX_INVOICE_NUMBER`) sums the matched Z45 lines across all the
voucher's invoice refs, so the header total reconciles against the aggregate. That is the correct
business semantics: one grand-total VAT covering several invoice references.

Keeping `FILTER (WHERE ALLKEYS)` — which *includes* `K_INVOICE` in this scenario — scopes the sum
to exactly the document's own invoice refs (each line matches its one Z45 line), not to the
vendor's other same-month invoices. And because `ALLKEYS` already implies same-vendor and
same-month, dropping the date/vendor sub-partition is safe.

> **`ASSUMPTION:` a voucher's line items carry distinct `INVOICE_NUMBER`s.** Two line items sharing
> the same invoice ref would double-count that Z45 line in the sum. DuckDB has **no
> `SUM(DISTINCT ...) OVER (...)`**, so this cannot be defended against inside the window — it would
> take a restructure into a pre-aggregated CTE. If a customer starts issuing vouchers with repeated
> refs on separate lines, scenario 3 is where it will break, and this is the note that tells you why.

#### `scen_four` — sum by payment date + vendor

```sql
, (ALLKEYS AND COALESCE(VAT_AMOUNT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
      OVER (PARTITION BY _er_id, Z_PAYMENT_DATE, Z_VENDOR_NAME), FALSE)) AS CAND_VAT_OK
```

The weakest case: no invoice ref, no per-line VAT. All we have is the document's header VAT, and
all we can group Z45 by is `(payment date, vendor)` — the coarsest defensible bucket. `_er_id` is
back in the partition for the same anti-double-count reason as scenario 2.

#### `scen_five` — vendor in master, the special case

```sql
, exact_match(sc.TAX_INVOICE_DATE, zz.payment_date) AS K_DATE     -- NOT month_match
…
, (ALLKEYS AND COALESCE(EXT_TOTAL_VAT = SUM(Z_VAT_AMOUNT) FILTER (WHERE ALLKEYS)
      OVER (PARTITION BY _er_id, Z_PAYMENT_DOCUMENT), FALSE)) AS CAND_VAT_OK
```

Master vendors are settled on a **known schedule**, so the tax-invoice date and the payment date
genuinely coincide — which is why this is the **only** scenario using `exact_match`, and why it can
afford to.

The comparison is group-to-group: `EXT_TOTAL_VAT` (the extraction side's whole
`(date, buyer, vendor)` VAT total, each document's header counted once) against the Z45 payment
document's matched total. Structurally, then, **one Z45 line can map several tax invoices** in this
scenario — that is not a bug, it is the design, and [§5.5](#55--the-enriched-z45) depends on it.

#### Scenario 0 — never joined to Z45

Copies and issue-flagged rows are handled entirely in Python, by
`ReconciliationBuilder._scen_zero_report()`, which selects straight from `scenario_mapping` and
appends `_blank_tail_sql()`: all four Z45 columns `NULL`, `mapping_status` `NULL`, and a
`remark_mapping` that explains the skip:

```sql
, CASE WHEN ISSUE_FLAG IS TRUE THEN $remark_issue_skip
       WHEN COPY IS TRUE       THEN $remark_copy_skip END AS remark_mapping
```

The issue message wins over the copy message when a row is both — the data-quality problem is the
actionable one, and the row's `Remark_AI Extract` column already spells out the specifics. Because
scenario 0 *is* `ISSUE_FLAG OR COPY`, exactly one branch always fires.

---

## 5 — Stage 3: output contracts

### 5.1 — `ReportOutput` — the 37-column Output Report

[schema/report_output.py](schema/report_output.py). **37 columns, every one a nullable
`Series[str]`, `coerce = True`, `strict = True`.** Field order is the contract:
`ReconciliationBuilder._to_aliased(...)` renames the builder's `snake_case` columns onto the
schema's aliases *positionally*, by zipping `model.__annotations__` against
`model.to_schema().columns` — so **reordering fields silently mis-labels columns**.

**Why all-string.** The frame mixes `'Yes'`/`'No'` flags, free-text remarks, `dd/MM/yyyy` dates and
plain numeric strings in the same table, and columns like Withholding Tax legitimately hold either
a number or a blank. A uniform string contract is the only one that can hold `'No'` next to a
numeric value in a sibling column.

**The `_blank_na` dataframe_parser:**

```python
@pa.dataframe_parser
def _blank_na(cls, df: pd.DataFrame) -> pd.DataFrame:
    df = df.astype(object)
    return df.where(df.notna(), "")
```

Cast to `object` **first**, across **all** columns, then blank-fill. Both parts matter:

- An **all-NULL column arrives from DuckDB typed `Int32`** (not `object`). A naive
  `.astype(str)` on it yields the literal strings `'None'` / `'nan'` in the delivered workbook —
  which a business user will read as data.
- Filtering to only `object` columns would skip exactly those all-NULL numeric columns, i.e. the
  ones that need it most.

**The `'Yes'`/`'No'` flags** — `copy`, `receiver_signature`, `stamp`:

```sql
CASE WHEN COPY IS TRUE THEN 'Yes' ELSE 'No' END AS copy
```

Note the `ELSE`: **NULL maps to `'No'`, not to blank.** A nullable boolean that was never
determined is reported as "No", by design (the business reads a blank as "not applicable"; here
"we did not see a stamp" *is* "No").

**Two different date formats, on purpose:**

| Column | Format | Source |
|---|---|---|
| `tax_invoice_date` | `strftime(TAX_INVOICE_DATE, '%d/%m/%Y')` → `02/03/2026` | The tax invoice's own date — Thai business convention, slashes. |
| `payment_date` | `strftime(Z_PAYMENT_DATE, '%d.%m.%Y')` → `02.03.2026` | The Z45 payment date — kept in **SAP's** `dd.mm.yyyy` form so it matches the source report cell-for-cell. |

Do not "harmonize" them; the difference is what tells a reader which system a date came from.

**`send_date`** is hard-coded blank (`'' AS send_date -- Left blank for the user to fill in manually`)
in both `_mapping_tail_sql` and `_blank_tail_sql`. It is a human-entry column, not an AI output.

### 5.2 — The all-NULL-column CAST guards

Two places where DuckDB's typing of an all-NULL frame column would otherwise crash the query:

**`REMARK`** — in `_extraction_columns_sql`:

```sql
, CASE WHEN DOC_STATUS <> $ext_completed AND (REMARK IS NULL OR trim(CAST(REMARK AS VARCHAR)) = '')
       THEN $review_remark
       ELSE NULLIF(CAST(REMARK AS VARCHAR), '') END AS remark_ai_extract
```

Without the `CAST(... AS VARCHAR)`, a run where every row happened to be `Completed` (so `REMARK`
is entirely NULL) types the column `INTEGER` and `trim()` raises. The same guard is applied to
`TAX_INVOICE_NUMBER` in `_scen_candidates_sql`.

**`INVOICE_NUMBER`** — in `scenario_mapping`, via `* REPLACE`:

```sql
SELECT * REPLACE (CAST(INVOICE_NUMBER AS VARCHAR) AS INVOICE_NUMBER), …
```

`* REPLACE` is used here rather than listing every column, because `scenario_mapping` deliberately
forwards `ext.*` and we only want to retype one column.

### 5.3 — `_report_pick` and the tie-break

Each scenario branch of the report is a `SELECT … FROM scen_x QUALIFY row_number() OVER (...) = 1`,
collapsing the candidate-grain view to one representative row per `_er_id`. The ordering:

```sql
QUALIFY row_number() OVER (
    PARTITION BY _er_id
    ORDER BY
        ALLKEYS DESC
        , CASE WHEN CAND_VAT_OK THEN 0 ELSE 1 END
        , vendor_sim(VENDOR_NAME_ENG, Z_VENDOR_NAME) DESC
        , vendor_sim(VENDOR_NAME_TH,  Z_VENDOR_NAME) DESC
        , Z_PAYMENT_DOCUMENT NULLS LAST
        , _z_id
) = 1
```

Read it as a preference list: prefer an all-key match; among those prefer the candidate that
actually **satisfied this scenario's VAT rule** (so the exported Z45 fields come from the row that
verified, not a sibling that merely shared the keys); then best vendor similarity, English first;
then a non-null payment document.

> **`_z_id` closes the ordering, and it must.** Fully tied candidates — same keys, same VAT
> verdict, same similarity, same payment document — would otherwise be picked **arbitrarily** by
> DuckDB, and the arbitrary choice can differ between executions. That means the exported Z45
> columns (Invoice Document, Vendor Code…) would change between two runs on **identical input**.
> `_z_id` is a stable integer assigned in Python (`z45_keyed["_z_id"] = range(len(z45_keyed))`)
> precisely because `row_number() OVER ()` is not guaranteed identical across separate executions.

The six branches (five scenarios + scenario 0) are `UNION ALL`-ed into one 37-column report.

### 5.4 — `Z45Output` + `Z45_OUTPUT_HEADERS` — the duplicate-header dance

[schema/z45_output.py](schema/z45_output.py). The enriched Z45 must be a **faithful re-export** of
the SAP source with one extra column appended (`Mapping Tax Invoice Status`). Faithful includes the
source's warts — most notably **two identical, truncated `Tax Cleari` headers** (one holds the
clearing *document*, the next holds the clearing *date*).

**Pandera cannot validate that frame.** It keys its schema by `alias`, so two model fields sharing
an alias **collapse into one column** — a `Z45Output` with N fields would expose N−1 schema columns,
and validation of the real duplicate-header frame becomes impossible.

The workaround, in `ReconciliationBuilder._finalize_z45`:

```python
expected = list(Z45Output.__annotations__.keys())          # 1. the unique field-name order
if list(df.columns) != expected:                           # 2. validate completeness + order
    raise ValueError(f"Z45 output columns mismatch: {list(df.columns)} != {expected}")
df = df.astype(object).where(df.notna(), "")               # 3. blank-fill (same reason as ReportOutput)
df.columns = Z45_OUTPUT_HEADERS                            # 4. assign the export headers POSITIONALLY
return df
```

So `Z45Output` documents the typed contract and the canonical field order; `Z45_OUTPUT_HEADERS` is
the literal header row with duplicates preserved. They are kept in lock-step **by hand** — if you
add a field to one, add it to the other at the same index.

> **The consequence you will trip over:** once headers are assigned, the frame has duplicate column
> labels, so `df["Tax Cleari"]` returns *two* columns. Any code touching the enriched frame must
> locate columns **by index, never by name**. `OutputExporter._z45_sort_keys` does exactly that:
>
> ```python
> fields = list(Z45Output.__annotations__)
> z45_df.iloc[:, fields.index(field)]        # positional, not z45_df[field]
> ```

### 5.5 — The enriched Z45

`_z45_status_sql()` computes, per `_z_id`, a **tri-state** status over the UNION of all five
scenario views' candidates:

```sql
CASE WHEN BOOL_OR(ALLKEYS AND ER_MAPPED)  THEN $status_completed
     WHEN BOOL_OR(ALLKEYS AND ER_MATCHED) THEN $status_incompleted
     ELSE '' END AS mapping_tax_invoice_status
```

- **`Completed`** — this Z45 line participated in a fully-mapped extraction row.
- **`Incompleted`** — its keys matched an extraction row, but the VAT did not verify.
- **`''` (blank)** — nothing matched it.

Grouping stays on `_z_id` **alone**. Grouping by the keys as well would emit one row per candidate
combination and fan out the `_z_id` join.

**The tax-invoice-number aggregation:**

```sql
, array_to_string(
      list_sort(list_distinct(
          array_agg(tax_invoice_number) FILTER (
              WHERE ALLKEYS AND ER_MAPPED
              AND tax_invoice_number IS NOT NULL AND tax_invoice_number <> ''
          )
      )), ', '
  ) AS tax_invoice_number
```

The `FILTER` is the *same* `ALLKEYS AND ER_MAPPED` predicate that drives `Completed`, so an
Incompleted or unmatched line's value stays NULL.

**Why not `MAX`?** Because **one Z45 line can legitimately map several distinct tax invoices** —
structurally so in scenario 5, where a single payment document settles a whole
`(date, buyer, vendor)` group of documents against `EXT_TOTAL_VAT`. `MAX` would silently drop all
but one of them, and the user would never know a tax invoice went missing from their report. So the
values are **de-duplicated** (`list_distinct`), **sorted** (`list_sort` — for run-to-run
determinism), and **joined with `', '`**. Single-valued lines (scenarios 1–4, normal case) simply
come back as their one number.

**What is reconciled and what is not** (`_z45_sql()`):

```sql
, COALESCE(s.tax_invoice_number, z.tax_invoice_number) AS tax_invoice_number   -- mapped value WINS
, z.tax_id      AS tax_id          -- untouched
, z.branch_code AS branch_code     -- untouched
```

Only `tax_invoice_number` is reconciled, and the mapped value wins — but via `COALESCE`, so when
nothing mapped the **source cell is preserved**. A re-run therefore never erases what SAP or manual
work already filled in. `tax_id` and `branch_code` are returned **verbatim** from the Z45 source:
no mapping, no transformation. That is a deliberate business decision — the user's data is trusted.

`ORDER BY z._z_id` keeps the frame in source-row order, so **its positional index equals `_z_id`** —
which is what lets `OutputExporter` slice it with `.iloc[order]`.

### 5.6 — `z45_link_df` — the authoritative attribution

`_z45_link_sql()` returns one row per `(Z45 line, document)` pair whose scenario keys all matched:

```sql
SELECT DISTINCT _z_id, FILE_NAME AS file_name
FROM ( <UNION of all five scen_* views> )
WHERE _z_id IS NOT NULL AND ALLKEYS
ORDER BY _z_id, file_name
```

Because `ALLKEYS` on a candidate implies `ER_MATCHED`, the linked `_z_id`s are **exactly** the lines
the tri-state status marks `Completed` or `Incompleted` — attributed to the document (`FILE_NAME`)
that matched them.

> **This is the authoritative attribution, and the exporter must use it.** The tempting alternative —
> fill each document's VAT workbook with the Z45 rows whose `ref_doc_inv` equals one of the
> document's invoice numbers — **silently drops every scenario 2, 4, and 5 document**, because those
> reconcile at the header/group level and have no line-item invoice ref to equate on. Their VAT
> workbooks would come out empty and nobody would get an error.

### 5.7 — `OutputExporter` routing

[module/output_exporter.py](module/output_exporter.py). Each report row is annotated (in DuckDB)
with `OUTPUT_PATH` (the `Extract&Mapping` workbook) and `Z45_OUTPUT_PATH` (the `VAT Report`
workbook), derived from its source `FILE_PATH` + `DATADATE`:

```sql
WITH pf AS (SELECT DISTINCT FILE_NAME, FILE_PATH, DATADATE FROM processing_df)
SELECT rdf.*
    , CASE
        WHEN contains(pf.FILE_PATH, 'E-TAX') THEN '/Extract&Mapping_E-TAX/' || pf.DATADATE || '/'
            || COALESCE(TRIM(SPLIT(SPLIT(pf.FILE_PATH, '/')[-2], '_')[2]), '0000') || '_' || pf.DATADATE || '_'
            || COALESCE(TRIM(SPLIT(SPLIT(pf.FILE_PATH, '/')[-2], '_')[1]), 'UNKNOW') || '_Output_ETAX.xlsx'
        WHEN contains(pf.FILE_PATH, 'Paper [Scan]') THEN '/Extract&Mapping_Paper [Scan]/' || pf.DATADATE || '/'
            || COALESCE(split_part(pf.FILE_NAME, '.', -2), pf.FILE_NAME) || '_Output.xlsx'
        ELSE NULL END AS OUTPUT_PATH
    , … same shape for Z45_OUTPUT_PATH …
FROM report_df rdf
LEFT JOIN pf ON rdf."File Name" = pf.FILE_NAME
```

**Two routing modes:**

- **E-TAX — merge by source folder.** The path is built from the *folder* (`SPLIT(FILE_PATH,'/')[-2]`,
  e.g. `0001_pornpa8`), split on `_` and reassembled as
  `{segment[2]}_{DATADATE}_{segment[1]}_Output_ETAX.xlsx`. Every file in one company/user folder
  therefore lands on the **same** path and merges into a single workbook.
  Worked example (from `tests/.../test_output_exporter.py`): source
  `/AI TAX Invoice/Input_TAX Invoices/E-TAX/0001_pornpa8/inv1.pdf`, `DATADATE=20260605` →
  `/dest/Extract&Mapping_E-TAX/20260605/pornpa8_20260605_0001_Output_ETAX.xlsx` and
  `/dest/VAT Report_E-TAX/20260605/pornpa8_20260605_0001_Output_Z45_ETAX.xlsx`.
- **Paper [Scan] — one-to-one.** The path is built from the source filename stem
  (`split_part(FILE_NAME, '.', -2)`), so every scanned document gets its own workbook.
- Anything matching neither → `OUTPUT_PATH = NULL`; those rows are counted, logged at WARNING, and
  **skipped**.

> **The `pf` DISTINCT CTE is a guard, not a cleanup.** `processing_df` has one row per *document
> line*, so joining `report_df` to it directly on `FILE_NAME` would multiply every report row by the
> number of lines in its file — a cartesian blow-up. `SELECT DISTINCT FILE_NAME, FILE_PATH, DATADATE`
> reduces the lookup to one row per file first. Do not inline it.

**VAT workbook contents** (`_export_vat`): `_linked_row_ids(grp, link, n_rows)` takes the group's
`File Name`s, looks them up in `z45_link_df`, and returns the sorted `_z_id`s — dropping
out-of-range ids defensively so a mismatched frame cannot raise. Those become **positional** slices
of `z45_enriched_df` (`.iloc[order]`), ordered by `vendor_name` then `ref_doc_inv` (both located by
*index*, per [§5.4](#54--z45output--z45_output_headers--the-duplicate-header-dance)), with the row
position as the final tiebreak.

When a group has **no** linked Z45 rows (or the Z45 source is empty), a **header-only workbook** is
still written (`z45_enriched_df.iloc[0:0]`) — so every document gets a VAT file, and the absence of
a match is visible as an empty report rather than as a missing file.

`Extract&Mapping` rows are sorted by `["File Name", "Tax Invoice Number"]` (stable) and projected
onto `_REPORT_COLUMNS` — which drops the SQL-added `OUTPUT_PATH`/`Z45_OUTPUT_PATH` helper columns.

> **Per-workbook upload failures are swallowed.** `_upload` catches everything and logs at WARNING.
> One SharePoint hiccup on one company's workbook does not abort the other twenty. The trade-off is
> real and you should know it: **a partial export looks like a success from the outside**, and — via
> the return-`pre_result` chain — finalize will still stamp those files `SUCCESS`. If workbooks go
> missing, grep the run's logs for `SharePoint upload failed`.

---

## 6 — Matching gotchas

These are the three that bite everyone.

### 6.1 — Tax-id leading zero

Thai tax IDs are 13 digits and **frequently start with `0`** (e.g. `0105553045044`). Excel reads
the master file's `Tax ID` column as a *number*, which drops the leading zero (→ `105553045044`)
and can append a `.0`. The OCR side, meanwhile, produces a 13-character string. Joined naively,
they never match, and every buyer/vendor silently falls out of the master.

**Three defenses, all of which must stay:**

1. **On read** — `ReportSourceLoader.load_master_buyer()` passes `dtype={"Tax ID": str}` to
   `pd.read_excel` so pandas never infers a numeric type in the first place.
   (`load_master_vendor()` does the same for `Vendor code`.)
2. **On the join** — `norm_taxid_sql` ([helper/sql_normalize.py](helper/sql_normalize.py)) is
   applied to **both** sides:
   ```sql
   lpad(regexp_replace(col, '[^0-9]', '', 'g'), 13, '0')
   ```
   Strip everything that is not a digit (kills a stray `.0`, dashes, spaces), then left-pad to 13.
3. **In Python** — `ValueNormalizer._normalize_taxid` does the identical thing
   (`re.sub(r"\D", "", ...)` then `.zfill(13)`) so the fact-check comparison agrees with what
   reconcile actually matched on.

### 6.2 — Thai text: `lower()` is a red herring

Thai script **has no case**, so `lower()`/`UPPER()` are effectively no-ops on it — they only help
embedded Latin. What actually depresses a Jaro-Winkler score on Thai company names and addresses is
**spacing variants** (Thai is written without word spaces, but data entry adds them inconsistently)
and **invisible characters** — zero-width space, zero-width non-joiner, zero-width joiner, and the
BOM, which render as nothing but count as characters.

So `norm` does: NFC-normalize (unifies tone-mark / vowel ordering, which can be encoded two ways
for the same visible glyph), then strip **all whitespace plus ZWSP/ZWNJ/ZWJ/BOM**, then lowercase.

> **⚠ WARNING — the same rule is implemented in three places, kept in sync BY HAND.**
>
> | Location | Form |
> |---|---|
> | [module/reconciliation.sql](module/reconciliation.sql) `norm()` macro | Visible RE2 escapes: `'[\s\x{200B}\x{200C}\x{200D}\x{FEFF}]'` |
> | [helper/sql_normalize.py](helper/sql_normalize.py) `norm_text_sql` | The zero-width characters embedded **literally** in the Python source string: `whitespace_class = "[\\s​‌‍﻿]"` |
> | [module/value_normalizer.py](module/value_normalizer.py) `_normalize_text` | The literal form again: `re.sub(r"[\s​‌‍﻿]", "", text)` |
>
> All three have the same semantics. Two of them contain **characters you cannot see in your
> editor**. If you "clean up" that regex — or if a tool strips the invisibles on save — buyer
> matching and fact-check text matching will start silently disagreeing with the reconcile engine,
> and no test will necessarily catch it. If you change one, change all three, and verify the
> codepoints (`U+200B`, `U+200C`, `U+200D`, `U+FEFF`) rather than trusting what the file looks like.

### 6.3 — Z45 dates, amounts, and the positional rename

[schema/z45_input.py](schema/z45_input.py) has three defenses against the SAP export.

**Dual date parse.** The `_normalize` dataframe-parser (which runs **before** coercion, unlike
`@pa.parser`, which runs after) does:

```python
sap = pd.to_datetime(df[col], format="%d.%m.%Y", errors="coerce")   # SAP text form, tried FIRST
iso = pd.to_datetime(df[col], errors="coerce")                      # general parse, fallback
df[col] = sap.fillna(iso)
```

The SAP `dd.mm.yyyy` **format is tried first, and only the NaTs fall back to a general parse.**
The order is not arbitrary and must not be flipped: a general parse of `05.03.2026` reads it
**month-first** as *May 3rd*, silently producing a date that is wrong by two months — which will
then quietly fail every `month_match` in the engine. The fallback exists because a real Excel
*date cell* (rather than SAP text) arrives — under the loader's `dtype=str` — as an ISO
`'YYYY-MM-DD HH:MM:SS'` string, which the `dd.mm.yyyy` format cannot parse. Applies to
`payment_date`, `send_date`, `process_date`, `tax_clearing_date`.

**SAP trailing-minus amounts.** SAP writes a credit as `"1,234.56-"`. So for
`vat_amount` / `tax_base_amount` / `net_paid`:

```python
s = df[col].astype("string").str.replace(",", "", regex=False).str.strip()
neg = s.str.endswith("-").fillna(False)
s = s.str.rstrip("-")
df[col] = s.mask(neg, "-" + s)     # move the minus to the FRONT
```

Then coercion casts to `Decimal(18, 2)` — exact, satang-precise, no float error, which is what lets
the engine compare VAT amounts with `=`.

**Positional rename — the field order IS the contract.** `validate_z45(df)` does **not** match
columns by header text:

```python
field_names = list(Z45Input.to_schema().columns.keys())
if len(df.columns) != len(field_names):
    raise ValueError(f"column count mismatch: expected {len(field_names)}, got {len(df.columns)}")
df.columns = field_names           # renamed BY POSITION
return Z45Input.validate(df)
```

The export's headers are unreliable in three separate ways — non-breaking spaces
(`Ref.\xa0Doc\xa0\xa0(Inv.)`), truncation (`Tax Base A`, `Payment Do`), and outright duplicates
(the two `Tax Cleari`) — so header text is unusable as a key.

> **Therefore: the field definition order in `Z45Input` IS the data contract.** Reordering the
> fields, or inserting one in the middle, does not raise — it **silently corrupts every row**, by
> loading (say) the vendor code into the doc-type column. The only tripwire is the column-count
> check, which catches an added/removed column but not a reordered one. The same hazard applies to
> `Z45Output` / `Z45_OUTPUT_HEADERS` ([§5.4](#54--z45output--z45_output_headers--the-duplicate-header-dance))
> and to `ReportOutput` ([§5.1](#51--reportoutput--the-37-column-output-report)).

---

## 7 — Fact-check subsystem

A quality-measurement pipeline, not a delivery pipeline. It runs the *same* OCR extraction over a
**fixed reference set** of documents for which a human has labelled the correct answer, and reports
how close the model got — as structured log lines, not an Excel report.

Everything lives on the **control** site under
`${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_FACT_CHECK_PATH}/` (`source_file/`,
`ground_truth_file/`, `master_file/`, `ocr_log/`), with its own `fact_check/` GCS namespace so it
can never collide with production. `resources/fact_check_ref/*` is the local dev copy — **test
fixtures only**.

### 7.1 — `FIELD_MAPPING` — the 23 scored fields

[helper/constant.py](helper/constant.py). Each entry is a frozen `FactCheckField(label, gt_field,
extraction_columns, compare)`.

| # | Label | GT column | Extraction column(s) | Compare |
|---|---|---|---|---|
| 1 | Document Name | `document_name` | `DOC_NAME` | text |
| 2 | Buyer Name | `buyer_name` | `BUYER_NAME_TH`, `BUYER_NAME_ENG` | text |
| 3 | Buyer Address | `buyer_address` | `BUYER_ADDRESS_TH`, `BUYER_ADDRESS_ENG` | text |
| 4 | Buyer Tax ID | `buyer_tax_id` | `BUYER_TAX_ID` | taxid |
| 5 | Buyer Branch Code | `buyer_branch_code` | `BUYER_BRANCH_CODE` | text |
| 6 | Buyer Branch Name | `buyer_branch_name` | `BUYER_BRANCH_NAME` | text |
| 7 | Vendor Name | `vendor_name` | `VENDOR_NAME_TH`, `VENDOR_NAME_ENG` | text |
| 8 | Vendor Address | `vendor_address` | `VENDOR_ADDRESS_TH`, `VENDOR_ADDRESS_ENG` | text |
| 9 | Vendor Tax ID | `vendor_tax_id` | `VENDOR_TAX_ID` | taxid |
| 10 | Vendor Branch Code | `vendor_branch_code` | `VENDOR_BRANCH_CODE` | text |
| 11 | Vendor Branch Name | `vendor_branch_name` | `VENDOR_BRANCH_NAME` | text |
| 12 | Tax Invoice Number | `tax_invoice_number` | `TAX_INVOICE_NUMBER` | text |
| 13 | Tax Invoice Date | `tax_invoice_date` | `TAX_INVOICE_DATE` | date |
| 14 | Total Amount | `total_amount` | `TOTAL_AMOUNT` | amount |
| 15 | VAT | `vat` | `VAT_AMOUNT` | amount |
| 16 | Net Amount | `net_amount` | `NET_AMOUNT` | amount |
| 17 | Copy | `copy` | `COPY` | bool |
| 18 | Receiver's Signature | `receiver_signature` | `RECEIVER_SIGNATURE` | bool |
| 19 | Withholding Tax | `withholding_tax` | `WITHHOLDING_TAX` | amount |
| 20 | Invoice Number | `invoice_number` | `INVOICE_NUMBER` | text |
| 21 | Invoice Amount | `invoice_amount` | `INVOICE_AMOUNT` | amount |
| 22 | Vat Invoice | `vat_invoice` | `VAT_INVOICE` | amount |
| 23 | Stamp | `stamp` | `STAMP` | bool |

**Deliberately excluded from scoring** — the GT-only columns with no extraction counterpart:
**Invoice Document, Payment Document, Payment Date, Vendor Code, Send Date**. These come from Z45,
not from the model, so scoring the model on them would be meaningless.
`GroundTruthSchema.Config.strict = False` lets them be present-or-absent in the workbook without
failing validation, and `file_name` (`GROUND_TRUTH_FILE_KEY`) is the join key, not a scored field.

Fields 2, 3, 7, 8 carry **two** extraction columns — a match against **either** the Thai or the
English column counts as correct. That is right: an invoice printed only in Thai leaves the English
column empty, and the model is not wrong for that.

> Note the GT header for field 18 uses a **curly apostrophe**: `Receiver’s Signature` (U+2019), not
> an ASCII `'`. That is documented in the schema and is easy to break when editing the workbook.

### 7.2 — The composite document-line join key

Both frames are one row per **document line**, so pairing them **per file would be wrong** — a file
with three invoice lines would collapse to one arbitrary pairing and two-thirds of the labels would
be scored against the wrong row.

`FactCheckEvaluator._composite_key` builds:

```
file_key | norm(tax_invoice_number) | norm(copy) | norm(invoice_number)
```

— the manual-evaluation `unique_identifier`. `_file_key` strips the extension and casefolds
(`os.path.splitext(str(value))[0].strip().casefold()`), and the other three components are run
through the *same* `ValueNormalizer` (text / bool / text) that will score them, so the key and the
comparison agree.

`_join` then:

1. builds the key on the extraction frame and drops duplicate keys (`keep="first"`);
2. restricts GT to files that appear in the extraction frame at all (file-level overlap);
3. **LEFT**-joins GT → extraction on the key, with `indicator=_matched`.

The LEFT join is the point: a ground-truth line the extraction **missed or mis-keyed** survives as
an unpaired row (`_matched == False`) instead of silently vanishing from the denominator. If it
vanished, missing a document entirely would *improve* your score.

### 7.3 — The correct-vs-incorrect confusion matrix (read this before quoting a number)

```python
def _score_field(self, field, rows):
    tp = 0
    for row in rows:
        if not row.get(_MATCHED, False):
            continue                                   # unpaired GT row: contributes nothing to TP
        gt_norm  = self._normalizer.normalize(row.get(field.gt_field), field.compare)
        ex_norms = {self._normalizer.normalize(row.get(col), field.compare) for col in field.extraction_columns}
        tp += int(self._is_match(gt_norm, ex_norms))
    return tp, len(rows) - tp                          # FP = everything that isn't a TP
```

and

```python
results.append({"label": field.label, **self.confusion_metrics(tp, fp, 0, 0)})   # FN = TN = 0, hard-coded
```

This is a **correct-vs-incorrect** matrix, not a binary classifier:

- **`TP`** = the normalized values agree.
- **`FP`** = they differ.
- **`FN = TN = 0`**, always. Hard-coded.
- An **unpaired GT row counts as `FP` on every field** (it skips the `tp += `, and `FP` is
  `len(rows) - tp`).

#### Why the match test is not a plain `gt_norm in ex_norms`

Four fields carry **two** candidate extraction columns and match against *either* — `Buyer Name`
holds `("BUYER_NAME_TH", "BUYER_NAME_ENG")`, and likewise Buyer Address / Vendor Name / Vendor
Address — because a Thai invoice prints the value in whichever language it uses. `ex_norms` is built
over both columns, and a **null** column normalizes to `NA_SENTINEL`. A naive `gt_norm in ex_norms`
therefore scores a **hallucination as a true positive**: with ground truth blank
(`gt_norm == "N/A"`), a model that invents a Thai name and leaves the English column null produces
`ex_norms == {"acme", "N/A"}`, and `"N/A" in {"acme", "N/A"}` is `True`.

`_is_match` splits the two cases:

```python
if gt_norm == NA_SENTINEL:
    return all(v == NA_SENTINEL for v in ex_norms)   # blank GT: a match only if NOTHING was extracted
return gt_norm in ex_norms                           # populated GT: any candidate column may carry it
```

A blank GT paired with a blank extraction is still a **`TP`** (correctly writing nothing is correct);
a blank GT paired with *any* extracted value is an **`FP`**.

**State the consequence plainly, because someone will otherwise quote these numbers in a deck:**

With `FN = 0`, `recall = tp / (tp + 0) = 100%` whenever `tp > 0` — **recall is always 100% unless
literally nothing matched.** And with `TN = 0`, `accuracy = tp / (tp + fp)` and
`precision = tp / (tp + fp)` — **precision and accuracy are the same number, always.**

F1 is computed from the raw counts, `2·TP / (2·TP + FP + FN)` — the form the manual evaluation
workbook (`doc/evaluation_20260709.xlsx`) states. It is algebraically the same as the harmonic mean
of precision and recall, but it does not silently depend on those two being passed in *unrounded*.
It is **not** an independent signal: substituting `FN = TN = 0` gives

```
F1 = 2·TP / (2·TP + FP) = 2A / (A + 1)        where A = accuracy
```

a strictly-increasing relabelling of accuracy that **always reads higher than it** — 80% accuracy
prints an F1 of 88.89%, 90% prints 94.74%. So of the four metrics emitted, exactly **one** carries
information (accuracy == precision), recall is a constant, and F1 is a fixed function of accuracy.
**Never compare F1 against the accuracy thresholds**; it will look one tier better than the model is.

This is not a bug. It is the **telesale/QA convention**, adopted deliberately so tax-invoice quality
numbers sit in the same dashboards as the other pipelines'. `FactCheckEvaluator.confusion_metrics`
produces the same *values* as telesale/QA's `_compute_eval_metrics`, divide-by-zero guards included
(each metric is `0.0` when its denominator is zero) — only the F1 *expression* differs. Just do not
present it as a standard binary classifier.

The `overall` row is a **micro-average**: `total_tp` / `total_fp` summed across all 23 fields.

### 7.4 — `ValueNormalizer` — the compare rules

[module/value_normalizer.py](module/value_normalizer.py). Every value — a GT text cell or a typed
extraction value (`Decimal` / `date` / `bool` / `str`) — is reduced to one canonical string so the
exact-match test is apples-to-apples.

**The null/blank rule fires first, for every compare type:**

```python
if self._is_null(value):
    return NA_SENTINEL          # "N/A"
```

`_is_null` covers `None`, `NaN`, `NaT`, and pandas `<NA>`. **This makes a labelled-blank ground
truth agree with an empty extraction — a `TP`.** That is intentional and correct: if the human
labeller wrote nothing because the field is not on the document, then the model extracting nothing
is the *right answer*. `COMPARE_TEXT` also returns the sentinel for a value that normalizes to
`''`, so a whitespace-only cell lands in the same bucket.

| Compare | Rule |
|---|---|
| `COMPARE_TEXT` | `unicodedata.normalize("NFC", str(v))` → strip all whitespace + ZWSP/ZWNJ/ZWJ/BOM → `.lower()`; empty → `N/A`. **Mirrors reconcile's SQL `norm_text_sql`** (see the sync warning in [§6.2](#62--thai-text-lower-is-a-red-herring)) so a fact-check text match agrees with what the engine matches. |
| `COMPARE_TAXID` | Digits only (`re.sub(r"\D", "", ...)`) → `.zfill(13)`; no digits → `N/A`. Same rule as `norm_taxid_sql`. |
| `COMPARE_AMOUNT` | Numeric types pass straight to `Decimal`; strings are stripped to `[\d.\-]` (kills thousands separators and currency marks) → `Decimal(...).quantize(Decimal("0.01"))` → 2dp string. Unparseable → falls back to `_normalize_text` (so garbage still compares as text rather than exploding). |
| `COMPARE_DATE` | A `date`/`datetime` passes through as `.isoformat()`. A string is tried against `%d/%m/%Y` (the GT format), `%Y-%m-%d`, `%d.%m.%Y`, `%d-%m-%Y`, in that order; unparseable → `N/A`. |
| `COMPARE_BOOL` | A real `bool` → `"true"`/`"false"`. A string is casefolded and matched against `{true,t,yes,y,1}` / `{false,f,no,n,0}`; anything else → `N/A`. |

### 7.5 — Emission

`emit_fact_check_logs(...)` sends each metric row (23 fields + `overall`) through the shared
`logging_ai_operation(log_type="fact_check", message="AI-Operation Fact Check log")`
([src/utils/common.py](../../src/utils/common.py)) — the same channel telesale/QA/reconcile use — so
each lands in Cloud Logging as one JSON line with the payload under the top-level `data` key:

| Field | Source |
|---|---|
| `created_datetime` | `ctx.execution_dt` — the **run start**. |
| `processed_datetime` | The Gemini batch's `END_TIME`, taken as the **mode** across the run's predictions, converted to the configured timezone. `END_TIME` is UTC wall-clock but the finalizer's DuckDB + pandera round-trip **drops the tzinfo**, so `_processed_datetime` re-attaches `UTC` before converting — remove that and every timestamp shifts by the TZ offset. |
| `gcp_project_id` | `gcp.project_id`, env-resolved. |
| `label` | The field label, or `overall`. |
| `accuracy` / `precision` / `recall` / `f1_score` | From `confusion_metrics` (percentages, 2dp). |

---

## 8 — Reference & testing

### 8.1 — Config wiring

| Config | Task keys, in execution order | Trigger |
|---|---|---|
| [ocr_pipeline_pre_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml) | `OCRSubmitTask` → `TaxInvoiceRejectTask` | Cloud Scheduler |
| [ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml) | **`ReconcilePrecheckTask`** → `OCRRetrieveTask` → `ReconcileTask` → `OCRFinalizeTask` | Eventarc |
| [ocr_pipeline_fact_check_pre_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_fact_check_pre_tasks.yml) | `OCRSubmitTask` (pointed at the control-site reference set; **`CONTROL_SITE_*` credentials**, not the tax-invoice source site) | Cloud Scheduler |
| [ocr_pipeline_fact_check_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_fact_check_post_tasks.yml) | `OCRRetrieveTask` → `TaxInvoiceFactCheckTask` → `OCRFinalizeTask` | Eventarc |

`OCRFinalizeTask` **must stay the last key** in both post configs. See
[config/tax_invoice_extraction/README.md](../../config/tax_invoice_extraction/README.md) for the
full env-var list.

### 8.2 — Source file patterns and the "latest file" rule

Configured as **regexes** on the task block and matched by
`SharePointModule.list_files_pattern(folder_path, pattern)`:

| Config key | Pattern | Loader |
|---|---|---|
| `master_buyer_file` | `Master Buyer Company_\d{8}.xlsx` | `ReportSourceLoader.load_master_buyer()` |
| `master_vendor_file` | `Master Vendor Company_\d{8}.xlsx` | `ReportSourceLoader.load_master_vendor()` |
| `z45_report_file` | `ZAPRPT45_\d{8}.xlsx` | `ReportSourceLoader.load_z45()` |

**The "latest" rule is `sorted(matches, reverse=True)[0]`** (`ReportSourceLoader._latest_file`, via
`safe_list_get`). That is a **lexicographic** sort on the full path, not a date parse — it works
only because the `_YYYYMMDD` suffix sorts identically to its chronological order. A file named
outside the pattern is invisible; zero matches raise `FileNotFoundError` (and `ReconcilePrecheckTask`
is what turns that into a clean, notified halt rather than a mid-run crash).

`load_z45()` appends a `path_file` column after validation (`Z45Input.Config.strict = False` permits
it), which is how `ReconcileTask._z45_source_path` knows what to archive. `ReconciliationBuilder`
ignores it.

The fact-check loader is different: `ground_truth_file` and `master_buyer_path` are **exact item
paths**, not patterns — the reference set is fixed, so there is nothing to pick a latest of.

### 8.3 — Email templates

All under `config/tax_invoice_extraction/email_template/`, rendered by `EmailNotifier`
(`{NAME}` placeholders via `str.format`, then `\n` → `<br>` because Graph always sends an HTML body).

| Template | Sent by | Case key | Placeholders |
|---|---|---|---|
| `dependency_missing.txt` | `ReconcilePrecheckTask` | `business_exception` | `{MISSING_FILES}` |
| `extraction.txt` | `ReconcileTask` (after the extraction CSV lands) | `extraction_success` | `{PROCESSING_NO}`, `{SUCCESS_NO}`, `{FAILED_NO}` |
| `report.txt` | `ReconcileTask` (after the workbooks land) | `mapping_success` | none |
| `processing_failed.txt` | any task's `on_error`; also the `body_path` for the OCR tasks' on-error emails | `system_exception` | none |

Recipients are **per case**, resolved from the task's own `framework.notifications.<case>` block —
the notifier itself holds no addresses. Every send is best-effort: a mail failure is logged and
swallowed so it can never undo delivered output (or suppress the real exception).

### 8.4 — Tests

`tests/test_tasks/tax_invoice_reconcile/`:

| File | Covers |
|---|---|
| `test_reconciliation_builder.py` | The engine — all six scenarios, remarks, the tri-state Z45 status |
| `test_extraction_report_builder.py`, `test_extraction_report_consistency.py` | Stage 1 aggregation, confidence, `DOC_STATUS`/`REMARK` |
| `test_z45_input.py` | Positional rename, dual date parse, trailing-minus amounts |
| `test_output_exporter.py` | E-TAX merge vs Paper one-to-one routing, link-based VAT slicing |
| `test_report_source_loader.py` | Latest-file pattern matching, `dtype=str` tax-id read |
| `test_precheck_task.py`, `test_reject_task.py`, `test_fact_check_task.py` | Task lifecycles |
| `test_reconcile_contract.py` | The return-`pre_result` contract |
| `test_pipeline_config.py`, `test_fact_check_pipeline_config.py` | **The YAML key order** — including precheck-first |
| `test_fact_check_evaluator.py`, `test_value_normalizer.py`, `test_ground_truth_loader.py` | Fact-check |
| `test_source_archiver.py`, `test_suspicious_reject.py`, `test_iqs_rejecter.py` | Archive / reject paths |
| `test_export_logging.py`, `test_email_notifier.py`, `test_init_conn.py`, `test_task_context.py` | Plumbing |

```bash
uv run pytest tests/test_tasks/tax_invoice_reconcile/
```

> Windows flake, unrelated to this package: a full-suite run can fail with `WinError 32` on
> `logs/app.log` when the log hits its 10 MB rotation cap. Clear `logs/app.log*` and re-run.

### 8.5 — Pointer table

See the [README](README.md) for the full module / helper / schema inventory. The sections above map
to it as:

| Topic | Section | Primary file |
|---|---|---|
| Engine threading, `OCRResult` | [§1](#1--purpose--relationship-to-the-ocr-pipeline) | [../ocr_tax_invoice_pipeline/schema/contracts.py](../ocr_tax_invoice_pipeline/schema/contracts.py) |
| Tasks | [§2](#2--the-four-tasks-step-by-step) | `*_task.py` |
| Extraction report | [§3](#3--stage-1--the-extraction-report) | [module/extraction_report_builder.py](module/extraction_report_builder.py) |
| **Matching engine** | [§4](#4--stage-2--the-reconciliation-engine) | **[module/reconciliation.sql](module/reconciliation.sql)** |
| Presentation SELECTs | [§5](#5--stage-3--output-contracts) | [module/reconciliation_builder.py](module/reconciliation_builder.py) |
| Output schemas | [§5](#5--stage-3--output-contracts) | [schema/report_output.py](schema/report_output.py), [schema/z45_output.py](schema/z45_output.py) |
| Normalization | [§6](#6--matching-gotchas) | [helper/sql_normalize.py](helper/sql_normalize.py), [schema/z45_input.py](schema/z45_input.py) |
| Fact check | [§7](#7--fact-check-subsystem) | [module/fact_check_evaluator.py](module/fact_check_evaluator.py), [helper/constant.py](helper/constant.py) |

### 8.6 — Known warts (verified, unfixed)

Recorded so the next person does not have to rediscover them. **None of these are fixed by this
document.**

1. **Docs contradict the shipped YAML on task order.** The [README](README.md) flow diagram, the
   repo-root `CLAUDE.md`, the header comment inside
   [ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml),
   and [config/tax_invoice_extraction/README.md](../../config/tax_invoice_extraction/README.md) all
   say `OCRRetrieveTask → ReconcilePrecheckTask → …`. The YAML says
   `ReconcilePrecheckTask → OCRRetrieveTask → …`, and the YAML is **correct** — see
   [§2](#the-real-post-pipeline-order). The prose should be corrected.
2. **`OutputExporter`'s E-TAX comment is backwards.** The inline comment says
   *"split on `'_'` → `[1]`=user, `[2]`=company"*, but the folder is `{CompanyCode}_{User}` (per
   `output_layout.reject_dest`'s docstring and the `0001_pornpa8` test fixture), so `[1]` is the
   **company** and `[2]` is the **user**. The SQL's `COALESCE` fallbacks are correspondingly
   swapped: `[2]` (the user) falls back to `'0000'` and `[1]` (the company code) falls back to
   `'UNKNOW'`. The *output filename* is nonetheless what the tests assert
   (`pornpa8_20260605_0001_Output_ETAX.xlsx`), so behavior is fine — only the comment and the
   fallback defaults are misleading.
3. **`Z45Output`'s docstring numbers are stale.** It says pandera "would expose 33 columns for 34
   fields". The model actually has **38** fields (37 Z45 source + `mapping_tax_invoice_status`) and,
   with the one duplicated `Tax Cleari` alias, pandera would expose **37**. The *reasoning* is right;
   the numbers are not.
4. ~~**Dead enum members.**~~ Resolved: `ExtractionStatus.FAILED`, `MappingZ45Message`'s four Fn-4
   "missing in Z45 report" messages, and `ValidationMessage.TAX_INVOICE_DATE_RULE_MESSAGE` have been
   deleted.
5. **A typo in the extraction SQL.** `agg_orc` has the trailing comment
   `-- For fileter out when reconciliation` on `OCR_ISSUE_FLAG` ("fileter").
6. **Empty docstrings.** `ExtractionStatus`, `MappingZ45Status`, `MasterBuyer`, and `MasterVendor`
   all carry `""""""` / `""" """` placeholder docstrings.
7. **Partial exports look like successes.** `OutputExporter._upload` and the extraction-CSV upload
   swallow SharePoint failures at WARNING. Combined with the return-`pre_result` chain, finalize
   still stamps `SUCCESS` for files whose workbook never uploaded. The only evidence is a
   `SharePoint upload failed` line in the logs.
8. **`reject_path` is a silent optional.** Configured, but not in `ReconcileTask.REQUIRED_STRING_KEYS`;
   omit it and Suspicious-page rejection silently stops (see [§2.2](#22--reconciletask)).
