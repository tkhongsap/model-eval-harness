resource "google_eventarc_trigger" "default" {
  name     = var.name
  location = var.location
  project  = var.project

  dynamic "matching_criteria" {
    for_each = var.matching_criteria
    content {
      attribute = matching_criteria.value.attribute
      value     = matching_criteria.value.value
      operator  = lookup(matching_criteria.value, "operator", null)
    }
  }

  destination {
    dynamic "cloud_run_service" {
      for_each = lookup(var.destination, "cloud_run_service", null) != null ? [1] : []
      content {
        service = var.destination.cloud_run_service.service
        path    = lookup(var.destination.cloud_run_service, "path", null)
        region  = lookup(var.destination.cloud_run_service, "region", null)
      }
    }

    dynamic "gke" {
      for_each = lookup(var.destination, "gke", null) != null ? [1] : []
      content {
        cluster   = var.destination.gke.cluster
        location  = var.destination.gke.location
        namespace = var.destination.gke.namespace
        service   = var.destination.gke.service
        path      = lookup(var.destination.gke, "path", null)
      }
    }

    workflow = lookup(var.destination, "workflow", null)

    dynamic "http_endpoint" {
      for_each = lookup(var.destination, "http_endpoint", null) != null ? [1] : []
      content {
        uri = var.destination.http_endpoint.uri
      }
    }

    dynamic "network_config" {
      for_each = lookup(var.destination, "network_config", null) != null ? [1] : []
      content {
        network_attachment = var.destination.network_config.network_attachment
      }
    }
  }

  service_account = var.service_account

  dynamic "transport" {
    for_each = var.transport != null ? [1] : []
    content {
      dynamic "pubsub" {
        for_each = lookup(var.transport, "pubsub", null) != null ? [1] : []
        content {
          topic = lookup(var.transport.pubsub, "topic", null)
        }
      }
    }
  }

  labels                  = var.labels
  channel                 = var.channel
  event_data_content_type = var.event_data_content_type

  dynamic "retry_policy" {
    for_each = var.retry_policy != null ? [1] : []
    content {
      max_attempts = var.retry_policy.max_attempts
    }
  }
}