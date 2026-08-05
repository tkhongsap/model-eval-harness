# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Package manager**: [uv](https://docs.astral.sh/uv/) (not pip/poetry)

```bash
# Install dependencies
uv sync

# Run a pipeline (telesale, qa, or tax invoice)
uv run python main.py --config_path config/sentiment_telesale/telesale_pipeline_tasks.yml
uv run python main.py --config_path config/sentiment_qa/qa_pipeline_tasks.yml
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml

# Date-window flags (all optional, YYYY-MM-DD):
#   --rerun_data_dt / -r   replay a single date
#   --start_data_dt / -s   start of a date range
#   --end_data_dt   / -e   end of a date range
uv run python main.py --config_path <config.yml> --rerun_data_dt 2026-02-13

# Run tests
uv run pytest
uv run pytest tests/core/test_engine.py          # single file
uv run pytest -m "not slow"                      # filter markers
uv run pytest --cov=src --cov=tasks --cov-report=term-missing

# Lint & format (run automatically by pre-commit)
uv run pre-commit install                        # one-time per clone
uv run pre-commit run --all-files                # ad-hoc sweep
uv run ruff check .
uv run ruff format .
```

## Architecture

This is a **pipeline orchestration framework** for AI-powered voice call analysis. It uploads call recordings from SharePoint to GCS, submits them as batch jobs to the Gemini API (Vertex AI), retrieves results, scores them, and exports reports back to SharePoint.

### Framework vs. Project Code

- `src/` — reusable framework (engine, modules, utils). Not specific to any pipeline.
- `tasks/` — project-specific task implementations.
- `config/` — YAML pipeline definitions that wire everything together.

### Core Execution Flow

`main.py` → `CoreEngine` → reads YAML config → instantiates registered tasks in order → runs each task sequentially, passing `pre_result` from one task to the next.

**CoreEngine** ([src/core/engine.py](src/core/engine.py)): Loads config, derives the shared `packages` dict (`execution_dt`, `pipeline_name`, `job_id`), then runs each top-level task key in sequence (the reserved `pipeline_name` key is skipped). It does **not** resolve `${VAR}` / `%{DATA_DATE}` placeholders — each task resolves its own values at runtime via `resolve_env` / `resolve_date` from [src/utils/common.py](src/utils/common.py).

**TaskInterface** ([src/core/task_interface.py](src/core/task_interface.py)): Abstract base class. Override `execute_task()` at minimum. Full lifecycle: `validate → pre_execute → execute_task → post_execute → cleanup` (with `on_error` on failure). Access config via `self.config` (dict) and shared packages via `self.packages` (dict).

**TaskRegistry** ([src/core/task_registry.py](src/core/task_registry.py)): Decorator-based registration. Decorate task classes with `@task_registry.register('TaskName')` so the engine can find them by the name used in YAML config.

### Configuration System

YAML config files in `config/` use **top-level task names** (matching `@task_registry.register('TaskName')`) as the keys; each value is the parameter block for that task. An optional top-level `pipeline_name` key sets the job-id prefix and is skipped during execution. There is no `tasks:` list wrapper — `CoreEngine.run()` iterates `self.config.keys()`. Two placeholder types are resolved **per task at runtime** (not by the engine):
- `${ENV_VAR}` — environment variable substitution
- `%{DATA_DATE[±offset][_FORMAT]}` — date substitution with optional offsets (e.g., `%{DATA_DATE-7D_YYYYMMDD}` = 7 days ago)

The tax-invoice tasks additionally resolve `${JOB_ID}` (from the `job_id` package) before env substitution.

### Key Integration Modules

| Module | File | Auth |
|---|---|---|
| Google Cloud Storage | [src/modules/google/gcs.py](src/modules/google/gcs.py) | Application Default Credentials |
| Vertex AI Batch (Gemini) | [src/modules/google/gemini_batch.py](src/modules/google/gemini_batch.py) | ADC or API key |
| SharePoint (Microsoft Graph) | [src/modules/microsoft/sharepoint.py](src/modules/microsoft/sharepoint.py) | MSAL OAuth2 |

