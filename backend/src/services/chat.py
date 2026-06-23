from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import HTTPException, WebSocket, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.roles import STAFF_ROLES, is_staff_role
from src.models.models import (
    ChatConversation,
    ChatMessage,
    ChatReadCursor,
    PersonalSignal,
    PersonalSignalReadCursor,
    User,
)
from src.services.delivery_outbox import CHANNEL_WEB_PUSH_SIGNAL, enqueue_delivery
from src.services.signals import (
    SUPPORT_MESSAGE_TYPES,
    WEBSOCKET_FANOUT_CONCURRENCY,
    WEBSOCKET_SEND_TIMEOUT_SECONDS,
    signal_to_payload,
)

CHAT_KIND_SUPPORT = "support"
CHAT_STATUS_OPEN = "open"
CHAT_STATUS_CLOSED = "closed"
CHAT_MESSAGE_TYPE_TEXT = "text"
CHAT_MESSAGE_MAX_LENGTH = 4000
SIGNAL_CONVERSATION_ID = "signals"
SUPPORT_CONVERSATION_KEY = "support"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def clean_chat_message_text(text: str) -> str:
    clean_text = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not clean_text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Сообщение не может быть пустым")
    if len(clean_text) > CHAT_MESSAGE_MAX_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Сообщение не может быть длиннее {CHAT_MESSAGE_MAX_LENGTH} символов",
        )
    return clean_text


def has_web_push_subscription(user: User) -> bool:
    subscription = getattr(user, "web_push_subscription", None)
    return isinstance(subscription, dict) and bool(subscription.get("endpoint"))


def frontend_url(open_target: str) -> str:
    base_url = settings.FRONTEND_BASE_URL.strip().rstrip("/") or "/app"
    return f"{base_url}?open={open_target}"


def chat_user_payload(user: User) -> dict[str, Any]:
    full_name = " ".join(part for part in [user.first_name, user.last_name] if part).strip()
    display_name = full_name or (f"@{user.username}" if user.username else f"ID {user.telegram_id}")
    if user.is_web_only and not full_name and not user.username:
        display_name = "Web/VK клиент"
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "photo_url": user.photo_url,
        "is_web_only": user.is_web_only,
        "role": user.role,
        "display_name": display_name,
    }


def chat_message_payload(message: ChatMessage) -> dict[str, Any]:
    direction = "client" if message.sender_role == "user" else "staff"
    return {
        "id": message.id,
        "conversation_id": str(message.conversation_id),
        "sender_user_id": message.sender_user_id,
        "sender_role": message.sender_role,
        "direction": direction,
        "author_label": "Shamrai" if direction == "staff" else "Клиент",
        "type": message.type,
        "text": message.text,
        "payload": message.payload or {},
        "client_message_id": str(message.client_message_id),
        "reply_to_id": message.reply_to_id,
        "created_at": message.created_at.isoformat() if message.created_at else utc_now().isoformat(),
        "edited_at": message.edited_at.isoformat() if message.edited_at else None,
        "deleted_at": message.deleted_at.isoformat() if message.deleted_at else None,
    }


async def support_conversation_for_user(
    db: AsyncSession,
    user: User,
    *,
    create: bool = False,
) -> Optional[ChatConversation]:
    result = await db.execute(
        select(ChatConversation)
        .filter(
            ChatConversation.kind == CHAT_KIND_SUPPORT,
            ChatConversation.owner_user_id == user.telegram_id,
        )
        .options(selectinload(ChatConversation.owner))
    )
    conversation = result.scalars().first()
    if conversation or not create:
        return conversation

    conversation = ChatConversation(
        kind=CHAT_KIND_SUPPORT,
        owner_user_id=user.telegram_id,
        status=CHAT_STATUS_OPEN,
        last_message_at=utc_now(),
    )
    db.add(conversation)
    await db.flush()
    conversation.owner = user
    return conversation


async def load_support_conversation_for_user(
    db: AsyncSession,
    conversation_id: UUID,
    user: User,
) -> ChatConversation:
    result = await db.execute(
        select(ChatConversation)
        .filter(
            ChatConversation.id == conversation_id,
            ChatConversation.kind == CHAT_KIND_SUPPORT,
            ChatConversation.owner_user_id == user.telegram_id,
        )
        .options(selectinload(ChatConversation.owner))
    )
    conversation = result.scalars().first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Диалог не найден")
    return conversation


