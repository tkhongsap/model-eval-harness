# Eventarc Trigger Module

> **Provider Compatibility**: Google Cloud Provider = v7.20.0 (pinned exact version)

Terraform module for creating and managing Google Cloud Eventarc triggers with:
- Cloud Run service destinations
- GKE service destinations
- Workflows destinations
- HTTP endpoint destinations (including private VPC via Network Attachment)
- Pub/Sub transport intermediary
- Flexible CloudEvent attribute filtering (with `match-path-pattern` operator)
- Retry policy configuration
- Custom event data content type

## Usage Examples

### 1. Cloud Run Destination (Pub/Sub trigger)
```terraform
module "eventarc_pubsub_trigger" {
    source = "../../modules/eventarc"

    name     = "pubsub-to-cloud-run"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.pubsub.topic.v1.messagePublished"
        }
    ]

    destination = {
        cloud_run_service = {
            service = "my-processing-service"
            region  = "asia-southeast1"
        }
    }

    transport = {
        pubsub = {
            topic = "projects/${var.project_id}/topics/my-topic"
        }
    }

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"

    labels = {
        environment = "production"
        team        = "data"
    }
}
```

### 2. Cloud Run Destination with Audit Log
```terraform
module "eventarc_audit_trigger" {
    source = "../../modules/eventarc"

    name     = "gcs-audit-to-cloud-run"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.audit.log.v1.written"
        },
        {
            attribute = "serviceName"
            value     = "storage.googleapis.com"
        },
        {
            attribute = "methodName"
            value     = "storage.objects.create"
        }
    ]

    destination = {
        cloud_run_service = {
            service = "file-processor"
            region  = "asia-southeast1"
            path    = "/process"
        }
    }

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"

    retry_policy = {
        max_attempts = 1
    }
}
```

### 3. GKE Destination
```terraform
module "eventarc_gke_trigger" {
    source = "../../modules/eventarc"

    name     = "pubsub-to-gke"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.pubsub.topic.v1.messagePublished"
        }
    ]

    destination = {
        gke = {
            cluster   = "projects/${var.project_id}/locations/asia-southeast1/clusters/my-cluster"
            location  = "asia-southeast1"
            namespace = "default"
            service   = "my-gke-service"
            path      = "/events"
        }
    }

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"
}
```

### 4. Workflow Destination
```terraform
module "eventarc_workflow_trigger" {
    source = "../../modules/eventarc"

    name     = "gcs-to-workflow"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.storage.object.v1.finalized"
        },
        {
            attribute = "bucket"
            value     = "my-input-bucket"
            operator  = "match-path-pattern"
        }
    ]

    destination = {
        workflow = "projects/${var.project_id}/locations/asia-southeast1/workflows/my-workflow"
    }

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"

    labels = {
        pipeline = "etl"
    }
}
```

### 5. HTTP Endpoint Destination (Private VPC)
```terraform
module "eventarc_http_trigger" {
    source = "../../modules/eventarc"

    name     = "pubsub-to-http-endpoint"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.pubsub.topic.v1.messagePublished"
        }
    ]

    destination = {
        http_endpoint = {
            uri = "http://10.0.0.5:8080/events"
        }
        network_config = {
            network_attachment = "projects/${var.project_id}/regions/asia-southeast1/networkAttachments/my-attachment"
        }
    }

    transport = {
        pubsub = {
            topic = "projects/${var.project_id}/topics/internal-events"
        }
    }

    event_data_content_type = "application/json"
}
```

### 6. Eventarc SaaS Partner (Channel)
```terraform
module "eventarc_partner_trigger" {
    source = "../../modules/eventarc"

    name     = "partner-event-trigger"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "com.example.partner.v1.event"
        }
    ]

    destination = {
        cloud_run_service = {
            service = "partner-event-handler"
            region  = "asia-southeast1"
        }
    }

    channel = "projects/${var.project_id}/locations/asia-southeast1/channels/my-partner-channel"

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"
}
```

