# Eventarc Google Channel Config Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for managing the Google Cloud Eventarc GoogleChannelConfig resource. This is a **singleton resource** — one per project per location — that configures CMEK encryption for Google Channel events (e.g., Cloud Audit Logs, direct Google API events).

## Usage Examples

### 1. Enable CMEK for Google Channel

```terraform
module "google_channel_config" {
    source = "../../modules/eventarc_google_channel_config"

    location = "us-central1"
    project  = var.project_id

    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"
}
```

### 2. Manage Config Without CMEK

```terraform
module "google_channel_config" {
    source = "../../modules/eventarc_google_channel_config"

    location = "us-central1"
    project  = var.project_id
}
```

### 3. Full Example with IAM Setup

```terraform
data "google_project" "current" {
    project_id = var.project_id
}

resource "google_kms_crypto_key_iam_member" "eventarc_key_access" {
    crypto_key_id = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"
    role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
    member        = "serviceAccount:service-${data.google_project.current.number}@gcp-sa-eventarc.iam.gserviceaccount.com"
}

module "google_channel_config" {
    source = "../../modules/eventarc_google_channel_config"

    location = "us-central1"
    project  = var.project_id

    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"

    depends_on = [google_kms_crypto_key_iam_member.eventarc_key_access]
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| location | GCP location for the resource (e.g., `us-central1`) | string | - | yes |
| name | Resource name suffix. Must be `googleChannelConfig` | string | `"googleChannelConfig"` | no |
| crypto_key_name | KMS crypto key for CMEK encryption. Must match `projects/*/locations/*/keyRings/*/cryptoKeys/*` | string | null | no |
| project | GCP project ID. Defaults to provider project | string | null | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/googleChannelConfig` |
| update_time | Resource last-modified timestamp |

## Notes

1. **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
2. **Singleton**: There is exactly one `googleChannelConfig` per project per location. This resource manages its configuration (e.g., CMEK key); it cannot be deleted via Terraform — only updated
3. **CMEK**: When using `crypto_key_name`, the Eventarc service account must have `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key before applying
4. **IAM Setup for CMEK**:
   ```bash
   gcloud kms keys add-iam-policy-binding KEY_NAME \
     --keyring=KEYRING --location=LOCATION \
     --member="serviceAccount:service-PROJECT_NUMBER@gcp-sa-eventarc.iam.gserviceaccount.com" \
     --role="roles/cloudkms.cryptoKeyEncrypterDecrypter"
   ```
5. **Timeouts**: Create, update, and delete operations each default to 20 minutes
