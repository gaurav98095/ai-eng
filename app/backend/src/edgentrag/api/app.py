"""Application factory, lifespan, and the ASGI entry point.

Keep startup wiring here. Route handlers live in dedicated modules so that
features can grow without turning this file into an untestable dependency hub.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from edgentrag.api.routes.health import router as health_router
from edgentrag.api.routes.sessions import router as sessions_router
from edgentrag.core.config import Settings, load_settings
from edgentrag.core.database import Database
from edgentrag.ingestion.queue import SQSIngestionQueue
from edgentrag.storage.s3 import S3ObjectStorage


def create_app(*, settings: Settings | None = None) -> FastAPI:
    """Create the HTTP application with its routes and metadata."""
    app_settings = settings or load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Create shared resources once and release them during shutdown."""
        app.state.database = Database(app_settings.database_url)
        app.state.object_storage = S3ObjectStorage(
            bucket=app_settings.s3_bucket,
            region=app_settings.aws_region,
            endpoint_url=app_settings.aws_endpoint_url,
        )
        app.state.ingestion_queue = SQSIngestionQueue(
            queue_url=app_settings.ingestion_queue_url,
            region=app_settings.aws_region,
            endpoint_url=app_settings.aws_endpoint_url,
        )
        try:
            yield
        finally:
            await app.state.database.dispose()
            app.state.object_storage.close()
            app.state.ingestion_queue.close()

    app = FastAPI(
        title="EdgentRAG API",
        version="0.1.0",
        description="API for uploading material and asking grounded questions.",
        lifespan=lifespan,
    )
    app.state.settings = app_settings
    app.include_router(health_router)
    app.include_router(sessions_router)
    return app


app = create_app()
