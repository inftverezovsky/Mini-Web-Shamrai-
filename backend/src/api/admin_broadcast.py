"""
Admin Broadcast & Smart Targeted Announcements API.
Handles announcement creation with coupon file uploads, 
target audience filtering, and push notification dispatch.
"""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from uuid import UUID
from typing import Optional, List

from src.models.database import get_db
from src.models.models import AdminAuditLog, Bookmaker, ForecastRequest, User, Bet
from src.api.deps import get_current_admin
from src.api.payments import call_telegram_api
from src.core.bookmaker_links import normalize_bookmaker_links, normalize_match_url
from src.core.config import settings
from src.core.quiet_hours import current_notification_time, user_is_in_quiet_hours
from src.core.roles import STAFF_ROLES
from src.core.telegram_delivery import user_can_receive_personal_telegram
from src.core.telegram_text import append_contact_footer
from src.schemas.schemas import BetResponse, ForecastRequestResponse
from src.services.forecast_delivery import (
    FORECAST_STATUS_ANNOUNCED,
    FORECAST_STATUS_CANCELLED,
    FORECAST_STATUS_DECLINED,
    FORECAST_STATUS_INTERESTED,
    FORECAST_STATUS_PROCESSING,
    FORECAST_STATUS_REMOVED,
    PLACEHOLDER_EVENT_NAME,
    build_forecast_teaser_payload,
    build_teaser_message,
    build_web_teaser_signal_data,
    cancel_forecast_request,
    deliver_forecast_request,
    load_forecast_request,
    refreshed_client_delivery_method,
)
from src.services.match_access import revoke_user_bet_access
from src.services.signals import broadcast_personal_signals
from src.services.vk_delivery import (
    _vk_broadcast_concurrency,
    build_vk_forecast_keyboard,
    html_to_vk_text,
    is_vk_message_permission_error,
    local_static_asset_path,
    mark_vk_messages_denied,
    refresh_vk_delivery_status,
    send_vk_message_to_user,
    user_can_receive_vk_messages,
)

