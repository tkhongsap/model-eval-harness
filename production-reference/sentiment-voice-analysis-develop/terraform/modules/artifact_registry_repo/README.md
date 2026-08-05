# Google Artifact Registry Repository Terraform Module

This Terraform module creates a Google Cloud Artifact Registry repository with comprehensive configuration options for standard, virtual, and remote repository modes.

## Features

- ✅ **Multiple Formats** - Docker, Maven, NPM, Python, APT, YUM, Go, KFP, Generic
- ✅ **Three Repository Modes** - Standard, Virtual (aggregation), Remote (caching)
- ✅ **Cleanup Policies** - Automated artifact lifecycle management
- ✅ **CMEK Support** - Customer-managed encryption keys
- ✅ **Docker Immutable Tags** - Prevent tag overwrites
- ✅ **Maven Version Policies** - Snapshot and release management
- ✅ **Remote Repository Caching** - Cache external registries (Docker Hub, Maven Central, etc.)
- ✅ **Virtual Repository Aggregation** - Unified view across multiple repositories
- ✅ **Upstream Credentials** - Secure authentication for remote repositories

## Requirements

- Terraform = 1.13.3
- Google Provider = 7.20.0
- Artifact Registry API enabled in your GCP project

## Usage Examples

### Example 1: Basic Docker Repository

```hcl
module "docker_repo" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "asia-southeast1"
  repository_id = "docker-images"
  format        = "DOCKER"
  description   = "Docker container images"

  labels = {
    environment = "production"
    team        = "platform"
  }
}
```

### Example 2: Docker Repository with Immutable Tags

```hcl
module "immutable_docker_repo" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "production-images"
  format        = "DOCKER"
  description   = "Production Docker images with immutable tags"

  docker_config = {
    immutable_tags = true
  }

  labels = {
    environment = "production"
    immutable   = "true"
  }
}
```

### Example 3: Maven Repository with Version Policy

```hcl
module "maven_repo" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "maven-releases"
  format        = "MAVEN"
  description   = "Maven release repository"

  maven_config = {
    allow_snapshot_overwrites = false
    version_policy            = "RELEASE"
  }

  labels = {
    type = "maven"
  }
}
```

### Example 4: Repository with CMEK Encryption

```hcl
module "encrypted_repo" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "europe-west1"
  repository_id = "secure-images"
  format        = "DOCKER"
  description   = "Encrypted Docker repository"

  kms_key_name = "projects/my-project/locations/europe-west1/keyRings/artifact-registry/cryptoKeys/registry-key"

  labels = {
    security = "cmek-encrypted"
  }
}
```

### Example 5: Repository with Cleanup Policies

```hcl
module "repo_with_cleanup" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "asia-southeast1"
  repository_id = "temporary-builds"
  format        = "DOCKER"
  description   = "Temporary build artifacts with auto-cleanup"

  cleanup_policies = [
    {
      id     = "delete-old-untagged"
      action = "DELETE"
      condition = {
        tag_state  = "UNTAGGED"
        older_than = "2592000s"  # 30 days
      }
    },
    {
      id     = "delete-old-dev-tags"
      action = "DELETE"
      condition = {
        tag_prefixes = ["dev-", "feature-"]
        older_than   = "604800s"  # 7 days
      }
    },
    {
      id     = "keep-recent-releases"
      action = "KEEP"
      most_recent_versions = {
        package_name_prefixes = ["prod/"]
        keep_count            = 10
      }
    }
  ]

  labels = {
    cleanup = "enabled"
  }
}
```

### Example 6: Cleanup Policies Dry Run

```hcl
module "repo_cleanup_dryrun" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "test-cleanup"
  format        = "DOCKER"

  cleanup_policy_dry_run = true  # Test policies without deleting

  cleanup_policies = [
    {
      id     = "test-cleanup"
      action = "DELETE"
      condition = {
        older_than = "86400s"  # 1 day
      }
    }
  ]
}
```

