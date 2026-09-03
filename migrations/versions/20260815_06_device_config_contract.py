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


def _column_names(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _add_missing_columns(table_name: str, columns: Sequence[sa.Column]) -> None:
    existing = _column_names(table_name)
    missing = [column for column in columns if column.name not in existing]
    if not missing:
        return
    with op.batch_alter_table(table_name) as batch:
        for column in missing:
            batch.add_column(column)


def upgrade() -> None:
    # MySQL DDL is non-transactional. Keep additions idempotent so a release can
    # resume safely if an earlier ALTER TABLE succeeded before a later one failed.
    _add_missing_columns(
        "devices",
        (
            sa.Column("hardware_profile_id", sa.String(80), nullable=True),
            sa.Column("display_profile_id", sa.String(80), nullable=True),
            sa.Column("profile_schema_version", sa.Integer(), nullable=True),
            sa.Column("profile_sha256", sa.String(64), nullable=True),
            sa.Column(
                "device_config_schema_version",
                sa.Integer(),
                nullable=False,
                server_default="1",
            ),
        ),
    )

    _add_missing_columns(
        "device_configurations",
        (
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
            # MySQL rejects defaults on JSON columns. Add nullable, backfill,
            # then enforce NOT NULL after every existing row has a value.
            sa.Column("desired_values", sa.JSON(), nullable=True),
            sa.Column("applied_values", sa.JSON(), nullable=True),
        ),
    )

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

    with op.batch_alter_table("device_configurations") as batch:
        batch.alter_column(
            "desired_values",
            existing_type=sa.JSON(),
            nullable=False,
        )


def downgrade() -> None:
    configuration_columns = _column_names("device_configurations")
    with op.batch_alter_table("device_configurations") as batch:
        for column_name in ("applied_values", "desired_values", "schema_version"):
            if column_name in configuration_columns:
                batch.drop_column(column_name)
    device_columns = _column_names("devices")
    with op.batch_alter_table("devices") as batch:
        for column_name in (
            "device_config_schema_version",
            "profile_sha256",
            "profile_schema_version",
            "display_profile_id",
            "hardware_profile_id",
        ):
            if column_name in device_columns:
                batch.drop_column(column_name)
