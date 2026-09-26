"""Record the object identity checked during upload confirmation.

Revision ID: 0011_add_uploaded_object_etag
Revises: 0010_add_message_updated_at
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_add_uploaded_object_etag"
down_revision: str | None = "0010_add_message_updated_at"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("files", sa.Column("object_etag", sa.String(length=255)))


def downgrade() -> None:
    op.drop_column("files", "object_etag")
