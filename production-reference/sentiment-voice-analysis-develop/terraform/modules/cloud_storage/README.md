# Cloud Storage Bucket Module

> **Provider Compatibility**: Google Cloud Provider >= v7.20.0 (tested with v7.20.0)

Terraform module for creating and managing Google Cloud Storage buckets with:
- IAM bindings
- Lifecycle rules (with advanced conditions)
- Versioning
- CMEK encryption
- CORS configuration
- Logging
- Retention policies
- Object uploads
- Pub/Sub notifications
- Autoclass (automatic storage class transitions)
- Static website hosting
- Hierarchical namespace (folder support)
- Soft delete policy
- IP filtering
- Dual-region with custom placement
- Object retention (compliance/legal hold)
- Enhanced lifecycle conditions (prefix/suffix matching, custom time)

## Usage Examples

### 1. Basic Bucket
```terraform
module "basic_bucket" {
    source = "../../modules/cloud_storage"

    name       = "my-project-data-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"
}
```

### 2. Bucket with IAM Permissions
```terraform
module "shared_bucket" {
    source = "../../modules/cloud_storage"

    name       = "shared-data-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    labels = {
        environment = "production"
        team        = "data"
    }

    iam_members = {
        viewer1 = {
            role   = "roles/storage.objectViewer"
            member = "user:analyst@example.com"
        }
        admin1 = {
            role   = "roles/storage.admin"
            member = "serviceAccount:app@project.iam.gserviceaccount.com"
        }
    }
}
```

### 3. Bucket with Lifecycle Rules
```terraform
module "archive_bucket" {
    source = "../../modules/cloud_storage"

    name       = "archived-logs-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    lifecycle_rules = [
        {
            action = {
                type          = "SetStorageClass"
                storage_class = "NEARLINE"
            }
            condition = {
                age = 30
            }
        },
        {
            action = {
                type          = "SetStorageClass"
                storage_class = "COLDLINE"
            }
            condition = {
                age = 90
            }
        },
        {
            action = {
                type = "Delete"
            }
            condition = {
                age = 365
            }
        }
    ]
}
```

### 4. Versioned Bucket with Retention
```terraform
module "compliance_bucket" {
    source = "../../modules/cloud_storage"

    name              = "compliance-records"
    project_id        = var.project_id
    location          = "asia-southeast1"
    versioning_enabled = true

    retention_policy = {
        retention_period = 2592000  # 30 days in seconds
        is_locked        = false    # Set true to lock permanently
    }

    lifecycle_rules = [
        {
            action = {
                type = "Delete"
            }
            condition = {
                num_newer_versions = 3
            }
        }
    ]
}
```

### 5. Encrypted Bucket with CMEK
```terraform
module "encrypted_bucket" {
    source = "../../modules/cloud_storage"

    name       = "sensitive-data-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    kms_key_name = "projects/${var.project_id}/locations/asia-southeast1/keyRings/my-ring/cryptoKeys/my-key"

    public_access_prevention    = "enforced"
    uniform_bucket_level_access = true
}
```

### 6. Bucket with Logging
```terraform
module "logging_bucket" {
    source = "../../modules/cloud_storage"

    name       = "logs-storage-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"
}

module "app_bucket" {
    source = "../../modules/cloud_storage"

    name       = "app-data-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    logging_config = {
        log_bucket        = module.logging_bucket.bucket_name
        log_object_prefix = "app-logs/"
    }
}
```

### 7. Bucket with CORS
```terraform
module "web_assets_bucket" {
    source = "../../modules/cloud_storage"

    name       = "web-assets-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    cors_rules = [
        {
            origin          = ["https://example.com", "https://www.example.com"]
            method          = ["GET", "HEAD"]
            response_header = ["Content-Type"]
            max_age_seconds = 3600
        }
    ]
}
```

### 8. Bucket with Pub/Sub Notifications
```terraform
module "event_bucket" {
    source = "../../modules/cloud_storage"

    name       = "event-driven-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    notification_config = {
        topic          = "projects/${var.project_id}/topics/bucket-events"
        payload_format = "JSON_API_V1"
        event_types    = ["OBJECT_FINALIZE", "OBJECT_DELETE"]
    }
}
```

### 9. Upload Objects to Bucket
```terraform
module "config_bucket" {
    source = "../../modules/cloud_storage"

    name       = "app-config-bucket"
    project_id = var.project_id
    location   = "asia-southeast1"

    objects = {
        "config/app.json" = {
            source = "./configs/app.json"
        }
        "config/env.txt" = {
            content = "ENVIRONMENT=production"
        }
    }
}
```

