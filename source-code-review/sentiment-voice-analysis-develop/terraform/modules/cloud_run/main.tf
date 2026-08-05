resource "google_cloud_run_v2_job" "default" {
  name     = var.job_name
  location = var.region
  project  = var.project_id

  deletion_protection = var.deletion_protection
  labels              = var.labels
  annotations         = var.annotations

  client         = var.client
  client_version = var.client_version
  launch_stage   = var.launch_stage

  # Binary Authorization
  dynamic "binary_authorization" {
    for_each = var.binary_authorization != null ? [1] : []
    content {
      use_default              = lookup(var.binary_authorization, "use_default", null)
      breakglass_justification = lookup(var.binary_authorization, "breakglass_justification", null)
      policy                   = lookup(var.binary_authorization, "policy", null)
    }
  }

  template {
    labels      = var.template_labels
    annotations = var.template_annotations
    parallelism = var.parallelism
    task_count  = var.task_count

    template {
      service_account       = var.service_account_email
      timeout               = var.timeout
      max_retries           = var.max_retries
      execution_environment = var.execution_environment
      encryption_key        = var.encryption_key

      gpu_zonal_redundancy_disabled = var.gpu_zonal_redundancy_disabled

      # VPC Access
      dynamic "vpc_access" {
        for_each = var.vpc_access != null ? [1] : []
        content {
          connector = lookup(var.vpc_access, "connector", null)
          egress    = lookup(var.vpc_access, "egress", null)

          dynamic "network_interfaces" {
            for_each = lookup(var.vpc_access, "network_interfaces", []) != null ? lookup(var.vpc_access, "network_interfaces", []) : []
            content {
              network    = lookup(network_interfaces.value, "network", null)
              subnetwork = lookup(network_interfaces.value, "subnetwork", null)
              tags       = lookup(network_interfaces.value, "tags", null)
            }
          }
        }
      }

      # Node Selector (GPU)
      dynamic "node_selector" {
        for_each = var.node_selector != null ? [1] : []
        content {
          accelerator = var.node_selector.accelerator
        }
      }

      # Containers
      dynamic "containers" {
        for_each = var.containers
        content {
          name        = lookup(containers.value, "name", null)
          image       = containers.value.image
          command     = lookup(containers.value, "command", null)
          args        = lookup(containers.value, "args", null)
          working_dir = lookup(containers.value, "working_dir", null)
          depends_on  = lookup(containers.value, "depends_on", null)

          # Environment variables
          dynamic "env" {
            for_each = lookup(containers.value, "env", []) != null ? lookup(containers.value, "env", []) : []
            content {
              name  = env.value.name
              value = lookup(env.value, "value", null)

              dynamic "value_source" {
                for_each = lookup(env.value, "value_source", null) != null ? [1] : []
                content {
                  secret_key_ref {
                    secret  = env.value.value_source.secret_key_ref.secret
                    version = env.value.value_source.secret_key_ref.version
                  }
                }
              }
            }
          }

          # Resources
          dynamic "resources" {
            for_each = lookup(containers.value, "resources", null) != null ? [1] : []
            content {
              limits = lookup(containers.value.resources, "limits", null)
            }
          }

          # Ports
          dynamic "ports" {
            for_each = lookup(containers.value, "ports", []) != null ? lookup(containers.value, "ports", []) : []
            content {
              name           = lookup(ports.value, "name", null)
              container_port = lookup(ports.value, "container_port", null)
            }
          }

          # Volume Mounts
          dynamic "volume_mounts" {
            for_each = lookup(containers.value, "volume_mounts", []) != null ? lookup(containers.value, "volume_mounts", []) : []
            content {
              name       = volume_mounts.value.name
              mount_path = volume_mounts.value.mount_path
              sub_path   = lookup(volume_mounts.value, "sub_path", null)
            }
          }

          # Startup Probe
          dynamic "startup_probe" {
            for_each = lookup(containers.value, "startup_probe", null) != null ? [1] : []
            content {
              initial_delay_seconds = lookup(containers.value.startup_probe, "initial_delay_seconds", null)
              timeout_seconds       = lookup(containers.value.startup_probe, "timeout_seconds", null)
              period_seconds        = lookup(containers.value.startup_probe, "period_seconds", null)
              failure_threshold     = lookup(containers.value.startup_probe, "failure_threshold", null)

              dynamic "tcp_socket" {
                for_each = lookup(containers.value.startup_probe, "tcp_socket", null) != null ? [1] : []
                content {
                  port = lookup(containers.value.startup_probe.tcp_socket, "port", null)
                }
              }

              dynamic "http_get" {
                for_each = lookup(containers.value.startup_probe, "http_get", null) != null ? [1] : []
                content {
                  path = lookup(containers.value.startup_probe.http_get, "path", null)
                  port = lookup(containers.value.startup_probe.http_get, "port", null)

                  dynamic "http_headers" {
                    for_each = lookup(containers.value.startup_probe.http_get, "http_headers", []) != null ? lookup(containers.value.startup_probe.http_get, "http_headers", []) : []
                    content {
                      name  = http_headers.value.name
                      value = lookup(http_headers.value, "value", null)
                    }
                  }
                }
              }

              dynamic "grpc" {
                for_each = lookup(containers.value.startup_probe, "grpc", null) != null ? [1] : []
                content {
                  port    = lookup(containers.value.startup_probe.grpc, "port", null)
                  service = lookup(containers.value.startup_probe.grpc, "service", null)
                }
              }
            }
          }
        }
      }

      # Volumes
      dynamic "volumes" {
        for_each = var.volumes
        content {
          name = volumes.value.name

          # Secret Volume
          dynamic "secret" {
            for_each = lookup(volumes.value, "secret", null) != null ? [1] : []
            content {
              secret       = volumes.value.secret.secret
              default_mode = lookup(volumes.value.secret, "default_mode", null)

              dynamic "items" {
                for_each = lookup(volumes.value.secret, "items", []) != null ? lookup(volumes.value.secret, "items", []) : []
                content {
                  path    = items.value.path
                  version = items.value.version
                  mode    = lookup(items.value, "mode", null)
                }
              }
            }
          }

          # Cloud SQL Instance
          dynamic "cloud_sql_instance" {
            for_each = lookup(volumes.value, "cloud_sql_instance", null) != null ? [1] : []
            content {
              instances = volumes.value.cloud_sql_instance.instances
            }
          }

          # Empty Dir
          dynamic "empty_dir" {
            for_each = lookup(volumes.value, "empty_dir", null) != null ? [1] : []
            content {
              medium     = lookup(volumes.value.empty_dir, "medium", null)
              size_limit = lookup(volumes.value.empty_dir, "size_limit", null)
            }
          }

          # GCS
          dynamic "gcs" {
            for_each = lookup(volumes.value, "gcs", null) != null ? [1] : []
            content {
              bucket        = volumes.value.gcs.bucket
              read_only     = lookup(volumes.value.gcs, "read_only", null)
              mount_options = lookup(volumes.value.gcs, "mount_options", null)
            }
          }

          # NFS
          dynamic "nfs" {
            for_each = lookup(volumes.value, "nfs", null) != null ? [1] : []
            content {
              server    = volumes.value.nfs.server
              path      = lookup(volumes.value.nfs, "path", null)
              read_only = lookup(volumes.value.nfs, "read_only", null)
            }
          }
        }
      }
    }
  }
}