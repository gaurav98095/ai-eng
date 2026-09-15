terraform {
  required_version = ">= 1.6.0"
  required_providers {
    aws = { source = "hashicorp/aws", version = "~> 5.0" }
    docker = { source = "kreuzwerker/docker", version = "~> 3.0" }
  }
}

provider "aws" {
  region = var.aws_region
}

provider "docker" {
  host = var.docker_host
}
