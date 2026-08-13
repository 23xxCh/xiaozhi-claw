"""add built-in voice preview URLs

Revision ID: 20260813_03
Revises: 20260813_02
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_03"
down_revision: str | None = "20260813_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    presets = sa.table(
        "voice_presets",
        sa.column("id", sa.String),
        sa.column("preview_url", sa.String),
    )
    op.execute(
        presets.update()
        .where(presets.c.id == "cherry")
        .values(preview_url="/voice-previews/cherry.mp3")
    )
    op.execute(
        presets.update()
        .where(presets.c.id == "ethan")
        .values(preview_url="/voice-previews/ethan.mp3")
    )


def downgrade() -> None:
    presets = sa.table(
        "voice_presets",
        sa.column("id", sa.String),
        sa.column("preview_url", sa.String),
    )
    op.execute(
        presets.update()
        .where(presets.c.id.in_(["cherry", "ethan"]))
        .values(preview_url=None)
    )
