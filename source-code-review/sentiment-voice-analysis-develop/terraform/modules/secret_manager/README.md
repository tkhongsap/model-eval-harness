# Google Secret Manager Terraform Module

This Terraform module creates a Google Cloud Secret Manager secret with comprehensive configuration options. This module creates only the secret metadata - secret values are added separately via console, CLI, or API.

## Features

- ✅ **Secret Metadata Management** - Creates secrets with labels and annotations
- ✅ **Auto & User-Managed Replication** - Global auto replication or specific regional replicas
- ✅ **Customer-Managed Encryption (CMEK)** - KMS key encryption for secrets
- ✅ **Rotation Configuration** - Automated secret rotation scheduling
- ✅ **Lifecycle Management** - TTL, expiration time, and version destroy TTL
- ✅ **Version Aliases** - Named aliases for specific secret versions
- ✅ **Pub/Sub Notifications** - Event notifications for secret changes
- ✅ **Multi-Region Replication** - Replicate secrets across multiple locations

## Important Note

This module creates **secret metadata only**. Secret values must be added separately:
- Via Google Cloud Console
- Using `gcloud secrets versions add` command
- Through the Secret Manager API
- With the `google_secret_manager_secret_version` Terraform resource (separate)

## Requirements

- Terraform = 1.13.3
- Google Provider = 7.20.0 (pinned exact version)
- Secret Manager API enabled in your GCP project

## Usage Examples

### Example 1: Basic Secret with Auto Replication

```hcl
module "api_key" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "api-key"

  labels = {
    environment = "production"
    app         = "web-service"
  }

  replication_type = "auto"
}
```

### Example 2: Secret with CMEK Auto Replication

```hcl
module "database_password" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "db-password"

  replication_type   = "auto"
  auto_cmek_key_name = "projects/my-project/locations/global/keyRings/my-keyring/cryptoKeys/my-key"

  labels = {
    type = "database-credential"
  }
}
```

### Example 3: User-Managed Multi-Region Replication

```hcl
module "global_api_secret" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "global-api-secret"

  replication_type = "user_managed"

  replicas = [
    {
      location     = "asia-southeast1"
      kms_key_name = null
    },
    {
      location     = "us-central1"
      kms_key_name = null
    },
    {
      location     = "europe-west1"
      kms_key_name = null
    }
  ]

  labels = {
    availability = "multi-region"
  }
}
```

### Example 4: User-Managed Replication with Regional CMEK

```hcl
module "encrypted_secret" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "encrypted-api-key"

  replication_type = "user_managed"

  replicas = [
    {
      location     = "asia-southeast1"
      kms_key_name = "projects/my-project/locations/asia-southeast1/keyRings/my-keyring/cryptoKeys/my-key"
    },
    {
      location     = "us-central1"
      kms_key_name = "projects/my-project/locations/us-central1/keyRings/my-keyring/cryptoKeys/us-key"
    }
  ]

  labels = {
    security = "cmek-encrypted"
  }
}
```

### Example 5: Secret with Rotation Configuration

```hcl
module "rotating_certificate" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "tls-certificate"

  # Rotate every 90 days
  rotation_period    = "7776000s"  # 90 days in seconds
  next_rotation_time = "2026-03-15T00:00:00Z"

  replication_type = "auto"

  labels = {
    type     = "certificate"
    rotation = "automated"
  }
}
```

### Example 6: Secret with TTL (Auto-Delete)

```hcl
module "temporary_token" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "temporary-access-token"

  # Secret will be deleted after 30 days
  ttl = "2592000s"

  replication_type = "auto"

  labels = {
    type = "temporary"
  }
}
```

### Example 7: Secret with Expiration Time

```hcl
module "expiring_credential" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "project-credential-2026"

  # Secret expires on specific date
  expire_time = "2026-12-31T23:59:59Z"

  replication_type = "auto"

  labels = {
    expires = "2026"
  }
}
```

### Example 8: Secret with Pub/Sub Notifications

```hcl
module "monitored_secret" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "monitored-api-key"

  # Receive notifications on secret changes
  topics = [
    "projects/my-project/topics/secret-changes",
    "projects/my-project/topics/audit-log"
  ]

  replication_type = "auto"

  labels = {
    monitoring = "enabled"
  }
}
```

### Example 9: Secret with Version Aliases

