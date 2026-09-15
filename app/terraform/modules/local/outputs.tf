output "database_url" { value = "postgresql+asyncpg://edgentrag:edgentrag@localhost:5432/edgentrag" }
output "redis_url" { value = "redis://localhost:6379/0" }
output "s3_bucket" { value = "edgentrag-local" }
output "queue_urls" { value = { ingestion = "http://localhost:4566/000000000000/edgentrag-ingestion", chat = "http://localhost:4566/000000000000/edgentrag-chat", stt = "http://localhost:4566/000000000000/edgentrag-stt", embedding = "http://localhost:4566/000000000000/edgentrag-embedding" } }
