"""The ingest worker: prepare work for the GPU, and store what comes back.

Version 1 ran this inside the web server, in a loop over every file in a
session, on the same forty threads that served requests. Here it is a separate
process consuming a queue.

What this worker does *not* do any more
---------------------------------------
It does not call the GPU and wait. Transcription and indexing are queued jobs
that the GPU pulls for itself through the broker, so this worker never holds a
connection open for twenty minutes. Its own jobs are all measured in seconds.

That turns ingestion into a small state machine, driven entirely by messages:

    stage "start"        a video     -> put an stt job on the queue, stop
                         a document  -> convert, chunk, store, queue an embed job, stop
    stage "transcribed"  (the broker sends this when the GPU finishes)
                                     -> chunk the transcript, store, queue an embed job, stop

    the file is marked done by the broker, when the embed job completes.

Every arrow ends in "stop". No step waits for the next one, which is why a
twenty-minute transcription costs this process nothing at all.

Three properties carried over from before
-----------------------------------------
**It streams.** Downloaded to disk, converted to a file on disk, chunked with
an iterator. Nothing holds a document in memory, so a 500 MB text file costs
the same as a 5 MB one — version 1 needed sixteen to twenty-six times the
file's size and died somewhere past 100 MB.

**It is idempotent.** Chunk keys are deterministic and written with an upsert,
so a redelivered message updates rows rather than duplicating them.
At-least-once delivery makes this a requirement, not a nicety.

**Nobody is waiting.** There is no browser connection behind any of this.

    python -m backend.workers.ingest
"""
import json
import logging
import os
import tempfile

from sqlalchemy.dialects.sqlite import insert

from ..shared import bookkeeping, convert, models, queues, storage
from ..shared.chunking import batched, iter_file_chunks
from ..shared.config import get_settings
from ..shared.db import init_db, worker_session
from .base import Worker

log = logging.getLogger(__name__)
settings = get_settings()


# --- the job -----------------------------------------------------------------

def handle(body: dict) -> None:
    session_id = body["session_id"]
    file_id = body["file_id"]
    stage = body.get("stage", "start")

    with worker_session() as db:
        row = db.get(models.File, file_id)
        if row is None:
            log.warning("no such file %s; nothing to do", file_id)
            return
        if row.status == models.FILE_DONE:
            # A redelivery of something already finished. Acknowledge and move on.
            log.info("%s is already done; skipping", row.filename)
            return

        row.status = models.FILE_PROCESSING
        row.error = None
        db.commit()
        bookkeeping.announce_file(session_id, row)

        try:
            if stage == "transcribed":
                _chunk_transcript(db, row)
            elif row.kind == convert.KIND_VIDEO:
                _request_transcription(row)
            else:
                _prepare_document(db, row)
        except Exception as exc:                 # noqa: BLE001
            db.rollback()
            bookkeeping.mark_failed(db, file_id, str(exc))
            raise                                 # let the queue retry it


# --- stage: a video needs the GPU before anything else can happen -------------

def _request_transcription(row) -> None:
    """Put an stt job on the queue and stop.

    Two signed URLs go in the message and nothing else of substance: one that
    lets the GPU read the video out of the bucket, one that lets it write the
    transcript back. The GPU learns no bucket name and holds no credentials --
    it is handed exactly two doors, both of which expire.

    The expiry is deliberately long. It has to outlive the *backlog*: a video
    queued behind two hours of other work still needs a working link when the
    GPU finally reaches it.
    """
    expiry = settings.job_url_expiry_seconds
    queues.send(settings.stt_queue_url, {
        "session_id": row.session_id,
        "file_id": row.id,
        "filename": row.filename,
        "media_url": storage.presign_get(row.raw_key, expiry),
        "result_url": storage.presign_put(
            storage.transcript_key(row.session_id, row.id),
            "application/json", expiry),
    })
    log.info("%s: queued for transcription", row.filename)


def _chunk_transcript(db, row) -> None:
    """The GPU has written a transcript. Turn it into chunks and index them.

    The GPU already cut the transcript into chunk-shaped pieces, in the same
    shape the text chunker produces -- once it is text, nothing downstream can
    tell where it came from.
    """
    payload = json.loads(storage.get_text(storage.transcript_key(row.session_id, row.id)))

    storage.put_text(storage.text_key(row.session_id, row.id),
                     payload.get("transcript", ""))
    row.text_key = storage.text_key(row.session_id, row.id)
    db.commit()

    chunks = [
        {
            "chunk_key": c.get("chunk_id") or f"{row.id}:{i:04d}",
            "session_id": row.session_id,
            "file_id": row.id,
            "source": row.filename,
            "kind": "video",
            "section": c.get("section"),
            "ordinal": c.get("order", i),
            "text": c.get("text", ""),
        }
        for i, c in enumerate(payload.get("chunks", []))
    ]
    _store_and_queue(db, row, iter(chunks))


