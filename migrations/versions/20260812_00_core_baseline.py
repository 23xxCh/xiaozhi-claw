"""Baseline the pre-gateway control-plane schema.

Existing pilot databases must be stamped at this revision before upgrading.
"""

import sqlalchemy as sa
from alembic import op

revision = "20260812_00"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("wechat_openid", sa.String(128), nullable=False),
        sa.Column("adult_confirmed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_wechat_openid", "users", ["wechat_openid"], unique=True)
    op.create_table(
        "devices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("serial_number", sa.String(64), nullable=False),
        sa.Column("credential_hash", sa.String(64), nullable=False),
        sa.Column("board_type", sa.String(64), nullable=False),
        sa.Column("lifecycle", sa.String(40), nullable=False),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id")),
        sa.Column("memory_consent", sa.Boolean(), nullable=False),
        sa.Column("firmware_version", sa.String(32), nullable=False),
        sa.Column("reset_epoch", sa.Integer(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_devices_serial_number", "devices", ["serial_number"], unique=True)
    op.create_index("ix_devices_lifecycle", "devices", ["lifecycle"])
    op.create_table(
        "claims",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_claims_code_hash", "claims", ["code_hash"], unique=True)
    op.create_index("ix_claims_device_id", "claims", ["device_id"])
    op.create_table(
        "entitlements",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plan", sa.String(32), nullable=False),
        sa.Column("monthly_turn_limit", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_entitlements_user_id", "entitlements", ["user_id"])
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("provider_cost_micros", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_device_id", "usage_events", ["device_id"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])
    op.create_table(
        "memory_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("device_id", "key", name="uq_device_memory_key"),
    )
    op.create_index("ix_memory_summaries_user_id", "memory_summaries", ["user_id"])
    op.create_index("ix_memory_summaries_device_id", "memory_summaries", ["device_id"])
    op.create_table(
        "firmware_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("board_type", sa.String(64), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("artifact_url", sa.String(500), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("signature", sa.Text(), nullable=False),
        sa.Column("rollout_percent", sa.Integer(), nullable=False),
        sa.Column("mandatory", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("board_type", "version", name="uq_board_version"),
    )
    op.create_index("ix_firmware_releases_board_type", "firmware_releases", ["board_type"])
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("actor_type", sa.String(32), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_events_action", "audit_events", ["action"])


def downgrade() -> None:
    for table in [
        "audit_events",
        "firmware_releases",
        "memory_summaries",
        "usage_events",
        "entitlements",
        "claims",
        "devices",
        "users",
    ]:
        op.drop_table(table)
