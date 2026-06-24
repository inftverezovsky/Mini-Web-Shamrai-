"""allow file messages in native chat

Revision ID: 20260624_0032
Revises: 20260624_0031
Create Date: 2026-06-24
"""

from alembic import op
import sqlalchemy as sa


revision = "20260624_0032"
down_revision = "20260624_0031"
branch_labels = None
depends_on = None


CONSTRAINT_NAME = "ck_chat_message_type"
FILE_TYPES_CHECK = "type IN ('text', 'image', 'voice', 'file')"
MEDIA_TYPES_CHECK = "type IN ('text', 'image', 'voice')"


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(
        constraint.get("name") == constraint_name
        for constraint in inspector.get_check_constraints(table_name)
    )


def _replace_message_type_constraint(expression: str) -> None:
    if not _has_table("chat_messages"):
        return
    bind = op.get_bind()
    has_constraint = _has_check_constraint("chat_messages", CONSTRAINT_NAME)
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("chat_messages", recreate="always") as batch_op:
            if has_constraint:
                batch_op.drop_constraint(CONSTRAINT_NAME, type_="check")
            batch_op.create_check_constraint(CONSTRAINT_NAME, expression)
        return

    if has_constraint:
        op.drop_constraint(CONSTRAINT_NAME, "chat_messages", type_="check")
    op.create_check_constraint(CONSTRAINT_NAME, "chat_messages", expression)


def upgrade() -> None:
    _replace_message_type_constraint(FILE_TYPES_CHECK)


def downgrade() -> None:
    if _has_table("chat_messages"):
        op.execute(
            """
            UPDATE chat_messages
            SET
                text = COALESCE(text, 'Файл'),
                payload = '{}',
                type = 'text'
            WHERE type = 'file'
            """
        )
    _replace_message_type_constraint(MEDIA_TYPES_CHECK)
