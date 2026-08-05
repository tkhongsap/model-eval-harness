variable "name" {
  description = "Name of the storage bucket (must be globally unique)"
  type        = string
}

variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "location" {
  description = "Bucket location (region or multi-region)"
  type        = string
  default     = "asia-southeast1"
}

variable "storage_class" {
  description = "Storage class: STANDARD, NEARLINE, COLDLINE, ARCHIVE"
  type        = string
  default     = "STANDARD"
}

variable "force_destroy" {
  description = "Allow bucket deletion even if it contains objects"
  type        = bool
  default     = false
}

variable "public_access_prevention" {
  description = "Prevents public access: 'enforced' or 'inherited'"
  type        = string
  default     = "enforced"
}

variable "uniform_bucket_level_access" {
  description = "Enable uniform bucket-level access (recommended)"
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels to apply to the bucket"
  type        = map(string)
  default     = {}
}

variable "versioning_enabled" {
  description = "Enable object versioning"
  type        = bool
  default     = false
}

variable "kms_key_name" {
  description = "KMS key name for customer-managed encryption"
  type        = string
  default     = null
}

variable "default_event_based_hold" {
  description = "Automatically apply an eventBasedHold to new objects"
  type        = bool
  default     = false
}

variable "enable_object_retention" {
  description = "Enable object retention (object lock) on the bucket"
  type        = bool
  default     = false
}

variable "requester_pays" {
  description = "Enable Requester Pays on the bucket"
  type        = bool
  default     = false
}

variable "rpo" {
  description = "Recovery point objective for cross-region replication: DEFAULT or ASYNC_TURBO (dual-region only)"
  type        = string
  default     = null
}

# Autoclass
variable "autoclass" {
  description = "Autoclass configuration for automatic storage class transitions"
  type = object({
    enabled                = bool
    terminal_storage_class = optional(string)
  })
  default = null
}

# Website
variable "website" {
  description = "Website configuration for static website hosting"
  type = object({
    main_page_suffix = optional(string)
    not_found_page   = optional(string)
  })
  default = null
}

# Custom placement config for dual-region
variable "custom_placement_config" {
  description = "Custom location configuration for dual-region buckets"
  type = object({
    data_locations = list(string)
  })
  default = null
}

# Soft delete policy
variable "soft_delete_policy" {
  description = "Soft delete policy configuration"
  type = object({
    retention_duration_seconds = optional(number)
  })
  default = null
}

# Hierarchical namespace
variable "hierarchical_namespace" {
  description = "Hierarchical namespace configuration for folder support"
  type = object({
    enabled = bool
  })
  default = null
}

# IP filter
variable "ip_filter" {
  description = "IP filtering configuration"
  type = object({
    mode                           = string
    allow_cross_org_vpcs           = optional(bool)
    allow_all_service_agent_access = optional(bool)
    public_network_source = optional(object({
      allowed_ip_cidr_ranges = list(string)
    }))
    vpc_network_sources = optional(list(object({
      network                = string
      allowed_ip_cidr_ranges = list(string)
    })))
  })
  default = null
}

# Lifecycle rules
variable "lifecycle_rules" {
  description = "List of lifecycle rules"
  type = list(object({
    action = object({
      type          = string
      storage_class = optional(string)
    })
    condition = object({
      age                                     = optional(number)
      created_before                          = optional(string)
      with_state                              = optional(string)
      matches_storage_class                   = optional(list(string))
      matches_prefix                          = optional(list(string))
      matches_suffix                          = optional(list(string))
      num_newer_versions                      = optional(number)
      send_num_newer_versions_if_zero         = optional(bool)
      custom_time_before                      = optional(string)
      days_since_custom_time                  = optional(number)
      send_days_since_custom_time_if_zero     = optional(bool)
      days_since_noncurrent_time              = optional(number)
      send_days_since_noncurrent_time_if_zero = optional(bool)
      noncurrent_time_before                  = optional(string)
      send_age_if_zero                        = optional(bool)
    })
  }))
  default = []
}

# CORS rules
variable "cors_rules" {
  description = "CORS configuration rules"
  type = list(object({
    origin          = list(string)
    method          = list(string)
    response_header = optional(list(string))
    max_age_seconds = optional(number)
  }))
  default = []
}

# Logging
variable "logging_config" {
  description = "Logging configuration"
  type = object({
    log_bucket        = string
    log_object_prefix = optional(string)
  })
  default = null
}

# Retention policy
variable "retention_policy" {
  description = "Retention policy configuration"
  type = object({
    retention_period = number
    is_locked        = optional(bool)
  })
  default = null
}

# IAM members
variable "iam_members" {
  description = "Map of IAM members with roles"
  type = map(object({
    role   = string
    member = string
  }))
  default = {}
}

# Objects to upload
variable "objects" {
  description = "Map of objects to upload to the bucket"
  type = map(object({
    source  = optional(string)
    content = optional(string)
  }))
  default = {}
}

# Pub/Sub notification
variable "notification_config" {
  description = "Pub/Sub notification configuration"
  type = object({
    topic          = string
    payload_format = string
    event_types    = optional(list(string))
  })
  default = null
}