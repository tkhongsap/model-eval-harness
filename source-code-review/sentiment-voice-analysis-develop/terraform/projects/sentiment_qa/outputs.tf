# ==============================================================================
# Outputs - Infrastructure Resource Information
# ==============================================================================

output "artifact_registry_id" {
  description = "Artifact Registry repository ID"
  value       = module.voice_sentiment_qa_artifact_registry.id
}

output "artifact_registry_name" {
  description = "Artifact Registry repository name"
  value       = module.voice_sentiment_qa_artifact_registry.name
}

output "artifact_registry_url" {
  description = "Full Artifact Registry URL for docker push/pull"
  value       = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-sentiment-qa-artifact-repo"
}

output "bucket_name" {
  description = "Cloud Storage bucket name"
  value       = module.voice_sentiment_qa_bucket.bucket_name
}

output "bucket_url" {
  description = "Cloud Storage bucket URL"
  value       = module.voice_sentiment_qa_bucket.bucket_url
}

output "cloud_run_job_id" {
  description = "Cloud Run job ID"
  value       = module.voice_sentiment_qa_job.cloud_run_id
}

output "cloud_run_job_name" {
  description = "Cloud Run job name"
  value       = module.voice_sentiment_qa_job.cloud_run_name
}

output "cloud_run_job_uri" {
  description = "Cloud Run job execution URI (for triggering)"
  value       = module.voice_sentiment_qa_job.cloud_run_uri
}

output "scheduler_job_id" {
  description = "Cloud Scheduler job ID"
  value       = module.voice_sentiment_qa_scheduler.scheduler_job_id
}

output "scheduler_job_name" {
  description = "Cloud Scheduler job name"
  value       = module.voice_sentiment_qa_scheduler.scheduler_job_name
}

output "scheduler_schedule" {
  description = "Cloud Scheduler cron schedule"
  value       = "0 9 * * * (Everyday at 9 AM)"
}

output "secrets_created" {
  description = "List of Secret Manager secrets created (values must be added manually)"
  value       = keys(module.voice_sentiment_qa_secrets)
}

output "environment" {
  description = "Current environment"
  value       = var.environment
}

output "gcp_project_id" {
  description = "GCP Project ID"
  value       = var.gcp_project_id
}

output "gcp_region" {
  description = "GCP Region"
  value       = var.gcp_region
}
