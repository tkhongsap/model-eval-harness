output "id" {
  description = "Resource identifier: projects/{{project}}/locations/{{location}}/googleChannelConfig"
  value       = google_eventarc_google_channel_config.default.id
}

output "update_time" {
  description = "The last-modified time."
  value       = google_eventarc_google_channel_config.default.update_time
}
