"""Persistence model for one user's document/chat session."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from edgentrag.core.models import Base


class ChatSession(Base):
    """A container that will later own uploaded files and chat messages."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('created', 'processing', 'ready', 'failed')",
            name="ck_chat_sessions_status",
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )


class SessionFile(Base):
    """Metadata for one file whose bytes will upload directly to storage."""

    __tablename__ = "session_files"
    __table_args__ = (
        CheckConstraint(
            "status IN ('awaiting_upload', 'uploaded', "
            "'processing', 'ready', 'failed')",
            name="ck_session_files_status",
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
        ForeignKey("chat_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(nullable=False)
    object_key: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
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
