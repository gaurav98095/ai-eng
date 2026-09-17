# Module 11: a standalone language-model API

> **Historical v2 walkthrough.** The model service contract is still a useful
> boundary, but local startup is now Compose/Make and the current request path
> is documented in [the current runbook](22-current-architecture.md) and
> [model-service hosting](model-service-hosting.md).

Search now returns relevant chunks. It does not yet write an answer. Before
connecting those two steps, we need one new component: a service that accepts
a prompt and generates text. This module builds that service only.

The local backend, ingestion worker, and embedding service stay unchanged.
The next module will combine search and generation into cited answers.

## 1. Understand the boundary

Our new Colab process exposes public `GET /health` and authenticated
`POST /generate`. It needs a model and a bearer token, but no database, S3,
SQS, or AWS credentials. Keep it separate from the embedding process:
embeddings turn text into vectors; generation turns a prompt into new text.

We keep the legacy project's `TinyLlama/TinyLlama-1.1B-Chat-v1.0` as a small
teaching baseline. Its [official model card](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0)
shows the Transformers chat-template interface. It can produce incorrect
answers; this endpoint alone provides no grounding or citation guarantees.

## 2. Write configuration and request contracts first

Start with [generation/settings.py](../backend/src/edgentrag/generation/settings.py).
This settings class reads process environment variables with the
`EDGENTRAG_GENERATION_` prefix. It deliberately does not load the local
backend's `.env` file.

| Variable suffix | Default | Purpose |
| --- | --- | --- |
| `API_TOKEN` | empty | Required to use generation; store in Colab Secrets |
| `MODEL_NAME` | `TinyLlama/TinyLlama-1.1B-Chat-v1.0` | Model identifier |
| `DEVICE` | `auto` | Choose CUDA if available, otherwise CPU |
| `MAX_INPUT_TOKENS` | `1536` | Maximum complete formatted input |
| `CONTEXT_WINDOW` | `2048` | Input and requested output budget together |

Then read [generation/schemas.py](../backend/src/edgentrag/generation/schemas.py).
`GenerateRequest` defines three fields:

- `instructions`: a short system instruction, with a useful default.
- `prompt`: required user text, at most 16,000 characters.
- `max_new_tokens`: an integer from 1 to 512, default 256.

Whitespace-only strings, unknown fields, and invalid lengths are rejected.
Character limits protect the request boundary; they are not token limits.
`GenerateResponse` returns the model name, generated content, and input/output
token counts. Output counts include generated special tokens even though
those tokens are removed from the displayed content.

## 3. Write the model adapter

Read [generation/model.py](../backend/src/edgentrag/generation/model.py).
`TextGenerator` is a small protocol: callers only need `is_loaded` and
`generate(request)`. This lets tests substitute a fake without downloading
weights or installing PyTorch.

`TransformersGenerator` performs these operations in order:

1. Acquire a lock and lazily load the tokenizer and model on the first call.
2. Format system and user messages with the model's chat template.
3. Tokenize the entire formatted prompt without truncation.
4. Check the input limit and reserve room for the requested output.
5. Run inference, slice off the original prompt tokens, and decode the answer.

