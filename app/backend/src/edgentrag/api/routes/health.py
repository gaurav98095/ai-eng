"""Health endpoint for the API process itself."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """The intentionally small contract used by uptime checks."""

    status: Literal["ok"]


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Report that this API process can accept HTTP requests.

    This is a liveness endpoint, not a readiness endpoint. It does not query a
    database, queue, or model service; a later component will add a separate
    readiness check for those dependencies.
    """
    return HealthResponse(status="ok")

