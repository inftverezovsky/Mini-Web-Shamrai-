"""add hot path indexes for cached reads

Revision ID: 20260624_0030
Revises: 20260624_0029
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0030"
down_revision = "20260624_0029"
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
    _create_index("ix_users_created_at", "users", ["created_at"])
    _create_index("ix_personal_signals_user_id_lookup", "personal_signals", ["user_id", "id"])
    _create_index("ix_personal_signals_user_type_id", "personal_signals", ["user_id", "type", "id"])
    _create_index("ix_subscriptions_user_created", "subscriptions", ["user_id", "created_at"])
    _create_index("ix_payment_attempts_user_created", "payment_attempts", ["user_id", "created_at"])
    _create_index("ix_match_balance_logs_user_created", "match_balance_logs", ["user_id", "created_at"])


def downgrade() -> None:
    _drop_index("ix_match_balance_logs_user_created", "match_balance_logs")
    _drop_index("ix_payment_attempts_user_created", "payment_attempts")
    _drop_index("ix_subscriptions_user_created", "subscriptions")
    _drop_index("ix_personal_signals_user_type_id", "personal_signals")
    _drop_index("ix_personal_signals_user_id_lookup", "personal_signals")
    _drop_index("ix_users_created_at", "users")
