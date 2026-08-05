module "voice_sentiment_qa_secrets" {
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

module "voice_sentiment_qa_artifact_registry" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "${var.environment}-sentiment-qa-artifact-repo"
  labels        = local.base_labels
  format        = "DOCKER"
}

module "voice_sentiment_qa_bucket" {
  source     = "../../modules/cloud_storage"
  name       = "${var.environment}-sentiment-qa-bucket"
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
      }
    }
  ]
}

module "voice_sentiment_qa_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-sentiment-qa-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels     = local.base_labels

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 0

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-qa-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "8"
        memory = "16Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.config_path}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "QA_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_ID", version = "latest" } } },
      { name = "QA_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "QA_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "QA_PROCESSING_BUCKET", version = "latest" } } },
      { name = "VERINT_SITE_NAME", value_source = { secret_key_ref = { secret = "VERINT_SITE_NAME", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_ID", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "VERINT_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_TENANT_ID", version = "latest" } } },
      { name = "VERINT_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "VERINT_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_PATH", version = "latest" } } },
      { name = "QA_VERINT_ROOT", value_source = { secret_key_ref = { secret = "QA_VERINT_ROOT", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_INBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_INBOUND", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_OUTBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_OUTBOUND", version = "latest" } } },
      { name = "QA_VERINT_OUTPUT", value_source = { secret_key_ref = { secret = "QA_VERINT_OUTPUT", version = "latest" } } },
      { name = "QA_MASTER_PATH", value_source = { secret_key_ref = { secret = "QA_MASTER_PATH", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "QA_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "QA_CONTROL_ROOT", version = "latest" } } },
      { name = "QA_CONTROL_FILE_PATH", value_source = { secret_key_ref = { secret = "QA_CONTROL_FILE_PATH", version = "latest" } } },
      { name = "QA_USER_PROMPT_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PROMPT_PATH", version = "latest" } } },
      { name = "QA_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "QA_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "QA_BATCH_PROCESSING_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_BATCH_PROCESSING_LOG_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PRODUCTS", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PRODUCTS", version = "latest" } } },
      { name = "QA_USER_PLAYGROUND_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PLAYGROUND_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_NAME", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_NAME", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_ID", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "SANDBOX_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_TENANT_ID", version = "latest" } } },
      { name = "QA_LOOKBACK_DAYS", value_source = { secret_key_ref = { secret = "QA_LOOKBACK_DAYS", version = "latest" } } },
      { name = "QA_BATCH_SIZE", value_source = { secret_key_ref = { secret = "QA_BATCH_SIZE", version = "latest" } } },
      { name = "QA_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "QA_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "BOT_EMAIL", value_source = { secret_key_ref = { secret = "BOT_EMAIL", version = "latest" } } },
      { name = "USER_EMAIL", value_source = { secret_key_ref = { secret = "USER_EMAIL", version = "latest" } } },
      { name = "OPER_EMAIL", value_source = { secret_key_ref = { secret = "OPER_EMAIL", version = "latest" } } },
      { name = "DEV_EMAIL", value_source = { secret_key_ref = { secret = "DEV_EMAIL", version = "latest" } } },
    ]
  }]
}

module "voice_sentiment_qa_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-sentiment-qa-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_region

  # Scheduler-specific variables
  description = "Schedule to trigger Voice Sentiment Analysis Job for QA every weekday at 6 AM"
  schedule    = "0 6 * * *"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.voice_sentiment_qa_job.cloud_run_uri
    http_method = "POST"
    oauth_token = {
      service_account_email = var.service_account_email
      scope                 = var.oauth_token_scope
    }
  }
}

