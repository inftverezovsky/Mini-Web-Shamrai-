from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import HTTPException, status
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.roles import STAFF_ROLES
from src.models.models import AdminAuditLog, PersonalSignal, User
from src.services.delivery_outbox import CHANNEL_WEB_PUSH_SIGNAL, enqueue_delivery
from src.services.signals import (
    SUPPORT_CLIENT_MESSAGE_TYPE,
    SUPPORT_MESSAGE_TYPES,
    SUPPORT_STAFF_MESSAGE_TYPE,
    signal_stream_hub,
    signal_to_payload,
    support_staff_stream_hub,
)

SUPPORT_MESSAGE_MAX_LENGTH = 2000
SUPPORT_THREAD_SCAN_LIMIT = 1000


def clean_support_message_text(text: str) -> str:
    clean_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Сообщение не может быть пустым")
    if len(clean_text) > SUPPORT_MESSAGE_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Сообщение не может быть длиннее {SUPPORT_MESSAGE_MAX_LENGTH} символов",
        )
    return clean_text


def _has_web_push_subscription(user: User) -> bool:
    subscription = getattr(user, "web_push_subscription", None)
    return isinstance(subscription, dict) and bool(subscription.get("endpoint"))


def _frontend_url_with_open_target(open_target: str) -> str:
    frontend_url = settings.FRONTEND_BASE_URL.strip().rstrip("/") or "/app"
    return f"{frontend_url}?open={open_target}"


def _client_display_name(user: User) -> str:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    if full_name:
        return full_name
    if user.username:
        return f"@{user.username}"
    return "Web/VK клиент" if user.is_web_only else f"ID {user.telegram_id}"


def support_user_payload(user: User) -> dict[str, Any]:
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "photo_url": user.photo_url,
        "is_web_only": user.is_web_only,
        "role": user.role,
        "display_name": _client_display_name(user),
    }


def support_message_payload(signal: PersonalSignal) -> dict[str, Any]:
    return signal_to_payload(signal)


def support_thread_payload(user: User, latest_signal: Optional[PersonalSignal]) -> dict[str, Any]:
    latest_payload = support_message_payload(latest_signal) if latest_signal else None
    latest_direction = latest_payload.get("direction") if latest_payload else None
    return {
        "user": support_user_payload(user),
        "last_message": latest_payload,
        "last_message_text": latest_signal.text if latest_signal else None,
        "last_message_created_at": (
            latest_signal.created_at.isoformat()
            if latest_signal and latest_signal.created_at
            else None
        ),
        "needs_reply": latest_direction == "client",
    }


async def load_support_target_user(db: AsyncSession, user_id: int) -> User:
    result = await db.execute(select(User).filter(User.telegram_id == user_id, User.role == "user"))
    user = result.scalars().first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    return user


async def create_staff_support_message(
    db: AsyncSession,
    *,
    target_user: User,
    staff_user: User,
    text: str,
) -> PersonalSignal:
    clean_text = clean_support_message_text(text)
    signal = PersonalSignal(
        user_id=target_user.telegram_id,
        text=clean_text,
        type=SUPPORT_STAFF_MESSAGE_TYPE,
        data={
            "direction": "staff",
            "author_label": "Shamrai",
            "sender_user_id": staff_user.telegram_id,
            "sender_role": staff_user.role,
            "event_type": "support_web_chat",
            "push_title": "Shamrai написал в чат",
            "push_body": clean_text,
            "push_url": _frontend_url_with_open_target("web-bot-chat"),
        },
        created_at=datetime.now(timezone.utc),
    )
    db.add(signal)
    await db.flush()

    payload = support_message_payload(signal)
    await signal_stream_hub.send_to_user(target_user.telegram_id, payload)
    if _has_web_push_subscription(target_user):
        await enqueue_delivery(
            db,
            channel=CHANNEL_WEB_PUSH_SIGNAL,
            user_id=target_user.telegram_id,
            personal_signal_id=signal.id,
            dedupe_key=f"support_staff:{signal.id}:client:{target_user.telegram_id}:web_push",
            payload={
                "subscription": target_user.web_push_subscription,
                "signal_payload": payload,
            },
        )

    db.add(AdminAuditLog(
        actor_id=staff_user.telegram_id,
        target_user_id=target_user.telegram_id,
        action="support_chat_staff_message_sent",
        details={
            "message_id": signal.id,
            "signal_type": SUPPORT_STAFF_MESSAGE_TYPE,
            "text_length": len(clean_text),
        },
    ))
    await db.flush()
    return signal


async def create_client_support_message(
    db: AsyncSession,
    *,
    client_user: User,
    text: str,
) -> PersonalSignal:
    clean_text = clean_support_message_text(text)
    signal = PersonalSignal(
        user_id=client_user.telegram_id,
        text=clean_text,
        type=SUPPORT_CLIENT_MESSAGE_TYPE,
        data={
            "direction": "client",
            "author_label": _client_display_name(client_user),
            "sender_user_id": client_user.telegram_id,
            "sender_role": "user",
            "event_type": "support_web_chat",
        },
        created_at=datetime.now(timezone.utc),
    )
    db.add(signal)
    await db.flush()

    payload = support_message_payload(signal)
    await signal_stream_hub.send_to_user(client_user.telegram_id, payload)
    await notify_staff_about_client_support_message(db, client_user=client_user, signal=signal)
    return signal