```hcl
module "versioned_config" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "app-config"

  version_aliases = {
    "stable"     = "1"
    "beta"       = "2"
    "production" = "1"
  }

  replication_type = "auto"

  labels = {
    type = "configuration"
  }
}
```

### Example 10: Secret with Version Destroy TTL

```hcl
module "audit_secret" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "audit-trail-key"

  # Keep destroyed versions for 30 days before permanent deletion
  version_destroy_ttl = "2592000s"

  replication_type = "auto"

  labels = {
    compliance = "audit-required"
  }
}
```

### Example 11: Production Secret with Full Configuration

```hcl
module "production_database" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "prod-db-connection-string"

  labels = {
    environment = "production"
    tier        = "critical"
    app         = "payment-service"
  }

  annotations = {
    owner       = "platform-team@company.com"
    compliance  = "pci-dss"
    last-review = "2026-02-01"
  }

  # User-managed replication with CMEK
  replication_type = "user_managed"
  replicas = [
    {
      location     = "asia-southeast1"
      kms_key_name = "projects/my-project/locations/asia-southeast1/keyRings/prod-keyring/cryptoKeys/secret-key"
    },
    {
      location     = "asia-east1"
      kms_key_name = "projects/my-project/locations/asia-east1/keyRings/prod-keyring/cryptoKeys/secret-key"
    }
  ]

  # Rotation every 60 days
  rotation_period    = "5184000s"
  next_rotation_time = "2026-04-15T00:00:00Z"

  # Notifications
  topics = [
    "projects/my-project/topics/secret-rotation-alerts",
    "projects/my-project/topics/security-audit"
  ]

  # Version management
  version_aliases = {
    "current" = "latest"
    "stable"  = "1"
  }
  version_destroy_ttl = "604800s"  # 7 days
}
```

### Example 12: Complete Configuration with Annotations

```hcl
module "comprehensive_secret" {
  source = "../../modules/secret_manager"

  project_id = "my-project-123"
  secret_id  = "comprehensive-example"

  labels = {
    environment = "staging"
    managed-by  = "terraform"
  }

  annotations = {
    description      = "Comprehensive secret configuration example"
    documentation    = "https://wiki.company.com/secrets/comprehensive-example"
    support-contact  = "devops@company.com"
    change-ticket    = "JIRA-12345"
  }

  # Auto replication with CMEK
  replication_type   = "auto"
  auto_cmek_key_name = "projects/my-project/locations/global/keyRings/staging-keyring/cryptoKeys/secret-key"

  # Rotation
  rotation_period    = "2592000s"  # 30 days
  next_rotation_time = "2026-03-15T00:00:00Z"

  # Lifecycle
  version_destroy_ttl = "86400s"  # 1 day

  # Notifications
  topics = [
    "projects/my-project/topics/staging-secret-updates"
  ]

  # Versioning
  version_aliases = {
    "latest" = "1"
    "stable" = "1"
  }
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| project_id | GCP project ID where the secret will be created | `string` | n/a | yes |
| secret_id | ID of the Secret Manager secret | `string` | n/a | yes |
| labels | Labels to apply to the secret (key-value pairs) | `map(string)` | `{}` | no |
| annotations | Annotations to apply to the secret (key-value pairs) | `map(string)` | `{}` | no |
| replication_type | Replication strategy: 'auto' or 'user_managed' | `string` | `"auto"` | no |
| ttl | Time-to-live in seconds (e.g., "3600s"). Secret will be deleted after this period. Conflicts with expire_time. | `string` | `null` | no |
| expire_time | Timestamp when secret expires (RFC3339 format, e.g., "2026-12-31T23:59:59Z"). Conflicts with ttl. | `string` | `null` | no |
| version_destroy_ttl | Secret version destroy TTL duration (e.g., "86400s" for 1 day) | `string` | `null` | no |
| rotation_period | Rotation period in seconds (e.g., "2592000s" for 30 days) | `string` | `null` | no |
| next_rotation_time | Timestamp for next rotation (RFC3339 format) | `string` | `null` | no |
| topics | List of Pub/Sub topic names for secret notifications | `list(string)` | `[]` | no |
| version_aliases | Map of alias names to version numbers | `map(string)` | `{}` | no |
| auto_cmek_key_name | KMS key name for auto replication CMEK (full resource name) | `string` | `null` | no |
| replicas | List of replica configurations with location and optional KMS key | `list(object({ location = string, kms_key_name = optional(string) }))` | `[]` | no |

## Outputs

| Name | Description |
|------|-------------|
| secret_id | The ID of the Secret Manager secret (full resource name) |
| name | The name of the Secret Manager secret |
| secret_id_short | The short ID of the secret (for use in Cloud Run env vars) |
| project | The project ID |
| create_time | The time at which the secret was created |

## Notes

### Secret Value Management

This module creates **secret metadata only**. To add secret values after creation:

**Via Console:**
1. Navigate to Secret Manager in Google Cloud Console
2. Select your secret
3. Click "NEW VERSION"
4. Add your secret value

**Via gcloud CLI:**
```bash
# Add value from string
echo -n "my-secret-value" | gcloud secrets versions add SECRET_ID --data-file=-

