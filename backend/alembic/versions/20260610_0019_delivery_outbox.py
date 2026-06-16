"""add delivery outbox

Revision ID: 20260610_0019
Revises: 20260610_0018
Create Date: 2026-06-10
"""

from alembic import op
import sqlalchemy as sa


revision = "20260610_0019"
down_revision = "20260610_0018"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("delivery_outbox"):
        return

    op.create_table(
        "delivery_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False, server_default="pending"),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("personal_signal_id", sa.Integer(), nullable=True),
        sa.Column("forecast_request_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.Column("dedupe_key", sa.String(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["forecast_request_id"], ["forecast_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["personal_signal_id"], ["personal_signals.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="uq_delivery_outbox_dedupe_key"),
    )
    op.create_index("ix_delivery_outbox_channel", "delivery_outbox", ["channel"])
    op.create_index("ix_delivery_outbox_channel_status", "delivery_outbox", ["channel", "status"])
    op.create_index("ix_delivery_outbox_forecast_request_id", "delivery_outbox", ["forecast_request_id"])
    op.create_index("ix_delivery_outbox_personal_signal_id", "delivery_outbox", ["personal_signal_id"])
    op.create_index("ix_delivery_outbox_status", "delivery_outbox", ["status"])
    op.create_index("ix_delivery_outbox_status_next_attempt", "delivery_outbox", ["status", "next_attempt_at"])
    op.create_index("ix_delivery_outbox_user_id", "delivery_outbox", ["user_id"])


def downgrade() -> None:
    if _has_table("delivery_outbox"):
        op.drop_table("delivery_outbox")
