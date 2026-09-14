"""FastAPI dependencies that expose application-owned resources."""

from collections.abc import AsyncIterator
from typing import cast

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingProvider
from edgentrag.ingestion.queue import IngestionQueue
from edgentrag.storage.s3 import ObjectStorage


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


def get_embedding_provider(request: Request) -> EmbeddingProvider | None:
    """Return the optional shared HTTP client owned by this API process."""
    return cast(EmbeddingProvider | None, request.app.state.embedding_provider)


async def get_db_session(request: Request) -> AsyncIterator[AsyncSession]:
    """Yield one database session for a request and always close it afterward."""
    database = get_database(request)
    async with database.sessions() as session:
        yield session
