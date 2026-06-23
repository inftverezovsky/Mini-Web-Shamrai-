"""add native web chat v2 tables

Revision ID: 20260623_0025
Revises: 20260623_0024
Create Date: 2026-06-23
"""

from alembic import op
import sqlalchemy as sa


revision = "20260623_0025"
down_revision = "20260623_0024"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    if not _has_table("chat_conversations"):
        op.create_table(
            "chat_conversations",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("kind", sa.String(length=32), nullable=False, server_default="support"),
            sa.Column("owner_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False),
            sa.Column("assigned_staff_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="open"),
            sa.Column("title", sa.String(length=200), nullable=True),
            sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.UniqueConstraint("kind", "owner_user_id", name="uq_chat_conversation_kind_owner"),
            sa.CheckConstraint("kind IN ('support')", name="ck_chat_conversation_kind"),
            sa.CheckConstraint("status IN ('open', 'closed')", name="ck_chat_conversation_status"),
        )
    op.create_index(
        "ix_chat_conversations_status_last_message",
        "chat_conversations",
        ["status", "last_message_at"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_chat_conversations_owner",
        "chat_conversations",
        ["owner_user_id"],
        unique=False,
        if_not_exists=True,
    )

    if not _has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
            sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False),
            sa.Column("sender_user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True),
            sa.Column("sender_role", sa.String(length=32), nullable=False),
            sa.Column("type", sa.String(length=32), nullable=False, server_default="text"),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
            sa.Column("client_message_id", sa.Uuid(), nullable=False),
            sa.Column("reply_to_id", sa.BigInteger(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
            sa.UniqueConstraint("sender_user_id", "client_message_id", name="uq_chat_message_sender_client"),
            sa.CheckConstraint("type IN ('text')", name="ck_chat_message_type"),
            sa.CheckConstraint("text IS NULL OR length(text) BETWEEN 1 AND 4000", name="ck_chat_message_text_length"),
        )
    op.create_index(
        "ix_chat_messages_conversation_id_id",
        "chat_messages",
        ["conversation_id", "id"],
        unique=False,
        if_not_exists=True,
    )
    op.create_index(
        "ix_chat_messages_conversation_created",
        "chat_messages",
        ["conversation_id", "created_at"],
        unique=False,
        if_not_exists=True,
    )

    if not _has_table("chat_read_cursors"):
        op.create_table(
            "chat_read_cursors",
            sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("last_read_message_id", sa.BigInteger(), sa.ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )
    op.create_index(
        "ix_chat_read_cursors_user",
        "chat_read_cursors",
        ["user_id"],
        unique=False,
        if_not_exists=True,
    )

    if not _has_table("personal_signal_read_cursors"):
        op.create_table(
            "personal_signal_read_cursors",
            sa.Column("user_id", sa.BigInteger(), sa.ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
            sa.Column("last_read_signal_id", sa.Integer(), sa.ForeignKey("personal_signals.id", ondelete="SET NULL"), nullable=True),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        )


def downgrade() -> None:
    if _has_table("personal_signal_read_cursors"):
        op.drop_table("personal_signal_read_cursors")
    if _has_table("chat_read_cursors"):
        op.drop_index("ix_chat_read_cursors_user", table_name="chat_read_cursors", if_exists=True)
        op.drop_table("chat_read_cursors")
    if _has_table("chat_messages"):
        op.drop_index("ix_chat_messages_conversation_created", table_name="chat_messages", if_exists=True)
        op.drop_index("ix_chat_messages_conversation_id_id", table_name="chat_messages", if_exists=True)
        op.drop_table("chat_messages")
    if _has_table("chat_conversations"):
        op.drop_index("ix_chat_conversations_owner", table_name="chat_conversations", if_exists=True)
        op.drop_index("ix_chat_conversations_status_last_message", table_name="chat_conversations", if_exists=True)
        op.drop_table("chat_conversations")