When formatting a chat template into text first, the following tokenizer call
uses `add_special_tokens=False`. This avoids duplicating special tokens, as
explained in the [Transformers chat-template documentation](https://huggingface.co/docs/transformers/v4.45.2/chat_templating).

The effective context limit is the smaller of our configured limit and the
model's reported positional limit. Oversized prompts fail with HTTP 413;
we do not silently discard text that might later contain important sources.
Changing models may require adapting this adapter and its context settings.

The lock serializes loading and generation within one process. The service
uses greedy decoding and inference mode. Run **one Uvicorn worker**, without
reload, so multiple processes do not each allocate a copy of the model.
There is no bounded request queue yet: this is a development service, not a
production multi-user deployment.

## 4. Add the HTTP boundary

Read [generation/app.py](../backend/src/edgentrag/generation/app.py).
`create_app()` accepts settings and a generator, making route tests independent
of infrastructure. The default instance supplies the real model adapter.

Bearer authentication runs before model inference. An unset server token fails
closed with 503; a missing or incorrect caller token returns 401. Model work
runs in a thread pool so it does not block the asynchronous event loop.
Unexpected model errors are logged server-side and return a generic 503,
without exposing internal exception details to callers.

`/health` does not download or warm up the model. `loaded: false` means the
HTTP process is alive, not that inference has been verified. A successful
`/generate` call is the actual smoke test.

## 5. Run the Colab notebook

For Lightning AI, use the separate
[Lightning model-services notebook](../lightning_model_services.ipynb). See
[model-service hosting](model-service-hosting.md) for local backend
host-profile configuration. The instructions below cover Colab.

Open [colab_model_services.ipynb](../colab_model_services.ipynb) in Colab.
This notebook runs both embedding and generation. Run its cells in order:

1. Choose a GPU runtime if available. CPU works but is slower.
2. Make the current project available to Colab; the setup cell can clone a
   public HTTPS repository. For a private project, upload it to the expected
   directory instead of putting credentials in a clone URL.
3. The setup installs `app/backend[embedding,generation]`. These optional model packages
   are not required by the local backend or its ordinary tests.
4. Create a Colab Secret named exactly `EDGENTRAG_GENERATION_API_TOKEN` and
   enable notebook access. Also enable access to `EDGENTRAG_EMBEDDING_API_TOKEN`;
   keep the existing embedding token and use a separate generation token.
5. Load settings and start embedding on port `8001` and generation on `8003`.
6. Run both local warm-up requests. The first calls download model weights and
   can take several minutes.
7. Start both Cloudflare tunnels and copy their labeled HTTPS URLs. Update the
   local embedding service URL and restart the API and worker.
8. Test it from your Mac with the command below.

Generate a token locally if needed:

```bash
.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

Both processes share one runtime. Embeddings default to CPU to leave GPU
memory for generation. Colab and Quick Tunnels are temporary
development infrastructure; runtime restarts stop the processes and tunnel
URLs change. Do not put tokens in committed notebook cells or outputs.
The cleanup cell is opt-in so running all cells does not immediately stop
the service.

## 6. Call generation from your Mac

In your usual zsh terminal, replace the URL and enter the same secret when
prompted. This goes directly to Colab, not to the local backend on port 8000.

```bash
GENERATION_URL="https://replace-me.trycloudflare.com"
read -s 'GENERATION_TOKEN?Generation API token: '
echo
curl --fail-with-body --max-time 300 -sS "$GENERATION_URL/generate" \
  -H "Authorization: Bearer $GENERATION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"instructions":"Explain briefly for a beginner.","prompt":"What is semantic search?","max_new_tokens":128}'
unset GENERATION_TOKEN
```

A successful JSON response contains `model`, `content`, `input_tokens`, and
`output_tokens`. The text and counts depend on the actual request and model.

For 401, check that the caller token matches the Colab secret. For 413, shorten
the prompt or lower the requested output. For 422, check the request fields.
For 503, check the server token and `/tmp/edgentrag-generation.log` in Colab.
If the tunnel cannot connect, check the runtime and rerun its tunnel cell.

The backend answer endpoint now consumes this service at
`POST /sessions/{session_id}/answers`; configure the URL/token as described in
Module 12. The queued chat endpoint is a separate acceptance-only contract
until its worker is implemented.

## 7. Verify the component without a GPU

From the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/pytest app/backend/tests/test_generation.py
.venv/bin/ruff check app/backend
```

The tests exercise authentication, validation, sanitized failures, lazy loading,
chat formatting, decoding, and token-budget rejection using fake model objects.
They do not establish real GPU compatibility or model answer quality; run the
notebook's warm-up and external curl request to check the deployed service.

Stop here for this module. We have an independently testable generation API,
not a complete RAG answer endpoint. Next we will retrieve session-specific
chunks, build a bounded context prompt, call this service, and return sources.
