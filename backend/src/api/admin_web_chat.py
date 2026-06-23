import secrets
import time
from threading import RLock
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.api.deps import get_current_admin, get_current_admin_read
from src.core.roles import is_staff_role
from src.models.database import AsyncSessionLocal, get_db, get_read_db
from src.models.models import User
from src.services.signals import support_staff_stream_hub
from src.services.support_web_chat import (
    create_staff_support_message,
    list_support_messages,
    list_support_threads,
    load_support_target_user,
    support_message_payload,
)

router = APIRouter(prefix="/admin/web-chat", tags=["Admin Web Chat"])


class SupportMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class SupportChatUserResponse(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None
    is_web_only: bool = False
    role: str = "user"
    display_name: str


class SupportChatMessageResponse(BaseModel):
    id: int
    user_id: int
    text: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    direction: str
    author_label: str
    sender_user_id: Optional[int] = None
    sender_role: Optional[str] = None


class SupportChatThreadResponse(BaseModel):
    user: SupportChatUserResponse
    last_message: Optional[SupportChatMessageResponse] = None
    last_message_text: Optional[str] = None
    last_message_created_at: Optional[str] = None
    needs_reply: bool = False


class SupportChatThreadListResponse(BaseModel):
    items: list[SupportChatThreadResponse]


class SupportChatStreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int


SUPPORT_CHAT_STREAM_TICKET_TTL_SECONDS = 30
_support_chat_stream_tickets: dict[str, tuple[int, float]] = {}
_support_chat_stream_ticket_lock = RLock()


def _issue_support_chat_stream_ticket(user_id: int) -> str:
    ticket = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + SUPPORT_CHAT_STREAM_TICKET_TTL_SECONDS
    with _support_chat_stream_ticket_lock:
        now = time.monotonic()
        expired = [
            existing_ticket
            for existing_ticket, (_, ticket_expires_at) in _support_chat_stream_tickets.items()
            if ticket_expires_at <= now
        ]
        for existing_ticket in expired:
            _support_chat_stream_tickets.pop(existing_ticket, None)
        _support_chat_stream_tickets[ticket] = (user_id, expires_at)
    return ticket


def _consume_support_chat_stream_ticket(ticket: str) -> Optional[int]:
    if not ticket:
        return None
    with _support_chat_stream_ticket_lock:
        item = _support_chat_stream_tickets.pop(ticket, None)
    if not item:
        return None
    user_id, expires_at = item
    if expires_at <= time.monotonic():
        return None
    return user_id


@router.get("/threads", response_model=SupportChatThreadListResponse)
async def list_admin_web_chat_threads(
    q: Optional[str] = None,
    limit: int = 40,
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    safe_limit = max(1, min(int(limit or 40), 100))
    return SupportChatThreadListResponse(
        items=await list_support_threads(db, q=q, limit=safe_limit)
    )


@router.get("/users/{user_id}/messages", response_model=list[SupportChatMessageResponse])
async def get_admin_web_chat_messages(
    user_id: int,
    limit: int = 100,
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    target_user = await load_support_target_user(db, user_id)
    safe_limit = max(1, min(int(limit or 100), 200))
    return await list_support_messages(db, target_user=target_user, limit=safe_limit)


@router.post("/users/{user_id}/messages", response_model=SupportChatMessageResponse)
async def send_admin_web_chat_message(
    user_id: int,
    payload: SupportMessageCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    target_user = await load_support_target_user(db, user_id)
    signal = await create_staff_support_message(
        db,
        target_user=target_user,
        staff_user=admin,
        text=payload.text,
    )
    return SupportChatMessageResponse(**support_message_payload(signal))


@router.post("/stream-ticket", response_model=SupportChatStreamTicketResponse)
async def create_admin_web_chat_stream_ticket(
    admin: User = Depends(get_current_admin),
):
    return SupportChatStreamTicketResponse(
        ticket=_issue_support_chat_stream_ticket(admin.telegram_id),
        expires_in=SUPPORT_CHAT_STREAM_TICKET_TTL_SECONDS,
    )


@router.websocket("/stream")
async def stream_admin_web_chat(
    websocket: WebSocket,
    ticket: str = Query(""),
):
    user_id = _consume_support_chat_stream_ticket(ticket)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).filter(User.telegram_id == user_id))
        user = result.scalars().first()

    if not user or not is_staff_role(user.role):
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await support_staff_stream_hub.connect(user.telegram_id, websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await support_staff_stream_hub.disconnect(user.telegram_id, websocket)
