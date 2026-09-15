"""Asynchronous vectorization worker for production ingestion jobs."""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select

from edgentrag.core.config import load_settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import HttpEmbeddingClient
from edgentrag.embedding.schemas import validate_embedding_batch
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.ingestion.worker import _update_session_status
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.sessions.state import lock_session
from edgentrag.shared.queues import Message, QueueError, SQSQueue

logger = logging.getLogger(__name__)


class EmbeddingJob(BaseModel):
    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=36)
    file_id: str = Field(min_length=1, max_length=36)


class InvalidEmbeddingJob(Exception):
    """Poison queue payload that should be acknowledged and discarded."""


async def process_message(database: Database, message: Message, *, batch_size: int) -> None:
    try:
        job = EmbeddingJob.model_validate(message.body)
    except ValidationError as exc:
        raise InvalidEmbeddingJob("invalid embedding job") from exc

    settings = load_settings()
    if settings.embedding_service_url is None:
        raise RuntimeError("embedding service is not configured")
    provider = HttpEmbeddingClient(
        base_url=str(settings.embedding_service_url),
        api_token=settings.embedding_api_token.get_secret_value(),
        timeout_seconds=settings.embedding_request_timeout_seconds,
    )
    try:
        async with database.sessions() as db:
            session = await db.get(ChatSession, job.session_id)
            file_record = await db.get(SessionFile, job.file_id)
            if session is None or file_record is None or file_record.session_id != job.session_id:
                raise InvalidEmbeddingJob("embedding job does not match stored records")
            rows = list(
                (
                    await db.scalars(
                        select(DocumentChunk)
                        .where(DocumentChunk.session_file_id == job.file_id)
                        .order_by(DocumentChunk.chunk_index)
                    )
                ).all()
            )
            if not rows:
                await lock_session(db, job.session_id)
                await db.refresh(file_record)
                file_record.status = "ready"
                await _update_session_status(db, session_id=job.session_id)
                await db.commit()
                return

            model_name: str | None = None
            for start in range(0, len(rows), batch_size):
                group = rows[start : start + batch_size]
                batch = await provider.embed([row.content for row in group])
                validate_embedding_batch(batch, len(group))
                if model_name is None:
                    model_name = batch.model
                elif batch.model != model_name:
                    raise RuntimeError("embedding model changed during one file")
                for row, vector in zip(group, batch.embeddings, strict=True):
                    row.embedding = vector
                    row.embedding_model = batch.model

            await lock_session(db, job.session_id)
            await db.refresh(file_record)
            file_record.status = "ready"
            await _update_session_status(db, session_id=job.session_id)
            await db.commit()
            logger.info("Embedded file %s (%d chunks)", job.file_id, len(rows))
    finally:
        await provider.aclose()


async def run_worker() -> None:
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    if not settings.embedding_queue_url:
        logger.info("Embedding queue is not configured; worker is disabled")
        return
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
    try:
        while True:
            try:
                messages = await asyncio.to_thread(queue.receive, max_messages=settings.queue_batch_size)
            except QueueError:
                logger.exception("Embedding queue unavailable")
                await asyncio.sleep(2)
                continue
            for message in messages:
                try:
                    await process_message(database, message, batch_size=settings.embedding_batch_size)
                except InvalidEmbeddingJob:
                    logger.exception("Discarding invalid embedding message")
                except RuntimeError:
                    logger.exception("Embedding configuration/provider failed; leaving job for retry")
                    continue
                except ValueError:
                    logger.exception("Embedding response invalid; leaving job for retry")
                    continue
                except Exception:
                    logger.exception("Embedding failed; leaving job for retry")
                    continue
                queue.delete(message)
    finally:
        await database.dispose()
        queue.close()


if __name__ == "__main__":
    asyncio.run(run_worker())
