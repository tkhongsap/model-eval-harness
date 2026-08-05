# Basic Configuration
variable "name" {
  description = "Required. The resource name of the trigger. Must be unique within the location on the project."
  type        = string
}

variable "matching_criteria" {
  description = "Required. The list of filters that applies to event attributes. Only events that match all the provided filters will be sent to the destination. All triggers MUST provide a filter for the 'type' attribute."
  type = list(object({
    attribute = string
    value     = string
    operator  = optional(string)
  }))
}

variable "destination" {
  description = "Required. Destination specifies where the events should be sent to. Exactly one of cloud_run_service, gke, workflow, or http_endpoint must be set. Use network_config alongside http_endpoint for private connectivity."
  type = object({
    cloud_run_service = optional(object({
      service = string
      path    = optional(string)
      region  = optional(string)
    }))
    gke = optional(object({
      cluster   = string
      location  = string
      namespace = string
      service   = string
      path      = optional(string)
    }))
    workflow = optional(string)
    http_endpoint = optional(object({
      uri = string
    }))
    network_config = optional(object({
      network_attachment = string
    }))
  })
}

variable "location" {
  description = "Required. The location for the resource (e.g., 'us-central1')."
  type        = string
}

variable "service_account" {
  description = "Optional. The IAM service account email associated with the trigger. Used to generate identity tokens when invoking Cloud Run destinations. Must have roles/eventarc.eventReceiver for Audit Log triggers."
  type        = string
  default     = null
}

variable "transport" {
  description = "Optional. Transport intermediary used by Eventarc to deliver messages. Supports a Pub/Sub topic as the intermediary."
  type = object({
    pubsub = optional(object({
      topic = optional(string)
    }))
  })
  default = null
}

variable "labels" {
  description = "Optional. User labels attached to the trigger that can be used to group resources."
  type        = map(string)
  default     = {}
}

variable "channel" {
  description = "Optional. The name of the channel associated with the trigger in 'projects/{project}/locations/{location}/channels/{channel}' format. Required to receive events from Eventarc SaaS partners."
  type        = string
  default     = null
}

variable "event_data_content_type" {
  description = "Optional. MIME type of the CloudEvent data field payload. Defaults to 'application/json' if not set."
  type        = string
  default     = null
}

variable "retry_policy" {
  description = "Optional. The retry policy configuration for the trigger. Can only be set with Cloud Run destinations. The only valid value for max_attempts is 1."
  type = object({
    max_attempts = number
  })
  default = null
}

variable "project" {
  description = "Optional. The ID of the GCP project in which the resource belongs. Defaults to the provider project if not set."
  type        = string
  default     = null
}