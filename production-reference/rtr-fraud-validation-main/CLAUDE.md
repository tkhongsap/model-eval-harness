# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RTR (Retailer) Fraud Validation system that uses Google Gemini 2.5 Flash to analyze shop images for fraud detection. The pipeline downloads images from S3, runs AI validation via Gemini, extracts EXIF metadata, computes image similarity (SSIM), generates Excel reports, uploads results to SharePoint, and sends email notifications.

## Common Commands

```bash
# Install dependencies
uv sync

# Run the main fraud validation pipeline
uv run -m app.main

# Run the fact-checker evaluation module
uv run -m app.modules.fact_checker --config_path config/fact_checker/rtr.yml

# Export requirements for Docker
uv export --no-hashes --no-dev -o .requirements.txt

# Run tests
uv run pytest

# Run unit tests only
uv run pytest tests/unit/ -v

# Lint
uv run ruff check .

# Type check
uv run mypy .
```

## Architecture

The codebase uses an OOP design with dependency injection. All business logic is separated into focused classes; `app/main.py` is a thin factory that wires services and runs the pipeline.

### Entry Points
- **`app/main.py`** — ~100-line factory: wires all services, builds `PipelineConfig`, calls `asyncio.run(pipeline.run())`.
- **`app/modules/fact_checker.py`** — `FactCheckerModule` class that evaluates model predictions against ground truth, calculates metrics (accuracy, precision, recall, F1), and generates reports.

### Core Layer (`app/core/`)
- **`app/core/models.py`** — Typed dataclasses: `ShopRecord`, `ShopResult`, `DetectionResult`, `TokenUsage`, `GpsMetadata`, `PipelineConfig`, `ProcessStatus`. `ShopResult.to_output_list()` is the single source of truth for column ordering. Factory classmethods: `ShopResult.no_photo()`, `.insufficient_photos()`, `.s3_error()`, `.unhandled_error()`.
- **`app/core/interfaces.py`** — `Protocol` definitions: `StorageReader`, `AIValidator`, `SecretProvider`, `Notifier` — enables mocking without subclassing.

### Services (`app/services/`)
- **`app/services/secret_service.py`** — `SecretService`: GCP Secret Manager + env fallback + in-memory cache. `get(key)` / `get_optional(key, default)`.
- **`app/services/s3_service.py`** — `S3Service`: boto3 wrapper. `read_bytes(key)`, `normalise_key(path)`.
- **`app/services/gcs_service.py`** — `GCSService`: gcsfs wrapper. `read()`, `write()`, `exists()`, `delete()`, `uri()`.
- **`app/services/sharepoint_service.py`** — `SharePointService`: unified MSAL-authenticated SharePoint client (one instance per site). `list_files()`, `download_file()`, `upload_file()`, `move_to_archive()`, `ensure_folder()`.
- **`app/services/gemini_service.py`** — `GeminiService`: async Gemini call with tenacity retry. `validate(images_b64, prompt) -> (dict, TokenUsage)`.
- **`app/services/email_service.py`** — `EmailService`: Microsoft Graph API email sender. `send(to, subject, body, attachments)`.

### Processors (`app/processors/`)
- **`app/processors/image_processor.py`** — `ImageProcessor`: pure, no I/O. `extract_metadata(bytes) -> GpsMetadata`, `compute_ssim(b64, b64) -> float`, `compute_same_photo_label(list[b64]) -> str` (e.g. `"2/3"`), `are_similar(score) -> bool`.
- **`app/processors/shop_processor.py`** — `ShopProcessor`: async per-shop orchestration. `process(record, prompt) -> ShopResult`. Composes `S3Service`, `GeminiService`, `ImageProcessor`.
- **`app/processors/report_builder.py`** — `ReportBuilder`: builds Excel workbooks and transaction/performance log rows. `build_user_excel(df1, df2) -> BytesIO`, `build_transaction_row(log, date, project_id, project_name) -> dict`, `build_performance_row(transaction_df) -> dict`.
- **`app/processors/email_composer.py`** — `EmailComposer`: builds HTML email body and inline chart images from result DataFrames.

### Pipeline (`app/pipeline/`)
- **`app/pipeline/fraud_pipeline.py`** — `FraudValidationPipeline`: orchestrates the full run via named stage methods: `_ingest()`, `_process_shops()`, `_build_report()`, `_publish()`, `_notify()`, `_cleanup()`. Uses `asyncio.Semaphore(batch_size)` + `asyncio.gather` for concurrency.

