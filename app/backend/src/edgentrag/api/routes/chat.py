"""Accept questions and read the persisted conversation."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from edgentrag.api.dependencies import get_chat_queue, get_db_session
from edgentrag.chat_queue import ChatQueue, ChatQueueUnavailable
from edgentrag.sessions.chat_schemas import ChatAccepted, ChatRequest, MessageResponse
from edgentrag.sessions.models import ChatSession, Message

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
) -> ChatAccepted:
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if session.status != "ready":
        raise HTTPException(status_code=409, detail="session has nothing indexed yet")

    question = Message(
        session_id=session_id, role="user", content=body.content, status="done"
    )
    answer = Message(session_id=session_id, role="assistant", status="pending")
    database_session.add_all([question, answer])
    await database_session.flush()
    try:
        await run_in_threadpool(
            chat_queue.enqueue_message,
            session_id=session_id,
            message_id=answer.id,
            question=body.content,
        )
    except ChatQueueUnavailable as exc:
        await database_session.rollback()
        raise HTTPException(
            status_code=503, detail="chat queue is temporarily unavailable"
        ) from exc
    await database_session.commit()
    return ChatAccepted(message_id=answer.id)


@router.get("/{session_id}/chat", response_model=list[MessageResponse])
async def list_messages(
    session_id: str,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[MessageResponse]:
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
