# Google Cloud Run v2 Job Terraform Module

This Terraform module creates a Google Cloud Run v2 Job with comprehensive configuration options for container orchestration, networking, storage, and security.

## Features

- ✅ **Multi-Container Support** - Run multiple containers in a single job
- ✅ **Environment Variables** - Literal values and Secret Manager integration
- ✅ **Resource Management** - CPU, memory, and GPU allocation
- ✅ **Volume Support** - 5 volume types (Secret, CloudSQL, EmptyDir, GCS, NFS)
- ✅ **VPC Networking** - Serverless VPC Connector or Direct VPC
- ✅ **GPU Support** - Node selector for GPU accelerators
- ✅ **Binary Authorization** - Container image verification
- ✅ **Startup Probes** - HTTP, TCP, and gRPC health checks
- ✅ **Task Parallelism** - Concurrent task execution
- ✅ **CMEK Support** - Customer-managed encryption keys

## Requirements

- Terraform = 1.13.3
- Google Provider = 7.20.0
- Cloud Run API enabled in your GCP project

## Usage Examples

### Example 1: Basic Cloud Run Job

```hcl
module "simple_job" {
  source = "../../modules/cloud_run"

  job_name              = "simple-batch-job"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "job-runner@my-project.iam.gserviceaccount.com"

  containers = [{
    image = "gcr.io/my-project/batch-processor:latest"
    resources = {
      limits = {
        cpu    = "2"
        memory = "1Gi"
      }
    }
  }]

  timeout     = "600s"
  max_retries = 3
  task_count  = 1

  labels = {
    environment = "production"
    team        = "data-engineering"
  }
}
```

### Example 2: Job with Secret Environment Variables

```hcl
module "job_with_secrets" {
  source = "../../modules/cloud_run"

  job_name              = "api-batch-processor"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "api-job@my-project.iam.gserviceaccount.com"

  containers = [{
    image = "gcr.io/my-project/api-processor:v1.0"
    
    env = [
      {
        name  = "ENVIRONMENT"
        value = "production"
      },
      {
        name = "API_KEY"
        value_source = {
          secret_key_ref = {
            secret  = "api-key"
            version = "latest"
          }
        }
      },
      {
        name = "DATABASE_PASSWORD"
        value_source = {
          secret_key_ref = {
            secret  = "db-password"
            version = "2"
          }
        }
      }
    ]

    resources = {
      limits = {
        cpu    = "1"
        memory = "512Mi"
      }
    }
  }]

  timeout     = "1800s"
  max_retries = 5
}
```

### Example 3: Multi-Container Job

```hcl
module "multi_container_job" {
  source = "../../modules/cloud_run"

  job_name              = "data-pipeline"
  project_id            = "my-project-123"
  region                = "us-central1"
  service_account_email = "pipeline@my-project.iam.gserviceaccount.com"

  containers = [
    {
      name  = "extractor"
      image = "gcr.io/my-project/data-extractor:latest"
      
      env = [
        {
          name  = "STAGE"
          value = "extract"
        }
      ]

      resources = {
        limits = {
          cpu    = "2"
          memory = "2Gi"
        }
      }
    },
    {
      name       = "transformer"
      image      = "gcr.io/my-project/data-transformer:latest"
      depends_on = ["extractor"]
      
      env = [
        {
          name  = "STAGE"
          value = "transform"
        }
      ]

      resources = {
        limits = {
          cpu    = "4"
          memory = "4Gi"
        }
      }
    },
    {
      name       = "loader"
      image      = "gcr.io/my-project/data-loader:latest"
      depends_on = ["transformer"]
      
      resources = {
        limits = {
          cpu    = "1"
          memory = "1Gi"
        }
      }
    }
  ]

  timeout     = "3600s"
  task_count  = 10
  parallelism = 3
}
```

### Example 4: Job with Secret Volume

```hcl
module "job_with_secret_volume" {
  source = "../../modules/cloud_run"

  job_name              = "certificate-processor"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "cert-job@my-project.iam.gserviceaccount.com"

  volumes = [{
    name = "certificates"
    secret = {
      secret = "tls-certificates"
      items = [
        {
          path    = "tls.crt"
          version = "latest"
          mode    = 0444
        },
        {
          path    = "tls.key"
          version = "latest"
          mode    = 0400
        }
      ]
    }
  }]

  containers = [{
    image = "gcr.io/my-project/cert-processor:latest"
    
    volume_mounts = [{
      name       = "certificates"
      mount_path = "/etc/ssl/certs"
    }]

    resources = {
      limits = {
        cpu    = "1"
        memory = "512Mi"
      }
    }
  }]

  timeout = "600s"
}
```

