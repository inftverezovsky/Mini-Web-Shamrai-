"""store custom emoji entities for feed text publications

Revision ID: 20260729_0040
Revises: 20260703_0039
Create Date: 2026-07-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260729_0040"
down_revision = "20260703_0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bets",
        sa.Column(
            "event_name_entities",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "bets",
        sa.Column(
            "description_entities",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("bets", "description_entities")
    op.drop_column("bets", "event_name_entities")
