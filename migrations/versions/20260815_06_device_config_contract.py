"""add generated device configuration contract fields

Revision ID: 20260815_06
Revises: 20260813_05
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260815_06"
down_revision: str | None = "20260813_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("hardware_profile_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("display_profile_id", sa.String(80), nullable=True))
        batch.add_column(sa.Column("profile_schema_version", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("profile_sha256", sa.String(64), nullable=True))
        batch.add_column(
            sa.Column(
                "device_config_schema_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            )
        )

    with op.batch_alter_table("device_configurations") as batch:
        batch.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch.add_column(
            sa.Column("desired_values", sa.JSON(), nullable=False, server_default="{}")
        )
        batch.add_column(sa.Column("applied_values", sa.JSON(), nullable=True))

    configurations = sa.table(
        "device_configurations",
        sa.column("device_id", sa.String()),
        sa.column("speaker_volume", sa.Integer()),
        sa.column("screen_brightness", sa.Integer()),
        sa.column("applied_speaker_volume", sa.Integer()),
        sa.column("applied_screen_brightness", sa.Integer()),
        sa.column("desired_values", sa.JSON()),
        sa.column("applied_values", sa.JSON()),
    )
    connection = op.get_bind()
    rows = connection.execute(
        sa.select(
            configurations.c.device_id,
            configurations.c.speaker_volume,
            configurations.c.screen_brightness,
            configurations.c.applied_speaker_volume,
            configurations.c.applied_screen_brightness,
        )
    ).mappings()
    for row in rows:
        desired = {
            "audio.speaker_volume": row["speaker_volume"],
            "display.brightness": row["screen_brightness"],
            "audio.wake_threshold": 15,
            "audio.vad_mode": "normal",
            "audio.vad_min_noise_ms": 1200,
            "display.lip_sync_noise_floor": 180,
            "display.lip_sync_reference_amplitude": 5000,
        }
        applied = None
        if row["applied_speaker_volume"] is not None:
            applied = {
                "audio.speaker_volume": row["applied_speaker_volume"],
                "display.brightness": row["applied_screen_brightness"],
            }
        connection.execute(
            configurations.update()
            .where(configurations.c.device_id == row["device_id"])
            .values(desired_values=desired, applied_values=applied)
        )


def downgrade() -> None:
    with op.batch_alter_table("device_configurations") as batch:
        batch.drop_column("applied_values")
        batch.drop_column("desired_values")
        batch.drop_column("schema_version")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("device_config_schema_version")
        batch.drop_column("profile_sha256")
        batch.drop_column("profile_schema_version")
        batch.drop_column("display_profile_id")
        batch.drop_column("hardware_profile_id")
