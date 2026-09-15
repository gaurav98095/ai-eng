output "database_url" {
  value = local.is_local ? module.local[0].database_url : module.aws[0].database_url
  sensitive = true
}
output "redis_url" {
  value = local.is_local ? module.local[0].redis_url : module.aws[0].redis_url
  sensitive = true
}
output "s3_bucket" { value = local.is_local ? module.local[0].s3_bucket : module.aws[0].s3_bucket }
output "queue_urls" { value = local.is_local ? module.local[0].queue_urls : module.aws[0].queue_urls }
output "cognito" { value = local.is_local ? {} : module.aws[0].cognito }
output "ecr_repositories" { value = local.is_local ? {} : module.aws[0].ecr_repositories }
