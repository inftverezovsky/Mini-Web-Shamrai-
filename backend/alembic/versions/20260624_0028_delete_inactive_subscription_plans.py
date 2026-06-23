"""delete inactive subscription plans

Revision ID: 20260624_0028
Revises: 20260623_0027
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0028"
down_revision = "20260623_0027"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    return table_name in sa.inspect(bind).get_table_names()


def upgrade() -> None:
    if not _has_table("subscription_plans"):
        return

    inactive_plan_ids = "SELECT id FROM subscription_plans WHERE is_active IS FALSE"

    if _has_table("subscriptions"):
        op.execute(f"UPDATE subscriptions SET plan_id = NULL WHERE plan_id IN ({inactive_plan_ids})")
    if _has_table("payment_attempts"):
        op.execute(f"UPDATE payment_attempts SET plan_id = NULL WHERE plan_id IN ({inactive_plan_ids})")
    if _has_table("ab_test_configs"):
        op.execute(f"DELETE FROM ab_test_configs WHERE plan_id IN ({inactive_plan_ids})")

    op.execute(f"DELETE FROM subscription_plans WHERE id IN ({inactive_plan_ids})")


def downgrade() -> None:
    pass
