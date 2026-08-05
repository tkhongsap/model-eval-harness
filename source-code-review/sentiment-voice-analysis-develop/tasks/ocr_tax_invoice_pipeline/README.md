# OCR Pipeline (`tasks/ocr_tax_invoice_pipeline`)

A **generic, domain-agnostic, config-driven** document-OCR batch pipeline. It ingests
source documents from SharePoint, gates them on image quality (IQS), submits the accepted
pages to the Gemini API (Vertex AI Batch), retrieves and validates the predictions, and
stamps a terminal per-file status back to an append-only log.

Nothing in this package hard-codes a domain, bucket, or SharePoint site — those live in
YAML config and environment variables. A new domain adopts the pipeline by writing YAML
only (its own `domain:` key, SharePoint credentials, GCS bucket paths, and log paths) and
naming the same three registered tasks. **Zero code changes.** Only the default prompt and
response schema shipped in this package are domain-aware (tax invoices / receipts); a domain
that needs different extraction points `sharepoint.control_site.system_prompt_path` (and, if
it forks the package, its own response model) at its own prompt file on the control
SharePoint site. The shipped [prompt/system_prompt.md](prompt/system_prompt.md) is the
versioned master copy that ops upload there — the runtime downloads the prompt from
SharePoint at submit time and never reads the repo file.

> **Deep dive:** see [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md) for the architecture, complex-logic
> walkthroughs, and the guide to adopting this pipeline for a new domain.

## Registered Tasks

The three tasks are registered as an import side effect (via `tasks/__init__.py` →
`tasks/ocr_tax_invoice_pipeline/__init__.py`) and wired into a pipeline by naming them as
top-level keys in the YAML config. They chain through the engine's `pre_result` threading,
passing a typed `OCRResult` forward.

| Registry name | File | Role |
|---|---|---|
| `OCRSubmitTask` | [submit_task.py](submit_task.py) | Ingest, IQS-gate, and submit. Starts the pipeline; returns `None`. |
| `OCRRetrieveTask` | [retrieve_task.py](retrieve_task.py) | Poll in-flight jobs, collect + validate predictions into an `OCRResult`. Stamps **no** terminal status. |
| `OCRFinalizeTask` | [finalize_task.py](finalize_task.py) | **Always last.** Stamps terminal `SUCCESS` / `SUCCESS_WITH_FAILURE` / `FAILED` only *after* the business task(s) succeed. |

### Chain shape

```
OCRSubmitTask        → submits the batch; files land PENDING/PARTIAL in the pre-processing log
                       (returns None — start of the pipeline)

OCRRetrieveTask      → collects predictions into an OCRResult (final_df + terminal file_statuses
                       + the pre-processing-log / page-manifest snapshots); stamps NO status
<business task(s)>   → consume OCRResult.final_df, do domain work, and return the OCRResult unchanged
OCRFinalizeTask      → ALWAYS last; appends terminal status rows only after business logic succeeds
```

Because `OCRFinalizeTask` runs **after** the business task(s), a business-task exception
leaves the files `PENDING` / `PARTIAL` and the next run re-collects the (already-completed)
Vertex predictions from GCS at **zero additional Gemini cost**. Finalize is idempotent: it
stamps only files whose latest log status is still in-flight, so a re-run is a no-op.

The business tasks are **not** part of this package — they live in each domain's own task
module and are wired in via YAML. In the tax-invoice adoption, the post pipeline chains
`OCRRetrieveTask → ReconcilePrecheckTask → ReconcileTask → OCRFinalizeTask`.

## Configuration & Running

Two single-task-style pipelines split the daily run into submit and retrieve halves (config
files under `config/tax_invoice_extraction/`):

| Config | Task chain |
|---|---|
| [`ocr_pipeline_pre_tasks.yml`](../../config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml) | `OCRSubmitTask` (+ any domain pre-tasks, e.g. `TaxInvoiceRejectTask`) |
| [`ocr_pipeline_post_tasks.yml`](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml) | `OCRRetrieveTask` → business task(s) → `OCRFinalizeTask` |

