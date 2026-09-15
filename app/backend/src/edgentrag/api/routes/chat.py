"""Accept questions and read the persisted conversation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.api.dependencies import get_chat_queue, get_db_session, get_events
from edgentrag.chat_queue import ChatQueue, ChatQueueUnavailable
from edgentrag.sessions.chat import enqueue_turn
from edgentrag.sessions.chat_schemas import ChatAccepted, ChatRequest, MessageResponse
from edgentrag.sessions.models import ChatSession, Message
from edgentrag.shared.auth import current_user, owns
from edgentrag.shared.events import RedisEvents

router = APIRouter(prefix="/sessions", tags=["chat"])


@router.post(
    "/{session_id}/chat",
    response_model=ChatAccepted,
    status_code=status.HTTP_202_ACCEPTED,
)
async def post_message(
    session_id: str,
    body: ChatRequest,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    chat_queue: Annotated[ChatQueue, Depends(get_chat_queue)],
    user_id: Annotated[str, Depends(current_user)],
    events: Annotated[RedisEvents, Depends(get_events)],
) -> ChatAccepted:
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="session has nothing indexed yet")

    try:
        message_id = await enqueue_turn(
            database_session,
            chat_queue,
            session_id=session_id,
            content=body.content,
        )
    except ChatQueueUnavailable as exc:
        raise HTTPException(
            status_code=503, detail="chat queue is temporarily unavailable"
        ) from exc
    events.append_history(session_id, "user", body.content)
    events.publish(session_id, "chat.accepted", {"message_id": message_id})
    return ChatAccepted(message_id=message_id)


@router.get("/{session_id}/chat", response_model=list[MessageResponse])
async def list_messages(
    session_id: str,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: Annotated[str, Depends(current_user)],
) -> list[MessageResponse]:
    session = await database_session.get(ChatSession, session_id)
    if session is None or not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")
    rows = (
        (
            await database_session.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [MessageResponse.model_validate(row, from_attributes=True) for row in rows]
