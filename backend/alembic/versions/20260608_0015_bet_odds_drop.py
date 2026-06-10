"""add odds drop fields to bets

Revision ID: 20260608_0015
Revises: 20260607_0014
Create Date: 2026-06-08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260608_0015"
down_revision = "20260607_0014"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("bets", "odds_dropped_to"):
        op.add_column("bets", sa.Column("odds_dropped_to", sa.Numeric(5, 2), nullable=True))
    if not _has_column("bets", "odds_drop_notified_at"):
        op.add_column("bets", sa.Column("odds_drop_notified_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if _has_column("bets", "odds_drop_notified_at"):
        op.drop_column("bets", "odds_drop_notified_at")
    if _has_column("bets", "odds_dropped_to"):
        op.drop_column("bets", "odds_dropped_to")
