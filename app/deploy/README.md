# v3 deployment templates

These templates intentionally contain placeholders, never credentials. The
production pipeline creates RDS/ElastiCache/SQS/Cognito through IaC and injects
values through Secrets Manager/SSM. Apply migrations once before switching the
API or workers to a new release.

Worker task definitions are intentionally separate: ingestion persists chunks,
embedding writes vectors, and chat produces answers. Register
`ecs-embedding-task.json` as its own ECS service when
`EDGENTRAG_EMBEDDING_QUEUE_URL` is enabled; do not fold it into the API task.
