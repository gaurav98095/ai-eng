"""Serialize file lifecycle writes before deriving the parent session state."""

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.sessions.models import ChatSession


async def lock_session(db: AsyncSession, session_id: str) -> None:
    """Hold a write lock until commit/rollback, including on SQLite.

    Call before reading lifecycle state or modifying files. Every writer takes
    the same parent lock, so simultaneous file completions cannot calculate a
    session status from each other's uncommitted state. No model I/O belongs
    inside this transaction.
    """
    await db.execute(
        update(ChatSession)
        .where(ChatSession.id == session_id)
        .values(status=ChatSession.status)
        .execution_options(synchronize_session=False)
    )
