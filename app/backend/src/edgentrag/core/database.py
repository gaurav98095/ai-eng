"""The async SQLAlchemy database boundary."""

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def _async_url(url: str) -> str:
    """Normalize v3 deployment URLs to the async SQLAlchemy driver."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


def _sync_url(url: str) -> str:
    """Normalize a deployment URL for synchronous worker sessions."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+asyncpg://"):
        return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    if url.startswith("sqlite+aiosqlite://"):
        return url.replace("sqlite+aiosqlite://", "sqlite://", 1)
    return url


class Database:
    """Own one engine and session factory for one application process.

    Route handlers receive request-scoped sessions from this boundary.
    Keeping engine ownership here gives the application one place to configure,
    test, and gracefully dispose database resources.
    """

    def __init__(self, database_url: str, *, pool_size: int = 10, max_overflow: int = 5) -> None:
        database_url = _async_url(database_url)
        engine_options = {
            "pool_pre_ping": True,
        }
        if database_url.startswith("postgresql+asyncpg://"):
            engine_options.update(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=30)
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            **engine_options,
        )
        if self._engine.dialect.name == "sqlite":
            event.listen(
                self._engine.sync_engine, "connect", _enable_sqlite_foreign_keys
            )
        self.sessions = async_sessionmaker(
            self._engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def ping(self) -> None:
        """Raise a SQLAlchemy error when the configured database is unavailable."""
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1"))

    async def dispose(self) -> None:
        """Close pooled connections during application shutdown."""
        await self._engine.dispose()


class WorkerDatabase:
    """Synchronous database boundary used by blocking worker processes."""

    def __init__(self, database_url: str, *, pool_size: int = 10, max_overflow: int = 5) -> None:
        url = _sync_url(database_url)
        options: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("postgresql+"):
            options.update(pool_size=pool_size, max_overflow=max_overflow, pool_timeout=30)
        self.engine = create_engine(url, **options)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite)
        self.sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()

def _enable_sqlite_foreign_keys(connection, _record) -> None:
    """SQLite otherwise silently ignores foreign keys and delete cascades."""
    cursor = connection.cursor()
    try:
        _configure_sqlite(cursor)
    finally:
        cursor.close()


def _configure_sqlite(connection) -> None:
    """Apply the v3 local SQLite safety/performance pragmas."""
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("PRAGMA busy_timeout=30000")
    connection.execute("PRAGMA foreign_keys=ON")