module "voice_sentiment_qa_fact_check_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-sentiment-qa-fact-check-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels = merge(
    local.base_labels,
    { task = "fact_check" }
  )

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 0

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-qa-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "2"
        memory = "4Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.fact_check_path}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "QA_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_ID", version = "latest" } } },
      { name = "QA_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "QA_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "QA_PROCESSING_BUCKET", version = "latest" } } },
      { name = "VERINT_SITE_NAME", value_source = { secret_key_ref = { secret = "VERINT_SITE_NAME", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_ID", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "VERINT_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_TENANT_ID", version = "latest" } } },
      { name = "VERINT_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "VERINT_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_PATH", version = "latest" } } },
      { name = "QA_VERINT_ROOT", value_source = { secret_key_ref = { secret = "QA_VERINT_ROOT", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_INBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_INBOUND", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_OUTBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_OUTBOUND", version = "latest" } } },
      { name = "QA_VERINT_OUTPUT", value_source = { secret_key_ref = { secret = "QA_VERINT_OUTPUT", version = "latest" } } },
      { name = "QA_MASTER_PATH", value_source = { secret_key_ref = { secret = "QA_MASTER_PATH", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "QA_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "QA_CONTROL_ROOT", version = "latest" } } },
      { name = "QA_CONTROL_FILE_PATH", value_source = { secret_key_ref = { secret = "QA_CONTROL_FILE_PATH", version = "latest" } } },
      { name = "QA_USER_PROMPT_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PROMPT_PATH", version = "latest" } } },
      { name = "QA_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "QA_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "QA_BATCH_PROCESSING_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_BATCH_PROCESSING_LOG_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PRODUCTS", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PRODUCTS", version = "latest" } } },
      { name = "QA_USER_PLAYGROUND_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PLAYGROUND_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_NAME", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_NAME", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_ID", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "SANDBOX_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_TENANT_ID", version = "latest" } } },
      { name = "QA_LOOKBACK_DAYS", value_source = { secret_key_ref = { secret = "QA_LOOKBACK_DAYS", version = "latest" } } },
      { name = "QA_BATCH_SIZE", value_source = { secret_key_ref = { secret = "QA_BATCH_SIZE", version = "latest" } } },
      { name = "QA_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "QA_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "BOT_EMAIL", value_source = { secret_key_ref = { secret = "BOT_EMAIL", version = "latest" } } },
      { name = "USER_EMAIL", value_source = { secret_key_ref = { secret = "USER_EMAIL", version = "latest" } } },
      { name = "OPER_EMAIL", value_source = { secret_key_ref = { secret = "OPER_EMAIL", version = "latest" } } },
      { name = "DEV_EMAIL", value_source = { secret_key_ref = { secret = "DEV_EMAIL", version = "latest" } } },
    ]
  }]
}

module "voice_sentiment_qa_fact_check_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-sentiment-qa-fact-check-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_region

  # Scheduler-specific variables
  description = "Schedule to trigger Voice Sentiment Analysis Job for QA Fact Check every day at 9 PM on the 1st and 2nd of the month"
  schedule    = "0 21 1,2 * *"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.voice_sentiment_qa_fact_check_job.cloud_run_uri
    http_method = "POST"
    oauth_token = {
      service_account_email = var.service_account_email
      scope                 = var.oauth_token_scope
    }
  }
}

