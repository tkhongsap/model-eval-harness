module "tax_invoice_extraction_secrets" {
  source   = "../../modules/secret_manager"
  for_each = contains(["nprd", "prod"], var.environment) ? toset(local.secret_names) : toset([])

  project_id = var.gcp_project_id
  secret_id  = each.value
  labels     = local.base_labels
}

data "google_secret_manager_secret" "existing" {
  for_each  = contains(["nprd", "prod"], var.environment) ? toset([]) : toset(local.secret_names)
  project   = var.gcp_project_id
  secret_id = each.value
}

module "tax_invoice_extraction_artifact_registry" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "${var.environment}-ai-tax-inv-reconcile-artifact-repo"
  labels        = local.base_labels
  format        = "DOCKER"
}

module "tax_invoice_extraction_bucket" {
  source     = "../../modules/cloud_storage"
  name       = "${var.environment}-tax-invoice-extraction-bucket"
  project_id = var.gcp_project_id
  location   = var.gcp_region
  labels     = local.base_labels

  soft_delete_policy = {
    retention_duration_seconds = 604800 # 7 days
  }

  lifecycle_rules = [
    {
      action = {
        type = "Delete"
      }
      condition = {
        age = 7
        matches_prefix = [
          "ocr_tax_invoice_workflow/ocr_landing/",
          "ocr_tax_invoice_workflow/ocr_processing/",
          "ocr_tax_invoice_workflow/ocr_output/",
          "ocr_tax_invoice_workflow/fact_check/ocr_landing/",
          "ocr_tax_invoice_workflow/fact_check/ocr_processing/",
          "ocr_tax_invoice_workflow/fact_check/ocr_output/"
        ]
      }
    }
  ]
}

module "tax_invoice_extraction_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-tax-invoice-extraction-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels     = local.base_labels

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 3

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-ai-tax-inv-reconcile-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "1"
        memory = "4Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.config_path_pre}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "TAX_INVOICE_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_GCP_PROJECT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "TAX_INVOICE_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "TAX_INVOICE_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PROCESSING_BUCKET", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_CLIENT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_TENANT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_SITE_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ROOT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ROOT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_INPUT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_INPUT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_OUTPUT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_OUTPUT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_Z45_REPORT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_Z45_REPORT", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_CONTROL_ROOT", version = "latest" } } },
      { name = "TAX_INVOICE_CONTROL_EXTRACTION_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_CONTROL_EXTRACTION_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_PAGE_MANIFEST_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PAGE_MANIFEST_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_OCR_PREP_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_OCR_PREP_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_OCR_TRACING_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_OCR_TRACING_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_REJECTED", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_REJECTED", version = "latest" } } },
      { name = "TAX_INVOICE_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_FACT_CHECK_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_SYSTEM_PROMPT_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SYSTEM_PROMPT_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "TAX_INVOICE_LOG_RETENTION_DAYS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_LOG_RETENTION_DAYS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_ID", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "SANDBOX_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_TENANT_ID", version = "latest" } } },
      { name = "BOT_EMAIL", value_source = { secret_key_ref = { secret = "BOT_EMAIL", version = "latest" } } },
      { name = "DEVELOPER_EMAIL", value_source = { secret_key_ref = { secret = "DEVELOPER_EMAIL", version = "latest" } } },
      { name = "USER_EMAIL", value_source = { secret_key_ref = { secret = "USER_EMAIL", version = "latest" } } },
      { name = "OPER_EMAIL", value_source = { secret_key_ref = { secret = "OPER_EMAIL", version = "latest" } } },
    ]
  }]
}

module "tax_invoice_extraction_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-tax-invoice-extraction-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_scheduler_location

  # Scheduler-specific variables
  description = "Schedule to trigger Extraction Tax Invoice Job every Saturday at 09:00 (Asia/Bangkok)"
  schedule    = "0 9 * * 6"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.tax_invoice_extraction_job.cloud_run_uri
    http_method = "POST"
    headers     = { "Content-Type" = "application/json" }
    body = jsonencode({
      overrides = {
        containerOverrides = [{
          args = ["main.py", "-c", "${var.config_path_pre}"]
        }]
      }
    })
    oauth_token = {
      service_account_email = var.service_account_email
      scope                 = var.oauth_token_scope
    }
  }
}

module "tax_invoice_extraction_workflow" {
  source          = "../../modules/workflows_workflow"
  name            = "${var.environment}-tax-invoice-extraction-workflow"
  region          = var.gcp_region
  labels          = local.base_labels
  source_contents = file("../../../config/tax_invoice_extraction/workflows/extraction_tax_invoice_workflow.yaml")

  user_env_vars = {
    PROJECT_ID       = var.gcp_project_id
    LOCATION         = var.gcp_region
    JOB_NAME         = module.tax_invoice_extraction_job.cloud_run_name
    POST_CONFIG_PATH = var.config_path_post
  }
}

module "tax_invoice_extraction_eventarc_trigger" {
  source = "../../modules/eventarc_trigger"

  name            = "${var.environment}-tax-invoice-extraction-eventarc-trigger"
  project         = var.gcp_project_id
  location        = var.gcp_region
  service_account = var.service_account_email
  labels          = local.base_labels

