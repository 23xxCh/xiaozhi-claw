"""Persist one service gift per device and exclude previously activated devices.

Revision ID: 20260907_10
Revises: 20260907_09
"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260907_10"
down_revision: str | None = "20260907_09"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    connection = op.get_bind()
    columns = {column["name"] for column in sa.inspect(connection).get_columns("devices")}
    # MySQL DDL can survive a failed migration. Re-running must finish the backfill.
    with op.batch_alter_table("devices") as batch:
        if "service_gift_status" not in columns:
            batch.add_column(
                sa.Column(
                    "service_gift_status", sa.String(24), nullable=False, server_default="eligible"
                )
            )
        if "service_gift_granted_at" not in columns:
            batch.add_column(sa.Column("service_gift_granted_at", sa.DateTime(timezone=True)))

    # No historical activation timestamp exists. Do not invent a grant date or
    # alter existing entitlements; only clear eligibility where activation is evidenced.
    connection.execute(
        sa.text("""
        UPDATE devices SET service_gift_status = 'legacy-consumed'
        WHERE service_gift_status = 'eligible' AND (
            lifecycle <> 'factory-unclaimed' OR owner_user_id IS NOT NULL OR reset_epoch <> 0
            OR id IN (SELECT device_id FROM claims WHERE consumed_at IS NOT NULL)
        )
    """)
    )
    audited_device_ids: set[str] = set()
    for raw in connection.scalars(
        sa.text("""
        SELECT payload_json FROM audit_events
        WHERE action IN ('device.claimed', 'device.unbound', 'device.service-gift-granted')
    """)
    ):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        device_id = payload.get("device_id") if isinstance(payload, dict) else None
        if isinstance(device_id, str):
            audited_device_ids.add(device_id)
    devices = sa.table("devices", sa.column("id"), sa.column("service_gift_status"))
    ids = sorted(audited_device_ids)
    for offset in range(0, len(ids), 500):
        connection.execute(
            devices.update()
            .where(
                devices.c.id.in_(ids[offset : offset + 500]),
                devices.c.service_gift_status == "eligible",
            )
            .values(service_gift_status="legacy-consumed")
        )


def downgrade() -> None:
    if op.get_bind().scalar(
        sa.text("SELECT count(*) FROM devices WHERE service_gift_status = 'granted'")
    ):
        raise RuntimeError("Cannot downgrade after device service gifts have been granted")
    with op.batch_alter_table("devices") as batch:
        batch.drop_column("service_gift_granted_at")
        batch.drop_column("service_gift_status")
