"""SQS worker that extracts UTF-8 text and stores overlapping chunks."""

import asyncio
import logging
from time import perf_counter
from typing import Literal
from uuid import NAMESPACE_URL, uuid5

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.core.telemetry import configure
from edgentrag.embedding.client import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingServiceUnavailable,
    HttpEmbeddingClient,
)
from edgentrag.embedding.schemas import validate_embedding_batch
from edgentrag.ingestion.extraction import (
    DocumentExtractionError,
    chunk_text,
    extract_text,
)
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.sessions.state import lock_session
from edgentrag.shared.queues import Message, QueueError, SQSQueue, maintain_visibility
from edgentrag.storage.s3 import (
    ObjectStorage,
    ObjectTooLarge,
    S3ObjectStorage,
    StorageUnavailable,
)

logger = logging.getLogger(__name__)


def _chunk_id(file_id: str, chunk_index: int) -> str:
    """Return a stable identifier so redelivery never duplicates a chunk."""
    return str(uuid5(NAMESPACE_URL, f"edgentrag:chunk:{file_id}:{chunk_index}"))


class IngestionJob(BaseModel):
    """Versioned queue contract containing identifiers, never file bytes."""

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=36)
    file_id: str = Field(min_length=1, max_length=36)


class InvalidIngestionJob(Exception):
    """Raised for poison messages that cannot refer to a valid file record."""


class RetryableIngestionJob(Exception):
    """Raised when a valid message arrives before its DB transaction commits."""


async def _update_session_status(
    database_session: AsyncSession,
    *,
    session_id: str,
) -> None:
    """Set session status from the current state of all its files."""
    session = await database_session.get(ChatSession, session_id)
    if session is None:
        return

    file_statuses = list(
        await database_session.scalars(
            select(SessionFile.status).where(SessionFile.session_id == session_id)
        )
    )
    active_statuses = {"awaiting_upload", "uploaded", "processing"}
    if any(file_status in active_statuses for file_status in file_statuses):
        session.status = "processing"
    elif any(file_status == "failed" for file_status in file_statuses):
        session.status = "failed"
    else:
        session.status = "ready"


async def _mark_file_failed(
    database: Database, job: IngestionJob, reason: str
) -> None:
    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        file_record = await db.get(SessionFile, job.file_id)
        if file_record is None or file_record.status in {"ready", "failed"}:
            return
        file_record.status = "failed"
        file_record.error = reason[:1000]
        await _update_session_status(db, session_id=job.session_id)
        await db.commit()


async def _embed_chunks(
    chunks: list[str],
    *,
    provider: EmbeddingProvider | None,
    batch_size: int,
) -> tuple[list[list[float] | None], str | None]:
    """Embed bounded batches and check every response uses one model/shape."""
    if batch_size <= 0:
        raise ValueError("embedding batch size must be positive")
    if provider is None:
        return [None for _ in chunks], None

    vectors: list[list[float]] = []
    model_name: str | None = None
    dimensions: int | None = None
    for start in range(0, len(chunks), batch_size):
        batch: EmbeddingBatch = await provider.embed(chunks[start : start + batch_size])
        try:
            validate_embedding_batch(batch, len(chunks[start : start + batch_size]))
        except ValueError as exc:
            raise EmbeddingServiceUnavailable(str(exc)) from exc
        if model_name is None:
            model_name = batch.model
            dimensions = batch.dimensions
        elif batch.model != model_name or batch.dimensions != dimensions:
            raise EmbeddingServiceUnavailable(
                "embedding model or dimensions changed between batches"
            )
        vectors.extend(batch.embeddings)

    return vectors, model_name


