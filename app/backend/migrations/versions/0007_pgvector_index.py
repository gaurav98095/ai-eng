"""Enable pgvector and add the v3 similarity index on PostgreSQL."""

from collections.abc import Sequence

from alembic import op

revision: str = "0007_pgvector_index"
down_revision: str | None = "0006_v3_schema_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Revision 0004 stored embeddings as JSON for SQLite compatibility. The
    # v3 PostgreSQL path must use a native fixed-width vector before creating
    # an HNSW index. PostgreSQL can cast JSON arrays (e.g. ``[0.1, 0.2]``)
    # through text to pgvector; NULL values remain NULL.
    op.execute(
        "ALTER TABLE chunks "
        "ALTER COLUMN embedding TYPE vector(384) "
        "USING CASE WHEN embedding IS NULL THEN NULL "
        "ELSE embedding::text::vector END"
    )
    # HNSW is safe to build concurrently during a later release window; the
    # normal migration keeps transactional semantics for first-time installs.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw "
        "ON chunks USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw")
