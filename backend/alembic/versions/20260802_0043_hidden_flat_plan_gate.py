"""add hidden flat-plan checkout allowlist

Revision ID: 20260802_0043
Revises: 20260802_0042
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_0043"
down_revision = "20260802_0042"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "subscription_plan_checkout_allowlist",
        sa.Column(
            "plan_id",
            sa.Integer(),
            sa.ForeignKey("subscription_plans.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "user_id",
            sa.BigInteger(),
            sa.ForeignKey("users.telegram_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_index(
        "ix_subscription_plan_checkout_allowlist_user",
        "subscription_plan_checkout_allowlist",
        ["user_id", "plan_id"],
        unique=False,
    )
    op.add_column(
        "quizzes",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=True,
            server_default=sa.func.now(),
        ),
    )
    op.add_column(
        "quizzes",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.alter_column(
        "quizzes",
        "is_active",
        existing_type=sa.Boolean(),
        server_default=None,
    )
    op.alter_column(
        "quizzes",
        "bet_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "quizzes",
        "bet_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_column("quizzes", "is_active")
    op.drop_column("quizzes", "created_at")
    op.drop_index(
        "ix_subscription_plan_checkout_allowlist_user",
        table_name="subscription_plan_checkout_allowlist",
    )
    op.drop_table("subscription_plan_checkout_allowlist")
    op.drop_column("subscription_plans", "is_hidden")