### Example 5: Job with CloudSQL Connection

```hcl
module "database_migration_job" {
  source = "../../modules/cloud_run"

  job_name              = "db-migration"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "migration@my-project.iam.gserviceaccount.com"

  volumes = [{
    name = "cloudsql"
    cloud_sql_instance = {
      instances = [
        "my-project:asia-southeast1:production-database"
      ]
    }
  }]

  containers = [{
    image = "gcr.io/my-project/db-migrator:latest"
    
    env = [
      {
        name  = "DB_SOCKET_PATH"
        value = "/cloudsql/my-project:asia-southeast1:production-database"
      },
      {
        name = "DB_USER"
        value_source = {
          secret_key_ref = {
            secret  = "db-username"
            version = "latest"
          }
        }
      }
    ]

    volume_mounts = [{
      name       = "cloudsql"
      mount_path = "/cloudsql"
    }]

    resources = {
      limits = {
        cpu    = "2"
        memory = "1Gi"
      }
    }
  }]

  timeout     = "7200s"
  max_retries = 1
}
```

### Example 6: Job with GCS Volume Mount

```hcl
module "gcs_processing_job" {
  source = "../../modules/cloud_run"

  job_name              = "gcs-data-processor"
  project_id            = "my-project-123"
  region                = "us-central1"
  service_account_email = "gcs-job@my-project.iam.gserviceaccount.com"

  volumes = [{
    name = "data-bucket"
    gcs = {
      bucket        = "my-data-bucket"
      read_only     = false
      mount_options = ["implicit-dirs"]
    }
  }]

  containers = [{
    image = "gcr.io/my-project/gcs-processor:latest"
    
    volume_mounts = [{
      name       = "data-bucket"
      mount_path = "/mnt/gcs"
    }]

    resources = {
      limits = {
        cpu    = "4"
        memory = "8Gi"
      }
    }
  }]

  timeout     = "3600s"
  task_count  = 5
  parallelism = 5
}
```

### Example 7: Job with VPC Connector

```hcl
module "vpc_job" {
  source = "../../modules/cloud_run"

  job_name              = "private-network-job"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "vpc-job@my-project.iam.gserviceaccount.com"

  vpc_access = {
    connector = "projects/my-project/locations/asia-southeast1/connectors/my-connector"
    egress    = "ALL_TRAFFIC"
  }

  containers = [{
    image = "gcr.io/my-project/internal-api-client:latest"
    
    env = [{
      name  = "INTERNAL_API_URL"
      value = "http://10.0.1.100:8080"
    }]

    resources = {
      limits = {
        cpu    = "1"
        memory = "512Mi"
      }
    }
  }]

  timeout = "600s"
}
```

### Example 8: Job with Direct VPC Access

```hcl
module "direct_vpc_job" {
  source = "../../modules/cloud_run"

  job_name              = "vpc-native-job"
  project_id            = "my-project-123"
  region                = "us-central1"
  service_account_email = "vpc-native@my-project.iam.gserviceaccount.com"

  vpc_access = {
    egress = "PRIVATE_RANGES_ONLY"
    network_interfaces = [{
      network    = "projects/my-project/global/networks/my-vpc"
      subnetwork = "projects/my-project/regions/us-central1/subnetworks/my-subnet"
      tags       = ["cloud-run-job", "backend"]
    }]
  }

  containers = [{
    image = "gcr.io/my-project/backend-processor:latest"
    
    resources = {
      limits = {
        cpu    = "2"
        memory = "2Gi"
      }
    }
  }]

  timeout = "1800s"
}
```

### Example 9: GPU-Enabled Job

```hcl
module "ml_training_job" {
  source = "../../modules/cloud_run"

  job_name              = "ml-model-training"
  project_id            = "my-project-123"
  region                = "us-central1"
  service_account_email = "ml-training@my-project.iam.gserviceaccount.com"

  node_selector = {
    accelerator = "nvidia-l4"
  }

  containers = [{
    image   = "gcr.io/my-project/ml-trainer:latest"
    command = ["python", "train.py"]
    args    = ["--epochs=100", "--batch-size=32"]

    resources = {
      limits = {
        cpu              = "8"
        memory           = "32Gi"
        "nvidia.com/gpu" = "1"
      }
    }
  }]

  timeout                          = "7200s"
  max_retries                      = 1
  gpu_zonal_redundancy_disabled    = true
  execution_environment            = "EXECUTION_ENVIRONMENT_GEN2"
}
```

