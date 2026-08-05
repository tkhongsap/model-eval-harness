# Developer Guide — `tasks/ocr_tax_invoice_pipeline`

This is the **deep companion** to [README.md](README.md). The README tells you *what* the package
contains (task table, module table, schema table, config keys). This guide tells you *why* it is
shaped the way it is, and what will bite you.

Two audiences:

1. **A developer taking this package over cold.** Read sections 1–6 and 9.
2. **Another team reusing this pipeline for a different document domain** (not tax invoices).
   Read sections 1, 2, 4, and then section 7 — which is honest about where the "zero code changes"
   promise actually stops.

Everything here is anchored to source. Where a claim depends on external API behaviour
(Vertex AI Batch, Microsoft Graph, GCS) that I could not verify from this repository, it is
called out explicitly as **unverified**.

---

## 1. Purpose and mental model

The package is a **generic document-OCR batch pipeline**. It moves documents from SharePoint to
Gemini (via Vertex AI Batch) and brings structured predictions back, gating on image quality on
the way in and joining predictions back to their source page on the way out.

It is deliberately split into **three registered tasks** rather than one:

| Registry name | File | Returns |
|---|---|---|
| `OCRSubmitTask` | [submit_task.py](submit_task.py) | `None` |
| `OCRRetrieveTask` | [retrieve_task.py](retrieve_task.py) | `OCRResult \| None` |
| `OCRFinalizeTask` | [finalize_task.py](finalize_task.py) | `OCRResult \| None` |

### Why the split — the money argument

Vertex AI Batch is asynchronous and **paid at submission time**. Once a batch job succeeds, its
`predictions.jsonl` sits in GCS and is yours to read as many times as you like, for free. The
expensive, irreversible act is *submitting*.

That single fact drives the whole design:

- **Submit and retrieve are separate pipelines** (`ocr_pipeline_pre_tasks.yml` and
  `ocr_pipeline_post_tasks.yml`), scheduled separately, because a batch job can take hours.
- **`OCRRetrieveTask` stamps no terminal status.** It collects predictions and computes what the
  terminal statuses *would* be, but writes nothing.
- **`OCRFinalizeTask` must be the last task in the YAML.** It is the only thing that stamps
  `SUCCESS` / `SUCCESS_WITH_FAILURE` / `FAILED` into the pre-processing log.

The payoff: **if a business task raises, `OCRFinalizeTask` never runs.** The files stay
`PENDING` / `PARTIAL` in the pre-processing log. On the next run, `OCRRetrieveTask` sees them as
still in-flight, re-polls their (already succeeded) Vertex jobs, and re-reads the same
`predictions.jsonl` from GCS — **at zero additional Gemini cost**. You fix the business bug, re-run
the post pipeline, and nothing was lost and nothing was re-billed.

If finalize ran *before* the business task, a business-task crash would leave files stamped
`SUCCESS` with no downstream output produced, and the next run would skip them entirely. The data
would be silently dropped and the only recovery would be re-submitting (and re-paying for) the
batch.

Finalize is also **idempotent**: `build_terminal_log_rows` ([module/status_finalizer.py:129](module/status_finalizer.py))
skips any file whose current latest status is already terminal, so re-running the post pipeline
after a successful run is a no-op.

---

## 2. End-to-end data flow

```
                                   SUBMIT PIPELINE  (ocr_pipeline_pre_tasks.yml)
 ┌──────────────┐
 │  SharePoint  │  sharepoint.source_site.src_path
 │ source_site  │  (recursive list; one path per data date in the CLI window)
 └──────┬───────┘
        │  SourceFileLoader.list_files_union  →  (supported, unsupported)  by framework.ext_filter
        │  SourceFileLoader.filter_new        →  drops files whose latest log status is PENDING/PARTIAL
        │  SourceFileLoader.upload_to_landing →  async, framework.concurrency_upload
        v
 ┌──────────────────────────┐
 │  GCS  gcs.landing_path   │  immutable copy of the original document
 └──────┬───────────────────┘
        │  PageProcessor reads each landing copy back (NOT SharePoint again)
        │  DocumentProcessor: PDF → per-page raster + IQS score; image → single-page IQS score
        │        ├─ page passed  → QualityStatus.ACCEPTED  → uploaded as a chunk
        │        └─ page failed  → QualityStatus.REJECTED  → NEVER sent to Gemini
        v
 ┌────────────────────────────┐        ┌──────────────────────────────────┐
 │ GCS gcs.processing_path    │        │ page_manifest_log.csv (GCS + SP) │
 │  {stem}_p001.pdf, ...      │        │ one row per page, incl. REJECTED │
 └──────┬─────────────────────┘        └──────────────────────────────────┘
        │  PayloadBuilder: one JSONL line per accepted page URI
        │    request = {contents:[file_data], system_instruction, generation_config+response_schema}
        │    split every framework.batch_job_limit lines (default 100 000)
        v
 ┌────────────────────────────────┐
 │ GCS gcs.payload_landing_path   │  {pipeline}_{YYYYMMDDHHMMSS}_{seq:03d}.jsonl
 └──────┬─────────────────────────┘
        │  BatchSubmitter → BatchJobClient.submit → one Vertex batch job PER payload file
        v
 ┌────────────────────────────────┐        ┌────────────────────────────────────┐
 │  Vertex AI Batch (Gemini)      │        │ pre_processing_log.csv (GCS + SP)  │
 │  dest = gcs.output_path/{stem} │        │ INITIAL row + PENDING/PARTIAL/     │
 └──────┬─────────────────────────┘        │ REJECTED/FAILED row per file       │
        │                                  └────────────────────────────────────┘
        │   ...... hours pass; the submit pipeline has already exited ......
        v
                                   POST PIPELINE  (ocr_pipeline_post_tasks.yml)
 ┌────────────────────────────────┐
 │ predictions.jsonl (GCS)        │  located under job.dest by BatchResultRetriever
 └──────┬─────────────────────────┘
        │  BatchResultRetriever: validate each line against ReceiptExtraction
        │    → explode to one row per line item (document fields repeated)
        │    → TracingLogBuilder rows (raw request/response) → SharePoint tracing log
        v
 ┌────────────────────────────────────────────────────────────────────┐
 │ ResultFinalizer (DuckDB)                                           │
 │   predictions  ⟕ page_manifest (child_path == source_file_uri)     │
 │               ⟕ pre_processing_log (gcs_landing_path == parent)    │
 │   ∪  IQS-rejected manifest pages, forced STATUS = FAILED           │
 │   → OCROutputSchema.validate                                       │
 └──────┬─────────────────────────────────────────────────────────────┘
        │  resolve_terminal_statuses → {sharepoint_input_path: JobStatus}
        v
   OCRResult(final_df, file_statuses, pre_processing_log, page_manifest_log)
        │
        v
 ┌────────────────────────────────┐
 │  <business task(s)>            │  consume final_df; RETURN THE OCRResult UNCHANGED
 └──────┬─────────────────────────┘
        v
 ┌────────────────────────────────┐
 │  OCRFinalizeTask               │  append terminal SUCCESS / SUCCESS_WITH_FAILURE / FAILED
 └────────────────────────────────┘
```

The critical asymmetry: **the page manifest is the join table.** A prediction row knows only its
GCS chunk URI (`source_file_uri`). Only the manifest knows which SharePoint document that chunk
came from, and which page of it. Lose the manifest and the predictions are unattributable.

---

## 3. The three tasks, step by step

All three share the same skeleton: `__init__` builds an immutable `OCRTaskContext`
([helper/task_context.py](helper/task_context.py)), `validate()` collects config errors, and
`on_error()` calls `notify_system_error` ([helper/error_notify.py](helper/error_notify.py)).

### 3.0 The `validate()` / `REQUIRED_STRING_KEYS` story

Each task declares a class-level `REQUIRED_STRING_KEYS: tuple[str, ...]` of **dotted config
paths**. `validate()` runs them through `missing_string_errors`
([src/utils/common.py](../../src/utils/common.py)), which checks only that each path resolves to
a **non-empty string** — placeholders are validated **unresolved**. `${TAX_INVOICE_GCP_PROJECT_ID}`
passes validation even if the env var is unset; resolution is a runtime concern.

This is deliberate. It means a config typo (`gcs.landing_paths`) is caught before any I/O, and it
also means a missing env var is *not*. If you want fail-fast on missing env vars, that check does
not exist today.

`validate()` **collects every error and logs each at ERROR**, then returns `not errors`.
`TaskInterface.run()` ([src/core/task_interface.py:138](../../src/core/task_interface.py)) turns a
`False` return into a raised `ValueError`. So you get the full list of config problems in one run,
not one per re-run.

`OCRSubmitTask` adds three more collectors on top of the string keys:

- `int_castable_errors(..., ("framework.concurrency_upload",), required=True)` — env-resolved then
  `int()`-cast. This is why `concurrency_upload` is **not** in `REQUIRED_STRING_KEYS`.
- `int_castable_errors(..., ("framework.batch_job_limit", "framework.batch_status_check_delay_seconds"), required=False)`
- `_shape_errors()` — `gcs.landing_path` must start with `gs://`; `vertexai.generation_config` must
  be a dict when set; `framework.ext_filter` must be a non-empty list when set.
- `_window_errors()` — calls `resolve_data_date_window` and turns its `ValueError` into a config
  error, so an invalid CLI flag combination halts **before** any SharePoint or GCS call.

### 3.1 `OCRSubmitTask`

**Config keys read** (see the README table for the authoritative list): `domain`, `gcp.project_id`,
the whole `gcs.*` block (`project_id`, `landing_path`, `processing_path`, `payload_landing_path`,
`output_path`, `pre_processing_log_path`, `page_manifest_log_path`), `vertexai.*`
(`project_id`, `location`, `model`, optional `generation_config`), the whole
`sharepoint.source_site.*` block including `src_path`,
`sharepoint.control_site.pre_processing_log_path`, `.page_manifest_log_path` and
`.system_prompt_path`, and
`framework.*` (`iqs_config_path`, `concurrency_upload`, optional
`ext_filter`, `batch_job_limit`, `batch_status_check_delay_seconds`, `notifications`).

**Collaborators built in `pre_execute()`** ([submit_task.py:123](submit_task.py)):

| Attribute | Type | Notes |
|---|---|---|
| `self._sp_control` | `SharePointModule` | credentials from `common.yml` `control:`, not from the task YAML |
| `self._router` | `GcsRouter` | one per run; caches a `GCSModule` per distinct bucket |
| `self._source_loader` | `SourceFileLoader` | SharePoint source + landing `GCSModule` |
| `self._page_processor` | `PageProcessor` | loads `iqs_config_path` via `load_yaml` here |
| `self._batch_submitter` | `BatchSubmitter` | downloads `control_site.system_prompt_path` from the control SharePoint site here (`_load_system_prompt`); missing or blank raises |
| `self._pre_log_builder` | `PreLogRowBuilder` | needs the **source** SharePoint module for `get_web_url` |

**`execute_task()` walkthrough** ([submit_task.py:179](submit_task.py)):

1. `datadate = execution_dt.strftime("%Y%m%d")` — stamped on every pre-log row.
2. Load the existing pre-processing log from GCS (`LogExporter.load_log`).
3. `_in_flight(existing_log)` → the set of `sharepoint_input_path` values whose **latest** status
   is `PENDING` or `PARTIAL`. This is the entire dedupe rule (see §6.9).
