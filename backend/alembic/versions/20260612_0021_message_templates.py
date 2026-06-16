"""add editable message templates

Revision ID: 20260612_0021
Revises: 20260612_0020
Create Date: 2026-06-12
"""

from alembic import op
import sqlalchemy as sa


revision = "20260612_0021"
down_revision = "20260612_0020"
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


def upgrade() -> None:
    if not _has_table("message_templates"):
        op.create_table(
            "message_templates",
            sa.Column("key", sa.String(), nullable=False),
            sa.Column("title", sa.String(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("body", sa.Text(), nullable=False),
            sa.Column("variables", sa.JSON(), nullable=False, server_default="[]"),
            sa.Column("updated_by", sa.BigInteger(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["updated_by"], ["users.telegram_id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("key"),
        )
    if _has_table("message_templates") and not _has_index("message_templates", "ix_message_templates_updated_by"):
        op.create_index("ix_message_templates_updated_by", "message_templates", ["updated_by"])


def downgrade() -> None:
    if _has_table("message_templates"):
        if _has_index("message_templates", "ix_message_templates_updated_by"):
            op.drop_index("ix_message_templates_updated_by", table_name="message_templates")
        op.drop_table("message_templates")
