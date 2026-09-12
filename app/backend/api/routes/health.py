"""Is anything alive?

Two endpoints, because a load balancer and a human want different things.

    /health   liveness. Cheap, no dependencies. Answer or be replaced.
    /ready    readiness. Checks the database, Redis and the queues.

Getting this wrong is a classic outage: if the load balancer's health check
also tests the database, then a brief database blip makes every task fail its
check, and the balancer removes all of them. A recoverable problem becomes a
total one.
"""
import logging

from fastapi import APIRouter
from sqlalchemy import text
from starlette.concurrency import run_in_threadpool

from ...shared import events, queues
from ...shared.config import get_settings
from ...shared.db import AsyncSessionLocal

router = APIRouter(tags=["health"])
log = logging.getLogger(__name__)
settings = get_settings()


@router.get("/health")
async def health() -> dict:
    """Liveness. Deliberately checks nothing external."""
    return {"status": "ok", "service": "api", "env": settings.env}


@router.get("/ready")
async def ready() -> dict:
    """Readiness. Reports each dependency separately so a failure names itself."""
    checks: dict[str, bool] = {}

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:                            # noqa: BLE001
        log.warning("database is not reachable", exc_info=True)
        checks["database"] = False

    try:
        client = events.async_client()
        await client.ping()
        await client.aclose()
        checks["redis"] = True
    except Exception:                            # noqa: BLE001
        log.warning("redis is not reachable", exc_info=True)
        checks["redis"] = False

    for name, url in (("ingest_queue", settings.ingest_queue_url),
                      ("chat_queue", settings.chat_queue_url),
                      ("stt_queue", settings.stt_queue_url),
                      ("embed_queue", settings.embed_queue_url)):
        if not url:
            checks[name] = False
            continue
        try:
            await run_in_threadpool(queues.depth, url)
            checks[name] = True
        except Exception:                        # noqa: BLE001
            log.warning("%s is not reachable", name, exc_info=True)
            checks[name] = False

    return {"status": "ok" if all(checks.values()) else "degraded", "checks": checks}


@router.get("/metrics/queues")
async def queue_metrics() -> dict:
    """Depth and in-flight counts — the numbers autoscaling watches.

    Exposed here so you can see them without an AWS console. In production the
    scaling policy reads them from CloudWatch, not from this endpoint.
    """
    out: dict[str, dict[str, int]] = {}
    # All four, because the two the GPU drains are the ones that actually back
    # up -- one card is the slowest thing in the system.
    for name, url in (("ingest", settings.ingest_queue_url),
                      ("chat", settings.chat_queue_url),
                      ("stt", settings.stt_queue_url),
                      ("embed", settings.embed_queue_url)):
        if not url:
            continue
        try:
            out[name] = await run_in_threadpool(queues.depth, url)
        except Exception:                        # noqa: BLE001
            out[name] = {"waiting": -1, "in_flight": -1}
    return out
