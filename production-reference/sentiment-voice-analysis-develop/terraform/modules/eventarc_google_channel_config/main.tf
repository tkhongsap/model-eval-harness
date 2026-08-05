resource "google_eventarc_google_channel_config" "default" {
  location        = var.location
  name            = var.name
  crypto_key_name = var.crypto_key_name
  project         = var.project
}
