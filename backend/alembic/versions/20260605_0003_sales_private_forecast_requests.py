"""add sales private forecast requests

Revision ID: 20260605_0003
Revises: 20260605_0002
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0003"
down_revision = "20260605_0002"
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
    if not _has_column("bets", "delivery_mode"):
        op.add_column(
            "bets",
            sa.Column("delivery_mode", sa.String(), nullable=False, server_default="feed"),
        )

    if not _has_table("forecast_requests"):
        op.create_table(
            "forecast_requests",
            sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("bet_id", sa.Uuid(as_uuid=True), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("status", sa.String(), nullable=False, server_default="announced"),
            sa.Column("delivery_method", sa.String(), nullable=True),
            sa.Column("handled_by", sa.BigInteger(), nullable=True),
            sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("balance_before", sa.Integer(), nullable=True),
            sa.Column("balance_after", sa.Integer(), nullable=True),
            sa.Column("no_balance_warning", sa.Boolean(), nullable=False, server_default=sa.text("false")),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
            sa.ForeignKeyConstraint(["bet_id"], ["bets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("bet_id", "user_id", name="uq_forecast_requests_bet_user"),
        )
        op.create_index("ix_forecast_requests_bet_id", "forecast_requests", ["bet_id"])
        op.create_index("ix_forecast_requests_user_id", "forecast_requests", ["user_id"])
        op.create_index("ix_forecast_requests_handled_by", "forecast_requests", ["handled_by"])
        op.create_index(
            "ix_forecast_requests_status_created",
            "forecast_requests",
            ["status", "created_at"],
        )


def downgrade() -> None:
    if _has_table("forecast_requests"):
        op.drop_index("ix_forecast_requests_status_created", table_name="forecast_requests")
        op.drop_index("ix_forecast_requests_handled_by", table_name="forecast_requests")
        op.drop_index("ix_forecast_requests_user_id", table_name="forecast_requests")
        op.drop_index("ix_forecast_requests_bet_id", table_name="forecast_requests")
        op.drop_table("forecast_requests")

    if _has_column("bets", "delivery_mode"):
        op.drop_column("bets", "delivery_mode")
