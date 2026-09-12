"""HTTP routes for creating and managing sessions."""

from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.api.dependencies import get_db_session
from edgentrag.sessions.models import ChatSession
from edgentrag.sessions.schemas import SessionResponse

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
) -> SessionResponse:
    """Create an empty session that can receive uploaded documents later."""
    session = ChatSession()
    database_session.add(session)
    await database_session.commit()
    await database_session.refresh(session)

    return SessionResponse(
        session_id=session.id,
        status=session.status,
        created_at=session.created_at,
    )
