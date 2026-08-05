resource "google_eventarc_message_bus" "default" {
  location       = var.location
  message_bus_id = var.message_bus_id
  project        = var.project

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
