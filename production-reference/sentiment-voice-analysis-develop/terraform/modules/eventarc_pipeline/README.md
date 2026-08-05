# Eventarc Pipeline Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Eventarc Pipeline resources. A Pipeline processes events from Message Bus enrollments and forwards them to destinations such as HTTP endpoints, Pub/Sub topics, Workflows, or other Message Buses — with optional payload format conversion, authentication, retry logic, and CEL-based message transformation.

## Usage Examples

### 1. Pub/Sub Topic Destination

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "my-pipeline"
    project     = var.project_id

    destinations = [{
        topic = "projects/${var.project_id}/locations/us-central1/topics/my-topic"
    }]
}
```

### 2. HTTP Endpoint Destination (Private VPC)

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "http-pipeline"
    project     = var.project_id

    destinations = [{
        http_endpoint = {
            uri = "https://10.77.0.0:80/route"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/us-central1/networkAttachments/my-attachment"
        }
    }]
}
```

### 3. Workflow Destination

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "workflow-pipeline"
    project     = var.project_id

    destinations = [{
        workflow = "projects/${var.project_id}/locations/us-central1/workflows/my-workflow"
    }]
}
```

### 4. HTTP Endpoint with OIDC Auth and JSON Format

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "oidc-pipeline"
    project     = var.project_id

    destinations = [{
        http_endpoint = {
            uri                      = "https://10.77.0.0:80/route"
            message_binding_template = "{\"headers\":{\"x-custom-header\": \"value\"}}"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/us-central1/networkAttachments/my-attachment"
        }
        authentication_config = {
            google_oidc = {
                service_account = "sa@${var.project_id}.iam.gserviceaccount.com"
                audience        = "https://my-service.example.com"
            }
        }
        output_payload_format = {
            json = {}
        }
    }]

    input_payload_format = {
        json = {}
    }
}
```

### 5. HTTP Endpoint with OAuth Token and Protobuf Format

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "oauth-pipeline"
    project     = var.project_id

    destinations = [{
        http_endpoint = {
            uri = "https://10.77.0.0:80/route"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/us-central1/networkAttachments/my-attachment"
        }
        authentication_config = {
            oauth_token = {
                service_account = "sa@${var.project_id}.iam.gserviceaccount.com"
                scope           = "https://www.googleapis.com/auth/cloud-platform"
            }
        }
        output_payload_format = {
            protobuf = {
                schema_definition = <<-EOF
                    syntax = "proto3";
                    message Event { string name = 1; string severity = 2; }
                EOF
            }
        }
    }]

    input_payload_format = {
        protobuf = {
            schema_definition = <<-EOF
                syntax = "proto3";
                message Event { string name = 1; string severity = 2; }
            EOF
        }
    }
}
```

### 6. Pipeline with Retry Policy and CEL Transformation

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "advanced-pipeline"
    project     = var.project_id

    destinations = [{
        http_endpoint = {
            uri = "https://10.77.0.0:80/events"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/us-central1/networkAttachments/my-attachment"
        }
    }]

    retry_policy = {
        max_attempts    = 5
        min_retry_delay = "5s"
        max_retry_delay = "60s"
    }

    mediations = [{
        transformation = {
            transformation_template = <<-EOF
                {
                    "id": message.id,
                    "datacontenttype": "application/json",
                    "data": "{ \"scrubbed\": \"true\" }"
                }
            EOF
        }
    }]

    logging_config = {
        log_severity = "DEBUG"
    }

    display_name = "Advanced Pipeline"

    labels = {
        environment = "production"
        team        = "platform"
    }
}
```

### 7. Pipeline with CMEK and Avro Format

