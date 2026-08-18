"""add profit-target flat subscriptions

Revision ID: 20260802_0041
Revises: 20260729_0040
Create Date: 2026-08-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260802_0041"
down_revision = "20260729_0040"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "subscription_plans",
        sa.Column("entitlement_type", sa.String(length=24), nullable=False, server_default="legacy_match"),
    )
    op.add_column(
        "subscription_plans",
        sa.Column("target_flats", sa.Numeric(12, 2), nullable=True),
    )
    op.add_column("promo_codes", sa.Column("target_flats", sa.Numeric(12, 2), nullable=True))
    op.add_column(
        "promo_code_redemptions",
        sa.Column("target_flats_added", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )
    op.add_column(
        "referral_reward_events",
        sa.Column("target_flats_awarded", sa.Numeric(12, 2), nullable=False, server_default="0"),
    )

    op.create_table(
        "flat_subscriptions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="pending_setup"),
        sa.Column("flat_amount_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("target_flats", sa.Numeric(12, 2), nullable=False, server_default="0.01"),
        sa.Column("profit_rub", sa.Numeric(16, 2), nullable=False, server_default="0"),
        sa.Column("profit_flats", sa.Numeric(16, 6), nullable=False, server_default="0"),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('pending_setup', 'active', 'closing', 'completed', 'cancelled')",
            name="ck_flat_subscriptions_status",
        ),
        sa.CheckConstraint(
            "flat_amount_rub IS NULL OR (flat_amount_rub >= 1 AND flat_amount_rub <= 100000000)",
            name="ck_flat_subscriptions_flat_amount_range",
        ),
        sa.CheckConstraint("target_flats >= 0.01", name="ck_flat_subscriptions_target_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flat_subscriptions_user_id", "flat_subscriptions", ["user_id"])
    op.create_index("ix_flat_subscriptions_status", "flat_subscriptions", ["status"])
    op.create_index("ix_flat_subscriptions_user_status", "flat_subscriptions", ["user_id", "status"])
    op.create_index(
        "uq_flat_subscriptions_one_open_per_user",
        "flat_subscriptions",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending_setup', 'active', 'closing')"),
        sqlite_where=sa.text("status IN ('pending_setup', 'active', 'closing')"),
    )

    op.add_column("subscriptions", sa.Column("flat_subscription_id", sa.Uuid(as_uuid=True), nullable=True))
    op.add_column("subscriptions", sa.Column("target_flats_snapshot", sa.Numeric(12, 2), nullable=True))
    op.create_foreign_key(
        "fk_subscriptions_flat_subscription_id",
        "subscriptions",
        "flat_subscriptions",
        ["flat_subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_subscriptions_flat_subscription_id", "subscriptions", ["flat_subscription_id"])

    op.create_table(
        "flat_subscription_credits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("flat_subscription_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("subscription_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("delta_target_flats", sa.Numeric(12, 2), nullable=False),
        sa.Column("event_type", sa.String(length=48), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["actor_id"], ["users.telegram_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["flat_subscription_id"], ["flat_subscriptions.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subscription_id"], ["subscriptions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_flat_subscription_credits_flat_subscription_id", "flat_subscription_credits", ["flat_subscription_id"])
    op.create_index("ix_flat_subscription_credits_subscription_id", "flat_subscription_credits", ["subscription_id"])
    op.create_index(
        "ix_flat_subscription_credits_subscription_created",
        "flat_subscription_credits",
        ["flat_subscription_id", "created_at"],
    )

    for name, column in (
        ("flat_subscription_id", sa.Column("flat_subscription_id", sa.Uuid(as_uuid=True), nullable=True)),
        ("stake_rub", sa.Column("stake_rub", sa.Numeric(14, 2), nullable=True)),
        ("flat_amount_rub_snapshot", sa.Column("flat_amount_rub_snapshot", sa.Numeric(14, 2), nullable=True)),
        ("stake_flats", sa.Column("stake_flats", sa.Numeric(16, 6), nullable=True)),
        ("coefficient_snapshot", sa.Column("coefficient_snapshot", sa.Numeric(8, 3), nullable=True)),
        ("settled_status", sa.Column("settled_status", sa.String(length=16), nullable=True)),
        ("profit_rub", sa.Column("profit_rub", sa.Numeric(16, 2), nullable=True)),
        ("profit_flats", sa.Column("profit_flats", sa.Numeric(16, 6), nullable=True)),
        ("settled_at", sa.Column("settled_at", sa.DateTime(timezone=True), nullable=True)),
    ):
        op.add_column("user_bets", column)
    op.create_foreign_key(
        "fk_user_bets_flat_subscription_id",
        "user_bets",
        "flat_subscriptions",
        ["flat_subscription_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_user_bets_flat_subscription",
        "user_bets",
        ["flat_subscription_id", "settled_status"],
    )

    for column in (
        sa.Column("flat_subscription_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("stake_rub", sa.Numeric(14, 2), nullable=True),
        sa.Column("stake_flats", sa.Numeric(16, 6), nullable=True),
        sa.Column("stake_input_channel", sa.String(length=16), nullable=True),
        sa.Column("stake_submitted_at", sa.DateTime(timezone=True), nullable=True),
    ):
        op.add_column("forecast_requests", column)
    op.create_foreign_key(
        "fk_forecast_requests_flat_subscription_id",
        "forecast_requests",
        "flat_subscriptions",
        ["flat_subscription_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_forecast_requests_flat_subscription_id", "forecast_requests", ["flat_subscription_id"])

    op.create_table(
        "forecast_stake_input_sessions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("forecast_request_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["forecast_request_id"], ["forecast_requests.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
        sa.CheckConstraint("channel IN ('telegram', 'vk')", name="ck_forecast_stake_input_channel"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel", "user_id", name="uq_forecast_stake_input_channel_user"),
    )
    op.create_index("ix_forecast_stake_input_sessions_user_id", "forecast_stake_input_sessions", ["user_id"])
    op.create_index("ix_forecast_stake_input_sessions_forecast_request_id", "forecast_stake_input_sessions", ["forecast_request_id"])
    op.create_index("ix_forecast_stake_input_expires", "forecast_stake_input_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_forecast_stake_input_expires", table_name="forecast_stake_input_sessions")
    op.drop_index("ix_forecast_stake_input_sessions_forecast_request_id", table_name="forecast_stake_input_sessions")
    op.drop_index("ix_forecast_stake_input_sessions_user_id", table_name="forecast_stake_input_sessions")
    op.drop_table("forecast_stake_input_sessions")

    op.drop_index("ix_forecast_requests_flat_subscription_id", table_name="forecast_requests")
    op.drop_constraint("fk_forecast_requests_flat_subscription_id", "forecast_requests", type_="foreignkey")
    for name in ("stake_submitted_at", "stake_input_channel", "stake_flats", "stake_rub", "flat_subscription_id"):
        op.drop_column("forecast_requests", name)

    op.drop_index("ix_user_bets_flat_subscription", table_name="user_bets")
    op.drop_constraint("fk_user_bets_flat_subscription_id", "user_bets", type_="foreignkey")
    for name in (
        "settled_at",
        "profit_flats",
        "profit_rub",
        "settled_status",
        "coefficient_snapshot",
        "stake_flats",
        "flat_amount_rub_snapshot",
        "stake_rub",
        "flat_subscription_id",
    ):
        op.drop_column("user_bets", name)

    op.drop_index("ix_flat_subscription_credits_subscription_created", table_name="flat_subscription_credits")
    op.drop_index("ix_flat_subscription_credits_subscription_id", table_name="flat_subscription_credits")
    op.drop_index("ix_flat_subscription_credits_flat_subscription_id", table_name="flat_subscription_credits")
    op.drop_table("flat_subscription_credits")

    op.drop_index("ix_subscriptions_flat_subscription_id", table_name="subscriptions")
    op.drop_constraint("fk_subscriptions_flat_subscription_id", "subscriptions", type_="foreignkey")
    op.drop_column("subscriptions", "target_flats_snapshot")
    op.drop_column("subscriptions", "flat_subscription_id")

    op.drop_index("uq_flat_subscriptions_one_open_per_user", table_name="flat_subscriptions")
    op.drop_index("ix_flat_subscriptions_user_status", table_name="flat_subscriptions")
    op.drop_index("ix_flat_subscriptions_status", table_name="flat_subscriptions")
    op.drop_index("ix_flat_subscriptions_user_id", table_name="flat_subscriptions")
    op.drop_table("flat_subscriptions")

    op.drop_column("subscription_plans", "target_flats")
    op.drop_column("subscription_plans", "entitlement_type")
    op.drop_column("referral_reward_events", "target_flats_awarded")
    op.drop_column("promo_code_redemptions", "target_flats_added")
    op.drop_column("promo_codes", "target_flats")