```bash
# Submit (pre-processing)
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml

# Retrieve + business + finalize (post-processing)
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml
```

Optional CLI date-window flags (all `YYYY-MM-DD`) drive `OCRSubmitTask` ingestion when
`sharepoint.source_site.src_path` carries a `%{DATA_DATE...}` placeholder:

- `--rerun_data_dt` / `-r` — replay a single date
- `--start_data_dt` / `-s` and `--end_data_dt` / `-e` — backfill a date range

Without a `%{DATA_DATE}` placeholder in `src_path`, the single configured path is listed
(and any window flags are ignored with a warning).

> Concurrency note: `OCRSubmitTask` and `OCRRetrieveTask` write the same
> `pre_processing_log.csv`. The GCS log write is generation-guarded with one retry, but avoid
> running the pre and post pipelines simultaneously against the same log.

### Config keys per task

Each task validates its own required keys in `validate()` (via `missing_string_errors` /
`int_castable_errors` in [src/utils/common.py](../../src/utils/common.py)) and halts on any error. Placeholders
(`${ENV_VAR}`, `%{DATA_DATE...}`, `${JOB_ID}`) are resolved per task at runtime.

- **`OCRSubmitTask`** (required): `gcp.project_id`; `gcs.project_id`, `gcs.landing_path`
  (must start with `gs://`), `gcs.processing_path`, `gcs.payload_landing_path`,
  `gcs.output_path`, `gcs.pre_processing_log_path`, `gcs.page_manifest_log_path`;
  `vertexai.project_id`, `vertexai.location`, `vertexai.model`; the full
  `sharepoint.source_site.*` block (`site_name`, `site_domain`, `site_path`, `client_id`,
  `client_secret`, `tenant_id`, `src_path`); `sharepoint.control_site.pre_processing_log_path`,
  `sharepoint.control_site.page_manifest_log_path`,
  `sharepoint.control_site.system_prompt_path` (the system prompt is downloaded from the
  control site at submit time; missing or blank fails the run); `framework.iqs_config_path`,
  `framework.concurrency_upload`. Optional:
  `framework.batch_job_limit` (default 100 000), `framework.batch_status_check_delay_seconds`
  (default 2), `framework.ext_filter` (default `['.pdf', '.jpg', '.jpeg', '.png']`),
  `vertexai.generation_config` (dict).
- **`OCRRetrieveTask`** (required): `gcp.project_id`; `gcs.project_id`,
  `gcs.pre_processing_log_path`, `gcs.page_manifest_log_path`; `vertexai.location`;
  `sharepoint.control_site.pre_processing_log_path`, `sharepoint.control_site.tracing_log_path`.
- **`OCRFinalizeTask`** (required): `gcs.project_id`, `gcs.pre_processing_log_path`;
  `sharepoint.control_site.pre_processing_log_path`.

All three tasks also read a `domain:` key and an optional
`framework.notifications.system_exception` block (a best-effort system-error email on
`on_error`), and pull control-site credentials + Microsoft Graph credentials from the shared
`control:` / `msgraph:` blocks of `config/common.yml`.

### IQS quality gate

[`config/tax_invoice_extraction/iqs_config.yml`](../../config/tax_invoice_extraction/iqs_config.yml)
tunes the image-quality gate. IQS is a custom in-house heuristic:

```
IQS = wV·VQ + wS·SQ + wC·CT
```

- **VQ** — visual quality (blur / sharpness)
- **SQ** — structural quality (skew / orientation)
- **CT** — content type (text density / foreground contrast)

Weights must sum to 1.0. Pages scoring below `threshold` (or below any set per-dimension
`sub_thresholds` floor) are **not** sent to Gemini; they are written to the page manifest
with `quality_status = REJECTED` and surface downstream as `FAILED`. Scoring is mandatory —
every page is always scored (there is no enable flag).

## Modules (`module/`)

