"""add client metadata fields

Revision ID: 20260605_0004
Revises: 20260605_0003
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0004"
down_revision = "20260605_0003"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "client_group"):
        op.add_column("users", sa.Column("client_group", sa.String(), nullable=True))

    if not _has_column("users", "client_tag"):
        op.add_column("users", sa.Column("client_tag", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "client_tag"):
        op.drop_column("users", "client_tag")

    if _has_column("users", "client_group"):
        op.drop_column("users", "client_group")
