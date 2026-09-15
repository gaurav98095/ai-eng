"""Persist a chat turn before publishing its reference to a queue."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from edgentrag.chat_queue import ChatQueue, ChatQueueUnavailable
from edgentrag.sessions.models import Message


async def enqueue_turn(
    db: AsyncSession, queue: ChatQueue, *, session_id: str, content: str
) -> str:
    question = Message(
        session_id=session_id, role="user", content=content, status="done"
    )
    answer = Message(session_id=session_id, role="assistant", status="pending")
    db.add_all([question, answer])
    # A consumer must be able to read these rows as soon as SQS accepts the job.
    await db.commit()
    try:
        await run_in_threadpool(
            queue.enqueue_message,
            session_id=session_id,
            message_id=answer.id,
            question=content,
        )
    except ChatQueueUnavailable:
        # Delivery can be ambiguous after a network failure. Do not overwrite a
        # consumer that has already claimed or completed this answer.
        await db.execute(
            update(Message)
            .where(Message.id == answer.id, Message.status == "pending")
            .values(status="failed")
        )
        await db.commit()
        raise
    return answer.id
