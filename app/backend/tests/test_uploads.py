"""Integration tests for direct-to-object-storage upload links."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from edgentrag.api.app import create_app
from edgentrag.api.dependencies import (
    get_ingestion_queue,
    get_object_storage,
    get_stt_queue,
)
from edgentrag.core.config import Settings
from edgentrag.ingestion.queue import QueueUnavailable
from edgentrag.sessions.models import ChatSession, SessionFile
from edgentrag.storage.s3 import ObjectMetadata, ObjectNotFound


class FakeObjectStorage:
    """A deterministic storage adapter that never contacts AWS."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str | int]] = []
        self.metadata_by_key: dict[str, ObjectMetadata] = {}

    def create_upload_url(
        self,
        *,
        key: str,
        content_type: str,
        expires_in: int,
    ) -> str:
        self.requests.append(
            {
                "key": key,
                "content_type": content_type,
                "expires_in": expires_in,
            }
        )
        return f"https://storage.test/{key}"

    def get_object_metadata(self, *, key: str) -> ObjectMetadata:
        if key not in self.metadata_by_key:
            raise ObjectNotFound("uploaded object was not found")
        return self.metadata_by_key[key]


class FakeIngestionQueue:
    """Record queued file references without contacting SQS."""

    def __init__(self) -> None:
        self.requests: list[dict[str, str]] = []
        self.unavailable = False

    def enqueue_file(self, *, session_id: str, file_id: str) -> None:
        if self.unavailable:
            raise QueueUnavailable("test queue unavailable")
        self.requests.append({"session_id": session_id, "file_id": file_id})


class FakeSTTQueue(FakeIngestionQueue):
    """Record transcription jobs without contacting SQS."""


def migrate_database(database_url: str) -> None:
    """Apply real schema revisions to the temporary integration-test database."""
    config = Config(Path(__file__).resolve().parents[1] / "alembic.ini")
    config.attributes["database_url"] = database_url
    command.upgrade(config, "head")


def test_upload_request_returns_signed_targets_and_persists_metadata(tmp_path) -> None:
    database_path = tmp_path / "uploads.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)

    settings = Settings(
        environment="test",
        database_url=database_url,
        s3_bucket="private-test-bucket",
        upload_url_ttl_seconds=600,
    )
    storage = FakeObjectStorage()
    app = create_app(settings=settings)
    app.dependency_overrides[get_object_storage] = lambda: storage

    with TestClient(app) as client:
        session_response = client.post("/sessions")
        session_id = session_response.json()["session_id"]
        response = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "notes.md",
                        "content_type": "text/markdown",
                        "size_bytes": 4096,
                    }
                ]
            },
        )

    assert response.status_code == 201
    target = response.json()["targets"][0]
    assert target["filename"] == "notes.md"
    assert target["content_type"] == "text/markdown"
    assert target["expires_in"] == 600
    assert target["upload_url"].startswith("https://storage.test/uploads/")
    assert storage.requests[0]["content_type"] == "text/markdown"
    assert "notes.md" not in str(storage.requests[0]["key"])

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as database_session:
            file_record = database_session.scalar(
                select(SessionFile).where(SessionFile.id == target["file_id"])
            )
            assert file_record is not None
            assert file_record.session_id == session_id
            assert file_record.filename == "notes.md"
            assert file_record.status == "awaiting_upload"
            assert database_session.get(ChatSession, session_id).status == "processing"
    finally:
        engine.dispose()


def test_upload_rejects_unsupported_extensions_without_signing(tmp_path) -> None:
    database_path = tmp_path / "unsupported.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    settings = Settings(environment="test", database_url=database_url)
    storage = FakeObjectStorage()
    app = create_app(settings=settings)
    app.dependency_overrides[get_object_storage] = lambda: storage

    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        response = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "program.exe",
                        "content_type": "application/octet-stream",
                        "size_bytes": 50,
                    }
                ]
            },
        )

    assert response.status_code == 415
    assert storage.requests == []


def test_upload_accepts_pdf_and_records_document_kind(tmp_path) -> None:
    database_path = tmp_path / "pdf-upload.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    storage = FakeObjectStorage()
    app = create_app(settings=Settings(environment="test", database_url=database_url))
    app.dependency_overrides[get_object_storage] = lambda: storage

    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        response = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "report.pdf",
                        "content_type": "application/pdf",
                        "size_bytes": 4096,
                    }
                ]
            },
        )

    assert response.status_code == 201
    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as db:
            file_record = db.get(SessionFile, response.json()["targets"][0]["file_id"])
            assert file_record is not None and file_record.kind == "document"
    finally:
        engine.dispose()


def test_upload_rejects_files_larger_than_the_configured_limit(tmp_path) -> None:
    database_path = tmp_path / "too-large.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    settings = Settings(
        environment="test",
        database_url=database_url,
        max_upload_bytes=100,
    )
    storage = FakeObjectStorage()
    app = create_app(settings=settings)
    app.dependency_overrides[get_object_storage] = lambda: storage

    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        response = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "big.md",
                        "content_type": "text/markdown",
                        "size_bytes": 101,
                    }
                ]
            },
        )

    assert response.status_code == 413
    assert storage.requests == []


