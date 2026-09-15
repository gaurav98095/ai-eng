"""The async SQLAlchemy database boundary."""

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """Own one engine and session factory for one application process.

    Route handlers receive request-scoped sessions from this boundary.
    Keeping engine ownership here gives the application one place to configure,
    test, and gracefully dispose database resources.
    """

    def __init__(self, database_url: str) -> None:
        self._engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
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


def _enable_sqlite_foreign_keys(connection, _record) -> None:
    """SQLite otherwise silently ignores foreign keys and delete cascades."""
    cursor = connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()
