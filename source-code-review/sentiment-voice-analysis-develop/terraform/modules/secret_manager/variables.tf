variable "project_id" {
  description = "GCP project ID where the secret will be created"
  type        = string
}

variable "secret_id" {
  description = "ID of the Secret Manager secret"
  type        = string
}

variable "labels" {
  description = "Labels to apply to the secret (key-value pairs)"
  type        = map(string)
  default     = {}
}

variable "annotations" {
  description = "Annotations to apply to the secret (key-value pairs)"
  type        = map(string)
  default     = {}
}

variable "replication_type" {
  description = "Replication strategy: 'auto' or 'user_managed'"
  type        = string
  default     = "auto"
  validation {
    condition     = contains(["auto", "user_managed"], var.replication_type)
    error_message = "replication_type must be either 'auto' or 'user_managed'"
  }
}

# TTL and Expiration
variable "ttl" {
  description = "Time-to-live in seconds. Secret will be deleted after this period. Conflicts with expire_time."
  type        = string
  default     = null
}

variable "expire_time" {
  description = "Timestamp when secret expires (RFC3339 format). Conflicts with ttl."
  type        = string
  default     = null
}

variable "version_destroy_ttl" {
  description = "Secret version destroy TTL duration (e.g., '86400s' for 1 day). Must be null or >= 86400s."
  type        = string
  default     = null
}

# Rotation
variable "rotation_period" {
  description = "Rotation period in seconds (e.g., '2592000s' for 30 days)"
  type        = string
  default     = null
}

variable "next_rotation_time" {
  description = "Timestamp for next rotation (RFC3339 format)"
  type        = string
  default     = null
}

# Topics for notifications
variable "topics" {
  description = "List of Pub/Sub topic names for secret notifications"
  type        = list(string)
  default     = []
}

# Version aliases
variable "version_aliases" {
  description = "Map of alias names to version numbers"
  type        = map(string)
  default     = {}
}

# Auto replication with optional CMEK
variable "auto_cmek_key_name" {
  description = "KMS key name for auto replication CMEK (full resource name)"
  type        = string
  default     = null
}

# User-managed replication with replicas
variable "replicas" {
  description = "List of replica configurations with location and optional KMS key"
  type = list(object({
    location     = string
    kms_key_name = optional(string)
  }))
  default = []
}