### Example 10: Job with Startup Probe (HTTP)

```hcl
module "healthcheck_job" {
  source = "../../modules/cloud_run"

  job_name              = "web-scraper"
  project_id            = "my-project-123"
  region                = "asia-southeast1"
  service_account_email = "scraper@my-project.iam.gserviceaccount.com"

  containers = [{
    image = "gcr.io/my-project/scraper:latest"

    ports = [{
      container_port = 8080
    }]

    startup_probe = {
      initial_delay_seconds = 5
      timeout_seconds       = 3
      period_seconds        = 10
      failure_threshold     = 3

      http_get = {
        path = "/health"
        port = 8080
        http_headers = [{
          name  = "Authorization"
          value = "Bearer health-check-token"
        }]
      }
    }

    resources = {
      limits = {
        cpu    = "1"
        memory = "512Mi"
      }
    }
  }]

  timeout = "1800s"
}
```

### Example 11: Job with Binary Authorization

```hcl
module "secure_job" {
  source = "../../modules/cloud_run"

  job_name              = "secure-processor"
  project_id            = "my-project-123"
  region                = "us-central1"
  service_account_email = "secure-job@my-project.iam.gserviceaccount.com"

  binary_authorization = {
    policy = "projects/my-project/policy"
  }

  containers = [{
    image = "gcr.io/my-project/verified-image:latest"
    
    resources = {
      limits = {
        cpu    = "2"
        memory = "2Gi"
      }
    }
  }]

  timeout             = "600s"
  deletion_protection = true
}
```

### Example 12: Production Job with Full Configuration

