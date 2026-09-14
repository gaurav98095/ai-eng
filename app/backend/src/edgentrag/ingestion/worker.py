"""SQS worker that extracts UTF-8 text and stores overlapping chunks."""

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import (
    EmbeddingBatch,
    EmbeddingProvider,
    EmbeddingServiceUnavailable,
    HttpEmbeddingClient,
)
from edgentrag.ingestion.extraction import (
    DocumentExtractionError,
    chunk_text,
    extract_text,
)
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.ingestion.queue import (
    QueueMessage,
    QueueUnavailable,
    SQSIngestionQueue,
)
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.storage.s3 import (
    ObjectStorage,
    ObjectTooLarge,
    S3ObjectStorage,
    StorageUnavailable,
)

logger = logging.getLogger(__name__)


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


async def _embed_chunks(
    chunks: list[str],
    *,
    provider: EmbeddingProvider | None,
    batch_size: int,
) -> tuple[list[list[float] | None], str | None]:
    """Embed bounded batches and check every response uses one model/shape."""
    if provider is None:
        return [None for _ in chunks], None

    vectors: list[list[float]] = []
    model_name: str | None = None
    dimensions: int | None = None
    for start in range(0, len(chunks), batch_size):
        batch: EmbeddingBatch = await provider.embed(chunks[start : start + batch_size])
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
    message: QueueMessage,
    *,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None = None,
) -> None:
    """Process one message; retryable infrastructure errors propagate."""
    try:
        job = IngestionJob.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidIngestionJob("message body is not a valid ingestion job") from exc

    async with database.sessions() as database_session:
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
            extracted_text = extract_text(
                filename=file_record.filename,
                content_type=file_record.content_type,
                content=content,
            )
            chunks = chunk_text(extracted_text)
        except (DocumentExtractionError, ObjectTooLarge) as exc:
            file_record.status = "failed"
            await _update_session_status(
                database_session,
                session_id=job.session_id,
            )
            await database_session.commit()
            logger.warning("File %s failed validation: %s", job.file_id, exc)
            return

        embeddings, embedding_model = await _embed_chunks(
            chunks,
            provider=embedding_provider,
            batch_size=settings.embedding_batch_size,
        )

        await database_session.execute(
            delete(DocumentChunk).where(DocumentChunk.session_file_id == job.file_id)
        )
        database_session.add_all(
            DocumentChunk(
                session_file_id=job.file_id,
                chunk_index=chunk_index,
                content=chunk,
                embedding=embeddings[chunk_index],
                embedding_model=embedding_model,
            )
            for chunk_index, chunk in enumerate(chunks)
        )
        file_record.status = "ready"
        await _update_session_status(database_session, session_id=job.session_id)
        await database_session.commit()
        logger.info(
            "Processed file %s into %d text chunks",
            job.file_id,
            len(chunks),
        )


async def run_worker() -> None:
    """Poll SQS forever; failed infrastructure work remains available to retry."""
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    database = Database(settings.database_url)
    storage = S3ObjectStorage(
        bucket=settings.s3_bucket,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )
    queue = SQSIngestionQueue(
        queue_url=settings.ingestion_queue_url,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
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
                    queue.receive_messages,
                    max_messages=1,
                    wait_time_seconds=20,
                )
            except QueueUnavailable:
                logger.exception("Could not poll the ingestion queue; retrying soon")
                await asyncio.sleep(2)
                continue

            for message in messages:
                try:
                    await process_message(
                        database,
                        storage,
                        message,
                        settings=settings,
                        embedding_provider=embedding_provider,
                    )
                except RetryableIngestionJob as exc:
                    logger.info("Leaving ingestion job for retry: %s", exc)
                    continue
                except InvalidIngestionJob:
                    logger.exception("Discarding invalid ingestion message")
                except StorageUnavailable:
                    logger.exception("Storage is unavailable; leaving job for retry")
                    continue
                except Exception:
                    logger.exception("Ingestion failed; leaving job for retry")
                    continue

                try:
                    await asyncio.to_thread(
                        queue.delete_message,
                        receipt_handle=message.receipt_handle,
                    )
                except QueueUnavailable:
                    logger.exception("Could not acknowledge job; it may be redelivered")
    finally:
        await database.dispose()
        storage.close()
        queue.close()
        if embedding_provider is not None:
            await embedding_provider.aclose()


if __name__ == "__main__":
    asyncio.run(run_worker())
