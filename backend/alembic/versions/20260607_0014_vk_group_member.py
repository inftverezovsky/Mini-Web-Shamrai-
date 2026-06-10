"""add vk group membership flag to users

Revision ID: 20260607_0014
Revises: 20260607_0013
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0014"
down_revision = "20260607_0013"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "vk_group_member"):
        op.add_column(
            "users",
            sa.Column("vk_group_member", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if _has_column("users", "vk_group_member"):
        op.drop_column("users", "vk_group_member")