SharePoint operations retry on HTTP 423/409 (up to 3 times, 10s delay).

### Telesale Pipeline Tasks

Defined in `tasks/sentiment_telesale/`, registered and run in order per the config:

1. `TelesaleGetBatchResultTask` — poll and retrieve results from previous batch
2. `TelesalePrepResultTask` — score results using `config/sentiment_telesale/telesale_scoring.yml`
3. `TelesaleExportOutputResultTask` — export to SharePoint + GCS
4. `TelesaleUploadVoiceTask` — SharePoint → GCS (async, concurrency-limited)
5. `TelesalePrepPayloadTask` — build JSONL batch payloads
6. `TelesaleExecuteBatchJobTask` — submit to Vertex AI Batch API
7. `TelesaleEvaluationOutputTask` — generate evaluation reports
8. `TelesaleFactCheckTask` — fact-checking pipeline (in `tasks/sentiment_telesale/fact_check_task.py`)

Pipeline configs: `telesale_pipeline_tasks.yml` (main, tasks 1–6), `telesale_pipeline_evaluate.yml` (evaluation: tasks 1–2 then 7), `telesale_pipeline_fact_check.yml` (fact-check, task 8 standalone).

The pipeline retrieves the previous day's batch results first (tasks 1–3), then uploads new voice files and submits a new batch (tasks 4–6) in the same run.

See [tasks/sentiment_telesale/README.md](tasks/sentiment_telesale/README.md) for full task documentation.

### QA Pipeline Tasks

Defined in `tasks/sentiment_qa/`, registered and run in order per the config:

1. `QAGetBatchResultTask` — poll and retrieve results from previous batch
2. `QAExportOutputResultTask` — score results (using `user_config.xlsx` weights) and export to SharePoint + GCS
3. `QAUploadVoiceTask` — SharePoint → GCS (async, concurrency-limited, multi-product)
4. `QAPrepPayloadTask` — build JSONL batch payloads
5. `QAExecuteBatchJobTask` — submit to Vertex AI Batch API
6. `QAFactCheckTask` — fact-checking pipeline (in `tasks/sentiment_qa/fact_check_task.py`)
7. `QAUserPlaygroundTask` — on-demand user-playground pipeline (in `tasks/sentiment_qa/user_playground_task.py`)

Pipeline configs: `qa_pipeline_tasks.yml` (main, tasks 1–5), `qa_pipeline_fact_check.yml` (fact-check, task 6 standalone), `qa_pipeline_user_playground.yml` (user-playground, task 7 standalone).

The main pipeline retrieves the previous day's batch results first (tasks 1–2), then uploads new voice files and submits a new batch (tasks 3–5) in the same run. Unlike the telesale pipeline, scoring is performed inside `QAExportOutputResultTask` rather than a separate `PrepResult` task.

### OCR Tax Invoice Pipeline Tasks

Two cooperating task packages, wired together only in YAML. The generic OCR package owns batch submit/retrieve/finalize; the reconcile package owns the tax-invoice business logic. Config files live in `config/tax_invoice_extraction/`.

**`tasks/ocr_tax_invoice_pipeline/`** — a generic, domain-agnostic OCR batch pipeline. Three registered tasks thread a typed `OCRResult` ([schema/contracts.py](tasks/ocr_tax_invoice_pipeline/schema/contracts.py)) via the engine's `pre_result`:

1. `OCRSubmitTask` ([submit_task.py](tasks/ocr_tax_invoice_pipeline/submit_task.py)) — SharePoint → GCS landing (async), per-page PDF raster + IQS scoring, upload IQS-accepted pages to the GCS processing path, build Vertex AI Batch JSONL payloads, submit one batch job per payload, then append rows to the pre-processing-log + page-manifest CSVs (GCS + SharePoint). Files land `PENDING`/`PARTIAL`.
2. `OCRRetrieveTask` ([retrieve_task.py](tasks/ocr_tax_invoice_pipeline/retrieve_task.py)) — reads in-flight (`PENDING`/`PARTIAL`) log rows, polls each Vertex AI job once, retrieves every `predictions.jsonl`, validates each line against `ReceiptExtraction`, joins predictions back to their source file/page via the page manifest (DuckDB), and returns an `OCRResult` (`final_df` + `file_statuses` + log snapshots). **Stamps no terminal status.**
3. `OCRFinalizeTask` ([finalize_task.py](tasks/ocr_tax_invoice_pipeline/finalize_task.py)) — **always last**; stamps terminal `SUCCESS`/`SUCCESS_WITH_FAILURE`/`FAILED` into the pre-processing log, only after the business tasks succeed. If a business task raises, finalize never runs → files stay in-flight → the next run re-collects from GCS at zero extra Gemini cost.

