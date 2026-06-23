"""add forecast fair coefficient and teaser text

Revision ID: 20260613_0022
Revises: 20260612_0021
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260613_0022"
down_revision = "20260612_0021"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _has_table("bets"):
        return
    if not _has_column("bets", "fair_coefficient"):
        op.add_column("bets", sa.Column("fair_coefficient", sa.Numeric(5, 2), nullable=True))
    if not _has_column("bets", "teaser_text"):
        op.add_column("bets", sa.Column("teaser_text", sa.Text(), nullable=True))


def downgrade() -> None:
    if not _has_table("bets"):
        return
    if _has_column("bets", "teaser_text"):
        op.drop_column("bets", "teaser_text")
    if _has_column("bets", "fair_coefficient"):
        op.drop_column("bets", "fair_coefficient")