### Example 7: Remote Docker Hub Repository

```hcl
module "dockerhub_cache" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "dockerhub-cache"
  format        = "DOCKER"
  description   = "Docker Hub caching repository"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config = {
    description = "Caches Docker Hub images"
    docker_repository = {
      public_repository = "DOCKER_HUB"
    }
  }

  labels = {
    type = "remote-cache"
  }
}
```

### Example 8: Remote Maven Central Repository

```hcl
module "maven_central_cache" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "maven-central-cache"
  format        = "MAVEN"
  description   = "Maven Central caching repository"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config = {
    description = "Caches Maven Central artifacts"
    maven_repository = {
      public_repository = "MAVEN_CENTRAL"
    }
  }

  labels = {
    type = "maven-cache"
  }
}
```

### Example 9: Remote NPM Registry

```hcl
module "npm_cache" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "europe-west1"
  repository_id = "npm-cache"
  format        = "NPM"
  description   = "NPM registry caching"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config = {
    npm_repository = {
      public_repository = "NPMJS"
    }
  }
}
```

### Example 10: Remote PyPI Repository

```hcl
module "pypi_cache" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "asia-southeast1"
  repository_id = "pypi-cache"
  format        = "PYTHON"
  description   = "PyPI caching repository"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config = {
    python_repository = {
      public_repository = "PYPI"
    }
  }
}
```

### Example 11: Remote Custom Registry with Authentication

```hcl
module "custom_docker_cache" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "custom-registry-cache"
  format        = "DOCKER"
  description   = "Custom Docker registry cache"
  mode          = "REMOTE_REPOSITORY"

  remote_repository_config = {
    description = "Company internal registry cache"
    
    docker_repository = {
      custom_repository = {
        uri = "https://registry.company.com"
      }
    }

    upstream_credentials = {
      username_password_credentials = {
        username                = "registry-user"
        password_secret_version = "projects/my-project/secrets/registry-password/versions/latest"
      }
    }

    disable_upstream_validation = false
  }

  labels = {
    type = "custom-cache"
  }
}
```

### Example 12: Virtual Repository (Aggregating Multiple Repos)

```hcl
module "virtual_docker_repo" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "docker-virtual"
  format        = "DOCKER"
  description   = "Virtual repository aggregating multiple Docker repos"
  mode          = "VIRTUAL_REPOSITORY"

  virtual_repository_config = {
    upstream_policies = [
      {
        id         = "dockerhub"
        repository = "projects/my-project/locations/us-central1/repositories/dockerhub-cache"
        priority   = 100
      },
      {
        id         = "internal"
        repository = "projects/my-project/locations/us-central1/repositories/docker-images"
        priority   = 200
      }
    ]
  }

  labels = {
    type = "virtual"
  }
}
```

### Example 13: Production Docker Repository with Full Features

```hcl
module "production_docker" {
  source = "../../modules/artifact_registry_repo"

  project_id    = "my-project-123"
  location      = "asia-southeast1"
  repository_id = "production-docker"
  format        = "DOCKER"
  description   = "Production Docker images with comprehensive policies"

  labels = {
    environment = "production"
    managed-by  = "terraform"
    team        = "platform"
  }

  # CMEK encryption
  kms_key_name = "projects/my-project/locations/asia-southeast1/keyRings/prod/cryptoKeys/artifact-registry"

  # Immutable tags for production
  docker_config = {
    immutable_tags = true
  }

  # Cleanup policies
  cleanup_policies = [
    # Keep last 50 production releases
    {
      id     = "keep-prod-releases"
      action = "KEEP"
      most_recent_versions = {
        package_name_prefixes = ["prod/"]
        keep_count            = 50
      }
    },
    # Delete old staging images after 30 days
    {
      id     = "delete-old-staging"
      action = "DELETE"
      condition = {
        tag_prefixes = ["staging-"]
        older_than   = "2592000s"  # 30 days
      }
    },
    # Delete untagged images after 7 days
    {
      id     = "delete-untagged"
      action = "DELETE"
      condition = {
        tag_state  = "UNTAGGED"
        older_than = "604800s"  # 7 days
      }
    }
  ]
}
```

