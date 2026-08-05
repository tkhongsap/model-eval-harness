# Basic Configuration
variable "name" {
  description = "Name of the Cloud Scheduler job"
  type        = string
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region for the scheduler job"
  type        = string
}

variable "description" {
  description = "Description of the scheduler job"
  type        = string
  default     = ""
}

variable "schedule" {
  description = "Cron schedule expression (e.g., '0 9 * * *' for daily at 9 AM). Required unless the job is paused."
  type        = string
  default     = null
}

variable "time_zone" {
  description = "Time zone for the schedule (IANA format, e.g., 'Asia/Bangkok')"
  type        = string
  default     = "UTC"
}

variable "paused" {
  description = "Whether the scheduler job is paused"
  type        = bool
  default     = false
}

variable "attempt_deadline" {
  description = "Deadline for job execution attempts (e.g., '320s')"
  type        = string
  default     = null
}

# Retry Configuration
variable "retry_config" {
  description = "Retry configuration for failed job attempts"
  type = object({
    retry_count          = optional(number)
    max_retry_duration   = optional(string)
    min_backoff_duration = optional(string)
    max_backoff_duration = optional(string)
    max_doublings        = optional(number)
  })
  default = null
}

# HTTP Target Configuration
variable "http_target" {
  description = "HTTP target configuration. Use for Cloud Run, Cloud Functions, or any HTTP endpoint."
  type = object({
    uri         = string
    http_method = optional(string)
    body        = optional(string)
    headers     = optional(map(string))
    oauth_token = optional(object({
      service_account_email = string
      scope                 = optional(string)
    }))
    oidc_token = optional(object({
      service_account_email = string
      audience              = optional(string)
    }))
  })
  default = null
}

# Pub/Sub Target Configuration
variable "pubsub_target" {
  description = "Pub/Sub target configuration. Use for triggering Pub/Sub topics."
  type = object({
    topic_name = string
    data       = optional(string)
    attributes = optional(map(string))
  })
  default = null
}

# App Engine Target Configuration
variable "app_engine_http_target" {
  description = "App Engine HTTP target configuration. Use for App Engine applications."
  type = object({
    http_method  = optional(string)
    relative_uri = string
    body         = optional(string)
    headers      = optional(map(string))
    app_engine_routing = optional(object({
      service  = optional(string)
      version  = optional(string)
      instance = optional(string)
    }))
  })
  default = null
}