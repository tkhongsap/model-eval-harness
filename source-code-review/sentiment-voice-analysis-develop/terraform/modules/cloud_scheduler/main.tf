resource "google_cloud_scheduler_job" "default" {
  name        = var.name
  project     = var.project_id
  region      = var.region
  description = var.description
  schedule    = var.schedule
  time_zone   = var.time_zone
  paused      = var.paused

  attempt_deadline = var.attempt_deadline

  # Retry configuration
  dynamic "retry_config" {
    for_each = var.retry_config != null ? [1] : []
    content {
      retry_count          = lookup(var.retry_config, "retry_count", null)
      max_retry_duration   = lookup(var.retry_config, "max_retry_duration", null)
      min_backoff_duration = lookup(var.retry_config, "min_backoff_duration", null)
      max_backoff_duration = lookup(var.retry_config, "max_backoff_duration", null)
      max_doublings        = lookup(var.retry_config, "max_doublings", null)
    }
  }

  # HTTP target (for Cloud Run, Cloud Functions, HTTP endpoints)
  dynamic "http_target" {
    for_each = var.http_target != null ? [1] : []
    content {
      uri         = var.http_target.uri
      http_method = lookup(var.http_target, "http_method", null)
      body        = lookup(var.http_target, "body", null) != null ? base64encode(var.http_target.body) : null
      headers     = lookup(var.http_target, "headers", null)

      # OAuth token authentication
      dynamic "oauth_token" {
        for_each = lookup(var.http_target, "oauth_token", null) != null ? [1] : []
        content {
          service_account_email = var.http_target.oauth_token.service_account_email
          scope                 = lookup(var.http_target.oauth_token, "scope", null)
        }
      }

      # OIDC token authentication
      dynamic "oidc_token" {
        for_each = lookup(var.http_target, "oidc_token", null) != null ? [1] : []
        content {
          service_account_email = var.http_target.oidc_token.service_account_email
          audience              = lookup(var.http_target.oidc_token, "audience", null)
        }
      }
    }
  }

  # Pub/Sub target
  dynamic "pubsub_target" {
    for_each = var.pubsub_target != null ? [1] : []
    content {
      topic_name = var.pubsub_target.topic_name
      data       = lookup(var.pubsub_target, "data", null) != null ? base64encode(var.pubsub_target.data) : null
      attributes = lookup(var.pubsub_target, "attributes", null)
    }
  }

  # App Engine HTTP target
  dynamic "app_engine_http_target" {
    for_each = var.app_engine_http_target != null ? [1] : []
    content {
      http_method  = lookup(var.app_engine_http_target, "http_method", null)
      relative_uri = var.app_engine_http_target.relative_uri
      body         = lookup(var.app_engine_http_target, "body", null) != null ? base64encode(var.app_engine_http_target.body) : null
      headers      = lookup(var.app_engine_http_target, "headers", null)

      # App Engine routing
      dynamic "app_engine_routing" {
        for_each = lookup(var.app_engine_http_target, "app_engine_routing", null) != null ? [1] : []
        content {
          service  = lookup(var.app_engine_http_target.app_engine_routing, "service", null)
          version  = lookup(var.app_engine_http_target.app_engine_routing, "version", null)
          instance = lookup(var.app_engine_http_target.app_engine_routing, "instance", null)
        }
      }
    }
  }
}
