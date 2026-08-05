# ==============================================================================
# Basic Configuration
# ==============================================================================

variable "job_name" {
  type        = string
  description = "Name of the Cloud Run Job"
}

variable "project_id" {
  type        = string
  description = "GCP Project ID where the Cloud Run Job will be deployed"
}

variable "region" {
  type        = string
  description = "Region where the Cloud Run Job will be deployed"
}

variable "deletion_protection" {
  type        = bool
  default     = false
  description = "Whether to enable deletion protection for the Cloud Run Job"
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Labels to apply to the Cloud Run Job (key-value pairs)"
}

variable "annotations" {
  type        = map(string)
  default     = {}
  description = "Unstructured key value map for job-level annotations"
}

variable "client" {
  type        = string
  default     = null
  description = "Arbitrary identifier for the API client"
}

variable "client_version" {
  type        = string
  default     = null
  description = "Arbitrary version identifier for the API client"
}

variable "launch_stage" {
  type        = string
  default     = null
  description = "Launch stage (ALPHA, BETA, GA, etc.)"
}

# ==============================================================================
# Template Configuration
# ==============================================================================

variable "template_labels" {
  type        = map(string)
  default     = {}
  description = "Labels to apply to the execution template"
}

variable "template_annotations" {
  type        = map(string)
  default     = {}
  description = "Annotations to apply to the execution template"
}

variable "parallelism" {
  type        = number
  default     = null
  description = "Maximum number of tasks to run in parallel"
}

variable "task_count" {
  type        = number
  default     = 1
  description = "Number of tasks to execute"
}

# ==============================================================================
# Task Template Configuration
# ==============================================================================

variable "service_account_email" {
  type        = string
  description = "Email of the service account to run the Cloud Run Job"
}

variable "timeout" {
  type        = string
  default     = "600s"
  description = "Max allowed time duration per task (e.g., '600s')"
}

variable "max_retries" {
  type        = number
  default     = 3
  description = "Maximum number of retries per task"
}

variable "execution_environment" {
  type        = string
  default     = "EXECUTION_ENVIRONMENT_GEN2"
  description = "Execution environment (EXECUTION_ENVIRONMENT_GEN1, EXECUTION_ENVIRONMENT_GEN2)"
}

variable "encryption_key" {
  type        = string
  default     = null
  description = "Customer managed encryption key (CMEK) for container image"
}

# ==============================================================================
# Container Configuration
# ==============================================================================

variable "containers" {
  type = list(object({
    name        = optional(string)
    image       = string
    command     = optional(list(string))
    args        = optional(list(string))
    working_dir = optional(string)
    depends_on  = optional(list(string))

    env = optional(list(object({
      name  = string
      value = optional(string)
      value_source = optional(object({
        secret_key_ref = object({
          secret  = string
          version = string
        })
      }))
    })))

    resources = optional(object({
      limits = optional(map(string))
    }))

    ports = optional(list(object({
      name           = optional(string)
      container_port = optional(number)
    })))

    volume_mounts = optional(list(object({
      name       = string
      mount_path = string
      sub_path   = optional(string)
    })))

    startup_probe = optional(object({
      initial_delay_seconds = optional(number)
      timeout_seconds       = optional(number)
      period_seconds        = optional(number)
      failure_threshold     = optional(number)

      tcp_socket = optional(object({
        port = optional(number)
      }))

      http_get = optional(object({
        path = optional(string)
        port = optional(number)
        http_headers = optional(list(object({
          name  = string
          value = optional(string)
        })))
      }))

      grpc = optional(object({
        port    = optional(number)
        service = optional(string)
      }))
    }))
  }))
  description = "List of containers to run in the job"
}

# ==============================================================================
# Volume Configuration
# ==============================================================================

variable "volumes" {
  type = list(object({
    name = string

    secret = optional(object({
      secret       = string
      default_mode = optional(number)
      items = optional(list(object({
        path    = string
        version = string
        mode    = optional(number)
      })))
    }))

    cloud_sql_instance = optional(object({
      instances = list(string)
    }))

    empty_dir = optional(object({
      medium     = optional(string)
      size_limit = optional(string)
    }))

    gcs = optional(object({
      bucket        = string
      read_only     = optional(bool)
      mount_options = optional(list(string))
    }))

    nfs = optional(object({
      server    = string
      path      = optional(string)
      read_only = optional(bool)
    }))
  }))
  default     = []
  description = "Volumes to make available to containers"
}

# ==============================================================================
# VPC Access Configuration
# ==============================================================================

variable "vpc_access" {
  type = object({
    connector = optional(string)
    egress    = optional(string)

    network_interfaces = optional(list(object({
      network    = optional(string)
      subnetwork = optional(string)
      tags       = optional(list(string))
    })))
  })
  default     = null
  description = "VPC Access configuration (connector or direct VPC)"
}

# ==============================================================================
# Node Selector (GPU Support)
# ==============================================================================

variable "node_selector" {
  type = object({
    accelerator = string
  })
  default     = null
  description = "GPU configuration (e.g., nvidia-l4)"
}

variable "gpu_zonal_redundancy_disabled" {
  type        = bool
  default     = null
  description = "Whether to disable GPU zonal redundancy"
}

# ==============================================================================
# Binary Authorization
# ==============================================================================

variable "binary_authorization" {
  type = object({
    use_default              = optional(bool)
    breakglass_justification = optional(string)
    policy                   = optional(string)
  })
  default     = null
  description = "Binary Authorization configuration"
}