resource "google_secret_manager_secret" "default" {
  project     = var.project_id
  secret_id   = var.secret_id
  labels      = var.labels
  annotations = var.annotations

  # TTL configuration (conflicts with expire_time)
  ttl = var.ttl

  # Expiration time (conflicts with ttl)
  expire_time = var.expire_time

  # Rotation configuration
  dynamic "rotation" {
    for_each = var.rotation_period != null ? [1] : []
    content {
      rotation_period    = var.rotation_period
      next_rotation_time = var.next_rotation_time
    }
  }

  # Topics for notifications
  dynamic "topics" {
    for_each = var.topics
    content {
      name = topics.value
    }
  }

  # Version aliases
  version_aliases = var.version_aliases

  # Version destroy TTL
  version_destroy_ttl = var.version_destroy_ttl

  # Auto replication
  dynamic "replication" {
    for_each = var.replication_type == "auto" ? [1] : []
    content {
      auto {
        dynamic "customer_managed_encryption" {
          for_each = var.auto_cmek_key_name != null ? [1] : []
          content {
            kms_key_name = var.auto_cmek_key_name
          }
        }
      }
    }
  }

  # User-managed replication with replicas
  dynamic "replication" {
    for_each = var.replication_type == "user_managed" ? [1] : []
    content {
      user_managed {
        dynamic "replicas" {
          for_each = var.replicas
          content {
            location = replicas.value.location

            dynamic "customer_managed_encryption" {
              for_each = replicas.value.kms_key_name != null ? [1] : []
              content {
                kms_key_name = replicas.value.kms_key_name
              }
            }
          }
        }
      }
    }
  }
}
# Secret versions are intentionally NOT managed by Terraform.
# Initial versions are bootstrapped by the CI/CD pipeline (init-secret-versions step).
# Real values are populated by humans via gcloud or the GCP console.