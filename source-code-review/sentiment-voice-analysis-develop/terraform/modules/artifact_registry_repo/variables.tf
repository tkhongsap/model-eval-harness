# ==============================================================================
# Basic Configuration
# ==============================================================================

variable "project_id" {
  type        = string
  description = "GCP Project ID where the Artifact Registry repository will be created"
}

variable "location" {
  type        = string
  description = "Location where the Artifact Registry repository will be created (e.g., us-central1)"
}

variable "repository_id" {
  type        = string
  description = "The ID of the Artifact Registry repository"
}

variable "format" {
  type        = string
  description = "The format of packages (DOCKER, MAVEN, NPM, PYTHON, APT, YUM, GOOGET, KFP, GO, GENERIC)"
}

variable "description" {
  type        = string
  default     = ""
  description = "Description of the Artifact Registry repository"
}

variable "labels" {
  type        = map(string)
  default     = {}
  description = "Labels to apply to the Artifact Registry repository (key-value pairs)"
}

variable "kms_key_name" {
  type        = string
  default     = null
  description = "The KMS key name to use for encrypting the repository"
}

variable "mode" {
  type        = string
  default     = "STANDARD_REPOSITORY"
  description = "The mode of the repository (STANDARD_REPOSITORY, VIRTUAL_REPOSITORY, REMOTE_REPOSITORY)"
  validation {
    condition     = contains(["STANDARD_REPOSITORY", "VIRTUAL_REPOSITORY", "REMOTE_REPOSITORY"], var.mode)
    error_message = "Mode must be STANDARD_REPOSITORY, VIRTUAL_REPOSITORY, or REMOTE_REPOSITORY"
  }
}

# ==============================================================================
# Format-Specific Configuration
# ==============================================================================

variable "docker_config" {
  type = object({
    immutable_tags = optional(bool)
  })
  default     = null
  description = "Docker repository configuration"
}

variable "maven_config" {
  type = object({
    allow_snapshot_overwrites = optional(bool)
    version_policy            = optional(string)
  })
  default     = null
  description = "Maven repository configuration"
}

# ==============================================================================
# Cleanup Policies
# ==============================================================================

variable "cleanup_policies" {
  type = list(object({
    id     = string
    action = optional(string)

    condition = optional(object({
      tag_state             = optional(string)
      tag_prefixes          = optional(list(string))
      version_name_prefixes = optional(list(string))
      package_name_prefixes = optional(list(string))
      older_than            = optional(string)
      newer_than            = optional(string)
    }))

    most_recent_versions = optional(object({
      package_name_prefixes = optional(list(string))
      keep_count            = optional(number)
    }))
  }))
  default     = []
  description = "Cleanup policies for this repository"
}

variable "cleanup_policy_dry_run" {
  type        = bool
  default     = false
  description = "If true, cleanup policies are in dry-run mode (no deletion)"
}

# ==============================================================================
# Virtual Repository Configuration
# ==============================================================================

variable "virtual_repository_config" {
  type = object({
    upstream_policies = optional(list(object({
      id         = optional(string)
      repository = optional(string)
      priority   = optional(number)
    })))
  })
  default     = null
  description = "Configuration for virtual repository mode"
}

# ==============================================================================
# Remote Repository Configuration
# ==============================================================================

variable "remote_repository_config" {
  type = object({
    description = optional(string)

    apt_repository = optional(object({
      public_repository = optional(object({
        repository_base = string
        repository_path = string
      }))
    }))

    docker_repository = optional(object({
      public_repository = optional(string)
      custom_repository = optional(object({
        uri = optional(string)
      }))
    }))

    maven_repository = optional(object({
      public_repository = optional(string)
      custom_repository = optional(object({
        uri = optional(string)
      }))
    }))

    npm_repository = optional(object({
      public_repository = optional(string)
      custom_repository = optional(object({
        uri = optional(string)
      }))
    }))

    python_repository = optional(object({
      public_repository = optional(string)
      custom_repository = optional(object({
        uri = optional(string)
      }))
    }))

    yum_repository = optional(object({
      public_repository = optional(object({
        repository_base = string
        repository_path = string
      }))
    }))

    upstream_credentials = optional(object({
      username_password_credentials = optional(object({
        username                = optional(string)
        password_secret_version = optional(string)
      }))
    }))

    disable_upstream_validation = optional(bool)
  })
  default     = null
  description = "Configuration for remote repository mode"
}