"""The API — thin, async, and stateless.

It validates a request, writes a row, puts a message on a queue, and returns.
Nothing here waits for a model, opens a file, or does anything measured in
seconds. That is the rule the whole design rests on, and it is what lets you
run thirty of these behind a load balancer.

Every handler is `async def`, which is a promise that nothing inside blocks:
the database is aiosqlite, Redis is redis.asyncio, and the calls that use
blocking libraries (boto3 for presigning and queueing) are pushed to a thread
with `run_in_threadpool`. The one long-lived endpoint — the event stream —
holds hundreds of idle connections, which an event loop does for almost nothing
and a thread pool cannot do at all.

    uvicorn backend.api.main:app --host 0.0.0.0 --port 8000
"""
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ..shared.config import get_settings
from ..shared.db import init_db
from .routes import broker, chat, config, events, health, sessions, uploads

settings = get_settings()

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s  %(levelname)-7s %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("api")

app = FastAPI(title="EdgentRAG v2 — API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.cors_origins.split(",")],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(config.router)
app.include_router(sessions.router)
app.include_router(uploads.router)
app.include_router(chat.router)
app.include_router(events.router)
# The GPU's only way in. See routes/broker.py for why it exists.
app.include_router(broker.router)


@app.on_event("startup")
async def on_startup() -> None:
    # Safe to run from every process: `create_all` skips what already exists, and
    # SQLite serialises the DDL. A real deployment runs migrations as a separate
    # step instead; this keeps `docker compose up` to one command.
    init_db()
    log.info("env          : %s", settings.env)
    log.info("database     : %s", settings.database_url.split("@")[-1])
    log.info("redis        : %s", settings.redis_url)
    log.info("bucket       : %s", settings.s3_bucket)
    for name in ("ingest", "chat", "stt", "embed"):
        url = getattr(settings, f"{name}_queue_url", "")
        log.info("%-9s queue: %s", name, url.rsplit("/", 1)[-1] if url else "(unset)")
    log.info("broker       : %s", "configured" if settings.broker_token else "NO TOKEN SET")
