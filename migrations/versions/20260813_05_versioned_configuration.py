"""add versioned device and agent configuration

Revision ID: 20260813_05
Revises: 20260813_04
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_05"
down_revision: str | None = "20260813_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            sa.Column("llm_temperature", sa.Float(), nullable=False, server_default="0.6")
        )
        batch.add_column(
            sa.Column("tts_speech_rate", sa.Float(), nullable=False, server_default="1.0")
        )

    op.create_table(
        "device_configurations",
        sa.Column("device_id", sa.String(36), nullable=False),
        sa.Column("desired_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("applied_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("speaker_volume", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("screen_brightness", sa.Integer(), nullable=False, server_default="75"),
        sa.Column("applied_speaker_volume", sa.Integer(), nullable=True),
        sa.Column("applied_screen_brightness", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(80), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"]),
        sa.PrimaryKeyConstraint("device_id"),
    )

    with op.batch_alter_table("device_commands") as batch:
        batch.add_column(sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("device_commands") as batch:
        batch.drop_column("applied_at")
    op.drop_table("device_configurations")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("tts_speech_rate")
        batch.drop_column("llm_temperature")

