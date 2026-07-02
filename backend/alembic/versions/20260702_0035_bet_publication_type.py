"""Add publication type to bets.

Revision ID: 20260702_0035
Revises: 20260629_0034
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0035"
down_revision = "20260629_0034"
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
    if not _has_table("bets"):
        return
    if not _has_column("bets", "publication_type"):
        op.add_column(
            "bets",
            sa.Column(
                "publication_type",
                sa.String(),
                nullable=False,
                server_default="forecast",
            ),
        )
        op.alter_column("bets", "publication_type", server_default=None)
    if not _has_index("bets", "ix_bets_publication_status_created"):
        op.create_index(
            "ix_bets_publication_status_created",
            "bets",
            ["publication_type", "status", "created_at"],
            unique=False,
        )


def downgrade() -> None:
    if not _has_table("bets"):
        return
    if _has_index("bets", "ix_bets_publication_status_created"):
        op.drop_index("ix_bets_publication_status_created", table_name="bets")
    if not _has_column("bets", "publication_type"):
        return
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("bets") as batch_op:
            batch_op.drop_column("publication_type")
        return
    op.drop_column("bets", "publication_type")
