"""Handing out upload links, and putting work on the queue.

Two handlers, both measured in milliseconds. No file bytes pass through here:
the browser is given a presigned URL and uploads straight to S3.

The difference from version 1 is the last line of `register_files`. Version 1
called `background.add_task(ingest.run, session_id)`, which ran the whole
ingestion inside this web server on a shared pool of forty threads. This puts
**one message per file** on a queue and returns. The work then happens in a
process whose death costs nothing, and fifty files are processed by fifty
workers rather than by one loop.
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from ...shared import models, queues, storage
from ...shared.config import get_settings
from ...shared.convert import kind_for_filename
from ...shared.db import get_session
from ...shared.schemas import (
    RegisterRequest,
    RegisterResponse,
    UploadRequest,
    UploadResponse,
    UploadTarget,
)

router = APIRouter(prefix="/sessions", tags=["uploads"])
log = logging.getLogger(__name__)
settings = get_settings()


@router.post("/{session_id}/uploads", response_model=UploadResponse)
async def create_uploads(session_id: str, body: UploadRequest,
                         db: AsyncSession = Depends(get_session)) -> UploadResponse:
    """Create a File row per upload and hand back one presigned URL each."""
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session")

    for spec in body.files:
        if spec.size and spec.size > settings.max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"{spec.filename} is larger than the "
                       f"{settings.max_upload_bytes // (1024 ** 3)} GiB limit",
            )

    targets: list[UploadTarget] = []
    rows: list[models.File] = []

    for spec in body.files:
        kind = kind_for_filename(spec.filename)
        if kind is None:
            raise HTTPException(status_code=400,
                                detail=f"unsupported file type: {spec.filename}")

        file_id = models.new_id()
        key = storage.raw_key(session_id, file_id, spec.filename)
        rows.append(models.File(
            id=file_id, session_id=session_id, filename=spec.filename,
            kind=kind, raw_key=key, status=models.FILE_PENDING,
            size_bytes=spec.size or 0,
        ))
        # boto3 is synchronous; signing is pure computation but it is not ours
        # to block the loop with.
        url = await run_in_threadpool(storage.presign_put, key, spec.content_type)
        targets.append(UploadTarget(file_id=file_id, filename=spec.filename,
                                    kind=kind, key=key, upload_url=url))

    db.add_all(rows)
    await db.commit()
    return UploadResponse(targets=targets)


@router.post("/{session_id}/files/register", response_model=RegisterResponse)
async def register_files(session_id: str, body: RegisterRequest,
                         db: AsyncSession = Depends(get_session)) -> RegisterResponse:
    """The uploads have landed. Put one message per file on the ingest queue."""
    session = await db.get(models.Session, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="no such session")

    wanted = {f.file_id for f in body.files}
    rows = (await db.execute(
        select(models.File)
        .where(models.File.session_id == session_id, models.File.id.in_(wanted))
    )).scalars().all()

    if not rows:
        raise HTTPException(status_code=400, detail="none of those files belong to this session")

    session.status = models.SESSION_PROCESSING
    session.files_total = len(rows)
    session.files_done = 0
    session.error = None
    await db.commit()

    # One message per file. Fifty files become fifty parallel jobs across the
    # worker fleet rather than one sequential loop.
    messages = [
        {
            "session_id": session_id,
            "file_id": row.id,
            "filename": row.filename,
            "kind": row.kind,
            "raw_key": row.raw_key,
        }
        for row in rows
    ]
    await run_in_threadpool(queues.send_many, settings.ingest_queue_url, messages)
    log.info("queued %d files for session %s", len(messages), session_id)

    return RegisterResponse(session_id=session_id,
                            status=models.SESSION_PROCESSING,
                            files=len(rows))
