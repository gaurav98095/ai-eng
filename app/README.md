# EdgentRAG application

EdgentRAG lets you upload documents and ask questions grounded in their contents.
It separates the web application, document processing, retrieval, and model
inference so each can be understood, tested, and replaced independently.

This is a learning-oriented baseline. The AWS deployment files are scaffolding,
and several reliability issues remain; see [current limitations](#current-limitations).

## What works today

- Create document sessions and upload UTF-8 Markdown (`.md`) or plain text (`.txt`).
- Upload directly to object storage using presigned URLs.
- Extract overlapping text chunks and index model embeddings.
- Search a session using cosine similarity with PostgreSQL/pgvector.
- Queue questions and persist generated answers with source metadata.
- View upload and chat state in a React workspace, using polling and event updates.
- Run embedding and generation services separately on Colab or Lightning AI.

PDFs, images, and audio transcription are not supported by the current upload
flow. The queue-based STT worker remains a skeleton, while the hosted
`edgentrag.stt.app` service provides authenticated `/transcribe` inference for
the Colab and Lightning model-service notebooks.

## How the pieces fit together

```text
Browser ──► FastAPI ──► PostgreSQL/pgvector (durable session, file, and chat state)
   │           │
   │           ├──► Redis (event tickets, notifications, cached history)
   │           └──► SQS-compatible queues ──► background workers
   │                                             │
   └── presigned upload ──► S3-compatible storage  └──► model-service APIs
```

For uploads, the API creates file records and upload targets. The browser uploads
each file, then confirms it with the API. Ingestion extracts chunks; the embedding
worker stores vectors and updates readiness. The current local Compose setup
configures the embedding queue, so this path is asynchronous.

For chat, the API accepts a question with HTTP `202`. The chat worker retrieves
matching chunks, builds a grounded prompt, calls generation, and persists the
answer. PostgreSQL is authoritative; Redis notifications are not durable results.
Queued chat currently answers individual questions rather than incorporating
the full stored conversation into each prompt.

## Local quick start

### Prerequisites

- Docker with Docker Compose, GNU Make, and `curl`.
- Available ports `4566`, `5432`, `6379`, `8000`, and `5173`.
- Running embedding and generation APIs for the complete upload-and-chat flow.

Python 3.12+ is needed for host-side backend development; Node.js 22 matches the
frontend image. Neither is required on the host when using only Compose.

### 1. Prepare application settings

From the repository root, copy the example **only if you do not already have a
backend `.env`**:

```sh
cp app/backend/.env.example app/backend/.env
```

Start the [Colab](colab_model_services.ipynb) or
[Lightning](lightning_model_services.ipynb) notebook and follow the
[model-service hosting guide](docs/model-service-hosting.md). Warm up both model
services, keep their runtimes active, and save their URLs and matching tokens
privately in `app/backend/.env`.

For Colab, the minimum model configuration is:

```dotenv
EDGENTRAG_USE_COLAB_FOR_EMBEDDING=true
EDGENTRAG_USE_COLAB_FOR_LLM=true
EDGENTRAG_COLAB_EMBEDDING_SERVICE_URL=https://your-embedding-host
EDGENTRAG_COLAB_GENERATION_SERVICE_URL=https://your-generation-host
EDGENTRAG_EMBEDDING_API_TOKEN=replace-with-your-private-embedding-token
EDGENTRAG_GENERATION_API_TOKEN=replace-with-your-private-generation-token
```

Configure each selected URL and token together. Without model services, the web
shell can start, but the default asynchronous indexing and answer flow cannot
complete. Colab tunnel URLs change when tunnels restart.

### 2. Start Floci separately

Run this in another terminal and leave it running:

```sh
docker run --rm -p 4566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock \
  -e FLOCI_SECURITY_EXTRA_CORS_ALLOWED_ORIGINS=http://localhost:5173 \
  floci/floci:latest
```

Floci is **not** managed by the application Compose stack. The local setup checks
`http://localhost:4566/_floci/health` and exits with “Floci not found” if it cannot
reach it. Use `http://localhost:5173` for the frontend to match the configured
storage CORS origin. The command mounts the Docker socket; use it only in a
trusted development environment.

This command does not configure persistent Floci storage. Do not rely on its
objects or queues surviving container removal.

### 3. Start the application with Make

From the repository root:

```sh
make -C app infra-local
```

Or, from `app/`, run `make infra-local`.

The target checks external Floci, builds and starts the application containers,
runs Alembic migrations, and creates the local bucket and four queues. Workers
start before resource initialization finishes, so temporary queue errors during
bootstrap can occur. The target force-recreates application containers; it is
also the restart path after editing backend settings.

| Service | Local address |
| --- | --- |
| Frontend | http://localhost:5173 |
| API documentation | http://127.0.0.1:8000/docs |
| API liveness | http://127.0.0.1:8000/health |
| API readiness | http://127.0.0.1:8000/ready |
| External Floci | http://localhost:4566 |

`/health` checks the API process only. `/ready` checks database and Redis
connectivity; neither verifies successful indexing or model inference.

### 4. Try the workspace

1. Open the frontend and connect to `http://127.0.0.1:8000`.
2. Leave the Cognito token empty for local development.
3. Create a session and upload a small `.md` or `.txt` document.
4. Wait until processing finishes and the document is `ready`.
5. Ask a question supported by that document and wait for the worker's answer.

Generation can be incorrect even with retrieved evidence. Verify important
claims against the source text.

## Configuration ownership

Do not copy local infrastructure values into multiple files.

| Configuration | Current owner |
| --- | --- |
| Local container database/Redis addresses, Floci endpoint, bucket, queues, test AWS credentials | [`compose.yaml`](compose.yaml) |
| Model URLs/tokens and application tuning | Private `backend/.env`, starting from [`.env.example`](backend/.env.example) |
| Typed defaults, validation, and host selection | [`core/config.py`](backend/src/edgentrag/core/config.py) |
| Embedding/generation model-process settings | Their respective `settings.py` modules and process environments |
| Production resource identifiers and secrets | Intended to come from IaC outputs, IAM roles, and a deployment secret provider |

Compose's explicit `environment` values override matching entries from its
`env_file`. Inside a Python process, environment variables override dotenv values.
Settings are cached at startup: restart the API and relevant workers after changes.

Containers reach external Floci through `host.docker.internal`; the browser
reaches it through `localhost`. The current frontend rewrites upload URLs to
bridge these addresses, but that workaround has signature limitations noted below.

## Everyday commands

Run these from the repository root:

```sh
# Inspect containers and recent logs.
docker compose -f app/compose.yaml ps
docker compose -f app/compose.yaml logs --tail=100 api ingestion-worker embedding-worker chat-worker

# Apply migrations to the running local API's database.
make -C app migrate-local

# Stop application containers; retain PostgreSQL/Redis volumes.
make -C app destroy-local
```

Stopping the application does not stop separately launched Floci or hosted model
services. Despite the current shutdown script's final message, external Floci
remains outside Compose's control. Avoid `down -v` unless you intend to delete
local PostgreSQL/Redis data.

## Troubleshooting

| Symptom | What to check |
| --- | --- |
| `Floci not found` | Start Floci separately and check its health endpoint on port `4566`. |
| `no configuration file provided` | Use `-f app/compose.yaml` from the repository root, or run Compose inside `app/`. |
| `command not found: --force-recreate` | Keep Compose arguments on one line or use a trailing `\` to continue the command. |
| Ticket endpoint reports CORS plus HTTP `500` | Inspect the API exception and Redis connectivity. A missing CORS header can mask the underlying server failure. |
| Upload preflight blocked on port `4566` | Check external Floci's CORS configuration and the exact browser origin. API CORS does not configure storage CORS. |
| Storage upload returns `404` | Check that bootstrap completed and the bucket exists in the currently running Floci instance. |
| Chat returns `409` | The session is not ready. Check upload, ingestion, and embedding-worker state first. |
| File remains `processing` | Check model URLs/tokens, hosted runtime availability, and embedding-worker logs. |
| Model input limit exceeded | Shorten the question/source input; the current character budget is not token-aware. |

To inspect a complete exception, increase `--tail` rather than filtering away
the final traceback lines. Do not share logs containing tokens or presigned URLs.

## Development and checks

From the repository root:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e './app/backend[dev]'
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest app/backend/tests
.venv/bin/ruff check app/backend/src app/backend/tests
git diff --check
```

For frontend development outside Docker:

```sh
cd app/frontend
npm ci
npm run build
npm run dev
```

Do not run a second frontend on the same port while Compose's frontend is active.
Ordinary backend tests use fake model providers; downloading model weights is
not required. Install the `embedding` and `generation` extras only where you
intend to run real model inference.

The current backend checks are not all green: SQLite migration compatibility and
lint violations remain. A collection error such as `No module named redis`
indicates the development environment needs the current backend dependencies.

## Production

The intended production architecture uses RDS PostgreSQL with pgvector,
ElastiCache Redis with TLS, S3, SQS, Cognito, and separately deployed API/worker
services. Use workload IAM roles and private infrastructure; inject secrets
through your deployment system rather than local development files.

Start with the [Terraform guide](terraform/README.md),
[deployment templates](deploy/README.md), and
[production checklist](deploy/production-checklist.md).

The existing tooling is incomplete:

- `make infra-prod-plan` plans infrastructure; `make infra-prod` applies Terraform
  and pushes backend images, but does not complete an ECS rollout or frontend deployment.
- `setup-prod.sh` applies infrastructure before all deployment prerequisites are
  checked. Review it before running: it can create billable resources.
- `compose.production.yaml` is not currently a safe production override. Merging
  it with the local base retains local infrastructure values, test AWS credentials,
  and the backend dotenv file. Do not deploy that merged stack unchanged.
- ECS templates contain placeholders and need a complete, consistent runtime
  configuration. Current shared validation requires ingestion and chat queue URLs
  for every production process; embedding configuration also requires its queue.
- The API's CORS allowlist currently contains only local frontend origins.
- Run migrations once as a release step before rolling out API and worker services.

Local Compose and local Terraform are alternative owners of PostgreSQL/Redis.
Do not start both on the same ports. Terraform, CloudFormation, and Compose are
not a unified deployment pipeline.

## Current limitations

- Worker claims lack crash-recovery leases; chat can remain stuck in `answering`.
- Synchronous search and answers routes still need session ownership checks
  before multi-user production exposure.
- Generic queue parsing/acknowledgment and partial-batch failure handling need hardening.
- Redis operations in async routes and SSE reconnect/cleanup behavior need work.
- A failed upload batch can leave unconfirmed files blocking session readiness;
  use distinct filenames and small test batches while experimenting.
- Browser-side presigned URL rewriting is unsafe with strict host-signature validation.
- Prompt packing uses characters rather than the model's complete token budget.
- SQLite migration `0006` is incompatible with the current test database path.
- STT validates queue jobs but cannot transcribe or persist transcripts.
- The frontend does not currently render the answer's structured source metadata.

These are known gaps, not features promised by the deployment scaffolding.

## Code and learning map

```text
app/
├── backend/src/edgentrag/  API, domain models, adapters, retrieval, and workers
├── backend/migrations/    Explicit schema release steps
├── backend/tests/         Backend contracts and fake-provider tests
├── frontend/              React/Vite workspace
├── docs/                  Numbered engineering walkthroughs and hosting guides
├── terraform/             Local/AWS infrastructure scaffolding
├── deploy/                AWS reference templates and checklist
├── compose.yaml           Local application stack; no Floci service
└── Makefile               Setup, migration, and teardown entry points
```

Read the [current architecture and operating guide](docs/22-current-architecture.md)
first. The numbered pages are historical walkthroughs, not an alternate setup
path. Then read the [engineering workflow](docs/00-engineering-with-codex.md),
[upload lifecycle](docs/06-confirm-and-queue-uploads.md),
[retrieval](docs/10-semantic-search.md),
[grounded answers](docs/12-grounded-answers.md), and
[chat worker](docs/14-chat-worker.md) for the design behind the code.
The numbered documents include historical component snapshots; this README
describes the current setup path.
