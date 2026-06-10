"""add vk delivery permission flags to users

Revision ID: 20260607_0012
Revises: 20260607_0011
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0012"
down_revision = "20260607_0011"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "vk_messages_allowed"):
        op.add_column(
            "users",
            sa.Column("vk_messages_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if not _has_column("users", "vk_notifications_allowed"):
        op.add_column(
            "users",
            sa.Column("vk_notifications_allowed", sa.Boolean(), nullable=False, server_default=sa.false()),
        )


def downgrade() -> None:
    if _has_column("users", "vk_notifications_allowed"):
        op.drop_column("users", "vk_notifications_allowed")
    if _has_column("users", "vk_messages_allowed"):
        op.drop_column("users", "vk_messages_allowed")
