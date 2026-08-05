variable "location" {
  description = "Required. The location for the resource (e.g., 'us-central1')."
  type        = string
}

variable "name" {
  description = "Required. The resource name of the config. Must be in the format `projects/{project}/locations/{location}/googleChannelConfig`. Typically set to 'googleChannelConfig'."
  type        = string
  default     = "googleChannelConfig"
}

variable "crypto_key_name" {
  description = "Optional. Resource name of a KMS crypto key used to encrypt/decrypt event data. Must match `projects/*/locations/*/keyRings/*/cryptoKeys/*`."
  type        = string
  default     = null
}

variable "project" {
  description = "Optional. The ID of the GCP project in which the resource belongs. Defaults to the provider project if not set."
  type        = string
  default     = null
}
