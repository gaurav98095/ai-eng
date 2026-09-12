"""The embedding service — port 8001.

Two jobs, and they are two halves of the same idea:

    /embed     chunks the monolith sends -> numbers, stored in the vector database
    /retrieve  a question                -> the chunks whose numbers are closest

Run it from the `backend/services` folder:

    uvicorn embedding.app:app --port 8001

or, on Colab, let `launch.py` start all three at once.

The model loads the first time you call an endpoint, not at startup, so the
service comes up immediately and /health works while the weights download.
"""
import logging

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

import broker

from . import jobs, model, store
from .config import get_settings, resolve_device
from .schemas import EmbedRequest, EmbedResponse, Hit, RetrieveRequest, RetrieveResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("embedding")
settings = get_settings()

app = FastAPI(title="EdgentRAG v1 — Embedding Service", version="1.0.0")

# The monolith calls this over the public internet now, and a browser may want
# to hit /health directly while you are debugging a tunnel.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

router = APIRouter()

_loaded = False


def ensure_model() -> None:
    """Load the model on first use."""
    global _loaded
    if not _loaded:
        model.load_model()
        _loaded = True


@router.get("/health")
def health() -> dict:
    # Report the device we will actually use, not the literal "auto" from the
    # settings. On Colab this is how you confirm the GPU is being used at all,
    # and "auto" would tell you nothing.
    return {"status": "ok", "service": "embedding", "model": settings.embed_model,
            "device": resolve_device(settings.device), "loaded": _loaded}


@router.post("/embed", response_model=EmbedResponse)
def embed(body: EmbedRequest) -> EmbedResponse:
    """Index every chunk the monolith sent us.

    The monolith reads its own .jsonl files and posts the records here, because
    on Colab this process cannot see the monolith's disk.
    """
    try:
        collection = store.collection_name(body.session_id)
        if not body.chunks:
            return EmbedResponse(session_id=body.session_id, collection=collection, indexed=0)

        ensure_model()

        records = [c.model_dump() for c in body.chunks]
        for start in range(0, len(records), settings.batch_size):
            batch = records[start : start + settings.batch_size]
            vectors = model.embed_texts([c["text"] for c in batch])
            store.add_chunks(body.session_id, batch, vectors)
            log.info("indexed %d/%d chunks",
                     min(start + settings.batch_size, len(records)), len(records))

        return EmbedResponse(
            session_id=body.session_id, collection=collection, indexed=len(records)
        )
    except Exception as exc:
        log.exception("embed failed")
        raise HTTPException(status_code=500, detail=f"embedding failed: {exc}") from exc


@router.post("/retrieve", response_model=RetrieveResponse)
def retrieve(body: RetrieveRequest) -> RetrieveResponse:
    """Find the chunks that best match a question.

    The question goes through exactly the same model as the documents did. It
    has to -- two texts can only be compared if they were mapped into the same
    space by the same model.

    Notice what we are *not* returning: the text. Only ids, locations and
    scores. The monolith still holds the chunk files, so it fetches the text
    itself.
    """
    try:
        ensure_model()
        vector = model.embed_texts([body.query])[0]
        hits = store.search(body.session_id, vector, body.top_k)
        return RetrieveResponse(hits=[Hit(**hit) for hit in hits])
    except Exception as exc:
        log.exception("retrieve failed")
        raise HTTPException(status_code=500, detail=f"retrieval failed: {exc}") from exc


SERVICE_NAME = "embedding"

app.include_router(router)

# --- pulling jobs ------------------------------------------------------------
#
# The service does two things at once now. It still answers HTTP calls, and it
# also runs a thread that asks the backend for queued work. Both use the same
# model in the same process, which is the point -- a separate program would
# mean a second copy of the weights on the same card.

_poller = None


@app.on_event("startup")
def start_polling() -> None:
    """Begin pulling jobs, if a broker is configured.

    With no BROKER_URL this does nothing and the service behaves exactly as it
    did before -- useful when you want to test it on its own.
    """
    global _poller
    if not (settings.broker_url and settings.broker_token):
        log.info("no broker configured; %s answers HTTP only", SERVICE_NAME)
        return
    client = broker.BrokerClient(settings.broker_url, settings.broker_token)
    _poller = jobs.poller(client)
    _poller.start()
    log.info("pulling %s jobs from %s", jobs.JOB, settings.broker_url)


@app.on_event("shutdown")
def stop_polling() -> None:
    if _poller is not None:
        _poller.stop()

