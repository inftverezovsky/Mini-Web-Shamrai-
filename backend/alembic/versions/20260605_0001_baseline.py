"""baseline schema from current SQLAlchemy models

Revision ID: 20260605_0001
Revises:
Create Date: 2026-06-05
"""

from alembic import op

from src.models.database import Base
from src.models import models  # noqa: F401


revision = "20260605_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    Base.metadata.create_all(bind=bind)


def downgrade() -> None:
    bind = op.get_bind()
    Base.metadata.drop_all(bind=bind)