4. `_load_source_files(in_flight)`:
   - `_resolve_src_paths()` — if `src_path` has no `%{DATA_DATE...}` placeholder, resolve it once
     and **warn** if window flags were passed anyway. Otherwise resolve once per date in the
     window and order-preserving-dedupe (a coarse format like `%{DATA_DATE_YYYYMM}` collides
     across days).
   - `list_files_union` — lists every path, dedupes by `sp_path` (first wins), skips a path that
     404s, but **raises `RuntimeError` if *every* path fails** so a SharePoint outage cannot
     masquerade as "no new files".
   - `filter_new` drops in-flight files.
   - `upload_to_landing` — async, `asyncio.run`, capped at `framework.concurrency_upload`.
   - Returns `(uploaded, failed, unsupported)`.
5. **Early return**: if all three lists are empty, log `No new files to process` and return `None`.
   Note the condition is `not uploaded and not failed and not unsupported` — unsupported-extension
   files alone are enough to keep going, because they still need their terminal `REJECTED` rows
   written.
6. `PageProcessor.run(uploaded)` → `(manifest_rows, chunks)`.
7. `BatchSubmitter.run(chunks)` → `list[BatchSubmission]`. An empty `chunks` list logs
   `No IQS-valid pages to submit` and returns `[]` — it does not raise.
