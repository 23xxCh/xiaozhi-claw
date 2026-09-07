"""Add selectable realtime voice routes and explicit provider metering.

Revision ID: 20260907_09
Revises: 20260903_08
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_09"
down_revision: str | None = "20260903_08"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CASCADE_FIELDS = {
    "asr_provider": 40,
    "asr_model": 120,
    "llm_provider": 40,
    "llm_model": 120,
    "tts_provider": 40,
    "tts_model": 120,
}


def upgrade() -> None:
    with op.batch_alter_table("model_presets") as batch:
        batch.add_column(
            sa.Column("route_kind", sa.String(24), nullable=False, server_default="cascade")
        )
        batch.add_column(sa.Column("realtime_provider", sa.String(40), nullable=True))
        batch.add_column(sa.Column("realtime_model", sa.String(120), nullable=True))
        for name, length in CASCADE_FIELDS.items():
            batch.alter_column(name, existing_type=sa.String(length), nullable=True)
    with op.batch_alter_table("provider_usage") as batch:
        batch.alter_column("cost_micros", existing_type=sa.Integer(), nullable=True)
        batch.add_column(
            sa.Column("cost_status", sa.String(16), nullable=False, server_default="estimated")
        )
        batch.add_column(sa.Column("billing_event_key", sa.String(160), nullable=True))
        batch.add_column(sa.Column("provider_request_id", sa.String(160), nullable=True))
        batch.add_column(sa.Column("usage_details_json", sa.Text(), nullable=True))
        batch.add_column(sa.Column("pricing_version", sa.String(64), nullable=True))
        batch.create_unique_constraint("uq_provider_usage_billing_event_key", ["billing_event_key"])
    # Backfill without a TEXT server default, which differs between MySQL versions.
    op.execute(sa.text("UPDATE provider_usage SET usage_details_json = '{}'"))
    with op.batch_alter_table("provider_usage") as batch:
        batch.alter_column("usage_details_json", existing_type=sa.Text(), nullable=False)


def downgrade() -> None:
    connection = op.get_bind()
    has_realtime = connection.scalar(
        sa.text("SELECT count(*) FROM model_presets WHERE route_kind <> 'cascade'")
    )
    unknown_cost = connection.scalar(
        sa.text("SELECT count(*) FROM provider_usage WHERE cost_micros IS NULL")
    )
    if has_realtime or unknown_cost:
        raise RuntimeError("Cannot downgrade while realtime presets or unknown-cost usage exist")
    with op.batch_alter_table("provider_usage") as batch:
        batch.drop_constraint("uq_provider_usage_billing_event_key", type_="unique")
        for name in (
            "pricing_version",
            "usage_details_json",
            "provider_request_id",
            "billing_event_key",
            "cost_status",
        ):
            batch.drop_column(name)
        batch.alter_column("cost_micros", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("model_presets") as batch:
        batch.drop_column("realtime_model")
        batch.drop_column("realtime_provider")
        batch.drop_column("route_kind")
        for name, length in CASCADE_FIELDS.items():
            batch.alter_column(name, existing_type=sa.String(length), nullable=False)
