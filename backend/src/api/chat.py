import secrets
import time
from datetime import datetime
from threading import RLock
from typing import Annotated, Any, Literal, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.api.deps import get_current_admin, get_current_admin_read, get_current_user, get_current_user_read
from src.core.roles import is_staff_role
from src.models.database import AsyncSessionLocal, get_db, get_read_db
from src.models.models import ChatConversation, User
from src.services.chat import (
    CHAT_STATUS_CLOSED,
    CHAT_STATUS_OPEN,
    chat_message_payload,
    chat_stream_hub,
    conversations_for_user,
    create_chat_message,
    emit_conversation_updated,
    load_support_conversation_for_staff,
    mark_signals_read,
    notify_chat_message,
    paginated_signal_messages,
    paginated_support_messages,
    set_conversation_status,
    support_conversation_for_user,
    support_conversation_payload,
    advance_read_cursor,
)

router = APIRouter(prefix="/chat", tags=["Native Web Chat"])


class ChatUserResponse(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None
    is_web_only: bool = False
    role: str = "user"
    display_name: str


class ChatMessageCreate(BaseModel):
    client_message_id: UUID
    text: str
    reply_to_id: Optional[int] = Field(default=None, ge=1)


class ChatMessageResponse(BaseModel):
    id: int
    conversation_id: str
    sender_user_id: Optional[int] = None
    sender_role: str
    direction: Literal["staff", "client"]
    author_label: str
    type: Literal["text"]
    text: Optional[str] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    client_message_id: str
    reply_to_id: Optional[int] = None
    created_at: str
    edited_at: Optional[str] = None
    deleted_at: Optional[str] = None


class ChatSignalMessagePage(BaseModel):
    items: list[dict[str, Any]]
    next_before_id: Optional[int] = None
    has_more: bool = False


class ChatMessagePage(BaseModel):
    items: list[ChatMessageResponse]
    next_before_id: Optional[int] = None
    has_more: bool = False


class ChatConversationResponse(BaseModel):
    id: Optional[str] = None
    kind: str
    key: str
    status: str
    owner_user: ChatUserResponse
    assigned_staff_id: Optional[int] = None
    last_message: Optional[dict[str, Any]] = None
    last_message_text: Optional[str] = None
    last_message_at: Optional[str] = None
    unread_count: int = 0


class ChatConversationListResponse(BaseModel):
    items: list[ChatConversationResponse]
    next_before: Optional[str] = None
    has_more: bool = False


class ChatReadRequest(BaseModel):
    last_read_message_id: int = Field(ge=1)


class SignalReadRequest(BaseModel):
    last_read_signal_id: int = Field(ge=1)


class ChatReadResponse(BaseModel):
    status: str = "ok"
    last_read_message_id: Optional[int] = None
    last_read_signal_id: Optional[int] = None


class ChatStatusUpdate(BaseModel):
    status: Literal["open", "closed"]


class ChatStreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int


CHAT_STREAM_TICKET_TTL_SECONDS = 30
_chat_stream_tickets: dict[str, tuple[int, float]] = {}
_chat_stream_ticket_lock = RLock()


def _issue_chat_stream_ticket(user_id: int) -> str:
    ticket = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + CHAT_STREAM_TICKET_TTL_SECONDS
    with _chat_stream_ticket_lock:
        now = time.monotonic()
        expired = [
            existing_ticket
            for existing_ticket, (_, ticket_expires_at) in _chat_stream_tickets.items()
            if ticket_expires_at <= now
        ]
        for existing_ticket in expired:
            _chat_stream_tickets.pop(existing_ticket, None)
        _chat_stream_tickets[ticket] = (user_id, expires_at)
    return ticket


def _consume_chat_stream_ticket(ticket: str) -> Optional[int]:
    if not ticket:
        return None
    with _chat_stream_ticket_lock:
        item = _chat_stream_tickets.pop(ticket, None)
    if not item:
        return None
    user_id, expires_at = item
    if expires_at <= time.monotonic():
        return None
    return user_id


def _safe_limit(limit: int, default: int = 50) -> int:
    return max(1, min(int(limit or default), 100))


def _ensure_staff(user: User) -> None:
    if not is_staff_role(user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied: Staff permissions required")


def _conversation_uuid(conversation_id: UUID | str) -> UUID:
    try:
        return conversation_id if isinstance(conversation_id, UUID) else UUID(str(conversation_id))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Диалог не найден")


@router.get("/conversations", response_model=ChatConversationListResponse)
async def list_chat_conversations(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    return ChatConversationListResponse(**await conversations_for_user(db, current_user))


@router.get("/conversations/signals/messages", response_model=ChatSignalMessagePage)
async def get_signal_conversation_messages(
    before_id: Annotated[Optional[int], Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    return ChatSignalMessagePage(**await paginated_signal_messages(
        db,
        user=current_user,
        before_id=before_id,
        limit=_safe_limit(limit),
    ))


@router.post("/conversations/signals/read", response_model=ChatReadResponse)
async def mark_signal_conversation_read(
    payload: SignalReadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cursor = await mark_signals_read(
        db,
        user=current_user,
        last_read_signal_id=payload.last_read_signal_id,
    )
    return ChatReadResponse(last_read_signal_id=cursor.last_read_signal_id)


@router.get("/conversations/support/messages", response_model=ChatMessagePage)
async def get_support_messages(
    before_id: Annotated[Optional[int], Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    conversation = await support_conversation_for_user(db, current_user)
    return ChatMessagePage(**await paginated_support_messages(
        db,
        conversation=conversation,
        before_id=before_id,
        limit=_safe_limit(limit),
    ))


@router.post("/conversations/support/messages", response_model=ChatMessageResponse)
async def create_support_message(
    payload: ChatMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if is_staff_role(current_user.role):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Используйте админский чат для ответа клиентам")
    conversation = await support_conversation_for_user(db, current_user, create=True)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Не удалось создать диалог")
    if conversation.status == CHAT_STATUS_CLOSED:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Диалог закрыт")

    message, created = await create_chat_message(
        db,
        conversation=conversation,
        sender=current_user,
        client_message_id=payload.client_message_id,
        text=payload.text,
        reply_to_id=payload.reply_to_id,
    )
    if created:
        await notify_chat_message(db, conversation=conversation, message=message)
    return ChatMessageResponse(**chat_message_payload(message))


@router.post("/conversations/support/read", response_model=ChatReadResponse)
async def mark_support_read(
    payload: ChatReadRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    conversation = await support_conversation_for_user(db, current_user)
    if not conversation:
        return ChatReadResponse(last_read_message_id=None)
    cursor = await advance_read_cursor(
        db,
        conversation_id=conversation.id,
        user_id=current_user.telegram_id,
        last_read_message_id=payload.last_read_message_id,
    )
    await chat_stream_hub.send_to_user(
        current_user.telegram_id,
        {
            "event": "chat.read.updated",
            "conversation_id": str(conversation.id),
            "user_id": current_user.telegram_id,
            "last_read_message_id": cursor.last_read_message_id,
            "updated_at": cursor.updated_at.isoformat() if cursor.updated_at else None,
        },
    )
    return ChatReadResponse(last_read_message_id=cursor.last_read_message_id)


@router.post("/stream-ticket", response_model=ChatStreamTicketResponse)
async def create_chat_stream_ticket(
    current_user: User = Depends(get_current_user),
):
    return ChatStreamTicketResponse(
        ticket=_issue_chat_stream_ticket(current_user.telegram_id),
        expires_in=CHAT_STREAM_TICKET_TTL_SECONDS,
    )


@router.get("/admin/conversations", response_model=ChatConversationListResponse)
async def list_admin_chat_conversations(
    status: Optional[Literal["open", "closed"]] = None,
    before: Optional[datetime] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    _ensure_staff(admin)
    from src.services.chat import list_admin_conversations

    return ChatConversationListResponse(**await list_admin_conversations(
        db,
        viewer=admin,
        status_filter=status,
        before=before,
        limit=_safe_limit(limit),
    ))


@router.get("/admin/conversations/{conversation_id}/messages", response_model=ChatMessagePage)
async def get_admin_chat_messages(
    conversation_id: UUID,
    before_id: Annotated[Optional[int], Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    _ensure_staff(admin)
    conversation = await load_support_conversation_for_staff(db, _conversation_uuid(conversation_id))
    return ChatMessagePage(**await paginated_support_messages(
        db,
        conversation=conversation,
        before_id=before_id,
        limit=_safe_limit(limit),
    ))


@router.post("/admin/conversations/{conversation_id}/messages", response_model=ChatMessageResponse)
async def create_admin_chat_message(
    conversation_id: UUID,
    payload: ChatMessageCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    _ensure_staff(admin)
    conversation = await load_support_conversation_for_staff(db, _conversation_uuid(conversation_id))
    if conversation.status == CHAT_STATUS_CLOSED:
        await set_conversation_status(db, conversation=conversation, status_value=CHAT_STATUS_OPEN)
    message, created = await create_chat_message(
        db,
        conversation=conversation,
        sender=admin,
        client_message_id=payload.client_message_id,
        text=payload.text,
        reply_to_id=payload.reply_to_id,
    )
    if created:
        await notify_chat_message(db, conversation=conversation, message=message)
    return ChatMessageResponse(**chat_message_payload(message))


@router.post("/admin/conversations/{conversation_id}/read", response_model=ChatReadResponse)
async def mark_admin_chat_read(
    conversation_id: UUID,
    payload: ChatReadRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    _ensure_staff(admin)
    conversation = await load_support_conversation_for_staff(db, _conversation_uuid(conversation_id))
    cursor = await advance_read_cursor(
        db,
        conversation_id=conversation.id,
        user_id=admin.telegram_id,
        last_read_message_id=payload.last_read_message_id,
    )
    await chat_stream_hub.send_to_user(
        admin.telegram_id,
        {
            "event": "chat.read.updated",
            "conversation_id": str(conversation.id),
            "user_id": admin.telegram_id,
            "last_read_message_id": cursor.last_read_message_id,
            "updated_at": cursor.updated_at.isoformat() if cursor.updated_at else None,
        },
    )
    return ChatReadResponse(last_read_message_id=cursor.last_read_message_id)


@router.post("/admin/conversations/{conversation_id}/status", response_model=ChatConversationResponse)
async def update_admin_chat_status(
    conversation_id: UUID,
    payload: ChatStatusUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    _ensure_staff(admin)
    conversation = await load_support_conversation_for_staff(db, _conversation_uuid(conversation_id))
    await set_conversation_status(db, conversation=conversation, status_value=payload.status)
    await emit_conversation_updated(db, conversation=conversation)
    return ChatConversationResponse(**await support_conversation_payload(db, conversation, viewer=admin, owner=conversation.owner))


@router.websocket("/stream")
async def stream_chat_events(
    websocket: WebSocket,
    ticket: Annotated[str, Query()] = "",
):
    user_id = _consume_chat_stream_ticket(ticket)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).filter(User.telegram_id == user_id))
        user = result.scalars().first()

    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await chat_stream_hub.connect(user.telegram_id, websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await chat_stream_hub.disconnect(user.telegram_id, websocket)
