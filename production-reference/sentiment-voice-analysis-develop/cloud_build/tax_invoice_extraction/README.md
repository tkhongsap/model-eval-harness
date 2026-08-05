# Tax Invoice Extraction — Cloud Build & Deployment

CI/CD pipeline and Docker configuration for the OCR Tax Invoice Extraction project.

## 📁 Files

| File                      | Purpose                                              |
| ------------------------- | ----------------------------------------------------- |
| `Dockerfile`              | Multi-stage build (builder + runtime)                |
| `Dockerfile.dockerignore` | Docker-specific ignores                              |
| `wf_deployment.yml`       | Full deployment pipeline (Cloud Build + Prisma scan) |
| `.env.example`            | Project-specific environment template (currently empty — see note below) |

> **Note:** `.env.example` in this folder is currently an empty file. Use the Tax
> Invoice-specific variable names documented in the repo [CLAUDE.md](../../CLAUDE.md)
> until it is populated.

---

## 🐳 Dockerfile

Multi-stage build using `python:3.11.14-slim-bookworm`:

1. **Builder Stage** — Installs dependencies with UV (`uv sync --frozen --no-install-project`)
2. **Runtime Stage** — Applies outstanding Debian security updates (`apt-get upgrade`) and
   upgrades `pip`/`setuptools`/`wheel` to clear base-image CVEs, copies `.venv`, application
   source (owned by `appuser`), runs as non-root `appuser`

**Included in image:** `main.py`, `src/`, `tasks/`, `config/`, `resources/`
**Excluded:** `terraform/`, `tests/`, `cloud_build/`, `.git/`, `.env`

### Build & Run Locally

```bash
# Build
docker build -f cloud_build/tax_invoice_extraction/Dockerfile -t sentiment-voice-analysis:latest .

# Run locally
docker run --env-file .env sentiment-voice-analysis:latest

# Run the pre (submit) pipeline
docker run --env-file .env sentiment-voice-analysis:latest \
  python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_pre_tasks.yml

# Run the post (retrieve + reconcile) pipeline
docker run --env-file .env sentiment-voice-analysis:latest \
  python main.py --config_path config/tax_invoice_extraction/ocr_pipeline_post_tasks.yml
```

---

## 🚀 Deployment Pipeline (`wf_deployment.yml`)

The Cloud Build workflow handles:

1. **Environment Configuration** — Validates `_ENVIRONMENT` (nprd/release/prod) and the
   `tax-inv-vX.X.X[-rcX]` tag format
2. **Docker Build & Push** — Builds image, pushes to Artifact Registry (skipped for prod)
3. **Terraform Init / Validate / Secret Import** — Initializes backend, validates config,
   imports any pre-existing Secret Manager secrets
4. **Terraform Apply — Pre-requisites** — Creates Artifact Registry, GCS bucket, Secret shells
5. **Prisma Cloud Image Scan Gate** — Fetches `twistcli`, scans the image, archives the report,
   then fails the build if any vulnerability/compliance finding is at or above
   `_SCAN_SEVERITY_THRESHOLD` (see below)
6. **Image Promotion** — For prod: pulls the image by digest from the release registry (no
   rebuild)
7. **Terraform Apply — Full infrastructure** — Creates/updates the Cloud Run job once the image
   exists and every secret has ≥1 version

This pipeline is the only one of the three projects with an integrated vulnerability/compliance
scan gate — QA and Telesale do not have this step.

### Image Promotion Strategy

```
[nprd env]  ──build──▶  [release env]  ──build──▶  [prod env]
   Build                    Build                    Copy only
   Deploy                   Deploy                   (no rebuild)
                            tax-inv-vX.X.X-rcX        tax-inv-vX.X.X
```

- **nprd & release**: Same GCP project — build, scan, and deploy new images
- **prod**: Different GCP project — promotes the scanned image from the release artifact
  registry by digest (no rebuild)

### Deploy Commands

```bash
# Deploy to nprd
gcloud builds submit . \
  --config=cloud_build/tax_invoice_extraction/wf_deployment.yml \
  --region=asia-southeast3 \
  --substitutions=_ENVIRONMENT=nprd

# Deploy to release
gcloud builds submit . \
  --config=cloud_build/tax_invoice_extraction/wf_deployment.yml \
  --region=asia-southeast3 \
  --substitutions=_ENVIRONMENT=release,_IMAGE_TAG=tax-inv-v1.0.0-rc1

# Deploy to prod (promotes image from release, no rebuild)
gcloud builds submit . \
  --config=cloud_build/tax_invoice_extraction/wf_deployment.yml \
  --region=asia-southeast3 \
  --substitutions=_ENVIRONMENT=prod,_IMAGE_TAG=tax-inv-v1.0.0,_SOURCE_IMAGE_TAG=tax-inv-v1.0.0-rc1,_SOURCE_PROJECT_ID=<NPRD_PROJECT_ID>
```

### Substitution Variables

| Variable                  | Default           | Description                                                        |
| -------------------------- | ----------------- | ------------------------------------------------------------------- |
| `_ENVIRONMENT`             | `nprd`            | Target environment: `nprd`, `release`, `prod`                      |
| `_IMAGE_TAG`               | `latest`          | Docker image tag (overridden by git tag if triggered)               |
| `_REGION`                  | `asia-southeast3` | GCP region                                                          |
| `_IMAGE_NAME`               | `ai_tax_inv_reconcile` | Docker image name in Artifact Registry                        |
| `_SOURCE_PROJECT_ID`       | —                 | Source project for prod image promotion                            |
| `_SOURCE_IMAGE_TAG`        | —                 | Source image tag for prod promotion (e.g., `tax-inv-v1.0.0-rc1`)   |
| `_TERRAFORM_ACTION`        | `apply`           | `plan` or `apply`                                                   |
| `_AUTO_APPROVE`            | `true`            | Auto-approve terraform apply                                       |
| `_PRISMA_API_VERSION`      | `v1`              | Prisma Compute Console API version used to fetch `twistcli`         |
| `_SCAN_SEVERITY_THRESHOLD` | `high`            | Minimum finding severity that fails the build: `low`/`medium`/`high`/`critical` |

