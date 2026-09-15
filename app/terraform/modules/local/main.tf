terraform { required_providers { docker = { source = "kreuzwerker/docker" } } }

resource "docker_network" "app" { name = "${var.project_name}-local" }
resource "docker_volume" "postgres" { name = "${var.project_name}-postgres" }
resource "docker_volume" "redis" { name = "${var.project_name}-redis" }

resource "docker_image" "postgres" { name = "pgvector/pgvector:pg16" }
resource "docker_image" "redis" { name = "redis:7-alpine" }

resource "docker_container" "postgres" {
  name = "${var.project_name}-postgres"
  image = docker_image.postgres.image_id
  env = ["POSTGRES_USER=edgentrag", "POSTGRES_PASSWORD=edgentrag", "POSTGRES_DB=edgentrag"]
  ports { internal = 5432, external = 5432 }
  volumes { volume_name = docker_volume.postgres.name, container_path = "/var/lib/postgresql/data" }
  networks_advanced { name = docker_network.app.name }
}

resource "docker_container" "redis" {
  name = "${var.project_name}-redis"
  image = docker_image.redis.image_id
  command = ["redis-server", "--appendonly", "yes"]
  ports { internal = 6379, external = 6379 }
  volumes { volume_name = docker_volume.redis.name, container_path = "/data" }
  networks_advanced { name = docker_network.app.name }
}