### 10. Complete Production Example
```terraform
module "production_data_bucket" {
    source = "../../modules/cloud_storage"

    name       = "production-data-lake"
    project_id = var.project_id
    location   = "asia-southeast1"

    storage_class               = "STANDARD"
    versioning_enabled          = true
    public_access_prevention    = "enforced"
    uniform_bucket_level_access = true

    labels = {
        environment = "production"
        team        = "data-platform"
        criticality = "high"
    }

    # CMEK encryption
    kms_key_name = "projects/${var.project_id}/locations/asia-southeast1/keyRings/data-ring/cryptoKeys/data-key"

    # Lifecycle management
    lifecycle_rules = [
        {
            action = {
                type          = "SetStorageClass"
                storage_class = "NEARLINE"
            }
            condition = {
                age                   = 30
                matches_storage_class = [\"STANDARD\"]
            }
        },
        {
            action = {
                type          = "SetStorageClass"
                storage_class = "ARCHIVE"
            }
            condition = {
                age = 365
            }
        }
    ]

    # IAM permissions
    iam_members = {
        data_engineers = {
            role   = "roles/storage.objectAdmin"
            member = "group:data-engineers@example.com"
        }
        app_service_account = {
            role   = "roles/storage.objectCreator"
            member = "serviceAccount:app@${var.project_id}.iam.gserviceaccount.com"
        }
        analysts = {
            role   = "roles/storage.objectViewer"
            member = "group:analysts@example.com"
        }
    }

    # Logging
    logging_config = {
        log_bucket        = "audit-logs-bucket"
        log_object_prefix = "data-lake-access/"
    }

    # Pub/Sub notifications
    notification_config = {
        topic          = "projects/${var.project_id}/topics/data-ingestion"
        payload_format = "JSON_API_V1"
        event_types    = ["OBJECT_FINALIZE"]
    }
}
```

### 11. Autoclass Enabled Bucket
```terraform
module "autoclass_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-autoclass"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    # Autoclass automatically transitions objects based on access patterns
    autoclass = {
        enabled                = true
        terminal_storage_class = "ARCHIVE"  # Objects eventually move to ARCHIVE
    }

    labels = {
        env     = "nprd"
        purpose = "auto-tiering"
    }
}
```

### 12. Static Website Hosting
```terraform
module "website_bucket" {
    source = "../../modules/cloud_storage"

    name       = "my-static-website-bucket"
    project_id = var.gcp_project_id
    location   = "US"

    # Website configuration
    website = {
        main_page_suffix = "index.html"
        not_found_page   = "404.html"
    }

    # CORS for website
    cors_rules = [
        {
            origin          = ["https://example.com"]
            method          = ["GET", "HEAD"]
            response_header = ["*"]
            max_age_seconds = 3600
        }
    ]

    public_access_prevention = "inherited"  # Allow public access for website
}
```

### 13. Hierarchical Namespace (Folder Support)
```terraform
module "hns_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-hns"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    # Hierarchical namespace for folder-like structure
    hierarchical_namespace = {
        enabled = true
    }

    uniform_bucket_level_access = true  # Required for HNS
}
```

### 14. Soft Delete Policy
```terraform
module "soft_delete_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-soft-delete"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    # Soft delete - objects can be recovered for 7 days
    soft_delete_policy = {
        retention_duration_seconds = 604800  # 7 days (default)
    }

    versioning_enabled = true
}
```

### 15. IP Filtering
```terraform
module "ip_filtered_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-ip-filtered"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    # IP filtering to restrict access
    ip_filter = {
        mode                           = "Enabled"
        allow_all_service_agent_access = true

        public_network_source = {
            allowed_ip_cidr_ranges = [
                "203.0.113.0/24",  # Your office IP range
                "198.51.100.0/24"  # Your VPN IP range
            ]
        }

        # Optional: Allow specific VPC networks
        vpc_network_sources = [
            {
                network                = "projects/${var.gcp_project_id}/global/networks/my-vpc"
                allowed_ip_cidr_ranges = ["10.0.0.0/8"]
            }
        ]
    }
}
```

### 16. Dual-Region with Turbo Replication
```terraform
module "dual_region_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-dual-region"
    project_id = var.gcp_project_id
    location   = "US"  # Multi-region required for custom placement

    # Custom placement for dual-region
    custom_placement_config = {
        data_locations = ["US-EAST1", "US-WEST1"]
    }

    # Turbo replication for faster cross-region sync
    rpo = "ASYNC_TURBO"
}
```

### 17. Advanced Lifecycle Rules
```terraform
module "advanced_lifecycle_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-advanced-lifecycle"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    versioning_enabled = true

    lifecycle_rules = [
        # Delete objects with age 0 (created today)
        {
            action = {
                type = "Delete"
            }
            condition = {
                age              = 0
                send_age_if_zero = true
                matches_prefix   = ["temp/"]
            }
        },
        # Delete noncurrent versions after 7 days
        {
            action = {
                type = "Delete"
            }
            condition = {
                days_since_noncurrent_time              = 7
                send_days_since_noncurrent_time_if_zero = false
                with_state                              = "ARCHIVED"
            }
        },
        # Move to NEARLINE if not accessed for 30 days
        {
            action = {
                type          = "SetStorageClass"
                storage_class = "NEARLINE"
            }
            condition = {
                days_since_custom_time              = 30
                send_days_since_custom_time_if_zero = false
                matches_suffix                      = [".log", ".txt"]
            }
        },
        # Keep only 3 versions
        {
            action = {
                type = "Delete"
            }
            condition = {
                num_newer_versions              = 3
                send_num_newer_versions_if_zero = false
            }
        }
    ]
}
```

