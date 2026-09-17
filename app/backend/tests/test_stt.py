"""End-to-end persistence tests for the speech-to-text queue worker."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from edgentrag.core.config import Settings
from edgentrag.core.database import Database
from edgentrag.ingestion.models import DocumentChunk
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.shared.queues import Message
from edgentrag.stt.client import Transcript
from edgentrag.stt.worker import process_message


class FakeObjectStorage:
    def __init__(self, content: bytes) -> None:
        self.content = content

    def read_object(self, *, key: str, max_bytes: int) -> bytes:
        assert key == "uploads/session-1/audio-1"
        assert len(self.content) <= max_bytes
        return self.content


class FakeSTTProvider:
    async def transcribe(self, **kwargs) -> Transcript:
        assert kwargs["filename"] == "meeting.mp3"
        assert kwargs["content_type"] == "audio/mpeg"
        assert kwargs["content"] == b"audio-bytes"
        return Transcript(
            model="whisper-test",
            text="The project review starts now. Action items follow.",
        )

    async def aclose(self) -> None:
        return None


def migrate_database(database_url: str) -> None:
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def test_stt_worker_persists_transcript_and_searchable_chunks(tmp_path) -> None:
    database_url = f"sqlite+aiosqlite:///{tmp_path / 'stt.db'}"
    migrate_database(database_url)
    database = Database(database_url)

    async def seed() -> None:
        async with database.sessions() as db:
            db.add(ChatSession(id="session-1", status="processing"))
            db.add(
                SessionFile(
                    id="audio-1",
                    session_id="session-1",
                    filename="meeting.mp3",
                    content_type="audio/mpeg",
                    size_bytes=len(b"audio-bytes"),
                    object_key="uploads/session-1/audio-1",
                    kind="audio",
                    status="uploaded",
                )
            )
            await db.commit()

    asyncio.run(seed())
    message = Message(
        body={"schema_version": 1, "session_id": "session-1", "file_id": "audio-1"},
        receipt_handle="receipt-1",
        receive_count=1,
        queue_url="https://sqs.example.test/stt",
    )
    asyncio.run(
        process_message(
            database,
            FakeObjectStorage(b"audio-bytes"),
            FakeSTTProvider(),
            message,
            settings=Settings(
                _env_file=None,
                environment="test",
                database_url=database_url,
            ),
        )
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'stt.db'}")
    try:
        with Session(engine) as db:
            file_record = db.get(SessionFile, "audio-1")
            session = db.get(ChatSession, "session-1")
            chunks = list(
                db.scalars(
                    select(DocumentChunk).where(
                        DocumentChunk.session_file_id == "audio-1"
                    )
                )
            )
            assert file_record is not None
            assert file_record.status == "ready"
            assert file_record.transcript == (
                "The project review starts now. Action items follow."
            )
            assert file_record.chunk_count == 1
            assert session is not None and session.status == "ready"
            assert [chunk.content for chunk in chunks] == [file_record.transcript]
    finally:
        engine.dispose()
        asyncio.run(database.dispose())
