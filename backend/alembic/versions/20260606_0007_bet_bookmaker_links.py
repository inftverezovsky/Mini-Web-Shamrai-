"""add per-bookmaker forecast links

Revision ID: 20260606_0007
Revises: 20260606_0006
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260606_0007"
down_revision = "20260606_0006"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if _has_column("bets", "bookmaker_links"):
        return
    if bind.dialect.name == "postgresql":
        op.add_column(
            "bets",
            sa.Column("bookmaker_links", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
        )
    else:
        op.add_column(
            "bets",
            sa.Column("bookmaker_links", sa.JSON(), nullable=False, server_default="[]"),
        )


def downgrade() -> None:
    if _has_column("bets", "bookmaker_links"):
        op.drop_column("bets", "bookmaker_links")
