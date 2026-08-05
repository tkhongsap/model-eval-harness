# Tax-Invoice Extraction Config

Pipeline definitions and supporting files for the OCR tax-invoice pipeline v2. The pipeline
is split into two single-purpose runs (a **pre** run that submits, a **post** run that
retrieves + reconciles) rather than one daily job. Each YAML uses **top-level task names** as
keys — the names registered with `@task_registry.register(...)` — and `CoreEngine.run()`
executes them in insertion order. The reserved `pipeline_name` key sets the job-id prefix and
is skipped during execution.

Two placeholder types are resolved per task at runtime: `${ENV_VAR}` (environment variable)
and `%{DATA_DATE[±offset][_FORMAT]}` (date substitution). The tax-invoice tasks also resolve
`${JOB_ID}` from the `job_id` package before env substitution.

## Files

| File | Purpose |
|---|---|
| [ocr_pipeline_pre_tasks.yml](ocr_pipeline_pre_tasks.yml) | Pre (submit) pipeline: `OCRSubmitTask` → `TaxInvoiceRejectTask` |
| [ocr_pipeline_post_tasks.yml](ocr_pipeline_post_tasks.yml) | Post (retrieve + reconcile) pipeline: `OCRRetrieveTask` → `ReconcilePrecheckTask` → `ReconcileTask` → `OCRFinalizeTask` |
| [iqs_config.yml](iqs_config.yml) | IQS (Image Quality Score) weights, threshold, and sub-thresholds; consumed by `OCRSubmitTask` via `framework.iqs_config_path` |
| [email_template/](email_template/) | Plain-text Microsoft Graph notification bodies (see below) |
| [workflows/extraction_tax_invoice_workflow.yaml](workflows/extraction_tax_invoice_workflow.yaml) | GCP Workflows definition that runs the Cloud Run job with the post-config path (`POST_CONFIG_PATH`) |