async def load_support_conversation_for_staff(db: AsyncSession, conversation_id: UUID) -> ChatConversation:
    result = await db.execute(
        select(ChatConversation)
        .filter(ChatConversation.id == conversation_id, ChatConversation.kind == CHAT_KIND_SUPPORT)
        .options(selectinload(ChatConversation.owner))
    )
    conversation = result.scalars().first()
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Диалог не найден")
    return conversation


async def get_read_cursor(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: int,
) -> Optional[int]:
    result = await db.execute(
        select(ChatReadCursor.last_read_message_id).filter(
            ChatReadCursor.conversation_id == conversation_id,
            ChatReadCursor.user_id == user_id,
        )
    )
    value = result.scalar()
    return int(value) if value is not None else None


async def advance_read_cursor(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    user_id: int,
    last_read_message_id: int,
) -> ChatReadCursor:
    message_result = await db.execute(
        select(ChatMessage.id).filter(
            ChatMessage.id == int(last_read_message_id),
            ChatMessage.conversation_id == conversation_id,
        )
    )
    if message_result.scalar() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")

    existing = await db.get(ChatReadCursor, {"conversation_id": conversation_id, "user_id": user_id})
    now = utc_now()
    if existing:
        current_value = int(existing.last_read_message_id or 0)
        if int(last_read_message_id) > current_value:
            existing.last_read_message_id = int(last_read_message_id)
            existing.updated_at = now
        return existing

    cursor = ChatReadCursor(
        conversation_id=conversation_id,
        user_id=user_id,
        last_read_message_id=int(last_read_message_id),
        updated_at=now,
    )
    db.add(cursor)
    await db.flush()
    return cursor


async def unread_count_for_user(
    db: AsyncSession,
    *,
    conversation_id: UUID,
    user: User,
) -> int:
    last_read_message_id = await get_read_cursor(
        db,
        conversation_id=conversation_id,
        user_id=user.telegram_id,
    )
    filters = [
        ChatMessage.conversation_id == conversation_id,
        ChatMessage.deleted_at.is_(None),
    ]
    if last_read_message_id:
        filters.append(ChatMessage.id > last_read_message_id)
    if is_staff_role(user.role):
        filters.append(ChatMessage.sender_role == "user")
    else:
        filters.append(ChatMessage.sender_role != "user")
    result = await db.execute(select(func.count(ChatMessage.id)).filter(*filters))
    return int(result.scalar() or 0)


async def support_conversation_payload(
    db: AsyncSession,
    conversation: Optional[ChatConversation],
    *,
    viewer: User,
    owner: Optional[User] = None,
) -> dict[str, Any]:
    if not conversation:
        return {
            "id": None,
            "kind": CHAT_KIND_SUPPORT,
            "key": SUPPORT_CONVERSATION_KEY,
            "status": CHAT_STATUS_OPEN,
            "owner_user": chat_user_payload(owner or viewer),
            "assigned_staff_id": None,
            "last_message": None,
            "last_message_text": None,
            "last_message_at": None,
            "unread_count": 0,
        }

    last_result = await db.execute(
        select(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation.id)
        .order_by(ChatMessage.id.desc())
        .limit(1)
    )
    last_message = last_result.scalars().first()
    owner_user = owner or conversation.owner
    return {
        "id": str(conversation.id),
        "kind": conversation.kind,
        "key": SUPPORT_CONVERSATION_KEY,
        "status": conversation.status,
        "owner_user": chat_user_payload(owner_user),
        "assigned_staff_id": conversation.assigned_staff_id,
        "last_message": chat_message_payload(last_message) if last_message else None,
        "last_message_text": last_message.text if last_message else None,
        "last_message_at": (
            last_message.created_at.isoformat()
            if last_message and last_message.created_at
            else conversation.last_message_at.isoformat()
            if conversation.last_message_at
            else None
        ),
        "unread_count": await unread_count_for_user(db, conversation_id=conversation.id, user=viewer),
    }


