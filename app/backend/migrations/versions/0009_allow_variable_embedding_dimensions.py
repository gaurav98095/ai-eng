"""Allow pgvector rows from compatible embedding models with other dimensions.

Revision ID: 0009_allow_variable_embedding_dimensions
Revises: 0008_add_audio_transcripts
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0009_allow_variable_embedding_dimensions"
down_revision: str | None = "0008_add_audio_transcripts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the old 384-dimension type modifier and its fixed-dimension index."""
    if op.get_bind().dialect.name != "postgresql":
        return
    # HNSW indexes require a dimension-specific operator class. Keep correct
    # model-scoped cosine search while model choice is configurable; a later
    # index migration can pin a chosen production embedding dimension.
    op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
    op.execute("ALTER TABLE chunks ALTER COLUMN embedding TYPE vector")


def downgrade() -> None:
    """Do not truncate non-384-dimensional vectors during a downgrade."""
    # Reverting safely requires deleting/re-ingesting embeddings first, so this
    # migration intentionally leaves the broader type in place.
    pass