# Add value from file
gcloud secrets versions add SECRET_ID --data-file=/path/to/secret.txt
```

**Via Terraform (separate resource):**
```hcl
resource "google_secret_manager_secret_version" "secret_value" {
  secret      = module.api_key.secret_id
  secret_data = var.secret_value
}
```

### TTL vs Expire Time

- **ttl**: Secret is deleted after a duration (e.g., "2592000s" = 30 days from creation)
- **expire_time**: Secret is deleted at a specific timestamp (e.g., "2026-12-31T23:59:59Z")
- These options are mutually exclusive - use only one

### Replication Strategies

**Auto Replication:**
- Secret is replicated across all GCP regions
- Simpler configuration
- Optional global CMEK key

**User-Managed Replication:**
- You specify exact locations
- Each location can have its own CMEK key
- Better control over data residency
- Recommended for compliance requirements

### Rotation

When `rotation_period` is set:
- Secret Manager tracks rotation schedule
- `next_rotation_time` specifies when the next rotation should occur
- You must implement the actual rotation logic separately
- Consider using Cloud Functions or Cloud Run jobs triggered by Pub/Sub

### Version Destroy TTL

- Specifies how long to keep deleted secret versions before permanent deletion
- Useful for compliance and audit requirements
- Set to "0s" for immediate permanent deletion
- Common values: "86400s" (1 day), "604800s" (7 days), "2592000s" (30 days)

### CMEK (Customer-Managed Encryption Keys)

- KMS key must exist in the same location as the replica
- For auto replication, use a global or multi-region KMS key
- Service account needs `cloudkms.cryptoKeyEncrypterDecrypter` role
- KMS key format: `projects/PROJECT/locations/LOCATION/keyRings/KEYRING/cryptoKeys/KEY`

### Pub/Sub Notifications

Secret Manager can publish notifications to Pub/Sub topics for events like:
- Secret creation
- Secret version creation
- Secret deletion
- Secret version destruction

Topic format: `projects/PROJECT_ID/topics/TOPIC_NAME`

### Version Aliases

- Aliases let you reference secret versions by name instead of number
- Useful for managing different environments
- Example: `"production" = "5"`, `"staging" = "6"`
- Access via: `projects/PROJECT/secrets/SECRET/versions/ALIAS`

## IAM Permissions

To use this module, ensure the Terraform service account has:

```
roles/secretmanager.admin
```

Or these specific permissions:
```
secretmanager.secrets.create
secretmanager.secrets.update
secretmanager.secrets.delete
secretmanager.secrets.get
```

For CMEK, also grant:
```
cloudkms.cryptoKeyEncrypterDecrypter (on the KMS key)
```

## Best Practices

1. **Use Labels**: Tag secrets with environment, application, and owner information
2. **Enable Rotation**: Set rotation_period for sensitive credentials
3. **Use Annotations**: Document secrets with metadata (owner, purpose, documentation links)
4. **Version Management**: Use version_aliases to track stable versions
5. **Notifications**: Configure Pub/Sub topics to monitor secret changes
6. **CMEK for Sensitive Data**: Use customer-managed encryption for highly sensitive secrets
7. **Region Selection**: For user-managed replication, choose regions close to your services
8. **Version Destroy TTL**: Set appropriate retention for compliance requirements
9. **Expiration**: Use ttl or expire_time for temporary secrets
10. **Separate Secret Data**: Never store secret values in Terraform code or state files

## Related Resources

- [google_secret_manager_secret_version](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_version) - Manage secret values
- [google_secret_manager_secret_iam](https://registry.terraform.io/providers/hashicorp/google/latest/docs/resources/secret_manager_secret_iam) - Manage secret access
- [Secret Manager Documentation](https://cloud.google.com/secret-manager/docs)

## License

MIT