router = APIRouter(tags=["Admin Broadcast"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "coupons")

class ForecastBulkSendResponse(BaseModel):
    status: str
    total: int
    sent: int
    failed: int
    errors: List[str] = Field(default_factory=list)


class ForecastBroadcastFullResponse(BaseModel):
    bet: BetResponse
    auto_send_enabled: bool = False
    auto_send: Optional[ForecastBulkSendResponse] = None


def _telegram_broadcast_concurrency() -> int:
    try:
        value = int(settings.TELEGRAM_BROADCAST_CONCURRENCY or 1)
    except Exception:
        value = 1
    return max(1, min(value, 30))


def _bookmaker_links_from_form(form_data) -> Optional[List[str]]:
    values = [
        str(value)
        for value in form_data.getlist("bookmaker_links")
        if value is not None
    ]
    values.extend(
        str(value)
        for value in form_data.getlist("bookmaker_links[]")
        if value is not None
    )
    return values if values else None


async def _send_telegram_jobs(
    jobs: List[tuple[int, str, dict]],
    *,
    log_prefix: str,
) -> tuple[int, int, List[str]]:
    if not jobs:
        return 0, 0, []

    semaphore = asyncio.Semaphore(_telegram_broadcast_concurrency())

    async def send_one(user_id: int, method: str, payload: dict) -> tuple[bool, Optional[str]]:
        async with semaphore:
            try:
                call_result = await asyncio.to_thread(call_telegram_api, method, payload)
                if (
                    method == "sendPhoto"
                    and not call_result.get("ok")
                    and "PHOTO_INVALID_DIMENSIONS" in str(call_result.get("description") or "").upper()
                ):
                    fallback_payload = dict(payload)
                    fallback_payload["document"] = fallback_payload.pop("photo", None)
                    call_result = await asyncio.to_thread(call_telegram_api, "sendDocument", fallback_payload)
            except Exception as exc:
                print(f"[{log_prefix}] Failed to send to {user_id}: {exc}")
                return False, str(exc)
            if not call_result.get("ok"):
                description = call_result.get("description", "unknown error")
                print(
                    f"[{log_prefix}] Telegram failed for {user_id}: "
                    f"{description}"
                )
                return False, str(description)
            return True, None

    results = await asyncio.gather(*(send_one(user_id, method, payload) for user_id, method, payload in jobs))
    sent_count = sum(1 for ok, _ in results if ok)
    errors: List[str] = []
    for ok, error in results:
        if not ok and error and error not in errors:
            errors.append(error)
    return sent_count, len(results) - sent_count, errors[:5]


async def _send_vk_jobs(
    jobs: List[tuple[int, User, str, Optional[dict], Optional[str]]],
    *,
    db: AsyncSession,
    log_prefix: str,
) -> tuple[int, int, List[str]]:
    if not jobs:
        return 0, 0, []

    semaphore = asyncio.Semaphore(_vk_broadcast_concurrency())

    async def send_one(
        user_id: int,
        user: User,
        message: str,
        keyboard: Optional[dict],
        image_path: Optional[str],
    ) -> tuple[bool, Optional[str], Optional[User]]:
        async with semaphore:
            try:
                call_result = await asyncio.to_thread(
                    send_vk_message_to_user,
                    user,
                    message,
                    keyboard=keyboard,
                    image_path=image_path,
                )
            except Exception as exc:
                print(f"[{log_prefix}] Failed to send VK to {user_id}: {exc}")
                return False, str(exc), None
            if not call_result.get("ok"):
                permission_error = is_vk_message_permission_error(call_result)
                description = (
                    "VK не разрешает отправлять личные сообщения этому клиенту"
                    if permission_error
                    else call_result.get("description", "unknown error")
                )
                print(f"[{log_prefix}] VK failed for {user_id}: {description}")
                return False, str(description), user if permission_error else None
            return True, None, None

    results = await asyncio.gather(
        *(send_one(user_id, user, message, keyboard, image_path) for user_id, user, message, keyboard, image_path in jobs)
    )
    denied_users = [user for ok, _, user in results if not ok and user is not None]
    for user in denied_users:
        await mark_vk_messages_denied(db, user)
    if denied_users:
        await db.commit()

    sent_count = sum(1 for ok, _, _ in results if ok)
    errors: List[str] = []
    for ok, error, _ in results:
        if not ok and error and error not in errors:
            errors.append(error)
    return sent_count, len(results) - sent_count, errors[:5]


def _delivery_breakdown(
    *,
    telegram_total: int,
    telegram_sent: int,
    telegram_failed: int,
    telegram_errors: List[str],
    vk_total: int,
    vk_sent: int,
    vk_failed: int,
    vk_errors: List[str],
    vk_notifications_enabled: int,
    web_total: int = 0,
    web_sent: int = 0,
    web_failed: int = 0,
    web_errors: Optional[List[str]] = None,
    web_push_audience: int = 0,
    web_push_sent: int = 0,
    web_push_failed: int = 0,
    web_push_missing_permission: int = 0,
    web_push_errors: Optional[List[str]] = None,
) -> dict:
    resolved_web_errors = web_errors or []
    resolved_web_push_errors = web_push_errors or []
    return {
        "telegram": {
            "total_audience": telegram_total,
            "sent": telegram_sent,
            "failed": telegram_failed,
            "errors": telegram_errors,
        },
        "vk_messages": {
            "total_audience": vk_total,
            "sent": vk_sent,
            "failed": vk_failed,
            "errors": vk_errors,
        },
        "vk_notifications": {
            "requested": vk_notifications_enabled,
            "sent": 0,
            "failed": 0,
        },
        "web_chat": {
            "total_audience": web_total,
            "sent": web_sent,
            "failed": web_failed,
            "errors": resolved_web_errors,
        },
        "web_push": {
            "total_audience": web_push_audience,
            "sent": web_push_sent,
            "failed": web_push_failed,
            "missing_permission": web_push_missing_permission,
            "errors": resolved_web_push_errors,
        },
        "sent": telegram_sent + vk_sent + web_sent,
        "failed": telegram_failed + vk_failed + web_failed,
        "errors": [*telegram_errors, *vk_errors, *resolved_web_errors, *resolved_web_push_errors][:10],
    }


async def _refresh_vk_audience(
    db: AsyncSession,
    users: List[User],
    *,
    refresh_group: bool = False,
    commit: bool = True,
) -> List[User]:
    candidates = [user for user in users if getattr(user, "vk_user_id", None)]
    changed = False
    for user in candidates:
        status_payload = await refresh_vk_delivery_status(db, user, refresh_group=refresh_group)
        changed = bool(status_payload.get("changed")) or changed
    if changed and commit:
        await db.commit()
    return [user for user in candidates if user_can_receive_vk_messages(user)]


async def _client_delivery_method(db: AsyncSession, user: User) -> str:
    return await refreshed_client_delivery_method(db, user)


def _parse_bookmaker_id_values(values: Optional[List[str]]) -> List[int]:
    bookmaker_ids: List[int] = []
    for raw_value in values or []:
        if raw_value is None:
            continue
        for part in str(raw_value).split(","):
            clean = part.strip()
            if not clean:
                continue
            try:
                bookmaker_id = int(clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Некорректный ID букмекера"
                )
            if bookmaker_id not in bookmaker_ids:
                bookmaker_ids.append(bookmaker_id)
    return bookmaker_ids


def _merge_bookmaker_ids(
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
) -> List[int]:
    selected_ids: List[int] = []
    if bookmaker_id:
        selected_ids.append(bookmaker_id)
    for selected_id in bookmaker_ids or []:
        if selected_id and selected_id not in selected_ids:
            selected_ids.append(selected_id)
    return selected_ids


def _normalized_target_value(value: Optional[object]) -> str:
    return str(value or "").strip().lower()


def _bookmaker_logo_path(bookmaker: Bookmaker) -> str:
    code = str(bookmaker.code or "other").strip().lower()
    if code == "other":
        return "/bookmakers/other.svg"
    if code == "olimp":
        code = "olimpbet"
    return f"/bookmakers/transparent/{code}.png"


def _web_bookmaker_items(bookmakers: List[Bookmaker], raw_url: Optional[str] = None) -> List[dict]:
    normalized_url = normalize_match_url(raw_url)
    return [
        {
            "id": bookmaker.id,
            "name": bookmaker.name,
            "code": bookmaker.code,
            "logo_url": _bookmaker_logo_path(bookmaker),
            "url": normalized_url,
        }
        for bookmaker in bookmakers
    ]


async def _store_coupon_image(coupon_image: Optional[UploadFile]) -> Optional[str]:
    if not coupon_image or not coupon_image.filename:
        return None

    allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = os.path.splitext(coupon_image.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимый формат файла. Разрешены: {', '.join(sorted(allowed_ext))}"
        )

    contents = await coupon_image.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл слишком большой. Максимум 5 МБ."
        )

    os.makedirs(STATIC_DIR, exist_ok=True)
    filename = f"{uuid.uuid4().hex}{ext}"
    filepath = os.path.join(STATIC_DIR, filename)
    with open(filepath, "wb") as file_obj:
        file_obj.write(contents)
    return f"/static/coupons/{filename}"


