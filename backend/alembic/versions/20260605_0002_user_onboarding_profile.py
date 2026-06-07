"""add user onboarding profile fields

Revision ID: 20260605_0002
Revises: 20260605_0001
Create Date: 2026-06-05
"""

from alembic import op
import sqlalchemy as sa


revision = "20260605_0002"
down_revision = "20260605_0001"
branch_labels = None
depends_on = None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    if not _has_column("users", "experience_level"):
        op.add_column("users", sa.Column("experience_level", sa.String(), nullable=True))

    if not _has_column("users", "favorite_sports"):
        op.add_column(
            "users",
            sa.Column(
                "favorite_sports",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            ),
        )

    if not _has_column("users", "risk_tolerance"):
        op.add_column("users", sa.Column("risk_tolerance", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("users", "risk_tolerance"):
        op.drop_column("users", "risk_tolerance")

    if _has_column("users", "favorite_sports"):
        op.drop_column("users", "favorite_sports")

    if _has_column("users", "experience_level"):
        op.drop_column("users", "experience_level")
