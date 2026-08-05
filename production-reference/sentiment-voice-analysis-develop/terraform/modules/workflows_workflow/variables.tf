variable "name" {
  description = "Name of the Workflow. If unset and `name_prefix` is also unset, a random name is generated."
  type        = string
  default     = null
}

variable "name_prefix" {
  description = "Creates a unique name beginning with this prefix. Mutually exclusive with `name` if both are set."
  type        = string
  default     = null
}

variable "region" {
  description = "GCP region for the workflow (e.g., `us-central1`)."
  type        = string
  default     = null
}

variable "project" {
  description = "GCP project ID. Defaults to the provider project if not set."
  type        = string
  default     = null
}

variable "description" {
  description = "Human-readable description of the workflow. Maximum 1000 unicode characters."
  type        = string
  default     = null
}

variable "service_account" {
  description = "Service account email or unique ID that the workflow runs as. If unset, the project's default service account is used. Format: `projects/{project}/serviceAccounts/{account}` or `{account}`."
  type        = string
  default     = null
}

variable "source_contents" {
  description = "Workflow YAML/JSON source code to execute. Maximum size 128 KB. Use `$$` to escape Terraform variable interpolation inside workflow YAML."
  type        = string
  default     = null
}

variable "crypto_key_name" {
  description = "KMS crypto key for encrypting workflow and execution data. Format: `projects/{project}/locations/{location}/keyRings/{keyRing}/cryptoKeys/{cryptoKey}`."
  type        = string
  default     = null
}

variable "call_log_level" {
  description = "Platform logging level for calls during execution. One of: `CALL_LOG_LEVEL_UNSPECIFIED`, `LOG_ALL_CALLS`, `LOG_ERRORS_ONLY`, `LOG_NONE`."
  type        = string
  default     = null

  validation {
    condition = var.call_log_level == null || contains(
      ["CALL_LOG_LEVEL_UNSPECIFIED", "LOG_ALL_CALLS", "LOG_ERRORS_ONLY", "LOG_NONE"],
      var.call_log_level
    )
    error_message = "call_log_level must be one of: CALL_LOG_LEVEL_UNSPECIFIED, LOG_ALL_CALLS, LOG_ERRORS_ONLY, LOG_NONE."
  }
}

variable "execution_history_level" {
  description = "Level of execution history stored for this workflow. One of: `EXECUTION_HISTORY_LEVEL_UNSPECIFIED`, `EXECUTION_HISTORY_BASIC`, `EXECUTION_HISTORY_DETAILED`."
  type        = string
  default     = null

  validation {
    condition = var.execution_history_level == null || contains(
      ["EXECUTION_HISTORY_LEVEL_UNSPECIFIED", "EXECUTION_HISTORY_BASIC", "EXECUTION_HISTORY_DETAILED"],
      var.execution_history_level
    )
    error_message = "execution_history_level must be one of: EXECUTION_HISTORY_LEVEL_UNSPECIFIED, EXECUTION_HISTORY_BASIC, EXECUTION_HISTORY_DETAILED."
  }
}

variable "user_env_vars" {
  description = "User-defined environment variables for this workflow revision (max 20 entries, each value up to 4 KiB). Keys cannot be empty or start with `GOOGLE` or `WORKFLOWS`."
  type        = map(string)
  default     = {}
}

variable "labels" {
  description = "User-defined labels for the workflow. Non-authoritative — only manages labels present in configuration."
  type        = map(string)
  default     = {}
}

variable "tags" {
  description = "Resource manager tags. Keys must be in format `tagKeys/{tag_key_id}`; values in format `tagValues/{tag_value_id}`. Ignored when empty."
  type        = map(string)
  default     = {}
}

variable "deletion_protection" {
  description = "When true, Terraform will refuse to destroy the workflow (terraform destroy/apply will fail). Set to false to allow deletion. Defaults to true."
  type        = bool
  default     = true
}
