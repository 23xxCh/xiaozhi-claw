"""align agent preset nullability with the runtime contract

Revision ID: 20260829_07
Revises: 20260815_06
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260829_07"
down_revision: str | None = "20260815_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    agents = sa.table(
        "agents",
        sa.column("model_preset_id", sa.String(64)),
        sa.column("voice_preset_id", sa.String(64)),
    )
    connection = op.get_bind()
    connection.execute(
        agents.update()
        .where(agents.c.model_preset_id.is_(None))
        .values(model_preset_id="fast-chat")
    )
    connection.execute(
        agents.update()
        .where(agents.c.voice_preset_id.is_(None))
        .values(voice_preset_id="cherry")
    )

    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "model_preset_id",
            existing_type=sa.String(64),
            nullable=False,
        )
        batch.alter_column(
            "voice_preset_id",
            existing_type=sa.String(64),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "voice_preset_id",
            existing_type=sa.String(64),
            nullable=True,
        )
        batch.alter_column(
            "model_preset_id",
            existing_type=sa.String(64),
            nullable=True,
        )
