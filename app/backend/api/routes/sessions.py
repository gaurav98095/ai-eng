"""Starting a session, and reporting how it is going."""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared import models
from ...shared.db import get_session
from ...shared.schemas import FileStatus, SessionOut, SessionStatus

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.post("", response_model=SessionOut, status_code=201)
async def create_session(db: AsyncSession = Depends(get_session)) -> SessionOut:
    session = models.Session(status=models.SESSION_CREATED)
    db.add(session)
    await db.commit()
    return SessionOut(session_id=session.id)


@router.get("/{session_id}/status", response_model=SessionStatus)
async def get_status(session_id: str,
                     db: AsyncSession = Depends(get_session)) -> SessionStatus:
    """How far along is everything.

    Still here, and still polled by browsers that cannot use the event stream —
    but it is no longer the primary way progress travels. Two indexed queries,
    no work.
    """
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session")

    rows = (await db.execute(
        select(models.File)
        .where(models.File.session_id == session_id)
        .order_by(models.File.created_at)
    )).scalars().all()

    return SessionStatus(
        session_id=session.id,
        status=session.status,
        error=session.error,
        files_total=session.files_total,
        files_done=session.files_done,
        files=[
            FileStatus(
                file_id=f.id,
                filename=f.filename,
                kind=f.kind,
                status=f.status,
                chunk_count=f.chunk_count or 0,
                error=f.error,
            )
            for f in rows
        ],
    )
