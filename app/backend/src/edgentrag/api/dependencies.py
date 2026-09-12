"""FastAPI dependencies that expose application-owned resources."""

from typing import cast

from fastapi import Request

from edgentrag.core.config import Settings
from edgentrag.core.database import Database


def get_settings(request: Request) -> Settings:
    """Return the immutable configuration snapshot for this application."""
    return cast(Settings, request.app.state.settings)


def get_database(request: Request) -> Database:
    """Return the database boundary created during the application's lifespan."""
    return cast(Database, request.app.state.database)
