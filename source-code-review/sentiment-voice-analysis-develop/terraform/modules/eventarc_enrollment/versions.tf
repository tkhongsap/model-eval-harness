terraform {
  required_version = "= 1.13.3"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "7.20.0"
    }
  }
}
