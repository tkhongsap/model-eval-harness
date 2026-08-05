# Sentiment Telesale — Cloud Build & Deployment

CI/CD pipeline and Docker configuration for the Sentiment Telesale project.

## 📁 Files

| File                      | Purpose                                |
| ------------------------- | -------------------------------------- |
| `Dockerfile`              | Multi-stage build (builder + runtime)  |
| `Dockerfile.dockerignore` | Docker-specific ignores                |
| `wf_deployment.yml`       | Full deployment pipeline (Cloud Build) |
| `.env.example`            | Project-specific environment template  |

---

## 🐳 Dockerfile

Multi-stage build using `python:3.11.9-slim`:

1. **Builder Stage** — Installs dependencies with UV (`uv sync --frozen`)
2. **Runtime Stage** — Copies `.venv`, application source, runs as non-root `appuser`

**Included in image:** `main.py`, `src/`, `tasks/`, `config/`, `resources/`
**Excluded:** `terraform/`, `tests/`, `cloud_build/`, `.git/`, `.env`

### Build & Run Locally

```bash
# Build
docker build -f cloud_build/sentiment_telesale/Dockerfile -t sentiment-voice-analysis:latest .

# Run locally
docker run --env-file .env sentiment-voice-analysis:latest

# Run with custom config
docker run --env-file .env sentiment-voice-analysis:latest \
  python main.py --config_path config/sentiment_telesale/telesale_pipeline_tasks.yml
```

---

## 🚀 Deployment Pipeline (`wf_deployment.yml`)

The Cloud Build workflow handles:

1. **Environment Configuration** — Validates `_ENVIRONMENT` (nprd/release/prod)
2. **Docker Build & Push** — Builds image, pushes to Artifact Registry (skipped for prod)
3. **Image Promotion** — For prod: copies image from release registry (no rebuild)
4. **Terraform Init** — Initializes backend with GCS state bucket
5. **Terraform Plan/Apply** — Creates/updates infrastructure

### Image Promotion Strategy

```
[nprd env]  ──build──▶  [release env]  ──build──▶  [prod env]
   Build                    Build                    Copy only
   Deploy                   Deploy                   (no rebuild)
                            telesale-vX.X.X-rcX      telesale-vX.X.X
```

- **nprd & release**: Same GCP project — build and deploy new images
- **prod**: Different GCP project — promotes images from release artifact registry (no rebuild)

### Deploy Commands

```bash
# Deploy to nprd
gcloud builds submit . \
  --config=cloud_build/sentiment_telesale/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=nprd

# Deploy to release
gcloud builds submit . \
  --config=cloud_build/sentiment_telesale/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=release,_IMAGE_TAG=telesale-v1.0.0-rc1

# Deploy to prod (promotes image from release, no rebuild)
gcloud builds submit . \
  --config=cloud_build/sentiment_telesale/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=prod,_IMAGE_TAG=telesale-v1.0.0,_SOURCE_IMAGE_TAG=telesale-v1.0.0-rc1,_SOURCE_PROJECT_ID=<NPRD_PROJECT_ID>
```

### Substitution Variables

| Variable             | Default           | Description                                                       |
| -------------------- | ----------------- | ----------------------------------------------------------------- |
| `_ENVIRONMENT`       | `nprd`            | Target environment: `nprd`, `release`, `prod`                     |
| `_IMAGE_TAG`         | `latest`          | Docker image tag (overridden by git tag if triggered)             |
| `_REGION`            | `asia-southeast1` | GCP region                                                        |
| `_SOURCE_PROJECT_ID` | —                 | Source project for prod image promotion                           |
| `_SOURCE_IMAGE_TAG`  | —                 | Source image tag for prod promotion (e.g., `telesale-v1.0.0-rc1`) |
| `_TERRAFORM_ACTION`  | `apply`           | `plan` or `apply`                                                 |
| `_AUTO_APPROVE`      | `true`            | Auto-approve terraform apply                                      |

---

## 📋 Pipeline Tasks

The main pipeline (`telesale_pipeline_tasks.yml`) runs 6 tasks in order:

| #   | Task                               | Description                                          |
| --- | ---------------------------------- | ---------------------------------------------------- |
| 1   | **TelesaleGetBatchResultTask**     | Download predictions from completed batch            |
| 2   | **TelesalePrepResultTask**         | Score predictions using `telesale_scoring.yml`       |
| 3   | **TelesaleExportOutputResultTask** | Export results to SharePoint, archive GCS files      |
| 4   | **TelesaleUploadVoiceTask**        | Download voice files from SharePoint → Upload to GCS |
| 5   | **TelesalePrepPayloadTask**        | Create JSONL payload with system/user prompts        |
| 6   | **TelesaleExecuteBatchJobTask**    | Submit batch job to Gemini Batch API                 |

