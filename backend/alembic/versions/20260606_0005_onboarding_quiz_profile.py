"""extend onboarding quiz profile

Revision ID: 20260606_0005
Revises: 20260605_0004
Create Date: 2026-06-06
"""

from alembic import op
import sqlalchemy as sa


revision = "20260606_0005"
down_revision = "20260605_0004"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "bankroll_size"):
        op.add_column("users", sa.Column("bankroll_size", sa.String(), nullable=True))

    if not _has_column("users", "primary_bookmaker"):
        op.add_column("users", sa.Column("primary_bookmaker", sa.String(), nullable=True))

    if not _has_column("users", "currency_preference"):
        op.add_column(
            "users",
            sa.Column("currency_preference", sa.String(), nullable=False, server_default="RUB"),
        )


def downgrade() -> None:
    if _has_column("users", "currency_preference"):
        op.drop_column("users", "currency_preference")

    if _has_column("users", "primary_bookmaker"):
        op.drop_column("users", "primary_bookmaker")

    if _has_column("users", "bankroll_size"):
        op.drop_column("users", "bankroll_size")
