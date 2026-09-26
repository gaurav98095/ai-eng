"""Asynchronous vectorization worker for production ingestion jobs."""

from __future__ import annotations

import asyncio
import logging
from time import perf_counter
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from edgentrag.core.config import load_settings
from edgentrag.core.database import Database
from edgentrag.core.telemetry import configure
from edgentrag.embedding.client import EmbeddingProvider, HttpEmbeddingClient
from edgentrag.embedding.schemas import validate_embedding_batch
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.ingestion.worker import _update_session_status
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.sessions.state import lock_session
from edgentrag.shared.queues import Message, QueueError, SQSQueue, maintain_visibility

logger = logging.getLogger(__name__)


class EmbeddingJob(BaseModel):
    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=36)
    file_id: str = Field(min_length=1, max_length=36)


class InvalidEmbeddingJob(Exception):
    """Poison queue payload that should be acknowledged and discarded."""


async def _mark_file_failed(
    database: Database, job: EmbeddingJob, reason: str
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


async def process_message(
    database: Database,
    message: Message,
    *,
    batch_size: int,
    provider: EmbeddingProvider,
) -> None:
    started = perf_counter()
    try:
        job = EmbeddingJob.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidEmbeddingJob("invalid embedding job") from exc

    async with database.sessions() as db:
        session = await db.get(ChatSession, job.session_id)
        file_record = await db.get(SessionFile, job.file_id)
        if (
            session is None
            or file_record is None
            or file_record.session_id != job.session_id
        ):
            raise InvalidEmbeddingJob("embedding job does not match stored records")
        if file_record.status in {"ready", "failed"}:
            return
        rows = list(
            (
                await db.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.session_file_id == job.file_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            ).all()
        )
        row_inputs = [(row.id, row.content) for row in rows]
        if not row_inputs:
            await lock_session(db, job.session_id)
            await db.refresh(file_record)
            file_record.status = "ready"
            await _update_session_status(db, session_id=job.session_id)
            await db.commit()
            return

    model_name: str | None = None
    vectors_by_id: dict[str, list[float]] = {}
    for start in range(0, len(row_inputs), batch_size):
        group = row_inputs[start : start + batch_size]
        batch = await provider.embed([content for _, content in group])
        validate_embedding_batch(batch, len(group))
        if model_name is None:
            model_name = batch.model
        elif batch.model != model_name:
            raise RuntimeError("embedding model changed during one file")
        vectors_by_id.update(
            (row_id, vector)
            for (row_id, _), vector in zip(group, batch.embeddings, strict=True)
        )

    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        file_record = await db.get(SessionFile, job.file_id)
        if file_record is None or file_record.status in {"ready", "failed"}:
            return
        current_rows = list(
            await db.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.session_file_id == job.file_id
                )
            )
        )
        if {row.id for row in current_rows} != set(vectors_by_id):
            raise RuntimeError("document chunks changed during embedding")
        for row in current_rows:
            row.embedding = vectors_by_id[row.id]
            row.embedding_model = model_name
        file_record.status = "ready"
        await _update_session_status(db, session_id=job.session_id)
        await db.commit()

    logger.info(
        "embedding_job file_id=%s chunks=%d model=%s duration_ms=%.2f",
        job.file_id,
        len(row_inputs),
        model_name,
        (perf_counter() - started) * 1000,
    )


async def run_worker() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    configure("edgentrag-embedding-worker")
    if not settings.embedding_queue_url:
        logger.info("Embedding queue is not configured; worker is disabled")
        return
    if settings.embedding_service_url is None:
        raise RuntimeError("embedding service is not configured")
    database = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    queue = SQSQueue(
        queue_url=settings.embedding_queue_url,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
        visibility_timeout=settings.queue_visibility_timeout_seconds,
        wait_seconds=settings.queue_wait_seconds,
    )
    provider = HttpEmbeddingClient(
        base_url=str(settings.embedding_service_url),
        api_token=settings.embedding_api_token.get_secret_value(),
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )
    try:
        while True:
            try:
                messages = await asyncio.to_thread(
                    queue.receive, max_messages=1
                )
            except QueueError:
                logger.exception("Embedding queue unavailable")
                await asyncio.sleep(2)
                continue
            for message in messages:
                try:
                    async with maintain_visibility(queue, message):
                        await process_message(
                            database,
                            message,
                            batch_size=settings.embedding_batch_size,
                            provider=provider,
                        )
                except InvalidEmbeddingJob:
                    logger.exception("Discarding invalid embedding message")
                except RuntimeError:
                    logger.exception(
                        "Embedding configuration/provider failed; leaving job for retry"
                    )
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = EmbeddingJob.model_validate_json(message.body)
                    except ValidationError:
                        continue
                    await _mark_file_failed(
                        database,
                        job,
                        f"job exceeded {settings.queue_max_receives} attempts",
                    )
                except ValueError:
                    logger.exception(
                        "Embedding response invalid; leaving job for retry"
                    )
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = EmbeddingJob.model_validate_json(message.body)
                    except ValidationError:
                        continue
                    await _mark_file_failed(
                        database,
                        job,
                        f"job exceeded {settings.queue_max_receives} attempts",
                    )
                except Exception:
                    logger.exception("Embedding failed; leaving job for retry")
                    if message.receive_count < settings.queue_max_receives:
                        continue
                    try:
                        job = EmbeddingJob.model_validate_json(message.body)
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
                    logger.exception("Could not acknowledge embedding job")
    finally:
        await provider.aclose()
        await database.dispose()
        queue.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