async def notify_staff_about_client_support_message(
    db: AsyncSession,
    *,
    client_user: User,
    signal: PersonalSignal,
) -> None:
    staff_result = await db.execute(select(User).filter(User.role.in_(list(STAFF_ROLES))))
    staff_users = staff_result.scalars().all()
    if not staff_users:
        return

    message_payload = support_message_payload(signal)
    thread_payload = support_thread_payload(client_user, signal)
    stream_payload = {
        "event": "support_message",
        "message": message_payload,
        "thread": thread_payload,
    }

    for staff_user in staff_users:
        await support_staff_stream_hub.send_to_user(staff_user.telegram_id, stream_payload)
        if not _has_web_push_subscription(staff_user):
            continue
        staff_signal_payload = {
            **message_payload,
            "data": {
                **(message_payload.get("data") or {}),
                "push_title": f"Ответ клиента: {_client_display_name(client_user)}",
                "push_body": signal.text,
                "push_url": _frontend_url_with_open_target(f"admin-web-chat&user_id={client_user.telegram_id}"),
            },
        }
        await enqueue_delivery(
            db,
            channel=CHANNEL_WEB_PUSH_SIGNAL,
            user_id=staff_user.telegram_id,
            personal_signal_id=signal.id,
            dedupe_key=f"support_client:{signal.id}:staff:{staff_user.telegram_id}:web_push",
            payload={
                "subscription": staff_user.web_push_subscription,
                "signal_payload": staff_signal_payload,
            },
        )


async def list_support_threads(
    db: AsyncSession,
    *,
    q: Optional[str] = None,
    limit: int = 40,
) -> list[dict[str, Any]]:
    clean_q = (q or "").strip().lower()
    support_result = await db.execute(
        select(PersonalSignal)
        .filter(PersonalSignal.type.in_(list(SUPPORT_MESSAGE_TYPES)))
        .order_by(PersonalSignal.created_at.desc(), PersonalSignal.id.desc())
        .limit(SUPPORT_THREAD_SCAN_LIMIT)
    )
    latest_signal_by_user_id: dict[int, PersonalSignal] = {}
    for signal in support_result.scalars().all():
        latest_signal_by_user_id.setdefault(signal.user_id, signal)

    candidate_user_ids = set(latest_signal_by_user_id)
    if clean_q:
        like_q = f"%{clean_q}%"
        search_result = await db.execute(
            select(User.telegram_id)
            .filter(
                User.role == "user",
                or_(
                    func.lower(func.coalesce(User.username, "")).like(like_q),
                    func.lower(func.coalesce(User.first_name, "")).like(like_q),
                    func.lower(func.coalesce(User.last_name, "")).like(like_q),
                    func.lower(func.coalesce(User.client_group, "")).like(like_q),
                    func.lower(func.coalesce(User.client_tag, "")).like(like_q),
                    cast(User.telegram_id, String).like(f"%{clean_q}%"),
                ),
            )
            .limit(100)
        )
        candidate_user_ids.update(int(user_id) for user_id in search_result.scalars().all())

    if not candidate_user_ids:
        return []

    users_result = await db.execute(
        select(User).filter(User.telegram_id.in_(candidate_user_ids), User.role == "user")
    )
    users = users_result.scalars().all()

    def sort_key(user: User) -> tuple[datetime, int]:
        signal = latest_signal_by_user_id.get(user.telegram_id)
        created_at = signal.created_at if signal and signal.created_at else user.created_at
        if created_at is None:
            created_at = datetime.fromtimestamp(0, timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return created_at, int(user.telegram_id)

    threads = [
        support_thread_payload(user, latest_signal_by_user_id.get(user.telegram_id))
        for user in sorted(users, key=sort_key, reverse=True)
    ]
    if clean_q:
        threads = [
            thread for thread in threads
            if _thread_matches_query(thread, clean_q)
        ]
    return threads[:limit]


def _thread_matches_query(thread: dict[str, Any], clean_q: str) -> bool:
    user = thread.get("user") or {}
    latest_message = thread.get("last_message") or {}
    searchable = " ".join(
        str(value or "")
        for value in [
            user.get("telegram_id"),
            user.get("username"),
            user.get("first_name"),
            user.get("last_name"),
            user.get("display_name"),
            thread.get("last_message_text"),
            latest_message.get("text"),
        ]
    ).lower()
    return clean_q in searchable


async def list_support_messages(
    db: AsyncSession,
    *,
    target_user: User,
    limit: int = 100,
) -> list[dict[str, Any]]:
    result = await db.execute(
        select(PersonalSignal)
        .filter(
            PersonalSignal.user_id == target_user.telegram_id,
            PersonalSignal.type.in_(list(SUPPORT_MESSAGE_TYPES)),
        )
        .order_by(PersonalSignal.created_at.desc(), PersonalSignal.id.desc())
        .limit(limit)
    )
    return [
        support_message_payload(signal)
        for signal in reversed(result.scalars().all())
    ]
