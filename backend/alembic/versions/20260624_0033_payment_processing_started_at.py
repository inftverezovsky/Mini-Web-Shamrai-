"""add payment processing started timestamp

Revision ID: 20260624_0033
Revises: 20260624_0032
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0033"
down_revision = "20260624_0032"
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


def upgrade() -> None:
    if not _has_table("payment_attempts") or _has_column("payment_attempts", "processing_started_at"):
        return
    op.add_column("payment_attempts", sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    if not _has_table("payment_attempts") or not _has_column("payment_attempts", "processing_started_at"):
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("payment_attempts") as batch_op:
            batch_op.drop_column("processing_started_at")
        return
    op.drop_column("payment_attempts", "processing_started_at")
