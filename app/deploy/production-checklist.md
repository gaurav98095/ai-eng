# Production v3 checklist

- RDS PostgreSQL has the `vector` extension and migrations have completed.
- ElastiCache Redis uses TLS (`rediss://`) and is private.
- Ingest/chat/STT/embed queues have matching DLQs and visibility timeouts.
- API runs behind ALB; workers run as separate ECS services.
- API and workers use IAM roles, not AWS access keys.
- Cognito pool/client IDs and service secrets come from SSM/Secrets Manager.
- CloudFront/WAF is the only public edge; RDS and Redis remain private.
