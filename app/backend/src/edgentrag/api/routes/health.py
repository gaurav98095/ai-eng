"""Liveness and readiness endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from edgentrag.api.dependencies import get_database, get_settings
from edgentrag.core.config import Settings
from edgentrag.core.database import Database

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
    database, queue, or model service; a later component will add a separate
    readiness check for those dependencies.
    """
    return HealthResponse(status="ok")


@router.get("/ready", response_model=ReadinessResponse)
async def readiness_check(
    settings: Annotated[Settings, Depends(get_settings)],
    database: Annotated[Database, Depends(get_database)],
    response: Response,
) -> ReadinessResponse:
    """Report whether configuration and the database are ready.

    Later components will add queue and model-service checks. Keeping readiness
    distinct from liveness means dependency failures do not cause an
    orchestration platform to mistake a running API process for dead.
    """
    try:
        await database.ping()
    except SQLAlchemyError:
        response.status_code = 503
        return ReadinessResponse(
            status="not_ready",
            environment=settings.environment,
            checks={"configuration": "ok", "database": "failed"},
        )

    return ReadinessResponse(
        status="ready",
        environment=settings.environment,
        checks={"configuration": "ok", "database": "ok"},
    )
