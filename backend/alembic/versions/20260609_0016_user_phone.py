"""add phone to users

Revision ID: 20260609_0016
Revises: 20260608_0015
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260609_0016"
down_revision = "20260608_0015"
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
    if not _has_column("users", "phone"):
        op.add_column("users", sa.Column("phone", sa.String(), nullable=True))

    if not _has_index("users", "ix_users_phone"):
        op.create_index("ix_users_phone", "users", ["phone"], unique=True)


def downgrade() -> None:
    if _has_index("users", "ix_users_phone"):
        op.drop_index("ix_users_phone", table_name="users")

    if _has_column("users", "phone"):
        op.drop_column("users", "phone")
