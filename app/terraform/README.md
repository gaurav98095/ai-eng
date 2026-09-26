# EdgentRAG infrastructure

This root selects one of two deliberately separate backends:

- `local` can use the Docker provider for persistent PostgreSQL/pgvector and
  Redis. The recommended `scripts/setup-local.sh` path uses Compose as the single
  owner of those containers; do not run both owners on the same ports.
- `production` uses the AWS provider for RDS PostgreSQL, ElastiCache Redis, S3,
  SQS, and Cognito. Supply an existing VPC and private subnets; credentials
  come from the AWS credential chain and secret values must be injected by CI.

Commands are exposed from `app/Makefile`:

```sh
make -C app infra-local
cp app/terraform/prod.tfvars.example app/terraform/prod.tfvars
# edit prod.tfvars, then:
make -C app infra-prod
```

Production planning requires `TF_VAR_database_password` from CI or a secret
manager, a VPC with at least two private subnets, ECS task security groups, and
the deployed frontend origins in `prod.tfvars`. The AWS module creates S3/SQS,
RDS, ElastiCache, Cognito, and ECR resources; ECS task/service rollout remains
a separate release step under `app/deploy/`.

Terraform creates infrastructure only. Run Alembic migrations as a separate
release step, then deploy the API and the separate ingestion, embedding, chat,
and STT ECS task definitions from `app/deploy/`.

Never commit `prod.tfvars`, Terraform state, database passwords, or model API
tokens. Use a remote encrypted Terraform backend in a real AWS account.
