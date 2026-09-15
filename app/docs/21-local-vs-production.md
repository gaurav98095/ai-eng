# Local versus production services

The runtime boundary is selected by `EDGENTRAG_ENVIRONMENT`:

- `local`: Floci, PostgreSQL/pgvector, and Redis all run in Docker Compose for
  S3/SQS emulation and persistence. Start the stack with `./setup-local.sh`.
- `production`: RDS PostgreSQL, ElastiCache Redis, S3, and SQS. Leave
  `EDGENTRAG_AWS_ENDPOINT_URL` unset. Credentials come from the workload IAM
  role/Secrets Manager rather than committed files.

Example local command:

```sh
./setup-local.sh
```

The normal local profile keeps embedding synchronous when the embedding queue
URL is unset. To exercise the v3 asynchronous embedding boundary, configure an
embedding queue URL; Compose starts the dedicated worker, which consumes only
`{session_id, file_id}` identifiers after ingestion has committed chunks.

The production overlay is only a configuration convenience; the v3 AWS
deployment remains the preferred ECS/ASG deployment described in the release
runbook.
