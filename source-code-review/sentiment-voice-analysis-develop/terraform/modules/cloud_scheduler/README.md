# Cloud Scheduler Job Module

> **Provider Compatibility**: Google Cloud Provider >= v5.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Scheduler jobs with:
- HTTP targets (Cloud Run, Cloud Functions, HTTP endpoints)
- OAuth token authentication (for GCP APIs)
- OIDC token authentication (for Cloud Run/Cloud Functions)
- Pub/Sub targets (topic triggers)
- App Engine HTTP targets
- Configurable retry policies
- Flexible scheduling with cron expressions

## Usage Examples

### 1. Cloud Run Job with OIDC
```terraform
module "cloud_run_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-data-processing"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 9 * * *"  # Daily at 9 AM
    time_zone  = "Asia/Bangkok"
    
    http_target = {
        uri         = "https://asia-southeast1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/data-processor:run"
        http_method = "POST"
        oidc_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
    
    retry_config = {
        retry_count          = 3
        max_retry_duration   = "0s"
        min_backoff_duration = "5s"
        max_backoff_duration = "3600s"
        max_doublings        = 5
    }
}
```

### 2. Cloud Run Service with OAuth
```terraform
module "cloud_run_service_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-api-endpoint"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "*/15 * * * *"  # Every 15 minutes
    
    http_target = {
        uri         = "https://my-service-abc123-uc.a.run.app/api/process"
        http_method = "POST"
        headers = {
            "Content-Type" = "application/json"
        }
        body = jsonencode({
            action = "process"
            env    = "production"
        })
        oauth_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
            scope                 = "https://www.googleapis.com/auth/cloud-platform"
        }
    }
}
```

### 3. Cloud Functions with OIDC
```terraform
module "cloud_function_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name        = "trigger-cleanup-function"
    project_id  = var.project_id
    region      = "asia-southeast1"
    description = "Triggers nightly cleanup function"
    schedule    = "0 2 * * *"  # Daily at 2 AM
    time_zone   = "Asia/Bangkok"
    
    http_target = {
        uri         = "https://asia-southeast1-${var.project_id}.cloudfunctions.net/cleanup"
        http_method = "POST"
        oidc_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
}
```

### 4. Pub/Sub Topic Trigger
```terraform
module "pubsub_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-event-processor"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 */6 * * *"  # Every 6 hours
    time_zone  = "UTC"
    
    pubsub_target = {
        topic_name = "projects/${var.project_id}/topics/scheduled-events"
        data       = jsonencode({
            event_type = "scheduled_processing"
            timestamp  = "{{ .ScheduleTime }}"
        })
        attributes = {
            source = "cloud-scheduler"
            env    = "production"
        }
    }
    
    retry_config = {
        retry_count = 5
    }
}
```

### 5. App Engine HTTP Target
```terraform
module "appengine_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-appengine-task"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 12 * * 1"  # Every Monday at noon
    
    app_engine_http_target = {
        http_method  = "POST"
        relative_uri = "/tasks/weekly-report"
        headers = {
            "Content-Type" = "application/json"
        }
        body = jsonencode({
            report_type = "weekly"
        })
        app_engine_routing = {
            service = "default"
            version = "v1"
        }
    }
}
```

### 6. Complex Retry Configuration
```terraform
module "resilient_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name             = "critical-job-trigger"
    project_id       = var.project_id
    region           = "asia-southeast1"
    schedule         = "0 8 * * *"
    attempt_deadline = "600s"  # 10 minutes per attempt
    
    http_target = {
        uri         = "https://api.example.com/critical-process"
        http_method = "POST"
        oauth_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
    
    retry_config = {
        retry_count          = 10              # Retry up to 10 times
        max_retry_duration   = "7200s"         # Give up after 2 hours total
        min_backoff_duration = "10s"           # Start with 10s backoff
        max_backoff_duration = "600s"          # Cap backoff at 10 minutes
        max_doublings        = 5               # Double 5 times, then use max
    }
}
```

