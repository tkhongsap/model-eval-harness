resource "google_workflows_workflow" "default" {
  name        = var.name
  name_prefix = var.name_prefix
  region      = var.region
  project     = var.project

  description             = var.description
  service_account         = var.service_account
  source_contents         = var.source_contents
  crypto_key_name         = var.crypto_key_name
  call_log_level          = var.call_log_level
  execution_history_level = var.execution_history_level
  user_env_vars           = var.user_env_vars
  labels                  = var.labels
  tags                    = var.tags
  deletion_protection     = var.deletion_protection
}
