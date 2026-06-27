"""add identity device links

Revision ID: 20260623_0027
Revises: 20260623_0026
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0027"
down_revision = "20260623_0026"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("identity_device_links"):
        op.create_table(
            "identity_device_links",
            sa.Column("device_key_hash", sa.String(length=64), nullable=False),
            sa.Column("source_user_id", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["source_user_id"], ["users.telegram_id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("device_key_hash"),
        )
    op.create_index(
        "ix_identity_device_links_source_user_id",
        "identity_device_links",
        ["source_user_id"],
        if_not_exists=True,
    )


def downgrade() -> None:
    if _has_table("identity_device_links"):
        op.drop_index("ix_identity_device_links_source_user_id", table_name="identity_device_links", if_exists=True)
        op.drop_table("identity_device_links")
