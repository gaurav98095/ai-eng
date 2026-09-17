"""FastAPI dependencies that expose application-owned resources."""

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.chat_queue import ChatQueue
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider
from edgentrag.generation.client import GenerationProvider
from edgentrag.ingestion.queue import IngestionQueue
from edgentrag.shared.events import RedisEvents
from edgentrag.storage.s3 import ObjectStorage
from edgentrag.stt.queue import STTQueue


def get_settings(request: Request) -> Settings:
    """Return the immutable configuration snapshot for this application."""
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    """Return the database boundary created during the application's lifespan."""
    return cast(Database, request.app.state.database)


def get_object_storage(request: Request) -> ObjectStorage:
    """Return the object-storage adapter configured for this application."""
    return cast(ObjectStorage, request.app.state.object_storage)


def get_ingestion_queue(request: Request) -> IngestionQueue:
    """Return the queue adapter created for this application process."""
    return cast(IngestionQueue, request.app.state.ingestion_queue)


def get_stt_queue(request: Request) -> STTQueue:
    """Return the queue adapter that schedules audio transcription."""
    return cast(STTQueue, request.app.state.stt_queue)


def get_chat_queue(request: Request) -> ChatQueue:
    """Return the queue adapter for interactive chat jobs."""
    return cast(ChatQueue, request.app.state.chat_queue)


def get_embedding_provider(request: Request) -> EmbeddingProvider | None:
    """Return the optional shared HTTP client owned by this API process."""
    return cast(EmbeddingProvider | None, request.app.state.embedding_provider)


def get_generation_provider(request: Request) -> GenerationProvider | None:
    """Return the optional shared generation client owned by this API process."""
    return cast(GenerationProvider | None, request.app.state.generation_provider)


def get_events(request: Request) -> RedisEvents:
    return cast(RedisEvents, request.app.state.events)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one database session for a request and always close it afterward."""
    database = get_database(request)
    async with database.sessions() as session:
        yield session