async def _load_private_forecast_bet(db: AsyncSession, bet_id: UUID) -> Bet:
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(
            selectinload(Bet.bookmaker),
            selectinload(Bet.bookmakers),
        )
    )
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Закрытый прогноз не найден",
        )
    if bet.delivery_mode != "sales_private":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот прогноз не относится к закрытой рассылке",
        )
    return bet


async def _apply_full_forecast_fields(
    bet: Bet,
    *,
    event_name: Optional[str],
    outcome: Optional[str],
    coefficient: Optional[Decimal],
    sport_type: Optional[str],
    description: Optional[str],
    match_link: Optional[str],
    bookmaker_links: Optional[List[str]],
    category: Optional[str],
    live_ends_at: Optional[datetime],
    coupon_image: Optional[UploadFile],
) -> Bet:
    clean_event_name = (event_name or "").strip()
    clean_outcome = (outcome or "").strip()
    if not clean_event_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите матч для полной ставки",
        )
    if not clean_outcome:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите исход для полной ставки",
        )

    coupon_url = await _store_coupon_image(coupon_image)
    if coupon_url:
        bet.coupon_image_url = coupon_url
    if not bet.coupon_image_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Загрузите скрин купона для полной ставки",
        )

    bet.event_name = clean_event_name
    bet.outcome = clean_outcome
    bet.description = description.strip() if description else None
    bet.match_link = match_link.strip() if match_link else None
    if bookmaker_links is not None:
        bet.bookmaker_links = normalize_bookmaker_links(
            bookmaker_links,
            allowed_bookmaker_ids=bet.bookmaker_ids,
        )
    if not any(
        isinstance(item, dict) and str(item.get("url") or "").strip()
        for item in (bet.bookmaker_links or [])
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Добавьте хотя бы одну ссылку по БК",
        )
    if coefficient is not None:
        bet.coefficient = coefficient
    if sport_type and sport_type.strip():
        bet.sport_type = sport_type.strip()
    if category and category.strip():
        bet.category = category.strip()
    if live_ends_at is not None:
        bet.live_ends_at = live_ends_at
    return bet


async def _load_bookmakers(db: AsyncSession, bookmaker_ids: List[int]) -> List[Bookmaker]:
    if not bookmaker_ids:
        return []

    result = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(bookmaker_ids)))
    bookmakers_by_id = {bookmaker.id: bookmaker for bookmaker in result.scalars().all()}
    missing_ids = [bookmaker_id for bookmaker_id in bookmaker_ids if bookmaker_id not in bookmakers_by_id]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Selected bookmaker not found: {', '.join(map(str, missing_ids))}"
        )
    return [bookmakers_by_id[bookmaker_id] for bookmaker_id in bookmaker_ids]


async def _get_target_users(
    db: AsyncSession,
    sport_filter: Optional[str] = None,
    bookmaker_id: Optional[int] = None,
    bookmaker_ids: Optional[List[int]] = None,
    delivery_channel: str = "telegram",
) -> List[User]:
    """Fetches users matching bookmaker targets. Sport is cosmetic and does not affect audience."""
    filters = [User.role.notin_(list(STAFF_ROLES))]
    if delivery_channel == "telegram":
        filters.append(User.telegram_id > 0)

    query = (
        select(User)
        .filter(*filters)
        .options(selectinload(User.bookmakers))
    )

    result = await db.execute(query)
    users = list({u.telegram_id: u for u in result.scalars().all()}.values())
    if delivery_channel == "telegram":
        users = [u for u in users if user_can_receive_personal_telegram(u)]
    elif delivery_channel == "vk":
        users = [u for u in users if getattr(u, "vk_user_id", None)]
    elif delivery_channel == "any":
        users = users
    elif delivery_channel == "web":
        users = users
    
    selected_bookmaker_ids = _merge_bookmaker_ids(bookmaker_id, bookmaker_ids)
    if selected_bookmaker_ids:
        selected_id_set = set(selected_bookmaker_ids)
        bookmaker_result = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(selected_bookmaker_ids)))
        selected_bookmakers = bookmaker_result.scalars().all()
        selected_bookmaker_values = {
            value
            for bookmaker in selected_bookmakers
            for value in (
                _normalized_target_value(bookmaker.id),
                _normalized_target_value(bookmaker.code),
                _normalized_target_value(bookmaker.name),
            )
            if value
        }

        users = [
            u for u in users
            if any(bookmaker.id in selected_id_set for bookmaker in u.bookmakers)
            or _normalized_target_value(u.primary_bookmaker) in selected_bookmaker_values
        ]

    if delivery_channel == "vk":
        users = await _refresh_vk_audience(db, users)
    
    return users