```terraform
module "pipeline" {
    source = "../../modules/eventarc_pipeline"

    location    = "us-central1"
    pipeline_id = "cmek-pipeline"
    project     = var.project_id

    crypto_key_name = "projects/${var.project_id}/locations/us-central1/keyRings/my-ring/cryptoKeys/my-key"

    destinations = [{
        http_endpoint = {
            uri = "https://10.77.0.0:80/events"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/us-central1/networkAttachments/my-attachment"
        }
        output_payload_format = {
            avro = {
                schema_definition = "{\"type\": \"record\", \"name\": \"Event\", \"fields\": [{\"name\": \"id\", \"type\": \"string\"}]}"
            }
        }
    }]

    input_payload_format = {
        avro = {
            schema_definition = "{\"type\": \"record\", \"name\": \"Event\", \"fields\": [{\"name\": \"id\", \"type\": \"string\"}]}"
        }
    }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| pipeline_id | User-provided ID for the Pipeline. Must match `^[a-z]([a-z0-9-]{0,61}[a-z0-9])?$` | string | - | yes |
| location | GCP location for the resource (e.g., `us-central1`) | string | - | yes |
| destinations | List of destinations. Currently exactly one destination is supported | list(object) | - | yes |
| project | GCP project ID. Defaults to provider project | string | null | no |
| display_name | Human-readable display name | string | null | no |
| crypto_key_name | KMS crypto key for CMEK encryption | string | null | no |
| labels | User labels for the resource | map(string) | `{}` | no |
| annotations | User-defined annotations | map(string) | `{}` | no |
| input_payload_format | Format of incoming message data | object | null | no |
| retry_policy | Retry policy configuration | object | null | no |
| mediations | List of mediation operations (currently max 1 transformation) | list(object) | `[]` | no |
| logging_config | Platform Telemetry logging configuration | object | null | no |

### destinations Object

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| workflow | Full resource name of a Workflow | string | no |
| message_bus | Full resource name of a MessageBus | string | no |
| topic | Full resource name of a Pub/Sub topic | string | no |
| http_endpoint | HTTP endpoint configuration (see sub-table) | object | no |
| network_config | VPC network attachment for private HTTP endpoints | object | no |
| authentication_config | Auth config for HTTP destinations | object | no |
| output_payload_format | Format conversion for outbound messages | object | no |

### http_endpoint Object (within destinations)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| uri | RFC2396 URI of the HTTPS endpoint (HTTPS only) | string | yes |
| message_binding_template | CEL expression to customize HTTP request construction | string | no |

### network_config Object (within destinations)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| network_attachment | NetworkAttachment resource name (`projects/{p}/regions/{r}/networkAttachments/{n}`) | string | no |

### authentication_config Object (within destinations)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| google_oidc | Google OIDC token auth (for Cloud Run, Cloud Functions, OIDC-aware endpoints) | object | no |
| oauth_token | OAuth2 token auth (for Google APIs on *.googleapis.com) | object | no |

### google_oidc Object (within authentication_config)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service_account | Service account email to generate the OIDC token | string | yes |
| audience | JWT audience claim. Defaults to the destination URI | string | no |

### oauth_token Object (within authentication_config)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service_account | Service account email to generate the OAuth token | string | yes |
| scope | OAuth scope. Defaults to `https://www.googleapis.com/auth/cloud-platform` | string | no |

### output_payload_format / input_payload_format Object

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| json | Enable JSON format (set to `{}` to activate) | object({}) | no |
| avro | Enable Avro format (see sub-table) | object | no |
| protobuf | Enable Protobuf format (see sub-table) | object | no |

### avro / protobuf Object (within payload format)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| schema_definition | Full schema definition string | string | no |

### retry_policy Object

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| max_retry_delay | Maximum delay between retries (e.g., `"60s"`). Must be 1–600s | string | no |
| max_attempts | Maximum delivery attempts (1–100). Default is 5 | number | no |
| min_retry_delay | Minimum delay between retries (e.g., `"5s"`). Must be 1–600s | string | no |

### mediations Object

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| transformation | CEL transformation to apply to the message | object | no |

### transformation Object (within mediations)

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| transformation_template | CEL expression that transforms the message | string | yes |

### logging_config Object

| Field | Description | Type | Required |
|-------|-------------|------|----------|
| log_severity | Minimum log severity. One of: `NONE`, `DEBUG`, `INFO`, `NOTICE`, `WARNING`, `ERROR`, `CRITICAL`, `ALERT`, `EMERGENCY` | string | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/pipelines/{{pipeline_id}}` |
| name | Resource name of the Pipeline |
| uid | Server-assigned UUID4 identifier (unchanged until deletion) |
| create_time | Pipeline creation timestamp |
| update_time | Pipeline last-modified timestamp |
| etag | Server-computed checksum |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |
| effective_annotations | All annotations on the resource in GCP (Terraform + external) |

## Notes

1. **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
2. **Destinations**: Currently exactly one destination is supported per Pipeline
3. **Network Config**: Only valid with `http_endpoint`; requires a pre-provisioned Network Attachment. Must not be set for `workflow`, `message_bus`, or `topic` destinations
4. **Authentication**: `google_oidc` is for Cloud Run, Cloud Functions, and OIDC-aware endpoints; `oauth_token` is for Google APIs hosted on `*.googleapis.com`
5. **Payload Format**: Set `input_payload_format` to enable subfield access in CEL expressions; set `output_payload_format` per destination for format conversion
6. **Mediations**: Currently only one `transformation` mediation is allowed per Pipeline
7. **Retry Policy**: The pipeline exponentially backs off starting at 5s, doubling each attempt, capped at `max_retry_delay` (default 60s)
8. **CMEK**: When using `crypto_key_name`, the Eventarc service account must have `roles/cloudkms.cryptoKeyEncrypterDecrypter` on the key before applying
9. **Labels/Annotations**: Non-authoritative — only manages entries present in configuration
10. **Timeouts**: Create, update, and delete operations each default to 20 minutes