async def signal_unread_count(db: AsyncSession, user: User) -> int:
    cursor = await db.get(PersonalSignalReadCursor, user.telegram_id)
    filters = [
        PersonalSignal.user_id == user.telegram_id,
        PersonalSignal.type.notin_(list(SUPPORT_MESSAGE_TYPES)),
    ]
    if cursor and cursor.last_read_signal_id:
        filters.append(PersonalSignal.id > cursor.last_read_signal_id)
    result = await db.execute(select(func.count(PersonalSignal.id)).filter(*filters))
    return int(result.scalar() or 0)


async def conversations_for_user(db: AsyncSession, user: User) -> dict[str, Any]:
    support_conversation = await support_conversation_for_user(db, user)
    latest_signal_result = await db.execute(
        select(PersonalSignal)
        .filter(
            PersonalSignal.user_id == user.telegram_id,
            PersonalSignal.type.notin_(list(SUPPORT_MESSAGE_TYPES)),
        )
        .order_by(PersonalSignal.id.desc())
        .limit(1)
    )
    latest_signal = latest_signal_result.scalars().first()
    signal_conversation = {
        "id": SIGNAL_CONVERSATION_ID,
        "kind": "signals",
        "key": SIGNAL_CONVERSATION_ID,
        "status": "open",
        "owner_user": chat_user_payload(user),
        "assigned_staff_id": None,
        "last_message": signal_to_payload(latest_signal) if latest_signal else None,
        "last_message_text": latest_signal.text if latest_signal else None,
        "last_message_at": latest_signal.created_at.isoformat() if latest_signal and latest_signal.created_at else None,
        "unread_count": await signal_unread_count(db, user),
    }
    return {
        "items": [
            signal_conversation,
            await support_conversation_payload(db, support_conversation, viewer=user, owner=user),
        ]
    }


