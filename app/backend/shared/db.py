"""Database connections — two of them, on purpose.

SQLite, on the same machine as everything else. That is deliberate: the point of
version 2 is the *code* — async endpoints and queued background work — not
renting managed infrastructure.

Why SQLite is safe here and was not in version 1
------------------------------------------------
Version 1 ran SQLite in its default journal mode, where **a writer locks the
whole file and blocks every reader**. Ingestion wrote constantly, browsers
polled constantly, and they fought.

WAL mode changes that: one writer and many readers proceed at the same time, and
several *processes* can share the file safely. Writers still take turns, but
each write here is milliseconds, and `busy_timeout` makes a second writer wait
rather than fail.

The real limit is worth knowing: **WAL requires every process to be on the same
machine.** It does not work over a network filesystem. So this scales to more
workers on one box and no further — which is precisely the wall that makes
Postgres necessary later, and precisely why the database stays behind a URL.

Two engines
-----------
The API is async — it holds many mostly-idle connections, so it uses `aiosqlite`
and never blocks its event loop. The workers are synchronous, because blocking
is their whole job. One `DATABASE_URL` configures both.
"""
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import get_settings
from .models import Base

log = logging.getLogger(__name__)
settings = get_settings()

BUSY_TIMEOUT_MS = 30_000


def _driver(url: str, driver: str | None) -> str:
    """Point a URL at a specific driver, keeping the rest of it intact.

    Also normalises `postgres://`, which SQLAlchemy dropped as a dialect name
    and which RDS consoles still emit — so a URL copied from AWS does not fail
    at import with an opaque "Can't load plugin".
    """
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)

    scheme, rest = url.split("://", 1)
    base = scheme.split("+", 1)[0]
    return f"{base}+{driver}://{rest}" if driver else f"{base}://{rest}"


def _is_sqlite() -> bool:
    return settings.database_url.startswith("sqlite")


def _pragmas(dbapi_connection, _record) -> None:
    """Applied to every new SQLite connection, in every process.

    These are per-connection settings, not stored in the file, so they have to
    be set on each one rather than once. WAL is what makes a concurrent reader
    and writer possible at all; busy_timeout turns "database is locked" into
    "wait your turn".
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # Durable enough and much faster than FULL: a crash can lose the last
    # transaction but cannot corrupt the file.
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


_pool_kwargs: dict = {} if _is_sqlite() else {
    "pool_size": settings.db_pool_size,
    "max_overflow": settings.db_max_overflow,
}


# --- async, for the API ------------------------------------------------------

_async_engine = create_async_engine(
    _driver(settings.database_url, "aiosqlite" if _is_sqlite() else "asyncpg"),
    pool_pre_ping=True,
    **_pool_kwargs,
)
AsyncSessionLocal = async_sessionmaker(_async_engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request, always closed."""
    async with AsyncSessionLocal() as session:
        yield session


# --- sync, for the workers ---------------------------------------------------

_sync_engine = create_engine(
    _driver(settings.database_url, None if _is_sqlite() else "psycopg2"),
    pool_pre_ping=True,
    **_pool_kwargs,
)

if _is_sqlite():
    event.listen(_sync_engine, "connect", _pragmas)
    # The async engine wraps a sync one; the event goes on the inner engine.
    event.listen(_async_engine.sync_engine, "connect", _pragmas)

SyncSessionLocal = sessionmaker(bind=_sync_engine, autoflush=False, expire_on_commit=False)


@contextmanager
def worker_session() -> Iterator[Session]:
    """One database session per message handled, always closed."""
    session = SyncSessionLocal()
    try:
        yield session
    finally:
        session.close()


# --- schema ------------------------------------------------------------------

def init_db() -> None:
    """Create any tables that do not exist yet.

    Safe to call from every process at start-up. Real deployments run migrations
    as a separate step; this keeps `docker compose up` to one command.
    """
    Base.metadata.create_all(bind=_sync_engine)
    if _is_sqlite():
        with _sync_engine.connect() as conn:
            mode = conn.execute(text("PRAGMA journal_mode")).scalar()
            log.info("sqlite journal_mode=%s — this is what lets the API and the "
                     "workers share one file", mode)
