terraform {
  required_version = "= 1.13.3"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.20.0"
    }
  }

  backend "gcs" {
    # Bucket name must be provided via init-time flag:
    # terraform init -backend-config="bucket=YOUR_STATE_BUCKET_NAME"
    # Optionally, you can also specify a prefix for the state files:
  }
}

provider "google" {
  project = var.gcp_project_id
  region  = var.gcp_region
}