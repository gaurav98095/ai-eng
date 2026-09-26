"""Liveness and readiness endpoints."""

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.exc import SQLAlchemyError

from edgentrag.api.dependencies import (
    get_chat_queue,
    get_database,
    get_events,
    get_ingestion_queue,
    get_object_storage,
    get_settings,
    get_stt_queue,
)
from edgentrag.chat_queue import ChatQueue
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.ingestion.queue import IngestionQueue
from edgentrag.shared.events import RedisEvents
from edgentrag.storage.s3 import ObjectStorage
from edgentrag.stt.queue import STTQueue

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
    storage: Annotated[ObjectStorage, Depends(get_object_storage)],
    ingestion_queue: Annotated[IngestionQueue, Depends(get_ingestion_queue)],
    chat_queue: Annotated[ChatQueue, Depends(get_chat_queue)],
    stt_queue: Annotated[STTQueue, Depends(get_stt_queue)],
    response: Response,
) -> ReadinessResponse:
    """Report whether configuration and the database are ready.

    Keeping readiness distinct from liveness means dependency failures do not
    cause an orchestration platform to mistake a running API process for dead.
    """
    database_ok = True
    try:
        await database.ping()
        if settings.environment == "production":
            await database.check_schema()
    except SQLAlchemyError:
        database_ok = False
    redis_ok = await events.ping()
    checks: dict[str, Literal["ok", "failed"]] = {
        "configuration": "ok",
        "database": "ok" if database_ok else "failed",
        "redis": "ok" if redis_ok else "failed",
    }
    if settings.environment == "production":
        checks.update(
            {
                "storage": "ok"
                if await asyncio.to_thread(storage.ping)
                else "failed",
                "ingestion_queue": "ok"
                if await asyncio.to_thread(ingestion_queue.ping)
                else "failed",
                "chat_queue": "ok"
                if await asyncio.to_thread(chat_queue.ping)
                else "failed",
            }
        )
        if settings.stt_service_url is not None:
            checks["stt_queue"] = (
                "ok" if await asyncio.to_thread(stt_queue.ping) else "failed"
            )
    if any(value == "failed" for value in checks.values()):
        response.status_code = 503
        return ReadinessResponse(
            status="not_ready",
            environment=settings.environment,
            checks=checks,
        )

    return ReadinessResponse(
        status="ready",
        environment=settings.environment,
        checks=checks,
    )
