"""Persist complete speech-to-text output alongside searchable chunks."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008_add_audio_transcripts"
down_revision: str | None = "0007_pgvector_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("files")}
    if "transcript" not in columns:
        op.add_column("files", sa.Column("transcript", sa.Text(), nullable=True))


def downgrade() -> None:
    if "transcript" in {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("files")
    }:
        op.drop_column("files", "transcript")
