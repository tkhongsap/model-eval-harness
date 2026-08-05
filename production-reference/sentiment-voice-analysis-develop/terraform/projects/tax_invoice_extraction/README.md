# Extraction Tax Invoice - Terraform Infrastructure

This directory contains Terraform configurations for the Extraction Tax Invoice project.

## 🏗️ Architecture Overview

### Multi-Environment Setup

This project uses **2 GCP Projects** for 3 environments:

| Environment | GCP Project         | Purpose                                       |
| ----------- | ------------------- | --------------------------------------------- |
| **nprd**    | `<NPRD_PROJECT_ID>` | Development/Testing                           |
| **release** | `<NPRD_PROJECT_ID>` | Pre-production/Staging (same project as nprd) |
| **prod**    | `<PROD_PROJECT_ID>` | Production                                    |

Regions vary by environment (per `*_config.tfvars`): **nprd** uses `asia-southeast3`, with Cloud Scheduler pinned to `asia-southeast1` via the separate `gcp_scheduler_location` variable (Cloud Scheduler isn't available in `asia-southeast3`); **release** and **prod** use `asia-southeast1` for everything.

### Image Promotion Strategy

```
[nprd env]  ──build──▶  [release env]  ──promote──▶  [prod env]
   Build                    Build                    Copy only
   Deploy                   Deploy                   (no rebuild)
```

- **nprd & release**: Same GCP project — build and deploy new images
- **prod**: Different GCP project — promotes images from release artifact registry (no rebuild)

> **Note**: Secrets are only created by Terraform for `nprd` and `prod` environments. The `release` environment reads existing secrets from `nprd`.

---

## 📁 Directory Structure

```
terraform/projects/tax_invoice_extraction/
├── main.tf                  # Infrastructure modules (Artifact Registry, GCS, Cloud Run Job, Scheduler, Workflow, Eventarc Trigger, Secrets)
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

| Variable                | Default                                          | Description                                                                        |
| ----------------------- | ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| `project_name`          | —                                                | Project label name                                                                 |
| `environment`           | —                                                | Environment: `nprd`, `release`, or `prod`                                          |
| `gcp_project_id`        | —                                                | GCP Project ID                                                                     |
| `gcp_region`            | —                                                | GCP Region                                                                         |
| `gcp_scheduler_location`| —                                                | GCP region for Cloud Scheduler                                                     |
| `service_account_email` | —                                                | Service account for Cloud Run & Scheduler                                          |
| `oauth_token_scope`     | `https://www.googleapis.com/auth/cloud-platform` | OAuth scope for Scheduler                                                          |
| `image_tag`             | `latest`                                         | Docker image tag to deploy                                                         |
| `image_name`            | —                                                | Docker image name in Artifact Registry                                             |
| `config_path_pre`       | —                                                | Path to pre-process pipeline config (e.g. `config/tax_invoice_extraction/tax_invoice_pre_tasks.yml`) |
| `config_path_post`      | —                                                | Path to post-process pipeline config (e.g. `config/tax_invoice_extraction/tax_invoice_post_tasks.yml`) |
| `eventarc_log_severity` | `INFO`                                           | Platform-telemetry log severity for Eventarc Advanced resources (DEBUG/INFO/NOTICE/WARNING/ERROR/CRITICAL/ALERT/EMERGENCY) |

> `fact_check_path` and the fact-check Cloud Run job/scheduler modules exist in `main.tf`/`variables.tf` but are commented out and not currently declared/deployed.

---

## 🚀 Deployment

### Via Cloud Build (Recommended)

The full deployment pipeline (build image + Terraform) runs automatically via Cloud Build.

### Manual Terraform (Standalone)

For standalone infrastructure changes without a full image build:

```bash
cd terraform/projects/tax_invoice_extraction

# 1. Initialize backend (first time or after provider changes)
terraform init \
  -backend-config="bucket=${PROJECT_ID}-terraform-state" \
  -backend-config="prefix=terraform-state/${ENVIRONMENT}-tax-invoice-extraction"

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

| Resource               | Name Pattern                                         |
| ---------------------- | ---------------------------------------------------- |
| Artifact Registry      | `{env}-ai-tax-inv-reconcile-artifact-repo`           |
| Cloud Storage Bucket   | `{env}-tax-invoice-extraction-bucket`                |
| Cloud Run Job (main)   | `{env}-tax-invoice-extraction-job`                   |
| Cloud Scheduler (main) | `{env}-tax-invoice-extraction-scheduler`             |
| Workflow               | `{env}-tax-invoice-extraction-workflow`              |
| Eventarc Trigger       | `{env}-tax-invoice-extraction-eventarc-trigger`      |
| Secret Manager Secrets | 47 secrets (see below)                               |

> The fact-check Cloud Run job (`{env}-tax-invoice-extraction-fact-check-job`) and its scheduler (`{env}-tax-invoice-extraction-fact-check-scheduler`) are defined in `main.tf` but fully commented out — they are not currently created.

### Cloud Run Job Specs

| Setting     | Value                                        |
| ----------- | -------------------------------------------- |
| CPU         | `1`                                          |
| Memory      | `4Gi`                                        |
| Timeout     | `7200s` (2 hours)                            |
| Max retries | `3`                                          |
| Command     | `python main.py -c <config_path_pre>`        |

### Cloud Storage Bucket

- Soft delete retention: **7 days**
- Lifecycle rule: **Delete objects under `ocr_workflow/ocr_landing/` and `ocr_workflow/ocr_processing/` after 7 days**

### Cloud Schedulers

| Job          | Schedule         | Description                                              |
| ------------ | ---------------- | -------------------------------------------------------- |
| Main job     | `0 9 * * 6`      | 9:00 AM every Saturday (Asia/Bangkok)                    |

> The fact-check scheduler is commented out in `main.tf` (see note above) and does not currently run.

---

## 🔐 Secret Manager (47 Secrets)

Secrets are **created by Terraform** (for `nprd` and `prod`) but **values must be populated separately**. They are injected into the Cloud Run jobs as environment variables.

### Application Configuration

- `ENVIRONMENT`
- `TAX_INVOICE_GCP_PROJECT_ID`
- `TAX_INVOICE_GCP_PROJECT_NAME`
- `TAX_INVOICE_VERTEX_AI_MODEL_NAME`
- `TAX_INVOICE_VERTEX_AI_LOCATION`
- `TAX_INVOICE_PROCESSING_BUCKET`
- `LOG_LEVEL`

### Tax Invoice Site Integration

- `TAX_INVOICE_SITE_NAME`
- `TAX_INVOICE_SITE_CLIENT_ID`
- `TAX_INVOICE_SITE_CLIENT_SECRET`
- `TAX_INVOICE_SITE_TENANT_ID`
- `TAX_INVOICE_SITE_SITE_DOMAIN`
- `TAX_INVOICE_SITE_SITE_PATH`
- `TAX_INVOICE_TAX_INVOICE_ROOT`
- `TAX_INVOICE_TAX_INVOICE_INPUT`
- `TAX_INVOICE_TAX_INVOICE_OUTPUT`
- `TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV`
- `TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT`
- `TAX_INVOICE_TAX_INVOICE_REJECTED`
- `TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS`
- `TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS`
- `TAX_INVOICE_TAX_INVOICE_Z45_REPORT`

### Control Site Integration

- `CONTROL_SITE_NAME`
- `CONTROL_SITE_CLIENT_ID`
- `CONTROL_SITE_CLIENT_SECRET`
- `CONTROL_SITE_TENANT_ID`
- `CONTROL_SITE_SITE_DOMAIN`
- `CONTROL_SITE_SITE_PATH`
- `TAX_INVOICE_CONTROL_ROOT`
- `TAX_INVOICE_CONTROL_EXTRACTION_PATH`

### Paths & Configuration

- `GEMINI_COST_PATH`
- `TAX_INVOICE_TRANSACTION_LOG_PATH`
- `TAX_INVOICE_PERFORMANCE_LOG_PATH`
- `TAX_INVOICE_PAGE_MANIFEST_LOG_PATH`
- `TAX_INVOICE_OCR_PREP_LOG_PATH`
- `TAX_INVOICE_OCR_TRACING_LOG_PATH`
- `TAX_INVOICE_FACT_CHECK_PATH`
- `TAX_INVOICE_SYSTEM_PROMPT_PATH`

### Sandbox Site & Email Notifications

- `SANDBOX_SITE_CLIENT_ID`
- `SANDBOX_SITE_CLIENT_SECRET`
- `SANDBOX_SITE_TENANT_ID`
- `BOT_EMAIL`
- `DEVELOPER_EMAIL`
- `USER_EMAIL`
- `OPER_EMAIL`

### Performance Settings

- `TAX_INVOICE_MAX_CONCURRENT_UPLOADS`

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

| Output                    | Description                             |
| ------------------------- | --------------------------------------- |
| `artifact_registry_id`    | Artifact Registry repository ID         |
| `artifact_registry_name`  | Artifact Registry repository name       |
| `artifact_registry_url`   | Full URL for docker push/pull           |
| `bucket_name`             | Cloud Storage bucket name               |
| `bucket_url`              | Cloud Storage bucket URL                |
| `cloud_run_job_id`        | Cloud Run main job ID                   |
| `cloud_run_job_name`      | Cloud Run main job name                 |
| `cloud_run_job_uri`       | Execution URI (used by Scheduler)       |
| `scheduler_job_id`        | Cloud Scheduler main job ID             |
| `scheduler_job_name`      | Cloud Scheduler main job name           |
| `scheduler_schedule`      | Cron expression summary                 |
| `secrets_created`         | List of all Secret Manager secret names |
| `environment`             | Deployed environment                    |
| `gcp_project_id`          | GCP project ID                          |
| `gcp_region`              | GCP region                              |
| `eventarc_pipeline_service_account_email` | Service account used by the Tax Invoice Eventarc Pipeline (reuses `var.service_account_email`) |
| `eventarc_trigger_id`     | Resource ID of the Tax Invoice Eventarc Trigger |
| `workflow_id`             | ID of the Workflow orchestrating the Tax Invoice processing pipeline |
| `workflow_name`           | Name of the Workflow orchestrating the Tax Invoice processing pipeline |

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
3. **Separate state prefixes** — each environment has an independent state under `terraform-state/{env}-tax-invoice-extraction`
4. **Never commit secret values** — only secret names are in Terraform; values are added via CLI or Console
5. **Exact version pins** — Terraform `= 1.13.3`, Google Provider `7.20.0`

---

## 📚 Additional Resources

- [Cloud Run Jobs Documentation](https://cloud.google.com/run/docs/create-jobs)
- [Cloud Scheduler Documentation](https://cloud.google.com/scheduler/docs)
- [Secret Manager Best Practices](https://cloud.google.com/secret-manager/docs/best-practices)
- [Terraform Google Provider v7.20.0](https://registry.terraform.io/providers/hashicorp/google/7.20.0)
