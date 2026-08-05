resource "google_eventarc_enrollment" "default" {
  location      = var.location
  enrollment_id = var.enrollment_id
  message_bus   = var.message_bus
  destination   = var.destination
  cel_match     = var.cel_match
  project       = var.project

  display_name = var.display_name
  labels       = var.labels
  annotations  = var.annotations
}