### Prisma Cloud Image Scan Gate

`PRISMA_USER`, `PRISMA_PASSWORD`, and `PRISMA_CONSOLE_URL` must exist as Secret Manager secrets
in the target project (they are **not** Terraform-managed — create them manually). The workflow
maps them to `PCC_USER` / `PCC_PASSWORD` / `PCC_CONSOLE_URL` and uses them to download `twistcli`
and scan the built (or, for prod, the promoted-by-digest) image. The scan itself never aborts the
step — the JSON report is always archived to
`gs://${_STATE_BUCKET}/PRISMA_IMAGE_SCAN/${BUILD_ID}/image-scan.json` first, and only then does a
dedicated gate step parse that report and fail the build if any vulnerability or compliance
finding is at or above `_SCAN_SEVERITY_THRESHOLD`. A missing or unparseable report also fails
the build (fails closed).

---

## 📋 Pipeline Tasks

The pipeline is split into two single-purpose runs rather than one daily job:

**Pre (submit) pipeline** — `ocr_pipeline_pre_tasks.yml`

| #   | Task                     | Description                                                                                       |
| --- | ------------------------ | --------------------------------------------------------------------------------------------------- |
| 1   | **OCRSubmitTask**        | SharePoint → GCS landing, per-page raster + IQS scoring, upload IQS-valid pages, build Vertex AI Batch JSONL payloads, submit batch job(s), stamp pre-processing + page-manifest logs |
| 2   | **TaxInvoiceRejectTask** | Read this run's stamped logs from GCS, move fully-`REJECTED` files and split `PARTIAL` files' bad pages into the SharePoint reject path |

**Post (retrieve + reconcile) pipeline** — `ocr_pipeline_post_tasks.yml`

| #   | Task                       | Description                                                                                    |
| --- | -------------------------- | ------------------------------------------------------------------------------------------------ |
| 1   | **OCRRetrieveTask**        | Poll each in-flight Vertex job once, validate predictions, join back to source file/page        |
| 2   | **ReconcilePrecheckTask**  | Halt + email if Master-Buyer / Master-Vendor / Z45 source files are missing                     |
| 3   | **ReconcileTask**          | Reconcile against Master Buyer + Master Vendor + Z45; export Output workbooks + archives + audit logs |
| 4   | **OCRFinalizeTask**        | Stamp `SUCCESS` / `SUCCESS_WITH_FAILURE` / `FAILED` into the pre-processing log (must stay the last key) |

See [config/tax_invoice_extraction/README.md](../../config/tax_invoice_extraction/README.md) for
full task documentation.

### Trigger Model

Unlike the QA and Telesale pipelines (which poll for a previous batch and submit a new one in a
single daily Cloud Scheduler run), the tax-invoice pre and post pipelines are triggered
independently:

- **Pre pipeline** — triggered weekly by Cloud Scheduler (`0 9 * * 6`, Asia/Bangkok)
- **Post pipeline** — triggered by a GCP Workflow that fires on GCS object-finalize events for
  `predictions.jsonl` files landing under the batch output path, so retrieval starts as soon as
  a Vertex AI batch job actually finishes rather than on a fixed schedule

---

## 🔧 Environment Variables

Required secrets (injected via Secret Manager by Terraform). See the "Tax Invoice-specific"
section of the repo [CLAUDE.md](../../CLAUDE.md) for the full, authoritative list; the main
groups are:

- **Application Configuration** — `ENVIRONMENT`, `TAX_INVOICE_GCP_PROJECT_ID`,
  `TAX_INVOICE_GCP_PROJECT_NAME`, `TAX_INVOICE_VERTEX_AI_MODEL_NAME`,
  `TAX_INVOICE_VERTEX_AI_LOCATION`, `TAX_INVOICE_PROCESSING_BUCKET`, `LOG_LEVEL`
- **Tax Invoice SharePoint Site** — `TAX_INVOICE_SITE_NAME`, `TAX_INVOICE_SITE_SITE_DOMAIN`,
  `TAX_INVOICE_SITE_SITE_PATH`, `TAX_INVOICE_SITE_CLIENT_ID`, `TAX_INVOICE_SITE_CLIENT_SECRET`,
  `TAX_INVOICE_SITE_TENANT_ID`
- **Source / Destination Paths** — `TAX_INVOICE_TAX_INVOICE_ROOT`,
  `TAX_INVOICE_TAX_INVOICE_INPUT`, `TAX_INVOICE_TAX_INVOICE_OUTPUT`,
  `TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV`, `TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT`
- **Control & Log Paths** — `TAX_INVOICE_CONTROL_ROOT`, `TAX_INVOICE_OCR_PREP_LOG_PATH`,
  `TAX_INVOICE_PAGE_MANIFEST_LOG_PATH`
- **Performance** — `TAX_INVOICE_MAX_CONCURRENT_UPLOADS`
- **Retention** — `TAX_INVOICE_LOG_RETENTION_DAYS` (days; applies to every tax-invoice log — pre-processing,
  page-manifest, tracing, transaction, performance. `-1` disables retention; unset/invalid → 90)
- **Shared** — `VERINT_SITE_*`, `CONTROL_SITE_*`, `GEMINI_COST_PATH`
