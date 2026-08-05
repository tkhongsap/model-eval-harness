# ==============================================================================
# Outputs - Infrastructure Resource Information
# ==============================================================================

output "artifact_registry_id" {
  description = "Artifact Registry repository ID"
  value       = module.tax_invoice_extraction_artifact_registry.id
}

output "artifact_registry_name" {
  description = "Artifact Registry repository name"
  value       = module.tax_invoice_extraction_artifact_registry.name
}

output "artifact_registry_url" {
  description = "Full Artifact Registry URL for docker push/pull"
  value       = "${var.gcp_region}-docker.pkg.dev/${var.gcp_project_id}/${var.environment}-extraction-tax-invoice-artifact-repo"
}

output "bucket_name" {
  description = "Cloud Storage bucket name"
  value       = module.tax_invoice_extraction_bucket.bucket_name
}

output "bucket_url" {
  description = "Cloud Storage bucket URL"
  value       = module.tax_invoice_extraction_bucket.bucket_url
}

output "cloud_run_job_id" {
  description = "Cloud Run job ID"
  value       = module.tax_invoice_extraction_job.cloud_run_id
}

output "cloud_run_job_name" {
  description = "Cloud Run job name"
  value       = module.tax_invoice_extraction_job.cloud_run_name
}

output "cloud_run_job_uri" {
  description = "Cloud Run job execution URI (for triggering)"
  value       = module.tax_invoice_extraction_job.cloud_run_uri
}

output "scheduler_job_id" {
  description = "Cloud Scheduler job ID"
  value       = module.tax_invoice_extraction_scheduler.scheduler_job_id
}

output "scheduler_job_name" {
  description = "Cloud Scheduler job name"
  value       = module.tax_invoice_extraction_scheduler.scheduler_job_name
}

output "secrets_created" {
  description = "List of Secret Manager secrets created (values must be added manually)"
  value       = keys(module.tax_invoice_extraction_secrets)
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

output "eventarc_pipeline_service_account_email" {
  description = "Service account used by the Tax Invoice Eventarc Pipeline (reuses var.service_account_email; IAM grants are managed outside Terraform)."
  value       = var.service_account_email
}

output "eventarc_trigger_id" {
  description = "Resource ID of the Tax Invoice Eventarc Trigger that connects the Message Bus to the Pipeline."
  value       = module.tax_invoice_extraction_eventarc_trigger.id
}

output "workflow_id" {
  description = "ID of the Workflow that orchestrates the Tax Invoice processing pipeline."
  value       = module.tax_invoice_extraction_workflow.id
}

output "workflow_name" {
  description = "Name of the Workflow that orchestrates the Tax Invoice processing pipeline."
  value       = module.tax_invoice_extraction_workflow.name
}