| Module | Class(es) | Responsibility |
|---|---|---|
| [gcs_router.py](module/gcs_router.py) | `GcsRouter` | Resolve `gcs.*` paths (`${JOB_ID}` → `${ENV_VAR}` → `%{DATA_DATE}`) and cache one `GCSModule` per distinct bucket (each path may name a different bucket in the same project). |
| [source_loader.py](module/source_loader.py) | `SourceFileLoader` | List source files from SharePoint (union over the date window), dedupe against in-flight files, and upload to the GCS landing path (async, concurrency-limited). |
| [document_processor.py](module/document_processor.py) | `DocumentProcessor` | Split a PDF/image into per-page files and IQS-score each page; build manifest rows + accepted-page upload dicts. |
| [page_processor.py](module/page_processor.py) | `PageProcessor` | Read each landing copy, run the document processor, upload accepted page chunks to the processing path, and emit `ChunkEntry` list + manifest rows. |
| [payload_builder.py](module/payload_builder.py) | `PayloadBuilder` | Build Vertex AI Batch JSONL request lines from page URIs (response schema derived from `ReceiptExtraction`); split at the line limit. |
| [batch_submitter.py](module/batch_submitter.py) | `BatchSubmitter` | Upload each JSONL payload and submit one Vertex batch job per payload; capture per-job submission failures without aborting the rest. |
| [batch_job_client.py](module/batch_job_client.py) | `BatchJobClient` | Thin wrapper over `GeminiBatchModule` — submit, verify initial status, poll job detail/status. |
| [result_retriever.py](module/result_retriever.py) | `BatchResultRetriever` | Locate each succeeded job's `predictions.jsonl`, validate each line against `ReceiptExtraction`, and explode into one row per line item (document fields repeated); emit summary rows for failed jobs. |
| [result_finalizer.py](module/result_finalizer.py) | `ResultFinalizer` | Join predictions back to their SharePoint source file/page via the page manifest (DuckDB), union IQS-rejected pages as `FAILED`, and validate against `OCROutputSchema`. |
| [status_finalizer.py](module/status_finalizer.py) | (pure functions) | `resolve_terminal_statuses`, `rollup_status`, `aggregate_file_messages`, `build_terminal_log_rows` — roll up per-page statuses to a file-level `JobStatus` and clone/stamp terminal log rows (no I/O). |
| [pre_log_builder.py](module/pre_log_builder.py) | `PreLogRowBuilder`, `PreLogContext` | Build the `INITIAL` + terminal-for-now (`PENDING`/`PARTIAL`/`REJECTED`/`FAILED`) pre-processing-log rows for each file in a submit run. |
| [log_exporter.py](module/log_exporter.py) | `LogExporter` | Append-only read-merge-write of the pre-processing and page-manifest CSVs to GCS (generation-guarded with one retry) + SharePoint mirror. |
| [tracing_builder.py](module/tracing_builder.py) | `TracingLogBuilder`, `TracingLogContext` | Build raw-Gemini audit rows (request trimmed of static parts, verbatim response) from prediction lines. |
| [tracing_exporter.py](module/tracing_exporter.py) | `TracingLogExporter` | Persist the tracing log SharePoint-only, one CSV per month (`tracing_log_YYYYMM.csv`), pruning month-files past the 3-month retention window. |

## Schemas (`schema/`)

| Schema | File | Type / Purpose |
|---|---|---|
| `ReceiptExtraction`, `InvoiceLineItem` | [model_response.py](schema/model_response.py) | Pydantic model output — the Gemini response contract (tax-invoice/receipt fields + line items). Drives the batch `response_schema`. |
| `OCROutputSchema` | [ocr_output.py](schema/ocr_output.py) | Pandera frame — the finalized output (one row per page/line item). Every data field nullable; only `STATUS` is required. |
| `PreProcessingLogSchema`, `PageManifestLogSchema` | [pre_processing_log.py](schema/pre_processing_log.py) | Pandera frames — append-only per-file status log and per-page IQS/chunk manifest. |
| `TracingLogSchema` | [tracing_log.py](schema/tracing_log.py) | Pandera frame — append-only raw-Gemini request/status/response audit log. |
| `ChunkEntry`, `BatchSubmission`, `OCRResult` | [contracts.py](schema/contracts.py) | Dataclass hand-off contracts. `OCRResult` is the typed object threaded from `OCRRetrieveTask` through the business task(s) to `OCRFinalizeTask` (carries `final_df`, `file_statuses`, and the pre-processing-log / page-manifest snapshots). |

