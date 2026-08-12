"""Add agents, presets, gateway sessions, staff roles and privacy-safe sessions."""

import sqlalchemy as sa
from alembic import op

revision = "20260812_01"
down_revision = "20260812_00"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("wechat_unionid", sa.String(128), nullable=True))
    op.add_column(
        "users", sa.Column("display_name", sa.String(80), nullable=False, server_default="微信用户")
    )
    op.add_column("users", sa.Column("terms_version", sa.String(32), nullable=True))
    op.add_column("users", sa.Column("privacy_version", sa.String(32), nullable=True))
    op.add_column(
        "users", sa.Column("ai_disclosure_confirmed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index("ix_users_wechat_unionid", "users", ["wechat_unionid"], unique=True)

    op.create_table(
        "model_presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(240), nullable=False, server_default=""),
        sa.Column("asr_provider", sa.String(40), nullable=False),
        sa.Column("asr_model", sa.String(120), nullable=False),
        sa.Column("llm_provider", sa.String(40), nullable=False),
        sa.Column("llm_model", sa.String(120), nullable=False),
        sa.Column("tts_provider", sa.String(40), nullable=False),
        sa.Column("tts_model", sa.String(120), nullable=False),
        sa.Column(
            "asr_cost_micros_per_minute",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "llm_input_cost_micros_per_million_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "llm_output_cost_micros_per_million_tokens",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "tts_cost_micros_per_10k_chars",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "voice_presets",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("language", sa.String(32), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("voice", sa.String(120), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "agents",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("avatar_url", sa.String(500), nullable=True),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("model_preset_id", sa.String(64), sa.ForeignKey("model_presets.id")),
        sa.Column("voice_preset_id", sa.String(64), sa.ForeignKey("voice_presets.id")),
        sa.Column("memory_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("tools_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("config_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_agents_owner_user_id", "agents", ["owner_user_id"])
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("active_agent_id", sa.String(36), nullable=True))
        batch.add_column(
            sa.Column("name", sa.String(80), nullable=False, server_default="Hensun Desk")
        )
        batch.add_column(
            sa.Column("hardware_version", sa.String(32), nullable=False, server_default="v1")
        )
        batch.add_column(
            sa.Column("ota_auto_update", sa.Boolean(), nullable=False, server_default=sa.true())
        )
        batch.create_foreign_key("fk_devices_active_agent", "agents", ["active_agent_id"], ["id"])
        batch.create_index("ix_devices_active_agent_id", ["active_agent_id"])

    op.create_table(
        "agent_memories",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("encrypted_value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("agent_id", "key", name="uq_agent_memory_key"),
    )
    op.create_index("ix_agent_memories_user_id", "agent_memories", ["user_id"])
    op.create_index("ix_agent_memories_agent_id", "agent_memories", ["agent_id"])
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("turn_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_audio_latency_ms", sa.Integer(), nullable=True),
        sa.Column("provider_cost_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("end_reason", sa.String(64), nullable=False, server_default="normal"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    for column in ["user_id", "agent_id", "device_id"]:
        op.create_index(f"ix_conversation_sessions_{column}", "conversation_sessions", [column])
    op.create_table(
        "encrypted_session_summaries",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column(
            "session_id", sa.String(36), sa.ForeignKey("conversation_sessions.id"), nullable=False
        ),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("agent_id", sa.String(36), sa.ForeignKey("agents.id"), nullable=False),
        sa.Column("encrypted_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_encrypted_session_summaries_session_id",
        "encrypted_session_summaries",
        ["session_id"],
        unique=True,
    )
    op.create_index(
        "ix_encrypted_session_summaries_user_id", "encrypted_session_summaries", ["user_id"]
    )
    op.create_index(
        "ix_encrypted_session_summaries_agent_id", "encrypted_session_summaries", ["agent_id"]
    )
    op.create_table(
        "provider_usage",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("session_id", sa.String(36), sa.ForeignKey("conversation_sessions.id")),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("provider", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("operation", sa.String(24), nullable=False),
        sa.Column("input_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_units", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cost_micros", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("error_code", sa.String(80), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    for column in ["session_id", "user_id", "device_id"]:
        op.create_index(f"ix_provider_usage_{column}", "provider_usage", [column])
    op.create_table(
        "device_sessions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("gateway_id", sa.String(80), nullable=False),
        sa.Column("connection_id", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("firmware_version", sa.String(32), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_device_sessions_device_id", "device_sessions", ["device_id"])
    op.create_index("ix_device_sessions_gateway_id", "device_sessions", ["gateway_id"])
    op.create_index(
        "ix_device_sessions_connection_id", "device_sessions", ["connection_id"], unique=True
    )
    op.create_table(
        "device_commands",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("device_id", sa.String(36), sa.ForeignKey("devices.id"), nullable=False),
        sa.Column("command_type", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(80), nullable=True),
    )
    op.create_index("ix_device_commands_device_id", "device_commands", ["device_id"])
    op.create_index("ix_device_commands_status", "device_commands", ["status"])
    op.create_table(
        "staff_users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("username", sa.String(80), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("role", sa.String(24), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_staff_users_username", "staff_users", ["username"], unique=True)
    op.create_index("ix_staff_users_role", "staff_users", ["role"])


def downgrade() -> None:
    for table in [
        "staff_users",
        "device_commands",
        "device_sessions",
        "provider_usage",
        "encrypted_session_summaries",
        "conversation_sessions",
        "agent_memories",
    ]:
        op.drop_table(table)
    with op.batch_alter_table("devices") as batch:
        batch.drop_index("ix_devices_active_agent_id")
        batch.drop_constraint("fk_devices_active_agent", type_="foreignkey")
        for column in ["ota_auto_update", "hardware_version", "name", "active_agent_id"]:
            batch.drop_column(column)
    op.drop_table("agents")
    op.drop_table("voice_presets")
    op.drop_table("model_presets")
    op.drop_index("ix_users_wechat_unionid", table_name="users")
    op.drop_column("users", "terms_accepted_at")
    op.drop_column("users", "ai_disclosure_confirmed_at")
    op.drop_column("users", "privacy_version")
    op.drop_column("users", "terms_version")
    op.drop_column("users", "display_name")
    op.drop_column("users", "wechat_unionid")
