terraform { required_providers { aws = { source = "hashicorp/aws" } } }

data "aws_caller_identity" "current" {}
resource "aws_ecr_repository" "images" {
  for_each = toset(["api", "ingestion", "embedding", "chat", "stt"])
  name = "${var.project_name}/${each.key}"
  image_scanning_configuration { scan_on_push = true }
}
resource "aws_s3_bucket" "documents" { bucket_prefix = "${var.project_name}-${var.environment}-" }
resource "aws_s3_bucket_public_access_block" "documents" { bucket = aws_s3_bucket.documents.id; block_public_acls = true; block_public_policy = true; ignore_public_acls = true; restrict_public_buckets = true }
resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  rule { apply_server_side_encryption_by_default { sse_algorithm = "AES256" } }
}
resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration { status = "Enabled" }
}
resource "aws_s3_bucket_cors_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id
  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["PUT"]
    allowed_origins = var.frontend_origins
    expose_headers = ["etag"]
    max_age_seconds = 3600
  }
}

resource "aws_sqs_queue" "dead_letters" {
  for_each = toset(["ingestion", "chat", "stt", "embedding"])
  name = "${var.project_name}-${var.environment}-${each.key}-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled = true
}

resource "aws_sqs_queue" "queues" {
  for_each = toset(["ingestion", "chat", "stt", "embedding"])
  name = "${var.project_name}-${var.environment}-${each.key}"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds = 20
  sqs_managed_sse_enabled = true
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dead_letters[each.key].arn
    maxReceiveCount = var.queue_max_receives
  })
}

resource "aws_sqs_queue_redrive_allow_policy" "dead_letters" {
  for_each = aws_sqs_queue.dead_letters
  queue_url = each.value.id
  redrive_allow_policy = jsonencode({
    redrivePermission = "byQueue"
    sourceQueueArns = [aws_sqs_queue.queues[each.key].arn]
  })
}

resource "aws_security_group" "redis" {
  name_prefix = "${var.project_name}-${var.environment}-redis-"
  description = "Redis access from EdgentRAG ECS tasks"
  vpc_id = var.vpc_id
  ingress {
    from_port = 6379
    to_port = 6379
    protocol = "tcp"
    security_groups = var.ecs_security_group_ids
  }
}

resource "aws_security_group" "postgres" {
  name_prefix = "${var.project_name}-${var.environment}-postgres-"
  description = "PostgreSQL access from EdgentRAG ECS tasks"
  vpc_id = var.vpc_id
  ingress {
    from_port = 5432
    to_port = 5432
    protocol = "tcp"
    security_groups = var.ecs_security_group_ids
  }
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
  security_group_ids = [aws_security_group.redis.id]
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
  vpc_security_group_ids = [aws_security_group.postgres.id]
  storage_encrypted = true
  backup_retention_period = 7
  skip_final_snapshot = false
  deletion_protection = true
  publicly_accessible = false
}

resource "aws_cognito_user_pool" "users" { name = "${var.project_name}-${var.environment}-users"; auto_verified_attributes = ["email"] }
resource "aws_cognito_user_pool_client" "web" { name = "${var.project_name}-web"; user_pool_id = aws_cognito_user_pool.users.id; generate_secret = false }
