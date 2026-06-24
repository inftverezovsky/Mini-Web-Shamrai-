import ipaddress
import secrets
import time
from threading import RLock
from typing import Any, Optional
from urllib.parse import urlparse
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.api.deps import get_current_user
from src.core.config import settings
from src.core.roles import is_staff_role
from src.models.database import AsyncSessionLocal, get_db
from src.models.models import Bet, ForecastRequest, PersonalSignal, User
from src.services.forecast_delivery import (
    FORECAST_CONTACT_DRAFT_TEXT,
    build_web_forecast_signal_data,
    build_web_paid_set_signal_data,
    build_web_teaser_signal_data,
    notify_sales_manager_for_request,
    set_forecast_request_declined,
    set_forecast_request_interested,
)
from src.services.signals import signal_stream_hub, signal_to_payload, web_push_configured

router = APIRouter(prefix="/signals", tags=["Personal Signals"])


class PersonalSignalResponse(BaseModel):
    id: int
    user_id: int
    text: str
    type: str
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    direction: Optional[str] = None
    author_label: Optional[str] = None
    sender_user_id: Optional[int] = None
    sender_role: Optional[str] = None


class ForecastSignalActionResponse(BaseModel):
    status: str
    message: str
    forecast_request_id: str
    action: str = "accepted"
    contact: Optional[dict[str, Any]] = None


