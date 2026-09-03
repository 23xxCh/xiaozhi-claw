"""add indexes for hot control-plane queries

Revision ID: 20260903_08
Revises: 20260829_07
Create Date: 2026-09-03
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260903_08"
down_revision: str | None = "20260829_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_usage_events_user_kind_created_at",
        "usage_events",
        ["user_id", "kind", "created_at"],
    )
    op.create_index(
        "ix_usage_events_kind_created_at",
        "usage_events",
        ["kind", "created_at"],
    )
    op.create_index(
        "ix_conversation_sessions_user_started_at",
        "conversation_sessions",
        ["user_id", "started_at"],
    )
    op.create_index(
        "ix_provider_usage_created_at_operation",
        "provider_usage",
        ["created_at", "operation"],
    )
    op.create_index(
        "ix_device_sessions_device_connected_at",
        "device_sessions",
        ["device_id", "connected_at"],
    )
    op.create_index(
        "ix_device_sessions_status_heartbeat_at",
        "device_sessions",
        ["status", "heartbeat_at"],
    )
    op.create_index(
        "ix_device_commands_status_created_at",
        "device_commands",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_device_commands_status_created_at", table_name="device_commands")
    op.drop_index("ix_device_sessions_status_heartbeat_at", table_name="device_sessions")
    op.drop_index("ix_device_sessions_device_connected_at", table_name="device_sessions")
    op.drop_index("ix_provider_usage_created_at_operation", table_name="provider_usage")
    op.drop_index("ix_conversation_sessions_user_started_at", table_name="conversation_sessions")
    op.drop_index("ix_usage_events_kind_created_at", table_name="usage_events")
    op.drop_index("ix_usage_events_user_kind_created_at", table_name="usage_events")
