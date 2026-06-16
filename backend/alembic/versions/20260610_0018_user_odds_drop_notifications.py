"""add odds drop notification preference to users

Revision ID: 20260610_0018
Revises: 20260609_0017
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_0018"
down_revision = "20260609_0017"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "odds_drop_notifications_enabled"):
        op.add_column(
            "users",
            sa.Column(
                "odds_drop_notifications_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            ),
        )


def downgrade() -> None:
    if _has_column("users", "odds_drop_notifications_enabled"):
        op.drop_column("users", "odds_drop_notifications_enabled")
