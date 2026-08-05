output "id" {
  description = "Full resource identifier: `projects/{{project}}/locations/{{region}}/workflows/{{name}}`."
  value       = google_workflows_workflow.default.id
}

output "name" {
  description = "Name of the workflow (useful when generated via `name_prefix`)."
  value       = google_workflows_workflow.default.name
}

output "state" {
  description = "Deployment state of the workflow."
  value       = google_workflows_workflow.default.state
}

output "revision_id" {
  description = "Current revision ID. A new revision is created whenever `service_account` or `source_contents` changes."
  value       = google_workflows_workflow.default.revision_id
}

output "create_time" {
  description = "Workflow creation timestamp (RFC3339 UTC)."
  value       = google_workflows_workflow.default.create_time
}

output "update_time" {
  description = "Workflow last-modified timestamp (RFC3339 UTC)."
  value       = google_workflows_workflow.default.update_time
}

output "effective_labels" {
  description = "All labels present on the resource in GCP, including labels configured through Terraform, other clients, and services."
  value       = google_workflows_workflow.default.effective_labels
}

output "terraform_labels" {
  description = "Combined Terraform-managed labels and provider-default labels."
  value       = google_workflows_workflow.default.terraform_labels
}
