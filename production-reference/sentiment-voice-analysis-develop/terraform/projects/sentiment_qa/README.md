# Sentiment QA - Terraform Infrastructure

This directory contains Terraform configurations for the Voice Sentiment Analysis for QA project.

## 🏗️ Architecture Overview

### Multi-Environment Setup

This project uses **2 GCP Projects** for 3 environments:

| Environment | GCP Project         | Purpose                                       |
| ----------- | ------------------- | --------------------------------------------- |
| **nprd**    | `<NPRD_PROJECT_ID>` | Development/Testing                           |
| **release** | `<NPRD_PROJECT_ID>` | Pre-production/Staging (same project as nprd) |
| **prod**    | `<PROD_PROJECT_ID>` | Production                                    |

All environments use region **`asia-southeast1`**.

### Image Promotion Strategy

```
[nprd env]  ──build──▶  [release env]  ──promote──▶  [prod env]
   Build                    Build                    Copy only
   Deploy                   Deploy                   (no rebuild)
```

- **nprd & release**: Same GCP project — build and deploy new images
- **prod**: Different GCP project — promotes images from release artifact registry (no rebuild)

---

## 📁 Directory Structure

```
terraform/projects/sentiment_qa/
├── main.tf                  # Infrastructure modules (Artifact Registry, GCS, Cloud Run, Scheduler, Secrets)
├── locals.tf                # Local variables & secret names list (47 secrets)
├── variables.tf             # Input variable declarations
├── provider.tf              # Terraform & Google provider version pins, GCS backend
├── outputs.tf               # Output values
├── nprd_config.tfvars       # nprd environment values
├── release_config.tfvars    # release environment values
└── prod_config.tfvars       # prod environment values
```

> All environments share one set of Terraform files. The target environment is selected by passing the appropriate `*_config.tfvars` file.

---

## ⚙️ Version Requirements

| Tool            | Version                |
| --------------- | ---------------------- |
| Terraform       | `= 1.13.3` (exact pin) |
| Google Provider | `7.20.0`               |

---

## 🔧 Input Variables

| Variable                | Default                                          | Description                                                                                 |
| ----------------------- | ------------------------------------------------ | ------------------------------------------------------------------------------------------- |
| `project_name`          | —                                                | Project label name                                                                          |
| `environment`           | —                                                | Environment: `nprd`, `release`, or `prod`                                                   |
| `gcp_project_id`        | —                                                | GCP Project ID                                                                              |
| `gcp_region`            | —                                                | GCP Region                                                                                  |
| `service_account_email` | —                                                | Service account for Cloud Run & Scheduler                                                   |
| `oauth_token_scope`     | `https://www.googleapis.com/auth/cloud-platform` | OAuth scope for Scheduler                                                                   |
| `image_tag`             | `latest`                                         | Docker image tag to deploy                                                                  |
| `image_name`            | —                                                | Docker image name in Artifact Registry                                                      |
| `config_path`           | —                                                | Path to main pipeline YAML config (e.g. `config/sentiment_qa/qa_pipeline_tasks.yml`)        |
| `fact_check_path`       | —                                                | Path to fact-check pipeline YAML config (e.g. `config/sentiment_qa/qa_pipeline_fact_check.yml`) |
| `user_playground_path`  | —                                                | Path to user-playground pipeline YAML config (e.g. `config/sentiment_qa/qa_pipeline_user_playground.yml`) |

---

## 🚀 Deployment

### Via Cloud Build (Recommended)

The full deployment pipeline (build image + Terraform) runs automatically via Cloud Build:

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
  --substitutions=_ENVIRONMENT=release,_IMAGE_TAG=v1.0.0-rc1

# Deploy to prod (promotes image from release, no rebuild)
gcloud builds submit . \
  --config=cloud_build/sentiment_qa/wf_deployment.yml \
  --region=asia-southeast1 \
  --substitutions=_ENVIRONMENT=prod,_IMAGE_TAG=v1.0.0,_SOURCE_PROJECT_ID=<NPRD_PROJECT_ID>
```

### Manual Terraform (Standalone)

For standalone infrastructure changes without a full image build:

```bash
cd terraform/projects/sentiment_qa

# 1. Initialize backend (first time or after provider changes)
terraform init \
  -backend-config="bucket=${PROJECT_ID}-terraform-state" \
  -backend-config="prefix=terraform-state/${ENVIRONMENT}-sentiment-qa"

# 2. Plan changes
terraform plan -var-file=nprd_config.tfvars -var="image_tag=latest"

# 3. Apply changes
terraform apply -var-file=nprd_config.tfvars -var="image_tag=latest"