Adopting a new domain is YAML-only (its own `domain:` key, SharePoint creds, bucket env vars, log paths); nothing in this package hard-codes a domain/bucket/site — only the default prompt/schema file contents are domain-aware.

**`tasks/tax_invoice_reconcile/`** — the tax-invoice business tasks that consume `OCRResult.final_df` and return the `OCRResult` unchanged:
- `ReconcilePrecheckTask` ([precheck_task.py](tasks/tax_invoice_reconcile/precheck_task.py)) — halts (and email-notifies) if the Master Buyer / Master Vendor / Z45 report sources are missing; else passes through.
- `ReconcileTask` ([reconcile_task.py](tasks/tax_invoice_reconcile/reconcile_task.py)) — reconciles extracted rows against Master Buyer + Master Vendor + Z45, exports per-document Output workbooks + archives (invoice + Z45) + audit logs (transaction/performance/extraction), returns the `OCRResult` unchanged.
- `TaxInvoiceRejectTask` ([reject_task.py](tasks/tax_invoice_reconcile/reject_task.py)) — moves IQS-rejected pages/files to the SharePoint reject folder; runs in the **pre** pipeline alongside `OCRSubmitTask`.
- `TaxInvoiceFactCheckTask` ([fact_check_task.py](tasks/tax_invoice_reconcile/fact_check_task.py)) — a post-processing quality task that measures extraction quality against a human-labelled ground truth and reports it as structured `AI-Operation Fact Check log` JSON lines (not Excel), on its own fact-check pipeline configs (below) that reuse the generic OCR submit/retrieve/finalize tasks unchanged. Consumes `OCRResult.final_df`, runs it (+ Master Buyer) through the reconcile `ExtractionReportBuilder`, compares per-document fields against `ground_truth.xlsx` field-by-field (correct-vs-incorrect confusion matrix, matching telesale/QA: `TP`=match, `FP`=mismatch, `FN=TN=0`; a *blank* GT cell matches only when **every** candidate extraction column is blank, so a hallucination alongside a null Thai/English sibling scores `FP`), and emits per-field + `overall` metric rows (`accuracy`/`precision`/`recall`/`f1_score`) via the shared `logging_ai_operation(log_type="fact_check")` util ([src/utils/common.py](src/utils/common.py)). `f1_score` is computed from raw counts as `2·TP/(2·TP+FP+FN)` (the manual-evaluation form), but with `FN=TN=0` it reduces to `2A/(A+1)` and `precision` equals `accuracy` — **only accuracy carries information**; see the reconcile DEVELOPER_GUIDE §7.3. Writes transaction + performance logs like `ReconcileTask` (`ExportLogging`), returns the `OCRResult` unchanged, and sends two emails: the fact-check **result** (best-effort, `BOT_EMAIL`→`USER_EMAIL` cc `DEVELOPER_EMAIL`+`OPER_EMAIL`; `fact_check_result.txt` template + accuracy-only HTML table via `EmailNotifier.build_fact_check_table`, whose per-field Baseline column loads from the UAT-baseline YAML named by `framework.notifications.fact_check_result.baseline_path` — `config/tax_invoice_extraction/fact_check_uat_baseline.yml`, keyed by `FIELD_MAPPING` `gt_field` names (snake_case) → fraction, label mapped to `gt_field` at render, unmapped label → 0.00%, `overall` row excluded/log-only; skipped when nothing matched ground truth; subject/template as code constants, `baseline_path` required by `validate()`) and a **system-error** alert (`BOT_EMAIL`→`DEVELOPER_EMAIL` cc `OPER_EMAIL`) on failure. Its fact-check-specific modules/schema/helpers live in the same package's `module/` (`fact_check_evaluator`, `ground_truth_loader`, `value_normalizer`), `schema/ground_truth.py`, and `helper/` (the `emit_fact_check_logs` function in `fact_check_log_emitter.py`, `FIELD_MAPPING`/`FactCheckField` in `constant.py`, `ReconcileTaskContext` in `task_context.py`). All reference files + logs live on the **control** site under `${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_FACT_CHECK_PATH}`; `resources/fact_check_ref/*` is the local dev copy (test fixtures only). See [tasks/tax_invoice_reconcile/README.md](tasks/tax_invoice_reconcile/README.md).

