# Eventarc Enrollment Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Eventarc Enrollments. An enrollment links a Message Bus to a Pipeline using a CEL filter expression, so that only matching messages are forwarded.

## Usage Examples

### 1. Basic Enrollment

```terraform
module "enrollment" {
    source = "../../modules/eventarc_enrollment"

    location      = "us-central1"
    enrollment_id = "my-enrollment"
    project       = var.project_id

    message_bus = "projects/${var.project_id}/locations/us-central1/messageBuses/my-bus"
    destination = "projects/${var.project_id}/locations/us-central1/pipelines/my-pipeline"
    cel_match   = "message.type == 'google.cloud.dataflow.job.v1beta3.statusChanged'"
}
```

### 2. Enrollment with Labels and Display Name

```terraform
module "enrollment" {
    source = "../../modules/eventarc_enrollment"

    location      = "us-central1"
    enrollment_id = "audit-enrollment"
    project       = var.project_id

    message_bus  = "projects/${var.project_id}/locations/us-central1/messageBuses/audit-bus"
    destination  = "projects/${var.project_id}/locations/us-central1/pipelines/audit-pipeline"
    cel_match    = "message.type.startsWith('google.cloud.audit')"
    display_name = "Audit Log Enrollment"

    labels = {
        environment = "production"
        team        = "security"
    }

    annotations = {
        managed-by = "terraform"
    }
}
```

### 3. With Inline Message Bus and Pipeline References

```terraform
module "message_bus" {
    source = "../../modules/eventarc_message_bus"

    location       = "us-central1"
    message_bus_id = "my-bus"
    project        = var.project_id
}

module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "my-pipeline"
    project     = var.project_id

    destinations = [{
        topic = "projects/${var.project_id}/locations/us-central1/topics/my-topic"
    }]
}

module "enrollment" {
    source = "../../modules/eventarc_enrollment"

    location      = "us-central1"
    enrollment_id = "my-enrollment"
    project       = var.project_id

    message_bus = module.message_bus.id
    destination = module.pipeline.id
    cel_match   = "true"
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| enrollment_id | User-provided ID for the Enrollment. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$` | string | - | yes |
| message_bus | Full resource name of the source MessageBus | string | - | yes |
| cel_match | CEL expression to filter messages from the bus | string | - | yes |
| destination | Full resource name of the destination Pipeline | string | - | yes |
| location | GCP location for the resource (e.g., `us-central1`) | string | - | yes |
| project | GCP project ID. Defaults to provider project | string | null | no |
| display_name | Human-readable display name | string | null | no |
| labels | User labels for the resource | map(string) | `{}` | no |
| annotations | User annotations for the resource | map(string) | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/enrollments/{{enrollment_id}}` |
| name | Resource name of the enrollment |
| uid | Server-assigned UUID4 identifier (unchanged until deletion) |
| create_time | Enrollment creation timestamp |
| update_time | Enrollment last-modified timestamp |
| etag | Server-computed checksum |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |
| effective_annotations | All annotations on the resource in GCP (Terraform + external) |

## Notes

1. **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
2. **CEL Filter**: The `cel_match` expression filters which messages from the bus are forwarded to the pipeline
3. **Destination**: Must be a full Pipeline resource name in the same project
4. **Labels/Annotations**: Non-authoritative — only manages entries present in configuration
5. **Timeouts**: Create, update, and delete operations each default to 20 minutes