# 4. View outputs
terraform output
```

Replace `nprd_config.tfvars` with `release_config.tfvars` or `prod_config.tfvars` as needed.

---

## 📊 Resources Created

Each environment creates:

| Resource                          | Name Pattern                                      |
| --------------------------------- | ------------------------------------------------- |
| Artifact Registry                 | `{env}-sentiment-qa-artifact-repo`                |
| Cloud Storage Bucket              | `{env}-sentiment-qa-bucket`                       |
| Cloud Run Job (main)              | `{env}-sentiment-qa-job`                          |
| Cloud Run Job (fact-check)        | `{env}-sentiment-qa-fact-check-job`               |
| Cloud Run Job (user-playground)   | `{env}-sentiment-qa-user-playground-job`          |
| Cloud Scheduler (main)            | `{env}-sentiment-qa-scheduler`                    |
| Cloud Scheduler (fact-check)      | `{env}-sentiment-qa-fact-check-scheduler`         |
| Workflow (main)                   | `{env}-sentiment-qa-workflow`                     |
| Workflow (user-playground)        | `{env}-sentiment-qa-user-playground-workflow`     |
| Eventarc Trigger (main)           | `{env}-sentiment-qa-trigger`                      |
| Eventarc Trigger (user-playground)| `{env}-sentiment-qa-user-playground-trigger`      |
| Secret Manager Secrets            | 47 secrets (see below)                            |

### Cloud Run Job Specs

| Setting     | Main job | Fact-check job | User-playground |
| ----------- | -------- | -------------- | --------------- |
| CPU         | `8`      | `2`            | `8`             |
| Memory      | `16Gi`   | `4Gi`          | `16Gi`          |
| Timeout     | `7200s` (2 hours) | `7200s` (2 hours) | `7200s` (2 hours) |
| Max retries | `0`      | `0`            | `0`             |
| Command     | `python main.py -c <config_path>` | `python main.py -c <fact_check_path>` | `python main.py -c <user_playground_path>` |

### Cloud Storage Bucket

- Soft delete retention: **7 days**
- Lifecycle rule: **Delete objects after 7 days**

### Cloud Scheduler

| Scheduler             | Cron expression  | Description                                |
| --------------------- | ---------------- | ------------------------------------------ |
| Main                  | `0 6 * * *`      | 6:00 AM every day                          |
| Fact-check            | `0 21 1,2 * *`   | 9:00 PM on the 1st and 2nd of each month   |

- Timezone: `Asia/Bangkok`
- The user-playground job has no Cloud Scheduler entry — it is triggered on-demand by the Eventarc/Workflow pair below.

### Event-Driven Automation (Workflows + Eventarc)

| Trigger                            | Watches for                                                                  | Runs                                     |
| ----------------------------------- | ----------------------------------------------------------------------------- | ----------------------------------------- |
| `{env}-sentiment-qa-trigger`        | GCS object create matching `sentiment_qa/output/*/*/*/predictions.jsonl`     | `{env}-sentiment-qa-workflow`             |
| `{env}-sentiment-qa-user-playground-trigger` | GCS object create matching `sentiment_qa/user_playground/output/*/*/predictions.jsonl` | `{env}-sentiment-qa-user-playground-workflow` |

Each workflow waits 120s for the file to commit, then executes the corresponding Cloud Run job (`voice_sentiment_qa_job` / `voice_sentiment_qa_user_playground_job`) with `INPUT_BUCKET` / `INPUT_FILE` container overrides. The main workflow additionally tracks a per-day retry counter in a GCS state file (`sentiment_qa/state_counter/main_process/counter_<date>.json`, max 1 retry) before re-triggering the job.

---

## 🔐 Secret Manager (47 Secrets)

Secrets are **created by Terraform** but **values must be populated separately**. They are injected into the Cloud Run job as environment variables.

### Application Configuration

- `ENVIRONMENT`
- `QA_GCP_PROJECT_ID`
- `QA_GCP_PROJECT_NAME`
- `QA_VERTEX_AI_MODEL_NAME`
- `QA_VERTEX_AI_LOCATION`
- `QA_PROCESSING_BUCKET`
- `LOG_LEVEL`

### Verint Integration

- `VERINT_SITE_NAME`
- `VERINT_SITE_CLIENT_ID`
- `VERINT_SITE_CLIENT_SECRET`
- `VERINT_SITE_TENANT_ID`
- `VERINT_SITE_SITE_DOMAIN`
- `VERINT_SITE_SITE_PATH`
- `QA_VERINT_ROOT`
- `QA_VERINT_PRODUCTS_INBOUND`
- `QA_VERINT_PRODUCTS_OUTBOUND`
- `QA_VERINT_OUTPUT`
- `QA_MASTER_PATH`

### Control Site Integration

- `CONTROL_SITE_NAME`
- `CONTROL_SITE_CLIENT_ID`
- `CONTROL_SITE_CLIENT_SECRET`
- `CONTROL_SITE_TENANT_ID`
- `CONTROL_SITE_SITE_DOMAIN`
- `CONTROL_SITE_SITE_PATH`
- `QA_CONTROL_ROOT`
- `QA_CONTROL_FILE_PATH`

### Sandbox Site Integration

- `SANDBOX_SITE_NAME`
- `SANDBOX_SITE_CLIENT_ID`
- `SANDBOX_SITE_CLIENT_SECRET`
- `SANDBOX_SITE_TENANT_ID`
- `SANDBOX_SITE_SITE_DOMAIN`
- `SANDBOX_SITE_SITE_PATH`

### Paths & Configuration

- `GEMINI_COST_PATH`
- `QA_USER_PROMPT_PATH`
- `QA_TRANSACTION_LOG_PATH`
- `QA_PERFORMANCE_LOG_PATH`
- `QA_BATCH_PROCESSING_LOG_PATH`
- `QA_FACT_CHECK_PATH`
- `QA_FACT_CHECK_PRODUCTS`
- `QA_USER_PLAYGROUND_PATH`

### Performance & Notification Settings

- `QA_LOOKBACK_DAYS`
- `QA_BATCH_SIZE`
- `QA_MAX_CONCURRENT_UPLOADS`
- `BOT_EMAIL`
- `USER_EMAIL`
- `OPER_EMAIL`
- `DEV_EMAIL`

### Adding Secret Values

```bash
# Via gcloud CLI
echo -n "your-value" | gcloud secrets versions add SECRET_NAME \
  --data-file=- \
  --project=PROJECT_ID

