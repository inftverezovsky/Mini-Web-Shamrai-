"""add telegram profile photo url to users

Revision ID: 20260607_0011
Revises: 20260607_0010
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0011"
down_revision = "20260607_0010"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "photo_url"):
        op.add_column("users", sa.Column("photo_url", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "photo_url"):
        op.drop_column("users", "photo_url")
