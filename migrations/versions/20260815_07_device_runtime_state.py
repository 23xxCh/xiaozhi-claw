"""add device runtime state for acknowledged standby

Revision ID: 20260815_07
Revises: 20260815_06
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_07"
down_revision: str | None = "20260815_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(
            sa.Column(
                "runtime_state",
                sa.String(24),
                nullable=False,
                server_default="offline",
            )
        )
        batch.add_column(sa.Column("runtime_state_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("runtime_reason", sa.String(40)))


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("runtime_reason")
        batch.drop_column("runtime_state_at")
        batch.drop_column("runtime_state")
