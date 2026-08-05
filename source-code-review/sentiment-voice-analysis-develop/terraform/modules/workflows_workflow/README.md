# Workflows Workflow Module

> **Provider Compatibility**: Google Cloud Provider = v7.20.0 (pinned exact version)

Terraform module for creating and managing Google Cloud Workflows resources. Workflows lets you orchestrate and automate Google Cloud and HTTP-based API services with YAML/JSON workflow definitions, with built-in retry, error handling, and environment variable support.

## Usage Examples

### 1. Basic Workflow

```terraform
module "workflow" {
    source = "../../modules/workflows_workflow"

    name    = "my-workflow"
    region  = "us-central1"
    project = var.project_id

    deletion_protection = false

    source_contents = <<-EOF
      - returnHello:
          return: "Hello, World!"
    EOF
}
```

### 2. Workflow with Service Account and Logging

```terraform
resource "google_service_account" "workflow_sa" {
    account_id   = "workflow-runner"
    display_name = "Workflow Runner SA"
    project      = var.project_id
}

module "workflow" {
    source = "../../modules/workflows_workflow"

    name            = "my-workflow"
    region          = "us-central1"
    project         = var.project_id
    description     = "Fetches current time and Wikipedia articles"
    service_account = google_service_account.workflow_sa.email
    call_log_level  = "LOG_ERRORS_ONLY"

    deletion_protection = false

    source_contents = <<-EOF
      - getCurrentTime:
          call: http.get
          args:
              url: $${sys.get_env("url")}
          result: currentTime
      - returnOutput:
          return: $${currentTime.body}
    EOF

    user_env_vars = {
        url = "https://timeapi.io/api/Time/current/zone?timeZone=UTC"
    }

    labels = {
        environment = "production"
        team        = "platform"
    }
}
```

### 3. Workflow with Execution History and Env Vars

```terraform
module "workflow" {
    source = "../../modules/workflows_workflow"

    name                    = "detailed-workflow"
    region                  = "us-central1"
    project                 = var.project_id
    service_account         = var.service_account_email
    call_log_level          = "LOG_ALL_CALLS"
    execution_history_level = "EXECUTION_HISTORY_DETAILED"

    deletion_protection = false

    source_contents = file("${path.module}/workflows/my_workflow.yaml")

    user_env_vars = {
        api_url     = "https://api.example.com"
        bucket_name = var.gcs_bucket
    }
}
```

### 4. Workflow with CMEK Encryption

```terraform
resource "google_kms_crypto_key_iam_member" "workflow_key_access" {
    crypto_key_id = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"
    role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
    member        = "serviceAccount:service-${var.project_number}@gcp-sa-workflows.iam.gserviceaccount.com"
}

module "workflow" {
    source = "../../modules/workflows_workflow"

    name            = "cmek-workflow"
    region          = "us-central1"
    project         = var.project_id
    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"

    deletion_protection = false

    source_contents = <<-EOF
      - returnHello:
          return: "Encrypted workflow"
    EOF

    depends_on = [google_kms_crypto_key_iam_member.workflow_key_access]
}
```

### 5. Workflow with Name Prefix (auto-generated name)

```terraform
module "workflow" {
    source = "../../modules/workflows_workflow"

    name_prefix = "job-processor-"
    region      = "us-central1"
    project     = var.project_id

    deletion_protection = false

    source_contents = <<-EOF
      - processJob:
          return: "done"
    EOF
}

output "workflow_name" {
    value = module.workflow.name
}
```

### 6. Using Workflow as an Eventarc Trigger Destination

```terraform
module "workflow" {
    source = "../../modules/workflows_workflow"

    name                = "event-handler"
    region              = "us-central1"
    project             = var.project_id
    service_account     = var.service_account_email
    deletion_protection = false

    source_contents = <<-EOF
      - handleEvent:
          return: $${sys.get_env("GOOGLE_CLOUD_WORKFLOW_EXECUTION_ID")}
    EOF
}

module "trigger" {
    source = "../../modules/eventarc_trigger"

    name     = "workflow-trigger"
    location = "us-central1"
    project  = var.project_id

    matching_criteria = [{
        attribute = "type"
        value     = "google.cloud.pubsub.topic.v1.messagePublished"
    }]

    destination = {
        workflow = module.workflow.id
    }

    service_account = var.service_account_email
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Name of the workflow. If unset and `name_prefix` is also unset, a random name is generated | string | null | no |
| name_prefix | Creates a unique name beginning with this prefix | string | null | no |
| region | GCP region (e.g., `us-central1`) | string | null | no |
| project | GCP project ID. Defaults to provider project | string | null | no |
| description | Human-readable description (max 1000 unicode characters) | string | null | no |
| service_account | Service account email or unique ID the workflow runs as | string | null | no |
| source_contents | Workflow YAML/JSON source code (max 128 KB). Use `$$` to escape `$` in YAML | string | null | no |
| crypto_key_name | KMS crypto key for CMEK. Format: `projects/*/locations/*/keyRings/*/cryptoKeys/*` | string | null | no |
| call_log_level | Logging level for calls. One of: `CALL_LOG_LEVEL_UNSPECIFIED`, `LOG_ALL_CALLS`, `LOG_ERRORS_ONLY`, `LOG_NONE` | string | null | no |
| execution_history_level | Execution history retention. One of: `EXECUTION_HISTORY_LEVEL_UNSPECIFIED`, `EXECUTION_HISTORY_BASIC`, `EXECUTION_HISTORY_DETAILED` | string | null | no |
| user_env_vars | User-defined environment variables (max 20 entries, values up to 4 KiB each) | map(string) | `{}` | no |
| labels | User-defined labels. Non-authoritative | map(string) | `{}` | no |
| tags | Resource manager tags. Keys: `tagKeys/{id}`, Values: `tagValues/{id}` | map(string) | `{}` | no |
| deletion_protection | When true, prevents `terraform destroy` from deleting the workflow | bool | `true` | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{region}}/workflows/{{name}}` |
| name | Workflow name (useful when auto-generated via `name_prefix`) |
| state | Deployment state of the workflow |
| revision_id | Current revision ID (changes when `service_account` or `source_contents` changes) |
| create_time | Workflow creation timestamp (RFC3339 UTC) |
| update_time | Workflow last-modified timestamp (RFC3339 UTC) |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |

## Notes

1. **Provider Version**: Requires Google Cloud Provider = v7.20.0 (pinned exact version)
2. **`deletion_protection`**: Defaults to `true` — set to `false` in configuration before running `terraform destroy`, otherwise the destroy will fail
3. **Source Escaping**: Inside `source_contents`, use `$$` instead of `$` to prevent Terraform from interpreting workflow variable expressions (e.g., `$${sys.get_env("key")}`)
4. **Revisions**: Changing `service_account` or `source_contents` creates a new workflow revision; other fields update in place
5. **CMEK**: The Workflows service account (`service-PROJECT_NUMBER@gcp-sa-workflows.iam.gserviceaccount.com`) must have `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key before applying
6. **user_env_vars**: Keys cannot be empty or start with `GOOGLE` or `WORKFLOWS`; maximum 20 entries
7. **Import**: This resource does **not** support `terraform import`
8. **Timeouts**: Create, update, and delete operations each default to 20 minutes