### Example 14: Multi-Format Organization Setup

```hcl
# Docker images
module "docker" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "docker"
  format        = "DOCKER"
}

# Maven artifacts
module "maven" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "maven"
  format        = "MAVEN"
  
  maven_config = {
    allow_snapshot_overwrites = true
    version_policy            = "SNAPSHOT"
  }
}

# NPM packages
module "npm" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "npm"
  format        = "NPM"
}

# Python packages
module "python" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "python"
  format        = "PYTHON"
}

# Go modules
module "go" {
  source        = "../../modules/artifact_registry_repo"
  project_id    = "my-project-123"
  location      = "us-central1"
  repository_id = "go"
  format        = "GO"
}
```

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| project_id | GCP Project ID | `string` | n/a | yes |
| location | Location (e.g., us-central1, asia-southeast1) | `string` | n/a | yes |
| repository_id | Repository ID | `string` | n/a | yes |
| format | Package format (DOCKER, MAVEN, NPM, PYTHON, APT, YUM, GOOGET, KFP, GO, GENERIC) | `string` | n/a | yes |
| description | Repository description | `string` | `""` | no |
| labels | Labels (key-value pairs) | `map(string)` | `{}` | no |
| kms_key_name | KMS key for encryption | `string` | `null` | no |
| mode | Repository mode (STANDARD_REPOSITORY, VIRTUAL_REPOSITORY, REMOTE_REPOSITORY) | `string` | `"STANDARD_REPOSITORY"` | no |
| docker_config | Docker configuration | `object` | `null` | no |
| maven_config | Maven configuration | `object` | `null` | no |
| cleanup_policies | Cleanup policies | `list(object)` | `[]` | no |
| cleanup_policy_dry_run | Dry run mode for cleanup policies | `bool` | `false` | no |
| virtual_repository_config | Virtual repository configuration | `object` | `null` | no |
| remote_repository_config | Remote repository configuration | `object` | `null` | no |

## Outputs

| Name | Description |
|------|-------------|
| id | The ID of the repository |
| name | The name of the repository |
| repository_id | The repository ID |
| location | The location of the repository |
| format | The format of packages |
| mode | The repository mode |
| project | The project ID |
| create_time | Creation timestamp |
| update_time | Last update timestamp |

## Repository Formats

| Format | Description |
|--------|-------------|
| DOCKER | Docker container images |
| MAVEN | Maven artifacts (.jar, .war, .pom) |
| NPM | Node.js packages |
| PYTHON | Python packages (pip) |
| APT | Debian packages |
| YUM | RPM packages |
| GOOGET | Windows packages |
| KFP | Kubeflow Pipelines |
| GO | Go modules |
| GENERIC | Generic artifacts |

## Repository Modes

### STANDARD_REPOSITORY
- Default mode
- Stores artifacts directly
- Full write and read access

### VIRTUAL_REPOSITORY
- Aggregates multiple repositories
- Read-only for clients
- Configurable upstream priority
- Unified view across repos

### REMOTE_REPOSITORY
- Caches external registries
- Reduces external bandwidth
- Improves build performance
- Supports authentication

## Cleanup Policy Actions

### DELETE
Permanently removes artifacts matching conditions:
```hcl
{
  id     = "delete-old"
  action = "DELETE"
  condition = {
    older_than = "2592000s"  # 30 days
  }
}
```

### KEEP
Retains most recent versions:
```hcl
{
  id     = "keep-recent"
  action = "KEEP"
  most_recent_versions = {
    keep_count = 10
  }
}
```

## Cleanup Policy Conditions