module "voice_sentiment_qa_user_playground_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-sentiment-qa-user-playground-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels = merge(
    local.base_labels,
    { task = "user_playground" }
  )

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 0

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-qa-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "8"
        memory = "16Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.user_playground_path}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "QA_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_ID", version = "latest" } } },
      { name = "QA_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "QA_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "QA_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "QA_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "QA_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "QA_PROCESSING_BUCKET", version = "latest" } } },
      { name = "VERINT_SITE_NAME", value_source = { secret_key_ref = { secret = "VERINT_SITE_NAME", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_ID", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "VERINT_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_TENANT_ID", version = "latest" } } },
      { name = "VERINT_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "VERINT_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_PATH", version = "latest" } } },
      { name = "QA_VERINT_ROOT", value_source = { secret_key_ref = { secret = "QA_VERINT_ROOT", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_INBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_INBOUND", version = "latest" } } },
      { name = "QA_VERINT_PRODUCTS_OUTBOUND", value_source = { secret_key_ref = { secret = "QA_VERINT_PRODUCTS_OUTBOUND", version = "latest" } } },
      { name = "QA_VERINT_OUTPUT", value_source = { secret_key_ref = { secret = "QA_VERINT_OUTPUT", version = "latest" } } },
      { name = "QA_MASTER_PATH", value_source = { secret_key_ref = { secret = "QA_MASTER_PATH", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "QA_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "QA_CONTROL_ROOT", version = "latest" } } },
      { name = "QA_CONTROL_FILE_PATH", value_source = { secret_key_ref = { secret = "QA_CONTROL_FILE_PATH", version = "latest" } } },
      { name = "QA_USER_PROMPT_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PROMPT_PATH", version = "latest" } } },
      { name = "QA_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "QA_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "QA_BATCH_PROCESSING_LOG_PATH", value_source = { secret_key_ref = { secret = "QA_BATCH_PROCESSING_LOG_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PATH", version = "latest" } } },
      { name = "QA_FACT_CHECK_PRODUCTS", value_source = { secret_key_ref = { secret = "QA_FACT_CHECK_PRODUCTS", version = "latest" } } },
      { name = "QA_USER_PLAYGROUND_PATH", value_source = { secret_key_ref = { secret = "QA_USER_PLAYGROUND_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_NAME", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_NAME", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "SANDBOX_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_SITE_PATH", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_ID", version = "latest" } } },
      { name = "SANDBOX_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "SANDBOX_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "SANDBOX_SITE_TENANT_ID", version = "latest" } } },
      { name = "QA_LOOKBACK_DAYS", value_source = { secret_key_ref = { secret = "QA_LOOKBACK_DAYS", version = "latest" } } },
      { name = "QA_BATCH_SIZE", value_source = { secret_key_ref = { secret = "QA_BATCH_SIZE", version = "latest" } } },
      { name = "QA_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "QA_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "BOT_EMAIL", value_source = { secret_key_ref = { secret = "BOT_EMAIL", version = "latest" } } },
      { name = "USER_EMAIL", value_source = { secret_key_ref = { secret = "USER_EMAIL", version = "latest" } } },
      { name = "OPER_EMAIL", value_source = { secret_key_ref = { secret = "OPER_EMAIL", version = "latest" } } },
      { name = "DEV_EMAIL", value_source = { secret_key_ref = { secret = "DEV_EMAIL", version = "latest" } } },
    ]
  }]
}

module "voice_sentiment_qa_user_playground_workflow" {
  source  = "../../modules/workflows_workflow"
  name    = "${var.environment}-sentiment-qa-user-playground-workflow"
  project = var.gcp_project_id
  region  = var.gcp_region
  labels  = local.base_labels

  # Ensure your module has a variable to accept the service account email
  service_account = var.service_account_email

  # The source code for the workflow
  source_contents = <<-EOF
    main:
      params: [event]
      steps:
        - init:
            assign:
              - project_id: $${sys.get_env("GOOGLE_CLOUD_PROJECT_ID")}
              - location: "${var.gcp_region}"
              - job_name: "${module.voice_sentiment_qa_user_playground_job.cloud_run_name}"
              - resourceName: $${event.data.protoPayload.resourceName}
              - path_parts: $${text.split(resourceName, "/")}
              - bucket: $${path_parts[3]}
              - file_path: $${text.split(resourceName, "/objects/")[1]}
              - expected_file: "predictions.jsonl"
        
        - debug_log:
            call: sys.log
            args:
              data:
                msg: "Debugging Initial Variables"
                project: $${project_id}
                bucket: $${bucket}
                full_path: $${file_path}
                expected: $${expected_file}

        - check_file_type:
            switch:
              # Ensure the path ends with our target file and isn't just the folder creation
              - condition: $${text.substring(file_path, len(file_path) - len(expected_file), len(file_path)) == expected_file}
                next: wait_for_file_commit
            next: end_skipping

        - wait_for_file_commit:
            call: sys.sleep
            args:
                seconds: 120
            next: run_job

        - run_job:
            call: googleapis.run.v1.namespaces.jobs.run
            args:
                name: $${"namespaces/" + project_id + "/jobs/" + job_name}
                location: $${location}
                body:
                    overrides:
                        containerOverrides:
                            - env:
                                - name: INPUT_BUCKET
                                  value: $${bucket}
                                - name: INPUT_FILE
                                  value: $${file_path}
        - finish:
            return: "Job triggered successfully"

        - end_skipping:
            return: '$${"Skipped: file " + file_path + " is not the expected " + expected_file}'
EOF
}

module "voice_sentiment_qa_playground_trigger" {
  source   = "../../modules/eventarc_trigger"
  name     = "${var.environment}-sentiment-qa-user-playground-trigger"
  project  = var.gcp_project_id
  location = var.gcp_region
  labels   = local.base_labels

  service_account = var.service_account_email

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
      value     = "projects/_/buckets/${module.voice_sentiment_qa_bucket.bucket_name}/objects/sentiment_qa/user_playground/output/*/*/predictions.jsonl"
    }
  ]

  destination = {
    workflow = module.voice_sentiment_qa_user_playground_workflow.id
  }
}

module "voice_sentiment_qa_workflow" {
  source  = "../../modules/workflows_workflow"
  name    = "${var.environment}-sentiment-qa-workflow"
  project = var.gcp_project_id
  region  = var.gcp_region
  labels  = local.base_labels

  # Ensure your module has a variable to accept the service account email
  service_account = var.service_account_email

  # The source code for the workflow
  source_contents = <<-EOF
    main:
      params: [event]
      steps:
        - init:
            assign:
              - project_id: $${sys.get_env("GOOGLE_CLOUD_PROJECT_ID")}
              - location: "${var.gcp_region}"
              - job_name: "${module.voice_sentiment_qa_job.cloud_run_name}"
              - resourceName: $${event.data.protoPayload.resourceName}
              - path_parts: $${text.split(resourceName, "/")}
              - bucket: $${path_parts[3]}
              - file_path: $${text.split(resourceName, "/objects/")[1]}
              - expected_file: "predictions.jsonl"
              - max_retries: 1
              - file_segments: $${text.split(file_path, "/")}
              - rfc_date: $${time.format(sys.now(), "Asia/Bangkok")}  # YYYY-MM-DDTHH:MM:SS+07:00
              - current_date: $${text.substring(rfc_date, 0, 10)}     # YYYY-MM-DD
              - state_file_path: $${"sentiment_qa/state_counter/main_process/counter_" + current_date + ".json"}
              - global_error: {}
        
        - debug_log:
            call: sys.log
            args:
              data:
                msg: "Debugging Initial Variables"
                project: $${project_id}
                bucket: $${bucket}
                full_path: $${file_path}
                expected: $${expected_file}
                state_file_path: $${state_file_path}

        - check_file_type:
            switch:
              # Ensure the path ends with our target file and isn't just the folder creation
              - condition: $${text.substring(file_path, len(file_path) - len(expected_file), len(file_path)) == expected_file}
                next: read_state_counter
            next: end_skipping

        - read_state_counter:
            try:
              call: http.get
              args:
                url: $${"https://storage.googleapis.com/storage/v1/b/" + bucket + "/o/" + text.url_encode(state_file_path)}
                query:
                  alt: "media"
                auth:
                  type: OAuth2
              result: state_file_content
            except:
              as: e
              steps:
                - capture_error_globally:
                    assign:
                      - global_error: $${e}
                - handle_http_error:
                    switch:
                      - condition: $${global_error.code == 404}
                        next: create_first_counter
                    next: handle_other_errors
    
        - assign_stored_counter:
            assign:
              - stored_count: $${int(state_file_content.body.retry_count)}
            next: check_existing_counter_threshold

        - check_existing_counter_threshold:
            switch:
              - condition: $${stored_count >= max_retries}
                next: exceed_max_retry
            next: increment_existing_counter

        - increment_existing_counter:
            assign:
              - current_count: $${stored_count + 1}
            next: update_gcs_state
    
        - create_first_counter:
            assign:
              - current_count: 1
            next: update_gcs_state
    
        - update_gcs_state:
            call: http.post
            args:
              url: $${"https://storage.googleapis.com/upload/storage/v1/b/" + bucket + "/o"}
              query:
                uploadType: "media"
                name: $${state_file_path}
              headers:
                Content-Type: "application/json"
              body:
                retry_count: $${current_count}
              auth:
                type: OAuth2
            next: wait_for_file_commit

        - wait_for_file_commit:
            call: sys.sleep
            args:
                seconds: 120
            next: run_job

        - run_job:
            call: googleapis.run.v1.namespaces.jobs.run
            args:
                name: $${"namespaces/" + project_id + "/jobs/" + job_name}
                location: $${location}
                body:
                    overrides:
                        containerOverrides:
                            - env:
                                - name: INPUT_BUCKET
                                  value: $${bucket}
                                - name: INPUT_FILE
                                  value: $${file_path}
        - finish:
            return: "Job triggered successfully"

        - end_skipping:
            return: '$${"Skipped: file " + file_path + " is not the expected " + expected_file}'

        - handle_other_errors:
            call: sys.log
            args:
              text: '$${"Unexpected GCS error occurred: " + global_error.message}'
              severity: "CRITICAL"
            next: raise_other_errors

        - raise_other_errors:
            raise: $${global_error}
    
        - exceed_max_retry:
            return: '$${"counter exceed limit: " + string(max_retries) + ", Skipped job trigger"}'
EOF
}

module "voice_sentiment_qa_trigger" {
  source   = "../../modules/eventarc_trigger"
  name     = "${var.environment}-sentiment-qa-trigger"
  project  = var.gcp_project_id
  location = var.gcp_region
  labels   = local.base_labels

  service_account = var.service_account_email

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
      value     = "projects/_/buckets/${module.voice_sentiment_qa_bucket.bucket_name}/objects/sentiment_qa/output/*/*/*/predictions.jsonl"
    }
  ]

  destination = {
    workflow = module.voice_sentiment_qa_workflow.id
  }
}