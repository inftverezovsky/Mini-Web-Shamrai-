"""add marketing widgets and referral risk statuses

Revision ID: 20260703_0038
Revises: 20260703_0037
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260703_0038"
down_revision = "20260703_0037"
branch_labels = None
depends_on = None


def _table_names() -> set[str]:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return set(inspector.get_table_names())


def _has_table(table_name: str) -> bool:
    return table_name in _table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if _has_table("referral_reward_events"):
        if not _has_column("referral_reward_events", "status"):
            op.add_column(
                "referral_reward_events",
                sa.Column("status", sa.String(), nullable=False, server_default="approved"),
            )
        if not _has_column("referral_reward_events", "risk_score"):
            op.add_column(
                "referral_reward_events",
                sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
            )
        if not _has_column("referral_reward_events", "risk_reasons"):
            op.add_column(
                "referral_reward_events",
                sa.Column("risk_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            )
        if not _has_column("referral_reward_events", "reviewed_by"):
            op.add_column("referral_reward_events", sa.Column("reviewed_by", sa.BigInteger(), nullable=True))
            op.create_foreign_key(
                "fk_referral_reward_events_reviewed_by_users",
                "referral_reward_events",
                "users",
                ["reviewed_by"],
                ["telegram_id"],
                ondelete="SET NULL",
            )
        if not _has_column("referral_reward_events", "reviewed_at"):
            op.add_column("referral_reward_events", sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))

        op.create_index(
            "ix_referral_reward_status_created",
            "referral_reward_events",
            ["status", "created_at"],
            unique=False,
        )
        op.create_index(
            "ix_referral_reward_events_reviewed_by",
            "referral_reward_events",
            ["reviewed_by"],
            unique=False,
        )

    if not _has_table("marketing_widget_configs"):
        op.create_table(
            "marketing_widget_configs",
            sa.Column("key", sa.String(length=64), nullable=False),
            sa.Column("is_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("audience", sa.String(length=32), nullable=False, server_default="all"),
            sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cooldown_hours", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("per_user_limit", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("global_daily_limit", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("reward_type", sa.String(length=32), nullable=False, server_default="none"),
            sa.Column("reward_value", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("promo_valid_hours", sa.Integer(), nullable=False, server_default="24"),
            sa.Column("settings_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["updated_by"], ["users.telegram_id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("key"),
        )
        op.create_index("ix_marketing_widget_configs_key", "marketing_widget_configs", ["key"])
        op.create_index("ix_marketing_widget_configs_updated_by", "marketing_widget_configs", ["updated_by"])

    if not _has_table("marketing_reward_events"):
        op.create_table(
            "marketing_reward_events",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("widget_key", sa.String(length=64), nullable=False),
            sa.Column("reward_type", sa.String(length=32), nullable=False, server_default="none"),
            sa.Column("reward_value", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("promo_code_id", sa.Integer(), nullable=True),
            sa.Column("risk_status", sa.String(length=16), nullable=False, server_default="approved"),
            sa.Column("risk_reasons", sa.JSON(), nullable=False, server_default=sa.text("'[]'")),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_marketing_reward_events_id", "marketing_reward_events", ["id"])
        op.create_index("ix_marketing_reward_events_user_id", "marketing_reward_events", ["user_id"])
        op.create_index("ix_marketing_reward_events_widget_key", "marketing_reward_events", ["widget_key"])
        op.create_index("ix_marketing_reward_events_promo_code_id", "marketing_reward_events", ["promo_code_id"])
        op.create_index(
            "ix_marketing_reward_user_widget_created",
            "marketing_reward_events",
            ["user_id", "widget_key", "created_at"],
        )
        op.create_index(
            "ix_marketing_reward_widget_created",
            "marketing_reward_events",
            ["widget_key", "created_at"],
        )
        op.create_index(
            "ix_marketing_reward_risk_status_created",
            "marketing_reward_events",
            ["risk_status", "created_at"],
        )


def downgrade() -> None:
    if _has_table("marketing_reward_events"):
        op.drop_table("marketing_reward_events")
    if _has_table("marketing_widget_configs"):
        op.drop_table("marketing_widget_configs")
    if _has_table("referral_reward_events"):
        op.drop_index("ix_referral_reward_status_created", table_name="referral_reward_events")
        op.drop_index("ix_referral_reward_events_reviewed_by", table_name="referral_reward_events")
        for column_name in ("reviewed_at", "reviewed_by", "risk_reasons", "risk_score", "status"):
            if _has_column("referral_reward_events", column_name):
                op.drop_column("referral_reward_events", column_name)
