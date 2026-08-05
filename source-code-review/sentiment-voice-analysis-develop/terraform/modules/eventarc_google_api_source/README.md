# Eventarc Google API Source Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Eventarc GoogleApiSource resources. A GoogleApiSource routes events from Google APIs to an Eventarc Message Bus, with optional CMEK encryption and logging configuration.

## Usage Examples

### 1. Basic Google API Source

```terraform
module "google_api_source" {
    source = "../../modules/eventarc_google_api_source"

    location             = "us-central1"
    google_api_source_id = "my-api-source"
    project              = var.project_id

    destination = "projects/${var.project_id}/locations/us-central1/messageBuses/my-bus"
}
```

### 2. With CMEK Encryption

```terraform
module "google_api_source" {
    source = "../../modules/eventarc_google_api_source"

    location             = "us-central1"
    google_api_source_id = "my-api-source"
    project              = var.project_id

    destination     = "projects/${var.project_id}/locations/us-central1/messageBuses/my-bus"
    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"
}
```

### 3. With Logging and Labels

```terraform
module "google_api_source" {
    source = "../../modules/eventarc_google_api_source"

    location             = "us-central1"
    google_api_source_id = "my-api-source"
    project              = var.project_id

    destination  = "projects/${var.project_id}/locations/us-central1/messageBuses/my-bus"
    display_name = "My Google API Source"

    logging_config = {
        log_severity = "WARNING"
    }

    labels = {
        environment = "production"
        team        = "platform"
    }

    annotations = {
        managed-by = "terraform"
    }
}
```

### 4. With Inline Message Bus Reference

```terraform
module "message_bus" {
    source = "../../modules/eventarc_message_bus"

    location       = "us-central1"
    message_bus_id = "my-bus"
    project        = var.project_id
}

module "google_api_source" {
    source = "../../modules/eventarc_google_api_source"

    location             = "us-central1"
    google_api_source_id = "my-api-source"
    project              = var.project_id

    destination = module.message_bus.id
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| google_api_source_id | User-provided ID for the GoogleApiSource. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$` | string | - | yes |
| destination | Full resource name of the destination MessageBus | string | - | yes |
| location | GCP location for the resource (e.g., `us-central1`) | string | - | yes |
| project | GCP project ID. Defaults to provider project | string | null | no |
| display_name | Human-readable display name | string | null | no |
| crypto_key_name | KMS crypto key for CMEK encryption. Must match `projects/*/locations/*/keyRings/*/cryptoKeys/*` | string | null | no |
| labels | User labels for the resource | map(string) | `{}` | no |
| annotations | User annotations for the resource | map(string) | `{}` | no |
| logging_config | Platform Telemetry logging configuration | object | null | no |

### logging_config Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| log_severity | Minimum log severity to send. One of: `NONE`, `DEBUG`, `INFO`, `NOTICE`, `WARNING`, `ERROR`, `CRITICAL`, `ALERT`, `EMERGENCY` | string | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/googleApiSources/{{google_api_source_id}}` |
| name | Resource name of the GoogleApiSource |
| uid | Server-assigned UUID4 identifier (unchanged until deletion) |
| create_time | Resource creation timestamp |
| update_time | Resource last-modified timestamp |
| etag | Server-computed checksum |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |
| effective_annotations | All annotations on the resource in GCP (Terraform + external) |

## Notes

1. **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
2. **CMEK**: When using `crypto_key_name`, the Eventarc service account must have `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key before the resource is created
3. **IAM Setup for CMEK**:
   ```bash
   gcloud kms keys add-iam-policy-binding KEY_NAME \
     --keyring=KEYRING --location=LOCATION \
     --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-eventarc.iam.gserviceaccount.com" \
     --role="roles/cloudkms.cryptoKeyEncrypterDecrypter"
   ```
4. **Labels/Annotations**: Non-authoritative — only manages entries present in configuration
5. **Timeouts**: Create, update, and delete operations each default to 20 minutes
