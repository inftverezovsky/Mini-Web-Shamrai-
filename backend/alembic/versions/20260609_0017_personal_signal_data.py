"""add data payload to personal signals

Revision ID: 20260609_0017
Revises: 20260609_0016
Create Date: 2026-06-09
"""

from alembic import op
import sqlalchemy as sa


revision = "20260609_0017"
down_revision = "20260609_0016"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("personal_signals", "data"):
        op.add_column("personal_signals", sa.Column("data", sa.JSON(), nullable=True))


def downgrade() -> None:
    if _has_column("personal_signals", "data"):
        op.drop_column("personal_signals", "data")
