# Tax-Invoice Reconcile Tasks

Business-logic tasks that sit **on top of** the domain-agnostic OCR pipeline v2
(`tasks/ocr_tax_invoice_pipeline/`). The OCR pipeline extracts and validates receipts;
this package reconciles that output against the treasury master data (Master Buyer,
Master Vendor) and the SAP **ZAPRPT45 (Z45)** input-VAT report, then delivers the
per-document Output workbooks, archives, and audit logs.

> **Deep dive:** see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the reconciliation-engine walkthrough, the output/schema contracts, and the matching gotchas.

The tasks consume the typed `OCRResult` produced by `OCRRetrieveTask` and **return it
unchanged**, so the trailing `OCRFinalizeTask` stamps a terminal status in the
pre-processing log only *after* reconciliation succeeds. If reconcile raises, finalize
never runs, files stay `PENDING`/`PARTIAL`, and the next run re-collects the same
predictions from GCS at zero extra Gemini cost.

## Registered tasks

| Registry name | File | Runs in |
|---|---|---|
| `ReconcilePrecheckTask` | [precheck_task.py](precheck_task.py) | post pipeline (`ocr_pipeline_post_tasks.yml`) |
| `ReconcileTask` | [reconcile_task.py](reconcile_task.py) | post pipeline (`ocr_pipeline_post_tasks.yml`) |
| `TaxInvoiceRejectTask` | [reject_task.py](reject_task.py) | pre pipeline (`ocr_pipeline_pre_tasks.yml`) |
| `TaxInvoiceFactCheckTask` | [fact_check_task.py](fact_check_task.py) | fact-check post pipeline (`ocr_pipeline_fact_check_post_tasks.yml`) |

Classes are imported in [`__init__.py`](__init__.py) so the `@task_registry.register(...)`
decorators run as an import side effect (via `tasks/__init__.py`).

### Post-pipeline task order

```
OCRRetrieveTask        → collects predictions into an OCRResult (final_df, file_statuses,
                         pre_processing_log + page_manifest_log snapshots);
                         stamps NO terminal status
ReconcilePrecheckTask  → halts (and email-notifies) if Master-Buyer / Master-Vendor / Z45
                         sources are missing; else passes the OCRResult straight through
ReconcileTask          → reconciles OCRResult.final_df vs Master Buyer + Master Vendor + Z45,
                         exports Output workbooks + archives + audit logs, RETURNS the
                         OCRResult unchanged
OCRFinalizeTask        → MUST stay last; stamps SUCCESS / SUCCESS_WITH_FAILURE / FAILED into
                         the pre-processing log only after reconcile succeeds
```

### Pre-pipeline placement

`TaxInvoiceRejectTask` runs after `OCRSubmitTask` in `ocr_pipeline_pre_tasks.yml`. It reads
the pre-processing and page-manifest logs `OCRSubmitTask` just stamped on GCS, filters to
this run's `job_id`, and moves IQS rejects out of the SharePoint source folder. It is
best-effort (per-file errors are logged and swallowed) and returns `pre_result` unchanged
(always `None` in the pre pipeline).

## `ReconcilePrecheckTask`

Verifies the required source files exist on the source SharePoint site before reconcile runs:

- **Master Buyer** — `master_buyer_path` / `master_buyer_file`
- **Z45 report** — `z45_report_path` / `z45_report_file`
- **Master Vendor** — `master_vendor_path` / `master_vendor_file`

If any are missing it sends a single consolidated business-exception email listing the
missing file(s) (`dependency_missing.txt`) and raises `DependencyMissingError`, which halts
the pipeline (finalize never runs). A non-dependency error triggers a system-exception email
(`processing_failed.txt`) from `on_error`. Recipient sets (`business_exception`,
`system_exception`) come from `framework.notifications`.

## `ReconcileTask`

Orchestration only — it wires the modules below:

1. `ReportSourceLoader.load_master_buyer()` — latest Master Buyer file.
2. `ExtractionReportBuilder.build(...)` — collapse OCR line items to one row per resolved
   document, LEFT JOIN the Master Buyer on a zero-padded tax id, and fold the buyer verdict
   into `DOC_STATUS` / `REMARK`.
3. `to_extraction_output(...)` — write the dated
   `extraction_result_%{DATA_DATE_YYYY-MM-DD}.csv` to the control site (UTF-8-SIG, uploaded
   inline); send the `extraction_success` email (`extraction.txt`) with per-file counts.
