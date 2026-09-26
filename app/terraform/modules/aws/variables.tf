variable "project_name" { type = string }
variable "environment" { type = string }
variable "aws_region" { type = string }
variable "vpc_id" { type = string }
variable "private_subnet_ids" { type = list(string) }
variable "database_password" { type = string, sensitive = true }
variable "ecs_security_group_ids" { type = list(string) }
variable "frontend_origins" { type = list(string) }
variable "queue_max_receives" { type = number }
