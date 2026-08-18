from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.quiet_hours import current_notification_time, user_is_in_quiet_hours
from src.core.roles import STAFF_ROLES
from src.core.telegram_delivery import user_can_receive_personal_telegram
from src.models.models import AdminAuditLog, Bet, SystemSetting, User
from src.services.delivery_outbox import enqueue_telegram_copy_batch
from src.services.system_settings import is_system_setting_enabled

MAX_FEED_TITLE_LENGTH = 200
MAX_FEED_BODY_LENGTH = 4000
MAX_POST_TEXT_LENGTH = 4096
MAX_CUSTOM_EMOJI_PER_POST = 100


@dataclass(frozen=True)
class TelegramPostDraft:
    source_chat_id: int
    source_message_id: int
    text: str
    title: str
    body: str
    custom_emoji_count: int


@dataclass(frozen=True)
class TelegramPostPublicationReport:
    feed_post_id: UUID
    telegram_recipients: int
    custom_emoji_count: int
    already_published: bool = False


def _positive_int(value: Any, field_label: str) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Не удалось определить {field_label}.") from exc
    if parsed <= 0:
        raise ValueError(f"Не удалось определить {field_label}.")
    return parsed


def _message_text(message: dict[str, Any]) -> str:
    text = str(message.get("text") or message.get("caption") or "").strip()
    if len(text) > MAX_POST_TEXT_LENGTH:
        raise ValueError(f"Текст публикации не должен превышать {MAX_POST_TEXT_LENGTH} символов.")
    return text


def _feed_parts(text: str) -> tuple[str, str]:
    lines = text.splitlines()
    title_index = next((index for index, line in enumerate(lines) if line.strip()), None)
    if title_index is None:
        raise ValueError("В исходном сообщении нет текста для публикации.")

    raw_title = lines[title_index].strip()
    title = (
        raw_title
        if len(raw_title) <= MAX_FEED_TITLE_LENGTH
        else f"{raw_title[: MAX_FEED_TITLE_LENGTH - 1].rstrip()}…"
    )
    body = "\n".join(lines[title_index + 1 :]).strip()
    return title, body[:MAX_FEED_BODY_LENGTH]


def _custom_emoji_count(message: dict[str, Any]) -> int:
    entities = message.get("entities") or message.get("caption_entities") or []
    if not isinstance(entities, list):
        return 0
    count = sum(
        1
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("type") == "custom_emoji"
        and str(entity.get("custom_emoji_id") or "").isdigit()
    )
    if count > MAX_CUSTOM_EMOJI_PER_POST:
        raise ValueError(f"В одном посте можно использовать не более {MAX_CUSTOM_EMOJI_PER_POST} custom emoji.")
    return count


def extract_replied_post(
    command_message: dict[str, Any],
    *,
    actor_user_id: int,
) -> TelegramPostDraft:
    command_chat = command_message.get("chat") or {}
    command_sender = command_message.get("from") or {}
    if command_chat.get("type") != "private":
        raise ValueError("Публикация разрешена только в личном диалоге с ботом.")
    if _positive_int(command_sender.get("id"), "автора команды") != actor_user_id:
        raise ValueError("Команда должна быть отправлена с вашего Telegram-аккаунта.")

    source = command_message.get("reply_to_message")
    if not isinstance(source, dict):
        raise ValueError(  # noqa: TRY004 - missing reply is a user input validation error
            "Отправьте готовый пост и ответьте на него командой /publish."
        )

    source_chat = source.get("chat") or {}
    source_sender = source.get("from") or {}
    source_chat_id = _positive_int(source_chat.get("id"), "исходный чат")
    if source_chat.get("type") != "private" or source_chat_id != actor_user_id:
        raise ValueError("Команда должна быть ответом на сообщение из вашего личного диалога с ботом.")
    if _positive_int(source_sender.get("id"), "автора поста") != actor_user_id:
        raise ValueError("Можно публиковать только собственное подготовленное сообщение.")

    text = _message_text(source)
    title, body = _feed_parts(text)
    return TelegramPostDraft(
        source_chat_id=source_chat_id,
        source_message_id=_positive_int(source.get("message_id"), "номер исходного сообщения"),
        text=text,
        title=title,
        body=body,
        custom_emoji_count=_custom_emoji_count(source),
    )


async def load_telegram_broadcast_recipients(db: AsyncSession) -> list[User]:
    filters = [
        User.role.notin_(list(STAFF_ROLES)),
        User.telegram_id > 0,
    ]
    if await is_system_setting_enabled(db, "BROADCAST_ONLY_ACTIVE_SUBSCRIBERS"):
        filters.append(User.matches_remaining >= 1)

    result = await db.execute(select(User).filter(*filters))
    now = current_notification_time()
    return [
        user
        for user in result.scalars().all()
        if user_can_receive_personal_telegram(user)
        and not user_is_in_quiet_hours(user, now)
    ]


async def publish_telegram_post(
    db: AsyncSession,
    draft: TelegramPostDraft,
    *,
    actor_user_id: int,
) -> TelegramPostPublicationReport:
    source_key = f"{draft.source_chat_id}:{draft.source_message_id}"
    marker_key = f"TELEGRAM_MANUAL_POST_{hashlib.sha256(source_key.encode()).hexdigest()[:32]}"
    marker_result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == marker_key)
    )
    existing_marker = marker_result.scalars().first()
    if existing_marker:
        marker_value = json.loads(existing_marker.value)
        return TelegramPostPublicationReport(
            feed_post_id=UUID(str(marker_value["feed_post_id"])),
            telegram_recipients=int(marker_value.get("telegram_recipients") or 0),
            custom_emoji_count=int(marker_value.get("custom_emoji_count") or 0),
            already_published=True,
        )

    feed_post = Bet(
        event_name=draft.title,
        coefficient=Decimal("1.00"),
        description=draft.body or None,
        category="prematch",
        sport_type="Текст",
        delivery_mode="feed",
        publication_type="text",
        status="pending",
        author_id=actor_user_id,
        bookmaker_links=[],
    )
    db.add(feed_post)
    await db.flush()

    recipients = await load_telegram_broadcast_recipients(db)
    await enqueue_telegram_copy_batch(
        db,
        source_chat_id=draft.source_chat_id,
        source_message_id=draft.source_message_id,
        user_ids=[user.telegram_id for user in recipients],
        dedupe_prefix=f"manual_post:{draft.source_chat_id}:{draft.source_message_id}",
    )

    report = TelegramPostPublicationReport(
        feed_post_id=feed_post.id,
        telegram_recipients=len(recipients),
        custom_emoji_count=draft.custom_emoji_count,
    )
    db.add(
        SystemSetting(
            key=marker_key,
            value=json.dumps(
                {
                    "feed_post_id": str(report.feed_post_id),
                    "telegram_recipients": report.telegram_recipients,
                    "custom_emoji_count": report.custom_emoji_count,
                },
                separators=(",", ":"),
            ),
            description="Idempotency marker for a Telegram manual post.",
            is_secret=False,
        )
    )
    db.add(
        AdminAuditLog(
            actor_id=actor_user_id,
            action="telegram_manual_post_published",
            details={
                "feed_post_id": str(report.feed_post_id),
                "telegram_recipients": report.telegram_recipients,
                "custom_emoji_count": report.custom_emoji_count,
            },
        )
    )
    return report
