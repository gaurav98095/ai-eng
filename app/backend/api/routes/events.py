"""The event stream — what replaces polling.

A browser opens one connection per session and the server writes to it when
there is something to say. Version 1 asked "is it done yet?" every two seconds;
with three hundred users that is a hundred and fifty requests a second carrying
no information most of the time, and the user still waits up to two seconds
after the answer already exists.

How it works across a fleet
---------------------------
The worker doing the job and the API task holding this connection are different
processes and always will be. So the worker publishes to a Redis channel named
for the session, and whichever API task holds the connection is subscribed and
relays what arrives. Without that indirection, streaming only works when there
is exactly one API instance — which is to say, it does not work.

Why this endpoint must be async
-------------------------------
It holds a connection open for as long as the browser is on the page, doing
nothing almost all of that time. On an event loop, hundreds of those cost
almost nothing. On version 1's pool of forty threads it would be impossible —
four hundred idle users would take every thread and the API would stop
answering anything.

Why the heartbeat
-----------------
Getting response headers out immediately clears the proxy's time-to-first-byte
limit, but an idle stream still gets closed by intermediaries. A comment line
every fifteen seconds keeps it open. Early headers turn "must finish within a
hundred seconds" into "must not go quiet" — much easier, still a rule.
"""
import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import StreamingResponse

from ...shared import events
from ...shared.config import get_settings

router = APIRouter(prefix="/sessions", tags=["events"])
log = logging.getLogger(__name__)
settings = get_settings()


def _frame(event: str, data: dict) -> str:
    """One server-sent-event frame. The blank line is what ends it."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _stream(session_id: str, request: Request) -> AsyncIterator[str]:
    # Yield before touching Redis. Two reasons: response headers reach the
    # browser immediately, which clears any proxy's time-to-first-byte limit;
    # and if Redis is briefly unreachable the client gets a stream that says so
    # rather than a 500 from a generator that raised before its first yield.
    yield _frame("open", {"session_id": session_id})

    client = None
    pubsub = None
    try:
        client = events.async_client()
        pubsub = client.pubsub()
        await pubsub.subscribe(events.channel(session_id))

        while True:
            if await request.is_disconnected():
                break

            message = await pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=settings.sse_heartbeat_seconds,
            )

            if message is None:
                yield ": keepalive\n\n"          # a comment; the browser ignores it
                continue

            try:
                payload = json.loads(message["data"])
            except (TypeError, ValueError):
                continue

            yield _frame(payload.get("event", "message"), payload)
    except asyncio.CancelledError:
        raise
    except Exception:                            # noqa: BLE001
        log.exception("event stream failed for %s", session_id)
    finally:
        try:
            if pubsub is not None:
                await pubsub.unsubscribe(events.channel(session_id))
                await pubsub.aclose()
            if client is not None:
                await client.aclose()
        except Exception:                        # noqa: BLE001
            pass


@router.get("/{session_id}/events")
async def stream_events(session_id: str, request: Request) -> StreamingResponse:
    """Subscribe to everything happening in one session."""
    return StreamingResponse(
        _stream(session_id, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers responses by default, which would hold the stream
            # until it was complete and defeat the entire mechanism.
            "X-Accel-Buffering": "no",
        },
    )