```hcl
module "production_job" {
  source = "../../modules/cloud_run"

  job_name              = "voice-sentiment-analysis"
  project_id            = "my-project-865343412789"
  region                = "asia-southeast1"
  service_account_email = "865343412789-compute@developer.gserviceaccount.com"

  deletion_protection = true
  
  labels = {
    environment = "production"
    application = "sentiment-analysis"
    team        = "data-science"
    cost-center = "analytics"
  }

  annotations = {
    owner               = "data-team@company.com"
    documentation       = "https://wiki.company.com/voice-analysis"
    deployment-pipeline = "github-actions"
  }

  template_labels = {
    version = "v2.1.0"
  }

  # Parallel task execution
  task_count  = 10
  parallelism = 5

  # VPC Access
  vpc_access = {
    connector = "projects/my-project/locations/asia-southeast1/connectors/analytics-connector"
    egress    = "PRIVATE_RANGES_ONLY"
  }

  # CloudSQL connection
  volumes = [{
    name = "cloudsql"
    cloud_sql_instance = {
      instances = [
        "my-project:asia-southeast1:analytics-db"
      ]
    }
  }]

  containers = [{
    name  = "sentiment-analyzer"
    image = "gcr.io/my-project/voice-sentiment-analysis:latest"

    env = [
      # Literal values
      {
        name  = "ENVIRONMENT"
        value = "production"
      },
      {
        name  = "LOG_LEVEL"
        value = "INFO"
      },
      {
        name  = "DB_SOCKET_PATH"
        value = "/cloudsql/my-project:asia-southeast1:analytics-db"
      },
      # Secrets
      {
        name = "API_KEY"
        value_source = {
          secret_key_ref = {
            secret  = "gemini-api-key"
            version = "latest"
          }
        }
      },
      {
        name = "DATABASE_PASSWORD"
        value_source = {
          secret_key_ref = {
            secret  = "db-password"
            version = "3"
          }
        }
      }
    ]

    volume_mounts = [{
      name       = "cloudsql"
      mount_path = "/cloudsql"
    }]

    resources = {
      limits = {
        cpu    = "4"
        memory = "8Gi"
      }
    }

    startup_probe = {
      initial_delay_seconds = 10
      timeout_seconds       = 5
      period_seconds        = 10
      failure_threshold     = 3

      http_get = {
        path = "/health"
        port = 8080
      }
    }
  }]

  timeout                = "7200s"
  max_retries            = 3
  execution_environment  = "EXECUTION_ENVIRONMENT_GEN2"

  # Binary Authorization
  binary_authorization = {
    use_default = true
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| job_name | Name of the Cloud Run Job | `string` | n/a | yes |
| project_id | GCP Project ID where the Cloud Run Job will be deployed | `string` | n/a | yes |
| region | Region where the Cloud Run Job will be deployed | `string` | n/a | yes |
| service_account_email | Email of the service account to run the Cloud Run Job | `string` | n/a | yes |
| containers | List of containers to run in the job | `list(object)` | n/a | yes |
| deletion_protection | Whether to enable deletion protection | `bool` | `false` | no |
| labels | Labels to apply to the job | `map(string)` | `{}` | no |
| annotations | Job-level annotations | `map(string)` | `{}` | no |
| client | Arbitrary identifier for the API client | `string` | `null` | no |
| client_version | Arbitrary version identifier for the API client | `string` | `null` | no |
| launch_stage | Launch stage (ALPHA, BETA, GA) | `string` | `null` | no |
| template_labels | Labels for execution template | `map(string)` | `{}` | no |
| template_annotations | Annotations for execution template | `map(string)` | `{}` | no |
| parallelism | Maximum number of tasks to run in parallel | `number` | `null` | no |
| task_count | Number of tasks to execute | `number` | `1` | no |
| timeout | Max allowed time duration per task (e.g., "600s") | `string` | `"600s"` | no |
| max_retries | Maximum number of retries per task | `number` | `3` | no |
| execution_environment | Execution environment (GEN1 or GEN2) | `string` | `"EXECUTION_ENVIRONMENT_GEN2"` | no |
| encryption_key | Customer managed encryption key (CMEK) | `string` | `null` | no |
| volumes | Volumes to make available to containers | `list(object)` | `[]` | no |
| vpc_access | VPC Access configuration | `object` | `null` | no |
| node_selector | GPU configuration | `object` | `null` | no |
| gpu_zonal_redundancy_disabled | Whether to disable GPU zonal redundancy | `bool` | `null` | no |
| binary_authorization | Binary Authorization configuration | `object` | `null` | no |

### Container Object Structure

```hcl
{
  name        = string           # Optional container name
  image       = string           # Required: Container image URL
  command     = list(string)     # Optional: Override entrypoint
  args        = list(string)     # Optional: Command arguments
  working_dir = string           # Optional: Working directory
  depends_on  = list(string)     # Optional: Container dependencies

  env = list({                   # Environment variables
    name  = string
    value = string               # Literal value OR
    value_source = {             # Secret value
      secret_key_ref = {
        secret  = string
        version = string
      }
    }
  })

  resources = {                  # Resource limits
    limits = {
      cpu              = string  # e.g., "1", "2000m"
      memory           = string  # e.g., "512Mi", "2Gi"
      "nvidia.com/gpu" = string  # GPU count (requires node_selector)
    }
  }

  ports = list({                 # Container ports
    name           = string
    container_port = number
  })

  volume_mounts = list({         # Volume mounts
    name       = string
    mount_path = string
    sub_path   = string
  })

  startup_probe = {              # Health check
    initial_delay_seconds = number
    timeout_seconds       = number
    period_seconds        = number
    failure_threshold     = number

    tcp_socket = { port = number }  # OR
    http_get = {                    # OR
      path = string
      port = number
      http_headers = list({ name = string, value = string })
    }
    grpc = {                        # OR
      port    = number
      service = string
    }
  }
}
```

## Outputs

| Name | Description |
|------|-------------|
| cloud_run_id | The ID of the Cloud Run job |
| cloud_run_name | The name of the Cloud Run job |
| cloud_run_uri | The URI for triggering the job (for Cloud Scheduler) |

## Volume Types

### 1. Secret Volume
Mount Secret Manager secrets as files:
```hcl
volumes = [{
  name = "app-secrets"
  secret = {
    secret       = "my-secret"
    default_mode = 0444
    items = [{
      path    = "config.json"
      version = "latest"
      mode    = 0400
    }]
  }
}]
```

### 2. CloudSQL Instance
Connect to CloudSQL via Unix socket:
```hcl
volumes = [{
  name = "cloudsql"
  cloud_sql_instance = {
    instances = ["project:region:instance-name"]
  }
}]
```

### 3. EmptyDir Volume
Temporary storage shared between containers:
```hcl
volumes = [{
  name = "temp-data"
  empty_dir = {
    medium     = "MEMORY"  # or null for disk
    size_limit = "1Gi"
  }
}]
```

### 4. GCS Bucket
Mount Cloud Storage bucket:
```hcl
volumes = [{
  name = "data-bucket"
  gcs = {
    bucket        = "my-bucket"
    read_only     = false
    mount_options = ["implicit-dirs"]
  }
}]
```

### 5. NFS Volume
Mount network file system:
```hcl
volumes = [{
  name = "nfs-share"
  nfs = {
    server    = "10.0.1.100"
    path      = "/exports/data"
    read_only = false
  }
}]
```

## VPC Access Options

### Serverless VPC Connector
```hcl
vpc_access = {
  connector = "projects/PROJECT/locations/REGION/connectors/CONNECTOR"
  egress    = "ALL_TRAFFIC"  # or "PRIVATE_RANGES_ONLY"
}
```

### Direct VPC (VPC-native)
```hcl
vpc_access = {
  egress = "PRIVATE_RANGES_ONLY"
  network_interfaces = [{
    network    = "projects/PROJECT/global/networks/NETWORK"
    subnetwork = "projects/PROJECT/regions/REGION/subnetworks/SUBNET"
    tags       = ["tag1", "tag2"]
  }]
}
```

## GPU Support

Available accelerators:
- `nvidia-l4` - NVIDIA L4 GPU
- `nvidia-tesla-t4` - NVIDIA T4 GPU
- `nvidia-tesla-a100` - NVIDIA A100 GPU

```hcl
node_selector = {
  accelerator = "nvidia-l4"
}

