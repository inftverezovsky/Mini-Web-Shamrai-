"""add package balance for supercompensation

Revision ID: 20260606_0006
Revises: 20260606_0005
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260606_0006"
down_revision = "20260606_0005"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    bind = op.get_bind()
    if not _has_column("users", "purchased_bets_balance"):
        op.add_column(
            "users",
            sa.Column("purchased_bets_balance", sa.Integer(), nullable=False, server_default="0"),
        )

    users_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "matches_remaining" in users_columns and "purchased_bets_balance" in users_columns:
        op.execute(
            "UPDATE users SET purchased_bets_balance = COALESCE(matches_remaining, 0) "
            "WHERE COALESCE(purchased_bets_balance, 0) = 0 AND COALESCE(matches_remaining, 0) > 0"
        )
    if "free_bets_available" in users_columns:
        op.execute("UPDATE users SET free_bets_available = 0 WHERE COALESCE(free_bets_available, 0) < 0")
        if bind.dialect.name != "sqlite":
            op.alter_column("users", "free_bets_available", server_default="0")


def downgrade() -> None:
    bind = op.get_bind()
    users_columns = {column["name"] for column in sa.inspect(bind).get_columns("users")}
    if "free_bets_available" in users_columns and bind.dialect.name != "sqlite":
        op.alter_column("users", "free_bets_available", server_default="1")
    if _has_column("users", "purchased_bets_balance"):
        op.drop_column("users", "purchased_bets_balance")