4. `ReportSourceLoader.load_master_vendor()` + `load_z45()`.
5. `ReconciliationBuilder.build(...)` — reconcile against Z45 (six scenarios; engine in
   [module/reconciliation.sql](module/reconciliation.sql)); returns the 37-column Output
   Report, the enriched Z45, and the Z45↔document match link (`_z_id`/`file_name`).
6. `OutputExporter.export(...)` — per-document `Extract&Mapping` + `VAT Report` workbooks to
   the destination site (E-TAX rows sharing a source folder merge; Paper [Scan] rows stay
   one-to-one). Each VAT workbook carries the Z45 lines the engine linked to that workbook's
   documents (statuses `Completed`/`Incompleted`) — attribution is by the match link, not by
   invoice-number equality, so header-level matches (scen 2/4/5) keep their rows.
7. `SourceArchiver.archive_invoices/archive_z45(...)` — copy each processed source invoice
   and the Z45 report into the archive folders, then delete the archived originals from the
   source site (idempotent re-runs).
8. `SourceRejecter.reject_suspicious(...)` — copy each `SUSPICIOUS` page's immutable GCS
   chunk into the reject folder (original still archived).
9. Send the `mapping_success` email (`report.txt`).

`post_execute` then exports the transaction / performance / AI-operation logs via
`ExportLogging` (best-effort: a logging failure is swallowed so it never undoes the delivered
output). The pre-processing-log snapshot for those logs is read off `OCRResult.pre_processing_log`
(no GCS re-read). `execute_task` returns `self.pre_result` (the `OCRResult`) unchanged.

## `TaxInvoiceRejectTask`

Reads this run's stamped logs from GCS and delegates to `IqsRejecter`:

- fully `REJECTED` files (all pages failed IQS, **or** an unsupported file extension —
  logged `REJECTED` with message `"Unsupported file type: <ext>"` by `OCRSubmitTask`) are
  copy-then-deleted into the reject folder;
- `PARTIAL` files keep their source intact — only the IQS-rejected pages are split out
  (`extract_single_page`) and uploaded to the reject folder.

The reject tree is date-first with the E-TAX company/user subfolder preserved (see
`helper/output_layout.reject_dest`).

## `TaxInvoiceFactCheckTask`

Measures the quality of the tax-invoice OCR extraction against a human-labelled ground truth
and reports the result as structured **`AI-Operation Fact Check log`** JSON lines (no Excel
report). It reuses the generic OCR pipeline (`OCRSubmitTask` / `OCRRetrieveTask` /
`OCRFinalizeTask`) unchanged and, like `ReconcileTask`, **returns the upstream `OCRResult`
unchanged** so `OCRFinalizeTask` still stamps terminal status last.

It runs in its own **pre** (Cloud Scheduler) / **post** (Eventarc) pipeline on a dedicated
`fact_check/` GCS + log namespace:

| Config | Tasks | Trigger |
|---|---|---|
| [`config/tax_invoice_extraction/ocr_pipeline_fact_check_pre_tasks.yml`](../../config/tax_invoice_extraction/ocr_pipeline_fact_check_pre_tasks.yml) | `OCRSubmitTask` | Cloud Scheduler |
| [`config/tax_invoice_extraction/ocr_pipeline_fact_check_post_tasks.yml`](../../config/tax_invoice_extraction/ocr_pipeline_fact_check_post_tasks.yml) | `OCRRetrieveTask` → `TaxInvoiceFactCheckTask` → `OCRFinalizeTask` | Eventarc |

What it does:

1. Runs `OCRResult.final_df` + the Master Buyer through `ExtractionReportBuilder` (same
   buyer-enrichment first step as `ReconcileTask`).
2. Loads `ground_truth.xlsx` (`FactCheckSourceLoader` → `GroundTruthSchema`).
3. Compares the extraction against ground truth **field by field** (`FactCheckEvaluator`,
   driven by `FIELD_MAPPING`). Both frames are one row per **document line**, so rows are
   paired on the composite key *file + tax invoice number + copy + invoice number*
   (normalized) — not per file. Scoring is a **correct-vs-incorrect** confusion matrix
   (telesale/QA convention: `TP` = normalized values agree, `FP` = differ, `FN = TN = 0`);
   a ground-truth line with no extraction partner counts as `FP` on **every** field. Each field type is normalized first (`ValueNormalizer`): text (NFC + strip
   whitespace/zero-width + lower, matching reconcile's SQL `norm_text`), tax id (digits, left-pad
   13), amount (2dp Decimal), date (ISO), boolean (Yes/No → true/false).
   Name/address fields match against **either** the Thai or English extraction column — but a
   *blank* ground-truth cell matches only when **every** candidate column is blank, so a
   hallucinated value alongside a null sibling column scores `FP`, not `TP`.
   **Read `accuracy` only.** `F1` is emitted as `2·TP / (2·TP + FP + FN)` (the manual-evaluation
   form), but with `FN = TN = 0` it reduces to `2A/(A+1)` — a relabelling of accuracy that always
   reads higher than it — while `precision` equals accuracy and `recall` is a constant 100%. See
   [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) §7.3.