Additional pipelines: `telesale_pipeline_evaluate.yml` adds task 7 (`TelesaleEvaluationOutputTask`) for evaluation reports. `telesale_pipeline_fact_check.yml` runs `TelesaleFactCheckTask` as a standalone ground-truth validation pipeline (deployed as a separate Cloud Run job `{env}-sentiment-telesale-fact-check-job` triggered monthly on the 1st & 2nd at 9 PM). See [tasks/sentiment_telesale/README.md](../../tasks/sentiment_telesale/README.md) for full task documentation.

### Execution Timeline

```
Day 1 — Prepare & Submit:
  09:00 AM  Cloud Scheduler triggers job
  09:01 AM  Download ~150 voice files from SharePoint
  09:05 AM  Upload to GCS (10 concurrent)
  09:08 AM  Create JSONL payload, submit Gemini batch
  09:10 AM  Job complete — batch processes asynchronously

Day 2 — Retrieve & Report:
  09:00 AM  Check batch status → SUCCEEDED
  09:03 AM  Download predictions, validate, score
  09:05 AM  Generate Excel, upload to SharePoint
  09:07 AM  Archive processed files, job complete
```

---

## 🔧 Environment Variables

Required secrets (injected via Secret Manager by Terraform):

<details>
<summary>Application Configuration (7 vars)</summary>

- `ENVIRONMENT`, `TELESALE_GCP_PROJECT_ID`, `TELESALE_GCP_PROJECT_NAME`
- `TELESALE_VERTEX_AI_MODEL_NAME`, `TELESALE_VERTEX_AI_LOCATION`
- `TELESALE_PROCESSING_BUCKET`, `LOG_LEVEL`
</details>

<details>
<summary>Verint Integration (10 vars)</summary>

- `VERINT_SITE_NAME`, `VERINT_SITE_CLIENT_ID`, `VERINT_SITE_CLIENT_SECRET`
- `VERINT_SITE_TENANT_ID`, `VERINT_SITE_SITE_DOMAIN`, `VERINT_SITE_SITE_PATH`
- `TELESALE_VERINT_ROOT`, `TELESALE_VERINT_INPUT`, `TELESALE_VERINT_OUTPUT`
- `TELESALE_MASTER_PATH`
</details>

<details>
<summary>Control Site Integration (8 vars)</summary>

- `CONTROL_SITE_NAME`, `CONTROL_SITE_CLIENT_ID`, `CONTROL_SITE_CLIENT_SECRET`
- `CONTROL_SITE_TENANT_ID`, `CONTROL_SITE_SITE_DOMAIN`, `CONTROL_SITE_SITE_PATH`
- `TELESALE_CONTROL_ROOT`, `TELESALE_CONTROL_FILE_PATH`
</details>

<details>
<summary>Paths, Configs & Performance (10 vars)</summary>

- `GEMINI_COST_PATH`, `TELESALE_USER_PROMPT_PATH`
- `TELESALE_TRANSACTION_LOG_PATH`, `TELESALE_PERFORMANCE_LOG_PATH`
- `TELESALE_BATCH_PROCESSING_LOG_PATH`, `TELESALE_RAW_PREDICTION_PATH`
- `TELESALE_FACT_CHECK_PATH`
- `TELESALE_LOOKBACK_DAYS`, `TELESALE_BATCH_SIZE`, `TELESALE_MAX_CONCURRENT_UPLOADS`
</details>

<details>
<summary>Monitoring (1 var)</summary>

- `IS_MONITORING_ENABLED`
</details>

---

## 📊 Output Data Structure

### Pydantic Validation Models (80+ Fields)

| Category                           | Key Fields                                                            | Format               |
| ---------------------------------- | --------------------------------------------------------------------- | -------------------- |
| **Operations & Professionalism**   | call opening, customer ID verification, language/tone, active listening, call closing | True/False/None      |
| **Sales Effectiveness**            | needs analysis, offer presentation, objection handling, closing, cross-sell/upsell    | True/False/None      |
| **Customer Experience**            | positive experience, clarity of communication, building trust                         | True/False/None      |
| **Compliance**                     | data privacy, sales integrity, professional conduct                                  | True/False/None      |
| **Check List**                     | Campaign-specific binary flags (active tags from check list Excel)                    | True/False/None      |
| **Support Detail**                 | Evidence-based reasoning with direct quotes                                           | Free text (max 800)  |
| **Campaign Ratio**                 | Breakdown of main vs. other topics (must sum to 1.0)                                  | Float                |
| **Sales Performance**              | package offered/accepted, upsell/cross-sell offered/accepted                          | True/False/None      |
| **Customer Insight**               | rejection reason, network issue, churn risk (0–100), sentiment                        | Mixed                |

See [tasks/sentiment_telesale/README.md](../../tasks/sentiment_telesale/README.md) for full output validation documentation.
