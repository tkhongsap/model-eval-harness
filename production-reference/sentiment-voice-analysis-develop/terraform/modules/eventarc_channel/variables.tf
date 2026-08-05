variable "name" {
  description = "The resource name of the channel. Must be unique within the location on the project."
  type        = string
}

variable "location" {
  description = "GCP location for the resource (e.g., `us-central1`)."
  type        = string
}

variable "project" {
  description = "GCP project ID. Defaults to the provider project if not set."
  type        = string
  default     = null
}

variable "third_party_provider" {
  description = "The name of the event provider (e.g. Eventarc SaaS partner) associated with the channel. Format: `projects/{project}/locations/{location}/providers/{provider_id}`."
  type        = string
  default     = null
}

variable "crypto_key_name" {
  description = "Resource name of a KMS crypto key used to encrypt/decrypt event data. Pattern: `projects/*/locations/*/keyRings/*/cryptoKeys/*`."
  type        = string
  default     = null
}

variable "labels" {
  description = "User-defined labels for the channel. Non-authoritative — only manages labels present in configuration."
  type        = map(string)
  default     = {}
}
