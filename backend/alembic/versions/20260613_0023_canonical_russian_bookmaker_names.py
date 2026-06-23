"""canonical russian bookmaker names

Revision ID: 20260613_0023
Revises: 20260613_0022
Create Date: 2026-06-13
"""

from alembic import op
import sqlalchemy as sa


revision = "20260613_0023"
down_revision = "20260613_0022"
branch_labels = None
depends_on = None


CANONICAL_NAMES_BY_CODE = {
    "fonbet": "Фонбет",
    "betboom": "БетБум",
    "winline": "Винлайн",
    "pari": "Пари",
}

def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _rename_bookmakers(names_by_code: dict[str, str]) -> None:
    if not _has_table("bookmakers"):
        return
    bind = op.get_bind()
    bookmakers = sa.table(
        "bookmakers",
        sa.column("code", sa.String()),
        sa.column("name", sa.String()),
    )
    for code, name in names_by_code.items():
        bind.execute(
            bookmakers.update()
            .where(bookmakers.c.code == code)
            .values(name=name)
        )


def upgrade() -> None:
    _rename_bookmakers(CANONICAL_NAMES_BY_CODE)


def downgrade() -> None:
    _rename_bookmakers(CANONICAL_NAMES_BY_CODE)