### 7. Complete Production Example
```terraform
module "eventarc_production_trigger" {
    source = "../../modules/eventarc"

    name     = "voice-upload-processor"
    location = "asia-southeast1"
    project  = var.project_id

    matching_criteria = [
        {
            attribute = "type"
            value     = "google.cloud.storage.object.v1.finalized"
        },
        {
            attribute = "bucket"
            value     = "${var.project_id}-voice-uploads"
        }
    ]

    destination = {
        cloud_run_service = {
            service = "voice-analysis-service"
            region  = "asia-southeast1"
            path    = "/process"
        }
    }

    service_account = "eventarc-sa@${var.project_id}.iam.gserviceaccount.com"

    transport = {
        pubsub = {
            topic = "projects/${var.project_id}/topics/voice-events"
        }
    }

    retry_policy = {
        max_attempts = 1
    }

    event_data_content_type = "application/json"

    labels = {
        environment = "production"
        pipeline    = "voice-analysis"
        team        = "ml"
    }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Unique trigger name within the location and project | string | - | yes |
| location | GCP location for the trigger (e.g., `asia-southeast1`) | string | - | yes |
| matching_criteria | List of CloudEvent attribute filters. Must include a filter for `type` | list(object) | - | yes |
| destination | Destination for events. Exactly one of `cloud_run_service`, `gke`, `workflow`, or `http_endpoint` must be set | object | - | yes |
| project | GCP project ID. Defaults to provider project | string | null | no |
| service_account | IAM service account email for the trigger identity | string | null | no |
| transport | Pub/Sub intermediary transport configuration | object | null | no |
| labels | User labels for the trigger | map(string) | `{}` | no |
| channel | Channel name for Eventarc SaaS partner events (`projects/{p}/locations/{l}/channels/{c}`) | string | null | no |
| event_data_content_type | MIME type of CloudEvent data payload. Defaults to `application/json` | string | null | no |
| retry_policy | Retry policy (Cloud Run destinations only). Only valid `max_attempts` value is `1` | object | null | no |

### matching_criteria Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| attribute | CloudEvents attribute name (e.g., `type`, `serviceName`, `bucket`) | string | yes |
| value | Value to match for the attribute | string | yes |
| operator | Matching operator. Only allowed value is `match-path-pattern` | string | no |

### destination Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| cloud_run_service | Cloud Run service destination (see sub-table) | object | no |
| gke | GKE service destination (see sub-table) | object | no |
| workflow | Workflow resource name (`projects/{p}/locations/{l}/workflows/{w}`) | string | no |
| http_endpoint | HTTP endpoint destination (see sub-table) | object | no |
| network_config | Network config for private HTTP endpoint connectivity (see sub-table) | object | no |

### cloud_run_service Object (within destination)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service | Cloud Run service name | string | yes |
| region | Region the Cloud Run service is deployed in | string | no |
| path | Relative URI path on the service (e.g., `/route`) | string | no |

### gke Object (within destination)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| cluster | Full cluster resource name | string | yes |
| location | Compute zone or region of the cluster | string | yes |
| namespace | Kubernetes namespace | string | yes |
| service | GKE service name | string | yes |
| path | Relative URI path on the service | string | no |

### http_endpoint Object (within destination)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| uri | RFC2396 URI of the HTTP endpoint (HTTP/HTTPS only) | string | yes |

### network_config Object (within destination)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| network_attachment | NetworkAttachment resource name (`projects/{p}/regions/{r}/networkAttachments/{n}`) | string | yes |

### transport Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| pubsub | Pub/Sub topic configuration (see sub-table) | object | no |

### pubsub Object (within transport)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| topic | Existing Pub/Sub topic name. Required only for `google.cloud.pubsub.topic.v1.messagePublished` triggers | string | no |

### retry_policy Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| max_attempts | Maximum delivery attempts. Only valid value is `1` | number | no |

## Outputs

| Name | Description |
|------|-------------|
| id | Full resource identifier: `projects/{{project}}/locations/{{location}}/triggers/{{name}}` |
| name | The resource name of the trigger |
| uid | Server-assigned UUID4 identifier (unchanged until deletion) |
| create_time | Trigger creation timestamp |
| update_time | Trigger last-modified timestamp |
| etag | Server-computed checksum |
| conditions | Reason(s) why a trigger is in FAILED state |
| effective_labels | All labels on the resource in GCP (Terraform + external) |
| terraform_labels | Combined Terraform-managed and provider-default labels |

## Notes

1. **Provider Version**: Requires Google Cloud Provider = v7.20.0 (pinned exact version)
2. **Destination**: Exactly one of `cloud_run_service`, `gke`, `workflow`, or `http_endpoint` must be set inside `destination`
3. **Type filter**: All triggers must include a `matching_criteria` entry for the `type` attribute
4. **Cloud Function**: Cloud Functions V2 triggers cannot be created via this resource — use the Cloud Functions product directly
5. **Retry Policy**: Only supported with Cloud Run destinations; the only valid `max_attempts` value is `1`
6. **Network Config**: Only valid when used with `http_endpoint`; requires a pre-provisioned Network Attachment
7. **Transport Topic**: For `google.cloud.pubsub.topic.v1.messagePublished` triggers you may supply an existing topic; Eventarc will manage the subscription
8. **Service Account**: Must have `roles/eventarc.eventReceiver` for Audit Log triggers and appropriate invoker roles on the destination
9. **Labels**: Non-authoritative — only labels in config are managed; see `effective_labels` for all labels present on the resource
10. **Timeouts**: Create, update, and delete operations each default to 20 minutes

## IAM Requirements

### For Cloud Run Destinations
```bash
gcloud run services add-iam-policy-binding SERVICE_NAME \
  --member="serviceAccount:EVENTARC_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker" \
  --region=REGION
```

### For Audit Log Triggers
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:EVENTARC_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/eventarc.eventReceiver"
```

### For GKE Destinations
```bash
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:EVENTARC_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/container.developer"
```
