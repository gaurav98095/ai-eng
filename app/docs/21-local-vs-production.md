# Local versus production services

The runtime boundary is selected by `EDGENTRAG_ENVIRONMENT`:

- `local`: PostgreSQL/pgvector and Redis run in Docker Compose. Floci runs
  separately on the host's published port `4566` for S3/SQS emulation. Start
  Floci first, then use `make -C app infra-local` from the repository root.
- `production`: RDS PostgreSQL, ElastiCache Redis, S3, and SQS. Leave
  `EDGENTRAG_AWS_ENDPOINT_URL` unset. Credentials come from the workload IAM
  role/Secrets Manager rather than committed files.

Example local command:

```sh
./setup-local.sh
```

The current local Compose configuration sets an embedding queue URL and starts
the dedicated worker, which consumes only
`{session_id, file_id}` identifiers after ingestion has committed chunks.
Direct embedding remains available in code when the embedding queue URL is unset.

The production overlay currently inherits local configuration when merged with
the base and must not be deployed unchanged. See the [application guide](../README.md)
for production prerequisites and known limitations.
