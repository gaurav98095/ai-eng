"""Persistence model for extracted document text."""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from edgentrag.core.models import Base


class DocumentChunk(Base):
    """Extracted text stored in deterministic, searchable file chunks."""

    __tablename__ = "document_chunks"
    __table_args__ = (
        UniqueConstraint(
            "session_file_id",
            "chunk_index",
            name="uq_document_chunks_file_index",
        ),
        CheckConstraint(
            "chunk_index >= 0", name="ck_document_chunks_index_nonnegative"
        ),
    )

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid4()),
    )
    session_file_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("session_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    chunk_index: Mapped[int] = mapped_column(nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