4. Emits one `AI-Operation Fact Check log` line **per field** plus one `overall` row
   (`emit_fact_check_logs` → `logging_ai_operation(log_type="fact_check")`); each line carries
   `created_datetime` (run start), `processed_datetime` (Gemini batch processed time — the most
   common `END_TIME` across the run's predictions, config timezone, matching telesale/QA),
   `gcp_project_id`, `label`, `accuracy`, `precision`, `recall`, `f1_score` under the top-level
   `data` key.
5. Emails the result (best-effort) `BOT_EMAIL → USER_EMAIL` (cc `DEVELOPER_EMAIL`, `OPER_EMAIL`)
   via the task's `framework.notifications.fact_check_result` block: the `fact_check_result.txt`
   template filled with the report datetime, the batch model name (from the run log), ground-truth /
   prediction counts, and an accuracy-only HTML table (`EmailNotifier.build_fact_check_table`) whose
   Baseline column comes from the per-field UAT baseline YAML named by the block's `baseline_path`
   key (`config/tax_invoice_extraction/fact_check_uat_baseline.yml`, keyed by the snake_case
   `gt_field` names of `FIELD_MAPPING` → fraction; the builder maps each row's display label to its
   `gt_field`, and a label without a baseline entry shows 0.00%). The `overall` row stays log-only
   (it has no UAT baseline). Skipped when the
   evaluator matched nothing. Subject + template filename are code constants (package convention);
   YAML supplies sender/receiver/cc + `baseline_path` (required by `validate()`).
6. Writes the transaction + performance logs exactly as `ReconcileTask` does (`ExportLogging`).

On a system error it emails `BOT_EMAIL → DEVELOPER_EMAIL` (cc `OPER_EMAIL`) via the task's
`framework.notifications.system_exception` block. The reference files live under the **control**
site at `${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_FACT_CHECK_PATH}/` (`source_file/`,
`ground_truth_file/`, `master_file/`); `resources/fact_check_ref/*` is the local dev copy used
only as test fixtures.

## Modules (`module/`)

| Class | File | Responsibility |
|---|---|---|
| `ReportSourceLoader` | [report_source_loader.py](module/report_source_loader.py) | Load + validate the latest Master Buyer, Master Vendor, and Z45 files from SharePoint |
| `ExtractionReportBuilder` | [extraction_report_builder.py](module/extraction_report_builder.py) | Aggregate OCR line items to one row per document, enrich with the Master Buyer, fold the buyer verdict into `DOC_STATUS`/`REMARK` (DuckDB) |
| `ReconciliationBuilder` | [reconciliation_builder.py](module/reconciliation_builder.py) | Reconcile the extraction report against Z45 (six-scenario engine in `reconciliation.sql`); emit the Output Report + enriched Z45 |
| `OutputExporter` | [output_exporter.py](module/output_exporter.py) | Route reconciled rows to per-document `Extract&Mapping` + `VAT Report` workbooks and upload them |
| `SourceArchiver` | [source_archiver.py](module/source_archiver.py) | Archive processed source invoices + the Z45 report, then delete the archived originals |
| `SourceRejecter` | [source_rejecter.py](module/source_rejecter.py) | Copy each Suspicious page's immutable GCS chunk into the reject folder |
| `IqsRejecter` | [iqs_rejecter.py](module/iqs_rejecter.py) | Move IQS-rejected files / split bad pages from the source site (pre pipeline) |
| `ExportLogging` | [export_logging.py](module/export_logging.py) | Build + persist per-page transaction / performance / AI-operation logs |
| `EmailNotifier` | [email_notifier.py](module/email_notifier.py) | Render a `.txt` template to HTML and send it via Microsoft Graph; also renders the fact-check accuracy table (`build_fact_check_table`) |
| `FactCheckSourceLoader` | [ground_truth_loader.py](module/ground_truth_loader.py) | Load + validate the fact-check `ground_truth.xlsx` from SharePoint (`GroundTruthSchema`) |
| `FactCheckEvaluator` | [fact_check_evaluator.py](module/fact_check_evaluator.py) | Pairs GT and extraction rows on the document-line key (file + tax invoice number + copy + invoice number), then scores field-by-field correct-vs-incorrect; per-field + `overall` metric rows (static `confusion_metrics` for the accuracy/precision/recall/f1 math) |
| `emit_fact_check_logs` | [fact_check_log_emitter.py](helper/fact_check_log_emitter.py) | Emit each metric row as an `AI-Operation Fact Check log` line via `logging_ai_operation` |
| `ValueNormalizer` | [value_normalizer.py](module/value_normalizer.py) | Reduce a GT/extraction value to a canonical comparable string per `COMPARE_*` type (fact-check) |

The reconciliation matching **engine** (macros, `scenario_mapping`, and the `scen_one`..`scen_five`
candidate views) lives in [module/reconciliation.sql](module/reconciliation.sql); the builder is
a thin loader that runs the presentation SELECTs over those views.

## Helpers (`helper/`)

| File | Contents |
|---|---|
| [task_context.py](helper/task_context.py) | `ReconcileTaskContext` — the immutable per-run context shared by every task in this package (config blocks + engine packages + `config/common.yml` `control:`/`msgraph:` blocks), with the shared `recipients`/`subject`/`resolve_path`/`logging_cfg` helpers |
| [init_conn.py](helper/init_conn.py) | Connection factories used in each task's `pre_execute`: `init_sharepoint`, `init_gcs`, and `init_email_notifier` (self-contained — the package keeps its own copies rather than importing the OCR pipeline's) |
| [output_layout.py](helper/output_layout.py) | Pure path builders for the archive (`archive_invoice_dest`, `archive_vat_dest`) and reject (`reject_dest`) trees, plus `classify` (E-TAX vs Paper [Scan]) |
| [sql_normalize.py](helper/sql_normalize.py) | DuckDB SQL fragments `norm_taxid_sql` (strip non-digits + lpad-13) and `norm_text_sql` (lower + NFC + strip whitespace/zero-width) for buyer matching |
| [constant.py](helper/constant.py) | `ExtractionStatus` (Completed/RequiresReview), `MappingZ45Status` (Completed/Incompleted); fact-check `FIELD_MAPPING` + `FactCheckField` + `COMPARE_*` / `NA_SENTINEL` / `OVERALL_LABEL` / `FACT_CHECK_LOG_*` constants |
| [messages.py](helper/messages.py) | Remark text: `MappingMasterMessage`, `RequiredFieldMessage`, `ValidationMessage`, `MappingZ45Message`, and the `EXTRACTION_REVIEW_REMARK` / `EXTRACTION_SYSTEM_FAILURE_REMARK` constants |

