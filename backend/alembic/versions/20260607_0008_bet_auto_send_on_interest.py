"""add auto-send flag for private forecast requests

Revision ID: 20260607_0008
Revises: 20260606_0007
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0008"
down_revision = "20260606_0007"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_column("bets", "auto_send_on_interest"):
        return
    op.add_column(
        "bets",
        sa.Column(
            "auto_send_on_interest",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    if _has_column("bets", "auto_send_on_interest"):
        op.drop_column("bets", "auto_send_on_interest")
