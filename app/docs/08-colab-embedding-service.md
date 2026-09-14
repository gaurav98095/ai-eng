# Component 8: Colab embedding service

This component adds a small HTTP service that turns text into dense vectors
with Sentence Transformers. It is designed to run separately from FastAPI so
the API and ingestion worker do not need to load a machine-learning model or
occupy a Colab GPU.

The service is standalone in this component. Component 9 connects the
ingestion worker to Colab and saves returned vectors next to their chunks.

## Why a separate service?

The API's job is handling sessions, upload metadata, and requests. The worker's
job is reading uploaded documents and splitting them into chunks. Keeping model
inference in its own process lets those parts stay lightweight, while allowing
the embedding model to use Colab's available accelerator. It also means the
embedding service needs no AWS credentials: it accepts text and returns
vectors.

The service uses `sentence-transformers/all-MiniLM-L6-v2`, lazy-loads the model
on the first `/embed` request, and returns normalized vectors. Normalization
makes cosine similarity straightforward in the vector-search component.
Sentence Transformers documents installation and the `encode` options in its
[installation guide](https://sbert.net/docs/installation.html) and
[model API reference](https://sbert.net/docs/package_reference/sentence_transformer/model.html).

## Service layout

~~~text
embedding/
├── settings.py  # model, device, limits, and shared API token
├── model.py     # lazy Sentence Transformers adapter
└── app.py       # authenticated /embed and lightweight /health endpoints
~~~

`TextEmbedder` is a small interface, so the HTTP layer can be tested with a
fake model and does not need to download real model weights during tests. The
embedding package is an optional dependency; installing the regular backend
dependencies does not install PyTorch or Sentence Transformers.

## Configure the shared token

Generate a long random token once on your own machine:

~~~bash
python -c 'import secrets; print(secrets.token_urlsafe(32))'
~~~

Save it in your password manager. You will later configure the same value as
`EDGENTRAG_EMBEDDING_API_TOKEN` for the backend and as a Colab Secret for this
service. Do not commit it, paste it into a notebook cell, or put it in the
project `.env` file. The service refuses `/embed` requests if the token is not
configured.

## Start it in Colab

Open a Colab notebook and clone or upload this project into the runtime. In a
cell, install the backend's optional embedding extra from the project root:

~~~python
%cd /content/ai-eng
!pip install -e "app/backend[embedding]"
~~~

In Colab's **Secrets** panel, add a secret named
`EDGENTRAG_EMBEDDING_API_TOKEN` containing the random token. Then configure the
runtime to read the secret and choose the accelerator. Set the notebook's
hardware accelerator to GPU if you want to use CUDA; `auto` uses CUDA when
PyTorch reports it available and otherwise falls back to CPU.

~~~python
import os
import subprocess
import sys
import time

from google.colab import userdata

os.environ["EDGENTRAG_EMBEDDING_API_TOKEN"] = userdata.get(
    "EDGENTRAG_EMBEDDING_API_TOKEN"
)
os.environ["EDGENTRAG_EMBEDDING_DEVICE"] = "auto"

server = subprocess.Popen(
    [
        sys.executable,
        "-m",
        "uvicorn",
        "edgentrag.embedding.app:app",
        "--host",
        "0.0.0.0",
        "--port",
        "8001",
    ],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.STDOUT,
)
time.sleep(3)
assert server.poll() is None, "Embedding service failed to start"
~~~

The server's health endpoint does not load the model, so the first successful
embedding request may take longer while Colab downloads and initializes model
weights.

## Check the service

Run these from another notebook cell. Health is intentionally unauthenticated
and reports whether model weights have loaded; `/embed` requires the bearer
token.

~~~python
import os
import requests

base_url = "http://127.0.0.1:8001"
health = requests.get(f"{base_url}/health", timeout=10)
print(health.status_code, health.json())

response = requests.post(
    f"{base_url}/embed",
    headers={
        "Authorization": (
            "Bearer " + os.environ["EDGENTRAG_EMBEDDING_API_TOKEN"]
        )
    },
    json={"texts": ["A short example document chunk."]},
    timeout=180,
)
response.raise_for_status()
result = response.json()
print("model:", result["model"])
print("dimensions:", result["dimensions"])
print("vector count:", len(result["embeddings"]))
~~~

The response contains one vector per input string, in the same order as the
request. By default each vector has 384 values for this model. The API limits
each batch to at most 64 texts, each no longer than 10,000 characters; those
limits can be lowered through environment settings.

## Endpoint contract

~~~http
POST /embed
Authorization: Bearer <shared-token>
Content-Type: application/json
~~~

~~~json
{"texts": ["first chunk", "second chunk"]}
~~~

Successful responses contain `model`, `dimensions`, and `embeddings`. Each
input yields a normalized list of floats. Missing/wrong authentication returns
401; an unset server token or unavailable model returns a generic service
error, and invalid text/batch sizes return 422. Error responses do not expose
the token or model exception details.

## Run locally with the fake-model tests

The tests do not install or download Sentence Transformers weights. From the
repository root, run:

~~~bash
.venv/bin/ruff check app/backend/src/edgentrag/embedding app/backend/tests/test_embedding_app.py
.venv/bin/pytest app/backend/tests/test_embedding_app.py
~~~

The FastAPI process listens inside the Colab runtime; the notebook
`app/colab_embedding_service.ipynb` can create a temporary Cloudflare Quick
Tunnel for development tests. That public URL changes on restart and is not a
production hosting setup. Component 9 configures the local worker to call the
service and persists returned vectors.
