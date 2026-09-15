"""Application factory, lifespan, and the ASGI entry point.

Keep startup wiring here. Route handlers live in dedicated modules so that
features can grow without turning this file into an untestable dependency hub.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from edgentrag.api.routes.answers import router as answers_router
from edgentrag.api.routes.chat import router as chat_router
from edgentrag.api.routes.health import router as health_router
from edgentrag.api.routes.search import router as search_router
from edgentrag.api.routes.sessions import router as sessions_router
from edgentrag.chat_queue import SQSChatQueue
from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import HttpEmbeddingClient
from edgentrag.generation.client import HttpGenerationClient
from edgentrag.ingestion.queue import SQSIngestionQueue
from edgentrag.storage.s3 import S3ObjectStorage
from edgentrag.shared.events import RedisEvents
from edgentrag.api.routes.events import router as events_router


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create the HTTP application with its routes and metadata."""
    app_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Create shared resources once and release them during shutdown."""
        app.state.database = Database(
            app_settings.database_url,
            pool_size=app_settings.db_pool_size,
            max_overflow=app_settings.db_max_overflow,
        )
        app.state.object_storage = S3ObjectStorage(
            bucket=app_settings.s3_bucket,
            region=app_settings.aws_region,
            endpoint_url=app_settings.aws_endpoint_url,
        )
        app.state.events = RedisEvents(
            app_settings.redis_url,
            history_turns=app_settings.history_turns,
            tls=app_settings.redis_tls,
        )
        app.state.ingestion_queue = SQSIngestionQueue(
            queue_url=app_settings.ingestion_queue_url,
            region=app_settings.aws_region,
            endpoint_url=app_settings.aws_endpoint_url,
        )
        app.state.chat_queue = SQSChatQueue(
            queue_url=app_settings.chat_queue_url,
            region=app_settings.aws_region,
            endpoint_url=app_settings.aws_endpoint_url,
        )
        app.state.embedding_provider = None
        app.state.generation_provider = None
        try:
            if app_settings.embedding_service_url is not None:
                app.state.embedding_provider = HttpEmbeddingClient(
                    base_url=str(app_settings.embedding_service_url),
                    api_token=app_settings.embedding_api_token.get_secret_value(),
                    timeout_seconds=app_settings.embedding_request_timeout_seconds,
                )
            if app_settings.generation_service_url is not None:
                app.state.generation_provider = HttpGenerationClient(
                    base_url=str(app_settings.generation_service_url),
                    api_token=app_settings.generation_api_token.get_secret_value(),
                    timeout_seconds=app_settings.generation_request_timeout_seconds,
                )
            yield
        finally:
            if app.state.generation_provider is not None:
                await app.state.generation_provider.aclose()
            if app.state.embedding_provider is not None:
                await app.state.embedding_provider.aclose()
            await app.state.database.dispose()
            app.state.events.close()
            app.state.object_storage.close()
            app.state.ingestion_queue.close()
            app.state.chat_queue.close()

    app = FastAPI(
        title="EdgentRAG API",
        version="0.1.0",
        description="API for uploading material and asking grounded questions.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.include_router(health_router)
    app.include_router(sessions_router)
    app.include_router(chat_router)
    app.include_router(search_router)
    app.include_router(answers_router)
    app.include_router(events_router)
    return app


app = create_app()
