output "id" {
  description = "Full resource identifier: `projects/{{project}}/locations/{{location}}/channels/{{name}}`."
  value       = google_eventarc_channel.default.id
}

output "uid" {
  description = "Server-assigned UUID4 identifier. Unchanged until the resource is deleted."
  value       = google_eventarc_channel.default.uid
}

output "create_time" {
  description = "Channel creation timestamp."
  value       = google_eventarc_channel.default.create_time
}

output "update_time" {
  description = "Channel last-modified timestamp."
  value       = google_eventarc_channel.default.update_time
}

output "pubsub_topic" {
  description = "Name of the Pub/Sub topic managed by Eventarc as the event delivery transport. Format: `projects/{project}/topics/{topic_id}`."
  value       = google_eventarc_channel.default.pubsub_topic
}

output "state" {
  description = "State of the channel."
  value       = google_eventarc_channel.default.state
}

output "activation_token" {
  description = "Activation token for the channel. Must be used by the third-party provider to register the channel for publishing."
  value       = google_eventarc_channel.default.activation_token
  sensitive   = true
}

output "effective_labels" {
  description = "All labels present on the resource in GCP, including labels configured through Terraform, other clients, and services."
  value       = google_eventarc_channel.default.effective_labels
}

output "terraform_labels" {
  description = "Combined Terraform-managed labels and provider-default labels."
  value       = google_eventarc_channel.default.terraform_labels
}
