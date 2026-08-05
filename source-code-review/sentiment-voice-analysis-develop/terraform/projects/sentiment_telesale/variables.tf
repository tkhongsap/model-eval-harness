variable "project_name" {
  type        = string
  description = "Name of the project"
}

variable "environment" {
  type        = string
  description = "Environment (nprd, prod, etc.)"
}

variable "gcp_project_id" {
  type        = string
  description = "GCP Project ID"
}

variable "gcp_region" {
  type        = string
  description = "GCP Region"
}

variable "service_account_email" {
  type        = string
  description = "Service account email for Cloud Run and Cloud Scheduler"
}

variable "oauth_token_scope" {
  type        = string
  default     = "https://www.googleapis.com/auth/cloud-platform"
  description = "OAuth token scope for Cloud Scheduler"
}

variable "image_tag" {
  type        = string
  default     = "latest"
  description = "Docker image tag to deploy (e.g., 'latest', commit SHA, or version tag)"
}

variable "image_name" {
  type        = string
  description = "Docker image name in Artifact Registry"
}

variable "config_path" {
  type        = string
  description = "Path to the main configuration file for the application (e.g., config.yaml)"
}

variable "config_path_pre" {
  type        = string
  description = "Path to the pre-tasks configuration file for the application (e.g., config_pre.yaml)"
}

variable "config_path_post" {
  type        = string
  description = "Path to the post-tasks configuration file for the application (e.g., config_post.yaml)"
}

variable "fact_check_path" {
  type        = string
  description = "Path to the fact check configuration file for the application (e.g., fact_check.yaml)"
}