### 18. Object Retention (Compliance Lock)
```terraform
module "retention_bucket" {
    source = "../../modules/cloud_storage"

    name       = "${var.gcp_project_id}-retention"
    project_id = var.gcp_project_id
    location   = var.gcp_region

    enable_object_retention = true  # Enable object lock

    # Bucket-level retention policy
    retention_policy = {
        retention_period = 2592000  # 30 days
        is_locked        = false     # Set to true for compliance mode (irreversible!)
    }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|----------|
| name | Bucket name (globally unique) | string | - | yes |
| project_id | GCP project ID | string | - | yes |
| location | Region or multi-region | string | "asia-southeast1" | no |
| storage_class | STANDARD/NEARLINE/COLDLINE/ARCHIVE | string | "STANDARD" | no |
| force_destroy | Allow bucket deletion even if it contains objects | bool | false | no |
| public_access_prevention | Prevents public access: 'enforced' or 'inherited' | string | "enforced" | no |
| uniform_bucket_level_access | Enable uniform bucket-level access (recommended) | bool | true | no |
| labels | Labels to apply to the bucket | map(string) | {} | no |
| versioning_enabled | Enable versioning | bool | false | no |
| kms_key_name | KMS key for CMEK | string | null | no |
| default_event_based_hold | Auto-apply event-based hold | bool | false | no |
| enable_object_retention | Enable object retention/lock | bool | false | no |
| requester_pays | Enable Requester Pays | bool | false | no |
| rpo | Recovery point objective (DEFAULT/ASYNC_TURBO) | string | null | no |
| autoclass | Autoclass config (enabled, terminal_storage_class) | object | null | no |
| website | Website config (main_page_suffix, not_found_page) | object | null | no |
| custom_placement_config | Dual-region data_locations | object | null | no |
| soft_delete_policy | Soft delete retention config | object | null | no |
| hierarchical_namespace | Folder support (enabled) | object | null | no |
| ip_filter | IP filtering configuration | object | null | no |
| lifecycle_rules | Lifecycle rules list (with enhanced conditions) | list(object) | [] | no |
| cors_rules | CORS configuration | list(object) | [] | no |
| logging_config | Access log configuration | object | null | no |
| retention_policy | Retention policy config | object | null | no |
| iam_members | IAM member bindings | map(object) | {} | no |
| objects | Objects to upload | map(object) | {} | no |
| notification_config | Pub/Sub notification config | object | null | no |

### Enhanced Lifecycle Rule Conditions
New condition fields available:
- `matches_prefix` - Match objects by name prefix
- `matches_suffix` - Match objects by name suffix
- `send_age_if_zero` - Allow age=0
- `custom_time_before` - Match by custom time metadata
- `send_days_since_custom_time_if_zero` - Allow days_since_custom_time=0
- `send_days_since_noncurrent_time_if_zero` - Allow days_since_noncurrent_time=0
- `send_num_newer_versions_if_zero` - Allow num_newer_versions=0
- `noncurrent_time_before` - Match by noncurrent time

## Outputs

| Name | Description |
|------|-------------|
| bucket_name | Bucket name |
| bucket_id | Bucket ID |
| bucket_url | Bucket base URL |
| bucket_self_link | Bucket URI |
| bucket_location | Bucket location |
| bucket_storage_class | Bucket storage class |

## Use with Cloud Run Job

```terraform
module "processing_bucket" {
    source     = "../../modules/cloud_storage"
    name       = "job-processing-bucket"
    project_id = var.project_id
}

module "data_job" {
    source = "../../modules/cloud_run"

    secrets_map = {
        PROCESSING_BUCKET = module.processing_bucket.bucket_name
    }
}
```

## Notes

- **Provider Version**: Requires Google Cloud Provider >= v7.20.0 (tested with v7.20.0)
- **Naming**: Bucket names must be globally unique
- **Force Destroy**: Set `force_destroy = true` carefully (deletes all objects)
- **CMEK**: KMS key must exist before bucket creation
- **Uniform Access**: Recommended over legacy ACLs
- **Retention Lock**: Once locked, cannot be removed
- **Hierarchical Namespace**: Requires `uniform_bucket_level_access = true`
- **IP Filter**: Once enabled, can only be toggled between Enabled/Disabled (not removable)
- **Autoclass**: Cannot be used with lifecycle rules that set storage class
- **Turbo Replication (rpo)**: Only available for dual-region buckets
- **Soft Delete**: Default is 7 days (604800 seconds), set to 0 to disable
- **Custom Placement**: Recreates bucket if data_locations change
- **Object Retention**: Set `is_locked = true` carefully (irreversible)
