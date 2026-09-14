# Switch model hosting between Colab and Lightning AI

Use the same [combined notebook](../colab_model_services.ipynb) for both hosts.
The filename is kept for existing links; it now supports both providers.
Both APIs still use the same request schemas and bearer authentication.

## 1. Choose where to run the notebook

In cell 1, set `use_colab_for_embedding` and `use_colab_for_llm` independently:
`True` selects Colab; `False` selects Lightning. The notebook detects its runtime
and starts only the services assigned there. For mixed hosting, open this same
notebook in both runtimes with matching switches. No second Colab notebook is
needed. Colab uses its existing Secrets and tunnel workflow.

For Lightning AI:

1. Create/open a Studio with a Python 3.12+ environment and suitable memory.
   A GPU is useful for generation; embeddings default to CPU.
2. Clone/upload this project into the Studio and open the combined notebook.
3. Set the switch for each Lightning-hosted service to `False` in cell 1.
   Enter the absolute repository
   root when prompted. The cell installs both model dependency extras.
4. In cell 2, supply `EDGENTRAG_EMBEDDING_API_TOKEN` and
   `EDGENTRAG_GENERATION_API_TOKEN` through the notebook kernel's environment
   or the hidden prompts. Shell exports in another terminal do not necessarily
   reach an already-running notebook kernel. These are your app's shared
   tokens, not Lightning account credentials. Do not save them in code cells.
5. Run the startup and warm-up cells. The servers bind to `0.0.0.0` on ports
   8001 and 8003; first requests download the models.
6. Use Lightning's port/API hosting tools to expose both ports and copy the
   public HTTPS URLs into cell 5. It verifies health and authenticated model
   calls. Lightning documents exposing ports and APIs in its
   [hosting guide](https://lightning.ai/docs/overview/host-web-apps/expose-web-apps).
7. Use public API URLs, not Studio editor links or previews requiring browser
   login. The existing HTTP client supports the app's bearer token only; it
   does not log in to Lightning or add platform-specific authentication.
8. Copy the printed configuration to your Mac and run the notebook's curl
   examples from your local terminal. Keep both model processes running.

This is a Studio-based development setup, not an automatically provisioned
managed deployment. Check your account's resource costs and runtime settings.
Sleep/restarts can interrupt requests, and cold model loads can exceed client
timeouts. Warm up both APIs before starting the ingestion worker. Cleanup stops
the notebook's processes, not the Studio or its billing.

## 2. Select the host in your local backend

Save provider URLs in `app/backend/.env` (replace the illustrative URLs):

```dotenv
EDGENTRAG_USE_COLAB_FOR_EMBEDDING=false
EDGENTRAG_USE_COLAB_FOR_LLM=false
EDGENTRAG_LIGHTNING_EMBEDDING_SERVICE_URL=https://your-lightning-embedding-host
EDGENTRAG_LIGHTNING_GENERATION_SERVICE_URL=https://your-lightning-generation-host
EDGENTRAG_COLAB_EMBEDDING_SERVICE_URL=https://your-embedding.trycloudflare.com
EDGENTRAG_COLAB_GENERATION_SERVICE_URL=https://your-generation.trycloudflare.com
```

Keep `EDGENTRAG_EMBEDDING_API_TOKEN` configured privately with the value used by
the selected remote service. Switching profiles changes URLs, not tokens: use
the same embedding token on both hosts or update this value when switching.

Change either switch between `true` (Colab) and `false` (Lightning) once its
URLs are populated. For example, `true` for embedding and `false` for LLM uses
Colab embeddings with Lightning generation. Refresh Colab URLs when tunnels
restart. Restart the
local API and worker after each configuration change; their settings are
snapshotted at startup. A shell-exported setting overrides the `.env` value.
`test-run.sh` also reads the resolved settings, so its embedding check follows
the selected profile.

Selecting Lightning for embedding requires its URL and never falls back to a
Colab URL. Unselected URLs are validated but are not used for requests.

## 3. Preserve existing setups

Both switches default to `true`. Colab uses `COLAB_EMBEDDING_SERVICE_URL` when
configured, otherwise the legacy `EDGENTRAG_EMBEDDING_SERVICE_URL`. This preserves
existing installations, including text-only ingestion when URL and token are
both unset. The local `.env` now contains both switches; credentials are preserved.

The generation profile URL is optional and reserved for the next answer
integration module. The local backend does not call generation yet. Test
`/generate` directly with its own bearer token as shown in the notebook.

Switching hosting does not migrate vectors or change model names. Keep the
same embedding model when reusing stored vectors; otherwise re-ingest the
documents so query and document embeddings are compatible.
