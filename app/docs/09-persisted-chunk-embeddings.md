# Component 9: call Colab and persist chunk embeddings

> **Historical v2 walkthrough.** Current ingestion publishes identifiers to the
> dedicated embedding queue and the Compose embedding worker writes vectors.
> Use [the current runbook](22-current-architecture.md); `test-run.sh` and the
> old `document_chunks` workflow are not current commands.

Component 8 created a standalone Colab API. This component teaches the local
ingestion worker to call that API after text extraction and save each returned
vector with its chunk.

~~~text
S3 / Floci -> extract and chunk -> Colab /embed -> database
                                  text in       chunk text + vector
~~~

The Colab process does not need database or AWS access. The worker is the
client: it sends bounded batches of chunk text over HTTPS, validates the
response, then commits the chunks and vectors together.

## Where vectors are stored

Migration `0004_add_chunk_embeddings` adds two nullable columns to
`document_chunks`:

- `embedding` stores the list of floating-point values as JSON.
- `embedding_model` records which model produced those values.

Both columns are nullable so the earlier text-only worker mode still works and
already stored chunks remain valid. JSON keeps this tutorial compatible with
the current SQLite development database and ordinary SQL databases. It is a
simple first persistence format, not an approximate-nearest-neighbor index;
the search component can later move production retrieval to a vector index
such as pgvector without changing the Colab HTTP contract.

## What happens during a job

1. The worker downloads and validates the source file, then splits it into the
   existing 1,200-character chunks with 150-character overlap.
2. If embedding settings are present, it sends up to 32 chunks per HTTP
   request to `/embed` (the setting can be changed up to the endpoint's limit
   of 64).
3. It verifies the vector count and dimensions and checks that all batches
   came from the same model.
4. It replaces the file's chunk rows and commits their text, embeddings, model
   name, and ready status in a database transaction.

The worker does not delete old chunks until all embedding batches succeed. If
Colab is unreachable, returns an error, or sends malformed vectors, processing
fails transiently: the worker leaves the SQS message for retry and does not
commit partial vectors. Once the Colab endpoint is configured, keep the Colab
runtime and tunnel alive while jobs are processing.

## Configure the local worker

Start the notebook from [Component 8](08-colab-embedding-service.md) and copy
its current URL. Configure the selected provider URL and token in the private
`app/backend/.env`; see [model-service hosting](model-service-hosting.md).

~~~zsh
EDGENTRAG_USE_COLAB_FOR_EMBEDDING=true
EDGENTRAG_COLAB_EMBEDDING_SERVICE_URL=https://your-current-tunnel
EDGENTRAG_EMBEDDING_API_TOKEN=your-private-token
~~~

The secret is not echoed by `read`. These are process environment variables;
they are not committed to the repository. Do not put the token in the tunnel
URL or print it. The required settings are a pair: startup validation rejects
configuring only one of them.

Run the complete local pipeline while keeping the model notebook/tunnel
running:

~~~bash
make -C app infra-local
~~~

Compose starts FastAPI and the workers against Floci. With the embedding
URL/token set, the embedding worker contacts the hosted service and persists a
vector for each chunk. Without them, ingestion can run text-only, but search
requires compatible stored vectors.

The embedding worker is started by Compose. Do not start a second host worker
against the same queue unless you intentionally want another consumer.

## Apply the migration and run tests

Upgrade the local schema before starting the worker:

~~~bash
make -C app migrate-local
~~~

Run the worker and client tests without Floci or a real Colab runtime:

~~~bash
.venv/bin/ruff check app/backend
.venv/bin/pytest app/backend/tests
~~~

The tests use a fake embedding provider, so they verify persistence and
transaction behavior without loading model weights or sending document text
over the network.

## Important limits

The endpoint URL from a Quick Tunnel changes when Colab/tunnel restarts, so
update the local variable each time. Colab Quick Tunnels are for development,
not durable hosting. Also, document chunks are sent to the Colab runtime; only
send documents you are allowed to process there. This component stores vectors
but does not perform vector search itself. Continue with
[Component 10: semantic search](10-semantic-search.md) to query these vectors.
