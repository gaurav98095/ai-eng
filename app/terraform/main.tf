locals {
  is_local = var.environment == "local"
  name = "${var.project_name}-${var.environment}"
}

check "production_inputs" {
  assert {
    condition = local.is_local || (
      length(var.database_password) >= 16 &&
      var.vpc_id != "" &&
      length(var.private_subnet_ids) >= 2 &&
      length(var.ecs_security_group_ids) > 0 &&
      length(var.frontend_origins) > 0
    )
    error_message = "Production requires a 16+ character database password, a VPC, at least two private subnets, ECS security groups, and frontend origins."
  }
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
  database_password = var.database_password
  ecs_security_group_ids = var.ecs_security_group_ids
  frontend_origins = var.frontend_origins
  queue_max_receives = var.queue_max_receives
}
