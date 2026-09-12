"""The broker — how the GPU gets work without holding AWS credentials.

The problem
-----------
Transcription and indexing belong on a queue: they are slow, nobody is waiting
on a connection, and a backlog should be a backlog rather than a wall of failed
requests. But the machine that does that work is a Colab runtime, and it cannot
be given AWS credentials. There is no workload identity on Colab to exchange
for a role, and a long-lived key sitting in Drive is a worse secret than the
one it would replace.

The pattern
-----------
So the GPU never talks to SQS. It talks to **us**, and we talk to SQS on its
behalf. Three endpoints, and nothing else:

    POST /broker/claim       "have you got work?"   -> a job, or 204
    POST /broker/heartbeat   "still going"          -> keeps the job ours
    POST /broker/complete    "done" or "failed"     -> acknowledge, or let it retry

The GPU holds one token. That token grants nothing in AWS — it cannot read a
bucket, delete a queue, or see any other job. Revoking it is a line in `.env`.
Compare that with an access key, which grants whatever its policy says for as
long as it exists.

Two consequences worth understanding
------------------------------------
**The connection is outbound from the GPU.** It asks us for work; we never
push. So the GPU needs no public address for this path at all — no tunnel, no
ephemeral hostname, and none of the hundred-second limit that shaped version 1.

**The GPU still never sees the bucket either.** Every job carries presigned
URLs: one to read its input, one to write its output. Those are the only
storage access it gets, they expire, and they are scoped to a single object.

What is deliberately *not* here
-------------------------------
Retrieval and generation. Somebody is waiting for those, so they stay direct
HTTP calls from the chat worker, with a timeout and a retry — see
`shared/clients.py`. A queue would add latency to the one path that cannot
afford it.
"""
import base64
import binascii
import json
import logging

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from starlette.concurrency import run_in_threadpool

from ...shared import bookkeeping, events, models, queues
from ...shared.config import get_settings
from ...shared.db import worker_session
from ...shared.schemas import ClaimRequest, JobComplete, JobLease, JobOffer

router = APIRouter(prefix="/broker", tags=["broker"])
log = logging.getLogger(__name__)
settings = get_settings()

# Which queue each job name maps to. A name the GPU sends that is not in here
# is rejected, so a typo cannot make it poll something it should not.
QUEUES = {
    "stt": lambda: settings.stt_queue_url,
    "embed": lambda: settings.embed_queue_url,
}

# The same value bootstrap.py put on the queue, so a last attempt is
# recognisable as the last one.
MAX_RECEIVES = settings.queue_max_receives


# --- authentication ----------------------------------------------------------

def require_token(authorization: str = Header(default="")) -> None:
    """One shared token, sent as a bearer header.

    Not sophisticated, and deliberately so: the point of the pattern is that
    this credential is worth almost nothing. It authorises "ask for a job on
    one of two queues" and nothing else.
    """
    if not settings.broker_token:
        raise HTTPException(status_code=503,
                            detail="broker is not configured; set BROKER_TOKEN")
    expected = f"Bearer {settings.broker_token}"
    # Compared in constant time so the comparison itself does not leak the
    # token one character at a time.
    import hmac
    if not hmac.compare_digest(authorization.strip(), expected):
        raise HTTPException(status_code=401, detail="bad or missing broker token")


# --- leases ------------------------------------------------------------------
#
# A lease is how the GPU refers to the job it is holding. It is the queue name
# plus SQS's receipt handle, wrapped up so the GPU treats it as opaque. It
# needs no signature: it is only ever accepted from a caller who already proved
# they hold the token, and it grants nothing except the right to finish or
# extend the job it names.

def _pack(queue: str, receipt_handle: str, attempt: int) -> str:
    raw = json.dumps({"q": queue, "rh": receipt_handle, "n": attempt}).encode()
    return base64.urlsafe_b64encode(raw).decode()


def _unpack(lease: str) -> queues.Message:
    """Recover the message this lease refers to.

    The attempt number rides along because two decisions depend on it: how long
    to wait before retrying, and whether this was the last chance.
    """
    try:
        data = json.loads(base64.urlsafe_b64decode(lease.encode()))
        queue_url = QUEUES[data["q"]]()
    except (KeyError, ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=400, detail="unusable lease") from exc
    return queues.Message(body={}, receipt_handle=data["rh"],
                          receive_count=int(data.get("n", 1)), queue_url=queue_url)


def _backoff(attempt: int) -> int:
    """How long to hide a failed message before offering it again.

    Zero would be wrong. A job that fails in two seconds -- a CUDA
    out-of-memory, say -- would then be reclaimed instantly, fail again, and
    burn all five attempts in about ten seconds. Nothing transient ever gets a
    chance to clear.

    So: thirty seconds, doubling, capped at the visibility timeout.
    """
    return min(30 * (2 ** max(0, attempt - 1)), settings.queue_visibility_seconds)


# --- the three endpoints -----------------------------------------------------

@router.post("/claim", response_model=JobOffer | None,
             dependencies=[Depends(require_token)])
