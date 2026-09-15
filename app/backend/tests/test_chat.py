"""Integration tests for the persisted chat boundary."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from edgentrag.api.app import create_app
from edgentrag.api.dependencies import get_chat_queue
from edgentrag.chat_queue import ChatQueueUnavailable
from edgentrag.core.config import Settings
from edgentrag.sessions.models import ChatSession, Message


def migrate(database_url: str) -> None:
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


class RecordingQueue:
    def __init__(self, *, fail: bool = False) -> None:
        self.jobs: list[dict[str, str]] = []
        self.fail = fail

    def enqueue_message(self, **job: str) -> None:
        if self.fail:
            raise ChatQueueUnavailable("offline")
        self.jobs.append(job)


def test_chat_accepts_only_ready_sessions_and_queues_two_message_turns(
    tmp_path,
) -> None:
    database_path = tmp_path / "chat.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate(database_url)
    engine = create_engine(f"sqlite:///{database_path}")
    with Session(engine) as db:
        db.add(ChatSession(id="ready", status="ready"))
        db.commit()
    engine.dispose()

    class VisibleQueue(RecordingQueue):
        def enqueue_message(self, **job: str) -> None:
            # An independent consumer connection must see the committed answer.
            with Session(engine) as db:
                answer = db.get(Message, job["message_id"])
                assert answer is not None and answer.status == "pending"
            super().enqueue_message(**job)

    queue = VisibleQueue()
    app = create_app(settings=Settings(environment="test", database_url=database_url))
    app.dependency_overrides[get_chat_queue] = lambda: queue
    with TestClient(app) as client:
        response = client.post("/sessions/ready/chat", json={"content": "What is RAG?"})
        messages = client.get("/sessions/ready/chat")
        assert (
            client.post("/sessions/missing/chat", json={"content": "Q"}).status_code
            == 404
        )
        new_id = client.post("/sessions").json()["session_id"]
        assert (
            client.post(f"/sessions/{new_id}/chat", json={"content": "Q"}).status_code
            == 409
        )
        assert (
            client.post("/sessions/ready/chat", json={"content": "  "}).status_code
            == 422
        )
    engine.dispose()
    assert len(queue.jobs) == 1

    assert response.status_code == 202
    assert response.json()["message_id"] == queue.jobs[0]["message_id"]
    assert [item["role"] for item in messages.json()] == ["user", "assistant"]
    assert messages.json()[1]["status"] == "pending"


def test_chat_preserves_failed_turn_when_queue_is_unavailable(tmp_path) -> None:
    database_path = tmp_path / "chat-fail.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate(database_url)
    engine = create_engine(f"sqlite:///{database_path}")
    with Session(engine) as db:
        db.add(ChatSession(id="ready", status="ready"))
        db.commit()
    engine.dispose()

    app = create_app(settings=Settings(environment="test", database_url=database_url))
    app.dependency_overrides[get_chat_queue] = lambda: RecordingQueue(fail=True)
    with TestClient(app) as client:
        response = client.post("/sessions/ready/chat", json={"content": "Question"})
        messages = client.get("/sessions/ready/chat")

    assert response.status_code == 503
    assert [item["status"] for item in messages.json()] == ["done", "failed"]
