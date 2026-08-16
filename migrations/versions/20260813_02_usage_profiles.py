"""Add adult and youth usage profiles without changing existing ownership."""

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "20260813_02"
down_revision = "20260812_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "usage_profiles",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False, server_default="adult"),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("age_band", sa.String(16), nullable=True),
        sa.Column("guardian_consent_version", sa.String(32), nullable=True),
        sa.Column("guardian_consent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("memory_consent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("quiet_start_minute", sa.Integer(), nullable=False, server_default="1320"),
        sa.Column("quiet_end_minute", sa.Integer(), nullable=False, server_default="420"),
        sa.Column("daily_limit_minutes", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("continuous_reminder_minutes", sa.Integer(), nullable=False, server_default="30"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_user_id", "kind", "display_name", name="uq_profile_identity"),
    )
    op.create_index("ix_usage_profiles_owner_user_id", "usage_profiles", ["owner_user_id"])
    op.create_index("ix_usage_profiles_kind", "usage_profiles", ["kind"])

    with op.batch_alter_table("voice_presets") as batch:
        batch.add_column(sa.Column("preview_url", sa.String(500), nullable=True))
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("usage_profile_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_agents_usage_profile", "usage_profiles", ["usage_profile_id"], ["id"]
        )
        batch.create_index("ix_agents_usage_profile_id", ["usage_profile_id"])
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("active_profile_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_devices_active_profile", "usage_profiles", ["active_profile_id"], ["id"]
        )
        batch.create_index("ix_devices_active_profile_id", ["active_profile_id"])
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.add_column(sa.Column("usage_profile_id", sa.String(36), nullable=True))
        batch.create_foreign_key(
            "fk_conversations_usage_profile",
            "usage_profiles",
            ["usage_profile_id"],
            ["id"],
        )
        batch.create_index("ix_conversation_sessions_usage_profile_id", ["usage_profile_id"])

    bind = op.get_bind()
    now = datetime.now(UTC)
    profiles: dict[str, str] = {}
    users = bind.execute(sa.text("SELECT id, display_name FROM users")).mappings().all()
    for user in users:
        profile_id = str(uuid.uuid4())
        profiles[str(user["id"])] = profile_id
        bind.execute(
            sa.text(
                """
                INSERT INTO usage_profiles (
                    id, owner_user_id, kind, display_name, age_band,
                    guardian_consent_version, guardian_consent_at, memory_consent,
                    quiet_start_minute, quiet_end_minute, daily_limit_minutes,
                    continuous_reminder_minutes, created_at, updated_at
                ) VALUES (
                    :id, :owner_user_id, 'adult', :display_name, NULL,
                    NULL, NULL, 0, 1320, 420, 90, 30, :created_at, :updated_at
                )
                """
            ),
            {
                "id": profile_id,
                "owner_user_id": user["id"],
                "display_name": user["display_name"] or "本人",
                "created_at": now,
                "updated_at": now,
            },
        )
    for user_id, profile_id in profiles.items():
        bind.execute(
            sa.text("UPDATE agents SET usage_profile_id=:profile_id WHERE owner_user_id=:user_id"),
            {"profile_id": profile_id, "user_id": user_id},
        )
        bind.execute(
            sa.text(
                "UPDATE devices SET active_profile_id=:profile_id WHERE owner_user_id=:user_id"
            ),
            {"profile_id": profile_id, "user_id": user_id},
        )
        bind.execute(
            sa.text(
                "UPDATE conversation_sessions SET usage_profile_id=:profile_id "
                "WHERE user_id=:user_id"
            ),
            {"profile_id": profile_id, "user_id": user_id},
        )

    with op.batch_alter_table("agents") as batch:
        batch.alter_column("usage_profile_id", existing_type=sa.String(36), nullable=False)
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.alter_column("usage_profile_id", existing_type=sa.String(36), nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("conversation_sessions") as batch:
        batch.drop_index("ix_conversation_sessions_usage_profile_id")
        batch.drop_constraint("fk_conversations_usage_profile", type_="foreignkey")
        batch.drop_column("usage_profile_id")
    with op.batch_alter_table("devices") as batch:
        batch.drop_index("ix_devices_active_profile_id")
        batch.drop_constraint("fk_devices_active_profile", type_="foreignkey")
        batch.drop_column("active_profile_id")
    with op.batch_alter_table("agents") as batch:
        batch.drop_index("ix_agents_usage_profile_id")
        batch.drop_constraint("fk_agents_usage_profile", type_="foreignkey")
        batch.drop_column("usage_profile_id")
    with op.batch_alter_table("voice_presets") as batch:
        batch.drop_column("preview_url")
    op.drop_table("usage_profiles")