### 7. Scheduled External API Call
```terraform
module "external_api_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "sync-external-data"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 */4 * * *"  # Every 4 hours
    
    http_target = {
        uri         = "https://external-api.example.com/sync"
        http_method = "POST"
        headers = {
            "Authorization" = "Bearer ${var.api_token}"
            "Content-Type"  = "application/json"
        }
        body = jsonencode({
            source      = "gcp-scheduler"
            sync_type   = "incremental"
        })
    }
    
    retry_config = {
        retry_count = 3
    }
}
```

### 8. Paused Job (Temporarily Disabled)
```terraform
module "paused_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "seasonal-task"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 0 1 * *"  # First day of each month
    paused     = true         # Disabled until needed
    
    http_target = {
        uri = "https://asia-southeast1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/seasonal-job:run"
        oidc_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
}
```

### 9. Multi-Region Deployment
```terraform
locals {
    regions = ["asia-southeast1", "us-central1", "europe-west1"]
}

module "multi_region_scheduler" {
    for_each = toset(local.regions)
    
    source = "../../modules/cloud_scheduler"
    
    name       = "health-check-${each.value}"
    project_id = var.project_id
    region     = each.value
    schedule   = "*/5 * * * *"  # Every 5 minutes
    
    http_target = {
        uri = "https://${each.value}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/health-check:run"
        oidc_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
}
```

### 10. Complete Production Example
```terraform
module "production_data_pipeline" {
    source = "../../modules/cloud_scheduler"
    
    name        = "production-etl-pipeline"
    project_id  = var.project_id
    region      = "asia-southeast1"
    description = "Triggers production ETL pipeline daily at 9 AM Bangkok time"
    
    # Daily at 9 AM, skip on days 1-4 (only run days 5-31)
    schedule  = "0 9 5-31 * *"
    time_zone = "Asia/Bangkok"
    
    # Allow up to 5 minutes per attempt
    attempt_deadline = "320s"
    
    # Cloud Run Job v2 API
    http_target = {
        uri         = "https://asia-southeast1-run.googleapis.com/apis/run.googleapis.com/v2/projects/${var.project_id}/locations/asia-southeast1/jobs/sentiment-analysis-batch:run"
        http_method = "POST"
        
        headers = {
            "Content-Type" = "application/json"
        }
        
        body = jsonencode({
            pipeline    = "sentiment_analysis"
            environment = "production"
            config = {
                batch_size = 1000
                parallel   = true
            }
        })
        
        # Use OIDC for Cloud Run authentication
        oidc_token = {
            service_account_email = "${var.project_number}-compute@developer.gserviceaccount.com"
            audience              = "https://asia-southeast1-run.googleapis.com/apis/run.googleapis.com/v2/projects/${var.project_id}/locations/asia-southeast1/jobs/sentiment-analysis-batch:run"
        }
    }
    
    # Retry configuration for resilience
    retry_config = {
        retry_count          = 3      # Retry up to 3 times
        max_retry_duration   = "0s"   # No time limit on retries
        min_backoff_duration = "5s"   # Wait 5s before first retry
        max_backoff_duration = "3600s" # Cap backoff at 1 hour
        max_doublings        = 5      # Exponential backoff
    }
}
```

### 11. Cloud Run Job with Custom Headers
```terraform
module "custom_headers_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "api-with-custom-headers"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 10 * * *"
    
    http_target = {
        uri         = "https://my-api-abc123-uc.a.run.app/process"
        http_method = "POST"
        
        headers = {
            "Content-Type"      = "application/json"
            "X-API-Version"     = "v2"
            "X-Source"          = "cloud-scheduler"
            "X-Environment"     = "production"
        }
        
        body = jsonencode({
            job_id = "scheduled-job"
            params = {
                mode = "batch"
            }
        })
        
        oauth_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
}
```

