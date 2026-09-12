"""Integration tests for session creation."""

from pathlib import Path
from uuid import UUID

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from edgentrag.api.app import create_app
from edgentrag.core.config import Settings
from edgentrag.sessions.models import ChatSession


def migrate_database(database_url: str) -> None:
    """Apply the same migration used by local and deployed environments."""
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def test_create_session_persists_and_returns_a_new_session(tmp_path) -> None:
    """The endpoint stores a new record and returns its stable public ID."""
    database_path = tmp_path / "sessions.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    settings = Settings(environment="test", database_url=database_url)

    with TestClient(create_app(settings=settings)) as client:
        response = client.post("/sessions")

    assert response.status_code == 201
    body = response.json()
    session_id = str(UUID(body["session_id"]))
    assert body["status"] == "created"
    assert body["created_at"]

    sync_url = f"sqlite:///{database_path}"
    engine = create_engine(sync_url)
    try:
        with Session(engine) as database_session:
            persisted_session = database_session.scalar(
                select(ChatSession).where(ChatSession.id == session_id)
            )
    finally:
        engine.dispose()

    assert persisted_session is not None
    assert persisted_session.status == "created"