async def claim(body: ClaimRequest, response: Response):
    """Hand out one job, or answer 204 if there is nothing to do.

    This holds the connection for up to twenty seconds, because that is SQS
    long polling: one call that waits for work instead of a tight loop of empty
    ones. The GPU calls it again the moment it returns.

    Holding a request open for twenty seconds is exactly the kind of thing an
    async endpoint does for free and a thread pool does badly — the same
    property that makes the event stream possible.
    """
    if body.job not in QUEUES:
        raise HTTPException(status_code=400, detail=f"unknown job type {body.job!r}")
    queue_url = QUEUES[body.job]()
    if not queue_url:
        raise HTTPException(status_code=503, detail=f"{body.job} queue is not configured")

    messages = await run_in_threadpool(queues.receive, queue_url, 1)
    if not messages:
        response.status_code = 204
        return None

    message = messages[0]
    log.info("handed a %s job to the gpu (attempt %d)", body.job, message.receive_count)
    return JobOffer(
        lease=_pack(body.job, message.receipt_handle, message.receive_count),
        job=body.job,
        attempt=message.receive_count,
        body=message.body,
    )


@router.post("/heartbeat", status_code=204, dependencies=[Depends(require_token)])
async def heartbeat(body: JobLease) -> Response:
    """Keep a long job from being handed to somebody else.

    Without this, a message whose visibility timeout expires goes back on the
    queue while the GPU is still working on it — and the same video gets
    transcribed twice.
    """
    message = _unpack(body.lease)
    await run_in_threadpool(queues.extend, message, settings.queue_visibility_seconds)
    return Response(status_code=204)


@router.post("/complete", status_code=204, dependencies=[Depends(require_token)])
async def complete(body: JobComplete) -> Response:
    """The GPU reports the outcome.

    On success we acknowledge the message and move the file to its next stage.
    On failure we do *not* acknowledge: SQS makes the message visible again and
    the dead-letter queue catches it if it keeps failing. That is the whole
    reason the work is on a queue.
    """
    message = _unpack(body.lease)

    if not body.ok:
        attempt = message.receive_count
        last = attempt >= MAX_RECEIVES
        log.warning("gpu reported failure on %s (attempt %d/%d): %s",
                    body.job, attempt, MAX_RECEIVES, body.error)

        if last:
            # Out of attempts. SQS will move the message to the dead-letter
            # queue, and if we stop here the file stays "processing" for ever
            # and its session never closes -- the browser would spin with no
            # error and nothing to explain it. So settle it now.
            await run_in_threadpool(_give_up, body)
            await run_in_threadpool(queues.delete, message)
            return Response(status_code=204)

        await run_in_threadpool(_record_failure, body)
        await run_in_threadpool(queues.extend, message, _backoff(attempt))
        return Response(status_code=204)

    await run_in_threadpool(_advance, body)
    await run_in_threadpool(queues.delete, message)
    return Response(status_code=204)


# --- what a completed job means ----------------------------------------------

def _advance(body: JobComplete) -> None:
    """Move the file to whatever comes after the job that just finished."""
    if body.job == "stt":
        # The transcript is in the bucket. Put the file back on the ingest
        # queue so a worker -- not the GPU -- does the chunking and storing.
        queues.send(settings.ingest_queue_url, {
            "session_id": body.session_id,
            "file_id": body.file_id,
            "stage": "transcribed",
        })
        log.info("transcription done for %s; queued for chunking", body.file_id)
        return

    if body.job == "embed":
        # Indexing was the last step. The file is finished.
        with worker_session() as db:
            row = db.get(models.File, body.file_id)
            if row is None:
                log.warning("embed finished for unknown file %s", body.file_id)
                return
            row.status = models.FILE_DONE
            row.chunk_count = body.indexed or row.chunk_count or 0
            row.error = None
            db.commit()
            bookkeeping.announce_file(row.session_id, row)
            bookkeeping.finish_file(db, row.session_id)
            log.info("%s indexed: %d chunks", row.filename, row.chunk_count)


def _give_up(body: JobComplete) -> None:
    """The last attempt failed. Mark the file failed and settle the session."""
    with worker_session() as db:
        bookkeeping.mark_failed(
            db, body.file_id,
            f"{body.job} failed after {MAX_RECEIVES} attempts: {body.error}")
    log.error("%s gave up on file %s", body.job, body.file_id)


def _record_failure(body: JobComplete) -> None:
    """Show the failure while it is still being retried.

    The file is not marked failed here -- the queue may yet succeed on a later
    attempt. This only publishes the error so it is visible on screen instead
    of the file appearing to hang.
    """
    with worker_session() as db:
        row = db.get(models.File, body.file_id)
        if row is None:
            return
        events.publish(row.session_id, {
            "event": events.FILE_PROGRESS,
            "file_id": row.id,
            "filename": row.filename,
            "status": row.status,
            "chunk_count": row.chunk_count or 0,
            "error": f"{body.job} attempt failed: {body.error}",
        })