async def paginated_support_messages(
    db: AsyncSession,
    *,
    conversation: Optional[ChatConversation],
    before_id: Optional[int],
    limit: int,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    if not conversation:
        return {"items": [], "next_before_id": None, "has_more": False}

    filters = [ChatMessage.conversation_id == conversation.id]
    if before_id:
        filters.append(ChatMessage.id < int(before_id))
    result = await db.execute(
        select(ChatMessage)
        .filter(*filters)
        .order_by(ChatMessage.id.desc())
        .limit(safe_limit + 1)
    )
    messages = list(result.scalars().all())
    has_more = len(messages) > safe_limit
    page_messages = list(reversed(messages[:safe_limit]))
    next_before_id = page_messages[0].id if has_more and page_messages else None
    return {
        "items": [chat_message_payload(message) for message in page_messages],
        "next_before_id": next_before_id,
        "has_more": has_more,
    }


async def paginated_signal_messages(
    db: AsyncSession,
    *,
    user: User,
    before_id: Optional[int],
    limit: int,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    filters = [
        PersonalSignal.user_id == user.telegram_id,
        PersonalSignal.type.notin_(list(SUPPORT_MESSAGE_TYPES)),
    ]
    if before_id:
        filters.append(PersonalSignal.id < int(before_id))
    result = await db.execute(
        select(PersonalSignal)
        .filter(*filters)
        .order_by(PersonalSignal.id.desc())
        .limit(safe_limit + 1)
    )
    signals = list(result.scalars().all())
    has_more = len(signals) > safe_limit
    page_signals = list(reversed(signals[:safe_limit]))
    next_before_id = page_signals[0].id if has_more and page_signals else None
    return {
        "items": [signal_to_payload(signal) for signal in page_signals],
        "next_before_id": next_before_id,
        "has_more": has_more,
    }


async def mark_signals_read(db: AsyncSession, *, user: User, last_read_signal_id: int) -> PersonalSignalReadCursor:
    signal_result = await db.execute(
        select(PersonalSignal.id).filter(
            PersonalSignal.id == int(last_read_signal_id),
            PersonalSignal.user_id == user.telegram_id,
            PersonalSignal.type.notin_(list(SUPPORT_MESSAGE_TYPES)),
        )
    )
    if signal_result.scalar() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение не найдено")

    cursor = await db.get(PersonalSignalReadCursor, user.telegram_id)
    now = utc_now()
    if cursor:
        if int(last_read_signal_id) > int(cursor.last_read_signal_id or 0):
            cursor.last_read_signal_id = int(last_read_signal_id)
            cursor.updated_at = now
        return cursor

    cursor = PersonalSignalReadCursor(
        user_id=user.telegram_id,
        last_read_signal_id=int(last_read_signal_id),
        updated_at=now,
    )
    db.add(cursor)
    await db.flush()
    return cursor


async def create_chat_message(
    db: AsyncSession,
    *,
    conversation: ChatConversation,
    sender: User,
    client_message_id: UUID,
    text: str,
    reply_to_id: Optional[int] = None,
) -> tuple[ChatMessage, bool]:
    existing_result = await db.execute(
        select(ChatMessage).filter(
            ChatMessage.sender_user_id == sender.telegram_id,
            ChatMessage.client_message_id == client_message_id,
        )
    )
    existing = existing_result.scalars().first()
    if existing:
        return existing, False

    clean_text = clean_chat_message_text(text)
    if reply_to_id is not None:
        reply_result = await db.execute(
            select(ChatMessage.id).filter(
                ChatMessage.id == int(reply_to_id),
                ChatMessage.conversation_id == conversation.id,
            )
        )
        if reply_result.scalar() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Сообщение для ответа не найдено")

    now = utc_now()
    message = ChatMessage(
        conversation_id=conversation.id,
        sender_user_id=sender.telegram_id,
        sender_role=sender.role,
        type=CHAT_MESSAGE_TYPE_TEXT,
        text=clean_text,
        payload={},
        client_message_id=client_message_id,
        reply_to_id=reply_to_id,
        created_at=now,
    )
    db.add(message)
    conversation.last_message_at = now
    conversation.updated_at = now
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        existing_result = await db.execute(
            select(ChatMessage).filter(
                ChatMessage.sender_user_id == sender.telegram_id,
                ChatMessage.client_message_id == client_message_id,
            )
        )
        existing = existing_result.scalars().first()
        if existing:
            return existing, False
        raise
    return message, True


class ChatStreamHub:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))
        if not sockets:
            return

        message = json.dumps(payload, ensure_ascii=False)

        async def _send_one(websocket: WebSocket) -> Optional[WebSocket]:
            try:
                await asyncio.wait_for(
                    websocket.send_text(message),
                    timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
                )
                return None
            except Exception:
                return websocket

        stale_sockets = [
            websocket
            for websocket in await asyncio.gather(*(_send_one(websocket) for websocket in sockets))
            if websocket is not None
        ]
        for websocket in stale_sockets:
            await self.disconnect(user_id, websocket)

    async def send_to_users(self, user_ids: list[int], payload: dict[str, Any]) -> None:
        semaphore = asyncio.Semaphore(WEBSOCKET_FANOUT_CONCURRENCY)

        async def _send_one(user_id: int) -> None:
            async with semaphore:
                await self.send_to_user(user_id, payload)

        if user_ids:
            await asyncio.gather(*(_send_one(user_id) for user_id in sorted(set(user_ids))))


chat_stream_hub = ChatStreamHub()


async def staff_users(db: AsyncSession) -> list[User]:
    result = await db.execute(select(User).filter(User.role.in_(list(STAFF_ROLES))))
    return list(result.scalars().all())


async def emit_message_created(db: AsyncSession, *, conversation: ChatConversation, message: ChatMessage) -> None:
    owner = conversation.owner
    owner_payload = {
        "event": "chat.message.created",
        "conversation_id": str(conversation.id),
        "message": chat_message_payload(message),
        "conversation": await support_conversation_payload(db, conversation, viewer=owner, owner=owner),
    }
    await chat_stream_hub.send_to_user(conversation.owner_user_id, owner_payload)

    for staff_user in await staff_users(db):
        staff_payload = {
            **owner_payload,
            "conversation": await support_conversation_payload(
                db,
                conversation,
                viewer=staff_user,
                owner=owner,
            ),
        }
        await chat_stream_hub.send_to_user(staff_user.telegram_id, staff_payload)


