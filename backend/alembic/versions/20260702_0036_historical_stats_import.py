"""Add historical stats import tables.

Revision ID: 20260702_0036
Revises: 20260702_0035
Create Date: 2026-07-02
"""

from alembic import op
import sqlalchemy as sa


revision = "20260702_0036"
down_revision = "20260702_0035"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(index.get("name") == index_name for index in inspector.get_indexes(table_name))


def upgrade() -> None:
    if not _has_table("historical_stats_import_batches"):
        op.create_table(
            "historical_stats_import_batches",
            sa.Column("id", sa.Uuid(), nullable=False),
            sa.Column("source_filename", sa.String(), nullable=False),
            sa.Column("source_sha256", sa.String(length=64), nullable=False),
            sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("imported_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("unit_stake_rub", sa.Numeric(12, 2), nullable=False),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("total_bets", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_wins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_losses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_refunds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("total_turnover_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("total_profit_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_sha256", name="uq_historical_stats_import_batches_sha256"),
        )
    if not _has_index("historical_stats_import_batches", "ix_historical_stats_batches_active_cutoff"):
        op.create_index(
            "ix_historical_stats_batches_active_cutoff",
            "historical_stats_import_batches",
            ["is_active", "cutoff_at"],
            unique=False,
        )

    if not _has_table("historical_stats_monthly"):
        op.create_table(
            "historical_stats_monthly",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("period_key", sa.String(length=7), nullable=False),
            sa.Column("period_label", sa.String(), nullable=False),
            sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
            sa.Column("bets", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("refunds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("turnover_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("profit_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("average_coefficient", sa.Numeric(8, 3), nullable=False, server_default="0"),
            sa.Column("top_sport", sa.String(), nullable=True),
            sa.Column("top_bookmaker", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["batch_id"], ["historical_stats_import_batches.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id", "period_key", name="uq_historical_stats_monthly_batch_period"),
        )
    if not _has_index("historical_stats_monthly", "ix_historical_stats_monthly_batch_period"):
        op.create_index(
            "ix_historical_stats_monthly_batch_period",
            "historical_stats_monthly",
            ["batch_id", "period_key"],
            unique=False,
        )

    if not _has_table("historical_stats_breakdowns"):
        op.create_table(
            "historical_stats_breakdowns",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("dimension", sa.String(length=32), nullable=False),
            sa.Column("icon", sa.String(), nullable=True),
            sa.Column("label", sa.String(), nullable=False),
            sa.Column("normalized_key", sa.String(), nullable=False),
            sa.Column("bookmaker_code", sa.String(), nullable=True),
            sa.Column("bets", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("refunds", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("turnover_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("profit_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("average_coefficient", sa.Numeric(8, 3), nullable=False, server_default="0"),
            sa.ForeignKeyConstraint(["batch_id"], ["historical_stats_import_batches.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id", "dimension", "label", name="uq_historical_stats_breakdowns_batch_dimension_label"),
        )
    if not _has_index("historical_stats_breakdowns", "ix_historical_stats_breakdowns_batch_dimension"):
        op.create_index(
            "ix_historical_stats_breakdowns_batch_dimension",
            "historical_stats_breakdowns",
            ["batch_id", "dimension"],
            unique=False,
        )

    if not _has_table("historical_stats_details"):
        op.create_table(
            "historical_stats_details",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("batch_id", sa.Uuid(), nullable=False),
            sa.Column("period_key", sa.String(length=7), nullable=False),
            sa.Column("period_label", sa.String(), nullable=False),
            sa.Column("source_row_number", sa.Integer(), nullable=False),
            sa.Column("sport_icon", sa.String(), nullable=True),
            sa.Column("sport_type", sa.String(), nullable=True),
            sa.Column("event_name", sa.String(), nullable=False),
            sa.Column("bookmaker_icon", sa.String(), nullable=True),
            sa.Column("bookmaker_name", sa.String(), nullable=True),
            sa.Column("bookmaker_code", sa.String(), nullable=True),
            sa.Column("coefficient", sa.Numeric(8, 3), nullable=False),
            sa.Column("outcome", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False),
            sa.Column("turnover_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("profit_rub", sa.Numeric(14, 2), nullable=False, server_default="0"),
            sa.Column("source_file", sa.String(), nullable=True),
            sa.ForeignKeyConstraint(["batch_id"], ["historical_stats_import_batches.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("batch_id", "period_key", "source_row_number", name="uq_historical_stats_details_batch_period_row"),
        )
    if not _has_index("historical_stats_details", "ix_historical_stats_details_batch_period"):
        op.create_index(
            "ix_historical_stats_details_batch_period",
            "historical_stats_details",
            ["batch_id", "period_key"],
            unique=False,
        )


def downgrade() -> None:
    for table_name, index_name in [
        ("historical_stats_details", "ix_historical_stats_details_batch_period"),
        ("historical_stats_breakdowns", "ix_historical_stats_breakdowns_batch_dimension"),
        ("historical_stats_monthly", "ix_historical_stats_monthly_batch_period"),
        ("historical_stats_import_batches", "ix_historical_stats_batches_active_cutoff"),
    ]:
        if _has_table(table_name) and _has_index(table_name, index_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name in [
        "historical_stats_details",
        "historical_stats_breakdowns",
        "historical_stats_monthly",
        "historical_stats_import_batches",
    ]:
        if _has_table(table_name):
            op.drop_table(table_name)
