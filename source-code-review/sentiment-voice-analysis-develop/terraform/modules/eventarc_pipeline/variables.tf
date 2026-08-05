variable "pipeline_id" {
  description = "Required. The user-provided ID to be assigned to the Pipeline. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$`."
  type        = string
}

variable "location" {
  description = "Required. The location for the resource (e.g., 'us-central1')."
  type        = string
}

variable "destinations" {
  description = "Required. List of destinations to which messages will be forwarded. Currently, exactly one destination is supported per Pipeline."
  type = list(object({
    workflow    = optional(string)
    message_bus = optional(string)
    topic       = optional(string)

    http_endpoint = optional(object({
      uri                      = string
      message_binding_template = optional(string)
    }))

    network_config = optional(object({
      network_attachment = string
    }))

    authentication_config = optional(object({
      google_oidc = optional(object({
        service_account = string
        audience        = optional(string)
      }))
      oauth_token = optional(object({
        service_account = string
        scope           = optional(string)
      }))
    }))

    output_payload_format = optional(object({
      json = optional(object({}))
      avro = optional(object({
        schema_definition = optional(string)
      }))
      protobuf = optional(object({
        schema_definition = optional(string)
      }))
    }))
  }))
}

variable "display_name" {
  description = "Optional. Display name of resource."
  type        = string
  default     = null
}

variable "crypto_key_name" {
  description = "Optional. Resource name of a KMS crypto key used to encrypt/decrypt event data. Must match `projects/{project}/locations/{location}/keyRings/{keyring}/cryptoKeys/{key}`."
  type        = string
  default     = null
}

variable "labels" {
  description = "Optional. User labels attached to the Pipeline. Non-authoritative — only manages labels present in configuration."
  type        = map(string)
  default     = {}
}

variable "annotations" {
  description = "Optional. User-defined annotations. Non-authoritative — only manages annotations present in configuration."
  type        = map(string)
  default     = {}
}

variable "input_payload_format" {
  description = "Optional. Format of incoming message data. Exactly one of json, avro, or protobuf may be set."
  type = object({
    json = optional(object({}))
    avro = optional(object({
      schema_definition = optional(string)
    }))
    protobuf = optional(object({
      schema_definition = optional(string)
    }))
  })
  default = null
}

variable "retry_policy" {
  description = "Optional. Retry policy configuration for the Pipeline. The pipeline exponentially backs off when the destination is non-responsive or returns a retryable error."
  type = object({
    max_retry_delay = optional(string)
    max_attempts    = optional(number)
    min_retry_delay = optional(string)
  })
  default = null
}

variable "mediations" {
  description = "Optional. List of mediation operations to be performed on the message. Currently, only one Transformation operation is allowed per Pipeline."
  type = list(object({
    transformation = optional(object({
      transformation_template = string
    }))
  }))
  default = []
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