  matching_criteria = [
    {
      attribute = "type"
      value     = "google.cloud.audit.log.v1.written"
    },
    {
      attribute = "serviceName"
      value     = "storage.googleapis.com"
    },
    {
      attribute = "methodName"
      value     = "storage.objects.create"
    },
    {
      attribute = "resourceName"
      operator  = "match-path-pattern"
      value     = "projects/_/buckets/${module.tax_invoice_extraction_bucket.bucket_name}/objects/ocr_tax_invoice_workflow/ocr_output/**/predictions.jsonl"
    }
  ]

  destination = {
    workflow = module.tax_invoice_extraction_workflow.id
  }
}

module "tax_invoice_extraction_fact_check_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-tax-invoice-extraction-fact-check-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels = merge(
    local.base_labels,
    { task = "fact_check" }
  )

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 3

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-ai-tax-inv-reconcile-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "1"
        memory = "4Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.config_fact_check_path_pre}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "TAX_INVOICE_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_GCP_PROJECT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "TAX_INVOICE_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "TAX_INVOICE_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PROCESSING_BUCKET", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_NAME", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_NAME", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_CLIENT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_TENANT_ID", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "TAX_INVOICE_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SITE_SITE_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ROOT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ROOT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_INPUT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_INPUT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_OUTPUT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_OUTPUT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_INV", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_ARCHIVE_VAT", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_MASTER_BUYERS", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_Z45_REPORT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_Z45_REPORT", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "TAX_INVOICE_CONTROL_ROOT", version = "latest" } } },
      { name = "TAX_INVOICE_CONTROL_EXTRACTION_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_CONTROL_EXTRACTION_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_PAGE_MANIFEST_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_PAGE_MANIFEST_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_OCR_PREP_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_OCR_PREP_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_OCR_TRACING_LOG_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_OCR_TRACING_LOG_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_MASTER_VENDORS", version = "latest" } } },
      { name = "TAX_INVOICE_TAX_INVOICE_REJECTED", value_source = { secret_key_ref = { secret = "TAX_INVOICE_TAX_INVOICE_REJECTED", version = "latest" } } },
      { name = "TAX_INVOICE_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_FACT_CHECK_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_SYSTEM_PROMPT_PATH", value_source = { secret_key_ref = { secret = "TAX_INVOICE_SYSTEM_PROMPT_PATH", version = "latest" } } },
      { name = "TAX_INVOICE_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "TAX_INVOICE_LOG_RETENTION_DAYS", value_source = { secret_key_ref = { secret = "TAX_INVOICE_LOG_RETENTION_DAYS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_ID", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "SANDBOX_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_TENANT_ID", version = "latest" } } },
      { name = "BOT_EMAIL", value_source = { secret_key_ref = { secret = "BOT_EMAIL", version = "latest" } } },
      { name = "DEVELOPER_EMAIL", value_source = { secret_key_ref = { secret = "DEVELOPER_EMAIL", version = "latest" } } },
      { name = "USER_EMAIL", value_source = { secret_key_ref = { secret = "USER_EMAIL", version = "latest" } } },
      { name = "OPER_EMAIL", value_source = { secret_key_ref = { secret = "OPER_EMAIL", version = "latest" } } },
    ]
  }]
}

module "tax_invoice_extraction_fact_check_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-tax-invoice-extraction-fact-check-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_scheduler_location

  # Scheduler-specific variables
  description = "Schedule to trigger Extraction Tax Invoice Fact Check Job at 21:00 on the 1st of each month (Asia/Bangkok)"
  schedule    = "0 21 1 * *"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.tax_invoice_extraction_fact_check_job.cloud_run_uri
    http_method = "POST"
    body = jsonencode({
      overrides = {
        containerOverrides = [{
          args = ["main.py", "-c", "${var.config_fact_check_path_pre}"]
        }]
      }
    })
    oauth_token = {
      service_account_email = var.service_account_email
      scope                 = var.oauth_token_scope
    }
  }
}

module "tax_invoice_fact_check_extraction_workflow" {
  source          = "../../modules/workflows_workflow"
  name            = "${var.environment}-tax-invoice-fact-check-extraction-workflow"
  region          = var.gcp_region
  labels          = local.base_labels
  source_contents = file("../../../config/tax_invoice_extraction/workflows/extraction_tax_invoice_fact_check_workflow.yaml")

  user_env_vars = {
    PROJECT_ID       = var.gcp_project_id
    LOCATION         = var.gcp_region
    JOB_NAME         = module.tax_invoice_extraction_fact_check_job.cloud_run_name
    POST_CONFIG_PATH = var.config_fact_check_path_post
  }
}

module "tax_invoice_fact_check_extraction_eventarc_trigger" {
  source = "../../modules/eventarc_trigger"

  name            = "${var.environment}-tax-invoice-fact-check-extraction-eventarc-trigger"
  project         = var.gcp_project_id
  location        = var.gcp_region
  service_account = var.service_account_email
  labels          = local.base_labels

  matching_criteria = [
    {
      attribute = "type"
      value     = "google.cloud.audit.log.v1.written"
    },
    {
      attribute = "serviceName"
      value     = "storage.googleapis.com"
    },
    {
      attribute = "methodName"
      value     = "storage.objects.create"
    },
    {
      attribute = "resourceName"
      operator  = "match-path-pattern"
      value     = "projects/_/buckets/${module.tax_invoice_extraction_bucket.bucket_name}/objects/ocr_tax_invoice_workflow/fact_check/ocr_output/**/predictions.jsonl"
    }
  ]

  destination = {
    workflow = module.tax_invoice_fact_check_extraction_workflow.id
  }
}
