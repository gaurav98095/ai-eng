"""HTTP routes for creating and managing sessions."""

from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from edgentrag.api.dependencies import (
    get_db_session,
    get_ingestion_queue,
    get_object_storage,
    get_settings,
    get_stt_queue,
)
from edgentrag.core.config import Settings
from edgentrag.ingestion.queue import IngestionQueue, QueueUnavailable
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.sessions.schemas import (
    SessionDetailResponse,
    SessionFileResponse,
    SessionResponse,
)
from edgentrag.sessions.state import lock_session
from edgentrag.sessions.upload_schemas import (
    UploadConfirmationResponse,
    UploadRequest,
    UploadResponse,
    UploadTarget,
)
from edgentrag.sessions.uploads import (
    expected_content_type,
    is_supported_filename,
    upload_kind,
)
from edgentrag.shared.auth import current_user, owns
from edgentrag.shared.queues import QueueError
from edgentrag.storage.s3 import (
    ObjectNotFound,
    ObjectStorage,
    StorageUnavailable,
)
from edgentrag.stt.queue import STTQueue

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session(
    session_id: str,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: Annotated[str, Depends(current_user)],
) -> SessionDetailResponse:
    """Return lifecycle state for a session and its uploaded files."""
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")

    rows = await database_session.scalars(
        select(SessionFile)
        .where(SessionFile.session_id == session_id)
        .order_by(SessionFile.created_at, SessionFile.id)
    )
    return SessionDetailResponse(
        session_id=session.id,
        status=session.status,
        created_at=session.created_at,
        files=[
            SessionFileResponse(
                file_id=row.id,
                filename=row.filename,
                content_type=row.content_type,
                kind=row.kind,
                size_bytes=row.size_bytes,
                status=row.status,
            )
            for row in rows
        ],
    )


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    user_id: Annotated[str, Depends(current_user)],
) -> SessionResponse:
    """Create an empty session that can receive uploaded documents later."""
    session = ChatSession(owner_id=user_id)
    database_session.add(session)
    await database_session.commit()
    await database_session.refresh(session)

    return SessionResponse(
        session_id=session.id,
        status=session.status,
        created_at=session.created_at,
    )


@router.post(
    "/{session_id}/uploads",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_targets(
    session_id: str,
    body: UploadRequest,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: Annotated[str, Depends(current_user)],
) -> UploadResponse:
    """Validate file metadata and return direct-to-S3 upload links."""
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="session not found")

    for file_spec in body.files:
        if not is_supported_filename(file_spec.filename):
            raise HTTPException(
                status_code=415,
                detail=f"unsupported file type: {file_spec.filename}",
            )
        max_size = (
            settings.max_audio_upload_bytes
            if upload_kind(file_spec.filename) == "audio"
            else settings.max_upload_bytes
        )
        if file_spec.size_bytes > max_size:
            raise HTTPException(
                status_code=413,
                detail=f"{file_spec.filename} exceeds the upload size limit",
            )
        if file_spec.content_type != expected_content_type(file_spec.filename):
            raise HTTPException(
                status_code=415,
                detail="content type does not match the file extension",
            )

    targets: list[UploadTarget] = []
    rows: list[SessionFile] = []

    try:
        for file_spec in body.files:
            file_id = str(uuid4())
            object_key = f"uploads/{session_id}/{file_id}"
            upload_url = await run_in_threadpool(
                storage.create_upload_url,
                key=object_key,
                content_type=file_spec.content_type,
                expires_in=settings.upload_url_ttl_seconds,
            )
            rows.append(
                SessionFile(
                    id=file_id,
                    session_id=session_id,
                    filename=file_spec.filename,
                    content_type=file_spec.content_type,
                    size_bytes=file_spec.size_bytes,
                    object_key=object_key,
                    kind=upload_kind(file_spec.filename),
                )
            )
            targets.append(
                UploadTarget(
                    file_id=file_id,
                    filename=file_spec.filename,
                    content_type=file_spec.content_type,
                    upload_url=upload_url,
                    expires_in=settings.upload_url_ttl_seconds,
                )
            )
    except StorageUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="file storage is temporarily unavailable",
        ) from exc

    # End the read transaction before acquiring the shared lifecycle write lock.
    await database_session.rollback()
    await lock_session(database_session, session_id)
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    session.status = "processing"
    database_session.add_all(rows)
    await database_session.commit()
    return UploadResponse(session_id=session_id, targets=targets)


@router.post(
    "/{session_id}/uploads/{file_id}/complete",
    response_model=UploadConfirmationResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def complete_upload(
    session_id: str,
    file_id: str,
    database_session: Annotated[AsyncSession, Depends(get_db_session)],
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    ingestion_queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
    stt_queue: Annotated[STTQueue, Depends(get_stt_queue)],
    settings: Annotated[Settings, Depends(get_settings)],
    user_id: Annotated[str, Depends(current_user)],
) -> UploadConfirmationResponse:
    """Verify stored bytes and enqueue the file for asynchronous processing."""
    await lock_session(database_session, session_id)
    file_record = await database_session.get(SessionFile, file_id)
    if file_record is None or file_record.session_id != session_id:
        raise HTTPException(status_code=404, detail="uploaded file not found")
    session = await database_session.get(ChatSession, session_id)
    if session is None or not owns(session.owner_id, user_id):
        raise HTTPException(status_code=404, detail="uploaded file not found")

    if file_record.status in {"uploaded", "processing", "ready", "failed"}:
        return UploadConfirmationResponse(
            session_id=session_id,
            file_id=file_id,
            status=file_record.status,
            ingestion_job_enqueued=True,
        )
    if file_record.status != "awaiting_upload":
        raise HTTPException(
            status_code=409,
            detail=f"file cannot be confirmed from status {file_record.status}",
        )

    try:
        metadata = await run_in_threadpool(
            storage.get_object_metadata,
            key=file_record.object_key,
        )
    except ObjectNotFound as exc:
        raise HTTPException(
            status_code=409,
            detail="uploaded object was not found; upload the file before confirming",
        ) from exc
    except StorageUnavailable as exc:
        raise HTTPException(
            status_code=503,
            detail="file storage is temporarily unavailable",
        ) from exc

    if metadata.size_bytes != file_record.size_bytes:
        raise HTTPException(
            status_code=422,
            detail="uploaded file size does not match the declared size",
        )
    if metadata.content_type != file_record.content_type:
        raise HTTPException(
            status_code=422,
            detail="uploaded file content type does not match the declared type",
        )

    if file_record.kind == "audio" and settings.stt_service_url is None:
        raise HTTPException(
            status_code=503,
            detail="audio transcription is not configured",
        )

    queue = stt_queue if file_record.kind == "audio" else ingestion_queue
    try:
        await run_in_threadpool(
            queue.enqueue_file,
            session_id=session_id,
            file_id=file_id,
        )
    except (QueueUnavailable, QueueError) as exc:
        raise HTTPException(
            status_code=503,
            detail="processing queue is temporarily unavailable",
        ) from exc

    file_record.status = "uploaded"
    session = await database_session.get(ChatSession, session_id)
    if session is not None:
        session.status = "processing"
    await database_session.commit()

    return UploadConfirmationResponse(
        session_id=session_id,
        file_id=file_id,
        status=file_record.status,
        ingestion_job_enqueued=True,
    )
