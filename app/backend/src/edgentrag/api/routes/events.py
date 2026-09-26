"""Redis-backed session events for browser EventSource clients."""

import asyncio
import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.api.dependencies import get_db_session, get_events
from edgentrag.sessions.models import ChatSession
from edgentrag.shared.auth import current_user, owns
from edgentrag.shared.events import RedisEvents, subscribe

router = APIRouter(prefix="/sessions", tags=["events"])


@router.post("/{session_id}/events/ticket")
async def mint_ticket(
    session_id: str,
    request: Request,
    db: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: Annotated[str, Depends(current_user)],
    events: Annotated[RedisEvents, Depends(get_events)],
):
    session = await db.get(ChatSession, session_id)
    if session is None or not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")
    ticket = secrets.token_urlsafe(32)
    await events.mint_ticket(
        ticket,
        f"{user_id}:{session_id}",
        request.app.state.settings.sse_ticket_seconds,
    )
    return {
        "ticket": ticket,
        "expires_in": request.app.state.settings.sse_ticket_seconds,
    }


@router.get("/{session_id}/events")
async def stream_events(
    session_id: str,
    ticket: str = Query(...),
    events: RedisEvents = Depends(get_events),
):
    value = await events.consume_ticket(ticket)
    if value != f"local-dev:{session_id}" and (
        not value or not value.endswith(f":{session_id}")
    ):
        raise HTTPException(status_code=401, detail="invalid or expired event ticket")
    async def body():
        try:
            async for event in subscribe(events.url, session_id, tls=events.tls):
                yield f"data: {json.dumps(event, separators=(',', ':'))}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