# Example
echo -n "nprd" | gcloud secrets versions add ENVIRONMENT \
  --data-file=- \
  --project=<NPRD_PROJECT_ID>
```

---

## 📤 Outputs

After `terraform apply`, the following outputs are available:

| Output                  | Description                             |
| ----------------------- | --------------------------------------- |
| `artifact_registry_id`  | Artifact Registry repository ID         |
| `artifact_registry_name`| Artifact Registry repository name       |
| `artifact_registry_url` | Full URL for docker push/pull           |
| `bucket_name`           | Cloud Storage bucket name               |
| `bucket_url`            | Cloud Storage bucket URL                |
| `cloud_run_job_id`      | Cloud Run job ID                        |
| `cloud_run_job_name`    | Cloud Run job name                      |
| `cloud_run_job_uri`     | Execution URI (used by Scheduler)       |
| `scheduler_job_id`      | Cloud Scheduler job ID                  |
| `scheduler_job_name`    | Cloud Scheduler job name                |
| `scheduler_schedule`    | Cron expression summary                 |
| `secrets_created`       | List of all Secret Manager secret names |
| `environment`           | Deployed environment                    |
| `gcp_project_id`        | GCP project ID                          |
| `gcp_region`            | GCP region                              |

```bash
terraform output
terraform output artifact_registry_url
```

---

## 🚨 Troubleshooting

### `terraform init` fails

Verify the state bucket exists and you have access:

```bash
gsutil ls gs://${PROJECT_ID}-terraform-state/
```

### Secrets not found by Cloud Run

Verify the secret has a version with a value:

```bash
gcloud secrets versions access latest --secret="SECRET_NAME" --project="PROJECT_ID"
```

### Cloud Run job fails to start

Check service account permissions — the SA needs:

- `roles/secretmanager.secretAccessor` on all secrets
- `roles/storage.objectViewer` on the processing bucket
- `roles/aiplatform.user` for Vertex AI

---

## 🛡️ Best Practices

1. **Test in nprd first** — always validate changes before promoting to release/prod
2. **Image promotion** — build in release, promote to prod (no rebuild in prod)
3. **Separate state prefixes** — each environment has an independent state under `terraform-state/{env}-sentiment-qa`
4. **Never commit secret values** — only secret names are in Terraform; values are added via CLI or Console
5. **Exact version pins** — Terraform `= 1.13.3`, Google Provider `7.20.0`

---

## 📚 Additional Resources

- [Cloud Run Jobs Documentation](https://cloud.google.com/run/docs/create-jobs)
- [Cloud Scheduler Documentation](https://cloud.google.com/scheduler/docs)
- [Secret Manager Best Practices](https://cloud.google.com/secret-manager/docs/best-practices)
- [Terraform Google Provider v7.20.0](https://registry.terraform.io/providers/hashicorp/google/7.20.0)
