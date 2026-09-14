"""Persist extracted text as searchable chunks.

Revision ID: 0003_create_document_chunks
Revises: 0002_create_session_files
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003_create_document_chunks"
down_revision: str | None = "0002_create_session_files"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create chunk storage with per-file ordering and cascade cleanup."""
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_file_id", sa.String(length=36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "chunk_index >= 0",
            name="ck_document_chunks_index_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["session_file_id"],
            ["session_files.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "session_file_id",
            "chunk_index",
            name="uq_document_chunks_file_index",
        ),
    )
    op.create_index(
        "ix_document_chunks_session_file_id",
        "document_chunks",
        ["session_file_id"],
        unique=False,
    )


def downgrade() -> None:
    """Remove chunk storage."""
    op.drop_index("ix_document_chunks_session_file_id", table_name="document_chunks")
    op.drop_table("document_chunks")
