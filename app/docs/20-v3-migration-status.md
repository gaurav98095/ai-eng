# v3 architecture migration status

The v3 reference is the source of truth for deployment and persistence
boundaries. This migration is intentionally incremental so local development
continues to use SQLite while production can use RDS PostgreSQL.

## Implemented foundation

- PostgreSQL URL normalization for async API and synchronous workers.
- Configurable database pools and SQLite WAL/busy-timeout/foreign-key pragmas.
- Redis and four queue URL settings (ingest, chat, STT, embedding).
- v3 table names (`sessions`, `files`, `chunks`) and session/file bookkeeping
  fields while preserving existing Python service interfaces.
- Explicit Alembic migrations `0006_v3_schema_foundation` and
  `0007_pgvector_index`; schema changes are not
  performed during application startup.
- Optional pgvector column type with a JSON fallback for installations that do
  not install pgvector.
- Local Compose uses Docker workers/API plus a Redis volume and reaches the
  host-run Floci emulator at `host.docker.internal:4566`; production overlays
  leave `AWS_ENDPOINT_URL` empty so boto3 uses managed AWS services.

## Implemented service boundaries

- Shared Redis history/events and generic SQS at-least-once contracts.
- Cognito/local authentication and ownership checks on session/chat/events.
- One-time SSE tickets and the Redis-backed event stream.
- Native PostgreSQL pgvector cosine retrieval with bounded SQLite fallback and
  `/embed_query` integration.
- Separate ingestion/chat/STT worker images, queue visibility settings, and
  deployment templates for AWS.
- Production configuration fails fast unless ingestion and chat queue URLs are
  present; embedding also requires its queue when an embedding provider is
  enabled. Workers with optional queues unset disable themselves cleanly.
- Asynchronous embedding queue path: ingestion persists chunks and publishes
  identifiers; the dedicated embedding worker writes vectors and finalizes
  file/session state. The local default keeps direct embedding for simplicity.

The STT worker validates jobs but deliberately leaves valid work unacknowledged
until a transcription provider and durable transcript callback are configured;
invalid payloads are acknowledged and discarded.

The deployment templates intentionally retain `REPLACE_*` values: networking,
IAM, secrets, and ECR image identifiers are account-specific release inputs.

The API and workers must remain stateless; PostgreSQL is the durable source of
truth, while Redis events/cache are recoverable and best-effort.
