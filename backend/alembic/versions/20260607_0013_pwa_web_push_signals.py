"""add pwa web push subscriptions and personal signal history

Revision ID: 20260607_0013
Revises: 20260607_0012
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0013"
down_revision = "20260607_0012"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_column("users", "web_push_subscription"):
        op.add_column("users", sa.Column("web_push_subscription", sa.JSON(), nullable=True))

    if not _has_table("personal_signals"):
        op.create_table(
            "personal_signals",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("type", sa.String(), nullable=False, server_default="signal"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )

    op.create_index(
        "ix_personal_signals_user_created",
        "personal_signals",
        ["user_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index("ix_personal_signals_user_id", "personal_signals", ["user_id"], unique=False, if_not_exists=True)
    op.create_index("ix_personal_signals_type", "personal_signals", ["type"], unique=False, if_not_exists=True)


def downgrade() -> None:
    if _has_table("personal_signals"):
        op.drop_index("ix_personal_signals_type", table_name="personal_signals", if_exists=True)
        op.drop_index("ix_personal_signals_user_id", table_name="personal_signals", if_exists=True)
        op.drop_index("ix_personal_signals_user_created", table_name="personal_signals", if_exists=True)
        op.drop_table("personal_signals")

    if _has_column("users", "web_push_subscription"):
        op.drop_column("users", "web_push_subscription")
