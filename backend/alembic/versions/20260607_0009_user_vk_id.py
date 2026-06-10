"""add vk id link to users

Revision ID: 20260607_0009
Revises: 20260607_0008
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0009"
down_revision = "20260607_0008"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return index_name in {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    if not _has_column("users", "vk_user_id"):
        op.add_column("users", sa.Column("vk_user_id", sa.String(), nullable=True))

    if not _has_index("users", "ix_users_vk_user_id"):
        op.create_index("ix_users_vk_user_id", "users", ["vk_user_id"], unique=True)


def downgrade() -> None:
    if _has_index("users", "ix_users_vk_user_id"):
        op.drop_index("ix_users_vk_user_id", table_name="users")

    if _has_column("users", "vk_user_id"):
        op.drop_column("users", "vk_user_id")
