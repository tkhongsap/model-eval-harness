resource "google_storage_bucket" "default" {
  name                        = var.name
  project                     = var.project_id
  location                    = var.location
  storage_class               = var.storage_class
  force_destroy               = var.force_destroy
  public_access_prevention    = var.public_access_prevention
  uniform_bucket_level_access = var.uniform_bucket_level_access
  labels                      = var.labels
  default_event_based_hold    = var.default_event_based_hold
  enable_object_retention     = var.enable_object_retention
  requester_pays              = var.requester_pays
  rpo                         = var.rpo

  # Versioning
  dynamic "versioning" {
    for_each = var.versioning_enabled ? [1] : []
    content {
      enabled = true
    }
  }

  # Autoclass
  dynamic "autoclass" {
    for_each = var.autoclass != null ? [1] : []
    content {
      enabled                = var.autoclass.enabled
      terminal_storage_class = lookup(var.autoclass, "terminal_storage_class", null)
    }
  }

  # Website
  dynamic "website" {
    for_each = var.website != null ? [1] : []
    content {
      main_page_suffix = lookup(var.website, "main_page_suffix", null)
      not_found_page   = lookup(var.website, "not_found_page", null)
    }
  }

  # Custom placement config
  dynamic "custom_placement_config" {
    for_each = var.custom_placement_config != null ? [1] : []
    content {
      data_locations = var.custom_placement_config.data_locations
    }
  }

  # Soft delete policy
  dynamic "soft_delete_policy" {
    for_each = var.soft_delete_policy != null ? [1] : []
    content {
      retention_duration_seconds = lookup(var.soft_delete_policy, "retention_duration_seconds", 604800)
    }
  }

  # Hierarchical namespace
  dynamic "hierarchical_namespace" {
    for_each = var.hierarchical_namespace != null ? [1] : []
    content {
      enabled = var.hierarchical_namespace.enabled
    }
  }

  # IP filter
  dynamic "ip_filter" {
    for_each = var.ip_filter != null ? [1] : []
    content {
      mode                           = var.ip_filter.mode
      allow_cross_org_vpcs           = lookup(var.ip_filter, "allow_cross_org_vpcs", null)
      allow_all_service_agent_access = lookup(var.ip_filter, "allow_all_service_agent_access", null)

      dynamic "public_network_source" {
        for_each = lookup(var.ip_filter, "public_network_source", null) != null ? [1] : []
        content {
          allowed_ip_cidr_ranges = var.ip_filter.public_network_source.allowed_ip_cidr_ranges
        }
      }

      dynamic "vpc_network_sources" {
        for_each = lookup(var.ip_filter, "vpc_network_sources", []) != null ? lookup(var.ip_filter, "vpc_network_sources", []) : []
        content {
          network                = vpc_network_sources.value.network
          allowed_ip_cidr_ranges = vpc_network_sources.value.allowed_ip_cidr_ranges
        }
      }
    }
  }

  # Lifecycle rules
  dynamic "lifecycle_rule" {
    for_each = var.lifecycle_rules
    content {
      action {
        type          = lifecycle_rule.value.action.type
        storage_class = lookup(lifecycle_rule.value.action, "storage_class", null)
      }
      condition {
        age                                     = lookup(lifecycle_rule.value.condition, "age", null)
        created_before                          = lookup(lifecycle_rule.value.condition, "created_before", null)
        with_state                              = lookup(lifecycle_rule.value.condition, "with_state", null)
        matches_storage_class                   = lookup(lifecycle_rule.value.condition, "matches_storage_class", null)
        matches_prefix                          = lookup(lifecycle_rule.value.condition, "matches_prefix", null)
        matches_suffix                          = lookup(lifecycle_rule.value.condition, "matches_suffix", null)
        num_newer_versions                      = lookup(lifecycle_rule.value.condition, "num_newer_versions", null)
        send_num_newer_versions_if_zero         = lookup(lifecycle_rule.value.condition, "send_num_newer_versions_if_zero", null)
        custom_time_before                      = lookup(lifecycle_rule.value.condition, "custom_time_before", null)
        days_since_custom_time                  = lookup(lifecycle_rule.value.condition, "days_since_custom_time", null)
        send_days_since_custom_time_if_zero     = lookup(lifecycle_rule.value.condition, "send_days_since_custom_time_if_zero", null)
        days_since_noncurrent_time              = lookup(lifecycle_rule.value.condition, "days_since_noncurrent_time", null)
        send_days_since_noncurrent_time_if_zero = lookup(lifecycle_rule.value.condition, "send_days_since_noncurrent_time_if_zero", null)
        noncurrent_time_before                  = lookup(lifecycle_rule.value.condition, "noncurrent_time_before", null)
        send_age_if_zero                        = lookup(lifecycle_rule.value.condition, "send_age_if_zero", null)
      }
    }
  }

  # Encryption
  dynamic "encryption" {
    for_each = var.kms_key_name != null ? [1] : []
    content {
      default_kms_key_name = var.kms_key_name
    }
  }

  # CORS
  dynamic "cors" {
    for_each = var.cors_rules
    content {
      origin          = cors.value.origin
      method          = cors.value.method
      response_header = lookup(cors.value, "response_header", null)
      max_age_seconds = lookup(cors.value, "max_age_seconds", null)
    }
  }

  # Logging
  dynamic "logging" {
    for_each = var.logging_config != null ? [1] : []
    content {
      log_bucket        = var.logging_config.log_bucket
      log_object_prefix = lookup(var.logging_config, "log_object_prefix", null)
    }
  }

  # Retention policy
  dynamic "retention_policy" {
    for_each = var.retention_policy != null ? [1] : []
    content {
      retention_period = var.retention_policy.retention_period
      is_locked        = lookup(var.retention_policy, "is_locked", false)
    }
  }
}

# IAM bindings for the bucket
resource "google_storage_bucket_iam_member" "members" {
  for_each = var.iam_members

  bucket = google_storage_bucket.default.name
  role   = each.value.role
  member = each.value.member
}

# Optional: Upload objects to the bucket
resource "google_storage_bucket_object" "objects" {
  for_each = var.objects

  name    = each.key
  bucket  = google_storage_bucket.default.name
  source  = each.value.source
  content = lookup(each.value, "content", null)
}

# Optional: Pub/Sub notification
resource "google_storage_notification" "notification" {
  count = var.notification_config != null ? 1 : 0

  bucket         = google_storage_bucket.default.name
  payload_format = var.notification_config.payload_format
  topic          = var.notification_config.topic
  event_types    = lookup(var.notification_config, "event_types", null)

  depends_on = [google_storage_bucket.default]
}