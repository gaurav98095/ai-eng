"""Integration tests for the text ingestion worker."""

import asyncio
import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.embedding.client import EmbeddingBatch
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.ingestion.queue import QueueMessage
from edgentrag.ingestion.worker import (
    RetryableIngestionJob,
    _embed_chunks,
    process_message,
)
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.storage.s3 import ObjectTooLarge


class FakeObjectStorage:
    """Return fixed object bytes without contacting S3 or Floci."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    def read_object(self, *, key: str, max_bytes: int) -> bytes:
        if len(self.content) > max_bytes:
            raise ObjectTooLarge("too large")
        return self.content


class FakeEmbeddingProvider:
    """Return a deterministic vector without making an HTTP request."""

    def __init__(self) -> None:
        self.batches: list[list[str]] = []

    async def embed(self, texts: list[str]) -> EmbeddingBatch:
        self.batches.append(texts)
        return EmbeddingBatch(
            model="test-embedding-model",
            dimensions=2,
            embeddings=[[1.0, 0.0] for _ in texts],
        )


def test_worker_sends_chunks_to_embedding_service_in_bounded_batches() -> None:
    provider = FakeEmbeddingProvider()

    vectors, model_name = asyncio.run(
        _embed_chunks(
            ["first", "second", "third"],
            provider=provider,
            batch_size=2,
        )
    )

    assert provider.batches == [["first", "second"], ["third"]]
    assert vectors == [[1.0, 0.0], [1.0, 0.0], [1.0, 0.0]]
    assert model_name == "test-embedding-model"


def migrate_database(database_url: str) -> None:
    """Apply all real schema revisions to a temporary database."""
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def test_worker_persists_chunks_and_marks_session_ready(tmp_path) -> None:
    database_path = tmp_path / "worker.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    content = b"# A useful note\n\nThis text should become a searchable chunk."
    session_id = "session-1"
    file_id = "file-1"
    object_key = f"uploads/{session_id}/{file_id}"
    database = Database(database_url)

    async def seed_file() -> None:
        async with database.sessions() as database_session:
            database_session.add(ChatSession(id=session_id, status="processing"))
            database_session.add(
                SessionFile(
                    id=file_id,
                    session_id=session_id,
                    filename="note.md",
                    content_type="text/markdown",
                    size_bytes=len(content),
                    object_key=object_key,
                    status="awaiting_upload",
                )
            )
            await database_session.commit()

    asyncio.run(seed_file())
    message = QueueMessage(
        body=json.dumps(
            {"schema_version": 1, "session_id": session_id, "file_id": file_id}
        ),
        receipt_handle="receipt-1",
    )
    settings = Settings(
        environment="test",
        database_url=database_url,
        max_text_extract_bytes=1024,
        embedding_service_url="https://embedding.example.test",
        embedding_api_token="test-token",
    )
    embedding_provider = FakeEmbeddingProvider()

    async def check_publish_race() -> None:
        with pytest.raises(RetryableIngestionJob):
            await process_message(
                database,
                FakeObjectStorage(content),
                message,
                settings=settings,
                embedding_provider=embedding_provider,
            )

    asyncio.run(check_publish_race())

    async def mark_upload_committed() -> None:
        async with database.sessions() as database_session:
            file_record = await database_session.get(SessionFile, file_id)
            assert file_record is not None
            file_record.status = "uploaded"
            await database_session.commit()

    asyncio.run(mark_upload_committed())

    asyncio.run(
        process_message(
            database,
            FakeObjectStorage(content),
            message,
            settings=settings,
            embedding_provider=embedding_provider,
        )
    )

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as database_session:
            file_record = database_session.get(SessionFile, file_id)
            session = database_session.get(ChatSession, session_id)
            chunks = list(
                database_session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.session_file_id == file_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            assert file_record is not None and file_record.status == "ready"
            assert session is not None and session.status == "ready"
            assert len(chunks) == 1
            assert chunks[0].chunk_index == 0
            assert (
                chunks[0].content
                == "# A useful note\n\nThis text should become a searchable chunk."
            )
            assert chunks[0].embedding == [1.0, 0.0]
            assert chunks[0].embedding_model == "test-embedding-model"
    finally:
        engine.dispose()
        asyncio.run(database.dispose())


def test_worker_fails_a_document_larger_than_extraction_limit(tmp_path) -> None:
    database_path = tmp_path / "worker-too-large.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    content = b"x" * 12
    session_id = "session-2"
    file_id = "file-2"
    database = Database(database_url)

    async def seed_file() -> None:
        async with database.sessions() as database_session:
            database_session.add(ChatSession(id=session_id, status="processing"))
            database_session.add(
                SessionFile(
                    id=file_id,
                    session_id=session_id,
                    filename="large.txt",
                    content_type="text/plain",
                    size_bytes=len(content),
                    object_key=f"uploads/{session_id}/{file_id}",
                    status="uploaded",
                )
            )
            await database_session.commit()

    asyncio.run(seed_file())
    message = QueueMessage(
        body=json.dumps(
            {"schema_version": 1, "session_id": session_id, "file_id": file_id}
        ),
        receipt_handle="receipt-2",
    )
    settings = Settings(
        environment="test",
        database_url=database_url,
        max_text_extract_bytes=10,
    )

    asyncio.run(
        process_message(
            database,
            FakeObjectStorage(content),
            message,
            settings=settings,
        )
    )

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as database_session:
            file_record = database_session.get(SessionFile, file_id)
            session = database_session.get(ChatSession, session_id)
            assert file_record is not None and file_record.status == "failed"
            assert session is not None and session.status == "failed"
    finally:
        engine.dispose()
        asyncio.run(database.dispose())