### 12. Pub/Sub with Complex Message
```terraform
module "pubsub_complex_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-workflow-orchestrator"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 0 * * 0"  # Weekly on Sunday at midnight
    
    pubsub_target = {
        topic_name = "projects/${var.project_id}/topics/workflow-triggers"
        
        data = jsonencode({
            workflow_id = "weekly-aggregation"
            config = {
                date_range = "last_7_days"
                output     = "gs://${var.project_id}-reports/weekly"
            }
            notification = {
                email = "team@example.com"
                slack = "engineering-alerts"
            }
        })
        
        attributes = {
            priority    = "high"
            source      = "cloud-scheduler"
            workflow    = "weekly-aggregation"
            environment = "production"
        }
    }
    
    retry_config = {
        retry_count = 5
    }
}
```

### 13. App Engine with Routing
```terraform
module "appengine_routed_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "trigger-backend-service"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = "0 3 * * *"  # Daily at 3 AM
    
    app_engine_http_target = {
        http_method  = "POST"
        relative_uri = "/admin/tasks/database-maintenance"
        
        headers = {
            "Content-Type" = "application/json"
            "X-Admin-Key"  = var.admin_key
        }
        
        body = jsonencode({
            task        = "vacuum_analyze"
            tables      = ["users", "events", "analytics"]
            aggressive  = false
        })
        
        # Route to specific service and version
        app_engine_routing = {
            service  = "backend"
            version  = "v2-stable"
            instance = ""  # Any instance
        }
    }
    
    retry_config = {
        retry_count          = 2
        min_backoff_duration = "60s"
    }
}
```

