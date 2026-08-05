output "scheduler_job_id" {
  description = "The ID of the Cloud Scheduler job"
  value       = google_cloud_scheduler_job.default.id
}

output "scheduler_job_name" {
  description = "The name of the Cloud Scheduler job"
  value       = google_cloud_scheduler_job.default.name
}

output "scheduler_job_state" {
  description = "The current state of the Cloud Scheduler job"
  value       = google_cloud_scheduler_job.default.state
}
