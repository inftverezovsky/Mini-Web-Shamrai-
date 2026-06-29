"""add match-credit promo rewards

Revision ID: 20260629_0034
Revises: 20260624_0033
Create Date: 2026-06-29
"""

from alembic import op
import sqlalchemy as sa


revision = "20260629_0034"
down_revision = "20260624_0033"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column.get("name") == column_name for column in inspector.get_columns(table_name))


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if _has_table("promo_codes"):
        if not _has_column("promo_codes", "reward_type"):
            op.add_column(
                "promo_codes",
                sa.Column(
                    "reward_type",
                    sa.String(),
                    nullable=False,
                    server_default="discount",
                ),
            )
        if not _has_column("promo_codes", "matches_count"):
            op.add_column(
                "promo_codes",
                sa.Column(
                    "matches_count",
                    sa.Integer(),
                    nullable=False,
                    server_default="0",
                ),
            )

    if not _has_table("promo_code_redemptions"):
        op.create_table(
            "promo_code_redemptions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("promo_code_id", sa.Integer(), nullable=False),
            sa.Column("user_id", sa.BigInteger(), nullable=False),
            sa.Column("matches_added", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("redeemed_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.ForeignKeyConstraint(["promo_code_id"], ["promo_codes.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["user_id"], ["users.telegram_id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("promo_code_id", "user_id", name="uq_promo_code_redemptions_code_user"),
        )

    if _has_table("promo_code_redemptions"):
        if not _has_index("promo_code_redemptions", "ix_promo_code_redemptions_promo_code_id"):
            op.create_index(
                "ix_promo_code_redemptions_promo_code_id",
                "promo_code_redemptions",
                ["promo_code_id"],
            )
        if not _has_index("promo_code_redemptions", "ix_promo_code_redemptions_user_id"):
            op.create_index(
                "ix_promo_code_redemptions_user_id",
                "promo_code_redemptions",
                ["user_id"],
            )
        if not _has_index("promo_code_redemptions", "ix_promo_code_redemptions_user_redeemed"):
            op.create_index(
                "ix_promo_code_redemptions_user_redeemed",
                "promo_code_redemptions",
                ["user_id", "redeemed_at"],
            )


def downgrade() -> None:
    if _has_table("promo_code_redemptions"):
        op.drop_table("promo_code_redemptions")

    if not _has_table("promo_codes"):
        return

    bind = op.get_bind()
    columns_to_drop = [
        column_name
        for column_name in ("matches_count", "reward_type")
        if _has_column("promo_codes", column_name)
    ]
    if not columns_to_drop:
        return

    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("promo_codes") as batch_op:
            for column_name in columns_to_drop:
                batch_op.drop_column(column_name)
        return

    for column_name in columns_to_drop:
        op.drop_column("promo_codes", column_name)
