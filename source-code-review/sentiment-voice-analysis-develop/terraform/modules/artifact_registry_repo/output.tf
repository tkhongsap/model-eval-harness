output "id" {
  description = "The ID of the Artifact Registry repository"
  value       = google_artifact_registry_repository.default.id
}

output "name" {
  description = "The name of the Artifact Registry repository"
  value       = google_artifact_registry_repository.default.name
}

output "repository_id" {
  description = "The repository ID"
  value       = google_artifact_registry_repository.default.repository_id
}

output "location" {
  description = "The location of the repository"
  value       = google_artifact_registry_repository.default.location
}

output "format" {
  description = "The format of packages in the repository"
  value       = google_artifact_registry_repository.default.format
}

output "mode" {
  description = "The mode of the repository"
  value       = google_artifact_registry_repository.default.mode
}

output "project" {
  description = "The project ID"
  value       = google_artifact_registry_repository.default.project
}

output "create_time" {
  description = "The time when the repository was created"
  value       = google_artifact_registry_repository.default.create_time
}

output "update_time" {
  description = "The time when the repository was last updated"
  value       = google_artifact_registry_repository.default.update_time
}