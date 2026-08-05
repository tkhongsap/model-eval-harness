output "bucket_name" {
  description = "The name of the bucket"
  value       = google_storage_bucket.default.name
}

output "bucket_id" {
  description = "The ID of the bucket"
  value       = google_storage_bucket.default.id
}

output "bucket_url" {
  description = "The base URL of the bucket"
  value       = google_storage_bucket.default.url
}

output "bucket_self_link" {
  description = "The URI of the bucket"
  value       = google_storage_bucket.default.self_link
}

output "bucket_location" {
  description = "The location of the bucket"
  value       = google_storage_bucket.default.location
}

output "bucket_storage_class" {
  description = "The storage class of the bucket"
  value       = google_storage_bucket.default.storage_class
}