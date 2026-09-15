"""Liveness and readiness endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from edgentrag.api.dependencies import get_database, get_events, get_settings
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.shared.events import RedisEvents

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """The intentionally small contract used by uptime checks."""

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """The dependency checks that currently make the API ready to serve."""

    status: Literal["ready", "not_ready"]
    environment: Literal["local", "test", "production"]
    checks: dict[str, Literal["ok", "failed"]]


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report that this API process can accept HTTP requests.

    This is a liveness endpoint, not a readiness endpoint. It does not query a
    database, queue, or model service.
    """
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
    events: Annotated[RedisEvents, Depends(get_events)],
    response: Response,
) -> ReadinessResponse:
    """Report whether configuration and the database are ready.

    Keeping readiness distinct from liveness means dependency failures do not
    cause an orchestration platform to mistake a running API process for dead.
    """
    database_ok = True
    try:
        await database.ping()
    except SQLAlchemyError:
        database_ok = False
    redis_ok = events.ping()
    if not database_ok or not redis_ok:
        response.status_code = 503
        return ReadinessResponse(
            status="not_ready",
            environment=settings.environment,
            checks={
                "configuration": "ok",
                "database": "ok" if database_ok else "failed",
                "redis": "ok" if redis_ok else "failed",
            },
        )

    return ReadinessResponse(
        status="ready",
        environment=settings.environment,
        checks={"configuration": "ok", "database": "ok", "redis": "ok"},
    )
