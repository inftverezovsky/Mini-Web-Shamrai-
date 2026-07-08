"""disable referral program by default

Revision ID: 20260703_0039
Revises: 20260703_0038
Create Date: 2026-07-03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260703_0039"
down_revision = "20260703_0038"
branch_labels = None
depends_on = None

REFERRAL_PROGRAM_ENABLED_KEY = "REFERRAL_PROGRAM_ENABLED"
REFERRAL_PROGRAM_ENABLED_DESCRIPTION = "Включает реферальную программу и учет квалифицированных покупок."


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("system_settings"):
        return

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT value FROM system_settings WHERE key = :key"),
        {"key": REFERRAL_PROGRAM_ENABLED_KEY},
    ).first()
    if existing is None:
        bind.execute(
            sa.text(
                """
                INSERT INTO system_settings (key, value, description, is_secret)
                VALUES (:key, 'false', :description, :is_secret)
                """
            ),
            {
                "key": REFERRAL_PROGRAM_ENABLED_KEY,
                "description": REFERRAL_PROGRAM_ENABLED_DESCRIPTION,
                "is_secret": False,
            },
        )
        return

    bind.execute(
        sa.text(
            """
            UPDATE system_settings
            SET value = 'false', description = :description, is_secret = :is_secret
            WHERE key = :key
            """
        ),
        {
            "key": REFERRAL_PROGRAM_ENABLED_KEY,
            "description": REFERRAL_PROGRAM_ENABLED_DESCRIPTION,
            "is_secret": False,
        },
    )


def downgrade() -> None:
    pass
