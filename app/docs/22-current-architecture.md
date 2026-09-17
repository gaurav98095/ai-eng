# Current architecture and operating guide

This page is the source of truth for the implementation in `app/`. The
numbered component pages before this one are learning snapshots from the
earlier v2 build. They explain useful boundaries, but their commands, table
names, queue names, and standalone-process assumptions are not a current
deployment procedure.

## Runtime topology

```text
Browser (localhost:5173)
  ├─ HTTP/SSE ─► API (localhost:8000)
  │               ├─ PostgreSQL + pgvector (Compose locally, RDS in production)
  │               ├─ Redis (Compose locally, ElastiCache in production)
  │               └─ S3/SQS (Floci locally, AWS in production)
  └─ presigned PUT ─► S3/SQS endpoint

API ──queues──► ingestion worker ──► embedding worker ──► PostgreSQL readiness
API ──chat queue──► chat worker ──► retrieval + generation service
API ──STT queue──► STT worker (currently validates jobs only)
```

The API does not own model weights. Embedding and generation are HTTP model
services hosted by the Colab or Lightning notebooks. Queue workers own slow
work; the synchronous `POST /sessions/{id}/answers` endpoint remains a direct
diagnostic path and can wait on retrieval/model calls.

## Local procedure

Run Floci separately; it is intentionally not in `compose.yaml`:

```sh
docker run --rm -p 4566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_SECURITY_EXTRA_CORS_ALLOWED_ORIGINS=http://localhost:5173 \
  floci/floci:latest
```

From the repository root, start the complete application stack with Make:

```sh
make -C app infra-local
```

This checks Floci, builds the API/frontend and four worker images, starts
PostgreSQL and Redis, applies Alembic migrations, and creates the bucket plus
the ingestion, chat, STT, and embedding queues. If Floci is not reachable it
fails with `Floci not found`; it does not start or manage Floci.

Useful commands:

```sh
docker compose -f app/compose.yaml ps
docker compose -f app/compose.yaml logs --tail=100 api ingestion-worker embedding-worker chat-worker
make -C app migrate-local
make -C app destroy-local
```

Use `http://localhost:5173`, `http://127.0.0.1:8000/docs`, and
`http://localhost:4566`. The browser talks to Floci through `localhost`; the
containers talk to it through `host.docker.internal`.

## Data and request lifecycle

1. `POST /sessions` creates a session.
2. `POST /sessions/{id}/uploads` creates pending `files` rows and presigned
   targets. The browser PUTs bytes directly to Floci/AWS.
3. `POST .../uploads/{file_id}/complete` verifies object metadata and queues
   `{session_id, file_id}`. The ingestion worker extracts UTF-8 Markdown/plain
   text into `chunks` rows.
4. The embedding worker sends chunk text to `/embed`, stores vectors and the
   model identity, and marks file/session readiness.
5. `POST /sessions/{id}/chat` stores the user and pending assistant rows and
   returns `202`; the chat worker retrieves compatible vectors, calls
   `/generate`, and persists the answer and sources. Poll `GET
   /sessions/{id}/chat` or use the event endpoint.

The current upload flow supports `.md` and `.txt` only. STT, PDF/Office/image
parsing, and source rendering in the frontend are not complete features.

Authentication is local-development friendly and ownership is enforced on the
session, upload, chat, and event routes. The synchronous search and answers
routes still need the same ownership dependency before this can be treated as
safe for multi-user production use.

## Configuration ownership

- Compose owns local service addresses, Floci endpoint, bucket, queue URLs,
  database, Redis, and dummy local AWS credentials.
- `backend/.env` owns private model URLs/tokens and tuning values.
- `core/config.py` owns typed defaults and production validation.
- Terraform/deployment templates own production resource identifiers, but are
  scaffolding and do not yet perform a complete rollout.

Do not copy queue URLs or infrastructure values into walkthroughs or another
`.env` file. Restart API/workers after changing settings; settings are cached
at process startup.

## Production status

Production is not equivalent to local Compose. The intended target is RDS,
ElastiCache, S3/SQS, Cognito, and separately deployed API/workers/frontend.
`compose.production.yaml` is not a safe standalone production deployment: it
is an override fragment and currently inherits local values when merged with
the base. Review `app/README.md`, `deploy/README.md`, and the checklist before
using Terraform or any billable AWS resource.

Do not expose the current synchronous search/answers endpoints to untrusted
users until their session ownership checks are implemented.