8. `PreLogRowBuilder.build(...)` → the pre-processing-log rows for this run.
9. `_persist_logs(...)` → soft-validate (log-don't-crash) and append both CSVs.

**Return value**: always `None`. It is the start of its pipeline.

**Why soft validation?** `_validate_soft` ([submit_task.py:286](submit_task.py)) catches
`SchemaErrors` and writes anyway. By the time we are writing logs, **the batch jobs already exist
and are already billable**. Refusing to write the log because a column dtype drifted would orphan
the jobs — we would have paid for predictions we can never attribute. Writing a slightly-off log
is strictly better.

### 3.2 `OCRRetrieveTask`

**Config keys read**: `gcp.project_id`; `gcs.project_id`, `gcs.pre_processing_log_path`,
`gcs.page_manifest_log_path`; `vertexai.location` (and `vertexai.project_id` for the log/tracing
rows); `sharepoint.control_site.pre_processing_log_path`, `.page_manifest_log_path`,
`.tracing_log_path`; `framework.batch_status_check_delay_seconds` (optional),
`framework.notifications`.

**Collaborators built in `pre_execute()`** ([retrieve_task.py:73](retrieve_task.py)):
`_sp_control`, `_batch_client` (`BatchJobClient`), `_router`, `_tracing_builder`,
`_tracing_exporter`, `_retriever` (`BatchResultRetriever`), `_result_finalizer`
(`ResultFinalizer`).

Note `BatchResultRetriever` is handed `self._router.module_for_bucket` — the **bucket-name→module**
resolver, not a single module. Each job's `dest` may live in a different bucket.

**`execute_task()` walkthrough** ([retrieve_task.py:89](retrieve_task.py)):

1. Load the pre-processing log and the page manifest from GCS. These two frames are loaded
   **once** and threaded forward on the `OCRResult` — nothing downstream re-reads them.
2. `_in_flight_jobs(pre_log)` → distinct `batch_inference_job_name` values whose latest per-file
   status is `PENDING`/`PARTIAL`.
   - **Early return `None`** when empty: "No batch jobs in PENDING/PARTIAL status".
3. `_classify_jobs(job_names)` — polls **each job exactly once** via `pull_job_detail`, and
   partitions into `(succeeded, failed, running_names)` using the Vertex `JobState` enum names:
   - `SUCCEEDED_STATES = ("JOB_STATE_SUCCEEDED", "JOB_STATE_PARTIALLY_SUCCEEDED")` — a
     partially-succeeded job **still emits predictions**, so it is treated as succeeded.
   - `TERMINAL_FAILED_STATES = ("JOB_STATE_FAILED", "JOB_STATE_CANCELLED", "JOB_STATE_EXPIRED")`.
   - Anything else is still running.
   - `_normalize_job_state` handles both an enum (`.name`) and a raw string, defaulting to
     `"UNKNOWN_STATE"`.
   - **Early return `None`** when nothing succeeded and nothing failed: all jobs still running.
4. `_collect(succeeded, failed, ...)`:
   - `retrieve_succeeded` and `retrieve_failed` each return `(DataFrame, tracing_rows)`.
   - The **tracing log is exported first**, before the empty-frame check, so a failed-only or
     all-blank run still records its traces.
   - Empty frames are dropped before `pd.concat` (an empty frame's columns are all `object` dtype
     and would raise a pandas `FutureWarning` about dtype coercion).
   - `start_time`/`end_time` are re-normalised from UTC into `ctx.timezone`.
   - `ResultFinalizer.run(...)` does the DuckDB join (§6.5).
5. `resolve_terminal_statuses(final_df, pre_log, dead, running_job_names=running)` (§5).
6. Return `OCRResult(...)`.

**Return value**: `OCRResult`, or `None` on the two early returns above. **Note the subtlety**: if
every polled job *died* with zero predictions, `_collect` returns an empty frame — but we still
build and return an `OCRResult`, because `resolve_terminal_statuses` will force those files to
`FAILED` via `_force_dead`. Returning `None` there would leave the files **eternally PENDING**:
retrieve would re-poll the same dead job forever and submit would keep skipping them.

### 3.3 `OCRFinalizeTask`

**Config keys read**: `gcs.project_id`, `gcs.pre_processing_log_path`;
`sharepoint.control_site.pre_processing_log_path`; `domain`; `framework.notifications`. That is
all — it is deliberately the thinnest task.

**Collaborators built in `pre_execute()`**: `_sp_control`, `_router`. No Gemini client, no
retriever — finalize **never talks to Vertex**.

**`execute_task()` walkthrough** ([finalize_task.py:61](finalize_task.py)):

1. `result = self.pre_result`.
2. **The isinstance guard**:
   ```python
   if not isinstance(result, OCRResult) or not result.file_statuses:
       logger.info("Nothing to finalize (no upstream OCRResult with file statuses)")
       return None
   ```
   This degrades to a **logged no-op**, not a crash. A business task that returns a bare
   `DataFrame` (or `None`) silently disables finalization. That is the safe direction — files stay
   in-flight and can be re-collected for free — but it is **silent**, so if your terminal statuses
   are never being stamped, this log line is the first place to look. **See the hazard box in §4.2 —
   this is the single most likely way to break the pipeline, and it does not raise.**
3. Build a `LogExporter` on the pre-processing-log bucket.
4. `build_terminal_log_rows(result.file_statuses, result.pre_processing_log, now_iso, file_messages=aggregate_file_messages(result.final_df))`.
   The pre-processing log comes **off the `OCRResult`** — no GCS re-read. One consistent snapshot
   per run.
5. If no rows (all already terminal, or all absent from the log), log and return the result
   unchanged.
6. Soft-validate, `save_log(..., sort_by="update_dt")`, return the result.

**Return value**: the upstream `OCRResult` unchanged (or `None` on the guard). Returning the result
keeps the chain composable if someone ever appends a task after finalize — though they should not.

---

## 4. The `pre_result` contract

This section is the one most likely to save you a production incident. The engine's threading
mechanism is four lines of code, and every one of them matters.

### 4.1 The engine mechanics, verified

**Task ordering is YAML key order — nothing else.**

```python
# src/core/engine.py:132
task_list = [k for k in self.config if k not in self.RESERVED_CONFIG_KEYS]
```

A plain iteration over the config dict. Python 3.7+ dicts preserve **insertion order**, and PyYAML
inserts in file order. There is no `tasks:` list, no `depends_on`, no dependency graph, no
topological sort. **The physical order of the top-level keys in the YAML file *is* the execution
order.** (`RESERVED_CONFIG_KEYS` is just `{"pipeline_name"}`.)

**The chain value is reassigned on every iteration.**

```python
# src/core/engine.py:165-170
if pre_result is not None:
    pre_result = task_instance.run(pre_result)
else:
    pre_result = task_instance.run()
```

Note there is no accumulation and no memory. Whatever a task returns **becomes** the chain value for
the next task. A task that returns `None` does not "pass the previous value along" — it **destroys
it**.

**A task's return value is `post_execute(execute_task())`.**

```python
# src/core/task_interface.py:152-159
result = self.execute_task()
result = self.post_execute(result)   # default: returns result unchanged
return result
```

So a task with no explicit `return` in `execute_task()` returns `None` — Python's implicit return —
and that `None` propagates all the way out through `run()`.

**A task only stores an incoming value when it is not `None`.**

```python
# src/core/task_interface.py:145-147
if pre_result is not None:
    self.pre_result = pre_result
```

`self.pre_result` is initialised to `None` in `__init__`. So once the chain value is `None`, every
downstream task sees `self.pre_result is None`. There is no recovery.

### 4.2 The rule

> **Any task wired between `OCRRetrieveTask` and `OCRFinalizeTask` MUST consume
> `OCRResult.final_df` and explicitly `return self.pre_result` — the `OCRResult` OBJECT, on every
> code path, including early returns.**

The tax-invoice `ReconcileTask` obeys this:
[tasks/tax_invoice_reconcile/reconcile_task.py](../tax_invoice_reconcile/reconcile_task.py) returns
`self.pre_result` at lines 145, 148, and 193 — its two early returns *and* its happy path.

### ⚠️ HAZARD — the silent chain-break

> **A mid-chain task that does not explicitly `return self.pre_result` returns `None`. The engine
> overwrites the chain with that `None`. `OCRFinalizeTask` then hits its isinstance guard, logs
> `Nothing to finalize (no upstream OCRResult with file statuses)`, and no-ops.**
>
> The consequences, in order:
>
> 1. **No terminal status is ever stamped.** The files stay `PENDING` / `PARTIAL` in the
>    pre-processing log.
> 2. **Gemini has already been paid** for those predictions.
> 3. Submit keeps skipping the files (they are in-flight). Retrieve keeps re-collecting them. The
>    business task keeps running. Finalize keeps no-op-ing. **The pipeline never converges.**
> 4. **Nothing crashes.** There is no exception, no non-zero exit, no alert. The only signal is that
>    one INFO log line and a pre-processing log whose `PENDING` rows never age out.
>
> The re-collection *is* the safety net (§1) — you lose no data and pay no extra Gemini cost while
> the bug is live. But the loop does not terminate on its own. Someone has to fix the return value.
>
> **Returning a bare `DataFrame` fails the same way** — `isinstance(result, OCRResult)` is `False`.

If your terminal statuses are not being written, grep the logs for `Nothing to finalize`. That line
is the fingerprint.

### 4.3 Corollary — a precondition task cannot sit mid-chain

A task that runs *before* the `OCRResult` exists — a dependency check, a precondition guard — has
**nothing to pass through**. It literally cannot satisfy the contract. Therefore it **must be the
first key**, ahead of `OCRRetrieveTask`.

The shipped tax-invoice config does exactly this. The real key order in
[ocr_pipeline_post_tasks.yml](../../config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml) is:

```
ReconcilePrecheckTask   (L25)   ← FIRST: cheap fail-fast guard, gets pre_result=None, returns None
OCRRetrieveTask         (L53)   ← creates the OCRResult
ReconcileTask           (L84)   ← consumes final_df, returns self.pre_result
OCRFinalizeTask         (L135)  ← MUST stay last
```

`ReconcilePrecheckTask.execute_task()` is annotated `-> None` and ends with a bare `return`
([tasks/tax_invoice_reconcile/precheck_task.py:88-102](../tax_invoice_reconcile/precheck_task.py)).
It halts the pipeline by **raising** `DependencyMissingError` when a master-data source is missing;
otherwise it returns `None`. That is **safe only because it runs first**, when the chain value is
already `None` and there is nothing to destroy.

### ⚠️ HAZARD — a landmine in the existing docs

> **Three places in this repo describe the post chain as
> `OCRRetrieveTask → ReconcilePrecheckTask → ReconcileTask → OCRFinalizeTask`. That order is wrong,
> and following it would break the pipeline.**
>
> - [README.md](README.md) L48 ("the post pipeline chains…")
> - the root `CLAUDE.md` OCR section
> - the header comment inside `ocr_pipeline_post_tasks.yml` itself (L3–15), which contradicts the
>   file's own key order below it
>
> The **shipped YAML key order** (§4.3 above) is the correct one and is what actually runs. If
> anyone "fixed" the YAML to match those diagrams — moving `ReconcilePrecheckTask` to sit *after*
> `OCRRetrieveTask` — the precheck's `return None` would land **mid-chain**, wipe the `OCRResult`,
> and silently disable finalize forever, per the hazard box in §4.2.
>
> Do not reorder the post-pipeline keys to match the diagrams. Fix the diagrams.

### 4.4 Why `OCRFinalizeTask` must be the last YAML key

Given §4.1, this is now mechanical: the last key is the last thing to run, and finalize must run
after the business logic has succeeded.

Move `OCRFinalizeTask` above a business task and you get the failure mode from §1 in reverse: files
stamped `SUCCESS` **before** the business task runs, so a business-task crash leaves them marked
complete with no downstream output produced — and the next submit run skips them, because they are
no longer in-flight. The data is silently dropped and the only recovery is re-submitting and
re-paying for the batch.

The YAML carries a `# MUST stay the last key` comment on that key. Respect it.

---

## 5. Status model

Three independent enums live in [helper/constant.py](helper/constant.py). They are **different
things at different granularities** and must not be conflated — though see the wart at the end of
this section.

### `JobStatus` — file-level, written to `pre_processing_log.csv`

| Value | Written by | Meaning |
|---|---|---|
| `INITIAL` | `PreLogRowBuilder._rows_for_file` | The file landed in GCS. Always the first row for a landed file. |
| `PENDING` | `PreLogRowBuilder._submitted_row` | **All** IQS-valid pages submitted to a batch job. In-flight. |
| `PARTIAL` | `PreLogRowBuilder._submitted_row` | Some pages submitted, some IQS-rejected. In-flight. |
| `REJECTED` | `PreLogRowBuilder` | Nothing submitted: **all** pages failed IQS, or the file type is outside `ext_filter`. The `message` column says which. Terminal. |
| `FAILED` | `PreLogRowBuilder` / `OCRFinalizeTask` | Technical error: landing upload failed, batch submit failed, or (post) no page succeeded. Terminal. |
| `SUCCESS` | `OCRFinalizeTask` | Post-processing complete, every page succeeded. Terminal. |
| `SUCCESS_WITH_FAILURE` | `OCRFinalizeTask` | Post-processing complete, but at least one page/line failed. Terminal. |

Only `PENDING` and `PARTIAL` are **in-flight**. That set is the dedupe key, the retrieve filter,
and the finalize guard — it appears as `_IN_FLIGHT_STATUSES` / `IN_FLIGHT_STATUSES` in three files.

### `QualityStatus` — per-page, written to `page_manifest_log.csv`

| Value | Meaning |
|---|---|
| `ACCEPTED` | Page passed the IQS gate. Uploaded to `gcs.processing_path`; `child_path` holds its `gs://` URI. |
| `REJECTED` | Page failed the IQS gate (or could not be scored at all). **Never sent to Gemini.** `child_path` is `""`. |

### `OCROutputStatus` — per-row, written to `final_df.STATUS`

| Value | Set where | Meaning |
|---|---|---|
| `SUCCESS` | `BatchResultRetriever._derive_status` | A line item was extracted. **No domain validation here** — a "normal" row is `SUCCESS`, and each consuming domain applies its own field/amount checks downstream. |
| `FAILED` | `_rows_for_line` (validation failure), `retrieve_failed` (dead job), `ResultFinalizer._rejected_stmt` (IQS-rejected page) | Something went wrong before or during extraction. |
| `SUSPICIOUS` | `_derive_status`, when `DOC_TYPE == "Suspicious"` | The page contains a prompt-injection / jailbreak attempt. `MESSAGE` carries the model's `SUSPICIOUS_REASON` plus the page number (appended later, see §6.5). |
| `UNSUPPORTED` | `_derive_status`, when `DOC_TYPE == "Other"` | Not a document type this prompt handles. |
| `BLANK` | `_derive_status`, when no line items | No line items on the page (e.g. a header-only or signature-only page). |

`Suspicious` and `Other` **take precedence over** `BLANK` — all three emit no line items, but the
first two are meaningful classifications and `BLANK` is the leftover.

### The page → file rollup

`rollup_status(page_statuses: set[str])` ([module/status_finalizer.py:43](module/status_finalizer.py)):

| Row statuses observed for one file | File-level `JobStatus` |
|---|---|
| `{SUCCESS}` (only) | `SUCCESS` |
| `SUCCESS` present, plus anything else | `SUCCESS_WITH_FAILURE` |
| no `SUCCESS` at all | `FAILED` |

Only `SUCCESS` counts as success. `BLANK`, `UNSUPPORTED`, `SUSPICIOUS`, and `FAILED` all count as
failure for the rollup.

Then `resolve_terminal_statuses` applies two corrections:

- **`_force_dead`** — an in-flight file whose batch job is in `dead_job_names` is forced to
  `FAILED`. This is necessary because a *fully-accepted* file on a dead job emits **no `final_df`
  rows at all** (there is no `predictions.jsonl` to read and no IQS-rejected page to union in), so
  the groupby above would never see it.
- **`_exclude_running`** — any file that still has a job in `running_job_names` is dropped from the
  status map. Never stamp a file while one of its jobs is still running. (A file can span multiple
  jobs when its pages land in different payload splits.)

### `STATUS_RANK` — and why it exists

```python
STATUS_RANK = {INITIAL: 0, PENDING: 1, PARTIAL: 1, REJECTED: 2, FAILED: 2, SUCCESS: 3, SUCCESS_WITH_FAILURE: 3}
```

The pre-processing log is **append-only** and `update_dt` is a wall-clock ISO timestamp from
`datetime.now(tz)`. For a single landed file, `PreLogRowBuilder` writes the `INITIAL` row and the
`PENDING` row **microseconds apart**. On Windows, `datetime.now()` has a resolution of roughly
15 ms — **both rows get the identical `update_dt` string**.

Sorting by `update_dt` alone therefore cannot order `INITIAL` before `PENDING`, and
`groupby(...).last()` would return whichever row pandas happened to see last. If it picked
`INITIAL`, the file would never be recognised as in-flight (`INITIAL` is not in
`_IN_FLIGHT_STATUSES`), so submit would re-upload and re-submit it on the next run — **paying
Gemini twice for the same document**.

`latest_status_per_file` ([helper/log_helper.py:13](helper/log_helper.py)) fixes this by sorting on
`["update_dt", "_rank"]` with `kind="stable"`, where `_rank` is the `STATUS_RANK` map (unknown
statuses map to `-1` via `.fillna(-1)`). Ties on `update_dt` break deterministically toward the
**furthest-progressed** status.

`REJECTED`/`FAILED` share rank 2 and `SUCCESS`/`SUCCESS_WITH_FAILURE` share rank 3 because they are
lifecycle-equivalent — both terminal at the same stage. The rank is a *lifecycle position*, not a
severity ordering.

### Known wart: `rollup_status` compares across enums

`rollup_status` reads `JobStatus.SUCCESS.value` and compares it against values that come from
`OCROutputStatus`. It works only because both enums spell it `"SUCCESS"`. Likewise
`aggregate_file_messages` filters on `OCROutputStatus.SUCCESS.value`. If a new domain ever
introduces a row status whose string collides with a `JobStatus` name, this will misbehave
quietly. It is not a bug today; it is a latent coupling worth knowing about.

---

## 6. Complex-logic deep dives

This section is the heart of the handover. Each subsection relocates reasoning that is (or was)
hard-won and easy to accidentally "fix" back into a bug.

### 6.1 The IQS quality gate

**IQS is an in-house heuristic. It is not a literature-backed IQA metric.** It was designed for
this pipeline. Do not describe it in a paper, a vendor deck, or a comparison table as if it were a
standard measure (BRISQUE, NIQE, etc.). It has no published validation, no reference dataset, and
no calibration against human judgement. It is a cheap, explainable, tunable pre-filter and nothing
more.

The formula, implemented in [src/utils/image_utils.py](../../src/utils/image_utils.py):

```
IQS = wV·VQ + wS·SQ + wC·CT
```

| Term | Function | What it actually measures |
|---|---|---|
| **VQ** — visual quality | `score_visual_quality` | Laplacian variance of the grayscale page, linearly ramped between `visual_quality.blur_min` (→ 0.0) and `visual_quality.blur_max` (→ 1.0). High variance = sharp edges = sharp scan. |
| **SQ** — structural quality | `score_structural_quality` | Skew. Otsu-threshold the page, take the min-area rectangle of the dark pixels, normalise its angle to `[0, 45]`, and ramp linearly to 0 at `structural_quality.max_skew_degrees`. **A blank page returns 1.0** — it has no skew. Emptiness is CT's job, not SQ's. |
| **CT** — content type | `score_content_type` | Foreground pixel density. `1.0` inside the band `[content_type.min_density, content_type.max_density]`; ramps to 0 below (too empty) and above (too noisy / all-black). |

`compute_iqs` **raises `ValueError` if the three weights do not sum to 1.0** (within `1e-6`), and
if any of `vq`/`sq`/`ct` is missing from the weights dict.

The shipped tuning
([config/tax_invoice_extraction/iqs_config.yml](../../config/tax_invoice_extraction/iqs_config.yml)):

```yaml
weights:      {vq: 0.40, sq: 0.30, ct: 0.30}   # must sum to 1.0
threshold:    0.60
sub_thresholds: {vq: 0.30, sq: null, ct: null} # null disables that sub-floor
visual_quality:     {blur_min: 50.0,  blur_max: 500.0}
structural_quality: {max_skew_degrees: 8.0}
content_type:       {min_density: 0.02, max_density: 0.60}
```

A page passes only if `IQS >= threshold` **AND** every non-null `sub_thresholds` floor is met. The
`vq: 0.30` sub-floor exists so a page that is catastrophically blurry cannot be rescued by a
perfect skew and a perfect density — a blurry page is unreadable regardless.

**Scoring is mandatory. There is no enable flag.** Every page is always scored
([module/document_processor.py:20](module/document_processor.py) docstring).

**What happens to a rejected page:**

1. It is written to `page_manifest_log.csv` with `quality_status = REJECTED` and `child_path = ""`.
2. **It is never uploaded to `gcs.processing_path`, so it never reaches Gemini.** No token is spent
   on it.
3. Its plain-language reject reason (`iqs_reject_reason`, [helper/messages.py:41](helper/messages.py))
   goes in the manifest `message` column. The reason names every sub-floor the page fell below,
   plus — when the weighted total is under `threshold` — the single weakest dimension as the main
   contributor. **It deliberately exposes no scores or formulas**; a business user reads
   "image is blurry or low-resolution", not "vq=0.21".
4. In the post pipeline, `ResultFinalizer._rejected_stmt` unions it back into `final_df` as a row
   with `STATUS = FAILED` and the manifest message. So a rejected page **surfaces as `FAILED`**
   downstream, not as a silent absence.
5. If *all* of a file's pages are rejected, the file gets a terminal `REJECTED` pre-log row and is
   never submitted at all.

PDF and image take different code paths in `DocumentProcessor`:

- **PDF** → `image_utils.score_pdf_pages`, which returns a **per-page** `passed` flag. Pages are
  gated individually; a 10-page PDF with one bad page submits 9 pages and lands `PARTIAL`.
- **Image** → `image_utils.score_image_bytes`, which returns the **aggregate** (min-page) result.
  For a one-page image the aggregate *is* the page. A rejected image is a whole-file reject.

### 6.2 `ReceiptExtraction` required-field backfill, `MoneyDecimal`, and the quantizers

Everything here is in [schema/model_response.py](schema/model_response.py).

**Why the required fields carry no default.** Pydantic only lists a field in
`model_json_schema()["required"]` if it has **no default**. That `required` array propagates into
Gemini's `responseSchema` (via `PayloadBuilder._build_response_schema`), and controlled generation
uses it to force the model to **emit the key** — as `null` if the value is genuinely absent, but
emit it. Give `VAT_AMOUNT` a `default=None` and it drops out of `required`, and the model starts
silently omitting it on pages where it is unsure.

That is why `_REQUIRED_FIELDS` (31 names, [model_response.py:20](schema/model_response.py)) — the
identity fields, all five receipt-level amounts, all eight party name/address fields, the invoice
fields, and every visual flag — are declared as `Field(description=...)` with **no `default=`**.

**Why `_backfill_required` then exists.** Forcing the schema is not the same as guaranteeing the
model complies. And **Vertex AI Batch has no per-record retry** — if one line's response fails
Pydantic validation, that page is `FAILED` and you have already paid for it. There is no cheap
"just ask again".

So `_backfill_required` is a `@model_validator(mode="before")` that does exactly one thing:

```python
for key in _REQUIRED_FIELDS:
    data.setdefault(key, None)
```

A field the model omitted degrades to `None` **for that field**, instead of raising a
`ValidationError` that would fail **the whole page** and lose 30 other correctly-extracted fields.
The schema tells the model "you must emit these"; the backfill says "and if you don't, we lose one
cell, not the page".

The same no-retry logic drives three sibling validators:

- `_coerce_doc_type` — any value outside the four `Literal` options becomes `"Other"` (→
  `UNSUPPORTED` downstream, i.e. surfaced to a human) rather than raising on the `Literal`.
- `_none_flag_to_false` — an explicit `null` visual flag becomes `False` rather than failing the
  non-optional `bool`.
- `_none_items_to_empty` — a `null` `line_items` becomes `[]`.

**`MoneyDecimal`.** Money parses to `Decimal` so that satang-precision sums stay exact:
`model_validate_json` reads the raw JSON number token, so `Decimal` gets the exact printed digits
rather than a float round-trip. But `Decimal` has an awkward JSON-schema projection — Pydantic emits
a `number`/`string`/`null` `anyOf` for it, and **an `anyOf` in the Vertex `responseSchema` would let
Gemini return amounts as strings**. `WithJsonSchema({"type": "number"})` pins the schema side to a
plain number while leaving the parse side as `Decimal`:

```python
MoneyDecimal = Annotated[Decimal, WithJsonSchema({"type": "number"})]
```

**`_quantize_money` / `_round_float`.** Gemini's decoder can produce a **digit-degeneration**
runaway: a value printed on the page as `32000.00` comes back as
`32000.00000000000000…040` with thousands of digits. The raw token parses to a legal but absurd
high-precision `Decimal`. `_quantize_money` salvages it with
`v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)`, so the correct value `32000.00` reaches
downstream instead of the degenerate one. `_round_float` does the same for `QUANTITY` and
`UNIT_PRICE` with plain `round(v, 2)`. The system prompt also forbids the behaviour (§6, "Numeric
precision") — belt and braces, because the prompt is a request and the validator is a guarantee.

### 6.3 The deliberately lenient gate — do not re-add strictness

Two things are **intentionally** absent from `ReceiptExtraction`:

1. **There is no "VAT is required" validator.** `VAT_AMOUNT` is `MoneyDecimal | None`. A page can
   come back with a null VAT and still be `SUCCESS`.
2. **`TAX_INVOICE_DATE` is nullable, and `_blank_date_to_none` maps `0000-01-01` (and `0000/…`, and
   the empty string) to `None`** before Pydantic's date parser sees it. Pydantic rejects year 0.

Both are **decisions, not oversights.**

The reasoning: a validation failure at this layer is not a "flag for review" — it is a
`ValidationError`, which makes the entire prediction line `FAILED`, which **discards every field on
that page**. A multi-page invoice's continuation page legitimately has no VAT footer. An `Other` or
`Suspicious` document legitimately has no date, and the model emits a `0000-01-01` placeholder for
it despite the prompt telling it not to. Making either of those strict converts a page with 30 good
fields and one missing one into a page with **zero** fields — and, because batch has no retry, that
data is simply gone until someone re-submits and re-pays.

The correct place for "this invoice has no VAT and that's suspicious" is the **consuming domain**
(the reconcile package's `RequiresReview` flag), where it costs a review flag rather than a page.

> If you find yourself adding `@field_validator` strictness to `ReceiptExtraction`, stop and ask
> whether the domain's own validator can carry it instead. The answer is almost always yes.

### 6.4 Strict never-sum on split VAT / exempt columns

Some Thai invoices split the pre-VAT base into two side-by-side subtotal columns —
`จำนวนเงิน / Amount (VAT)` and `จำนวนเงิน / Amount (Non VAT)` — and print **only a combined grand
total**, never a single combined pre-VAT subtotal.

The rule, enforced in the `BEFORE_VAT_AMOUNT` field description
([model_response.py:229](schema/model_response.py)) and in the system prompt (§6, "Totals box"):

> **`BEFORE_VAT_AMOUNT` is `null`. Never add the two columns together. Never sum the line items.**

This is not a rounding-safety concern; it is the pipeline's core principle applied to a hard case:
**transcribe, never fabricate.** The sum of the two columns is *not printed on the page*. Emitting
it would mean the OCR output contains a number no human can find on the source document — which
destroys the audit trail and, on the one document where the two columns don't actually compose that
way, produces a confidently wrong figure.

The accepted outcome is that `BEFORE_VAT_AMOUNT` is null, the line-level amounts are still captured
correctly on their own rows (each row fills exactly one of the two columns → that row's
`INVOICE_AMOUNT_BEFORE_VAT`), and the **downstream domain flags the document for human review**.
A review flag is the correct cost. Re-enabling summing to make the review queue shorter is trading
a visible inconvenience for an invisible error.

The prompt's self-consistency checks (§5 step 10) are explicit about this:
*"These identities are checks, never formulas to fill a blank."*

### 6.5 `ResultFinalizer` and its DuckDB join

[module/result_finalizer.py](module/result_finalizer.py). Pure transform, no I/O. Three
non-obvious things.

**(a) The `usage_metadata` dict-repr workaround.**

`usage_metadata` is a `dict` sitting in a pandas `object` column. DuckDB does **not** preserve
object-dict columns across a `SELECT rdf.*` — it rewrites each dict to its `str(dict)` repr, which
is Python-syntax (`{'a': 1}`), not JSON, and cannot be parsed back reliably. So:

```python
result_df["usage_metadata"] = result_df["usage_metadata"].map(lambda d: json.dumps(d) if isinstance(d, dict) else None)
# ... DuckDB passes it through as VARCHAR ...
df["USAGE_METADATA"] = df["USAGE_METADATA"].map(lambda s: json.loads(s) if isinstance(s, str) else None)
```

Serialize to JSON on a **copy** before registering, and parse back after the join, so the
`OCROutputSchema` `dict[str, Any]` coercion works. If you add another dict-valued column to the
prediction frame, you must repeat this dance.

**(b) The all-null column trap.**

An all-null column has no type information for either DuckDB or pandera to infer from, and both
will guess badly. In this package it bites on **`TAX_INVOICE_DATE`**:

- `OCROutputSchema.TAX_INVOICE_DATE` is declared `pa.typing.Series[object]`, **not** pandera's
  `date` Series type. The comment at [schema/ocr_output.py:27](schema/ocr_output.py) explains why:
  pandera's `date` Series type **rejects an all-null column**, which a zero-success run (every job
  dead, or every page IQS-rejected) produces every time.
- `_coerce_invoice_date` ([result_finalizer.py:137](module/result_finalizer.py)) therefore does the
  landing by hand: `pd.to_datetime(..., errors="coerce").dt.date`, then
  `.astype(object).where(pd.notna(parsed), None)` — real values become `datetime.date`, blanks stay
  `None`, and an all-null column stays a valid `object` column.
- `DATADATE` and `PAGE_NO` are declared `pd.Int64Dtype` (nullable Int64), **not** plain `int`, for
  the same reason: both arrive through a `LEFT JOIN` on the page manifest and must tolerate a
  no-match without crashing.

> **Discrepancy note for the reader:** the DuckDB `CAST` / `* REPLACE (...)` idiom for coping with
> an all-null column that DuckDB types as `INTEGER` (breaking a downstream `trim()`) lives in the
> **reconcile** package's SQL, not in this file. There is no `CAST` or `REPLACE` in
> `result_finalizer.py`. If you go looking for it here you will not find it — see
> `tasks/tax_invoice_reconcile/` and its `reconciliation.sql`.

**(c) `FILE_PATH` / `FILE_NAME` are always the SharePoint source path — never the GCS chunk URI.**

This is a hard contract. Business users, output reports, and reject folders all key on the path of
the **original document in SharePoint**. A `gs://` chunk URI is meaningless to them and points at a
transient, per-job artifact.

The join makes this happen ([result_finalizer.py:84](module/result_finalizer.py)):

```sql
WITH map_page_to_file AS (
    SELECT DISTINCT ppl.sharepoint_input_path AS FILE_PATH   -- <-- SharePoint, always
      , ppl.batch_inference_job_name AS BATCH_JOB_NAME
      , pml.parent_path  AS GCS_LANDING_PATH
      , pml.child_path   AS GCS_PROCESSING_PATH
      , pml.page_no, pml.iqs_score, ppl.datadate
    FROM pre_processing_log ppl
    LEFT JOIN page_manifest_log pml
      ON  ppl.job_id             = pml.job_id
      AND ppl.gcs_landing_path   = pml.parent_path
      AND ppl.batch_inference_job_name IS NOT NULL
)
SELECT rdf.*, mpf.FILE_PATH, SPLIT_PART(mpf.FILE_PATH, '/', -1) AS FILE_NAME, ...
FROM result_df rdf
LEFT JOIN map_page_to_file mpf
  ON  rdf.batch_inference_job_name = mpf.BATCH_JOB_NAME
  AND rdf.source_file_uri          = mpf.GCS_PROCESSING_PATH   -- chunk URI is the join key ONLY
```

The chunk URI (`source_file_uri` == manifest `child_path`) is used **as a join key and nothing
else**. `FILE_NAME` is derived by `SPLIT_PART(FILE_PATH, '/', -1)` — the basename of the SharePoint
path, not of the chunk.

(In the rejected-pages branch, `_rejected_stmt`, `FILE_NAME` is derived from
`SPLIT_PART(pml.parent_path, '/', -1)` — the basename of the GCS **landing** path, which by
construction is the original filename. Same document, same name.)

Both joins are `LEFT JOIN`s, which is why every file/page column in `OCROutputSchema` is nullable —
a manifest miss must degrade to a null cell, not an exception.

**(d) `_append_page_to_suspicious`.** The retriever sets a `SUSPICIOUS` row's `MESSAGE` to the
model's `SUSPICIOUS_REASON`, but at that point it has no idea what page number it is looking at
(the page number lives in the manifest). Once the join has attached `PAGE_NO`, this method appends
` (page N)` so the reason and the page travel together into the output report and the terminal
pre-processing-log message.

**(e) `connect_decimal_safe`.** [src/utils/duckdb_utils.py](../../src/utils/duckdb_utils.py) sets
`pandas_analyze_sample = 1_000_000_000`. DuckDB infers a `DECIMAL` column's width/scale for a pandas
`object` column of `Decimal` values from a **strided 1000-row sample**. If the largest-magnitude
value falls between stride points, the inferred type is too narrow (e.g. `DECIMAL(9,2)`) and the
full-column scan **overflows**. Sizing the sample beyond any realistic frame forces a full-column
scan.

### 6.6 Placeholder resolution order in `GcsRouter.resolve`

[module/gcs_router.py:48](module/gcs_router.py):

```python
def resolve(self, value: str, data_dt: datetime | None = None) -> str:
    with_job = (value or "").replace("${JOB_ID}", self._job_id)
    return resolve_date(resolve_env(with_job), data_dt or self._execution_dt)
```

The order is **`${JOB_ID}` → `${ENV_VAR}` → `%{DATA_DATE}`**, and it is **load-bearing**.

`resolve_env` ([src/utils/common.py:99](../../src/utils/common.py)) matches the pattern
`r"\$\{([A-Z0-9_]+)\}"` and substitutes `os.environ.get(var_name, "")` — **defaulting to the empty
string** for any name it does not find.

`JOB_ID` matches `[A-Z0-9_]+`. There is no `JOB_ID` environment variable (it is an engine *package*,
generated per run by `CoreEngine._generate_job_id`). So if `resolve_env` ran first, every
`${JOB_ID}` in every `gcs.*` path would be **silently replaced with an empty string** — and a path
like

```
gs://prod-bucket/ocr_tax_invoice_workflow/ocr_landing/202607/${JOB_ID}
```

would collapse to `.../ocr_landing/202607/`, so every run of every day would write into the same
directory, overwriting each other's landing copies and payloads. No error, no warning. Just silent
cross-run collision.

Hence the literal `.replace("${JOB_ID}", self._job_id)` **first**, before `resolve_env` gets a
chance to blank it. If you ever add a third non-env `${...}` placeholder, it must go in front of
`resolve_env` too.

`%{DATA_DATE...}` runs last and uses `data_dt` when given (the per-date pass in
`_resolve_src_paths`), else `execution_dt`.

### 6.7 Per-bucket GCS routing

Each `gcs.*` path in the config **may name a different bucket** — they only have to share
`gcs.project_id`. The tax-invoice config happens to point them all at
`${ENVIRONMENT}-${TAX_INVOICE_PROCESSING_BUCKET}`, but nothing requires that, and a domain that
wants landing in one bucket and predictions in another can just say so in YAML.

`GcsRouter` handles this with a **cache keyed on bucket name**:

```python
def module_for_bucket(self, bucket: str) -> GCSModule:
    if bucket not in self._by_bucket:
        self._by_bucket[bucket] = self._gcs_factory({"project_id": self._project_id, "bucket_name": bucket})
    return self._by_bucket[bucket]
```

`module_for(key)` resolves the config path, extracts its bucket with `extract_bucket`, and delegates
here. `prefix_for(key)` returns the bucket-relative prefix (stripping *that path's own*
`gs://bucket/`, not some globally-configured bucket).

**`GCSModule` deliberately stays single-bucket.** It is a framework class (`src/modules/google/gcs.py`)
used by the telesale and QA pipelines too; making it multi-bucket would complicate every caller to
serve one. Routing is a pipeline concern, so it lives in the pipeline. Do not "fix" `GCSModule` to
take a bucket per call.

`BatchResultRetriever` gets `router.module_for_bucket` injected directly, because a Vertex job's
`dest` bucket is discovered at *retrieve* time from `job.dest` — it is not in the config at all.

### 6.8 Append-only log writes (`LogExporter`) and the concurrency hazard

[module/log_exporter.py](module/log_exporter.py). Both `pre_processing_log.csv` and
`page_manifest_log.csv` are **append-only within the retention window** (§6.8.1): existing rows are
never modified; every run contributes new rows. There is no "update the row's status" — a status
change is a *new row*, which is exactly why `latest_status_per_file` and `STATUS_RANK` exist.

The write is a **generation-guarded read-merge-write with one retry**
(`_append_with_retry`, [log_exporter.py:100](module/log_exporter.py)):

1. `download_bytes_with_generation(gcs_path)` → `(bytes, generation)`.
2. Merge the new rows onto the existing frame (`pd.concat`), optionally sort by `update_dt`
   descending (stable, latest-first — this is presentation only; append-only semantics are
   unaffected). Then **always** prune the merged frame (§6.8.1).
3. `update_content_to_gcs(..., if_generation_match=precondition)` where
   `precondition = generation if generation is not None else 0`. **`0` means "create only"** — so a
   concurrent *create* also trips the precondition rather than silently clobbering.
4. On `PreconditionFailed` (someone wrote between our read and our write): reload, re-merge, retry
   **once**. `_MAX_WRITE_ATTEMPTS = 2`.
5. Lose the race twice → log at ERROR and **re-raise**. We do not silently drop rows.

Then the CSV bytes are mirrored to SharePoint via `upload_file`. **The SharePoint mirror is
best-effort** — a failure logs a WARNING and does not raise. GCS is the source of truth; SharePoint
is the human-readable copy.

### 6.8.1 Retention (`log_retention`) — one knob, two log shapes

[helper/log_retention.py](helper/log_retention.py). Left alone, these CSVs grow forever, and every run
re-downloads, re-sorts, and rewrites the whole history — so the write-race window in §6.8 widens
daily. **Every** tax-invoice log is now bounded by a single env var,
`TAX_INVOICE_LOG_RETENTION_DAYS`, surfaced as `framework.log_retention_days` in all four configs:

| Value | Effect |
|---|---|
| `90` (or any ≥ 0) | Rows / month-files older than that many days are pruned. |
| `-1` (any negative) | **Retention disabled.** `retention_cutoff` returns `None` and every prune is a no-op. |
| unset / garbage | Falls back to `DEFAULT_RETENTION_DAYS = 90` with a WARNING. |

That fallback is deliberate and load-bearing: `resolve_env` turns an *unset* env var into `""`, and
`int("")` raises. `log_retention_days` is therefore **not** in any task's `int_castable_errors` list —
a missing retention secret must never halt a pipeline run.

Retention is **intrinsic, not optional**. `LogExporter` takes the window in its constructor and prunes
the merged frame in `_append_with_retry`, inside the generation precondition and re-applied on the
retry attempt — so it costs zero extra I/O and can never race a concurrent writer. There is no
`prune=None` hook to forget.

**Rows age out purely by `update_dt`, regardless of status.** An in-flight (`PENDING`/`PARTIAL`) row
old enough to cross the window is a *stuck* file — one of the P1-3 stranding paths from the 2026-07
repo review — and pruning it is the deliberate backstop: once the row is gone, `filter_new` (§6.9)
stops skipping the file, so if it still sits in the input folder the next submit run re-processes it,
at fresh Gemini cost. The corollary: **keep the window well above batch-job wall time (days)** — a
very short window can prune a *still-running* job's rows, orphaning its predictions and
double-submitting its file.

Aged **terminal** rows are inert, and this is the part worth internalising before touching this code:
a `SUCCESS` row does **not** suppress a re-submit. `filter_new` only excludes in-flight paths (§6.9).
What actually keeps a completed document from being picked up again is that it **leaves the input
folder** — `SourceArchiver` copy-then-deletes the original, and `IqsRejecter` does the same for
rejected files. So the log is a concurrent-run guard, not a billing guard.

#### Cumulative files — prune rows

`ocr_pre_processing_log.csv` ages out on `update_dt` (`prune_by_timestamp`). The page manifest has
**no timestamp column at all** (`PageManifestLogSchema`), so its age is derived from the
pre-processing log via the shared `job_id`:

- `expired_job_ids(pre_log, cutoff)` → job ids whose *every* pre-log row is prunable.
- `prune_manifest(df, expired_ids)` drops only those jobs' pages. **A `job_id` absent from the
  pre-processing log is kept** — that fail-safe protects a concurrent run's freshly written manifest
  rows from a run whose pre-log snapshot predates them.

#### Month-partitioned files — delete expired month-files

`transaction_log_YYYYMM.csv` + `performance_log_YYYYMM.csv` (`ExportLogging`, in the reconcile package)
and `tracing_log_YYYYMM.csv` (`TracingLogExporter`) are one file per month. Row-pruning them is nearly
a no-op — each file only ever holds one month — so the real work is the shared `sweep_month_files`
(both exporters call it): it deletes the month-files lying **entirely** before the cutoff's month (the
current month's file is never deleted). The deletion is best-effort: a SharePoint failure logs a
WARNING and never fails a run whose logs were already written. Those logs also get a row-prune on
`load_dt`, which only bites when the window is shorter than a month (e.g. `15`).

Two further rules:

- The cutoff is anchored to *now in the configured timezone* (`framework.timezone` in
  `config/common.yml` → `OCRTaskContext.timezone`), never a hardcoded UTC. `retention_cutoff` falls
  back to `DEFAULT_TIMEZONE = "Asia/Bangkok"` when no tz is passed, so the module is usable and
  testable without a config.
- Rows with an **unparseable timestamp are kept**, not purged. (`NaT >= cutoff` is `False`, so a
  naive filter silently drops them — the bug logged as P3-13 against the QA batch log.) `ExportLogging`
  writes `load_dt` naive, which is parsed as UTC: that errs toward keeping a row up to the tz offset
  longer, never toward pruning it early.

#### What retention writes to Cloud Logging

Every run states what retention did — **including when it did nothing** — at INFO, so the live window
and per-run prune counts are answerable from Cloud Logging alone (filter on `"retention"`):

- `log retention active: window=90 day(s), cutoff=2026-04-16T09:12:33+07:00 (tz=Asia/Bangkok)` — one
  per exporter construction (`retention_cutoff`), each pipeline stage confirming its resolved window.
  The disabled case logs `log retention disabled (log_retention_days=-1)` instead.
- `<label> retention: pruned N of T row(s); K kept (cutoff=…)` — every row-prune, zero included
  (pre-processing / transaction / performance logs). The page manifest's variant is
  `page-manifest retention: pruned N of T row(s) across J fully-expired job(s)`.
- `<label> retention sweep: deleted D of C month-file(s) in <folder>` — the month-file sweep, where
  `C` counts only `<prefix>_YYYYMM.csv` candidates (plus one line naming each deleted file).

A submit run with no new files writes nothing, and therefore prunes nothing — fine, since submit runs
daily and `OCRFinalizeTask` prunes on the post pipeline too.

CSV encoding is `utf-8-sig` (BOM) so Excel opens Thai text correctly.

> **The hazard.** `OCRSubmitTask` (pre pipeline) and `OCRRetrieveTask` + `OCRFinalizeTask` (post
> pipeline) all write **the same `pre_processing_log.csv`**. The generation guard means a collision
> is *detected*, and one retry usually resolves it — but with only two attempts, a sustained
> overlap can still raise. **Do not schedule the pre and post pipelines to run concurrently against
> the same log.** The tax-invoice deployment separates them (Cloud Scheduler for pre, Eventarc for
> post). Both YAML files carry a comment saying so.

### 6.9 The tracing log (`TracingLogExporter`)

[module/tracing_exporter.py](module/tracing_exporter.py) +
[module/tracing_builder.py](module/tracing_builder.py). This is the raw-Gemini audit trail, and it
behaves **differently from every other log in the package**:

| Property | Pre-processing / page-manifest log | Tracing log |
|---|---|---|
| Source of truth | GCS, mirrored to SharePoint | **SharePoint only** — never written to GCS |
| Partitioning | one file, forever | **one CSV per month**, `tracing_log_YYYYMM.csv` |
| Retention | none | **3 months** (`_DEFAULT_RETENTION_MONTHS`), pruned on write |
| Concurrency guard | generation-guarded, one retry | none (single writer: retrieve only) |

The month comes from the `%{DATA_DATE_YYYYMM}` placeholder in
`sharepoint.control_site.tracing_log_path`. On every write, `_prune_month_files` parses the current
file's month, lists the sibling `tracing_log_YYYYMM.csv` files in the same folder, and **deletes any
whose month is more than `retention_months` older**. The sweep is best-effort — a failure logs a
WARNING. If `sp_path` is not month-partitioned (a static filename), the sweep is skipped entirely.

**What is stored, and what is trimmed** (`TracingLogBuilder.line_to_record`):

- **Request: trimmed.** `_trim_request` recursively strips `system_instruction` and
  `response_schema` **wherever they nest**. Both are byte-for-byte identical on every single row and
  together dominate the row size (the system prompt is ~360 lines; the response schema is the full
  `ReceiptExtraction` JSON schema). What survives is the file URI and the generation params — the
  parts that actually vary per row.
- **Response: verbatim.** The whole `line["response"]` is `json.dumps`'d unchanged and stored as a
  string. It is **never validated field-by-field**. This is the point: if a model-output change
  breaks `ReceiptExtraction`, the tracing log still has the raw response so you can see *what*
  changed. A tracing log that validated its payload would break at exactly the moment you need it.
- `page_no` is parsed back out of the page URI with `_p(\d+)\.` (matching
  `DocumentProcessor`'s `{stem}_p{page_no:03d}.pdf` naming) and stored as a **leading-zero-stripped
  string** to match the all-string CSV.
- For a terminally-failed job, `line` is `{}` and the per-line fields come back `None`; the row
  still exists, carrying `job.error.message`.
- `load_dt` is stamped **once per run** in `build_tracing_log`, so every row in a run shares one
  timestamp.

### 6.10 Thai text handling

**`normalize_thai_text`** ([helper/thai_text.py](helper/thai_text.py)) is wired in as a
`@field_validator("*", mode="before")` on **both** `ReceiptExtraction` and `InvoiceLineItem` (nested
models do **not** inherit the parent's validator — hence the duplication).

What it fixes: digital Thai PDFs encode **positional glyph variants** of combining marks — tone
marks shifted for tall consonants, left-shifted upper vowels, descenderless ฐ/ญ — in the Private Use
Area **U+F700–U+F71A** (the Microsoft/Adobe Thai font convention). When the model reads a page's
*text layer* instead of the rendered glyphs, those codepoints leak straight into the output. They
render as **boxes** in the report and they wreck the reconcile name matching, because
`บริษัท` with a PUA tone mark is not string-equal to `บริษัท` with the standard one.

Every PUA glyph in that range has an exact standard-Thai equivalent, so the mapping is **lossless**.
The function then:

1. Translates U+F700–U+F71A → standard codepoints.
2. Drops any **remaining** private-use codepoint (`0xE000`–`0xF8FF`) — an unmapped PUA glyph has no
   text meaning and renders as a box.
3. Recomposes the decomposed sara-am `ํ` + `า` (U+0E4D U+0E32) → `ำ` (U+0E33). This runs **after**
   step 1 so a PUA nikhahit recomposes too.
4. Collapses each run of control characters (`\n`/`\r`/`\t`) — together with any spaces touching it
   — into a **single space**, and trims the ends. **Interior printed spacing stays verbatim.**

Non-string input (numbers, `None`, lists) passes through untouched.

#### The comma-fidelity rule — do NOT add a comma-stripping validator

Thai addresses in the output frequently contain commas. This looks like model hallucination. **It is
not.** It was traced back to the source PDFs: those commas are **printed on the document**.

The prompt locks this down hard (§6, "Party name & address", tier 1):

> *"transcribe it exactly — character for character, **including its punctuation and spacing**. Copy
> every printed comma as printed, and **never insert a comma (or other separator) that is not
> printed**; do not restyle the address (e.g. do not comma-separate a space-separated Thai address,
> and do not strip commas the document does print)."*

And tier 3 (translated, not printed) has the opposite rule for Thai:

> *"Separate the remaining parts with **single spaces, never commas** — even though the English
> source is comma-separated, a *translated* Thai address uses spaces."*

So: **printed → verbatim, commas and all. Translated-to-Thai → space-separated.** The two rules are
different on purpose.

If you add a validator that strips commas from `*_ADDRESS_TH`, you will be **corrupting a faithful
transcription** to make it look like the translated form, and the resulting value will no longer
match the source document during an audit. Do not do it. If a downstream matcher chokes on commas,
normalise **in the matcher**, not in the transcription.

### 6.11 Dedupe and idempotency

The whole scheme rests on one predicate: **is this file's latest pre-processing-log status
`PENDING` or `PARTIAL`?**

| Task | Behaviour on re-run |
|---|---|
| **Submit** | `_in_flight()` collects the in-flight paths; `SourceFileLoader.filter_new` drops them. Everything else — `SUCCESS`, `SUCCESS_WITH_FAILURE`, `FAILED`, `REJECTED`, and files never seen — is **eligible for re-processing**. Re-running submit on a day whose files all succeeded will **re-submit and re-bill them**. That is intentional (it is how you replay a fixed prompt) but it is a footgun; use the date-window flags deliberately. |
| **Retrieve** | Returns `None` when nothing is in-flight, or when every in-flight job is still running. Otherwise polls each job **once** and collects. Re-running retrieve before finalize has stamped anything is free and safe — the predictions are just re-read from GCS. |
| **Finalize** | `build_terminal_log_rows` stamps **only** files whose current latest status is still `PENDING`/`PARTIAL`. Files already terminal are skipped silently; a file absent from the log is skipped with a WARNING. Running finalize twice is a no-op. |

The dead-job case closes the loop: a `FAILED`/`CANCELLED`/`EXPIRED` job produces no
`predictions.jsonl` at all, so its fully-accepted files would emit **zero** `final_df` rows.
`_force_dead` catches them and stamps `FAILED`, so they leave the in-flight set and become eligible
for a fresh submit. **Without it, those files would be `PENDING` forever.**

---

## 7. Adopting the pipeline for a new domain

This is the key handover section. Read it before you decide to fork.

### 7.1 What is genuinely YAML-only

The following can all be changed without touching Python:

| Config key | Purpose |
|---|---|
| `pipeline_name` (top level) | Job-id prefix; also `PayloadBuilder`'s JSONL filename prefix. Skipped by the engine (`RESERVED_CONFIG_KEYS`). |
| `domain` | Free-form string stamped into `domain_name` on every pre-processing-log and tracing-log row. Nothing branches on it. |
| `gcp.project_id` | Used to construct the `GeminiBatchModule`. |
| `gcs.project_id` | The GCP project every bucket lives in. |
| `gcs.landing_path` | Immutable copies of the source documents. **Must start with `gs://`** (`_shape_errors`). |
| `gcs.processing_path` | IQS-accepted page chunks. |
| `gcs.payload_landing_path` | Generated JSONL batch payloads. |
| `gcs.output_path` | Where Vertex writes `predictions.jsonl` (one subdirectory per payload). |
| `gcs.pre_processing_log_path` / `gcs.page_manifest_log_path` | The two append-only CSVs (source of truth). |
| `sharepoint.source_site.*` | `site_name`, `site_domain`, `site_path`, `client_id`, `client_secret`, `tenant_id`, `src_path`. |
| `sharepoint.control_site.pre_processing_log_path` / `.page_manifest_log_path` / `.tracing_log_path` | Where the logs are mirrored. **Note**: only the *paths* come from here — the control-site **credentials** come from `config/common.yml`, see below. |
| `sharepoint.control_site.system_prompt_path` | Control-site path of the system prompt markdown, downloaded at submit time (`_load_system_prompt`). Missing → `FileNotFoundError`; blank → `ValueError` — both fail the run before any batch is submitted. **Point this at your own file.** |
| `vertexai.project_id` / `.location` / `.model` | Gemini target. |
| `vertexai.generation_config` | Forwarded **verbatim** into every JSONL request line's `generation_config` (alongside the pipeline's own `response_mime_type` + `response_schema`). Must be a dict. |
| `framework.iqs_config_path` | Path to your IQS tuning YAML. |
| `framework.ext_filter` | Non-empty list of lowercase extensions. Default `('.pdf', '.jpg', '.jpeg', '.png')`. Anything outside it is logged `REJECTED` and never uploaded. |
| `framework.concurrency_upload` | Max concurrent SharePoint→GCS uploads. Required; must be int-castable after env resolution. |
| `framework.batch_job_limit` | JSONL lines per payload file. Default `100_000`. |
| `framework.batch_status_check_delay_seconds` | Seconds to sleep before the post-submit status check. Default `2`. |
| `framework.notifications.system_exception` | Optional `on_error` email: `enabled`, `sender_email`, `receiver_email`, `cc_email`, `subject`, and either `body` (inline) or `body_path` (a `.txt` file — wins over `body`). Subject/body support `{task}`, `{pipeline}`, `{date}`, `{error}`. |

Placeholders available in **any** of these values: `${JOB_ID}`, `${ENV_VAR}`, `%{DATA_DATE[±N][_FMT]}`
(resolved in that order — §6.6). Env-var placeholders are resolved by `os.environ`, so the
**prefix convention is yours to choose**: tax invoice uses `TAX_INVOICE_*`, telesale uses
`TELESALE_*`, QA uses `QA_*`. Nothing in the package reads an env var by name; it only expands
whatever the YAML asks for.

### 7.2 The `config/common.yml` dependencies

`OCRTaskContext.from_task` ([helper/task_context.py:44](helper/task_context.py)) reads
`config/common.yml` directly (the path is hardcoded as `_COMMON_CONFIG_PATH`) and pulls three
things out of it:

1. **`framework.timezone`** — **raises `ValueError(f"Timezone not set in {_COMMON_CONFIG_PATH}")`
   if absent.** This is the only hard failure in the context builder. Every timestamp in the package
   (`load_dt`, `update_dt`, the tracing `load_dt`, the prediction `start_time`/`end_time`
   re-normalisation) goes through it. Currently `"Asia/Bangkok"`.
2. **The `control:` block** — this is the **source of the control-site SharePoint credentials**
   (`ctx.control_site_access`). Your task YAML supplies control-site *paths*; `common.yml` supplies
   the *credentials*. If your new domain's logs live on a different SharePoint site than
   `CONTROL_SITE_*`, this is a real constraint — `init_sharepoint("Control", ctx.control_site_access)`
   has no other source.
3. **The `msgraph:` block** — Microsoft Graph credentials + default `sender_email` /
   `receiver_email` / `cc_email` for the `on_error` system-error email. Per-task
   `framework.notifications.system_exception` values override these; anything omitted falls back
   here.

The **source-site** credentials, by contrast, come entirely from your task YAML
(`sharepoint.source_site.*`).

### 7.3 The honest friction point — where "zero code changes" stops

The README and `__init__.py` both say adopting a new domain is **"Zero code changes."** That is
true **only if your domain extracts the same fields as a Thai tax invoice.** If it does not, it is
false, and here is exactly why.

`ReceiptExtraction` is **hard-imported** in two modules, and the two are not equally injectable:

**(a) `module/result_retriever.py` — has a seam, but it is not wired up.**

```python
# result_retriever.py:30
from tasks.ocr_tax_invoice_pipeline.schema.model_response import InvoiceLineItem, ReceiptExtraction

class BatchResultRetriever:
    def __init__(self, gemini_batch, gcs_factory, tracing_builder,
                 response_schema: type[BaseModel] = ReceiptExtraction,   # <-- injectable
                 prediction_filename: str = DEFAULT_PREDICTION_FILE):
```

The `response_schema=` parameter exists and is honoured (`self._schema.model_validate_json(text)` at
[result_retriever.py:256](module/result_retriever.py)). **But `OCRRetrieveTask` never passes it** —
[retrieve_task.py:86](retrieve_task.py) constructs it with only three positional args, so the default
`ReceiptExtraction` always wins.

And even with the seam used, `InvoiceLineItem` is **still hard-imported**:
`self._item_fields = list(InvoiceLineItem.model_fields)` ([result_retriever.py:85](module/result_retriever.py))
and the `line_items` field name is hardcoded in `self._doc_fields` (`if name != "line_items"`). So
the injection seam only supports a document/line-items shape with those exact names.

**(b) `module/payload_builder.py` — no seam at all.**

```python
# payload_builder.py:134
@staticmethod
def _build_response_schema() -> dict:
    """Derive the Vertex AI response schema from :class:`ReceiptExtraction`."""
    raw = ReceiptExtraction.model_json_schema()
    resolved = pydantic_resolve_refs(raw)
    return sanitize_json_schema_enums(resolved)
```

It is a `@staticmethod` referencing the class **directly**. `PayloadBuilder.__init__` takes no
schema argument. There is **no way** to give Vertex a different `responseSchema` without editing
this file.

**(c) `module/result_finalizer.py` — hard-imported and tax-invoice-shaped SQL.**

- `from ...schema.ocr_output import OCROutputSchema` (line 12), used as the frame contract:
  `df = df[list(OCROutputSchema.to_schema().columns.keys())]` and `OCROutputSchema.validate(df)`.
- `_coerce_invoice_date` operates on a column literally named **`TAX_INVOICE_DATE`**. It will
  `KeyError` on a frame that does not have one.
- The join SQL itself (`_predicted_stmt`, `_rejected_stmt`) is domain-**agnostic** — it does
  `SELECT rdf.*` and only adds the file/page context columns. That part is reusable. The
  column-narrowing and validation against `OCROutputSchema` is not.

**(d) `helper/messages.py`** — `STATUS_MESSAGES` and `iqs_reject_reason` are domain-agnostic. Fine
as-is.

**Exactly which files a new domain with different extraction fields would touch:**

| File | Line(s) | What is coupled | What you would have to do |
|---|---|---|---|
| [schema/model_response.py](schema/model_response.py) | whole file | `ReceiptExtraction`, `InvoiceLineItem`, `_REQUIRED_FIELDS` | Write your own Pydantic response model. |
| [schema/ocr_output.py](schema/ocr_output.py) | whole file | `OCROutputSchema` — 48 tax-invoice columns | Write your own pandera frame model. |
| [module/payload_builder.py](module/payload_builder.py) | 11 (import), 52, 134–139 (`_build_response_schema`) | Hard reference to `ReceiptExtraction`; **no injection param** | Add a `response_schema: type[BaseModel]` ctor arg and make `_build_response_schema` an instance method; then pass it from `OCRSubmitTask._build_batch_submitter`. |
| [module/result_retriever.py](module/result_retriever.py) | 30 (import), 63 (default), 84–85 (`_doc_fields` / `_item_fields`) | Seam exists for the document model; `InvoiceLineItem` and the literal `"line_items"` are still hardcoded | Pass `response_schema=` from `OCRRetrieveTask`; parameterise the line-item model + the nested-list field name. |
| [module/result_finalizer.py](module/result_finalizer.py) | 12 (import), 57, 60 (`OCROutputSchema`), 137–146 (`_coerce_invoice_date`) | Hard reference to `OCROutputSchema`; `TAX_INVOICE_DATE` by name | Inject the output schema; make the date coercion configurable or move it to the domain. |
| [retrieve_task.py](retrieve_task.py) | 86 | Constructs `BatchResultRetriever` without `response_schema=` | Pass the domain's model through. |
| [submit_task.py](submit_task.py) | 155–161 | Constructs `PayloadBuilder` without a schema arg | Pass the domain's model through (after adding the seam above). |
| [prompt/system_prompt.md](prompt/system_prompt.md) | — | Tax-invoice-specific | Ship your own: upload it to your control SharePoint site and point `sharepoint.control_site.system_prompt_path` at it (this part **is** YAML-only). |

**This guide documents the seam; it does not add it.** Adding an injection seam is a real change to
a shipped, tested package and is a decision for the owners, not a documentation task.

So the honest summary for the reuse audience:

> **If your domain extracts tax-invoice/receipt fields** (or a subset), adoption really is YAML-only.
>
> **If your domain extracts different fields**, you have two options:
> 1. **Add the injection seam** (2 files to change structurally, 2 more to wire) and keep one shared
>    package.
> 2. **Fork the package** under a new name and swap the four schema-coupled files.
>
> Option 1 is the right long-term answer. Option 2 is faster and does not risk regressing the
> tax-invoice pipeline. Talk to the owners before choosing.
>
> Everything *else* — ingestion, IQS gating, per-page rastering, payload splitting, batch submit and
> poll, prediction retrieval, the DuckDB source-attribution join, the status model, the append-only
> logs, the tracing log, the dedupe/idempotency scheme, and the finalize-last cost guarantee — is
> genuinely domain-agnostic and needs no changes at all.

### 7.4 New-domain checklist

1. **Decide**: same extraction fields as tax invoice, or different? (§7.3.) If different, decide
   fork vs. seam **first**.
2. **Env vars**: pick a prefix (`MYDOMAIN_*`) and define the GCP project, processing bucket, Vertex
   model + location, source-site SharePoint credentials, source/control paths, and
   `MYDOMAIN_MAX_CONCURRENT_UPLOADS`. Add them to `.env.example`.
3. **`config/common.yml`**: confirm `framework.timezone` is set (it is), and that the `control:`
   block points at the SharePoint site where you want your logs. If it does not, that is a blocker —
   raise it before writing YAML.
4. **Write the system prompt**, upload it to your control SharePoint site, and point
   `sharepoint.control_site.system_prompt_path` at it — the submit task downloads it at run
   time (missing or blank fails the run). Keep the reviewed master copy in the repo. Read the
   shipped [prompt/system_prompt.md](prompt/system_prompt.md) first — its structure
   (role / objective / input / context / steps / rules / output-format-with-examples) is worth
   copying even when the content is not.
5. **Tune IQS**: copy `iqs_config.yml`, adjust the weights (must sum to 1.0), `threshold`, and the
   `sub_thresholds`. **Validate on real rejects** — run submit on a sample and read the
   `page_manifest_log.csv` `iqs_score` / `message` columns before trusting the gate in production.
6. **Write `<domain>_pre_tasks.yml`** with `OCRSubmitTask` (+ any domain pre-tasks).
7. **Write `<domain>_post_tasks.yml`** with `OCRRetrieveTask` → your business task(s) →
   `OCRFinalizeTask`. **`OCRFinalizeTask` last.**
8. **Write your business task** subclassing `TaskInterface`, registered via
   `@task_registry.register(...)`, imported in `tasks/<pkg>/__init__.py`. It **must** consume
   `pre_result.final_df` and **return `self.pre_result`** (the `OCRResult`) on every path,
   including early returns. (§4.)
9. **Dry-run submit** on a tiny folder. Check: files appear in `gcs.landing_path`; chunks in
   `gcs.processing_path`; a JSONL in `gcs.payload_landing_path`; `INITIAL` + `PENDING` rows in the
   pre-processing log; one manifest row per page.
10. **Dry-run post** once the job succeeds. Check: `final_df` has `FILE_PATH` = the SharePoint path;
    terminal statuses stamped **after** your business task ran.
11. **Break your business task on purpose** and confirm the files stay `PENDING` and the next post
    run re-collects them for free. That is the whole design; verify it works for you.

---

## 8. Reference

### 8.1 `OCRResult` / `ChunkEntry` / `BatchSubmission`

All three in [schema/contracts.py](schema/contracts.py), all `@dataclass(frozen=True)`.
**`frozen=True` is shallow** — the DataFrames and dicts inside are still mutable. Treat them as
read-only by convention; nothing enforces it.

**`OCRResult`** — the typed hand-off from retrieve → business task(s) → finalize.

| Field | Type | Contract |
|---|---|---|
| `final_df` | `pd.DataFrame` | `OCROutputSchema`-validated. One row per page/line item. **May be EMPTY** when every in-flight job died without predictions — business tasks must handle an empty frame. |
| `file_statuses` | `dict[str, str]` | `{sharepoint_input_path: terminal JobStatus value}`. Keys are **SharePoint source paths from the pre-processing log — never GCS URIs.** Computed once in retrieve (dead jobs are already baked in as FAILED by `_force_dead`), so finalize never re-polls Vertex. Default `{}`. |
| `pre_processing_log` | `pd.DataFrame` | Append-only log snapshot, loaded **once** in retrieve. Business tasks and finalize read it from here instead of re-reading GCS — one consistent snapshot per run. Default empty frame. |
| `page_manifest_log` | `pd.DataFrame` | Per-page manifest snapshot, loaded once in retrieve. Carries each page's immutable GCS `child_path`, so a business task can copy the exact processed page (e.g. a `SUSPICIOUS` page) to a reject folder rather than re-deriving it. Default empty frame. |

**`ChunkEntry`** — one accepted page chunk (submit-side only).

| Field | Type | Meaning |
|---|---|---|
| `parent_landing_path` | `str` | `gs://` landing URI of the **source document** (not the page). This is what links a chunk back to its file for `BatchSubmission.parent_paths`. |
| `gcs_uri` | `str` | `gs://` URI of the per-page chunk in `gcs.processing_path`. |

**`BatchSubmission`** — the outcome of one Vertex submission attempt (submit-side only).

| Field | Type | Meaning |
|---|---|---|
| `payload_name` | `str` | `{pipeline}_{YYYYMMDDHHMMSS}_{seq:03d}.jsonl`. |
| `payload_uri` | `str` | Full `gs://` URI of the uploaded JSONL. |
| `output_uri` | `str` | `gs://{output_bucket}/{output_prefix}/{payload_stem}` — the `.jsonl` suffix is **stripped** so predictions land in a per-batch *directory*. |
| `parent_paths` | `frozenset[str]` | Landing URIs of every file whose pages are in this payload. `PreLogRowBuilder` uses this to find which submission a file belongs to. |
| `job` | `Any \| None` | The Vertex `BatchJob`. **`None` when submission failed.** |
| `error` | `str \| None` | The exception string when submission failed; `None` on success. A failed submission is **captured, not raised** — one bad payload must not kill the others. |

### 8.2 Environment-variable inventory (tax-invoice adoption)

Names referenced by the two configs; the values come from `.env` / Secret Manager. A new domain
substitutes its own prefix.

| Variable | Used for |
|---|---|
| `ENVIRONMENT` | GCS bucket-name **prefix** (`gs://${ENVIRONMENT}-${...BUCKET}/...`) and the log format (`local` = human-readable; anything else = JSON). |
| `TAX_INVOICE_GCP_PROJECT_ID` | `gcp.project_id`, `gcs.project_id`, `vertexai.project_id`. |
| `TAX_INVOICE_GCP_PROJECT_NAME` | `gcp.project_name` (carried, not used by this package). |
| `TAX_INVOICE_PROCESSING_BUCKET` | The bucket suffix for all five `gcs.*` paths. |
| `TAX_INVOICE_VERTEX_AI_MODEL_NAME` / `_LOCATION` | `vertexai.model` / `vertexai.location`. |
| `TAX_INVOICE_SITE_NAME`, `_SITE_SITE_DOMAIN`, `_SITE_SITE_PATH`, `_SITE_CLIENT_ID`, `_SITE_CLIENT_SECRET`, `_SITE_TENANT_ID` | Source-site SharePoint credentials. |
| `TAX_INVOICE_TAX_INVOICE_ROOT` / `_INPUT` | `sharepoint.source_site.src_path`. |
| `TAX_INVOICE_CONTROL_ROOT` | Prefix for every control-site path. |
| `TAX_INVOICE_OCR_PREP_LOG_PATH` | Control-site folder for `ocr_pre_processing_log.csv`. |
| `TAX_INVOICE_PAGE_MANIFEST_LOG_PATH` | Control-site folder for `page_manifest_log.csv`. |
| `TAX_INVOICE_OCR_TRACING_LOG_PATH` | Control-site folder for `tracing_log_YYYYMM.csv`. |
| `TAX_INVOICE_MAX_CONCURRENT_UPLOADS` | `framework.concurrency_upload`. |
| `BOT_EMAIL`, `OPER_EMAIL`, `USER_EMAIL`, `DEVELOPER_EMAIL` | `framework.notifications.system_exception` recipients. |
| `CONTROL_SITE_*` | Control-site SharePoint **credentials** — read from `config/common.yml` `control:`, **not** from the task YAML. |
| `SANDBOX_SITE_*`, `DEV_EMAIL` | Microsoft Graph sender, from `config/common.yml` `msgraph:`. |

### 8.3 Module / helper / schema pointers

The full tables are in the [README](README.md#modules-module) — do not duplicate them. What follows
is only the "which file do I open when…" index.

| I need to change… | Open |
|---|---|
| how source files are found or filtered | [module/source_loader.py](module/source_loader.py) |
| the IQS gate's *decisions* | [config/tax_invoice_extraction/iqs_config.yml](../../config/tax_invoice_extraction/iqs_config.yml) |
| the IQS gate's *maths* | [src/utils/image_utils.py](../../src/utils/image_utils.py) |
| how pages are split / named / rejected | [module/document_processor.py](module/document_processor.py) |
| the JSONL request shape or the response schema | [module/payload_builder.py](module/payload_builder.py) |
| how jobs are submitted / how a submit failure is handled | [module/batch_submitter.py](module/batch_submitter.py), [module/batch_job_client.py](module/batch_job_client.py) |
| how a prediction line becomes rows, or the row STATUS rules | [module/result_retriever.py](module/result_retriever.py) |
| the source-attribution join, `FILE_PATH`, or the output frame shape | [module/result_finalizer.py](module/result_finalizer.py), [schema/ocr_output.py](schema/ocr_output.py) |
| how page statuses roll up to a file status | [module/status_finalizer.py](module/status_finalizer.py) |
| what a pre-processing-log row looks like | [module/pre_log_builder.py](module/pre_log_builder.py), [schema/pre_processing_log.py](schema/pre_processing_log.py) |
| how logs are written / the concurrency guard | [module/log_exporter.py](module/log_exporter.py) |
| the raw-Gemini audit trail | [module/tracing_builder.py](module/tracing_builder.py), [module/tracing_exporter.py](module/tracing_exporter.py) |
| what the model is asked to extract | [prompt/system_prompt.md](prompt/system_prompt.md), [schema/model_response.py](schema/model_response.py) |
| the business-facing message strings | [helper/messages.py](helper/messages.py) |
| the `on_error` email | [helper/error_notify.py](helper/error_notify.py) |

### 8.4 Unverified external-API claims

Stated in the code, but **not verifiable from this repository** — check the Vertex AI docs before
relying on them:

- `PayloadBuilder`'s docstring says payloads are split at `BATCH_LINE_LIMIT` (100 000) "so each
  submitted job stays within the **200 000-line hard limit**." I could not confirm that limit.
- The `JobState` enum names (`JOB_STATE_SUCCEEDED`, `_PARTIALLY_SUCCEEDED`, `_FAILED`, `_CANCELLED`,
  `_EXPIRED`) are taken from the code's constants. `_normalize_job_state` defensively handles both
  an enum and a raw string, and an unrecognised state is treated as **still running** — which means
  a *new* terminal state added by Vertex would leave files `PENDING` indefinitely rather than
  crashing. Worth a periodic check against the SDK.
- Whether Vertex Batch truly has **no per-record retry** is the load-bearing assumption behind
  `_backfill_required`, `_coerce_doc_type`, and the lenient gate (§6.2, §6.3). If that ever becomes
  false, those decisions could be revisited — but they are safe either way.

---

## 9. Testing and ops

### 9.1 Test layout

`tests/test_tasks/ocr_pipeline/` — **one test module per source module**, same name:

```
test_batch_job_client.py     test_model_response.py       test_source_loader.py
test_batch_submitter.py      test_on_error_wiring.py      test_source_window.py
test_document_processor.py   test_page_processor.py       test_status_finalizer.py
test_error_notify.py         test_payload_builder.py      test_submit_task.py
test_finalize_task.py        test_pre_log_builder.py      test_task_context.py
test_gcs_router.py           test_reject_handling.py      test_thai_text.py
test_init_conn.py            test_result_finalizer.py     test_tracing_builder.py
test_log_exporter_retry.py   test_result_retriever.py     test_tracing_exporter.py
test_log_helper.py           test_retrieve_contract.py
```

(The `validate()` collectors `missing_string_errors` / `int_castable_errors` live in
`src/utils/common.py`, so their tests are in `tests/utils/test_common.py`.)

Four of these are cross-cutting rather than per-module and are worth knowing about:

- **`test_retrieve_contract.py`** — guards the `OCRResult` hand-off contract (§4).
- **`test_on_error_wiring.py`** — guards that all three tasks call `notify_system_error` on failure.
- **`test_log_exporter_retry.py`** — guards the generation-guard + single-retry logic (§6.8).
- **`test_source_window.py`** — guards the CLI date-window resolution and the no-placeholder warning.

Run them:

```bash
uv run pytest tests/test_tasks/ocr_pipeline/
uv run pytest tests/test_tasks/ocr_pipeline/test_result_finalizer.py -v
```

> **Windows flake**: a full `uv run pytest` can fail with `WinError 32` on `logs/app.log` when the
> log hits its 10 MB rotation cap mid-run. Clear `logs/app.log*` and re-run — it is not your change.

### 9.2 Re-run playbook

**Re-running the pre (submit) pipeline**

```bash
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml
```

- Files currently `PENDING`/`PARTIAL` are **skipped**.
- Files that are `SUCCESS` / `SUCCESS_WITH_FAILURE` / `FAILED` / `REJECTED` are **re-processed —
  re-uploaded, re-scored, re-submitted, and re-billed.** This is how you replay a fixed prompt, but
  it is not free. Use `--rerun_data_dt` / `--start_data_dt` + `--end_data_dt` to scope it (they only
  work if `src_path` carries a `%{DATA_DATE...}` placeholder — otherwise they are ignored with a
  warning).
- Safe: the landing/processing/payload paths all carry `${JOB_ID}`, so a re-run writes to fresh
  directories and cannot clobber the previous run's artifacts.

**Re-running the post (retrieve + business + finalize) pipeline**

```bash
uv run python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml
```

- If nothing is in-flight → retrieve returns `None`, the chain does nothing.
- If the jobs are still running → retrieve returns `None`.
- If the jobs succeeded and the business task previously **crashed** → the predictions are re-read
  from GCS **for free**, the business task runs again, and finalize stamps. **This is the designed
  recovery path.**
- If the previous run completed cleanly → the files are already terminal, so
  `build_terminal_log_rows` skips them all and finalize logs `No in-flight files to stamp`. But note
  that **retrieve still re-collects and the business task still re-runs** — retrieve's in-flight
  filter only looks at the pre-processing log, and after a clean run nothing is in-flight, so
  retrieve returns `None` and the business task gets `pre_result=None`. Whether that is a no-op
  depends on your business task; make sure it handles `pre_result=None` gracefully.

**Recovering a stuck `PENDING` file**

Diagnose in this order:

1. **Is its job still running?** Read `pre_processing_log.csv`, find the file's latest row, take
   `batch_inference_job_name`, and check the job in the Vertex console. If it is running, wait.
2. **Did the job die?** If Vertex reports `FAILED`/`CANCELLED`/`EXPIRED`, just **run the post
   pipeline**. `_force_dead` will stamp the file `FAILED` and it will leave the in-flight set. It
   then becomes eligible for a fresh submit.
3. **Is the business task crashing?** Check the logs for the business task's exception. Files stay
   `PENDING` by design until it succeeds. Fix the bug and re-run post — no Gemini cost.
4. **Is finalize silently no-op-ing?** Look for `Nothing to finalize (no upstream OCRResult with
   file statuses)` in the logs. That means the business task returned something other than an
   `OCRResult` (§4). Fix the business task's return value.
5. **Is the file missing from the log entirely?** `build_terminal_log_rows` logs
   `File absent from pre-processing log, skipping terminal stamp: {path}` at WARNING. That means
   `final_df` produced a `FILE_PATH` that has no matching `sharepoint_input_path` row — a join or
   data-integrity problem, not a status problem.
6. **Last resort — manual unstick.** The log is an append-only CSV in GCS (mirrored to SharePoint).
   Appending a hand-written terminal row for the file (same `sharepoint_input_path`, a later
   `update_dt`, `status = FAILED`) will take it out of the in-flight set. This is a destructive
   manual edit of a control file — get sign-off, and prefer options 2–4 whenever they apply.

### 9.3 Two gotchas worth knowing before you debug

- **A landing-read failure surfaces as a confusing `REJECTED` message.** If
  `PageProcessor._process_file` cannot download a file's landing copy, it logs a WARNING and returns
  **no manifest rows** ([page_processor.py:78](module/page_processor.py)). `PreLogRowBuilder` then
  sees zero pages, finds zero valid ones, and writes a `REJECTED` row saying
  **"All 0 page(s) failed the image quality check"**. The IQS gate is innocent; the GCS read failed.
  Look for the `Failed to read landing copy` warning above it.
- **The `iqs_config_path` fallback default points at a directory that does not exist.**
  [submit_task.py:138](submit_task.py) defaults to `"config/ocr_tax_invoice_pipeline/iqs_config.yml"`
  — there is no such directory in this repo. The default is unreachable in practice, because
  `framework.iqs_config_path` is in `REQUIRED_STRING_KEYS` and `validate()` halts without it. It is
  dead code, not a bug — but do not copy it into a new config as if it were a real path. (The
  system prompt has no local fallback at all: it is downloaded from
  `sharepoint.control_site.system_prompt_path`, and a missing or blank file fails the run.)