async def _get_smart_target_users(
    db: AsyncSession,
    *,
    sport_filter: Optional[str],
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
    delivery_channel: str,
    min_coef: Optional[float],
) -> List[User]:
    users = await _get_target_users(
        db,
        sport_filter,
        bookmaker_id,
        bookmaker_ids,
        delivery_channel=delivery_channel,
    )
    if min_coef is not None:
        users = [user for user in users if user.alert_min_coef <= min_coef]
    if sport_filter:
        normalized_sport = sport_filter.strip().lower()
        users = [
            user for user in users
            if not user.preferred_sports
            or any(str(sport).strip().lower() == normalized_sport for sport in user.preferred_sports)
        ]
    now = current_notification_time()
    return [user for user in users if not user_is_in_quiet_hours(user, now)]


def _web_push_report_values(report: Optional[dict]) -> dict:
    report = report or {}
    return {
        "web_push_audience": int(report.get("web_push_audience") or 0),
        "web_push_sent": int(report.get("web_push_sent") or 0),
        "web_push_failed": int(report.get("web_push_failed") or 0),
        "web_push_missing_permission": int(report.get("web_push_missing_permission") or 0),
        "web_push_errors": list(report.get("web_push_errors") or []),
    }


@router.post("/admin/announcements")
async def create_announcement(
    request: Request,
    title: str = Form(...),
    body: Optional[str] = Form(None),
    announcement_type: str = Form("general"),  # "general" | "bet_promo" | "flash_sale" | "urgent"
    sport_filter: Optional[str] = Form(None),
    bookmaker_id: Optional[int] = Form(None),
    bookmaker_name: Optional[str] = Form(None),
    bookmaker_names: Optional[str] = Form(None),
    min_coef: Optional[float] = Form(None),
    match_link: Optional[str] = Form(None),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/announcements
    Creates a targeted announcement and dispatches it to eligible subscribers.
    Supports optional coupon image upload (drag-and-drop from frontend).
    """
    coupon_url = None
    form_data = await request.form()
    selected_bookmaker_ids = _parse_bookmaker_id_values(
        [str(value) for value in form_data.getlist("bookmaker_ids")]
    )
    selected_bookmaker_names = bookmaker_names or bookmaker_name
    selected_bookmakers = await _load_bookmakers(
        db,
        _merge_bookmaker_ids(bookmaker_id, selected_bookmaker_ids),
    ) if _merge_bookmaker_ids(bookmaker_id, selected_bookmaker_ids) else []
    
    # Handle coupon image upload
    if coupon_image and coupon_image.filename:
        # Validate file type
        allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
        ext = os.path.splitext(coupon_image.filename)[1].lower()
        if ext not in allowed_ext:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Недопустимый формат файла. Разрешены: {', '.join(allowed_ext)}"
            )
        
        # Validate file size (max 5MB)
        contents = await coupon_image.read()
        if len(contents) > 5 * 1024 * 1024:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Файл слишком большой. Максимум 5 МБ."
            )
        
        # Save file
        os.makedirs(STATIC_DIR, exist_ok=True)
        filename = f"{uuid.uuid4().hex}{ext}"
        filepath = os.path.join(STATIC_DIR, filename)
        with open(filepath, "wb") as f:
            f.write(contents)
        coupon_url = f"/static/coupons/{filename}"
    
    # Get target audiences per channel using the same smart notification filters.
    telegram_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="telegram",
        min_coef=min_coef,
    )
    vk_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="vk",
        min_coef=min_coef,
    )
    web_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="web",
        min_coef=min_coef,
    )
    
    # Build announcement message
    type_emoji = {
        "general": "📢",
        "bet_promo": "🎯",
        "flash_sale": "⚡",
        "urgent": "🚨"
    }
    emoji = type_emoji.get(announcement_type, "📢")
    
    body_text = (body or "").strip()
    if not body_text:
        fallback_parts = ["Есть новый анонс"]
        if min_coef:
            fallback_parts.append(f"с коэффициентом {min_coef}")
        if selected_bookmaker_names:
            fallback_parts.append(f"в {selected_bookmaker_names}")
        body_text = " ".join(fallback_parts) + "."

    message_text = f"{emoji} <b>{title}</b>\n\n{body_text}"
    if selected_bookmaker_names:
        message_text += f"\n\n🏦 БК: <b>{selected_bookmaker_names}</b>"
    if match_link:
        message_text += f"\n\n🔗 <a href='{match_link}'>Перейти к матчу</a>"
    if min_coef:
        message_text += f"\n\n📊 Коэффициент: <b>{min_coef}</b>"
    message_text = append_contact_footer(message_text)

    # Dispatch to subscribers in limited parallel batches so the admin UI does not wait on every Telegram call serially.
    telegram_jobs: List[tuple[int, str, dict]] = []
    for user in telegram_subscribers:
        if coupon_url:
            api_url = settings.API_BASE_URL.rstrip("/")
            telegram_jobs.append((
                user.telegram_id,
                "sendPhoto",
                {
                    "chat_id": user.telegram_id,
                    "photo": f"{api_url}{coupon_url}",
                    "caption": message_text,
                    "parse_mode": "HTML",
                },
            ))
        else:
            telegram_jobs.append((
                user.telegram_id,
                "sendMessage",
                {
                    "chat_id": user.telegram_id,
                    "text": message_text,
                    "parse_mode": "HTML",
                },
            ))

    vk_jobs: List[tuple[int, User, str, Optional[dict], Optional[str]]] = []
    vk_message_text = html_to_vk_text(message_text)
    image_path = local_static_asset_path(coupon_url)
    for user in vk_subscribers:
        vk_jobs.append((user.telegram_id, user, vk_message_text, None, image_path))

    web_report = await broadcast_personal_signals(
        db,
        users=web_subscribers,
        text=vk_message_text,
        signal_type="announcement",
        data={
            "message_text": vk_message_text,
            "message_html": message_text,
            "coupon_image_url": coupon_url,
            "bookmakers": _web_bookmaker_items(selected_bookmakers, match_link),
            "match_link": normalize_match_url(match_link),
        },
        send_telegram=False,
        send_web_push=True,
        return_report=True,
    )
    web_sent = int(web_report.get("created", 0))
    telegram_sent, telegram_failed, telegram_errors = await _send_telegram_jobs(telegram_jobs, log_prefix="Broadcast")
    vk_sent, vk_failed, vk_errors = await _send_vk_jobs(vk_jobs, db=db, log_prefix="Broadcast")
    delivery = _delivery_breakdown(
        telegram_total=len(telegram_subscribers),
        telegram_sent=telegram_sent,
        telegram_failed=telegram_failed,
        telegram_errors=telegram_errors,
        vk_total=len(vk_subscribers),
        vk_sent=vk_sent,
        vk_failed=vk_failed,
        vk_errors=vk_errors,
        vk_notifications_enabled=sum(1 for user in vk_subscribers if user.vk_notifications_allowed),
        web_total=len(web_subscribers),
        web_sent=web_sent,
        **_web_push_report_values(web_report),
    )
    total_audience = len({user.telegram_id for user in [*telegram_subscribers, *vk_subscribers, *web_subscribers]})
    
    return {
        "status": "success",
        "announcement": {
            "title": title,
            "type": announcement_type,
            "sport_filter": sport_filter,
            "bookmaker_id": bookmaker_id,
            "bookmaker_ids": _merge_bookmaker_ids(bookmaker_id, selected_bookmaker_ids),
            "bookmaker_name": selected_bookmaker_names,
            "coupon_image_url": coupon_url,
            "match_link": match_link,
        },
        "delivery": {
            "total_audience": total_audience,
            **delivery,
        },
        "total_audience": total_audience,
        "sent": delivery["sent"],
        "failed": delivery["failed"],
        "errors": delivery["errors"],
        "created_by": current_admin.telegram_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }


@router.post("/admin/forecast-broadcast")
async def create_forecast_broadcast(
    request: Request,
    coefficient: Decimal = Form(...),
    bookmaker_id: Optional[int] = Form(None),
    category: str = Form("prematch"),
    live_ends_at: Optional[datetime] = Form(None),
    price_stars: Optional[int] = Form(None),
    brain_score: Optional[int] = Form(5),
    api_match_id: Optional[str] = Form(None),
    sport_type: Optional[str] = Form(None),
    teaser_text: Optional[str] = Form(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a private forecast and sends only a teaser with Telegram buttons to matching users.
    The full forecast is delivered later by the sales manager.
    """
    form_data = await request.form()
    selected_bookmaker_ids = _merge_bookmaker_ids(
        bookmaker_id,
        _parse_bookmaker_id_values([str(value) for value in form_data.getlist("bookmaker_ids")]),
    )
    if not selected_bookmaker_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для закрытого прогноза выберите хотя бы одну БК",
        )

    selected_bookmakers = await _load_bookmakers(db, selected_bookmaker_ids)

    bet = Bet(
        event_name=PLACEHOLDER_EVENT_NAME,
        coefficient=coefficient,
        bookmaker_id=selected_bookmaker_ids[0],
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=sport_type,
        status="pending",
        delivery_mode="sales_private",
        author_id=current_admin.telegram_id,
    )
    bet.bookmakers = selected_bookmakers
    db.add(bet)
    await db.flush()

    target_users = await _get_smart_target_users(
        db,
        sport_filter=sport_type,
        bookmaker_id=None,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="any",
        min_coef=float(coefficient),
    )

    requests_by_user_id: dict[int, ForecastRequest] = {}
    for user in target_users:
        forecast_request = ForecastRequest(
            bet=bet,
            user=user,
            status=FORECAST_STATUS_ANNOUNCED,
        )
        db.add(forecast_request)
        requests_by_user_id[user.telegram_id] = forecast_request

    await db.flush()
    await db.commit()

    forecast_requests = list(requests_by_user_id.values())
    web_message_text = html_to_vk_text(build_teaser_message(forecast_requests[0], teaser_text)) if forecast_requests else ""
    web_report = await broadcast_personal_signals(
        db,
        users=[forecast_request.user for forecast_request in forecast_requests],
        text=web_message_text,
        signal_type="forecast_teaser",
        data_by_user_id={
            forecast_request.user_id: build_web_teaser_signal_data(forecast_request, teaser_text)
            for forecast_request in forecast_requests
        },
        send_telegram=False,
        send_web_push=True,
        return_report=True,
    ) if web_message_text else 0
    web_sent = int(web_report.get("created", 0)) if isinstance(web_report, dict) else 0
    telegram_jobs = [
        (
            forecast_request.user_id,
            "sendMessage",
            build_forecast_teaser_payload(forecast_request, teaser_text),
        )
        for forecast_request in forecast_requests
        if user_can_receive_personal_telegram(forecast_request.user)
    ]
    vk_ready_users = {
        user.telegram_id: user
        for user in await _refresh_vk_audience(
            db,
            [forecast_request.user for forecast_request in forecast_requests],
        )
    }
    vk_jobs = [
        (
            forecast_request.user_id,
            forecast_request.user,
            html_to_vk_text(build_teaser_message(forecast_request, teaser_text)),
            build_vk_forecast_keyboard(forecast_request.id),
            None,
        )
        for forecast_request in forecast_requests
        if forecast_request.user_id in vk_ready_users
    ]
    telegram_sent, telegram_failed, telegram_errors = await _send_telegram_jobs(
        telegram_jobs,
        log_prefix="ForecastBroadcast",
    )
    vk_sent, vk_failed, vk_errors = await _send_vk_jobs(vk_jobs, db=db, log_prefix="ForecastBroadcast")
    telegram_total = len(telegram_jobs)
    vk_total = len(vk_jobs)
    delivery = _delivery_breakdown(
        telegram_total=telegram_total,
        telegram_sent=telegram_sent,
        telegram_failed=telegram_failed,
        telegram_errors=telegram_errors,
        vk_total=vk_total,
        vk_sent=vk_sent,
        vk_failed=vk_failed,
        vk_errors=vk_errors,
        vk_notifications_enabled=sum(
            1 for forecast_request in forecast_requests
            if forecast_request.user_id in vk_ready_users
            and forecast_request.user.vk_notifications_allowed
        ),
        web_total=len(forecast_requests),
        web_sent=web_sent,
        **_web_push_report_values(web_report if isinstance(web_report, dict) else None),
    )

    return {
        "status": "success",
        "bet_id": bet.id,
        "delivery_mode": "sales_private",
        "bookmaker_ids": selected_bookmaker_ids,
        "total_audience": len(target_users),
        "delivery": {
            "total_audience": len(target_users),
            **delivery,
        },
        "sent": delivery["sent"],
        "failed": delivery["failed"],
        "errors": delivery["errors"],
        "created_by": current_admin.telegram_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/admin/forecast-broadcast/{bet_id}/full-forecast", response_model=ForecastBroadcastFullResponse)
async def prepare_forecast_broadcast_full(
    bet_id: UUID,
    request: Request,
    event_name: Optional[str] = Form(None),
    outcome: Optional[str] = Form(None),
    coefficient: Optional[Decimal] = Form(None),
    sport_type: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    match_link: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    live_ends_at: Optional[datetime] = Form(None),
    auto_send_interested: bool = Form(False),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Saves the full private forecast after the teaser has been broadcast.
    If enabled, automatically sends it to interested clients and keeps future "take" clicks automatic.
    """
    bet = await _load_private_forecast_bet(db, bet_id)
    form_data = await request.form()
    await _apply_full_forecast_fields(
        bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        sport_type=sport_type,
        description=description,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form_data),
        category=category,
        live_ends_at=live_ends_at,
        coupon_image=coupon_image,
    )
    bet.author_id = bet.author_id or current_admin.telegram_id
    bet.auto_send_on_interest = bool(auto_send_interested)
    await db.commit()

    auto_send_result = None
    if auto_send_interested:
        result = await db.execute(
            select(ForecastRequest)
            .filter(
                ForecastRequest.bet_id == bet.id,
                ForecastRequest.status == FORECAST_STATUS_INTERESTED,
            )
            .options(
                selectinload(ForecastRequest.user).selectinload(User.bookmakers),
                selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
                selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
            )
        )
        interested_requests = result.scalars().all()
        sent_count = 0
        failed_count = 0
        errors: List[str] = []

        for forecast_request in interested_requests:
            try:
                _, access_result = await deliver_forecast_request(
                    db,
                    forecast_request=forecast_request,
                    handled_by=current_admin.telegram_id,
                    delivery_method=await _client_delivery_method(db, forecast_request.user),
                    send_to_client=True,
                )
                sent_count += 1
                if access_result.already_recorded:
                    errors.append(f"ID {forecast_request.user_id}: прогноз уже был отправлен")
            except HTTPException as exc:
                await db.rollback()
                failed_count += 1
                detail = str(exc.detail or "Не удалось отправить прогноз")
                errors.append(f"ID {forecast_request.user_id}: {detail}")
            except Exception as exc:
                await db.rollback()
                failed_count += 1
                errors.append(f"ID {forecast_request.user_id}: {exc}")

        auto_send_result = ForecastBulkSendResponse(
            status="success" if failed_count == 0 else "partial" if sent_count else "failed",
            total=len(interested_requests),
            sent=sent_count,
            failed=failed_count,
            errors=errors[:10],
        )

    refreshed_bet = await _load_private_forecast_bet(db, bet_id)
    return ForecastBroadcastFullResponse(
        bet=refreshed_bet,
        auto_send_enabled=bool(refreshed_bet.auto_send_on_interest),
        auto_send=auto_send_result,
    )


@router.get("/admin/forecast-requests", response_model=List[ForecastRequestResponse])
async def list_forecast_requests(
    request_status: Optional[str] = Query(None, alias="status"),
    bet_id: Optional[UUID] = Query(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    query = (
        select(ForecastRequest)
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
        .order_by(ForecastRequest.created_at.desc())
    )
    if request_status:
        query = query.filter(ForecastRequest.status == request_status)
    if bet_id:
        query = query.filter(ForecastRequest.bet_id == bet_id)

    result = await db.execute(query)
    return result.scalars().all()


@router.post("/admin/forecast-requests/bulk-send", response_model=ForecastBulkSendResponse)
async def send_selected_forecast_requests_from_admin(
    request: Request,
    request_ids: List[UUID] = Form(...),
    event_name: Optional[str] = Form(None),
    outcome: Optional[str] = Form(None),
    coefficient: Optional[Decimal] = Form(None),
    sport_type: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    match_link: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    live_ends_at: Optional[datetime] = Form(None),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    unique_request_ids = list(dict.fromkeys(request_ids))
    if not unique_request_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Выберите хотя бы одного клиента",
        )
    if len(unique_request_ids) > 200:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="За один раз можно отправить максимум 200 клиентам",
        )

    result = await db.execute(
        select(ForecastRequest)
        .filter(ForecastRequest.id.in_(unique_request_ids))
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
    )
    requests_by_id = {forecast_request.id: forecast_request for forecast_request in result.scalars().all()}
    forecast_requests = [
        requests_by_id[request_id]
        for request_id in unique_request_ids
        if request_id in requests_by_id
    ]

    if not forecast_requests:
        return ForecastBulkSendResponse(
            status="failed",
            total=len(unique_request_ids),
            sent=0,
            failed=len(unique_request_ids),
            errors=["Выбранные заявки не найдены"],
        )

    missing_count = len(unique_request_ids) - len(forecast_requests)
    bet_ids = {forecast_request.bet_id for forecast_request in forecast_requests}
    if len(bet_ids) > 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Выберите клиентов из одного закрытого прогноза",
        )

    form_data = await request.form()
    await _apply_full_forecast_fields(
        forecast_requests[0].bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        sport_type=sport_type,
        description=description,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form_data),
        category=category,
        live_ends_at=live_ends_at,
        coupon_image=coupon_image,
    )
    await db.commit()

    sent_count = 0
    failed_count = missing_count
    errors: List[str] = []
    if missing_count:
        errors.append(f"Не найдено заявок: {missing_count}")

    for forecast_request in forecast_requests:
        try:
            _, access_result = await deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=current_admin.telegram_id,
                delivery_method=await _client_delivery_method(db, forecast_request.user),
                send_to_client=True,
            )
            sent_count += 1
            if access_result.already_recorded:
                errors.append(f"ID {forecast_request.user_id}: прогноз уже был отправлен")
        except HTTPException as exc:
            await db.rollback()
            failed_count += 1
            detail = str(exc.detail or "Не удалось отправить прогноз")
            errors.append(f"ID {forecast_request.user_id}: {detail}")
        except Exception as exc:
            await db.rollback()
            failed_count += 1
            errors.append(f"ID {forecast_request.user_id}: {exc}")

    return ForecastBulkSendResponse(
        status="success" if sent_count and failed_count == 0 else "partial" if sent_count else "failed",
        total=len(unique_request_ids),
        sent=sent_count,
        failed=failed_count,
        errors=errors[:10],
    )


@router.post("/admin/forecast-requests/{request_id}/send", response_model=ForecastRequestResponse)
async def send_forecast_request_from_admin(
    request_id: UUID,
    request: Request,
    event_name: Optional[str] = Form(None),
    outcome: Optional[str] = Form(None),
    coefficient: Optional[Decimal] = Form(None),
    sport_type: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    match_link: Optional[str] = Form(None),
    category: Optional[str] = Form(None),
    live_ends_at: Optional[datetime] = Form(None),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    form_data = await request.form()
    await _apply_full_forecast_fields(
        forecast_request.bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        sport_type=sport_type,
        description=description,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form_data),
        category=category,
        live_ends_at=live_ends_at,
        coupon_image=coupon_image,
    )

    await deliver_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
        delivery_method=await _client_delivery_method(db, forecast_request.user),
        send_to_client=True,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-requests/{request_id}/send-saved", response_model=ForecastRequestResponse)
async def send_saved_forecast_request_from_admin(
    request_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    await deliver_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
        delivery_method=await _client_delivery_method(db, forecast_request.user),
        send_to_client=True,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-broadcast/{bet_id}/stop")
async def stop_forecast_broadcast_from_admin(
    bet_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    bet = await _load_private_forecast_bet(db, bet_id)
    already_stopped = bet.status == "deleted"
    bet.status = "deleted"
    bet.auto_send_on_interest = False
    bet.resolved_at = bet.resolved_at or datetime.now(timezone.utc)

    stoppable_statuses = {
        FORECAST_STATUS_ANNOUNCED,
        FORECAST_STATUS_INTERESTED,
        FORECAST_STATUS_DECLINED,
        FORECAST_STATUS_CANCELLED,
    }
    requests_result = await db.execute(
        select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id)
    )
    stopped_requests = 0
    skipped_processing = 0
    for forecast_request in requests_result.scalars().all():
        if forecast_request.status == FORECAST_STATUS_PROCESSING:
            skipped_processing += 1
            continue
        if forecast_request.status in stoppable_statuses:
            forecast_request.status = FORECAST_STATUS_REMOVED
            forecast_request.handled_by = current_admin.telegram_id
            stopped_requests += 1

    db.add(AdminAuditLog(
        actor_id=current_admin.telegram_id,
        action="forecast_broadcast_stopped",
        details={
            "bet_id": str(bet.id),
            "event_name": bet.event_name,
            "already_stopped": already_stopped,
            "stopped_requests": stopped_requests,
            "skipped_processing": skipped_processing,
        },
    ))
    await db.commit()
    return {
        "status": "success",
        "bet_id": str(bet.id),
        "already_stopped": already_stopped,
        "stopped_requests": stopped_requests,
        "skipped_processing": skipped_processing,
    }


@router.post("/admin/forecast-requests/{request_id}/mark-manual", response_model=ForecastRequestResponse)
async def mark_forecast_request_manual(
    request_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    await deliver_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
        delivery_method="manual",
        send_to_client=False,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-requests/{request_id}/cancel", response_model=ForecastRequestResponse)
async def cancel_forecast_request_from_admin(
    request_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    await cancel_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-requests/{request_id}/remove-client", response_model=ForecastRequestResponse)
async def remove_forecast_request_client_from_admin(
    request_id: UUID,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже обрабатывается, удаление клиента недоступно",
        )
    if forecast_request.status != FORECAST_STATUS_REMOVED:
        previous_status = forecast_request.status
        revoke_result = await revoke_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            actor_id=current_admin.telegram_id,
            reason="Client removed from forecast request by admin",
        )
        forecast_request.status = FORECAST_STATUS_REMOVED
        forecast_request.handled_by = current_admin.telegram_id
        if revoke_result.had_access:
            if forecast_request.balance_before is None:
                forecast_request.balance_before = revoke_result.balance_before
            forecast_request.balance_after = revoke_result.balance_after
            forecast_request.no_balance_warning = False
        db.add(AdminAuditLog(
            actor_id=current_admin.telegram_id,
            target_user_id=forecast_request.user_id,
            action="forecast_request_client_removed",
            details={
                "request_id": str(forecast_request.id),
                "bet_id": str(forecast_request.bet_id),
                "had_access": revoke_result.had_access,
                "balance_delta": revoke_result.delta_matches,
                "previous_status": previous_status,
            },
        ))
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.get("/admin/announcements/audience-count")
async def get_audience_count(
    request: Request,
    sport_filter: Optional[str] = None,
    bookmaker_id: Optional[int] = None,
    min_coef: Optional[float] = None,
    current_admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/announcements/audience-count
    Returns the estimated audience size for announcement targeting preview.
    """
    selected_bookmaker_ids = _parse_bookmaker_id_values(
        [str(value) for value in request.query_params.getlist("bookmaker_ids")]
    )
    telegram_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="telegram",
        min_coef=min_coef,
    )
    vk_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="vk",
        min_coef=min_coef,
    )
    web_subscribers = await _get_smart_target_users(
        db,
        sport_filter=sport_filter,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="web",
        min_coef=min_coef,
    )
    total_audience = len({user.telegram_id for user in [*telegram_subscribers, *vk_subscribers, *web_subscribers]})
    web_push_audience = sum(
        1 for user in web_subscribers
        if isinstance(user.web_push_subscription, dict) and user.web_push_subscription.get("endpoint")
    )

    return {
        "total_audience": total_audience,
        "telegram_audience": len(telegram_subscribers),
        "vk_messages_audience": len(vk_subscribers),
        "web_chat_audience": len(web_subscribers),
        "web_push_audience": web_push_audience,
        "web_push_missing_permission": max(0, len(web_subscribers) - web_push_audience),
        "vk_app_notifications_requested": sum(
            1 for user in vk_subscribers if user.vk_notifications_allowed
        ),
        "vk_notifications_enabled_or_requested": sum(
            1 for user in vk_subscribers if user.vk_notifications_allowed
        ),
        "sport_filter": sport_filter,
        "bookmaker_id": bookmaker_id,
        "bookmaker_ids": _merge_bookmaker_ids(bookmaker_id, selected_bookmaker_ids),
        "min_coef_filter": min_coef
    }