## Schemas (`schema/`)

| Schema | File | Frame |
|---|---|---|
| `MasterBuyer` | [master_buyer.py](schema/master_buyer.py) | Master Buyer source (Tax ID read as text; SAP company code) |
| `MasterVendor` | [master_vendor.py](schema/master_vendor.py) | Master Vendor source (vendor code + TH/EN names) |
| `Z45Input` + `validate_z45` | [z45_input.py](schema/z45_input.py) | Positional loader for the SAP ZAPRPT45 export (unreliable headers → renamed by position; typed amounts/dates) |
| `ExtractionProcessing` | [extraction_processing.py](schema/extraction_processing.py) | Builder→reconcile contract (typed amounts/dates + folded buyer verdict + `DATADATE`/`ISSUE_FLAG`) |
| `ExtractionOutput` + `to_extraction_output` | [extraction_output.py](schema/extraction_output.py) | Exported extraction report (projection of `ExtractionProcessing`, `DATADATE` dropped) |
| `ReportOutput` | [report_output.py](schema/report_output.py) | Final 37-column Output Report (all-string, `'No'` defaults) |
| `Z45Output` + `Z45_OUTPUT_HEADERS` | [z45_output.py](schema/z45_output.py) | Enriched Z45 re-export (+ `Mapping Tax Invoice Status`); duplicate `Tax Cleari` headers applied positionally |
| `GroundTruthSchema` + `GROUND_TRUTH_FILE_KEY` | [ground_truth.py](schema/ground_truth.py) | Fact-check ground-truth workbook (free-text headers aliased to canonical `snake_case`; `file_name` is the join key) |

## Configuration

Driven by [`config/tax_invoice_extraction/`](../../config/tax_invoice_extraction/); see that
folder's README for the full config, env vars, email templates, and SharePoint destination
paths.
