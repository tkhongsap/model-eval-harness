output "id" {
  description = "An identifier for the resource: projects/{{project}}/locations/{{location}}/triggers/{{name}}"
  value       = google_eventarc_trigger.default.id
}

output "name" {
  description = "The resource name of the trigger."
  value       = google_eventarc_trigger.default.name
}

output "uid" {
  description = "Server-assigned unique identifier for the trigger (UUID4). Unchanged until deletion."
  value       = google_eventarc_trigger.default.uid
}

output "create_time" {
  description = "The creation time of the trigger."
  value       = google_eventarc_trigger.default.create_time
}

output "update_time" {
  description = "The last-modified time of the trigger."
  value       = google_eventarc_trigger.default.update_time
}

output "etag" {
  description = "Server-computed checksum. May be sent on create requests to ensure the client has an up-to-date value."
  value       = google_eventarc_trigger.default.etag
}

output "conditions" {
  description = "The reason(s) why a trigger is in FAILED state."
  value       = google_eventarc_trigger.default.conditions
}

output "effective_labels" {
  description = "All labels present on the resource in GCP, including those configured through Terraform and other clients."
  value       = google_eventarc_trigger.default.effective_labels
}

output "terraform_labels" {
  description = "The combination of labels configured directly on the resource and default labels configured on the provider."
  value       = google_eventarc_trigger.default.terraform_labels
}
