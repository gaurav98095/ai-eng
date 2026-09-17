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
API ──STT queue──► STT worker ──► hosted STT service ──► transcript + chunks
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
   `{session_id, file_id}`. The ingestion worker extracts Markdown/plain text,
   PDF, and Word documents into `chunks` rows; the STT worker transcribes audio
   and video into a durable transcript plus the same `chunks` rows.
4. The embedding worker sends chunk text to `/embed`, stores vectors and the
   model identity, and marks file/session readiness.
5. `POST /sessions/{id}/chat` stores the user and pending assistant rows and
   returns `202`; the chat worker retrieves compatible vectors, calls
   `/generate`, and persists the answer and sources. Poll `GET
   /sessions/{id}/chat` or use the event endpoint.

The upload flow accepts `.md`, `.txt`, `.pdf`, `.doc`, `.docx`, `.aac`, `.m4a`,
`.mp3`, `.ogg`, `.wav`, `.mp4`, `.mov`, `.mkv`, and `.webm`. PDF/Word conversion
uses Docling in the ingestion worker; audio/video requires a configured hosted
STT service. Images and spreadsheet/presentation formats remain deferred.

Authentication is local-development friendly and ownership is enforced on the
session, upload, chat, and event routes. The synchronous search and answers
routes still need the same ownership dependency before this can be treated as
safe for multi-user production use.

## Configuration ownership

- Compose owns local service addresses, Floci endpoint, bucket, queue URLs,
  database, Redis, and dummy local AWS credentials.
- `backend/config.yml` owns committed, non-sensitive application defaults and
  tuning values.
- `backend/config.production.yml` owns the corresponding production defaults;
  production containers select it through `EDGENTRAG_CONFIG_FILE`.
- `backend/.env` owns passwords, tokens, and private model URLs. Environment
  variables override YAML for deployment-specific settings.
- `core/config.py` owns typed loading, source precedence, and production
  validation.
- Terraform/deployment templates own production resource identifiers, but are
  scaffolding and do not yet perform a complete rollout.

Do not copy queue URLs or infrastructure values into walkthroughs or another
`.env` file. `EDGENTRAG_CONFIG_FILE` may select an alternate YAML file for a
controlled deployment, but it is not needed for ordinary local development.
Restart API/workers after changing settings; settings are cached at process
startup.

`config.yml` also selects the embedding, STT, and generation model names and
their safe serving limits. The committed L4 profile selects
`BAAI/bge-base-en-v1.5`, `Qwen/Qwen2.5-7B-Instruct`, and
`Systran/faster-whisper-large-v3`; YAML comments list smaller and multilingual
alternatives. Service URLs and tokens remain private environment values.
`generation_prompt_max_chars` controls the bounded RAG evidence budget
independently of the model host's tokenizer limit.

Changing `embedding_model_name` requires re-ingestion: queries only search
chunks with exactly the same stored model identity. PostgreSQL now accepts
variable vector dimensions for that controlled swap. The old fixed-width HNSW
index is removed because it cannot cover multiple dimensions; a later
dimension-specific index migration is required before using a large corpus.

## LLM application boundary

The API and workers use this structure:

```text
llm/
├── contracts.py                  # stable /generate request-response shape
├── client.py                     # injectable hosted-provider transport
└── prompts/grounded_answer.py    # bounded RAG prompt and citations

generation/
├── app.py                        # separately deployed /generate server
├── model.py                      # Transformers model execution only
└── settings.py                   # model-host configuration
```

`retrieval/answer.py` retrieves session evidence and composes these two
application seams. Routes only translate domain exceptions to HTTP. Add a new
prompt module or `LLMProvider` implementation for experiments; do not put
prompt strings or hosted-model HTTP calls in routes, retrieval ranking, or
queue code.

## Production status

Production is not equivalent to local Compose. The intended target is RDS,
ElastiCache, S3/SQS, Cognito, and separately deployed API/workers/frontend.
`compose.production.yaml` is not a safe standalone production deployment: it
is an override fragment and currently inherits local values when merged with
the base. Review `app/README.md`, `deploy/README.md`, and the checklist before
using Terraform or any billable AWS resource.

Do not expose the current synchronous search/answers endpoints to untrusted
users until their session ownership checks are implemented.
