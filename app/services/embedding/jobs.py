"""Indexing as a pulled job. Retrieval stays a call.

This is the split that matters, and it is worth stating plainly:

    indexing    bulk, slow, nobody waiting     -> queued, pulled through the broker
    retrieval   one question, a person waiting -> a direct call to /retrieve

Same model, same vector store, same process. The difference is only in how the
work arrives, and the reason is only ever "is somebody sitting there watching a
spinner?"

A queue in front of a single GPU is not a scaling trick — there is one card and
it does one thing at a time either way. It is a way of making one slow consumer
*safe*: a thousand uploads become a backlog instead of a thousand failed
connections, and a runtime that dies mid-job loses nothing.
"""
import logging

import broker
from broker import iter_jsonl
from . import model, store
from .config import get_settings

log = logging.getLogger("embedding.jobs")
settings = get_settings()

JOB = "embed"


def handle(body: dict) -> dict:
    """Index every chunk in the file this job points at.

    The chunks arrive as a `.jsonl` file behind a signed link rather than
    inside the message, because SQS caps a message at 256 KB and a real
    document is far past that. We stream it, so the size of the document does
    not decide the size of this process.

    Idempotent: Chroma is given deterministic ids, so re-running a redelivered
    job overwrites the same vectors rather than adding duplicates.
    """
    session_id = body["session_id"]
    model.load_model()                      # a no-op after the first job

    indexed = 0
    batch: list[dict] = []
    for record in iter_jsonl(body["chunks_url"]):
        batch.append(record)
        if len(batch) >= settings.batch_size:
            indexed += _index(session_id, batch)
            batch = []
    if batch:
        indexed += _index(session_id, batch)

    log.info("indexed %d chunks for %s", indexed, body.get("filename", session_id))
    return {"indexed": indexed}


def _index(session_id: str, batch: list[dict]) -> int:
    vectors = model.embed_texts([c["text"] for c in batch])
    store.add_chunks(session_id, batch, vectors)
    return len(batch)


def poller(client: broker.BrokerClient) -> broker.JobPoller:
    return broker.JobPoller(client, JOB, handle, name="embed-jobs")
