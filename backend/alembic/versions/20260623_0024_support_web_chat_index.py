"""add support web chat signal index

Revision ID: 20260623_0024
Revises: 20260613_0023
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0024"
down_revision = "20260613_0023"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("personal_signals"):
        return
    op.create_index(
        "ix_personal_signals_type_created",
        "personal_signals",
        ["type", "created_at"],
        unique=False,
        if_not_exists=True,
    )


def downgrade() -> None:
    if not _has_table("personal_signals"):
        return
    op.drop_index("ix_personal_signals_type_created", table_name="personal_signals", if_exists=True)
