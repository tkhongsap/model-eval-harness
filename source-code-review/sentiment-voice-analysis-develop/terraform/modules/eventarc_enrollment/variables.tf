variable "enrollment_id" {
  description = "Required. The user-provided ID to be assigned to the Enrollment. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$`."
  type        = string
}

variable "message_bus" {
  description = "Required. Resource name of the message bus identifying the source of the messages. Format: projects/{project}/locations/{location}/messageBuses/{messageBus}."
  type        = string
}

variable "cel_match" {
  description = "Required. A CEL expression identifying which messages this enrollment applies to."
  type        = string
}

variable "destination" {
  description = "Required. Full resource name of the Pipeline that the Enrollment delivers to. Format: projects/{PROJECT_ID}/locations/{region}/pipelines/{PIPELINE_ID}."
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

variable "project" {
  description = "Optional. The ID of the GCP project in which the resource belongs. Defaults to the provider project if not set."
  type        = string
  default     = null
}
