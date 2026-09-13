"""Create file metadata records for direct uploads.

Revision ID: 0002_create_session_files
Revises: 0001_create_chat_sessions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002_create_session_files"
down_revision: str | None = "0001_create_chat_sessions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create session_files and its session lookup index."""
    op.create_table(
        "session_files",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=255), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("object_key", sa.String(length=512), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('awaiting_upload', 'uploaded', "
            "'processing', 'ready', 'failed')",
            name="ck_session_files_status",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_session_files_positive_size"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("object_key"),
    )
    op.create_index(
        "ix_session_files_session_id",
        "session_files",
        ["session_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the upload metadata table."""
    op.drop_index("ix_session_files_session_id", table_name="session_files")
    op.drop_table("session_files")
