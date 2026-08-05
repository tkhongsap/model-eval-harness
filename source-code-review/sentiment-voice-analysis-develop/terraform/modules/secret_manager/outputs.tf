output "secret_id" {
  description = "The ID of the Secret Manager secret (full resource name)"
  value       = google_secret_manager_secret.default.id
}

output "name" {
  description = "The name of the Secret Manager secret"
  value       = google_secret_manager_secret.default.name
}

output "secret_id_short" {
  description = "The short ID of the secret (for use in Cloud Run env vars)"
  value       = google_secret_manager_secret.default.secret_id
}

output "project" {
  description = "The project ID"
  value       = google_secret_manager_secret.default.project
}

output "create_time" {
  description = "The time at which the secret was created"
  value       = google_secret_manager_secret.default.create_time
}