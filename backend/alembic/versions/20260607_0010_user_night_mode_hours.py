"""add night mode quiet hours to users

Revision ID: 20260607_0010
Revises: 20260607_0009
Create Date: 2026-06-07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260607_0010"
down_revision = "20260607_0009"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "night_mode_start"):
        op.add_column(
            "users",
            sa.Column("night_mode_start", sa.String(length=5), nullable=False, server_default="23:00"),
        )
    if not _has_column("users", "night_mode_end"):
        op.add_column(
            "users",
            sa.Column("night_mode_end", sa.String(length=5), nullable=False, server_default="08:00"),
        )


def downgrade() -> None:
    if _has_column("users", "night_mode_end"):
        op.drop_column("users", "night_mode_end")
    if _has_column("users", "night_mode_start"):
        op.drop_column("users", "night_mode_start")
