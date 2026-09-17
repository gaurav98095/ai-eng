"""Tests for queued chat answer processing."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from edgentrag.chat.worker import ChatQueueMessage, RetryableChatJob
from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.retrieval.answer_schemas import AnswerResponse
from edgentrag.sessions.models import ChatSession, Message


def migrate(url: str) -> None:
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "head")


class Providers:
    pass


def test_worker_persists_generated_answer(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'worker.db'}"
    migrate(url)
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    with Session(engine) as db:
        db.add_all(
            [ChatSession(id="s", status="ready"), Message(
                id="m", session_id="s", role="assistant", status="pending"
            )]
        )
        db.commit()
    engine.dispose()

    async def fake_answer(*args, **kwargs):
        return AnswerResponse(
            session_id="s", query="question", answer="grounded answer",
            generation_model="test", input_tokens=1, output_tokens=2, sources=[]
        )

    import edgentrag.chat.worker as worker
    monkeypatch.setattr(worker, "answer_session", fake_answer)
    database = Database(url)
    asyncio.run(worker.process_message(
        database,
        ChatQueueMessage(
            body='{"schema_version":1,"session_id":"s","message_id":"m","question":"question"}',
            receipt_handle="r",
        ),
        settings=Settings(_env_file=None, environment="test", database_url=url),
        embedding_provider=None, llm_provider=Providers(),
    ))
    async def read():
        async with database.sessions() as db:
            return await db.scalar(select(Message))
    answer = asyncio.run(read())
    asyncio.run(database.dispose())
    assert answer.status == "done"
    assert answer.content == "grounded answer"


def test_worker_resets_pending_on_transient_failure(tmp_path, monkeypatch) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path / 'retry.db'}"
    migrate(url)
    engine = create_engine(f"sqlite:///{tmp_path / 'retry.db'}")
    with Session(engine) as db:
        db.add_all([ChatSession(id="s", status="ready"), Message(
            id="m", session_id="s", role="assistant", status="pending"
        )])
        db.commit()
    engine.dispose()

    async def broken(*args, **kwargs):
        raise RuntimeError("offline")

    import edgentrag.chat.worker as worker
    monkeypatch.setattr(worker, "answer_session", broken)
    database = Database(url)
    try:
        asyncio.run(worker.process_message(
            database,
            ChatQueueMessage(
                body='{"schema_version":1,"session_id":"s","message_id":"m","question":"question"}',
                receipt_handle="r",
            ),
            settings=Settings(_env_file=None, environment="test", database_url=url),
            embedding_provider=None, llm_provider=Providers(),
        ))
    except RetryableChatJob:
        pass
    else:
        raise AssertionError("transient failure should be retryable")
    async def read_status():
        async with database.sessions() as db:
            return await db.scalar(select(Message.status))
    assert asyncio.run(read_status()) == "pending"
    asyncio.run(database.dispose())
