"""The database tables.

Four now rather than three, and the new one matters: `chunks` holds the text of
every passage in the database.

Version 1 spread a chunk across two places — a line in a .jsonl file in storage
and a vector in Chroma — so answering a question meant asking Chroma which
chunks won and then reading a file out of S3 to find out what was in them. Here
the text is a row, and retrieval fetches it with one indexed query.

Where the vectors live
----------------------
Still in Chroma, beside the embedding service, because that service stores them
itself and offers no way to hand them back. That is the one thing this design
would change if it could touch the model services, and it is written up in the
README under "The one change the services would need". The consequence to know
about is that the index does not survive the GPU runtime — re-uploading rebuilds
it, and nothing else is lost, because the text is here.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def new_id() -> str:
    return str(uuid.uuid4())


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --- status vocabulary ------------------------------------------------------
SESSION_CREATED = "created"
SESSION_PROCESSING = "processing"
SESSION_READY = "ready"
SESSION_FAILED = "failed"

FILE_PENDING = "pending"
FILE_PROCESSING = "processing"
FILE_DONE = "done"
FILE_FAILED = "failed"

MESSAGE_PENDING = "pending"
MESSAGE_ANSWERING = "answering"
MESSAGE_DONE = "done"
MESSAGE_FAILED = "failed"

ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class Session(Base):
    """One person's upload-and-chat session."""

    __tablename__ = "sessions"

    id = Column(String(36), primary_key=True, default=new_id)
    owner_id = Column(String(128), nullable=True, index=True)   # from the JWT, once there is one

    status = Column(String(20), nullable=False, default=SESSION_CREATED)
    error = Column(Text, nullable=True)

    # How a session knows it is finished without a coordinator. After each
    # file settles, the worker recounts the settled files and stores the
    # total; whoever brings the two to equality marks the session ready.
    #
    # Recounted rather than incremented, deliberately. See _finish_file in
    # workers/ingest.py -- an increment runs once per delivery attempt rather
    # than once per file, so a retried file is counted twice.
    files_total = Column(Integer, nullable=False, default=0)
    files_done = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now(),
                        onupdate=func.now())


class File(Base):
    """One uploaded file, and how far through processing it is."""

    __tablename__ = "files"

    id = Column(String(36), primary_key=True, default=new_id)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    filename = Column(String(400), nullable=False)
    kind = Column(String(20), nullable=False)
    raw_key = Column(String(600), nullable=False)
    text_key = Column(String(600), nullable=True)

    status = Column(String(20), nullable=False, default=FILE_PENDING)
    chunk_count = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now(),
                        onupdate=func.now())


class Chunk(Base):
    """A passage of text, addressable by the key the vector store knows it by.

    `chunk_key` is what ties this row to its vector. The embedding service
    stores that key alongside the vector, returns it from /retrieve, and we look
    the text up here. Deterministic — `{file_id}:{ordinal:04d}` — so re-indexing
    a file updates rows rather than duplicating them.
    """

    __tablename__ = "chunks"

    id = Column(String(36), primary_key=True, default=new_id)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False)
    file_id = Column(String(36), ForeignKey("files.id", ondelete="CASCADE"), nullable=False)

    chunk_key = Column(String(80), nullable=False)

    source = Column(String(400), nullable=False)     # the original filename
    section = Column(String(400), nullable=True)     # a heading trail, or a timestamp range
    kind = Column(String(20), nullable=False)
    ordinal = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)

    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now())

    __table_args__ = (
        Index("ix_chunks_session", "session_id"),
        Index("ix_chunks_file", "file_id"),
        # Retrieval looks chunks up by key within a session, and this is also
        # what makes re-indexing an upsert instead of a duplicate.
        UniqueConstraint("session_id", "chunk_key", name="uq_chunks_session_key"),
    )


class Message(Base):
    """One thing said -- by the user or by the model."""

    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=new_id)
    session_id = Column(String(36), ForeignKey("sessions.id", ondelete="CASCADE"),
                        nullable=False, index=True)

    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=True)
    status = Column(String(20), nullable=False, default=MESSAGE_PENDING)
    sources = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), default=now_utc, server_default=func.now())
