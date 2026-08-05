output "id" {
  description = "Resource identifier: projects/{{project}}/locations/{{location}}/googleApiSources/{{google_api_source_id}}"
  value       = google_eventarc_google_api_source.default.id
}

output "name" {
  description = "Resource name of the form projects/{project}/locations/{location}/googleApiSources/{google_api_source}."
  value       = google_eventarc_google_api_source.default.name
}

output "uid" {
  description = "Server-assigned unique identifier (UUID4). Unchanged until deletion."
  value       = google_eventarc_google_api_source.default.uid
}

output "create_time" {
  description = "The creation time."
  value       = google_eventarc_google_api_source.default.create_time
}

output "update_time" {
  description = "The last-modified time."
  value       = google_eventarc_google_api_source.default.update_time
}

output "etag" {
  description = "Server-computed checksum. May be sent on update/delete requests to ensure the client has an up-to-date value."
  value       = google_eventarc_google_api_source.default.etag
}

output "effective_labels" {
  description = "All labels present on the resource in GCP, including those configured through Terraform and other clients."
  value       = google_eventarc_google_api_source.default.effective_labels
}

output "terraform_labels" {
  description = "The combination of labels configured directly on the resource and default labels configured on the provider."
  value       = google_eventarc_google_api_source.default.terraform_labels
}

output "effective_annotations" {
  description = "All annotations present on the resource in GCP, including those configured through Terraform and other clients."
  value       = google_eventarc_google_api_source.default.effective_annotations
}
