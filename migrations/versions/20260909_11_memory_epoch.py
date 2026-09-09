"""Fence memory mutations against delayed summaries across gateway processes."""

import sqlalchemy as sa
from alembic import op

revision = "20260909_11"
down_revision = "20260907_10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("agents")}
    if "memory_epoch" not in columns:
        op.add_column("agents", sa.Column("memory_epoch", sa.Integer(),
                                         nullable=False, server_default="0"))


def downgrade() -> None:
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("memory_epoch")
