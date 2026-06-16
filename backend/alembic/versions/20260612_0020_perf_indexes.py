"""add performance indexes for hot paths

Revision ID: 20260612_0020
Revises: 20260610_0019
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_0020"
down_revision = "20260610_0019"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def _create_index(index_name: str, table_name: str, columns: list[str]) -> None:
    if _has_table(table_name) and not _has_index(table_name, index_name):
        op.create_index(index_name, table_name, columns)


def _drop_index(index_name: str, table_name: str) -> None:
    if _has_table(table_name) and _has_index(table_name, index_name):
        op.drop_index(index_name, table_name=table_name)


def upgrade() -> None:
    _create_index("ix_bets_status_delivery_created", "bets", ["status", "delivery_mode", "created_at"])
    _create_index("ix_bets_status_resolved", "bets", ["status", "resolved_at"])
    _create_index("ix_bets_author_status_resolved", "bets", ["author_id", "status", "resolved_at"])
    _create_index("ix_user_bets_user_taken", "user_bets", ["user_id", "taken_at"])
    _create_index("ix_user_bets_bet_user", "user_bets", ["bet_id", "user_id"])
    _create_index("ix_forecast_requests_bet_status", "forecast_requests", ["bet_id", "status"])


def downgrade() -> None:
    _drop_index("ix_forecast_requests_bet_status", "forecast_requests")
    _drop_index("ix_user_bets_bet_user", "user_bets")
    _drop_index("ix_user_bets_user_taken", "user_bets")
    _drop_index("ix_bets_author_status_resolved", "bets")
    _drop_index("ix_bets_status_resolved", "bets")
    _drop_index("ix_bets_status_delivery_created", "bets")