## Helpers (`helper/`)

- [constant.py](helper/constant.py) — status enums + the deterministic tiebreaker:
  - **`JobStatus`** (file-level): `INITIAL`, `PENDING`, `PARTIAL`, `REJECTED`, `FAILED`,
    `SUCCESS`, `SUCCESS_WITH_FAILURE`.
  - **`QualityStatus`** (per-page IQS gate): `ACCEPTED`, `REJECTED`.
  - **`OCROutputStatus`** (per-line extraction outcome): `SUCCESS`, `FAILED`, `SUSPICIOUS`
    (prompt-injection / jailbreak attempt, `DOC_TYPE=Suspicious`), `UNSUPPORTED`
    (`DOC_TYPE=Other`), `BLANK` (no line items). Each consuming domain applies its own
    validation downstream — a normal extracted row is `SUCCESS` here.
  - **`STATUS_RANK`** — lifecycle-order map used as the tiebreaker for "latest status per
    file" when two append-only rows share `update_dt` (Windows clock collisions).
- [task_context.py](helper/task_context.py) — `OCRTaskContext`, the immutable per-run
  context (config blocks + engine packages + `config/common.yml` control/msgraph creds)
  built in each task's `__init__`.
- [log_helper.py](helper/log_helper.py) — `latest_status_per_file` (furthest-progressed row
  per `sharepoint_input_path`) and `unwrap_ocr_result` (shared `OCRResult` unwrapping for
  business tasks).
- [init_conn.py](helper/init_conn.py) — `init_sharepoint`, `init_gcs`
  (connection factories used in each task's `pre_execute`). The pure `validate()`
  collectors (`missing_string_errors`, `int_castable_errors`) live in the framework
  ([src/utils/common.py](../../src/utils/common.py)).
- [error_notify.py](helper/error_notify.py) — `notify_system_error` (best-effort,
  config-gated system-error email on `on_error`; never raises).
- [messages.py](helper/messages.py) — business-facing message text: `STATUS_MESSAGES`
  (`BLANK`/`UNSUPPORTED`) and `iqs_reject_reason` (plain-language IQS reject reason, no
  scores). Domain field/amount validation lives in the consuming domain, not here.

> **Cross-package contract**: a consuming business package (e.g. `tasks/tax_invoice_reconcile/`)
> may import only this package's hand-off surface — `schema/contracts.py` (`OCRResult`), the
> `helper/constant.py` status enums, `helper/log_helper.py` (`unwrap_ocr_result`), and
> `module/log_exporter.py` (`LogExporter`, for reading the pre-processing / page-manifest log
> artifacts). Everything else is internal; infra factories/validators are duplicated per
> package or live in `src/utils/`.

## Dedupe & idempotency

- **Submit** re-runs skip only files whose latest pre-processing-log status is in-flight
  (`PENDING` / `PARTIAL`). `SUCCESS` / `FAILED` / `REJECTED` files are eligible for
  re-processing.
- **Retrieve** polls each in-flight batch job once, retrieves succeeded/partially-succeeded
  jobs, and returns `None` when nothing is in-flight or every in-flight job is still running.
  Dead jobs (`FAILED` / `CANCELLED` / `EXPIRED`) still yield an `OCRResult` so their files do
  not leak as eternally `PENDING`.
- **Finalize** stamps a terminal status only for files still in-flight, so a re-run after a
  successful stamp is a no-op.