class SupportMessageCreate(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class WebPushKeys(BaseModel):
    p256dh: str = Field(min_length=16, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class WebPushSubscriptionPayload(BaseModel):
    endpoint: str = Field(min_length=12, max_length=2048)
    expirationTime: Optional[int] = Field(default=None, ge=0)
    keys: WebPushKeys

    @field_validator("endpoint")
    @classmethod
    def validate_endpoint(cls, value: str) -> str:
        endpoint = value.strip()
        parsed = urlparse(endpoint)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise ValueError("Web Push endpoint must be a public HTTPS URL")
        if parsed.username or parsed.password:
            raise ValueError("Web Push endpoint credentials are not allowed")
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Web Push endpoint port is invalid") from exc

        hostname = parsed.hostname.strip("[]").rstrip(".").lower()
        reserved_names = ("localhost", ".localhost", ".local", ".invalid", ".test", ".example")
        if hostname in {"localhost", "local"} or hostname.endswith(reserved_names[1:]):
            raise ValueError("Web Push endpoint host must be public")

        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return endpoint

        if (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("Web Push endpoint host must be public")
        return endpoint


class WebPushSubscriptionResponse(BaseModel):
    status: str
    configured: bool = False


class ConnectionOnboardingResponse(BaseModel):
    status: str = "ok"
    checklist: dict[str, bool] = Field(default_factory=dict)
    created_signal_types: list[str] = Field(default_factory=list)


class WebPushPublicKeyResponse(BaseModel):
    public_key: str = ""
    configured: bool = False


class SignalStreamTicketResponse(BaseModel):
    ticket: str
    expires_in: int


SIGNAL_STREAM_TICKET_TTL_SECONDS = 30
_signal_stream_tickets: dict[str, tuple[int, float]] = {}
_signal_stream_ticket_lock = RLock()


def _serialize_signal(signal: PersonalSignal) -> PersonalSignalResponse:
    return PersonalSignalResponse(**signal_to_payload(signal))


def _forecast_request_id_from_signal(signal: PersonalSignal) -> Optional[UUID]:
    signal_data = signal.data or {}
    raw_request_id = signal_data.get("forecast_request_id")
    if not raw_request_id:
        return None
    try:
        return UUID(str(raw_request_id))
    except (TypeError, ValueError):
        return None


async def _forecast_requests_for_signals(
    db: AsyncSession,
    *,
    signals: list[PersonalSignal],
    user_id: int,
) -> dict[str, ForecastRequest]:
    request_ids = {
        request_id
        for request_id in (_forecast_request_id_from_signal(signal) for signal in signals)
        if request_id is not None
    }
    if not request_ids:
        return {}

    result = await db.execute(
        select(ForecastRequest)
        .filter(
            ForecastRequest.user_id == user_id,
            ForecastRequest.id.in_(request_ids),
        )
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
    )
    return {str(forecast_request.id): forecast_request for forecast_request in result.scalars().all()}


def _is_missing_signal_data_value(value: Any) -> bool:
    return value is None or value == "" or value == []


def _enrich_forecast_signal_data(
    signal: PersonalSignal,
    signal_data: dict[str, Any],
    forecast_request: ForecastRequest,
) -> dict[str, Any]:
    if signal.type == "forecast_full":
        fresh_data = build_web_forecast_signal_data(
            forecast_request,
            status_value=forecast_request.status,
        )
    elif signal.type == "forecast_teaser":
        fresh_data = (
            build_web_paid_set_signal_data(forecast_request, None)
            if signal_data.get("request_kind") == "paid_set"
            else build_web_teaser_signal_data(forecast_request, None)
        )
        fresh_data["forecast_status"] = forecast_request.status
    else:
        return signal_data

    enriched_data = dict(signal_data)
    for key, value in fresh_data.items():
        if _is_missing_signal_data_value(enriched_data.get(key)):
            enriched_data[key] = value
    enriched_data["forecast_status"] = forecast_request.status
    return enriched_data


def _serialize_signal_with_forecast_data(
    signal: PersonalSignal,
    forecast_requests: dict[str, ForecastRequest],
) -> PersonalSignalResponse:
    payload = signal_to_payload(signal)
    signal_data = dict(payload.get("data") or {})
    request_id = signal_data.get("forecast_request_id")
    forecast_request = forecast_requests.get(str(request_id)) if request_id else None
    if forecast_request:
        signal_data = _enrich_forecast_signal_data(signal, signal_data, forecast_request)
    payload["data"] = signal_data
    return PersonalSignalResponse(**payload)


def _issue_signal_stream_ticket(user_id: int) -> str:
    ticket = secrets.token_urlsafe(32)
    expires_at = time.monotonic() + SIGNAL_STREAM_TICKET_TTL_SECONDS
    with _signal_stream_ticket_lock:
        now = time.monotonic()
        expired = [
            existing_ticket
            for existing_ticket, (_, ticket_expires_at) in _signal_stream_tickets.items()
            if ticket_expires_at <= now
        ]
        for existing_ticket in expired:
            _signal_stream_tickets.pop(existing_ticket, None)
        _signal_stream_tickets[ticket] = (user_id, expires_at)
    return ticket


def _consume_signal_stream_ticket(ticket: str) -> Optional[int]:
    if not ticket:
        return None
    with _signal_stream_ticket_lock:
        item = _signal_stream_tickets.pop(ticket, None)
    if not item:
        return None
    user_id, expires_at = item
    if expires_at <= time.monotonic():
        return None
    return user_id


@router.get("/web-push/public-key", response_model=WebPushPublicKeyResponse)
async def get_web_push_public_key():
    public_key = settings.WEB_PUSH_VAPID_PUBLIC_KEY.strip()
    return WebPushPublicKeyResponse(public_key=public_key, configured=bool(public_key and web_push_configured()))


@router.put("/web-push/subscription", response_model=WebPushSubscriptionResponse)
async def save_web_push_subscription(
    subscription: WebPushSubscriptionPayload,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.web_push_subscription = subscription.model_dump()
    await db.flush()
    if not is_staff_role(current_user.role):
        from src.services.connection_onboarding import sync_connection_onboarding

        await sync_connection_onboarding(db, current_user)
    return WebPushSubscriptionResponse(status="saved", configured=web_push_configured())


@router.delete("/web-push/subscription", response_model=WebPushSubscriptionResponse)
async def delete_web_push_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_user.web_push_subscription = None
    await db.flush()
    return WebPushSubscriptionResponse(status="deleted", configured=web_push_configured())


@router.post("/connection-onboarding/sync", response_model=ConnectionOnboardingResponse)
async def sync_connection_onboarding_state(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if is_staff_role(current_user.role):
        return ConnectionOnboardingResponse(status="skipped", checklist={"complete": True})

    from src.services.connection_onboarding import sync_connection_onboarding

    return ConnectionOnboardingResponse(**await sync_connection_onboarding(db, current_user))


@router.post("/stream-ticket", response_model=SignalStreamTicketResponse)
async def create_signal_stream_ticket(
    current_user: User = Depends(get_current_user),
):
    return SignalStreamTicketResponse(
        ticket=_issue_signal_stream_ticket(current_user.telegram_id),
        expires_in=SIGNAL_STREAM_TICKET_TTL_SECONDS,
    )


@router.get("/history", response_model=list[PersonalSignalResponse])
async def get_signal_history(
    limit: int = Query(60, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(PersonalSignal)
        .filter(PersonalSignal.user_id == current_user.telegram_id)
        .order_by(PersonalSignal.created_at.desc(), PersonalSignal.id.desc())
        .limit(limit)
    )
    signals = list(reversed(result.scalars().all()))
    forecast_requests = await _forecast_requests_for_signals(
        db,
        signals=signals,
        user_id=current_user.telegram_id,
    )
    return [_serialize_signal_with_forecast_data(signal, forecast_requests) for signal in signals]


@router.post("/messages", response_model=PersonalSignalResponse)
async def send_support_message_from_web_chat(
    payload: SupportMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from src.services.support_web_chat import create_client_support_message

    if is_staff_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Сообщения в клиентский чат доступны только клиентам",
        )

    signal = await create_client_support_message(
        db,
        client_user=current_user,
        text=payload.text,
    )
    return _serialize_signal(signal)


@router.post("/forecast-requests/{request_id}/{action}", response_model=ForecastSignalActionResponse)
async def answer_forecast_request_from_web_chat(
    request_id: UUID,
    action: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if action == "take":
        forecast_request, message, should_notify_sales = await set_forecast_request_interested(
            db,
            request_id=request_id,
            actor_user_id=current_user.telegram_id,
            notify_sales_manager_now=False,
            auto_delivery_method="auto",
            auto_delivery_now=False,
        )
        await db.commit()
        if should_notify_sales:
            background_tasks.add_task(notify_sales_manager_for_request, forecast_request.id)
        contact_required = (
            forecast_request.status == "announced"
            and FORECAST_CONTACT_DRAFT_TEXT in message
        )
        return ForecastSignalActionResponse(
            status=forecast_request.status,
            message=message,
            forecast_request_id=str(forecast_request.id),
            action="contact_required" if contact_required else "accepted",
            contact={
                "channel": "web",
                "draft_text": FORECAST_CONTACT_DRAFT_TEXT,
            } if contact_required else None,
        )

    if action == "decline":
        forecast_request, message = await set_forecast_request_declined(
            db,
            request_id=request_id,
            actor_user_id=current_user.telegram_id,
        )
        await db.commit()
        return ForecastSignalActionResponse(
            status=forecast_request.status,
            message=message,
            forecast_request_id=str(forecast_request.id),
            action="accepted",
        )

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Неизвестное действие",
    )


@router.websocket("/stream")
async def stream_personal_signals(
    websocket: WebSocket,
    ticket: str = Query(""),
):
    user_id = _consume_signal_stream_ticket(ticket)
    if not user_id:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).filter(User.telegram_id == user_id))
        user = result.scalars().first()

    if not user:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    await signal_stream_hub.connect(user.telegram_id, websocket)
    try:
        while True:
            message = await websocket.receive_text()
            if message == "ping":
                await websocket.send_json({"type": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await signal_stream_hub.disconnect(user.telegram_id, websocket)
