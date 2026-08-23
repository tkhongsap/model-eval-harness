# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Rules

@.claude/rules/ask-dont-guess.md

## Project

`model_migration` — a Python 3.12/3.13 project managed by **uv**. The tracked code is
currently a platform scaffold: a production-grade structured-logging/observability
library (`src/logger`) and a Microsoft Graph / SharePoint integration hook
(`src/hook`). [main.py](main.py) is a scratch entrypoint — most of it is commented-out
experiments against an OpenAI-compatible gateway; only the logger bootstrap is live.

> Working-tree note: this branch has the previous benchmark POC (`src/benchmark`,
> `src/client`, `src/serve`, `src/vllm*`) staged for deletion, so `git ls-files` lists
> ~80 files that no longer exist on disk. Trust the filesystem, not the index.

## Commands

Run everything from the repo root — imports are absolute (`from src.logger import ...`)
and depend on the CWD being on `sys.path`.

```bash
uv sync                                  # install deps + dev group into .venv
.venv/Scripts/python.exe main.py         # run the entrypoint (Windows; .venv/bin/python elsewhere)

.venv/Scripts/python.exe -m ruff check .            # lint (E4, E7, E9, F, I; line-length 100)
.venv/Scripts/python.exe -m ruff check . --fix      # autofix

.venv/Scripts/python.exe -m pytest                        # all tests
.venv/Scripts/python.exe -m pytest tests/test_x.py::test_y -v   # a single test
.venv/Scripts/python.exe -m pytest --cov=src                    # with coverage
```

**Do not run `ruff format`.** No formatter config is set, so it would rewrite
`src/logger` and `src/utils` from their 2-space indentation to 4-space. Only the linter
is configured in [pyproject.toml](pyproject.toml).

Any test touching logging must call `Logger.reset_for_testing()` in a fixture — the
logger config is process-global and idempotent, so without a reset the first test to
configure it wins for the whole session.

## Configuration

All runtime config is environment variables, read in
[src/logger/config.py](src/logger/config.py) via `LoggerConfig.from_env()`:
`ENVIRONMENT` (or `ENV`), `PROVIDER`, `SERVICE_NAME`, `SERVICE_VERSION`, `LOG_LEVEL`,
`LOG_FORCE_JSON`, `LOG_ENABLE_OTEL`, `LOG_CONSOLE_COMPACT`, plus `GCP_PROJECT_ID` (read
directly by the GCP processor). Local values live in `.env` (gitignored).