### Utilities
- **`app/share_log.py`** — JSON-structured logging for Cloud Logging. Use `get_logger(__name__)` in new modules.
- **`app/utils/common.py`** — YAML loading with `${ENV_VAR}` template resolution, date templating (`%{DATA_DATE[_±N[DMY]][_FORMAT]}`), nested dict navigation, JSON schema `$ref` resolution.

### Compatibility Shim
- **`app/modules/sharepoint.py`** — Thin shim that delegates to `SharePointService`; keeps `FactCheckerModule` imports working without modification.

### Legacy Files (superseded, kept for reference)
- **`app/utility.py`** — Original business logic (superseded by services/processors).
- **`app/sharepoint.py`** — Original procedural SharePoint (superseded by `SharePointService`).
- **`app/mail.py`** — Original email dispatch (superseded by `EmailService` + `EmailComposer`).

### Configuration System
YAML-based configs in `config/`:
- `config/app/rtr_main.yaml` — Input sources, output schemas (`appended_schema` column order matches `ShopResult.to_output_list()`), field definitions
- `config/model_setting/rtr.yml` — Gemini model parameters (temperature=0, seed=0 for deterministic output)
- `config/system_prompt/rtr.yml` — Multi-task prompt for 3 detection tasks: from-other-device, shop operation, unrelated content
- `config/fact_checker/rtr.yml` — Ground truth sources and metric thresholds

Environment variables are injected into YAML via `${VAR_NAME}` syntax and resolved at runtime.

### Cloud Integrations
- **AWS S3** (boto3) — Source image storage
- **GCP** — GCS for temp files, Secret Manager for credentials, Vertex AI/Gemini for inference, Cloud Run Jobs for execution, Cloud Scheduler for cron
- **Azure/Microsoft** — SharePoint for file I/O, Microsoft Graph API for email

## Tests

Unit tests live in `tests/unit/`. Run with `uv run pytest tests/unit/ -v`.

| File | What it tests |
|---|---|
| `tests/unit/test_models.py` | `ShopResult`, `ShopRecord`, `TokenUsage`, `DetectionResult`, `GpsMetadata` |
| `tests/unit/test_image_processor.py` | Haversine, GPS flags, SSIM, `compute_same_photo_label` formula |
| `tests/unit/test_shop_processor.py` | `ShopProcessor.process()` with mocked S3/Gemini |
| `tests/unit/test_report_builder.py` | Excel sheets/headers, transaction row fields |
| `tests/unit/test_secret_service.py` | `SecretService` — cache, env, GCP fallback, error paths |
| `tests/unit/test_s3_service.py` | `S3Service` — read, normalise, key_exists, decrypt |
| `tests/unit/test_gcs_service.py` | `GCSService` — all I/O methods, delete-error, copy-if-miss |
| `tests/unit/test_gemini_service.py` | `GeminiService` — validate, JSON strip, token parsing |
| `tests/unit/test_email_service.py` | `EmailService.send()` — Graph API, attachments, inline images |
| `tests/unit/test_sharepoint_service.py` | `SharePointService` — CRUD, 401 refresh, lock retry |
| `tests/unit/test_email_composer.py` | `EmailComposer` — compose, Thai date, HTML tables |
| `tests/unit/test_fraud_pipeline.py` | `FraudValidationPipeline` — all stage methods |

Shared fixtures in `tests/conftest.py`: `make_jpeg_bytes()`, `make_b64_jpeg()`, `make_patterned_jpeg_bytes()` (checkerboard patterns for SSIM tests).

> **Async tests:** `pytest-asyncio` is already a dev dependency. `asyncio_mode = "auto"` is set in `pyproject.toml` so async test functions run without `@pytest.mark.asyncio`.

> **Note on SSIM tests:** Solid-colour JPEG images have zero spatial variance and score unexpectedly high SSIM regardless of colour. Use `make_patterned_jpeg_bytes()` or mock `are_similar()` when testing "different images" cases.

## Deployment

Deployed as GCP Cloud Run Jobs via Cloud Build (`cloud_build/workflows/np_deployment.yaml`):
- Main job: 4 CPU, 8Gi memory, 24h timeout, scheduled 1st & 16th at 16:00 (Asia/Jakarta)
- Fact-checker job: 2 CPU, 4Gi memory, scheduled 1st of month at 21:00

## Code Style

- **Linter:** Ruff (line-length 88, target Python 3.10)
- **Rules:** E, F, W, I, C, B, UP, RUF enabled; E501 and C901 ignored
- **Type checking:** MyPy strict mode (`disallow_untyped_defs = true`)
- **Package manager:** uv (lockfile: `uv.lock`)
- **Python version:** >=3.10, <3.13 (Docker uses 3.12)
- Use `get_logger(__name__)` from `app.share_log` in every new module (not the global `logger` singleton).
