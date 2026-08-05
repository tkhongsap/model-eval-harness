module "voice_sentiment_telesale_secrets" {
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

module "voice_sentiment_telesale_artifact_registry" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = var.gcp_project_id
  location      = var.gcp_region
  repository_id = "${var.environment}-sentiment-telesale-artifact-repo"
  labels        = local.base_labels
  format        = "DOCKER"
}

module "voice_sentiment_telesale_bucket" {
  source     = "../../modules/cloud_storage"
  name       = "${var.environment}-sentiment-telesale-bucket"
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

module "voice_sentiment_telesale_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-sentiment-telesale-job"
  project_id = var.gcp_project_id
  region     = var.gcp_region
  labels     = local.base_labels

  service_account_email = var.service_account_email
  timeout               = "7200s"
  max_retries           = 3

  containers = [{
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-telesale-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "1"
        memory = "4Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.config_path}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "TELESALE_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "TELESALE_GCP_PROJECT_ID", version = "latest" } } },
      { name = "TELESALE_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "TELESALE_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "TELESALE_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "TELESALE_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "TELESALE_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "TELESALE_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "TELESALE_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "TELESALE_PROCESSING_BUCKET", version = "latest" } } },
      { name = "VERINT_SITE_NAME", value_source = { secret_key_ref = { secret = "VERINT_SITE_NAME", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_ID", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "VERINT_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_TENANT_ID", version = "latest" } } },
      { name = "VERINT_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "VERINT_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_PATH", version = "latest" } } },
      { name = "TELESALE_VERINT_ROOT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_ROOT", version = "latest" } } },
      { name = "TELESALE_VERINT_INPUT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_INPUT", version = "latest" } } },
      { name = "TELESALE_VERINT_OUTPUT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_OUTPUT", version = "latest" } } },
      { name = "TELESALE_MASTER_PATH", value_source = { secret_key_ref = { secret = "TELESALE_MASTER_PATH", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "TELESALE_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "TELESALE_CONTROL_ROOT", version = "latest" } } },
      { name = "TELESALE_CONTROL_FILE_PATH", value_source = { secret_key_ref = { secret = "TELESALE_CONTROL_FILE_PATH", version = "latest" } } },
      { name = "TELESALE_USER_PROMPT_PATH", value_source = { secret_key_ref = { secret = "TELESALE_USER_PROMPT_PATH", version = "latest" } } },
      { name = "TELESALE_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_BATCH_PROCESSING_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_BATCH_PROCESSING_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "TELESALE_FACT_CHECK_PATH", version = "latest" } } },
      { name = "TELESALE_LOOKBACK_DAYS", value_source = { secret_key_ref = { secret = "TELESALE_LOOKBACK_DAYS", version = "latest" } } },
      { name = "TELESALE_BATCH_SIZE", value_source = { secret_key_ref = { secret = "TELESALE_BATCH_SIZE", version = "latest" } } },
      { name = "TELESALE_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "TELESALE_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
      { name = "IS_MONITORING_ENABLED", value_source = { secret_key_ref = { secret = "IS_MONITORING_ENABLED", version = "latest" } } },
      { name = "TELESALE_RAW_PREDICTION_PATH", value_source = { secret_key_ref = { secret = "TELESALE_RAW_PREDICTION_PATH", version = "latest" } } },
    ]
  }]
}

module "voice_sentiment_telesale_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-sentiment-telesale-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_region

  # Scheduler-specific variables
  description = "Schedule to trigger Voice Sentiment Analysis Job for Telesale every weekday at 9 AM starting from the 5th of the month"
  schedule    = "0 9 11-31 * *"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.voice_sentiment_telesale_job.cloud_run_uri
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

module "voice_sentiment_telesale_fact_check_job" {
  source     = "../../modules/cloud_run"
  job_name   = "${var.environment}-sentiment-telesale-fact-check-job"
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
    image = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-telesale-artifact-repo/${var.image_name}:${var.image_tag}"
    resources = {
      limits = {
        cpu    = "1"
        memory = "4Gi"
      }
    }
    working_dir = "/app"
    command     = ["python"]
    args        = ["main.py", "-c", "${var.fact_check_path}"]
    env = [
      { name = "ENVIRONMENT", value_source = { secret_key_ref = { secret = "ENVIRONMENT", version = "latest" } } },
      { name = "TELESALE_GCP_PROJECT_ID", value_source = { secret_key_ref = { secret = "TELESALE_GCP_PROJECT_ID", version = "latest" } } },
      { name = "TELESALE_GCP_PROJECT_NAME", value_source = { secret_key_ref = { secret = "TELESALE_GCP_PROJECT_NAME", version = "latest" } } },
      { name = "TELESALE_VERTEX_AI_MODEL_NAME", value_source = { secret_key_ref = { secret = "TELESALE_VERTEX_AI_MODEL_NAME", version = "latest" } } },
      { name = "TELESALE_VERTEX_AI_LOCATION", value_source = { secret_key_ref = { secret = "TELESALE_VERTEX_AI_LOCATION", version = "latest" } } },
      { name = "TELESALE_PROCESSING_BUCKET", value_source = { secret_key_ref = { secret = "TELESALE_PROCESSING_BUCKET", version = "latest" } } },
      { name = "VERINT_SITE_NAME", value_source = { secret_key_ref = { secret = "VERINT_SITE_NAME", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_ID", version = "latest" } } },
      { name = "VERINT_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "VERINT_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "VERINT_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "VERINT_SITE_TENANT_ID", version = "latest" } } },
      { name = "VERINT_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "VERINT_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "VERINT_SITE_SITE_PATH", version = "latest" } } },
      { name = "TELESALE_VERINT_ROOT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_ROOT", version = "latest" } } },
      { name = "TELESALE_VERINT_INPUT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_INPUT", version = "latest" } } },
      { name = "TELESALE_VERINT_OUTPUT", value_source = { secret_key_ref = { secret = "TELESALE_VERINT_OUTPUT", version = "latest" } } },
      { name = "TELESALE_MASTER_PATH", value_source = { secret_key_ref = { secret = "TELESALE_MASTER_PATH", version = "latest" } } },
      { name = "CONTROL_SITE_NAME", value_source = { secret_key_ref = { secret = "CONTROL_SITE_NAME", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_CLIENT_SECRET", value_source = { secret_key_ref = { secret = "CONTROL_SITE_CLIENT_SECRET", version = "latest" } } },
      { name = "CONTROL_SITE_TENANT_ID", value_source = { secret_key_ref = { secret = "CONTROL_SITE_TENANT_ID", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_DOMAIN", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_DOMAIN", version = "latest" } } },
      { name = "CONTROL_SITE_SITE_PATH", value_source = { secret_key_ref = { secret = "CONTROL_SITE_SITE_PATH", version = "latest" } } },
      { name = "GEMINI_COST_PATH", value_source = { secret_key_ref = { secret = "GEMINI_COST_PATH", version = "latest" } } },
      { name = "TELESALE_CONTROL_ROOT", value_source = { secret_key_ref = { secret = "TELESALE_CONTROL_ROOT", version = "latest" } } },
      { name = "TELESALE_CONTROL_FILE_PATH", value_source = { secret_key_ref = { secret = "TELESALE_CONTROL_FILE_PATH", version = "latest" } } },
      { name = "TELESALE_USER_PROMPT_PATH", value_source = { secret_key_ref = { secret = "TELESALE_USER_PROMPT_PATH", version = "latest" } } },
      { name = "TELESALE_TRANSACTION_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_TRANSACTION_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_PERFORMANCE_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_PERFORMANCE_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_BATCH_PROCESSING_LOG_PATH", value_source = { secret_key_ref = { secret = "TELESALE_BATCH_PROCESSING_LOG_PATH", version = "latest" } } },
      { name = "TELESALE_FACT_CHECK_PATH", value_source = { secret_key_ref = { secret = "TELESALE_FACT_CHECK_PATH", version = "latest" } } },
      { name = "TELESALE_LOOKBACK_DAYS", value_source = { secret_key_ref = { secret = "TELESALE_LOOKBACK_DAYS", version = "latest" } } },
      { name = "TELESALE_BATCH_SIZE", value_source = { secret_key_ref = { secret = "TELESALE_BATCH_SIZE", version = "latest" } } },
      { name = "TELESALE_MAX_CONCURRENT_UPLOADS", value_source = { secret_key_ref = { secret = "TELESALE_MAX_CONCURRENT_UPLOADS", version = "latest" } } },
      { name = "LOG_LEVEL", value_source = { secret_key_ref = { secret = "LOG_LEVEL", version = "latest" } } },
    ]
  }]
}

