variable "message_bus_id" {
  description = "Required. The user-provided ID to be assigned to the MessageBus. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$`."
  type        = string
}

variable "location" {
  description = "Required. The location for the resource (e.g., 'us-central1')."
  type        = string
}

variable "display_name" {
  description = "Optional. Resource display name."
  type        = string
  default     = null
}

variable "crypto_key_name" {
  description = "Optional. Resource name of a KMS crypto key used to encrypt/decrypt event data. Must match `projects/*/locations/*/keyRings/*/cryptoKeys/*`."
  type        = string
  default     = null
}

variable "labels" {
  description = "Optional. Resource labels. Non-authoritative — only manages labels present in configuration."
  type        = map(string)
  default     = {}
}

variable "annotations" {
  description = "Optional. Resource annotations. Non-authoritative — only manages annotations present in configuration."
  type        = map(string)
  default     = {}
}

variable "logging_config" {
  description = "Optional. Configuration for Platform Telemetry logging."
  type = object({
    log_severity = optional(string)
  })
  default = null
}

variable "project" {
  description = "Optional. The ID of the GCP project in which the resource belongs. Defaults to the provider project if not set."
  type        = string
  default     = null
}
