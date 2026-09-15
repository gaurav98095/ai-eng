locals {
  is_local = var.environment == "local"
  name = "${var.project_name}-${var.environment}"
  db_password = var.database_password != "" ? var.database_password : "edgentrag"
}

module "local" {
  source = "./modules/local"
  count = local.is_local ? 1 : 0
  project_name = var.project_name
  docker_host = var.docker_host
}

module "aws" {
  source = "./modules/aws"
  count = local.is_local ? 0 : 1
  project_name = var.project_name
  environment = var.environment
  aws_region = var.aws_region
  vpc_id = var.vpc_id
  private_subnet_ids = var.private_subnet_ids
  database_password = local.db_password
  container_image = var.container_image
  embedding_service_url = var.embedding_service_url
  generation_service_url = var.generation_service_url
  ecs_cluster_name = var.ecs_cluster_name
  ecs_security_group_ids = var.ecs_security_group_ids
}
