"""Application factory and the ASGI entry point.

Keep startup wiring here. Route handlers live in dedicated modules so that
features can grow without turning this file into an untestable dependency hub.
"""

from fastapi import FastAPI

from edgentrag.api.routes.health import router as health_router


def create_app() -> FastAPI:
    """Create the HTTP application with its routes and metadata."""
    app = FastAPI(
        title="EdgentRAG API",
        version="0.1.0",
        description="API for uploading material and asking grounded questions.",
    )
    app.include_router(health_router)
    return app


app = create_app()

