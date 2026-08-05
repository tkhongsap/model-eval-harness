resource "google_eventarc_channel" "default" {
  name     = var.name
  location = var.location
  project  = var.project

  third_party_provider = var.third_party_provider
  crypto_key_name      = var.crypto_key_name
  labels               = var.labels
}
