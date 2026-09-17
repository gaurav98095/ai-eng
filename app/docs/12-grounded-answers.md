# Module 12: answer from retrieved sources

> **Historical v2 walkthrough.** This synchronous endpoint remains a diagnostic
> path. The normal UI flow is queued chat (`202`) handled by `chat-worker`; use
> [the current runbook](22-current-architecture.md) for the complete flow.

Module 10 made session search return ranked chunks. Module 11 added a separate
generation API. This module connects those boundaries: the backend retrieves
evidence for one session, sends a bounded prompt to the configured model
service, and returns both the generated answer and the exact excerpts provided
to the model.

This is a useful end-to-end RAG path, not a guarantee that a model will always
be correct or cite every claim. The caller should show the returned sources so
people can inspect the evidence.

## 0. Why the boundaries matter

This project is deliberately structured as a learning laboratory. Retrieval
(`retrieval/service.py`), context packing (`retrieval/prompting.py`), answer
orchestration (`retrieval/answer.py`), generation transport
(`generation/client.py`), and model execution (`generation/model.py`) are
separate seams. Future experiments can replace one layer—for example with
token-aware packing, a reranker, speculative decoding, a larger model, a
custom CUDA kernel, or a GPU-specific serving client—without rewriting the
other layers or changing the public route contract. Keep experimental code in
its own module and select it through configuration or dependency injection;
avoid embedding benchmark-specific logic in the API route.

## 1. Configure the backend's generation client

The backend already selects its embedding endpoint independently from its
generation endpoint. Configure the model host and bearer token in the local
backend environment (never in a committed file):

```dotenv
EDGENTRAG_USE_COLAB_FOR_LLM=true
EDGENTRAG_COLAB_GENERATION_SERVICE_URL=https://replace-me.trycloudflare.com
EDGENTRAG_GENERATION_API_TOKEN=the-same-token-configured-in-the-notebook
```

For Lightning AI, set `EDGENTRAG_USE_COLAB_FOR_LLM=false` and
`EDGENTRAG_LIGHTNING_GENERATION_SERVICE_URL` instead. The URL and token must be
configured together. The backend will send `POST /generate` with a bearer
header, using an async HTTP client with a bounded timeout. It does not load
model weights itself. If generation is not configured, search continues to
work while the answer endpoint returns 503.

## 2. Follow the answer request

The new route is:

```text
POST /sessions/{session_id}/answers
```

Example request:

```json
{
  "query": "What does the application shell provide?",
  "top_k": 5,
  "max_new_tokens": 256
}
```

The API validates the request, searches only ready chunks belonging to that
session, and releases the database connection before any network calls. It
then includes highest-ranked, complete chunks in a prompt up to 16,000
characters. Chunks are never silently truncated; if even the first source does
not fit, the endpoint returns 413. Any sources that do not fit after earlier,
higher-ranked sources are omitted.

The instruction asks the model to answer only from the excerpts, treat their
contents as untrusted data, acknowledge missing evidence, and use citation
labels such as `[S1]`. The response includes `answer`, model/token metadata, and
`sources`. Each source contains its label and the original chunk metadata,
including filename, chunk index, content, and retrieval score. The returned
source list represents exactly the evidence sent to generation. Citation
labels are guidance to the model; applications should not assume the model
followed them without validating the answer.

## 3. Call the local API

Start the backend, then send a request to its local API. This call is distinct
from the direct generation-service smoke test in Module 11: it exercises
retrieval, prompt construction, remote generation, and source return together.

```bash
curl --fail-with-body --max-time 360 -sS \
  http://127.0.0.1:8000/sessions/SESSION_ID/answers \
  -H 'Content-Type: application/json' \
  -d '{"query":"What does the application shell provide?","top_k":5,"max_new_tokens":128}'
```

The answer endpoint uses the configured generation bearer token internally;
the local caller does not send it. If the model service returns 404, verify its
base URL and `/generate` route. A 503 from the local API means a model service
is missing, unreachable, misconfigured, or returned an invalid response. A
409 means there are no compatible embedded chunks yet. A 413 means the session
or context exceeds a configured bound. Check the API logs for local diagnosis;
remote error bodies and credentials are not returned to the caller.

## 4. Verify without a hosted model

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest app/backend/tests/test_config.py app/backend/tests/test_generation_client.py app/backend/tests/test_search.py
.venv/bin/ruff check app/backend
```

The tests use fake embedding and generation providers, plus an HTTP transport
stub. They verify request validation, session-scoped retrieval, safe handling
of remote failures, prompt bounds, citation labels, and response sources. For
an end-to-end model check, configure either the Colab or Lightning generation
URL and token, then call the local endpoint above.
