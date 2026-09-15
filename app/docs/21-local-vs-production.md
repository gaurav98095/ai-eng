# Local versus production services

The runtime boundary is selected by `EDGENTRAG_ENVIRONMENT`:

- `local`: SQLite, Docker Compose, Redis container, and Floci on the host for
  S3/SQS emulation. Start Floci with `floci start`, then run Compose with the
  local profile.
- `production`: RDS PostgreSQL, ElastiCache Redis, S3, and SQS. Leave
  `EDGENTRAG_AWS_ENDPOINT_URL` unset. Credentials come from the workload IAM
  role/Secrets Manager rather than committed files.

Example local command:

```sh
floci start
docker compose --profile local -f compose.yaml up --build
```

The normal local profile keeps embedding synchronous. To exercise the v3
asynchronous embedding boundary, configure an embedding queue URL and start
the additional worker with the `async-embedding` Compose profile. It consumes
only `{session_id, file_id}` identifiers after ingestion has committed chunks.

The production overlay is only a configuration convenience; the v3 AWS
deployment remains the preferred ECS/ASG deployment described in the release
runbook.
