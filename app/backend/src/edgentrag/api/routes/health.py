"""Liveness and readiness endpoints."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from edgentrag.core.config import Settings, get_settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """The intentionally small contract used by uptime checks."""

    status: Literal["ok"]


class ReadinessResponse(BaseModel):
    """The dependency checks that currently make the API ready to serve."""

    status: Literal["ready"]
    environment: Literal["local", "test", "production"]
    checks: dict[str, Literal["ok"]]


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
) -> ReadinessResponse:
    """Report whether configuration required by this version has loaded.

    Later components will extend checks with database, queue, and model-service
    connectivity. Keeping readiness distinct means those failures will not
    cause an orchestration platform to mistake a running API process for dead.
    """
    return ReadinessResponse(
        status="ready",
        environment=settings.environment,
        checks={"configuration": "ok"},
    )
