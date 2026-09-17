"""Persistence model for one user's document/chat session."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from edgentrag.core.models import Base


class ChatSession(Base):
    """A container that will later own uploaded files and chat messages."""

    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'processing', 'ready', 'failed')",
            name="ck_sessions_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="created",
    )
    owner_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    files_total: Mapped[int] = mapped_column(nullable=False, default=0)
    files_done: Mapped[int] = mapped_column(nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC),
    )


class SessionFile(Base):
    """Metadata for one file whose bytes will upload directly to storage."""

    __tablename__ = "files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_upload', 'uploaded', "
            "'processing', 'ready', 'failed')",
            name="ck_files_status",
        ),
        CheckConstraint("size_bytes > 0", name="ck_session_files_positive_size"),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    session_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    object_key: Mapped[str] = mapped_column(
        "raw_key", String(512), unique=True, nullable=False
    )
    text_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    transcript: Mapped[str | None] = mapped_column(Text, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="document")
    chunk_count: Mapped[int] = mapped_column(nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(
        String(24),
        nullable=False,
        default="awaiting_upload",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC),
    )


class Message(Base):
    """One user question or assistant answer in a session."""

    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_messages_role",
        ),
        CheckConstraint(
            "status IN ('pending', 'answering', 'done', 'failed')",
            name="ck_messages_status",
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid4())
    )
    session_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    sources: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(UTC),
    )