Config files:
- `ocr_pipeline_pre_tasks.yml` — `OCRSubmitTask` + `TaxInvoiceRejectTask` (submit stage).
- `ocr_pipeline_post_tasks.yml` — `OCRRetrieveTask` → `ReconcilePrecheckTask` → `ReconcileTask` → `OCRFinalizeTask` (retrieve + reconcile + finalize; `OCRFinalizeTask` MUST stay the last key).
- `ocr_pipeline_fact_check_pre_tasks.yml` — `OCRSubmitTask` only (fact-check submit; Cloud Scheduler).
- `ocr_pipeline_fact_check_post_tasks.yml` — `OCRRetrieveTask` → `TaxInvoiceFactCheckTask` → `OCRFinalizeTask` (fact-check retrieve + score + finalize; Eventarc; `OCRFinalizeTask` MUST stay the last key).

Pipeline path: **ingest (PDF/JPEG/PNG) → per-page raster + IQS scoring (`config/tax_invoice_extraction/iqs_config.yml`; pages below `threshold` are written `REJECTED` to the page-manifest CSV and never sent to Gemini) → fused classify+extract prompt (downloaded at submit time from the control site — `sharepoint.control_site.system_prompt_path`, i.e. `${TAX_INVOICE_CONTROL_ROOT}/${TAX_INVOICE_SYSTEM_PROMPT_PATH}/system_prompt.md`; a missing or blank file fails the run; `tasks/ocr_tax_invoice_pipeline/prompt/system_prompt.md` is the versioned master ops upload there, never read at runtime; no SharePoint knowledge-base injection) → batch job**.

Key building blocks (in `tasks/ocr_tax_invoice_pipeline/` unless noted):
- **Statuses** ([helper/constant.py](tasks/ocr_tax_invoice_pipeline/helper/constant.py)): file-level `JobStatus` (INITIAL/PENDING/PARTIAL/REJECTED/FAILED/SUCCESS/SUCCESS_WITH_FAILURE), per-page `QualityStatus` (ACCEPTED/REJECTED), per-line `OCROutputStatus` (SUCCESS/FAILED/SUSPICIOUS/UNSUPPORTED/BLANK — no domain validation here; each consuming domain validates its own rows). `STATUS_RANK` is the deterministic tiebreaker for "latest status per file".
- **Schemas** (`schema/`): `ReceiptExtraction` + `InvoiceLineItem` ([model_response.py](tasks/ocr_tax_invoice_pipeline/schema/model_response.py), Pydantic model output), `OCROutputSchema` ([ocr_output.py](tasks/ocr_tax_invoice_pipeline/schema/ocr_output.py), final frame), `PreProcessingLogSchema` + `PageManifestLogSchema` ([pre_processing_log.py](tasks/ocr_tax_invoice_pipeline/schema/pre_processing_log.py)), `TracingLogSchema` ([tracing_log.py](tasks/ocr_tax_invoice_pipeline/schema/tracing_log.py), raw-Gemini trace), `OCRResult`/`ChunkEntry`/`BatchSubmission` ([contracts.py](tasks/ocr_tax_invoice_pipeline/schema/contracts.py), typed hand-off).
- **Modules** (`module/`): source loading (`source_loader.py`), PDF raster + IQS (`document_processor.py`, `page_processor.py`), per-bucket GCS routing (`gcs_router.py`), payload building (`payload_builder.py`), batch submit/poll/retrieve (`batch_submitter.py`, `batch_job_client.py`, `result_retriever.py`), result + status finalizing (`result_finalizer.py`, `status_finalizer.py`), and log/tracing export (`pre_log_builder.py`, `log_exporter.py`, `tracing_builder.py`, `tracing_exporter.py`). The reconcile package has its own `module/` (reconciliation, report/output export, archiving, rejecting, email).

