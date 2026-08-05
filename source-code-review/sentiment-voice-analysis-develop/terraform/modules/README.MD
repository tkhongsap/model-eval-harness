# Terraform Modules

Reusable, self-contained Terraform modules for provisioning GCP resources. Each module is designed to be used independently across multiple projects.

## Requirements

| Dependency          | Version    | Reference                                                                           |
| ------------------- | ---------- | ----------------------------------------------------------------------------------- |
| **Terraform**       | `= 1.13.3` | [terraform.io](https://www.terraform.io/)                                           |
| **Google Provider** | `7.20.0`   | [hashicorp/google](https://registry.terraform.io/providers/hashicorp/google/latest) |

## Available Modules

| Module                           | GCP Service          | Description                                                                      | Docs                                                 |
| -------------------------------- | -------------------- | -------------------------------------------------------------------------------- | ---------------------------------------------------- |
| **artifact_registry_repo**       | Artifact Registry    | Docker/package repositories with cleanup policies, CMEK, virtual/remote modes    | [README](artifact_registry_repo/README.md)           |
| **cloud_run**                    | Cloud Run v2 Jobs    | Batch jobs with secrets, VPC access, volumes, GPU, multi-container support       | [README](cloud_run/README.md)                        |
| **cloud_scheduler**              | Cloud Scheduler      | Cron-based triggers for HTTP, Pub/Sub, App Engine with retry & auth (OAuth/OIDC) | [README](cloud_scheduler/README.md)                  |
| **cloud_storage**                | Cloud Storage        | Buckets with IAM, lifecycle, versioning, CMEK, CORS, notifications, autoclass    | [README](cloud_storage/README.md)                    |
| **eventarc_channel**             | Eventarc             | Third-party provider channel with managed Pub/Sub transport, CMEK, activation    | [README](eventarc_channel/README.md)                 |
| **eventarc_enrollment**          | Eventarc Advanced    | Links a Message Bus to a Pipeline via CEL filter expression                      | [README](eventarc_enrollment/README.md)              |
| **eventarc_google_api_source**   | Eventarc Advanced    | Routes Google API events to a Message Bus, with optional CMEK and logging        | [README](eventarc_google_api_source/README.md)       |
| **eventarc_google_channel_config** | Eventarc           | Singleton config for CMEK encryption of Google Channel events per location       | [README](eventarc_google_channel_config/README.md)   |
| **eventarc_message_bus**         | Eventarc Advanced    | Central event bus for Advanced Eventarc with CMEK, logging, labels               | [README](eventarc_message_bus/README.md)             |
| **eventarc_pipeline**            | Eventarc Advanced    | Routes events to HTTP/Pub/Sub/Workflow/Bus with auth, format conversion, retry   | [README](eventarc_pipeline/README.md)                |
| **eventarc_trigger**             | Eventarc             | Triggers for Cloud Run, GKE, Workflows, HTTP endpoints via CloudEvent filters    | [README](eventarc_trigger/README.md)                 |
| **secret_manager**               | Secret Manager       | Secret metadata with replication, rotation, TTL, version aliases, Pub/Sub        | [README](secret_manager/README.md)                   |
| **workflows_workflow**           | Workflows            | Orchestration workflow with YAML source, CMEK, env vars, logging, revision mgmt  | [README](workflows_workflow/README.md)               |

## Module Structure

Each module follows a consistent file layout:

```
module_name/
├── main.tf          # Resource definitions
├── variables.tf     # Input variables with descriptions & defaults
├── outputs.tf       # Output values
├── versions.tf      # Terraform & provider version constraints
└── README.md        # Usage examples, inputs/outputs reference
```

## Usage

Reference a module from a project configuration:

```hcl
module "example" {
  source = "../../modules/<module_name>"

  # Module-specific variables
  project_id = var.gcp_project_id
  region     = var.region
  # ...
}
```

> [!IMPORTANT]
> These modules create **resource metadata only** (e.g., Secret Manager secrets without values, empty buckets). Sensitive data such as secret values must be added separately via `gcloud` CLI or the GCP Console.

## Provider Reference

All modules use the [hashicorp/google](https://registry.terraform.io/providers/hashicorp/google/latest) provider. Key resource documentation:

| Resource                                  | Module                           | Registry Docs                                                                                                           |
| ----------------------------------------- | -------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| `google_artifact_registry_repository`     | `artifact_registry_repo`         | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/artifact_registry_repository)    |
| `google_cloud_run_v2_job`                 | `cloud_run`                      | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_run_v2_job)                |
| `google_cloud_scheduler_job`              | `cloud_scheduler`                | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/cloud_scheduler_job)             |
| `google_eventarc_channel`                 | `eventarc_channel`               | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_channel)                |
| `google_eventarc_enrollment`              | `eventarc_enrollment`            | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_enrollment)             |
| `google_eventarc_google_api_source`       | `eventarc_google_api_source`     | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_google_api_source)      |
| `google_eventarc_google_channel_config`   | `eventarc_google_channel_config` | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_google_channel_config)  |
| `google_eventarc_message_bus`             | `eventarc_message_bus`           | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_message_bus)            |
| `google_eventarc_pipeline`                | `eventarc_pipeline`              | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_pipeline)               |
| `google_eventarc_trigger`                 | `eventarc_trigger`               | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/eventarc_trigger)                |
| `google_secret_manager_secret`            | `secret_manager`                 | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret)           |
| `google_storage_bucket`                   | `cloud_storage`                  | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket)                  |
| `google_storage_bucket_iam_member`        | `cloud_storage`                  | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket_iam)              |
| `google_storage_bucket_object`            | `cloud_storage`                  | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_bucket_object)           |
| `google_storage_notification`             | `cloud_storage`                  | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/storage_notification)            |
| `google_workflows_workflow`               | `workflows_workflow`             | [Docs](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/workflows_workflow)              |
