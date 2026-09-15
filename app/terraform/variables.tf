variable "environment" { type = string }
variable "aws_region" { type = string, default = "ap-south-1" }
variable "project_name" { type = string, default = "edgentrag" }
variable "docker_host" { type = string, default = "unix:///var/run/docker.sock" }
variable "vpc_id" { type = string, default = "" }
variable "private_subnet_ids" { type = list(string), default = [] }
variable "database_password" { type = string, sensitive = true, default = "" }
variable "container_image" { type = string, default = "" }
variable "embedding_service_url" { type = string, default = "" }
variable "generation_service_url" { type = string, default = "" }
variable "ecs_cluster_name" { type = string, default = "" }
variable "ecs_security_group_ids" { type = list(string), default = [] }
