"""Track message leases for worker crash recovery.

Revision ID: 0010_add_message_updated_at
Revises: 0009_allow_variable_embedding_dimensions
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010_add_message_updated_at"
down_revision: str | None = "0009_allow_variable_embedding_dimensions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )


def downgrade() -> None:
    op.drop_column("messages", "updated_at")
