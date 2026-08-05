resource "google_eventarc_google_api_source" "default" {
  location             = var.location
  google_api_source_id = var.google_api_source_id
  destination          = var.destination
  project              = var.project

  display_name    = var.display_name
  crypto_key_name = var.crypto_key_name
  labels          = var.labels
  annotations     = var.annotations

  dynamic "logging_config" {
    for_each = var.logging_config != null ? [1] : []
    content {
      log_severity = var.logging_config.log_severity
    }
  }
}