`DOC_LLM_CONCURRENCY` is read fail-soft by the local documents pipeline's `Config`
(via a `default_factory`, so it is resolved at `Config()` time — after `main.py`'s
`load_dotenv()`): parallel `extract_page` calls per chunk. Unset or invalid → 1
(sequential, today's behavior); invalid values additionally log a warning.
`SENTI_LLM_CONCURRENCY` is the same contract for the local sentiment ASR+LLM pipeline
(`internal_asr_llm_output.py`), where it gates whole files in flight — each worker owns
one file's ASR→label→analysis chain; GCS I/O and checkpoints stay on the main thread.

`LOG_CONSOLE_COMPACT` hides service-identity and callsite fields from the pretty console
renderer only — `LoggerConfig.hide_console_fields` gates it on `not use_json_renderer`,
so JSON output always keeps the full record for backend indexing.

The parsing is deliberately **fail-soft**: an unrecognized `ENVIRONMENT`, `PROVIDER`,
`LOG_LEVEL`, or boolean falls back to a default instead of raising, so a typo'd env var
can't crash service startup. `SERVICE_NAME` is the one exception — it raises if neither
the argument nor the env var is set. Preserve that asymmetry when extending config.

`main.py` calls `load_dotenv()`, but `python-dotenv` is only present transitively (via
`pydantic-settings`) — add it as a direct dependency if you rely on it.

## Architecture

### `src/logger` — structured logging + trace correlation

The design intent: every log line from application code *and* from third-party libraries
lands on stderr in one wire format, carrying service identity and OpenTelemetry trace IDs.
Four files, and understanding it requires all four:

- **[config.py](src/logger/config.py)** — frozen/slotted `LoggerConfig` plus the
  `Environment` and `Provider` enums. Two properties drive most branching downstream:
  `is_production` (gates the expensive dev-only callsite processor) and
  `use_json_renderer` (JSON everywhere except a local TTY).
- **[processors.py](src/logger/processors.py)** — `LoggerProcessor` ABC adapting
  structlog's `(logger, method_name, event_dict)` protocol, and three concrete
  processors: `ServiceContextProcessor` (stamps `service.name`/`service.version`/
  `deployment.environment` via `setdefault`, so caller fields always win),
  `OpenTelemetryProcessor` (injects W3C hex `trace_id`/`span_id`; a silent no-op when
  OTel is missing, no span is active, or the context is invalid — never raises), and
  `GCPFormatterProcessor` (renames `level`→`severity`, `event`→`message`,
  `timestamp`→`time`, and trace fields into Cloud Logging's expected keys).
- **[logger.py](src/logger/logger.py)** — the process-global facade. `configure()` is
  lock-guarded and idempotent; only the first call wins.
- **[tracing.py](src/logger/tracing.py)** — `TracedOperation`, a context manager that
  opens an OTel span and binds structlog contextvars for a block, emitting
  `operation.started`/`completed`/`failed` with `duration_ms`. It resolves its logger
  lazily in `__enter__`, *not* `__init__` — constructing one before `Logger.configure()`
  would otherwise lock the global into the `"unconfigured"` service name.

Three invariants worth knowing before editing:

1. **Processor order is load-bearing.** `merge_contextvars` first (so request-scoped
   fields are visible downstream) → `add_log_level` + `TimeStamper` (so later processors
   see canonical fields) → service context → OTel → `format_exc_info` +
   `StackInfoRenderer` (without these, exceptions are silently dropped from JSON) →
   `GCPFormatterProcessor` (must run *after* OTel so `trace_id` exists to rewrite) →
   callsite (dev only) → renderer.
2. **Two chains must stay in sync.** `_build_processor_chain` builds the native structlog
   chain; `_configure_stdlib_bridge` builds a near-identical `foreign_pre_chain` for
   `structlog.stdlib.ProcessorFormatter`, which is what routes `logging.getLogger(...)`
   records from google-cloud-*, sqlalchemy, urllib3 etc. through the same renderer.
   Adding a processor to one and not the other silently splits the wire format.
3. **Renderer and logger factory are coupled.** When JSON + `orjson` are both active the
   renderer emits *bytes*, which requires `BytesLoggerFactory`; otherwise
   `WriteLoggerFactory(sys.stderr)`. `ProcessorFormatter` rejects bytes, so the bridge
   asks `_build_renderer(..., str_output=True)` for a str-emitting renderer. Everything
   writes to **stderr** so stdout stays clean for tools that parse it.

### `src/hook` — external service integrations

- **[tls.py](src/hook/tls.py)** — `TlsPolicy` produces `SSLContext`s / `requests`
  adapters / sessions pinned to a minimum TLS version (default 1.2, not 1.3, so
  endpoints without 1.3 keep working). It raises the protocol floor only; certificate
  verification and the system CA bundle stay on. Use `TlsPolicy().session()` for any new
  outbound HTTP client rather than a bare `requests.Session()`.
- **[sharepoint.py](src/hook/sharepoint.py)** — `SharePointModule` wraps Microsoft Graph
  v1.0. Auth is MSAL client-credentials (`SANDBOX_CLIENT_ID` / `_SECRET` / `_TENANT_ID`
  / `_SITE_DOMAIN` / `_SITE_PATH`), with the TLS-pinned session handed to MSAL as its
  `http_client`. One `ConfidentialClientApplication` is built in `__init__` and reused, so
  MSAL's token cache actually serves repeat acquisitions. The constructor eagerly acquires
  a token and calls `_test_connection()`, so construction fails fast on bad config.
  `copy_file` deliberately does download+upload instead of Graph's `/copy` endpoint, which
  is async and misbehaves on same-folder copies.

Five invariants in this module are load-bearing:

1. **Paths must go through `_encode_path` before entering a URL.** Graph addresses items as
   `/drive/root:{path}`, and an unencoded `#` truncates the request at the URL fragment —
   taking any trailing `:/content` with it — so the call silently lands on a *different*
   item. `_item_endpoint` applies it and special-cases the root (`/drive/root`, no colon).
   Do **not** encode `@odata.nextLink` or `@microsoft.graph.downloadUrl`; both are absolute
   and already encoded.
2. **Retry callables must rebuild their headers.** All requests funnel through
   `__handle_response_with_retry`, which refreshes the token on 401 and honours
   `Retry-After` on 429/503. Its `retry_func` must call `self._get_headers()` *inside* the
   lambda — capturing a header dict re-sends the token that was just rejected.
3. **`_raise_for_status` is the single ERROR site for HTTP faults.** It maps status onto
   `SharePointError` / `SharePointAuthError` / `SharePointNotFoundError` /
   `SharePointConflictError`. Outer handlers must not log again, or every fault produces two
   records.
4. **`upload_file` does not touch the destination unless asked.** `archive_on_lock=False` by
   default; when enabled it archives only on 423 (never 409, which is usually an
   eTag/`nameAlreadyExists` conflict), at most once per upload, and restores the archived
   file if the retry ultimately fails.
5. **Every request passes `timeout=self._timeout`.** `requests` defaults to no timeout, so
   an omission blocks the process indefinitely with nothing in the log.

Logging is structured (`logger.info("sharepoint.upload.completed", path=..., bytes=...)`).
Reads are DEBUG; INFO is reserved for lifecycle, state changes, and one summary per bulk
listing. Never log a headers dict, `client_secret`, or a raw download URL — `_safe_url`
strips the query string precisely because Graph's download URLs carry a credential there.

- **[gcp_gcs.py](src/hook/gcp_gcs.py)** — `GCSModule` wraps google-cloud-storage. The bucket is a
  per-call argument, so one instance spans a project. Credentials come from ADC via
  `google.auth.default()`, which is also the constructor's fail-fast check — there is deliberately
  no connection probe, since a `list_buckets` call would demand a permission a narrowly-scoped
  service account will not have.

Four invariants here are load-bearing:

1. **`storage.Client(_http=...)` must be given an `AuthorizedSession`.** The base client only
   builds one when its backing field is `None`, and that kwarg is what fills it — so passing a
   bare `TlsPolicy().session()` sends every request with no `Authorization` header and turns the
   module into a 401 generator. `AuthorizedSession` subclasses `requests.Session`, so the TLS pin
   composes with it: mount the adapter on the session, and pass `auth_request=` to pin the
   separate token-refresh leg too. `credentials=` must accompany `_http=` or the client's
   universe-domain check has nothing to validate against.
2. **Object names must not start with `/`.** `bucket.blob("/a/b.txt")` addresses an object
   literally named `/a/b.txt` — a different object from `a/b.txt`, invisible to a listing of
   `a/`. Everything goes through `_normalize_blob_path` because `SharePointModule` paths all
   *do* start with `/`, and code bridging the two will pass one.
3. **`list_blobs(...).prefixes` is only valid after the iterator is consumed.** The SDK fills it
   page by page, so reading it off a fresh iterator always yields an empty set — the bug that
   made the previous `list_directories` return `[]`. `_iter_blobs` consumes first, then reads.
4. **Retries belong to the SDK, not this module.** Every method already takes `retry=` with
   `DEFAULT_RETRY` (exponential backoff over 429/5xx), unlike Graph. Do not add a retry loop
   here. `timeout=self._timeout` is passed for tunability, not hang-prevention: the SDK already
   defaults to 60s.

`_translate_errors` is the single ERROR site, mapping `GoogleAPICallError` onto
`GCSError` / `GCSAuthError` / `GCSNotFoundError` / `GCSConflictError` by `exc.code` — a status
lookup rather than an except-chain, which would need most-specific-first ordering
(`PreconditionFailed` subclasses `ClientError`) that a later edit breaks silently. Outer handlers
must not log again. Never log credentials or a signed URL; a signed URL carries its own
credential in the query string, exactly like Graph's `@microsoft.graph.downloadUrl`.

- **[gcp_genai.py](src/hook/gcp_genai.py)** — `VertexAIBatchInference` wraps google-genai's batch
  API. **Vertex-only, deliberately**: the Gemini Developer API (api_key auth) rejects a GCS or
  BigQuery source, accepting only `files/...` or inlined requests, so the batch contract this module
  implements does not exist there. Credentials come from ADC via `google.auth.default()` with
  google-genai's own `cloud-platform` scope, which is also the constructor's fail-fast check; the
  resolved credentials are handed to `genai.Client` so it does not resolve ADC twice, and the
  project falls back to the one ADC reports (which is what makes it work unconfigured on Cloud Run).
  Results are not downloaded here — `pull_batch_job_results` returns the `gs://` output *directory*
  (a set of JSONL shards, not one file) and the caller fetches it, normally with `GCSModule`.

Five invariants here are load-bearing:

1. **The resource is `batchPredictionJobs`, not `batchJobs`.** `google.genai._transformers
   .t_batch_job_name` validates a Vertex name against
   `^projects/[^/]+/locations/[^/]+/batchPredictionJobs/[^/]+$` and raises `ValueError` on anything
   else, so the wrong spelling fails before a request is ever sent. `get_batch_job_name` is the one
   place that builds it, and it is idempotent so a `job.name` can be handed straight back.
2. **`HttpOptions.timeout` is milliseconds.** `_api_client` divides it by 1000. The module takes
   seconds at its own boundary, matching `GCSModule`, and converts once in `_build_http_options` —
   passing `60` straight through would mean a 60ms timeout. That method also copies a
   caller-supplied `HttpOptions` before touching it: the TLS fields are assigned in place, so
   mutating the original leaks one client's SSL context into every other client built from it.
3. **Never poll on `BatchJob.done`.** It tests membership in the SDK's `JOB_STATES_ENDED`, which
   omits `JOB_STATE_PARTIALLY_SUCCEEDED` — a terminal state. A loop built on it never exits for a
   job that partially succeeded. `TERMINAL_STATES` in this module includes it; `wait_for_batch_job`
   uses that and raises `VertexAIBatchTimeoutError` rather than spinning forever.
4. **An omitted `dest` is silently derived, not rejected.** `_extra_utils.format_destination` turns
   a source of `gs://bucket/in.jsonl` into `gs://bucket/in/dest`. `submit_batch_job` therefore logs
   the *resolved* destination, so a run never leaves output somewhere unrecorded. It also rejects a
   `src` that is not `gs://` or `bq://` up front, because the SDK would otherwise read it as a
   Developer-API source and fail much later with a message that never mentions Vertex.
5. **Retries belong to the SDK.** `_api_client` already retries 408/429/5xx with exponential
   backoff and jitter over 5 attempts. Do not add a loop, exactly as in `gcp_gcs.py`.

`_translate_errors` is the single ERROR site, mapping `google.genai.errors.APIError` — the base of
both `ClientError` (4xx) and `ServerError` (5xx) — onto `VertexAIBatchInferenceError` /
`VertexAIBatchAuthError` / `VertexAIBatchNotFoundError` / `VertexAIBatchQuotaError` by `exc.code`.
Outer handlers must not log again. `exc.details` is never logged: it is the raw response body, the
one field that can echo request content back into the log. `location` defaults to `"global"`, which
is valid for batch inference with standard Gemini models but **not** for tuned models — those need
a real region such as `us-central1`.

### `src/utils` — leaf helpers

[data.py](src/utils/data.py) is pure conversion, no I/O (`str_to_bool` with a
deliberately narrow token set that raises on anything else); [common.py](src/utils/common.py)
wraps it for env vars, swallowing the `ValueError` into a default. Keep the layering:
`utils` imports nothing from `logger` or `hook`.

## Conventions

- **Indentation is inconsistent by directory.** `src/logger` and `src/utils` use 2-space
  indent; `src/hook` uses 4-space. Match the file you're editing.
- Google-style docstrings with Args/Returns/Raises; `src/logger` documents *why* a design
  choice was made (see the processor-order docstring) — keep that habit for non-obvious code.
- `from __future__ import annotations` at the top of new modules in `src/logger`.
- Absolute imports rooted at `src.` — `src/hook` and `src/utils` are namespace packages
  (no `__init__.py`), only `src/logger` has one and it defines the public `__all__`.
