"""The chat worker: retrieve, prompt, generate, publish, save.

The same five steps as version 1, in a process that is not a web server. What
changed is where the answer goes: it is published to Redis as it becomes
available, and whichever API task holds the browser's connection relays it.

On streaming
------------
The plumbing here is built for token-by-token output — a `publish_token`
helper, a channel per session, an SSE endpoint already relaying frames. The
language model service, which we are leaving exactly as it is for now, returns
a finished string rather than a stream. So today this emits progress events and
then the answer in one piece.

That is a deliberate seam. When the service gains a streaming endpoint, the
change here is to loop over its output calling `publish_token`, and nothing else
in the system has to know.

    python -m backend.workers.chat
"""
import logging
import time

from sqlalchemy import select

from ..shared import clients, events, models
from ..shared.config import get_settings
from ..shared.db import init_db, worker_session
from .base import Worker

log = logging.getLogger(__name__)
settings = get_settings()


SYSTEM_INSTRUCTIONS = """You are answering questions about a specific set of documents.

Use only the numbered context below. If the context does not contain the answer, say so
plainly instead of guessing. Mention which source you used.
"""


def handle(body: dict) -> None:
    session_id = body["session_id"]
    message_id = body["message_id"]
    question = body["question"]

    with worker_session() as db:
        row = db.get(models.Message, message_id)
        if row is None:
            log.warning("no such message %s", message_id)
            return
        if row.status == models.MESSAGE_DONE:
            log.info("message %s is already answered; skipping", message_id)
            return

        row.status = models.MESSAGE_ANSWERING
        db.commit()
        _announce(session_id, row, stage="retrieving")

        try:
            started = time.time()

            # 1. which chunks. The service searches its vector store and
            #    returns keys and scores -- not text.
            hits = clients.retrieve(session_id, question, settings.top_k)

            # 2. what is in them. One indexed query against our own database,
            #    rather than version 1's round trip to storage per file.
            sources = _fetch_sources(db, session_id, hits)
            _announce(session_id, row, stage="generating", sources=len(sources))

            # 3. the prompt, and the model.
            history = events.history(session_id)
            prompt = build_prompt(question, sources, history)
            result = clients.generate(prompt)
            content = result.get("content", "")

            # 4. publish, then persist. The stream is ephemeral; the database
            #    is the record, so a browser that was not connected -- or that
            #    reloaded -- can still recover the answer.
            publish_token(session_id, message_id, content)

            row.content = content
            row.status = models.MESSAGE_DONE
            row.sources = sources
            db.commit()

            _announce(session_id, row, stage="done",
                      usage=result.get("usage"), seconds=round(time.time() - started, 2))

            # 5. only now does this turn enter the conversation window. A
            #    question that produced no answer should not be remembered.
            events.push_turn(session_id, models.ROLE_USER, question)
            events.push_turn(session_id, models.ROLE_ASSISTANT, content)

            log.info("answered %s in %.1fs (%d sources)",
                     message_id, time.time() - started, len(sources))
        except Exception as exc:                 # noqa: BLE001
            db.rollback()
            row = db.get(models.Message, message_id)
            row.status = models.MESSAGE_FAILED
            row.content = f"Something went wrong while answering: {exc}"
            db.commit()
            _announce(session_id, row, stage="failed")
            raise                                 # let the queue retry it


# --- the pieces --------------------------------------------------------------

def _fetch_sources(db, session_id: str, hits: list[dict]) -> list[dict]:
    """Turn retrieval hits into chunks with their text, best first.

    Version 1 grouped hits by file and read a .jsonl out of S3 for each. Here
    the text is a row, so it is one query with an `IN` clause, and the result is
    reordered to match the ranking.
    """
    if not hits:
        return []

    keys = [h["chunk_id"] for h in hits if h.get("chunk_id")]
    if not keys:
        return []

    rows = db.execute(
        select(models.Chunk)
        .where(models.Chunk.session_id == session_id, models.Chunk.chunk_key.in_(keys))
    ).scalars().all()
    by_key = {r.chunk_key: r for r in rows}

    sources = []
    for hit in hits:
        chunk = by_key.get(hit.get("chunk_id"))
        if chunk is None:
            # A vector whose text is missing. Possible if the index outlived the
            # database, or a re-index is in flight. Skip it rather than fail the
            # whole answer.
            log.warning("chunk %s is indexed but not stored", hit.get("chunk_id"))
            continue
        sources.append({
            "chunk_id": chunk.chunk_key,
            "source": chunk.source,
            "section": chunk.section,
            "score": hit.get("score"),
            "text": chunk.text,
        })
    return sources


def build_prompt(question: str, sources: list[dict], history: list[dict]) -> str:
    """Assemble one string for the model.

    Unchanged from version 1, on purpose: this is where answer quality actually
    lives, and it was never the thing that was broken.
    """
    parts = [SYSTEM_INSTRUCTIONS.strip()]

    if sources:
        lines = ["Context:"]
        for number, source in enumerate(sources, start=1):
            label = source.get("source") or "unknown"
            if source.get("section"):
                label = f"{label} - {source['section']}"
            lines.append(f"[{number}] ({label})\n{source.get('text', '')}")
        parts.append("\n\n".join(lines))
    else:
        parts.append("Context: nothing relevant was found in the documents.")

    if history:
        turns = [
            f"{'User' if t['role'] == models.ROLE_USER else 'Assistant'}: {t['content']}"
            for t in history
        ]
        parts.append("Recent conversation:\n" + "\n".join(turns))

    parts.append(f"User: {question}\nAssistant:")
    return "\n\n".join(parts)


def publish_token(session_id: str, message_id: str, text: str) -> None:
    """Send a fragment of an answer to whoever is watching.

    Called once today, because the model service returns a finished string.
    When it can stream, this is called in a loop and nothing else changes.
    """
    events.publish(session_id, {
        "event": events.MESSAGE_TOKEN,
        "message_id": message_id,
        "text": text,
    })


def _announce(session_id: str, row, **extra) -> None:
    events.publish(session_id, {
        "event": events.MESSAGE_STATUS,
        "message_id": row.id,
        "status": row.status,
        **extra,
    })


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db()
    # A shorter heartbeat than ingest: a question should never take minutes, so
    # if one does we want the message back in the queue sooner.
    Worker("chat", settings.chat_queue_url, handle, heartbeat_seconds=30).run()


if __name__ == "__main__":
    main()
