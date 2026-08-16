"""add email one-time-code authentication

Revision ID: 20260813_04
Revises: 20260813_03
Create Date: 2026-08-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260813_04"
down_revision: str | None = "20260813_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch:
        batch.alter_column("wechat_openid", existing_type=sa.String(128), nullable=True)
        batch.add_column(sa.Column("email", sa.String(320), nullable=True))
        batch.add_column(sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_index("ix_users_email", ["email"], unique=True)

    op.create_table(
        "email_login_challenges",
        sa.Column("id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("request_ip_hash", sa.String(64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_login_challenges_email", "email_login_challenges", ["email"])
    op.create_index(
        "ix_email_login_challenges_request_ip_hash",
        "email_login_challenges",
        ["request_ip_hash"],
    )
    op.create_index(
        "ix_email_login_challenges_expires_at", "email_login_challenges", ["expires_at"]
    )
    op.create_index(
        "ix_email_login_challenges_created_at", "email_login_challenges", ["created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_email_login_challenges_created_at", table_name="email_login_challenges")
    op.drop_index("ix_email_login_challenges_expires_at", table_name="email_login_challenges")
    op.drop_index(
        "ix_email_login_challenges_request_ip_hash", table_name="email_login_challenges"
    )
    op.drop_index("ix_email_login_challenges_email", table_name="email_login_challenges")
    op.drop_table("email_login_challenges")

    users = sa.table(
        "users",
        sa.column("id", sa.String),
        sa.column("wechat_openid", sa.String),
    )
    op.execute(
        users.update()
        .where(users.c.wechat_openid.is_(None))
        .values(wechat_openid=sa.literal("email:") + users.c.id)
    )
    with op.batch_alter_table("users") as batch:
        batch.drop_index("ix_users_email")
        batch.drop_column("email_verified_at")
        batch.drop_column("email")
        batch.alter_column("wechat_openid", existing_type=sa.String(128), nullable=False)
