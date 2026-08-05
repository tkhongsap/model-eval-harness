resource "google_eventarc_pipeline" "default" {
  location    = var.location
  pipeline_id = var.pipeline_id
  project     = var.project

  display_name    = var.display_name
  crypto_key_name = var.crypto_key_name
  labels          = var.labels
  annotations     = var.annotations

  dynamic "destinations" {
    for_each = var.destinations
    content {
      workflow    = lookup(destinations.value, "workflow", null)
      message_bus = lookup(destinations.value, "message_bus", null)
      topic       = lookup(destinations.value, "topic", null)

      dynamic "http_endpoint" {
        for_each = lookup(destinations.value, "http_endpoint", null) != null ? [1] : []
        content {
          uri                      = destinations.value.http_endpoint.uri
          message_binding_template = lookup(destinations.value.http_endpoint, "message_binding_template", null)
        }
      }

      dynamic "network_config" {
        for_each = lookup(destinations.value, "network_config", null) != null ? [1] : []
        content {
          network_attachment = destinations.value.network_config.network_attachment
        }
      }

      dynamic "authentication_config" {
        for_each = lookup(destinations.value, "authentication_config", null) != null ? [1] : []
        content {
          dynamic "google_oidc" {
            for_each = lookup(destinations.value.authentication_config, "google_oidc", null) != null ? [1] : []
            content {
              service_account = destinations.value.authentication_config.google_oidc.service_account
              audience        = lookup(destinations.value.authentication_config.google_oidc, "audience", null)
            }
          }

          dynamic "oauth_token" {
            for_each = lookup(destinations.value.authentication_config, "oauth_token", null) != null ? [1] : []
            content {
              service_account = destinations.value.authentication_config.oauth_token.service_account
              scope           = lookup(destinations.value.authentication_config.oauth_token, "scope", null)
            }
          }
        }
      }

      dynamic "output_payload_format" {
        for_each = lookup(destinations.value, "output_payload_format", null) != null ? [1] : []
        content {
          dynamic "json" {
            for_each = lookup(destinations.value.output_payload_format, "json", null) != null ? [1] : []
            content {}
          }

          dynamic "avro" {
            for_each = lookup(destinations.value.output_payload_format, "avro", null) != null ? [1] : []
            content {
              schema_definition = lookup(destinations.value.output_payload_format.avro, "schema_definition", null)
            }
          }

          dynamic "protobuf" {
            for_each = lookup(destinations.value.output_payload_format, "protobuf", null) != null ? [1] : []
            content {
              schema_definition = lookup(destinations.value.output_payload_format.protobuf, "schema_definition", null)
            }
          }
        }
      }
    }
  }

  dynamic "input_payload_format" {
    for_each = var.input_payload_format != null ? [1] : []
    content {
      dynamic "json" {
        for_each = lookup(var.input_payload_format, "json", null) != null ? [1] : []
        content {}
      }

      dynamic "avro" {
        for_each = lookup(var.input_payload_format, "avro", null) != null ? [1] : []
        content {
          schema_definition = lookup(var.input_payload_format.avro, "schema_definition", null)
        }
      }

      dynamic "protobuf" {
        for_each = lookup(var.input_payload_format, "protobuf", null) != null ? [1] : []
        content {
          schema_definition = lookup(var.input_payload_format.protobuf, "schema_definition", null)
        }
      }
    }
  }

  dynamic "retry_policy" {
    for_each = var.retry_policy != null ? [1] : []
    content {
      max_retry_delay = lookup(var.retry_policy, "max_retry_delay", null)
      max_attempts    = lookup(var.retry_policy, "max_attempts", null)
      min_retry_delay = lookup(var.retry_policy, "min_retry_delay", null)
    }
  }

  dynamic "mediations" {
    for_each = var.mediations
    content {
      dynamic "transformation" {
        for_each = lookup(mediations.value, "transformation", null) != null ? [1] : []
        content {
          transformation_template = mediations.value.transformation.transformation_template
        }
      }
    }
  }

  dynamic "logging_config" {
    for_each = var.logging_config != null ? [1] : []
    content {
      log_severity = var.logging_config.log_severity
    }
  }
}