| Condition | Description | Example |
|-----------|-------------|---------|
| tag_state | TAGGED, UNTAGGED, ANY | `"UNTAGGED"` |
| tag_prefixes | Match tag prefixes | `["dev-", "test-"]` |
| version_name_prefixes | Match version prefixes | `["1.0.", "2.0."]` |
| package_name_prefixes | Match package prefixes | `["prod/"]` |
| older_than | Age in seconds | `"604800s"` (7 days) |
| newer_than | Minimum age in seconds | `"86400s"` (1 day) |

## Remote Repository Public Sources

### Docker
- `DOCKER_HUB` - Docker Hub (docker.io)

### Maven
- `MAVEN_CENTRAL` - Maven Central Repository

### NPM
- `NPMJS` - npmjs.com registry

### Python
- `PYPI` - Python Package Index

### APT/YUM
- Custom public repositories with `repository_base` and `repository_path`

## Best Practices

1. **Use Immutable Tags**: Enable for production Docker repos to prevent accidental overwrites
2. **Implement Cleanup Policies**: Reduce storage costs with automated cleanup
3. **Test with Dry Run**: Use `cleanup_policy_dry_run = true` before enabling deletion
4. **CMEK for Sensitive Data**: Use customer-managed keys for compliance
5. **Virtual Repos for Aggregation**: Simplify client configuration with virtual repos
6. **Remote Repos for Caching**: Improve build times and reduce external dependencies
7. **Label Everything**: Use consistent labeling for cost tracking and organization
8. **Regional Placement**: Choose locations close to your build infrastructure
9. **Separate Environments**: Use different repos for dev/staging/production
10. **Version Policies**: Use Maven version policies to separate snapshots and releases

## IAM Permissions

### Terraform Service Account
```
roles/artifactregistry.admin
```

Or specific permissions:
```
artifactregistry.repositories.create
artifactregistry.repositories.update
artifactregistry.repositories.delete
artifactregistry.repositories.get
```

### For CMEK
```
cloudkms.cryptoKeyEncrypterDecrypter (on the KMS key)
```

### For Remote Repositories with Credentials
Store passwords in Secret Manager and grant:
```
secretmanager.versions.access
```

## Accessing Repositories

### Docker
```bash
# Configure Docker
gcloud auth configure-docker LOCATION-docker.pkg.dev

# Pull image
docker pull LOCATION-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG

# Push image
docker tag IMAGE LOCATION-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG
docker push LOCATION-docker.pkg.dev/PROJECT/REPO/IMAGE:TAG
```

### Maven
```xml
<repository>
  <id>artifact-registry</id>
  <url>artifactregistry://LOCATION-maven.pkg.dev/PROJECT/REPO</url>
</repository>
```

### NPM
```bash
npm config set registry https://LOCATION-npm.pkg.dev/PROJECT/REPO/
```

### Python
```bash
pip install --index-url https://LOCATION-python.pkg.dev/PROJECT/REPO/simple/ PACKAGE
```

## Cost Optimization

1. **Cleanup Policies**: Automatically remove old/unused artifacts
2. **Remote Repositories**: Cache external dependencies once
3. **Regional Selection**: Minimize data transfer costs
4. **Tag Management**: Use immutable tags only where needed
5. **Dry Run Testing**: Validate cleanup policies before execution

## Related Resources

- [google_artifact_registry_repository](https://registry.terraform.io/providers/hashicorp/google/7.20.0/docs/resources/artifact_registry_repository)
- [Artifact Registry Documentation](https://cloud.google.com/artifact-registry/docs)
- [Cleanup Policies](https://cloud.google.com/artifact-registry/docs/repositories/cleanup-policy)
- [Remote Repositories](https://cloud.google.com/artifact-registry/docs/repositories/remote-repo)
- [Virtual Repositories](https://cloud.google.com/artifact-registry/docs/repositories/virtual-repo)

## License

MIT