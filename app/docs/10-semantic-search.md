# Component 10: search the saved document chunks

Component 9 persisted an embedding for each chunk. Now we use those vectors to
find source text related to a question. The result is ranked evidence; generating
an answer from that evidence is a separate component.

This module adds one endpoint to the main local FastAPI application:

~~~http
POST /sessions/{session_id}/search
Content-Type: application/json

{"query":"How do I start the backend?","top_k":3}
~~~

## 1. Define what a search request means

Start with `retrieval/schemas.py`. The request contains a question and how many
matches to return. Questions are trimmed, must contain non-whitespace text, and
are limited to 2,000 characters. `top_k` defaults to 5 and accepts integers from
1 to 20. Unknown fields are rejected so spelling mistakes are visible.

Each match includes the chunk ID, file ID, original filename, chunk index,
content, and a similarity score. These source references let a future answer
include citations. The vectors themselves are not part of the response.

## 2. Load candidates from the correct session

Next read `retrieval/service.py`. The database query joins `document_chunks`
to `session_files`, filtering by the requested session ID and file status
`ready`. A chunk must also have an embedding model recorded.

Filtering belongs in the database query: it prevents another session's source
text from entering the ranking step. Session IDs scope this tutorial's search;
account authentication and ownership checks have not been added yet.

An existing session can be searched while some of its files are still processing;
only its completed files contribute matches. An unknown session returns 404.
An empty session or one containing only text-only chunks returns 409 with a
message explaining that embedded chunks must finish ingesting first.

The selected rows are copied into small `Candidate` objects and the database
session closes before the request to Colab. A slow model request therefore does
not hold a database connection open.

## 3. Embed the question using Colab

Reuse `embedding/client.py` from Component 9. It sends the question as a
one-item batch to `/embed` and returns a vector plus its model identity.

The main API now owns an HTTP embedding client during its FastAPI lifespan,
just as it owns its database connection pool. The client is shared across
requests and closed when the API stops. `get_embedding_provider` exposes that
resource through a dependency, so tests can substitute a fake provider.

Both the API and worker need these settings:

~~~text
EDGENTRAG_EMBEDDING_SERVICE_URL=https://your-current-tunnel.trycloudflare.com
EDGENTRAG_EMBEDDING_API_TOKEN=<same secret as Colab>
~~~

Export them in both terminals if the API and worker run separately. The local
API sends the bearer token to Colab; a search request from your terminal to the
local API does not need that token. Restart the API and worker after changing
their environment settings.

## 4. Rank with cosine similarity

For vectors `q` and `v`, cosine similarity is:

~~~text
score = dot(q, v) / (length(q) * length(v))
~~~

We normalize both vectors and calculate their dot product. This also handles
stored vectors whose lengths are not exactly one. For example, a question
vector `[1, 0]` scores `[3, 4]` as `0.6` and `[5, 0]` as `1.0`.

The result lies between -1 and 1. Higher means closer in the model's vector
space; it is not an answer-confidence percentage. This endpoint returns the
nearest chunks even when none is a strong match. It does not impose a relevance
threshold yet.

Only vectors with the query's model identity and dimension are compared.
Malformed, non-finite, and zero-length vectors are skipped. If no compatible
vectors remain, the endpoint returns 409 rather than comparing unrelated
vector spaces. An invalid query embedding or unavailable Colab service returns
a sanitized 503.

Results are sorted by descending score, with chunk ID as a stable tie breaker.
The CPU work runs in a separate thread so the async HTTP handler does not do
the ranking loop on its event loop.

## 5. Keep the HTTP route small

`api/routes/search.py` validates the body through Pydantic, calls the search
service, and maps domain failures to HTTP responses:

| Status | Meaning |
| --- | --- |
| 200 | Ranked chunks are available. |
| 404 | The session does not exist. |
| 409 | No ready embedded chunks, or none compatible with the current model. |
| 413 | The session exceeds the configured candidate limit. |
| 422 | Invalid question or `top_k`. |
| 503 | The API's embedding service is unconfigured, unavailable, or invalid. |

The route is registered in `api/app.py`, which makes it visible in `/docs`.
There is no new database migration in this module: it reads Component 9's
existing columns.

## 6. Run and test it

Start Colab using `app/colab_model_services.ipynb` and test its `/embed`
endpoint once to load the model. Keep the runtime and tunnel running. At the
repository root, set the current URL and enter the shared token without echo:

~~~zsh
export EDGENTRAG_EMBEDDING_SERVICE_URL="https://your-current-tunnel.trycloudflare.com"
read -s "EDGENTRAG_EMBEDDING_API_TOKEN?Colab embedding token: "
echo
export EDGENTRAG_EMBEDDING_API_TOKEN
./test-run.sh
~~~

The script applies migrations, starts the local API and worker, uploads the
sample Markdown document, waits for chunks and vectors, and searches that same
session. It prints the matched filenames, chunk indices, and scores. It reads
effective application settings, including settings in `app/backend/.env`, to
decide whether to exercise embeddings and search.

With embeddings enabled, the ingestion wait budget accounts for the sample's
batch count and the configured embedding request timeout. This replaces the
old fixed 30-second cutoff, which could kill the worker before its embedding
request returned. For a longer diagnostic run you can override the budget:

~~~bash
TEST_WORKER_TIMEOUT_SECONDS=600 ./test-run.sh
~~~

This extends how long the script waits; it does not repair a stopped tunnel or
an unavailable embedding service. If embedding settings are absent, the script
tests text-only ingestion and explicitly skips search.

To search manually, start the main API with the same embedding settings:

~~~bash
.venv/bin/python -m uvicorn edgentrag.api.app:app --reload
~~~

Use a session that already has completed, embedded documents (for example, the
session ID printed by `test-run.sh`):

~~~bash
SESSION_ID="paste-your-session-id"
curl -sS -X POST "http://127.0.0.1:8000/sessions/${SESSION_ID}/search" \
  -H 'Content-Type: application/json' \
  -d '{"query":"How is the FastAPI application started?","top_k":3}'
~~~

A response contains `session_id`, the trimmed `query`, `model`,
`searched_chunks`, `skipped_chunks`, and `matches`. `searched_chunks` counts
valid compatible candidates; `skipped_chunks` counts candidates excluded for
model/shape/data problems. Files still processing and chunks without a model
are excluded before those counts.

Old text-only chunks are not automatically backfilled when embedding settings
are enabled. Upload those documents again with embeddings enabled. The worker
deliberately skips already-ready files on redelivery.

## 7. Verify behavior independently of Colab

~~~bash
.venv/bin/pytest app/backend/tests/test_search.py
.venv/bin/ruff check app/backend
~~~

The tests migrate a temporary SQLite database and inject known query vectors.
They check cosine ranking, limits, session isolation, unfinished files, invalid
requests, malformed vectors, model changes, and unavailable services. No model
download or network call is needed.

This is a bounded exhaustive search over JSON vectors. By default it accepts
up to 5,000 candidate chunks per session (`EDGENTRAG_SEARCH_MAX_CHUNKS`). Larger
sessions return 413 instead of silently searching only the first part. A
database vector index is the next scaling step.
[Module 11](11-colab-generation-service.md) first builds the standalone
generation service. The following module will connect it to these source
chunks for answer generation.