async def emit_conversation_updated(db: AsyncSession, *, conversation: ChatConversation) -> None:
    owner = conversation.owner
    owner_payload = {
        "event": "chat.conversation.updated",
        "conversation_id": str(conversation.id),
        "conversation": await support_conversation_payload(db, conversation, viewer=owner, owner=owner),
    }
    await chat_stream_hub.send_to_user(conversation.owner_user_id, owner_payload)

    for staff_user in await staff_users(db):
        staff_payload = {
            **owner_payload,
            "conversation": await support_conversation_payload(
                db,
                conversation,
                viewer=staff_user,
                owner=owner,
            ),
        }
        await chat_stream_hub.send_to_user(staff_user.telegram_id, staff_payload)


async def enqueue_support_web_push(
    db: AsyncSession,
    *,
    recipient: User,
    message: ChatMessage,
    title: str,
    url_target: str,
) -> None:
    if not has_web_push_subscription(recipient):
        return
    signal_payload = {
        "id": message.id,
        "user_id": recipient.telegram_id,
        "text": message.text or "",
        "type": "support_staff_message" if message.sender_role != "user" else "support_client_message",
        "data": {
            "push_title": title,
            "push_body": message.text or "",
            "push_url": frontend_url(url_target),
        },
        "created_at": message.created_at.isoformat() if message.created_at else utc_now().isoformat(),
    }
    await enqueue_delivery(
        db,
        channel=CHANNEL_WEB_PUSH_SIGNAL,
        user_id=recipient.telegram_id,
        dedupe_key=f"chat_message:{message.id}:web_push:{recipient.telegram_id}",
        payload={
            "subscription": recipient.web_push_subscription,
            "signal_payload": signal_payload,
        },
    )


async def notify_chat_message(db: AsyncSession, *, conversation: ChatConversation, message: ChatMessage) -> None:
    await emit_message_created(db, conversation=conversation, message=message)
    if message.sender_role != "user":
        await enqueue_support_web_push(
            db,
            recipient=conversation.owner,
            message=message,
            title="Shamrai написал в чат",
            url_target="web-chat&conversation=support",
        )
        return

    staff_result = await db.execute(select(User).filter(User.role.in_(list(STAFF_ROLES))))
    for staff_user in staff_result.scalars().all():
        await enqueue_support_web_push(
            db,
            recipient=staff_user,
            message=message,
            title=f"Ответ клиента: {chat_user_payload(conversation.owner)['display_name']}",
            url_target=f"admin-web-chat&conversation_id={conversation.id}",
        )


async def list_admin_conversations(
    db: AsyncSession,
    *,
    viewer: User,
    status_filter: Optional[str],
    before: Optional[datetime],
    limit: int,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit or 50), 100))
    filters = [ChatConversation.kind == CHAT_KIND_SUPPORT]
    if status_filter in {CHAT_STATUS_OPEN, CHAT_STATUS_CLOSED}:
        filters.append(ChatConversation.status == status_filter)
    if before:
        filters.append(ChatConversation.last_message_at < before)
    result = await db.execute(
        select(ChatConversation)
        .filter(*filters)
        .options(selectinload(ChatConversation.owner))
        .order_by(ChatConversation.last_message_at.desc(), ChatConversation.id.desc())
        .limit(safe_limit + 1)
    )
    conversations = list(result.scalars().all())
    has_more = len(conversations) > safe_limit
    page_conversations = conversations[:safe_limit]
    return {
        "items": [
            await support_conversation_payload(db, conversation, viewer=viewer, owner=conversation.owner)
            for conversation in page_conversations
        ],
        "next_before": (
            page_conversations[-1].last_message_at.isoformat()
            if has_more and page_conversations and page_conversations[-1].last_message_at
            else None
        ),
        "has_more": has_more,
    }


async def set_conversation_status(
    db: AsyncSession,
    *,
    conversation: ChatConversation,
    status_value: str,
) -> ChatConversation:
    if status_value not in {CHAT_STATUS_OPEN, CHAT_STATUS_CLOSED}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный статус диалога")
    conversation.status = status_value
    conversation.updated_at = utc_now()
    await db.flush()
    return conversation