Dedupe: re-runs skip only files whose latest pre-processing-log status is `PENDING`/`PARTIAL` (in-flight). Each `gcs.*` config path may name a different bucket within the same project; one `GCSModule` is cached per bucket (via `gcs_router.py`).

Retention ([helper/log_retention.py](tasks/ocr_tax_invoice_pipeline/helper/log_retention.py)): **every** tax-invoice log is bounded by one knob — `TAX_INVOICE_LOG_RETENTION_DAYS`, wired as `framework.log_retention_days` in all four configs. **A negative value (`-1`) disables retention entirely**; unset or unparseable falls back to 90 days (never a hard failure). Retention is intrinsic, not optional: `LogExporter` takes the window in its constructor and prunes the merged frame inline before the upload, inside the generation precondition (no extra I/O, re-applied on the write-race retry).

Two log shapes, two rules:
- **Cumulative files** (`ocr_pre_processing_log.csv`, `page_manifest_log.csv`) — prune *rows*, purely by age, **regardless of status**. An aged in-flight (`PENDING`/`PARTIAL`) row is a stuck file (repo-review P1-3) — pruning it deliberately makes the file re-processable (the dedupe stops skipping it), so keep the window well above batch-job runtime; a very short window can double-submit a still-running job's file. Aged *terminal* rows are inert — a completed document is kept out of the input folder by the archive/reject move (`source_archiver.py`, `iqs_rejecter.py`), not by this log. The page manifest has no timestamp column, so its rows age out via the `job_id` they share with the pre-processing log; a `job_id` absent from that log is kept (fail-safe for concurrent runs).
- **Month-partitioned files** (`transaction_log_YYYYMM.csv`, `performance_log_YYYYMM.csv` via `ExportLogging`; `tracing_log_YYYYMM.csv` via `TracingLogExporter`) — delete whole expired *month-files* (best-effort; a sweep failure never fails the run), plus a row-prune on `load_dt` so a sub-30-day window still works inside the current month's file.

Rows with an unparseable timestamp are kept, not silently purged.

### Environment Variables

Copy `.env.example` to `.env`. Project-specific variables live in `cloud_build/sentiment_telesale/.env.example` (telesale) and `cloud_build/sentiment_qa/.env.example` (QA).

**Shared (all pipelines):**
- `ENVIRONMENT` — `nprd`, `release`, or `prod` (controls log format: dev vs JSON)
- `LOG_LEVEL` — logging verbosity
- `VERINT_SITE_*` — SharePoint site credentials for voice files (name/client_id/secret/tenant/domain/path)
- `CONTROL_SITE_*` — SharePoint site credentials for control files
- `GEMINI_COST_PATH` — path to Gemini cost logs

**Telesale-specific:**
- `TELESALE_GCP_PROJECT_ID`, `TELESALE_GCP_PROJECT_NAME`, `TELESALE_PROCESSING_BUCKET` — GCP resources
- `TELESALE_VERTEX_AI_MODEL_NAME`, `TELESALE_VERTEX_AI_LOCATION` — Gemini model config
- `TELESALE_VERINT_ROOT/INPUT/OUTPUT`, `TELESALE_MASTER_PATH` — Verint SharePoint paths
- `TELESALE_CONTROL_ROOT`, `TELESALE_CONTROL_FILE_PATH`, `TELESALE_USER_PROMPT_PATH` — control paths
- `TELESALE_TRANSACTION_LOG_PATH`, `TELESALE_PERFORMANCE_LOG_PATH`, `TELESALE_BATCH_PROCESSING_LOG_PATH` — log paths
- `TELESALE_FACT_CHECK_PATH`, `TELESALE_RAW_PREDICTION_PATH` — fact-check and monitoring paths
- `TELESALE_LOOKBACK_DAYS`, `TELESALE_BATCH_SIZE`, `TELESALE_MAX_CONCURRENT_UPLOADS` — performance settings
- `IS_MONITORING_ENABLED` — enable raw prediction export

