"""Consume queued chat turns and persist grounded assistant answers."""

import asyncio
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import update

from edgentrag.chat_queue import ChatQueueMessage, ChatQueueUnavailable, SQSChatQueue
from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider, HttpEmbeddingClient
from edgentrag.generation.client import (
    GenerationInputTooLarge,
    GenerationProvider,
    HttpGenerationClient,
)
from edgentrag.retrieval.answer import answer_session
from edgentrag.retrieval.prompting import AnswerContextTooLarge
from edgentrag.retrieval.service import SearchLimitExceeded, SearchNotReady
from edgentrag.sessions.models import ChatSession, Message
from edgentrag.sessions.state import lock_session
from edgentrag.shared.events import RedisEvents

logger = logging.getLogger(__name__)


class ChatJob(BaseModel):
    """Versioned queue contract containing only persisted identifiers."""

    schema_version: Literal[1]
    session_id: str = Field(min_length=1, max_length=36)
    message_id: str = Field(min_length=1, max_length=36)
    question: str = Field(min_length=1, max_length=2000)


class InvalidChatJob(Exception):
    """Raised for poison messages that cannot identify a pending answer."""


class RetryableChatJob(Exception):
    """Raised when a remote dependency should be retried by SQS."""


async def process_message(
    database: Database,
    message: ChatQueueMessage,
    *,
    settings: Settings,
    embedding_provider: EmbeddingProvider | None,
    generation_provider: GenerationProvider | None,
    events: RedisEvents | None = None,
) -> None:
    """Process one chat job and leave transient failures available for retry."""
    try:
        job = ChatJob.model_validate_json(message.body)
    except ValidationError as exc:
        raise InvalidChatJob("message body is not a valid chat job") from exc

    async with database.sessions() as db:
        await lock_session(db, job.session_id)
        session = await db.get(ChatSession, job.session_id)
        answer = await db.get(Message, job.message_id)
        if session is None or answer is None or answer.session_id != job.session_id:
            raise InvalidChatJob("job does not match a stored session answer")
        if answer.role != "assistant":
            raise InvalidChatJob("chat job must target an assistant message")
        if answer.status in {"done", "failed"}:
            return
        if session.status != "ready":
            raise RetryableChatJob("session is not ready for answering")
        if answer.status != "pending":
            raise RetryableChatJob("answer is already being processed")
        answer.status = "answering"
        await db.commit()
    try:
        result = await answer_session(
            database,
            session_id=job.session_id,
            query=job.question,
            top_k=5,
            max_new_tokens=256,
            embedding_provider=embedding_provider,
            generation_provider=generation_provider,
            max_chunks=settings.search_max_chunks,
        )
    except (
        SearchNotReady,
        SearchLimitExceeded,
        AnswerContextTooLarge,
        GenerationInputTooLarge,
    ) as exc:
        async with database.sessions() as db:
            await db.execute(
                update(Message)
                .where(Message.id == job.message_id, Message.status == "answering")
                .values(status="failed", content=str(exc))
            )
            await db.commit()
        return
    except Exception as exc:
        async with database.sessions() as db:
            await db.execute(
                update(Message)
                .where(Message.id == job.message_id, Message.status == "answering")
                .values(status="pending")
            )
            await db.commit()
        raise RetryableChatJob("answer dependencies are unavailable") from exc

    async with database.sessions() as db:
        await db.execute(
            update(Message)
            .where(Message.id == job.message_id, Message.status == "answering")
            .values(
                status="done",
                content=result.answer,
                sources=[source.model_dump(mode="json") for source in result.sources],
            )
        )
        await db.commit()
    if events is not None:
        events.append_history(job.session_id, "assistant", result.answer)
        events.publish(
            job.session_id,
            "chat.completed",
            {"message_id": job.message_id, "status": "done"},
        )


async def run_worker() -> None:
    """Poll SQS forever; failed infrastructure work remains available to retry."""
    settings = load_settings()
    logging.basicConfig(level=settings.log_level)
    database = Database(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
    )
    queue = SQSChatQueue(
        queue_url=settings.chat_queue_url,
        region=settings.aws_region,
        endpoint_url=settings.aws_endpoint_url,
    )
    events = RedisEvents(
        settings.redis_url, history_turns=settings.history_turns, tls=settings.redis_tls
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
    generation_provider = (
        HttpGenerationClient(
            base_url=str(settings.generation_service_url),
            api_token=settings.generation_api_token.get_secret_value(),
            timeout_seconds=settings.generation_request_timeout_seconds,
        )
        if settings.generation_service_url is not None
        else None
    )
    try:
        while True:
            try:
                messages = await asyncio.to_thread(
                    queue.receive_messages,
                    max_messages=settings.queue_batch_size,
                    wait_time_seconds=settings.queue_wait_seconds,
                    visibility_timeout_seconds=settings.queue_visibility_timeout_seconds,
                )
            except ChatQueueUnavailable:
                logger.exception("Could not poll the chat queue; retrying soon")
                await asyncio.sleep(2)
                continue
            for message in messages:
                try:
                    await process_message(
                        database,
                        message,
                        settings=settings,
                        embedding_provider=embedding_provider,
                        generation_provider=generation_provider,
                        events=events,
                    )
                except RetryableChatJob as exc:
                    logger.info("Leaving chat job for retry: %s", exc)
                    continue
                except InvalidChatJob:
                    logger.exception("Discarding invalid chat message")
                except Exception:
                    logger.exception("Chat answer failed; leaving job for retry")
                    continue
                try:
                    await asyncio.to_thread(
                        queue.delete_message, receipt_handle=message.receipt_handle
                    )
                except ChatQueueUnavailable:
                    logger.exception(
                        "Could not acknowledge chat job; it may be redelivered"
                    )
    finally:
        await database.dispose()
        queue.close()
        events.close()
        if embedding_provider is not None:
            await embedding_provider.aclose()
        if generation_provider is not None:
            await generation_provider.aclose()


if __name__ == "__main__":
    asyncio.run(run_worker())