containers = [{
  resources = {
    limits = {
      cpu              = "8"
      memory           = "32Gi"
      "nvidia.com/gpu" = "1"
    }
  }
}]
```

## Best Practices

1. **Resource Limits**: Always specify CPU and memory limits for predictable performance
2. **Secrets Management**: Use Secret Manager for sensitive data, never hardcode
3. **Timeouts**: Set realistic timeouts based on expected job duration
4. **Retries**: Configure max_retries based on job idempotency
5. **Parallelism**: Use task_count and parallelism for scalable batch processing
6. **VPC Security**: Use Direct VPC for enhanced network security
7. **Startup Probes**: Implement health checks for long-running initialization
8. **Labels**: Tag resources with environment, team, and cost center
9. **Service Accounts**: Use dedicated service accounts with minimal IAM permissions
10. **Deletion Protection**: Enable for production jobs to prevent accidental deletion
11. **GPU Jobs**: Set `gpu_zonal_redundancy_disabled = true` for cost optimization
12. **Multi-Container**: Use `depends_on` to control container execution order

## IAM Permissions

### Terraform Service Account
```
roles/run.admin
roles/iam.serviceAccountUser
```

### Job Service Account
```
roles/secretmanager.secretAccessor (for secrets)
roles/cloudsql.client (for CloudSQL)
roles/storage.objectViewer (for GCS)
```

## Cost Optimization

1. **Right-size resources**: Don't over-provision CPU/memory
2. **Use GEN2 execution**: More efficient than GEN1
3. **Optimize parallelism**: Balance speed vs cost
4. **GPU redundancy**: Disable for non-critical workloads
5. **Timeout tuning**: Shorter timeouts prevent runaway costs
6. **Task batching**: Process multiple items per task

## Triggering Jobs

### Via Cloud Scheduler
```hcl
module "scheduler" {
  source = "../../modules/cloud_scheduler"
  
  job_name = "trigger-cloud-run-job"
  schedule = "0 2 * * *"
  
  http_target = {
    uri         = module.production_job.cloud_run_uri
    http_method = "POST"
    
    oauth_token = {
      service_account_email = "scheduler@my-project.iam.gserviceaccount.com"
    }
  }
}
```

### Via gcloud CLI
```bash
gcloud run jobs execute JOB_NAME \
  --region=REGION \
  --project=PROJECT_ID
```

### Via API
```bash
curl -X POST \
  -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  -H "Content-Type: application/json" \
  "https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/JOB_NAME:run"
```

## Related Resources

- [google_cloud_run_v2_job](https://registry.terraform.io/providers/hashicorp/google/7.20.0/docs/resources/cloud_run_v2_job)
- [Cloud Run Jobs Documentation](https://cloud.google.com/run/docs/create-jobs)
- [Cloud Run Pricing](https://cloud.google.com/run/pricing)

## License

MIT