module "voice_sentiment_telesale_fact_check_scheduler" {
  source     = "../../modules/cloud_scheduler"
  name       = "${var.environment}-sentiment-telesale-fact-check-scheduler"
  project_id = var.gcp_project_id
  region     = var.gcp_region

  # Scheduler-specific variables
  description = "Schedule to trigger Voice Sentiment Analysis Job for Telesale Fact Check every day at 9 PM on the 1st and 2nd of the month"
  schedule    = "0 21 1,2 * *"
  time_zone   = "Asia/Bangkok"
  paused      = false

  http_target = {
    uri         = module.voice_sentiment_telesale_fact_check_job.cloud_run_uri
    http_method = "POST"
    oauth_token = {
      service_account_email = var.service_account_email
      scope                 = var.oauth_token_scope
    }
  }
}

module "voice_sentiment_telesale_event_router_workflow" {
  source = "../../modules/workflows_workflow"

  name            = "${var.environment}-sentiment-telesale-event-router"
  project         = var.gcp_project_id
  region          = var.gcp_region
  service_account = var.service_account_email
  source_contents = file("../../../config/sentiment_telesale/workflows/telesale_event_router.yaml")
  user_env_vars = {
    PROJECT_ID       = var.gcp_project_id
    LOCATION         = var.gcp_region
    JOB_NAME         = module.voice_sentiment_telesale_job.cloud_run_name
    POST_CONFIG_PATH = var.config_path_post
  }
  labels = local.base_labels
}

module "voice_sentiment_telesale_predictions_trigger" {
  source = "../../modules/eventarc_trigger"

  name            = "${var.environment}-sentiment-telesale-predictions-trigger"
  project         = var.gcp_project_id
  location        = var.gcp_region
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
      value     = "/projects/_/buckets/${module.voice_sentiment_telesale_bucket.bucket_name}/objects/sentiment_telesale/output/**/predictions.jsonl"
    },
  ]

  destination = {
    workflow = module.voice_sentiment_telesale_event_router_workflow.id
  }

  labels = local.base_labels
}