# Sentiment QA — Cloud Build & Deployment

CI/CD pipeline and Docker configuration for the Sentiment QA project.

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
docker build -f cloud_build/sentiment_qa/Dockerfile -t sentiment-voice-analysis:latest .

# Run locally
docker run --env-file .env sentiment-voice-analysis:latest

# Run with custom config
docker run --env-file .env sentiment-voice-analysis:latest \
  python main.py --config_path config/sentiment_qa/qa_pipeline_tasks.yml
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
                            qa-vX.X.X-rcX            qa-vX.X.X
```

- **nprd & release**: Same GCP project — build and deploy new images
- **prod**: Different GCP project — promotes images from release artifact registry (no rebuild)

### Deploy Commands

```bash
# Deploy to nprd
gcloud builds submit . \
  --config=cloud_build/sentiment_qa/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=nprd

# Deploy to release
gcloud builds submit . \
  --config=cloud_build/sentiment_qa/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=release,_IMAGE_TAG=qa-v1.0.0-rc1

# Deploy to prod (promotes image from release, no rebuild)
gcloud builds submit . \
  --config=cloud_build/sentiment_qa/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=prod,_IMAGE_TAG=qa-v1.0.0,_SOURCE_IMAGE_TAG=qa-v1.0.0-rc1,_SOURCE_PROJECT_ID=<NPRD_PROJECT_ID>
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

The main QA pipeline (`qa_pipeline_tasks.yml`) runs 5 tasks in order:

| #   | Task                            | Description                                          |
| --- | ------------------------------- | ---------------------------------------------------- |
| 1   | **QAGetBatchResultTask**        | Poll & download predictions from completed batch     |
| 2   | **QAExportOutputResultTask**    | Score results, generate Excel report, upload to SP   |
| 3   | **QAUploadVoiceTask**           | Download voice files from SharePoint → Upload to GCS |
| 4   | **QAPrepPayloadTask**           | Create JSONL payload with system/user prompts        |
| 5   | **QAExecuteBatchJobTask**       | Submit batch job to Gemini Batch API                 |

The fact-check pipeline (`qa_pipeline_fact_check.yml`) runs `QAFactCheckTask` standalone — validating model predictions against a labelled ground truth dataset and producing precision/recall/F1 reports. Deployed as a separate Cloud Run job `{env}-sentiment-qa-fact-check-job`, triggered monthly on the 1st & 2nd at 9 PM.

The user-playground pipeline (`qa_pipeline_user_playground.yml`) runs `QAUserPlaygroundTask` standalone for ad-hoc operator runs from a SharePoint Control input folder. Deployed as a separate Cloud Run job `{env}-sentiment-qa-user-playground-job` that is triggered on-demand (no Cloud Scheduler).

### Execution Timeline

```
Day 1 — Prepare & Submit:
  06:00 AM  Cloud Scheduler triggers job
  06:01 AM  Download ~150 voice files from SharePoint
  06:05 AM  Upload to GCS (10 concurrent)
  06:08 AM  Create JSONL payload, submit Gemini batch
  06:10 AM  Job complete — batch processes asynchronously

Day 2 — Retrieve & Report:
  06:00 AM  Check batch status → SUCCEEDED
  06:03 AM  Download predictions, validate, score
  06:05 AM  Generate Excel, upload to SharePoint
  06:07 AM  Archive processed files, job complete
```

---

## 🔧 Environment Variables

Required secrets (injected via Secret Manager by Terraform):

<details>
<summary>Application Configuration (7 vars)</summary>

- `ENVIRONMENT`, `QA_GCP_PROJECT_ID`, `QA_GCP_PROJECT_NAME`
- `QA_VERTEX_AI_MODEL_NAME`, `QA_VERTEX_AI_LOCATION`
- `QA_PROCESSING_BUCKET`, `LOG_LEVEL`
</details>

<details>
<summary>Verint Integration (10 vars)</summary>

- `VERINT_SITE_NAME`, `VERINT_SITE_CLIENT_ID`, `VERINT_SITE_CLIENT_SECRET`
- `VERINT_SITE_TENANT_ID`, `VERINT_SITE_SITE_DOMAIN`, `VERINT_SITE_SITE_PATH`
- `QA_VERINT_ROOT`, `QA_VERINT_PRODUCTS`, `QA_VERINT_OUTPUT`
- `QA_MASTER_PATH`
</details>

<details>
<summary>Control Site Integration (8 vars)</summary>

- `CONTROL_SITE_NAME`, `CONTROL_SITE_CLIENT_ID`, `CONTROL_SITE_CLIENT_SECRET`
- `CONTROL_SITE_TENANT_ID`, `CONTROL_SITE_SITE_DOMAIN`, `CONTROL_SITE_SITE_PATH`
- `QA_CONTROL_ROOT`, `QA_CONTROL_FILE_PATH`
</details>

<details>
<summary>Sandbox Site Integration (6 vars)</summary>

- `SANDBOX_SITE_NAME`, `SANDBOX_SITE_CLIENT_ID`, `SANDBOX_SITE_CLIENT_SECRET`
- `SANDBOX_SITE_TENANT_ID`, `SANDBOX_SITE_SITE_DOMAIN`, `SANDBOX_SITE_SITE_PATH`
</details>

<details>
<summary>Paths, Configs & Performance (15 vars)</summary>

- `GEMINI_COST_PATH`, `QA_USER_PROMPT_PATH`
- `QA_TRANSACTION_LOG_PATH`, `QA_PERFORMANCE_LOG_PATH`
- `QA_BATCH_PROCESSING_LOG_PATH`
- `QA_FACT_CHECK_PATH`, `QA_FACT_CHECK_PRODUCTS`
- `QA_USER_PLAYGROUND_PATH`
- `QA_LOOKBACK_DAYS`, `QA_BATCH_SIZE`, `QA_MAX_CONCURRENT_UPLOADS`
- `BOT_EMAIL`, `USER_EMAIL`, `OPER_EMAIL`, `DEV_EMAIL`
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
| **Check List**                     | Campaign-specific binary flags                                                       | True/False/None      |
| **Support Detail**                 | Evidence-based reasoning with direct quotes                                           | Free text (max 800)  |
| **Campaign Ratio**                 | Breakdown of main vs. other topics (must sum to 1.0)                                  | Float                |
| **Sell Performance**               | package offered/accepted, upsell/cross-sell offered/accepted                          | True/False/None      |
| **Agent Strength/Weakness**        | Performance summary with behavioral examples                                         | Free text (max 800)  |
