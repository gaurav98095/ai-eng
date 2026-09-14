"""Persist model embeddings alongside extracted chunks.

Revision ID: 0004_add_chunk_embeddings
Revises: 0003_create_document_chunks
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004_add_chunk_embeddings"
down_revision: str | None = "0003_create_document_chunks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add nullable vector data so existing text-only chunks remain valid."""
    op.add_column(
        "document_chunks",
        sa.Column("embedding", sa.JSON(), nullable=True),
    )
    op.add_column(
        "document_chunks",
        sa.Column("embedding_model", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove vectors while preserving each chunk's extracted text."""
    op.drop_column("document_chunks", "embedding_model")
    op.drop_column("document_chunks", "embedding")