def test_upload_completion_checks_s3_then_queues_once(tmp_path) -> None:
    database_path = tmp_path / "confirm-upload.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    app = create_app(settings=Settings(environment="test", database_url=database_url))
    storage = FakeObjectStorage()
    queue = FakeIngestionQueue()
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_ingestion_queue] = lambda: queue

    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        target = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "notes.md",
                        "content_type": "text/markdown",
                        "size_bytes": 4096,
                    }
                ]
            },
        ).json()["targets"][0]
        complete_url = f"/sessions/{session_id}/uploads/{target['file_id']}/complete"

        missing = client.post(complete_url)
        assert missing.status_code == 409
        assert queue.requests == []

        object_key = str(storage.requests[0]["key"])
        storage.metadata_by_key[object_key] = ObjectMetadata(
            size_bytes=4095,
            content_type="text/markdown",
        )
        wrong_size = client.post(complete_url)
        assert wrong_size.status_code == 422
        assert queue.requests == []

        storage.metadata_by_key[object_key] = ObjectMetadata(
            size_bytes=4096,
            content_type="text/markdown",
        )
        queue.unavailable = True
        unavailable = client.post(complete_url)
        assert unavailable.status_code == 503
        assert queue.requests == []

        queue.unavailable = False
        completed = client.post(complete_url)
        repeated = client.post(complete_url)
        engine = create_engine(f"sqlite:///{database_path}")
        try:
            for lifecycle in ("processing", "ready", "failed"):
                with Session(engine) as db:
                    db.get(SessionFile, target["file_id"]).status = lifecycle
                    db.commit()
                retry = client.post(complete_url)
                assert retry.status_code == 202
                assert retry.json()["status"] == lifecycle
            with Session(engine) as db:
                db.get(SessionFile, target["file_id"]).status = "uploaded"
                db.commit()
        finally:
            engine.dispose()

    assert completed.status_code == 202
    assert completed.json()["status"] == "uploaded"
    assert completed.json()["ingestion_job_enqueued"] is True
    assert repeated.status_code == 202
    assert len(queue.requests) == 1
    assert queue.requests[0] == {
        "session_id": session_id,
        "file_id": target["file_id"],
    }

    engine = create_engine(f"sqlite:///{database_path}")
    try:
        with Session(engine) as database_session:
            file_record = database_session.get(SessionFile, target["file_id"])
            assert file_record is not None
            assert file_record.status == "uploaded"
    finally:
        engine.dispose()


def test_audio_upload_queues_stt_after_storage_confirmation(tmp_path) -> None:
    database_path = tmp_path / "audio-upload.db"
    database_url = f"sqlite+aiosqlite:///{database_path}"
    migrate_database(database_url)
    app = create_app(
        settings=Settings(
            environment="test",
            database_url=database_url,
            stt_service_url="https://stt.example.test",
            stt_api_token="test-token",
        )
    )
    storage = FakeObjectStorage()
    ingestion_queue = FakeIngestionQueue()
    stt_queue = FakeSTTQueue()
    app.dependency_overrides[get_object_storage] = lambda: storage
    app.dependency_overrides[get_ingestion_queue] = lambda: ingestion_queue
    app.dependency_overrides[get_stt_queue] = lambda: stt_queue

    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        target = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "meeting.mp3",
                        "content_type": "audio/mpeg",
                        "size_bytes": 4096,
                    }
                ]
            },
        ).json()["targets"][0]
        object_key = str(storage.requests[0]["key"])
        storage.metadata_by_key[object_key] = ObjectMetadata(
            size_bytes=4096,
            content_type="audio/mpeg",
        )
        completed = client.post(
            f"/sessions/{session_id}/uploads/{target['file_id']}/complete"
        )

    assert completed.status_code == 202
    assert stt_queue.requests == [
        {"session_id": session_id, "file_id": target["file_id"]}
    ]
    assert ingestion_queue.requests == []


@pytest.mark.parametrize("content_type", ["application/octet-stream", "text/plain"])
def test_upload_rejects_wrong_markdown_mime_before_signing(tmp_path, content_type):
    url = f"sqlite+aiosqlite:///{tmp_path / 'mime.db'}"
    migrate_database(url)
    storage = FakeObjectStorage()
    app = create_app(settings=Settings(environment="test", database_url=url))
    app.dependency_overrides[get_object_storage] = lambda: storage
    with TestClient(app) as client:
        session_id = client.post("/sessions").json()["session_id"]
        response = client.post(
            f"/sessions/{session_id}/uploads",
            json={
                "files": [
                    {
                        "filename": "notes.md",
                        "content_type": content_type,
                        "size_bytes": 10,
                    }
                ]
            },
        )
    assert response.status_code == 415
    assert storage.requests == []
