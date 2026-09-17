# Switch model hosting between Colab and Lightning AI

Use the [Colab notebook](../colab_model_services.ipynb) when hosting on Google
Colab, or the [Lightning AI notebook](../lightning_model_services.ipynb) when
hosting in a Lightning Studio. Each notebook starts embedding, speech-to-text
(STT), and generation APIs and verifies their public URLs. The Lightning setup
script provides the same startup path without a notebook.

## 1. Choose where to run the notebook

Choose the notebook for the host you want to use. The Colab notebook creates
temporary Cloudflare tunnels; the Lightning notebook obtains URLs from the
Lightning SDK after exposing ports 8001, 8002, and 8003. Both print the `.env` settings
to use on your local machine. The Colab runtime or Studio must stay active.

For Lightning AI:

1. Create/open a Studio with a Python 3.12+ environment and suitable memory.
   A GPU is useful for generation; embeddings default to CPU.
2. Clone/upload this project into the Studio and open
   [lightning_model_services.ipynb](../lightning_model_services.ipynb).
3. Enter the absolute repository root when prompted. The setup installs the
   embedding, STT, and generation dependency extras. Alternatively run
   `bash app/setup-lightning-model-services.sh` from the repository root.
4. In the setup cell (or script prompts), supply `EDGENTRAG_EMBEDDING_API_TOKEN`,
   `EDGENTRAG_STT_API_TOKEN`, and `EDGENTRAG_GENERATION_API_TOKEN` through the notebook kernel's environment
   or the hidden prompts. Shell exports in another terminal do not necessarily
   reach an already-running notebook kernel. These are your app's shared
   tokens, not Lightning account credentials. Do not save them in code cells.
5. Run the startup and warm-up cells. The servers bind to `0.0.0.0` on ports
   8001 (embedding), 8002 (STT), and 8003 (generation); first requests download
   the models. STT loads lazily on its first audio request.
6. The notebook exposes the ports through Lightning's SDK, prints the URLs,
   and verifies authenticated model calls. Lightning documents port exposure in its
   [hosting guide](https://lightning.ai/docs/overview/host-web-apps/expose-web-apps).
7. Copy the printed configuration to your Mac and verify the endpoints from
   your local terminal. Keep all three model processes running.

This is a Studio-based development setup, not an automatically provisioned
managed deployment. Check your account's resource costs and runtime settings.
Sleep/restarts can interrupt requests, and cold model loads can exceed client
timeouts. Warm up the APIs before starting the ingestion worker. Cleanup stops
the notebook's processes, not the Studio or its billing.

## 2. Select the host in your local backend

Save provider URLs in `app/backend/.env` (replace the illustrative URLs):

```dotenv
EDGENTRAG_USE_COLAB_FOR_EMBEDDING=false
EDGENTRAG_USE_COLAB_FOR_LLM=false
EDGENTRAG_LIGHTNING_EMBEDDING_SERVICE_URL=https://your-lightning-embedding-host
EDGENTRAG_LIGHTNING_STT_SERVICE_URL=https://your-lightning-stt-host
EDGENTRAG_LIGHTNING_GENERATION_SERVICE_URL=https://your-lightning-generation-host
EDGENTRAG_COLAB_EMBEDDING_SERVICE_URL=https://your-embedding.trycloudflare.com
EDGENTRAG_COLAB_STT_SERVICE_URL=https://your-stt.trycloudflare.com
EDGENTRAG_COLAB_GENERATION_SERVICE_URL=https://your-generation.trycloudflare.com
```

Keep the embedding, STT, and generation API tokens configured privately with the
values used by the selected remote services. Switching profiles changes URLs, not
tokens: use the same tokens on both hosts or update them when switching.

Change either switch between `true` (Colab) and `false` (Lightning) once its
URLs are populated. For mixed hosting, run each host's notebook and use the
matching provider URL for each service. Refresh Colab URLs when tunnels restart. Restart the
local API and worker after each configuration change; their settings are
snapshotted at startup. A shell-exported setting overrides the `.env` value.
The settings are read by the API and workers at startup. If a generation URL is
configured, verify it with the authenticated curl smoke test in the hosting
notebook before starting the local stack:

```sh
curl --fail-with-body -sS "https://your-generation-host/health"
```

Selecting Lightning for embedding requires its URL and never falls back to a
Colab URL. Unselected URLs are validated but are not used for requests.

## 3. Preserve existing setups

Both switches default to `true`. Colab uses `COLAB_EMBEDDING_SERVICE_URL` when
configured, otherwise the legacy `EDGENTRAG_EMBEDDING_SERVICE_URL`. This preserves
existing installations, including text-only ingestion when URL and token are
both unset. The local `.env` now contains both switches; credentials are preserved.

The generation profile URL is required for grounded answers and queued chat
turns; the chat worker calls the same `/generate` contract as the answers
endpoint. The notebook also verifies `/generate` directly.

Set `EDGENTRAG_USE_COLAB_FOR_STT` the same way when selecting an STT host. The
selected STT URL and `EDGENTRAG_STT_API_TOKEN` must be configured together.
Model names and serving limits now live in `backend/config.yml`: for example,
change `generation_model_name`, `generation_context_window`, or
`stt_model_name` there before starting the corresponding hosted service.

The committed profile targets one NVIDIA L4 (24 GB):

- `BAAI/bge-base-en-v1.5` for English embeddings; use `BAAI/bge-m3` when
  multilingual or longer input matters, and lower `embedding_batch_size`.
- `Qwen/Qwen2.5-7B-Instruct` for grounded generation in the existing FP16
  Transformers host. Do not select 14B+ models until a quantized/vLLM adapter
  is deployed.
- `Systran/faster-whisper-large-v3` with `stt_compute_type: float16` for
  multilingual transcription; use the distil or small variants for lower cost.

Changing the embedding model requires re-ingestion. Stored vectors retain the
model identity and are never mixed with a query from a different model.

Switching hosting does not migrate vectors or change model names. Keep the
same embedding model when reusing stored vectors; otherwise re-ingest the
documents so query and document embeddings are compatible.