async def process_message(
    database: Database,
    storage: ObjectStorage,
    message: Message,
    *,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
    embedding_queue: SQSQueue | None = None,
) -> None:
    """Process one message; retryable infrastructure errors propagate."""
    started = perf_counter()
    try:
        job = IngestionJob.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidIngestionJob("message body is not a valid ingestion job") from exc

    async with database.sessions() as database_session:
        await lock_session(database_session, job.session_id)
        file_record = await database_session.get(SessionFile, job.file_id)
        if file_record is None or file_record.session_id != job.session_id:
            raise InvalidIngestionJob("job does not match a stored session file")
        if file_record.status in {"ready", "failed"}:
            return
        if file_record.status == "awaiting_upload":
            # The API sends SQS before committing its uploaded status. SQS
            # visibility timeout safely retries this brief publication race.
            raise RetryableIngestionJob("upload confirmation is still committing")
        if file_record.status not in {"uploaded", "processing"}:
            raise InvalidIngestionJob(
                f"file cannot be processed from status {file_record.status}"
            )

        file_record.status = "processing"
        await database_session.commit()

        try:
            content = await asyncio.to_thread(
                storage.read_object,
                key=file_record.object_key,
                max_bytes=settings.max_text_extract_bytes,
            )
            if len(content) != file_record.size_bytes:
                raise DocumentExtractionError(
                    "stored object size changed after upload confirmation"
                )
            get_metadata = getattr(storage, "get_object_metadata", None)
            if file_record.object_etag and get_metadata is not None:
                metadata = await asyncio.to_thread(
                    get_metadata,
                    key=file_record.object_key,
                )
                if metadata.etag and metadata.etag != file_record.object_etag:
                    raise DocumentExtractionError(
                        "stored object changed after upload confirmation"
                    )
            extracted_text = extract_text(
                filename=file_record.filename,
                content_type=file_record.content_type,
                content=content,
            )
            chunks = chunk_text(extracted_text)
        except (DocumentExtractionError, ObjectTooLarge) as exc:
            await lock_session(database_session, job.session_id)
            await database_session.refresh(file_record)
            if file_record.status in {"ready", "failed"}:
                return
            file_record.status = "failed"
            await _update_session_status(
                database_session,
                session_id=job.session_id,
            )
            await database_session.commit()
            logger.warning("File %s failed validation: %s", job.file_id, exc)
            return

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

        await lock_session(database_session, job.session_id)
        await database_session.refresh(file_record)
        if file_record.status in {"ready", "failed"}:
            return
        if not asynchronous_embedding and file_record.chunk_count == len(chunks):
            # A redelivered job can observe a stale lifecycle status after a
            # concurrent completion on SQLite. Existing complete chunks make
            # replacing their generated IDs unnecessary and preserve
            # at-least-once idempotency.
            existing_chunks = await database_session.scalar(
                select(func.count())
                .select_from(DocumentChunk)
                .where(DocumentChunk.session_file_id == job.file_id)
            )
            if existing_chunks == len(chunks):
                file_record.status = "ready"
                await _update_session_status(
                    database_session,
                    session_id=job.session_id,
                )
                await database_session.commit()
                return
        existing_rows = list(
            (
                await database_session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.session_file_id == job.file_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            ).all()
        )
        for chunk_index, chunk in enumerate(chunks):
            if chunk_index < len(existing_rows):
                row = existing_rows[chunk_index]
                row.content = chunk
                row.embedding = embeddings[chunk_index]
                row.embedding_model = embedding_model
            else:
                database_session.add(
                    DocumentChunk(
                        id=_chunk_id(job.file_id, chunk_index),
                        session_file_id=job.file_id,
                        chunk_index=chunk_index,
                        content=chunk,
                        embedding=embeddings[chunk_index],
                        embedding_model=embedding_model,
                    )
                )
        if len(existing_rows) > len(chunks):
            await database_session.execute(
                delete(DocumentChunk).where(
                    DocumentChunk.id.in_(row.id for row in existing_rows[len(chunks) :])
                )
            )
        file_record.chunk_count = len(chunks)
        if not asynchronous_embedding:
            file_record.status = "ready"
            await _update_session_status(database_session, session_id=job.session_id)
        await database_session.commit()
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
                raise RetryableIngestionJob(
                    "embedding job could not be queued"
                ) from exc
        logger.info(
            "ingestion_job file_id=%s chunks=%d embedding=%s duration_ms=%.2f",
            job.file_id,
            len(chunks),
            "queued" if asynchronous_embedding else "inline",
            (perf_counter() - started) * 1000,
        )


async def run_worker() -> None:
    """Poll SQS forever; failed infrastructure work remains available to retry."""
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    configure("edgentrag-ingestion-worker")
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
        queue_url=settings.ingestion_queue_url,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        visibility_timeout=settings.queue_visibility_timeout_seconds,
        wait_seconds=settings.queue_wait_seconds,
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
    embedding_provider = None
    if settings.embedding_service_url is not None:
        embedding_provider = HttpEmbeddingClient(
            base_url=str(settings.embedding_service_url),
            api_token=settings.embedding_api_token.get_secret_value(),
            timeout_seconds=settings.embedding_request_timeout_seconds,
        )

    try:
        while True:
            try:
                messages = await asyncio.to_thread(
                    queue.receive,
                    max_messages=1,
                )
            except QueueError:
                logger.exception("Could not poll the ingestion queue; retrying soon")
                await asyncio.sleep(2)
                continue

            for message in messages:
                try:
                    async with maintain_visibility(queue, message):
                        await process_message(
                            database,
                            storage,
                            message,
                            settings=settings,
                            embedding_provider=embedding_provider,
                            embedding_queue=embedding_queue,
                        )
                except RetryableIngestionJob as exc:
                    logger.info("Leaving ingestion job for retry: %s", exc)
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = IngestionJob.model_validate_json(message.body)
                    except ValidationError:
                        continue
                    await _mark_file_failed(
                        database,
                        job,
                        f"job exceeded {settings.queue_max_receives} attempts: {exc}",
                    )
                except InvalidIngestionJob:
                    logger.exception("Discarding invalid ingestion message")
                except StorageUnavailable:
                    logger.exception("Storage is unavailable; leaving job for retry")
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = IngestionJob.model_validate_json(message.body)
                    except ValidationError:
                        continue
                    await _mark_file_failed(
                        database,
                        job,
                        f"job exceeded {settings.queue_max_receives} attempts",
                    )
                except Exception:
                    logger.exception("Ingestion failed; leaving job for retry")
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = IngestionJob.model_validate_json(message.body)
                    except ValidationError:
                        continue
                    await _mark_file_failed(
                        database,
                        job,
                        f"job exceeded {settings.queue_max_receives} attempts",
                    )

                try:
                    await asyncio.to_thread(queue.delete, message)
                except QueueError:
                    logger.exception("Could not acknowledge job; it may be redelivered")
    finally:
        await database.dispose()
        storage.close()
        queue.close()
        if embedding_queue is not None:
            embedding_queue.close()
        if embedding_provider is not None:
            await embedding_provider.aclose()


if __name__ == "__main__":
    asyncio.run(run_worker())
