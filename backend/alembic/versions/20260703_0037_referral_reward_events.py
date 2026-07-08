"""add referral reward events

Revision ID: 20260703_0037
Revises: 20260702_0036
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260703_0037"
down_revision = "20260702_0036"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if _has_table("referral_reward_events"):
        return

    op.create_table(
        "referral_reward_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("referrer_user_id", sa.BigInteger(), nullable=False),
        sa.Column("referred_user_id", sa.BigInteger(), nullable=False),
        sa.Column("source_payment_attempt_id", sa.Uuid(), nullable=True),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("discount_percent_snapshot", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("matches_awarded", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["referred_user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["referrer_user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_payment_attempt_id"], ["payment_attempts.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("referrer_user_id", "referred_user_id", name="uq_referral_reward_referrer_referred"),
    )
    op.create_index("ix_referral_reward_events_id", "referral_reward_events", ["id"])
    op.create_index("ix_referral_reward_events_referrer_user_id", "referral_reward_events", ["referrer_user_id"])
    op.create_index("ix_referral_reward_events_referred_user_id", "referral_reward_events", ["referred_user_id"])
    op.create_index("ix_referral_reward_events_source_payment_attempt_id", "referral_reward_events", ["source_payment_attempt_id"])
    op.create_index(
        "ix_referral_reward_referrer_created",
        "referral_reward_events",
        ["referrer_user_id", "created_at"],
    )


def downgrade() -> None:
    if _has_table("referral_reward_events"):
        op.drop_table("referral_reward_events")
