terraform { required_providers { aws = { source = "hashicorp/aws" } } }

data "aws_caller_identity" "current" {}
resource "aws_ecr_repository" "images" {
  for_each = toset(["api", "ingestion", "embedding", "chat", "stt"])
  name = "${var.project_name}/${each.key}"
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_s3_bucket" "documents" { bucket_prefix = "${var.project_name}-${var.environment}-" }
resource "aws_s3_bucket_public_access_block" "documents" { bucket = aws_s3_bucket.documents.id; block_public_acls = true; block_public_policy = true; ignore_public_acls = true; restrict_public_buckets = true }

resource "aws_sqs_queue" "queues" {
  for_each = toset(["ingestion", "chat", "stt", "embedding"])
  name = "${var.project_name}-${var.environment}-${each.key}"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds = 20
}

resource "aws_elasticache_subnet_group" "redis" { name = "${var.project_name}-${var.environment}-redis"; subnet_ids = var.private_subnet_ids }
resource "aws_elasticache_replication_group" "redis" {
  replication_group_id = "${var.project_name}-${var.environment}-redis"
  description = "EdgentRAG Redis"
  engine = "redis"
  node_type = "cache.t4g.small"
  num_cache_clusters = 1
  transit_encryption_enabled = true
  subnet_group_name = aws_elasticache_subnet_group.redis.name
}

resource "aws_db_subnet_group" "postgres" { name = "${var.project_name}-${var.environment}-db"; subnet_ids = var.private_subnet_ids }
resource "aws_db_instance" "postgres" {
  identifier = "${var.project_name}-${var.environment}-postgres"
  engine = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"
  allocated_storage = 20
  db_name = "edgentrag"
  username = "edgentrag"
  password = var.database_password
  db_subnet_group_name = aws_db_subnet_group.postgres.name
  skip_final_snapshot = false
  deletion_protection = true
  publicly_accessible = false
}

resource "aws_cognito_user_pool" "users" { name = "${var.project_name}-${var.environment}-users"; auto_verified_attributes = ["email"] }
resource "aws_cognito_user_pool_client" "web" { name = "${var.project_name}-web"; user_pool_id = aws_cognito_user_pool.users.id; generate_secret = false }
