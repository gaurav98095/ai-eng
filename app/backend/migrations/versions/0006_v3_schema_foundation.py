"""Align the initial schema with the v3 persistence contract.

This migration is deliberately explicit: deployments run Alembic once as a
release step; API and worker startup never create or mutate schema.
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006_v3_schema_foundation"
down_revision: str | None = "0005_create_messages"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "chat_sessions" in tables and "sessions" not in tables:
        op.rename_table("chat_sessions", "sessions")
    if "session_files" in tables and "files" not in tables:
        op.rename_table("session_files", "files")
    if "document_chunks" in tables and "chunks" not in tables:
        op.rename_table("document_chunks", "chunks")

    for column, column_type in (
        ("owner_id", sa.String(255)),
        ("error", sa.Text()),
        ("files_total", sa.Integer()),
        ("files_done", sa.Integer()),
        ("updated_at", sa.DateTime(timezone=True)),
    ):
        if column not in {c["name"] for c in sa.inspect(bind).get_columns("sessions")}:
            op.add_column("sessions", sa.Column(column, column_type, nullable=True))
    op.create_index("ix_sessions_owner_id", "sessions", ["owner_id"], if_not_exists=True)
    op.execute("UPDATE sessions SET files_total = 0 WHERE files_total IS NULL")
    op.execute("UPDATE sessions SET files_done = 0 WHERE files_done IS NULL")
    op.execute("UPDATE sessions SET updated_at = created_at WHERE updated_at IS NULL")
    if bind.dialect.name != "sqlite":
        op.alter_column("sessions", "files_total", nullable=False, server_default="0")
        op.alter_column("sessions", "files_done", nullable=False, server_default="0")
        op.alter_column("sessions", "updated_at", nullable=False)

    file_columns = {c["name"] for c in sa.inspect(bind).get_columns("files")}
    if "raw_key" not in file_columns and "object_key" in file_columns:
        op.alter_column("files", "object_key", new_column_name="raw_key")
    for column, column_type in (
        ("text_key", sa.String(512)), ("kind", sa.String(32)),
        ("chunk_count", sa.Integer()), ("error", sa.Text()),
        ("updated_at", sa.DateTime(timezone=True)),
    ):
        if column not in {c["name"] for c in sa.inspect(bind).get_columns("files")}:
            op.add_column("files", sa.Column(column, column_type, nullable=True))
    op.execute("UPDATE files SET kind = 'document' WHERE kind IS NULL")
    op.execute("UPDATE files SET chunk_count = 0 WHERE chunk_count IS NULL")
    op.execute("UPDATE files SET updated_at = created_at WHERE updated_at IS NULL")
    if bind.dialect.name != "sqlite":
        op.alter_column("files", "kind", nullable=False, server_default="document")
        op.alter_column("files", "chunk_count", nullable=False, server_default="0")
        op.alter_column("files", "updated_at", nullable=False)


def downgrade() -> None:
    # Keep downgrade conservative; production data should not be renamed back.
    pass