**QA-specific:**
- `QA_GCP_PROJECT_ID`, `QA_GCP_PROJECT_NAME`, `QA_PROCESSING_BUCKET` — GCP resources
- `QA_VERTEX_AI_MODEL_NAME`, `QA_VERTEX_AI_LOCATION` — Gemini model config
- `QA_VERINT_ROOT`, `QA_VERINT_PRODUCTS`, `QA_VERINT_PRODUCTS_INBOUND`, `QA_VERINT_PRODUCTS_OUTBOUND`, `QA_VERINT_OUTPUT` — Verint SharePoint paths (multi-product, inbound/outbound)
- `QA_CONTROL_ROOT`, `QA_CONTROL_FILE_PATH`, `QA_USER_PROMPT_PATH` — control paths
- `QA_TRANSACTION_LOG_PATH`, `QA_PERFORMANCE_LOG_PATH`, `QA_BATCH_PROCESSING_LOG_PATH` — log paths
- `QA_FACT_CHECK_PATH`, `QA_FACT_CHECK_PRODUCTS` — fact-check paths and product list
- `QA_USER_PLAYGROUND_PATH` — user-playground pipeline SharePoint root (input/output/archive folders)
- `QA_LOOKBACK_DAYS`, `QA_BATCH_SIZE`, `QA_MAX_CONCURRENT_UPLOADS` — performance settings
- `SANDBOX_SITE_*` — SharePoint sandbox credentials (used for msgraph email sender)
- `BOT_EMAIL`, `USER_EMAIL`, `OPER_EMAIL`, `DEV_EMAIL` — email notification recipients
- `ENVIRONMENT` — set to `local` for human-readable dev logs; any other value (`nprd`, `release`, `prod`) uses structured JSON logs
- `LOG_LEVEL` — logging verbosity

**Tax Invoice-specific** (names referenced by `config/tax_invoice_extraction/*.yml` + the `tax_invoice` block of `config/common.yml`; the tax-invoice SharePoint credentials also live in the root `.env.example`):
- `TAX_INVOICE_GCP_PROJECT_ID`, `TAX_INVOICE_GCP_PROJECT_NAME`, `TAX_INVOICE_PROCESSING_BUCKET` — GCP resources
- `TAX_INVOICE_VERTEX_AI_MODEL_NAME`, `TAX_INVOICE_VERTEX_AI_LOCATION` — Gemini model config
- `TAX_INVOICE_SITE_NAME`, `TAX_INVOICE_SITE_SITE_DOMAIN`, `TAX_INVOICE_SITE_SITE_PATH`, `TAX_INVOICE_SITE_CLIENT_ID`, `TAX_INVOICE_SITE_CLIENT_SECRET`, `TAX_INVOICE_SITE_TENANT_ID` — tax-invoice SharePoint site credentials
- `TAX_INVOICE_TAX_INVOICE_ROOT`, `TAX_INVOICE_TAX_INVOICE_INPUT` — source-document SharePoint paths
- `TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS`, `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`, `TAX_INVOICE_TAX_INVOICE_Z45_REPORT` — reconcile master-data / Z45-report source folders (files matched by regex in the config)
- `TAX_INVOICE_TAX_INVOICE_OUTPUT`, `TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV`, `TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT`, `TAX_INVOICE_TAX_INVOICE_REJECTED` — reconcile-stage destination roots (per-document Output workbooks, archived source invoices, archived Z45 report, rejected pages/files)
- `TAX_INVOICE_CONTROL_ROOT`, `TAX_INVOICE_OCR_PREP_LOG_PATH`, `TAX_INVOICE_PAGE_MANIFEST_LOG_PATH`, `TAX_INVOICE_OCR_TRACING_LOG_PATH`, `TAX_INVOICE_CONTROL_EXTRACTION_PATH`, `TAX_INVOICE_TRANSACTION_LOG_PATH`, `TAX_INVOICE_PERFORMANCE_LOG_PATH` — control-site log / result paths
- `TAX_INVOICE_FACT_CHECK_PATH` — control-site folder for the fact-check reference set (`source_file/`, `ground_truth_file/`, `master_file/`, logs), consumed by `config/tax_invoice_extraction/ocr_pipeline_fact_check_*.yml`
- `TAX_INVOICE_SYSTEM_PROMPT_PATH` — control-site folder holding `system_prompt.md`, the OCR Gemini system prompt downloaded by `OCRSubmitTask` at submit time (a missing or blank file fails the run; the repo copy `tasks/ocr_tax_invoice_pipeline/prompt/system_prompt.md` is the versioned master uploaded there)
- `TAX_INVOICE_MAX_CONCURRENT_UPLOADS` — SharePoint→GCS upload concurrency
- `TAX_INVOICE_LOG_RETENTION_DAYS` — retention window (days) for **all** tax-invoice logs (pre-processing, page-manifest, tracing, transaction, performance). Set to `-1` to disable retention entirely; unset/invalid falls back to 90. Lives in Secret Manager so it can be changed without a deploy.
- Email notifications reuse the shared `BOT_EMAIL` / `USER_EMAIL` / `OPER_EMAIL` / `DEVELOPER_EMAIL` recipients (msgraph sender from the `msgraph` block of `config/common.yml`)
- `ENVIRONMENT` — also used as the GCS bucket-name prefix

