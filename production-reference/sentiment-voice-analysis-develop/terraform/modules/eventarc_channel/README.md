# Eventarc Channel Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Eventarc Channel resources. A Channel enables third-party event providers (e.g. Datadog, Splunk) to publish events into your Google Cloud project via a managed Pub/Sub transport topic. After creation, the `activation_token` output is shared with the provider to activate the channel.

## Usage Examples

### 1. Basic Channel (Google-managed events)

```terraform
module "channel" {
    source = "../../modules/eventarc_channel"

    name     = "my-channel"
    location = "us-central1"
    project  = var.project_id
}
```

### 2. Channel with Third-Party Provider

```terraform
module "channel" {
    source = "../../modules/eventarc_channel"

    name                 = "datadog-channel"
    location             = "us-central1"
    project              = var.project_id
    third_party_provider = "projects/${var.project_id}/locations/us-central1/providers/datadog"

    labels = {
        environment = "production"
        provider    = "datadog"
    }
}
```

### 3. Channel with CMEK Encryption

```terraform
# Grant the Eventarc service account access to the key before creating the channel
resource "google_kms_crypto_key_iam_member" "eventarc_sa_key_access" {
    crypto_key_id = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"
    role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
    member        = "serviceAccount:service-${var.project_number}@gcp-sa-eventarc.iam.gserviceaccount.com"
}

module "channel" {
    source = "../../modules/eventarc_channel"

    name            = "cmek-channel"
    location        = "us-central1"
    project         = var.project_id
    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"

    depends_on = [google_kms_crypto_key_iam_member.eventarc_sa_key_access]
}
```

### 4. Using the Activation Token to Register with a Provider

```terraform
module "channel" {
    source = "../../modules/eventarc_channel"

    name                 = "splunk-channel"
    location             = "us-central1"
    project              = var.project_id
    third_party_provider = "projects/${var.project_id}/locations/us-central1/providers/splunk"
}

# Pass the activation token to the provider registration resource
resource "some_provider_webhook" "registration" {
    token = module.channel.activation_token
    # ...
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Unique resource name for the channel within the location | string | - | yes |
| location | GCP location for the resource (e.g., `us-central1`) | string | - | yes |
| project | GCP project ID. Defaults to provider project | string | null | no |
| third_party_provider | SaaS event provider resource name (`projects/{p}/locations/{l}/providers/{id}`) | string | null | no |
| crypto_key_name | KMS crypto key for CMEK encryption (`projects/*/locations/*/keyRings/*/cryptoKeys/*`) | string | null | no |
| labels | User-defined labels. Non-authoritative | map(string) | `{}` | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/channels/{{name}}` |
| uid | Server-assigned UUID4 identifier (unchanged until deletion) |
| create_time | Channel creation timestamp |
| update_time | Channel last-modified timestamp |
| pubsub_topic | Managed Pub/Sub topic name used as event delivery transport |
| state | State of the channel |
| activation_token | Token to share with the third-party provider to activate the channel (sensitive) |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |

## Notes

1. **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
2. **Activation**: After creation, retrieve `activation_token` and provide it to the third-party event provider to activate the channel for publishing
3. **Pub/Sub Topic**: Eventarc automatically creates and manages a Pub/Sub topic (`pubsub_topic` output) as the internal transport — do not manage this topic directly
4. **CMEK**: When using `crypto_key_name`, the Eventarc service account (`service-PROJECT_NUMBER@gcp-sa-eventarc.iam.gserviceaccount.com`) must have `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key before applying. Use a `depends_on` or create the IAM binding first
5. **Labels**: Non-authoritative — only manages label entries present in configuration; labels set outside Terraform are preserved
6. **Timeouts**: Create, update, and delete operations each default to 20 minutes
