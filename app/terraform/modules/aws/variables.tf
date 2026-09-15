variable "project_name" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "database_password" { type = string, sensitive = true }
variable "container_image" { type = string }
variable "embedding_service_url" { type = string }
variable "generation_service_url" { type = string }
variable "ecs_cluster_name" { type = string }
variable "ecs_security_group_ids" { type = list(string) }
