"""add user vk photo url

Revision ID: 20260624_0029
Revises: 20260624_0028
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0029"
down_revision = "20260624_0028"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "vk_photo_url"):
        op.add_column("users", sa.Column("vk_photo_url", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "vk_photo_url"):
        op.drop_column("users", "vk_photo_url")