> Note: the Terraform project `terraform/projects/tax_invoice_extraction/` (`locals.tf` / `main.tf`) provisions the current `TAX_INVOICE_SITE_*` / `TAX_INVOICE_TAX_INVOICE_*` secret family (the old `TAX_INVOICE_VERINT_*` names are gone). The secret list is now reconciled with the shipped configs: `TAX_INVOICE_OCR_TRACING_LOG_PATH`, `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`, and `TAX_INVOICE_TAX_INVOICE_REJECTED` are provisioned, and the unused legacy secrets (`TAX_INVOICE_BATCH_SIZE`, `TAX_INVOICE_LOOKBACK_DAYS`, `TAX_INVOICE_USER_PROMPT_PATH`, `TAX_INVOICE_CONTROL_FILE_PATH`, `TAX_INVOICE_BATCH_PROCESSING_LOG_PATH`) have been removed. `TAX_INVOICE_FACT_CHECK_PATH` is retained — it is live, consumed by the fact-check configs. `TAX_INVOICE_SYSTEM_PROMPT_PATH` is provisioned and injected into both Cloud Run jobs — it is consumed by the pre-task configs (control-site system-prompt folder).

### Logging

Production uses structured JSON logs via `python-json-logger`. Local dev uses human-readable format. Controlled by `ENVIRONMENT` and `LOG_LEVEL` env vars. Logger is in [src/utils/logger.py](src/utils/logger.py).

### Adding a New Task

1. Create a file in `tasks/<project>/`
2. Subclass `TaskInterface` and override `execute_task()`
3. Decorate with `@task_registry.register('YourTaskName')`
4. Import the class in `tasks/<project>/__init__.py` (the project package is already auto-imported from `tasks/__init__.py`)
5. Add the registry name as a top-level key in the pipeline YAML config, with the task's parameter block as its value

## Orchestration workflow

You (Fable) are the orchestrator. Plan, decompose, synthesize. Conserve your
own usage for planning and high-stakes reasoning; delegate execution:

- Reasoning-heavy phases (architecture, complex debugging, algorithm design)
  → `deep-reasoner` subagent (Opus)
- Mechanical work (boilerplate, tests, formatting, simple edits)
  → `fast-worker` subagent (Sonnet)

For high-stakes decisions, task deep-reasoner on the problem and weigh its
conclusion against your own judgment before committing. Keep your own
context lean — pass subagents self-contained instructions with acceptance
criteria, and take back only their conclusions.
