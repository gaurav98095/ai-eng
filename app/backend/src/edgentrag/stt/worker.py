"""Durable queue worker that turns media uploads into searchable transcripts."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete

from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, HttpEmbeddingClient
from edgentrag.ingestion.extraction import chunk_text
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.ingestion.worker import _embed_chunks, _update_session_status
from edgentrag.sessions.models import SessionFile
from edgentrag.sessions.state import lock_session
from edgentrag.shared.queues import Message, QueueError, SQSQueue
from edgentrag.storage.s3 import ObjectStorage, ObjectTooLarge, S3ObjectStorage
from edgentrag.stt.client import (
    HttpSTTClient,
    STTProvider,
    STTServiceUnavailable,
    STTTranscriptionRejected,
)

logger = logging.getLogger(__name__)


class STTJob(BaseModel):
    """Versioned contract containing identifiers, never media bytes."""

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=36)
    file_id: str = Field(min_length=1, max_length=36)


class InvalidSTTJob(Exception):
    """Poison job that can be acknowledged and discarded."""


class RetryableSTTJob(Exception):
    """Valid job that must remain visible for an at-least-once retry."""


async def _mark_failed(database: Database, job: STTJob, file_id: str) -> None:
    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        file_record = await db.get(SessionFile, file_id)
        if file_record is None or file_record.status in {"ready", "failed"}:
            return
        file_record.status = "failed"
        await _update_session_status(db, session_id=job.session_id)
        await db.commit()


async def process_message(
    database: Database,
    storage: ObjectStorage,
    provider: STTProvider,
    message: Message,
    *,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_queue: SQSQueue | None = None,
) -> None:
    """Transcribe one media file and persist chunks before acknowledging SQS."""
    try:
        job = STTJob.model_validate(message.body)
    except ValidationError as exc:
        raise InvalidSTTJob("message body is not a valid STT job") from exc

    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        file_record = await db.get(SessionFile, job.file_id)
        if file_record is None or file_record.session_id != job.session_id:
            raise InvalidSTTJob("job does not match a stored session file")
        if file_record.kind != "audio":
            raise InvalidSTTJob("STT job does not refer to an audio file")
        if file_record.status in {"ready", "failed"}:
            return
        if file_record.status == "awaiting_upload":
            raise RetryableSTTJob("upload confirmation is still committing")
        if file_record.status not in {"uploaded", "processing"}:
            raise InvalidSTTJob(f"file cannot be transcribed from {file_record.status}")
        file_record.status = "processing"
        await db.commit()
        filename = file_record.filename
        content_type = file_record.content_type
        object_key = file_record.object_key
        expected_size = file_record.size_bytes

    try:
        content = await asyncio.to_thread(
            storage.read_object,
            key=object_key,
            max_bytes=settings.max_audio_upload_bytes,
        )
        if len(content) != expected_size:
            raise STTTranscriptionRejected(
                "stored media size changed after confirmation"
            )
        transcript = await provider.transcribe(
            session_id=job.session_id,
            file_id=job.file_id,
            filename=filename,
            content_type=content_type,
            content=content,
        )
        chunks = chunk_text(transcript.text)
        if not chunks:
            raise STTTranscriptionRejected("speech-to-text returned no transcript")
    except (ObjectTooLarge, STTTranscriptionRejected) as exc:
        await _mark_failed(database, job, job.file_id)
        logger.warning("Media file %s failed transcription: %s", job.file_id, exc)
        return
    except STTServiceUnavailable as exc:
        raise RetryableSTTJob("speech-to-text service is unavailable") from exc

    asynchronous_embedding = embedding_queue is not None and bool(
        settings.embedding_queue_url
    )
    if asynchronous_embedding:
        embeddings, embedding_model = [None for _ in chunks], None
    else:
        embeddings, embedding_model = await _embed_chunks(
            chunks,
            provider=embedding_provider,
            batch_size=settings.embedding_batch_size,
        )

    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        file_record = await db.get(SessionFile, job.file_id)
        if file_record is None or file_record.status in {"ready", "failed"}:
            return
        await db.execute(
            delete(DocumentChunk).where(DocumentChunk.session_file_id == job.file_id)
        )
        db.add_all(
            DocumentChunk(
                session_file_id=job.file_id,
                chunk_index=index,
                content=chunk,
                embedding=embeddings[index],
                embedding_model=embedding_model,
            )
            for index, chunk in enumerate(chunks)
        )
        file_record.transcript = transcript.text
        file_record.chunk_count = len(chunks)
        if not asynchronous_embedding:
            file_record.status = "ready"
            await _update_session_status(db, session_id=job.session_id)
        await db.commit()

    if asynchronous_embedding:
        try:
            await asyncio.to_thread(
                embedding_queue.send,
                {
                    "schema_version": 1,
                    "session_id": job.session_id,
                    "file_id": job.file_id,
                },
            )
        except QueueError as exc:
            raise RetryableSTTJob("embedding job could not be queued") from exc
    logger.info("Transcribed media file %s into %d chunks", job.file_id, len(chunks))


async def run_worker() -> None:
    """Poll SQS and acknowledge only terminally handled media jobs."""
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.stt_queue_url or settings.stt_service_url is None:
        logger.info("STT queue or provider is not configured; worker is disabled")
        return
    database = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    storage = S3ObjectStorage(
        bucket=settings.s3_bucket,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )
    queue = SQSQueue(
        queue_url=settings.stt_queue_url,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        visibility_timeout=settings.queue_visibility_timeout_seconds,
        wait_seconds=settings.queue_wait_seconds,
    )
    provider = HttpSTTClient(
        base_url=str(settings.stt_service_url),
        api_token=settings.stt_api_token.get_secret_value(),
        timeout_seconds=settings.stt_request_timeout_seconds,
    )
    embedding_queue = (
        SQSQueue(
            queue_url=settings.embedding_queue_url,
            region=settings.aws_region,
            endpoint_url=settings.aws_endpoint_url,
            visibility_timeout=settings.queue_visibility_timeout_seconds,
            wait_seconds=settings.queue_wait_seconds,
        )
        if settings.embedding_queue_url
        else None
    )
    embedding_provider = (
        HttpEmbeddingClient(
            base_url=str(settings.embedding_service_url),
            api_token=settings.embedding_api_token.get_secret_value(),
            timeout_seconds=settings.embedding_request_timeout_seconds,
        )
        if settings.embedding_service_url is not None
        else None
    )
    try:
        while True:
            try:
                messages = await asyncio.to_thread(
                    queue.receive, max_messages=settings.queue_batch_size
                )
            except QueueError:
                logger.exception("STT queue unavailable")
                await asyncio.sleep(2)
                continue
            for message in messages:
                try:
                    await process_message(
                        database,
                        storage,
                        provider,
                        message,
                        settings=settings,
                        embedding_provider=embedding_provider,
                        embedding_queue=embedding_queue,
                    )
                except RetryableSTTJob as exc:
                    logger.info("Leaving STT job for retry: %s", exc)
                    continue
                except InvalidSTTJob:
                    logger.exception("Discarding invalid STT message")
                except Exception:
                    logger.exception("STT processing failed; leaving job for retry")
                    continue
                queue.delete(message)
    finally:
        await provider.aclose()
        if embedding_provider is not None:
            await embedding_provider.aclose()
        await database.dispose()
        storage.close()
        queue.close()
        if embedding_queue is not None:
            embedding_queue.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