### 14. Dynamic Schedule with Variables
```terraform
variable "environment" {
    type    = string
    default = "production"
}

locals {
    # Different schedules per environment
    schedule_map = {
        production  = "0 9 5-31 * *"   # Daily at 9 AM, days 5-31
        staging     = "0 10 * * *"     # Daily at 10 AM
        development = "0 */6 * * *"    # Every 6 hours
    }
    
    retry_count_map = {
        production  = 5
        staging     = 3
        development = 1
    }
}

module "environment_scheduler" {
    source = "../../modules/cloud_scheduler"
    
    name       = "${var.environment}-data-job"
    project_id = var.project_id
    region     = "asia-southeast1"
    schedule   = local.schedule_map[var.environment]
    
    http_target = {
        uri = "https://asia-southeast1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/data-processor:run"
        oidc_token = {
            service_account_email = "scheduler@${var.project_id}.iam.gserviceaccount.com"
        }
    }
    
    retry_config = {
        retry_count = local.retry_count_map[var.environment]
    }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Scheduler job name | string | - | yes |
| project_id | GCP project ID | string | - | yes |
| region | GCP region | string | - | yes |
| description | Job description | string | "" | no |
| schedule | Cron expression (e.g., '0 9 * * *') | string | null | no |
| time_zone | IANA timezone (e.g., 'Asia/Bangkok') | string | "UTC" | no |
| paused | Whether job is paused | bool | false | no |
| attempt_deadline | Deadline per attempt (e.g., '320s') | string | null | no |
| retry_config | Retry configuration object | object | null | no |
| http_target | HTTP target configuration | object | null | no |
| pubsub_target | Pub/Sub target configuration | object | null | no |
| app_engine_http_target | App Engine target configuration | object | null | no |

### retry_config Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| retry_count | Number of retry attempts | number | no |
| max_retry_duration | Max total retry time (e.g., '3600s') | string | no |
| min_backoff_duration | Initial backoff (e.g., '5s') | string | no |
| max_backoff_duration | Max backoff (e.g., '3600s') | string | no |
| max_doublings | Exponential backoff doublings | number | no |

### http_target Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| uri | Target HTTP/HTTPS URI | string | yes |
| http_method | HTTP method (GET/POST/PUT/DELETE/PATCH/HEAD) | string | no |
| body | Request body (auto base64-encoded) | string | no |
| headers | HTTP headers map | map(string) | no |
| oauth_token | OAuth authentication config | object | no |
| oidc_token | OIDC authentication config | object | no |

### oauth_token Object (within http_target)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service_account_email | Service account email | string | yes |
| scope | OAuth scope | string | no |

### oidc_token Object (within http_target)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service_account_email | Service account email | string | yes |
| audience | OIDC audience (defaults to uri) | string | no |

### pubsub_target Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| topic_name | Full topic name (projects/*/topics/*) | string | yes |
| data | Message data (auto base64-encoded) | string | no |
| attributes | Message attributes map | map(string) | no |

### app_engine_http_target Object
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| relative_uri | Relative URI path | string | yes |
| http_method | HTTP method | string | no |
| body | Request body (auto base64-encoded) | string | no |
| headers | HTTP headers map | map(string) | no |
| app_engine_routing | Routing configuration | object | no |

### app_engine_routing Object (within app_engine_http_target)
| Field | Description | Type | Required |
|-------|-------------|------|----------|
| service | App Engine service name | string | no |
| version | App Engine version | string | no |
| instance | Specific instance ID | string | no |

## Outputs

| Name | Description |
|------|-------------|
| scheduler_job_id | Full job resource ID |
| scheduler_job_name | Job name |
| scheduler_job_state | Job state (ENABLED/PAUSED) |

## Cron Schedule Examples

| Schedule | Description |
|----------|-------------|
| `0 9 * * *` | Daily at 9:00 AM |
| `*/15 * * * *` | Every 15 minutes |
| `0 */6 * * *` | Every 6 hours |
| `0 9 * * 1` | Every Monday at 9 AM |
| `0 0 1 * *` | First day of month at midnight |
| `0 9 5-31 * *` | Daily at 9 AM, days 5-31 only |
| `30 2 * * 0` | Every Sunday at 2:30 AM |
| `0 9 * * 1-5` | Weekdays at 9 AM |

## Authentication Types

### OAuth Token
- **Use Case**: Calling GCP APIs, Cloud Run services, Cloud Functions
- **Authentication**: OAuth 2.0 with service account
- **Scope**: Optional (defaults to cloud-platform)
- **Example**: Triggering Cloud Run services

### OIDC Token
- **Use Case**: Cloud Run, Cloud Functions (recommended)
- **Authentication**: OpenID Connect with service account
- **Audience**: Optional (defaults to target URI)
- **Example**: Cloud Run Jobs API v2

### No Authentication
- **Use Case**: Public HTTP endpoints, external APIs with API keys
- **Authentication**: None (use headers for API keys)
- **Example**: Third-party webhooks

## Notes

1. **Provider Version**: Requires Google Provider >= v5.0 (tested with v7.20.0)
2. **Target Type**: Exactly one of `http_target`, `pubsub_target`, or `app_engine_http_target` must be specified
3. **Authentication**: For Cloud Run/Functions, prefer OIDC over OAuth
4. **Service Account**: Must have appropriate IAM roles on target resource
5. **Schedule Required**: Schedule is required unless job is paused
6. **Body Encoding**: Request bodies are automatically base64-encoded
7. **Cron Format**: Uses Unix cron format (minute hour day month day-of-week)
8. **Time Zone**: Use IANA timezone names (e.g., 'Asia/Bangkok', 'America/New_York')
9. **Retry Behavior**: Retries use exponential backoff up to max_backoff_duration
10. **Attempt Deadline**: Independent timeout per attempt (separate from retry duration)
11. **Cloud Run Jobs**: Use v2 API format for Cloud Run Jobs
12. **Region**: Must match the region of Cloud Run/Cloud Functions targets
13. **Pub/Sub**: Service account needs `pubsub.publisher` role on topic

## IAM Requirements

### For HTTP Targets (Cloud Run/Functions)
```bash
# Service account needs invoker role
gcloud run services add-iam-policy-binding SERVICE_NAME \
  --member="serviceAccount:SCHEDULER_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/run.invoker"
```

### For Pub/Sub Targets
```bash
# Service account needs publisher role
gcloud pubsub topics add-iam-policy-binding TOPIC_NAME \
  --member="serviceAccount:SCHEDULER_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/pubsub.publisher"
```

### For App Engine Targets
```bash
# Service account needs App Engine Admin role
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:SCHEDULER_SA@PROJECT.iam.gserviceaccount.com" \
  --role="roles/appengine.appAdmin"
```
