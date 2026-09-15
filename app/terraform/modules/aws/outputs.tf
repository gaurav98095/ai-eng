output "database_url" { value = "postgresql+asyncpg://edgentrag:${var.database_password}@${aws_db_instance.postgres.address}:5432/edgentrag", sensitive = true }
output "redis_url" { value = "rediss://${aws_elasticache_replication_group.redis.primary_endpoint_address}:6379/0", sensitive = true }
output "s3_bucket" { value = aws_s3_bucket.documents.bucket }
output "queue_urls" { value = { for name, queue in aws_sqs_queue.queues : name => queue.url } }
output "cognito" { value = { region = var.aws_region, user_pool_id = aws_cognito_user_pool.users.id, client_id = aws_cognito_user_pool_client.web.id } }
output "ecr_repositories" { value = { for name, repo in aws_ecr_repository.images : name => repo.repository_url } }