> The submit task's `OCRSubmitTask` registration lives in `tasks/ocr_tax_invoice_pipeline/`.
> The reconcile/precheck/reject tasks live in `tasks/tax_invoice_reconcile/` (see that
> package's README). `domain: treasury` on each task block is the tracing/audit domain marker
> read by the OCR pipeline tasks.

## Pre pipeline — `ocr_pipeline_pre_tasks.yml`

| Task | Role |
|---|---|
| `OCRSubmitTask` | SharePoint → GCS landing, per-page raster + IQS scoring, upload IQS-valid pages, build Vertex AI Batch JSONL payloads, submit batch job(s), stamp the pre-processing + page-manifest logs. Files with an unsupported extension are logged `REJECTED` (`"Unsupported file type: <ext>"`) instead of being silently dropped |
| `TaxInvoiceRejectTask` | Read this run's stamped logs from GCS, move fully-`REJECTED` files (failed IQS or unsupported file type) and split `PARTIAL` files' bad pages into `sharepoint.source_site.reject_path` |

`OCRSubmitTask` config highlights:
- `gcs.*` — landing / processing / payload / output / log paths, all under
  `gs://${ENVIRONMENT}-${TAX_INVOICE_PROCESSING_BUCKET}/ocr_tax_invoice_workflow/…`
- `vertexai.generation_config` — deterministic decode (`temperature: 0.0`, `seed: 0`,
  `thinkingConfig.thinkingBudget: 0`, `maxOutputTokens: 65535`)
- `framework.iqs_config_path` → `iqs_config.yml`; `framework.concurrency_upload`
- `sharepoint.control_site.system_prompt_path` → `system_prompt.md` on the control site
  (`${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_SYSTEM_PROMPT_PATH}/system_prompt.md`),
  downloaded at submit time so the prompt can change without a deploy; a missing or blank
  file **fails the run**. The repo copy
  `tasks/ocr_tax_invoice_pipeline/prompt/system_prompt.md` is the versioned master that ops
  upload there — it is never read at runtime
- Optional `framework.notifications.system_exception` on-error email

## Post pipeline — `ocr_pipeline_post_tasks.yml`

The engine threads the typed `OCRResult` through the chain:

| Task | Role |
|---|---|
| `OCRRetrieveTask` | Poll each in-flight Vertex job once, validate predictions, join back to source file/page, build the `OCRResult` (`final_df`, `file_statuses`, `dead_job_names`, pre-processing + page-manifest snapshots); no terminal status |
| `ReconcilePrecheckTask` | Halt + email if Master-Buyer / Master-Vendor / Z45 sources are missing; else pass the `OCRResult` through |
| `ReconcileTask` | Reconcile `OCRResult.final_df` vs Master Buyer + Master Vendor + Z45; export Output workbooks + archives + audit logs; return the `OCRResult` unchanged |
| `OCRFinalizeTask` | **Must stay the last key.** Stamp `SUCCESS` / `SUCCESS_WITH_FAILURE` / `FAILED` into the pre-processing log only after reconcile succeeds |

If `ReconcileTask` raises, `OCRFinalizeTask` never runs → files stay `PENDING`/`PARTIAL` → the
next run re-collects from GCS at no extra Gemini cost.

`ReconcileTask` config highlights:
- `sharepoint.source_site` — Master Buyer, Master Vendor, and Z45 file patterns
  (`Master Buyer Company_\d{8}.xlsx`, `Master Vendor Company_\d{8}.xlsx`,
  `ZAPRPT45_\d{8}.xlsx`)
- `sharepoint.destination_site` — `dest_path` (Output workbooks), `archive_invoice_path`,
  `archive_vat_path`, and the date-less `reject_path` (Suspicious pages)
- `sharepoint.control_site` — `extraction_result_path`, `transaction_log_file`,
  `performance_log_file` (all monthly/dated CSVs)
- `framework.notifications` — `extraction_success`, `mapping_success`, `system_exception`
  recipient sets

## IQS config — `iqs_config.yml`

`IQS = wV·VQ + wS·SQ + wC·CT` (a custom in-house heuristic in `src/utils/image_utils.py`, not
a standard IQA metric):
- `weights` (`vq`/`sq`/`ct`) must sum to 1.0.
- `threshold` — pages scoring below it are **not** sent to Gemini and are written `REJECTED`
  to the page manifest.
- `sub_thresholds` — optional per-sub-score floors (`null` disables a floor).
- `visual_quality` / `structural_quality` / `content_type` — sub-score tuning.

## Email templates — `email_template/`

Plain-text bodies rendered by `EmailNotifier` (newlines → `<br>`, `{NAME}` placeholders filled
via `str.format`). `email_template_dir` on each task points here.

| File | Sent by | Trigger |
|---|---|---|
| [dependency_missing.txt](email_template/dependency_missing.txt) | `ReconcilePrecheckTask` | A required source file is missing (`{MISSING_FILES}`); business exception, halts the pipeline |
| [extraction.txt](email_template/extraction.txt) | `ReconcileTask` | Extraction report landed on the control site (`{PROCESSING_NO}` / `{SUCCESS_NO}` / `{FAILED_NO}`) |
| [report.txt](email_template/report.txt) | `ReconcileTask` | Mapping report delivered to the output path |
| [processing_failed.txt](email_template/processing_failed.txt) | any task's `on_error` | System exception; also used as the `body_path` for the pre/retrieve/finalize on-error emails |

## Environment variables

In addition to the shared and Tax-Invoice-specific variables listed in the repo
[CLAUDE.md](../../CLAUDE.md), these YAMLs reference the following SharePoint sub-path and
control-path variables:

- `TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS`, `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`,
  `TAX_INVOICE_TAX_INVOICE_Z45_REPORT` — source sub-folders for the master + Z45 files
  (under `TAX_INVOICE_TAX_INVOICE_ROOT`)
- `TAX_INVOICE_TAX_INVOICE_INPUT` — source-document input folder
- `TAX_INVOICE_TAX_INVOICE_OUTPUT`, `TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV`,
  `TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT`, `TAX_INVOICE_TAX_INVOICE_REJECTED` — destination
  Output / archive / reject sub-folders
- `TAX_INVOICE_CONTROL_ROOT`, `TAX_INVOICE_CONTROL_EXTRACTION_PATH`,
  `TAX_INVOICE_OCR_PREP_LOG_PATH`, `TAX_INVOICE_PAGE_MANIFEST_LOG_PATH`,
  `TAX_INVOICE_OCR_TRACING_LOG_PATH`, `TAX_INVOICE_TRANSACTION_LOG_PATH`,
  `TAX_INVOICE_PERFORMANCE_LOG_PATH` — control-site log/report sub-paths
- `TAX_INVOICE_SYSTEM_PROMPT_PATH` — control-site sub-folder holding `system_prompt.md`
  (the OCR Gemini system prompt, downloaded by `OCRSubmitTask` at submit time)
- Recipient addresses: `BOT_EMAIL`, `USER_EMAIL`, `OPER_EMAIL`, `DEVELOPER_EMAIL`

## Running

```bash
# Pre (submit) — SharePoint → GCS, IQS, batch submit, IQS reject-move
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml

# Post (retrieve + reconcile) — retrieve predictions, reconcile, export, finalize
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml

# Optional date-window flags: --rerun_data_dt / --start_data_dt / --end_data_dt (YYYY-MM-DD)
```