# --- stage: a document can be prepared without the GPU ------------------------

def _prepare_document(db, row) -> None:
    """Download, convert, chunk, store — all through files on disk."""
    with tempfile.TemporaryDirectory() as tmp:
        raw_path = os.path.join(tmp, os.path.basename(row.raw_key) or "upload")
        text_path = os.path.join(tmp, "extracted.txt")

        storage.download_to(row.raw_key, raw_path)
        convert.to_text_file(raw_path, row.kind, text_path)

        # Keep the extracted text so a human can see what was actually read.
        # Nothing downstream needs it; it is there for debugging retrieval.
        #
        # Uploaded from disk rather than read into a string: reading it back
        # would put the whole document in memory and undo the streaming that
        # the download, the conversion and the chunker all maintain.
        key = storage.text_key(row.session_id, row.id)
        storage.upload_file(key, text_path)
        row.text_key = key
        db.commit()

        chunks = iter_file_chunks(
            text_path,
            session_id=row.session_id, file_id=row.id,
            source=row.filename, kind=row.kind,
        )
        _store_and_queue(db, row, chunks)


# --- storing, and handing indexing to the GPU --------------------------------

def _store_and_queue(db, row, chunks) -> None:
    """Write the chunks to the database and to storage, then queue one embed job.

    The database first, deliberately. If indexing never happens, the text is
    still stored and the upsert makes a second attempt harmless. The reverse
    order could leave a vector pointing at text that was never written — which
    is exactly the silent failure version 1 could produce.

    The chunks also go to storage as a `.jsonl` file, because that is what the
    GPU will read. They cannot travel in the message itself: SQS caps a message
    at 256 KB and a large document is far past that. So the message carries a
    signed link to the file — the same trick the video uses, for the same
    reason.
    """
    total = 0
    chunks_path = None
    try:
        # Written to a temporary file first so a failure part-way through never
        # leaves a half-written chunk file in the bucket for the GPU to read.
        handle_, chunks_path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(handle_, "w", encoding="utf-8") as out:
            for batch in batched(chunks, settings.embed_batch_size):
                db.execute(
                    insert(models.Chunk)
                    .values([
                        {
                            "id": models.new_id(),
                            "session_id": c["session_id"],
                            "file_id": c["file_id"],
                            "chunk_key": c["chunk_key"],
                            "source": c["source"],
                            "section": c.get("section"),
                            "kind": c["kind"],
                            "ordinal": c["ordinal"],
                            "text": c["text"],
                        }
                        for c in batch
                    ])
                    .on_conflict_do_update(
                        # By column, not by constraint name: SQLite's ON CONFLICT
                        # targets an index, and naming the columns works on Postgres
                        # too, so this line survives the move to a real database.
                        index_elements=["session_id", "chunk_key"],
                        set_={"text": insert(models.Chunk).excluded.text,
                              "section": insert(models.Chunk).excluded.section,
                              "ordinal": insert(models.Chunk).excluded.ordinal},
                    )
                )
                db.commit()

                for c in batch:
                    # Exactly the fields version 1's /embed expects, and no
                    # more. The GPU does not need our ordinals or file ids.
                    out.write(json.dumps({
                        "chunk_id": c["chunk_key"],
                        "text": c["text"],
                        "source": c.get("source") or "",
                        "section": c.get("section") or "",
                        "chunks_key": "",
                    }) + "\n")
                total += len(batch)

        row.chunk_count = total
        db.commit()

        if total == 0:
            # Nothing to index. Finish here rather than queueing an empty job.
            log.warning("%s produced no chunks; nothing to index", row.filename)
            row.status = models.FILE_DONE
            db.commit()
            bookkeeping.announce_file(row.session_id, row)
            bookkeeping.finish_file(db, row.session_id)
            return

        key = storage.chunks_key(row.session_id, row.id)
        storage.upload_file(key, chunks_path, "application/x-ndjson")
    finally:
        if chunks_path and os.path.exists(chunks_path):
            os.unlink(chunks_path)

    queues.send(settings.embed_queue_url, {
        "session_id": row.session_id,
        "file_id": row.id,
        "filename": row.filename,
        "count": total,
        "chunks_url": storage.presign_get(key, settings.job_url_expiry_seconds),
    })
    log.info("%s: %d chunks stored, queued for indexing", row.filename, total)


def main() -> None:
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
        datefmt="%H:%M:%S",
    )
    init_db()
    Worker("ingest", settings.ingest_queue_url, handle).run()


if __name__ == "__main__":
    main()
