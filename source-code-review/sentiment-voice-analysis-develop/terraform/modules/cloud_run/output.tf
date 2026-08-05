output "cloud_run_id" {
  description = "The ID of the Cloud Run job"
  value       = google_cloud_run_v2_job.default.id
}

output "cloud_run_name" {
  description = "The name of the Cloud Run job"
  value       = google_cloud_run_v2_job.default.name
}

output "cloud_run_uri" {
  description = "The URI for triggering the Cloud Run job (for Cloud Scheduler) - v2 API format"
  value       = "https://run.googleapis.com/v2/projects/${google_cloud_run_v2_job.default.project}/locations/${google_cloud_run_v2_job.default.location}/jobs/${google_cloud_run_v2_job.default.name}:run"
}