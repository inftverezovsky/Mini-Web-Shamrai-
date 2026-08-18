"""
Admin Broadcast & Smart Targeted Announcements API.
Handles announcement creation with coupon file uploads, 
target audience filtering, and push notification dispatch.
"""
import asyncio
import html
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, or_
from sqlalchemy.orm import selectinload
from uuid import UUID
from typing import Optional, List

from src.models.database import get_db, get_read_db
from src.models.models import AdminAuditLog, Bookmaker, FlatSubscription, ForecastRequest, User, Bet
from src.api.deps import get_current_admin_read, get_current_privileged_admin
from src.core.bookmaker_links import normalize_bookmaker_links, normalize_match_url
from src.core.config import settings
from src.core.message_templates import (
    TEMPLATE_ANNOUNCEMENT,
    TEMPLATE_FORECAST_TEASER,
    TEMPLATE_PAID_SET_TEASER,
    load_message_template_body,
    render_message_template,
)
from src.core.quiet_hours import current_notification_time, user_is_in_quiet_hours
from src.core.roles import STAFF_ROLES
from src.core.telegram_delivery import user_can_receive_personal_telegram
from src.core.telegram_text import append_contact_footer, decor_custom_emoji, sport_custom_emoji
from src.schemas.schemas import BetResponse, ForecastRequestResponse
from src.services.forecast_delivery import (
    FORECAST_STATUS_ANNOUNCED,
    FORECAST_STATUS_CANCELLED,
    FORECAST_STATUS_DECLINED,
    FORECAST_STATUS_INTERESTED,
    FORECAST_STATUS_PROCESSING,
    FORECAST_STATUS_REMOVED,
    DELIVERY_MODE_PAID_SET,
    DELIVERY_MODE_SALES_PRIVATE,
    PAID_SET_PLACEHOLDER_EVENT_NAME,
    PLACEHOLDER_EVENT_NAME,
    build_paid_set_teaser_message,
    build_forecast_teaser_payload,
    build_teaser_message,
    build_web_paid_set_signal_data,
    build_web_teaser_signal_data,
    bet_is_paid_set,
    cancel_forecast_request,
    count_client_bet_takers,
    deliver_forecast_request,
    enqueue_admin_group_forecast_stopped_notification,
    enqueue_forecast_auto_delivery,
    load_forecast_request,
    refreshed_client_delivery_method,
    request_is_paid_set,
)
from src.services.telegram_bot import call_telegram_api
from src.services.match_access import revoke_user_bet_access
from src.services.flat_subscriptions import refresh_flat_subscriptions_after_terminal_requests
from src.services.signals import broadcast_personal_signals
from src.services.system_settings import is_system_setting_enabled
from src.services.coupon_uploads import store_coupon_image
from src.services.admin_broadcast_helpers import (
    _decode_forecast_request_cursor,
    _encode_forecast_request_cursor,
    _merge_bookmaker_ids,
    _parse_bookmaker_id_values,
    _parse_optional_fair_coefficient,
    _web_push_report_values,
)
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
logger = logging.getLogger("uvicorn")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "coupons")


def _optional_text(value: Optional[str]) -> Optional[str]:
    clean_value = str(value or "").strip()
    return clean_value or None


def _event_name_or_placeholder(value: Optional[str], placeholder: str) -> str:
    return str(value or "").strip() or placeholder


async def _ensure_broadcasts_are_not_paused(db: AsyncSession) -> None:
    if await is_system_setting_enabled(db, "PAUSE_BROADCASTS"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Рассылки временно поставлены на паузу в настройках",
        )

class ForecastBulkSendResponse(BaseModel):
    status: str
    total: int
    sent: int
    queued: int = 0
    failed: int
    errors: List[str] = Field(default_factory=list)


class ForecastTeaserDeliveryResponse(BaseModel):
    total_audience: int
    sent: int
    failed: int
    errors: List[str] = Field(default_factory=list)
    delivery: dict = Field(default_factory=dict)


class ForecastBroadcastFullResponse(BaseModel):
    bet: BetResponse
    auto_send_enabled: bool = False
    auto_send: Optional[ForecastBulkSendResponse] = None
    reannounce: Optional[ForecastTeaserDeliveryResponse] = None


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


def _bookmaker_ids_from_form(form_data) -> Optional[List[int]]:
    values = [
        str(value)
        for value in form_data.getlist("bookmaker_ids")
        if value is not None
    ]
    values.extend(
        str(value)
        for value in form_data.getlist("bookmaker_ids[]")
        if value is not None
    )
    return _parse_bookmaker_id_values(values) if values else None


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
                logger.exception(
                    "admin_broadcast_telegram_send_crashed",
                    extra={
                        "event": "admin_broadcast_send_failed",
                        "channel": "telegram",
                        "user_id": user_id,
                        "log_prefix": log_prefix,
                        "error_type": type(exc).__name__,
                    },
                )
                return False, str(exc)
            if not call_result.get("ok"):
                description = call_result.get("description", "unknown error")
                logger.warning(
                    "admin_broadcast_telegram_send_failed",
                    extra={
                        "event": "admin_broadcast_send_failed",
                        "channel": "telegram",
                        "user_id": user_id,
                        "log_prefix": log_prefix,
                        "description": description,
                    },
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
                logger.exception(
                    "admin_broadcast_vk_send_crashed",
                    extra={
                        "event": "admin_broadcast_send_failed",
                        "channel": "vk",
                        "user_id": user_id,
                        "log_prefix": log_prefix,
                        "error_type": type(exc).__name__,
                    },
                )
                return False, str(exc), None
            if not call_result.get("ok"):
                permission_error = is_vk_message_permission_error(call_result)
                description = (
                    "VK не разрешает отправлять личные сообщения этому клиенту"
                    if permission_error
                    else call_result.get("description", "unknown error")
                )
                logger.warning(
                    "admin_broadcast_vk_send_failed",
                    extra={
                        "event": "admin_broadcast_send_failed",
                        "channel": "vk",
                        "user_id": user_id,
                        "log_prefix": log_prefix,
                        "description": description,
                    },
                )
                return False, str(description), user if permission_error else None
            return True, None, None

    results = await asyncio.gather(
        *(send_one(user_id, user, message, keyboard, image_path) for user_id, user, message, keyboard, image_path in jobs)
    )
    denied_users = [user for ok, _, user in results if not ok and user is not None]
    for user in denied_users:
        await mark_vk_messages_denied(db, user)
    if denied_users:
        await db.flush()

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
    web_push_retry_queued: int = 0,
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
            "retry_queued": web_push_retry_queued,
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
    commit: bool = False,
) -> List[User]:
    candidates = [user for user in users if getattr(user, "vk_user_id", None)]
    semaphore = asyncio.Semaphore(_vk_broadcast_concurrency())

    async def refresh_one(user: User) -> bool:
        async with semaphore:
            status_payload = await refresh_vk_delivery_status(db, user, refresh_group=refresh_group)
            return bool(status_payload.get("changed"))

    changed_flags = await asyncio.gather(*(refresh_one(user) for user in candidates)) if candidates else []
    changed = any(changed_flags)
    if changed and commit:
        await db.commit()
    elif changed:
        await db.flush()
    return [user for user in candidates if user_can_receive_vk_messages(user)]


async def _client_delivery_method(db: AsyncSession, user: User) -> str:
    return await refreshed_client_delivery_method(db, user)


def _normalized_target_value(value: Optional[object]) -> str:
    return str(value or "").strip().lower()


def _bookmaker_logo_path(bookmaker: Bookmaker) -> str:
    code = str(bookmaker.code or "other").strip().lower()
    if code == "other":
        return "/bookmakers/other.jpg"
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
    return await store_coupon_image(coupon_image, target_dir=STATIC_DIR)


async def _load_private_forecast_bet(
    db: AsyncSession,
    bet_id: UUID,
    *,
    allow_paid_set: bool = False,
    for_update: bool = False,
) -> Bet:
    query = (
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(
            selectinload(Bet.bookmaker),
            selectinload(Bet.bookmakers),
        )
    )
    if for_update:
        query = query.with_for_update().execution_options(populate_existing=True)
    result = await db.execute(query)
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Закрытый прогноз не найден",
        )
    allowed_delivery_modes = {DELIVERY_MODE_SALES_PRIVATE}
    if allow_paid_set:
        allowed_delivery_modes.add(DELIVERY_MODE_PAID_SET)
    if bet.delivery_mode not in allowed_delivery_modes:
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
    fair_coefficient: Optional[Decimal],
    fair_coefficient_provided: bool,
    teaser_text: Optional[str],
    sport_type: Optional[str],
    description: Optional[str],
    match_link: Optional[str],
    bookmaker_links: Optional[List[str]],
    category: Optional[str],
    live_ends_at: Optional[datetime],
    coupon_image: Optional[UploadFile],
) -> Bet:
    clean_event_name = _event_name_or_placeholder(
        event_name if event_name is not None else bet.event_name,
        PAID_SET_PLACEHOLDER_EVENT_NAME if bet_is_paid_set(bet) else PLACEHOLDER_EVENT_NAME,
    )
    clean_outcome = str(outcome if outcome is not None else bet.outcome or "").strip()
    normalized_match_link = normalize_match_url(match_link)
    if not clean_outcome:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите исход для полной ставки",
        )
    if match_link and not normalized_match_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректная ссылка на матч",
        )

    coupon_url = await _store_coupon_image(coupon_image)
    if coupon_url:
        bet.coupon_image_url = coupon_url

    bet.event_name = clean_event_name
    bet.outcome = clean_outcome
    bet.description = description.strip() if description else None
    bet.match_link = normalized_match_link or None
    if bookmaker_links is not None:
        bet.bookmaker_links = normalize_bookmaker_links(
            bookmaker_links,
            allowed_bookmaker_ids=bet.bookmaker_ids,
        )
    if coefficient is not None:
        bet.coefficient = coefficient
    if fair_coefficient_provided:
        bet.fair_coefficient = fair_coefficient
    if teaser_text is not None:
        bet.teaser_text = teaser_text.strip() or None
    if sport_type is not None:
        bet.sport_type = sport_type.strip() or None
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


async def _apply_forecast_bookmakers(
    db: AsyncSession,
    bet: Bet,
    bookmaker_ids: Optional[List[int]],
) -> List[int]:
    if bookmaker_ids is None:
        return bet.bookmaker_ids
    selected_bookmaker_ids = _merge_bookmaker_ids(None, bookmaker_ids)
    if not selected_bookmaker_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Выберите хотя бы одну БК для закрытого прогноза",
        )
    selected_bookmakers = await _load_bookmakers(db, selected_bookmaker_ids)
    bet.bookmaker_id = selected_bookmaker_ids[0]
    bet.bookmakers = selected_bookmakers
    return selected_bookmaker_ids


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

    only_active_subscribers = await is_system_setting_enabled(db, "BROADCAST_ONLY_ACTIVE_SUBSCRIBERS")
    if only_active_subscribers:
        active_flat_exists = select(FlatSubscription.id).where(
            FlatSubscription.user_id == User.telegram_id,
            FlatSubscription.status == "active",
        ).exists()
        filters.append(
            (User.purchased_bets_balance >= 1)
            | (User.matches_remaining >= 1)
            | (User.guarantee_active == True)
            | active_flat_exists
        )

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


async def _send_forecast_teasers(
    db: AsyncSession,
    *,
    forecast_requests: List[ForecastRequest],
    teaser_text: Optional[str],
    template_body: Optional[str] = None,
    log_prefix: str,
) -> dict:
    if not forecast_requests:
        delivery = _delivery_breakdown(
            telegram_total=0,
            telegram_sent=0,
            telegram_failed=0,
            telegram_errors=[],
            vk_total=0,
            vk_sent=0,
            vk_failed=0,
            vk_errors=[],
            vk_notifications_enabled=0,
            web_total=0,
            web_sent=0,
            **_web_push_report_values(None),
        )
        return {
            "delivery": delivery,
            "sent": delivery["sent"],
            "failed": delivery["failed"],
            "errors": delivery["errors"],
            "web_report": None,
            "vk_ready_users": {},
        }

    teaser_template_body = template_body or await load_message_template_body(db, TEMPLATE_FORECAST_TEASER)
    web_message_text = html_to_vk_text(
        build_teaser_message(forecast_requests[0], teaser_text, template_body=teaser_template_body)
    )
    web_report = await broadcast_personal_signals(
        db,
        users=[forecast_request.user for forecast_request in forecast_requests],
        text=web_message_text,
        signal_type="forecast_teaser",
        data_by_user_id={
            forecast_request.user_id: build_web_teaser_signal_data(
                forecast_request,
                teaser_text,
                template_body=teaser_template_body,
            )
            for forecast_request in forecast_requests
        },
        send_telegram=False,
        send_web_push=True,
        return_report=True,
        commit_before_external_delivery=True,
    )
    web_sent = int(web_report.get("created", 0)) if isinstance(web_report, dict) else 0
    telegram_jobs = [
        (
            forecast_request.user_id,
            "sendMessage",
            build_forecast_teaser_payload(forecast_request, teaser_text, template_body=teaser_template_body),
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
            html_to_vk_text(build_teaser_message(forecast_request, teaser_text, template_body=teaser_template_body)),
            build_vk_forecast_keyboard(forecast_request.id),
            None,
        )
        for forecast_request in forecast_requests
        if forecast_request.user_id in vk_ready_users
    ]
    telegram_sent, telegram_failed, telegram_errors = await _send_telegram_jobs(
        telegram_jobs,
        log_prefix=log_prefix,
    )
    vk_sent, vk_failed, vk_errors = await _send_vk_jobs(vk_jobs, db=db, log_prefix=log_prefix)
    delivery = _delivery_breakdown(
        telegram_total=len(telegram_jobs),
        telegram_sent=telegram_sent,
        telegram_failed=telegram_failed,
        telegram_errors=telegram_errors,
        vk_total=len(vk_jobs),
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
        "delivery": delivery,
        "sent": delivery["sent"],
        "failed": delivery["failed"],
        "errors": delivery["errors"],
        "web_report": web_report,
        "vk_ready_users": vk_ready_users,
    }


async def _create_missing_forecast_requests_for_bet(
    db: AsyncSession,
    *,
    bet: Bet,
    users: List[User],
) -> List[ForecastRequest]:
    existing_result = await db.execute(
        select(ForecastRequest.user_id).filter(ForecastRequest.bet_id == bet.id)
    )
    existing_user_ids = {int(user_id) for user_id in existing_result.scalars().all()}
    new_requests: List[ForecastRequest] = []
    for user in users:
        if user.telegram_id in existing_user_ids:
            continue
        forecast_request = ForecastRequest(
            bet=bet,
            user=user,
            status=FORECAST_STATUS_ANNOUNCED,
        )
        db.add(forecast_request)
        new_requests.append(forecast_request)
        existing_user_ids.add(user.telegram_id)
    await db.flush()
    return new_requests


@router.post("/admin/announcements")
async def create_announcement(
    request: Request,
    title: str = Form(..., min_length=1, max_length=160),
    body: Optional[str] = Form(None, max_length=4000),
    announcement_type: str = Form("general", max_length=40),  # "general" | "bet_promo" | "flash_sale" | "urgent"
    sport_filter: Optional[str] = Form(None, max_length=80),
    bookmaker_id: Optional[int] = Form(None),
    bookmaker_name: Optional[str] = Form(None, max_length=120),
    bookmaker_names: Optional[str] = Form(None, max_length=1000),
    min_coef: Optional[float] = Form(None, ge=1.0, le=999.99),
    match_link: Optional[str] = Form(None, max_length=2048),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_privileged_admin),
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
    
    coupon_url = await _store_coupon_image(coupon_image)
    
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
    fallback_emoji = type_emoji.get(announcement_type, "📢")
    emoji = decor_custom_emoji(announcement_type, fallback_emoji)
    
    body_text = (body or "").strip()
    if not body_text:
        fallback_parts = ["Есть новый анонс"]
        if min_coef:
            fallback_parts.append(f"с коэффициентом {min_coef}")
        if selected_bookmaker_names:
            fallback_parts.append(f"в {selected_bookmaker_names}")
        body_text = " ".join(fallback_parts) + "."

    normalized_match_link = normalize_match_url(match_link)
    context_lines = []
    if sport_filter:
        context_lines.append(
            f"{sport_custom_emoji(sport_filter)} Спорт: "
            f"<b>{html.escape(str(sport_filter), quote=True)}</b>"
        )
    if selected_bookmaker_names:
        context_lines.append(
            f"{decor_custom_emoji('bookmaker', '🏦')} БК: "
            f"<b>{html.escape(selected_bookmaker_names, quote=True)}</b>"
        )
    bookmaker_line = "\n".join(context_lines)
    match_link_line = (
        f"{decor_custom_emoji('link', '🔗')} "
        f"<a href=\"{html.escape(normalized_match_link, quote=True)}\">Перейти к матчу</a>"
        if normalized_match_link
        else ""
    )
    coefficient_line = (
        f"{decor_custom_emoji('coefficient', '📊')} Коэффициент: "
        f"<b>{html.escape(str(min_coef), quote=True)}</b>"
        if min_coef
        else ""
    )
    message_text = await render_message_template(
        db,
        TEMPLATE_ANNOUNCEMENT,
        {
            "emoji": emoji,
            "title": title,
            "body": body_text,
            "bookmaker_line": bookmaker_line,
            "match_link_line": match_link_line,
            "coefficient_line": coefficient_line,
            "contact_footer": append_contact_footer("").strip(),
        },
        safe_keys={"emoji", "bookmaker_line", "match_link_line", "coefficient_line", "contact_footer"},
    )

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
        commit_before_external_delivery=True,
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
    coefficient: Decimal = Form(..., ge=Decimal("1.0"), le=Decimal("999.99")),
    fair_coefficient: Optional[str] = Form(None, max_length=32),
    bookmaker_id: Optional[int] = Form(None),
    category: str = Form("prematch", max_length=40),
    live_ends_at: Optional[datetime] = Form(None),
    price_stars: Optional[int] = Form(None, ge=0, le=100000),
    brain_score: Optional[int] = Form(5, ge=0, le=100),
    api_match_id: Optional[str] = Form(None, max_length=160),
    sport_type: Optional[str] = Form(None, max_length=120),
    teaser_text: Optional[str] = Form(None, max_length=4000),
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a private forecast and sends only a teaser with Telegram buttons to matching users.
    The full forecast is delivered later by the sales manager.
    """
    await _ensure_broadcasts_are_not_paused(db)
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
    clean_teaser_text = (teaser_text or "").strip() or "Есть закрытый прогноз под вашу БК. Берете матч?"
    parsed_fair_coefficient = _parse_optional_fair_coefficient(fair_coefficient)
    clean_sport_type = _optional_text(sport_type)

    bet = Bet(
        event_name=PLACEHOLDER_EVENT_NAME,
        coefficient=coefficient,
        fair_coefficient=parsed_fair_coefficient,
        bookmaker_id=selected_bookmaker_ids[0],
        teaser_text=clean_teaser_text,
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=clean_sport_type,
        status="pending",
        delivery_mode="sales_private",
        author_id=current_admin.telegram_id,
    )
    bet.bookmakers = selected_bookmakers
    db.add(bet)
    await db.flush()

    target_users = await _get_smart_target_users(
        db,
        sport_filter=clean_sport_type,
        bookmaker_id=None,
        bookmaker_ids=selected_bookmaker_ids,
        delivery_channel="any",
        min_coef=float(coefficient),
    )

    forecast_requests = await _create_missing_forecast_requests_for_bet(
        db,
        bet=bet,
        users=target_users,
    )
    await db.commit()

    teaser_result = await _send_forecast_teasers(
        db,
        forecast_requests=forecast_requests,
        teaser_text=clean_teaser_text,
        log_prefix="ForecastBroadcast",
    )
    delivery = teaser_result["delivery"]

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


@router.post("/admin/paid-set-broadcast")
async def create_paid_set_broadcast(
    request: Request,
    title: str = Form(PAID_SET_PLACEHOLDER_EVENT_NAME, max_length=200),
    event_name: Optional[str] = Form(None, max_length=200),
    outcome: Optional[str] = Form(None, max_length=200),
    coefficient: Decimal = Form(..., ge=Decimal("1.0"), le=Decimal("999.99")),
    price_rub: int = Form(..., ge=1, le=1000000),
    bookmaker_id: Optional[int] = Form(None),
    sport_type: Optional[str] = Form(None, max_length=120),
    teaser_text: Optional[str] = Form(None, max_length=4000),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Creates a paid set teaser with a "take" button. The client cannot buy inside the app:
    the request goes to the sales manager for a personal dialogue.
    """
    await _ensure_broadcasts_are_not_paused(db)
    clean_title = (title or PAID_SET_PLACEHOLDER_EVENT_NAME).strip() or PAID_SET_PLACEHOLDER_EVENT_NAME
    clean_event_name = _event_name_or_placeholder(event_name, PAID_SET_PLACEHOLDER_EVENT_NAME)
    clean_outcome = (outcome or "").strip()
    clean_teaser_text = (teaser_text or "").strip() or "Реальный КФ не выше 1.9!"
    clean_sport_type = _optional_text(sport_type)
    if not clean_outcome:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите исход набора",
        )
    if price_rub <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Укажите стоимость набора в рублях",
        )

    form_data = await request.form()
    selected_bookmaker_ids = _merge_bookmaker_ids(
        bookmaker_id,
        _parse_bookmaker_id_values([str(value) for value in form_data.getlist("bookmaker_ids")]),
    )
    if not selected_bookmaker_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для набора выберите хотя бы одну БК",
        )

    selected_bookmakers = await _load_bookmakers(db, selected_bookmaker_ids)
    coupon_url = await _store_coupon_image(coupon_image)

    bet = Bet(
        event_name=clean_event_name,
        coefficient=coefficient,
        bookmaker_id=selected_bookmaker_ids[0],
        category="prematch",
        price_stars=price_rub,
        sport_type=clean_sport_type,
        outcome=clean_outcome,
        description=clean_teaser_text,
        status="pending",
        delivery_mode=DELIVERY_MODE_PAID_SET,
        author_id=current_admin.telegram_id,
        coupon_image_url=coupon_url,
    )
    bet.bookmakers = selected_bookmakers
    db.add(bet)
    await db.flush()

    target_users = await _get_smart_target_users(
        db,
        sport_filter=clean_sport_type,
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
    teaser_template_body = await load_message_template_body(db, TEMPLATE_PAID_SET_TEASER)
    await db.commit()

    forecast_requests = list(requests_by_user_id.values())
    first_message = (
        build_paid_set_teaser_message(
            forecast_requests[0],
            clean_teaser_text,
            title=clean_title,
            price_rub=price_rub,
            template_body=teaser_template_body,
        )
        if forecast_requests
        else ""
    )
    web_message_text = html_to_vk_text(first_message) if first_message else ""
    web_report = await broadcast_personal_signals(
        db,
        users=[forecast_request.user for forecast_request in forecast_requests],
        text=web_message_text,
        signal_type="forecast_teaser",
        data_by_user_id={
            forecast_request.user_id: build_web_paid_set_signal_data(
                forecast_request,
                clean_teaser_text,
                title=clean_title,
                price_rub=price_rub,
                template_body=teaser_template_body,
            )
            for forecast_request in forecast_requests
        },
        send_telegram=False,
        send_web_push=True,
        return_report=True,
        commit_before_external_delivery=True,
    ) if web_message_text else 0
    web_sent = int(web_report.get("created", 0)) if isinstance(web_report, dict) else 0
    telegram_jobs = []
    for forecast_request in forecast_requests:
        if not user_can_receive_personal_telegram(forecast_request.user):
            continue
        message_text = build_paid_set_teaser_message(
            forecast_request,
            clean_teaser_text,
            title=clean_title,
            price_rub=price_rub,
            template_body=teaser_template_body,
        )
        telegram_jobs.append((
            forecast_request.user_id,
            "sendMessage",
            build_forecast_teaser_payload(
                forecast_request,
                clean_teaser_text,
                template_body=teaser_template_body,
                message_text=message_text,
            ),
        ))

    vk_ready_users = {
        user.telegram_id: user
        for user in await _refresh_vk_audience(
            db,
            [forecast_request.user for forecast_request in forecast_requests],
        )
    }
    vk_jobs = []
    for forecast_request in forecast_requests:
        if forecast_request.user_id not in vk_ready_users:
            continue
        message_text = build_paid_set_teaser_message(
            forecast_request,
            clean_teaser_text,
            title=clean_title,
            price_rub=price_rub,
            template_body=teaser_template_body,
        )
        vk_jobs.append((
            forecast_request.user_id,
            forecast_request.user,
            html_to_vk_text(message_text),
            build_vk_forecast_keyboard(forecast_request.id),
            None,
        ))

    telegram_sent, telegram_failed, telegram_errors = await _send_telegram_jobs(
        telegram_jobs,
        log_prefix="PaidSetBroadcast",
    )
    vk_sent, vk_failed, vk_errors = await _send_vk_jobs(vk_jobs, db=db, log_prefix="PaidSetBroadcast")
    delivery = _delivery_breakdown(
        telegram_total=len(telegram_jobs),
        telegram_sent=telegram_sent,
        telegram_failed=telegram_failed,
        telegram_errors=telegram_errors,
        vk_total=len(vk_jobs),
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
        "delivery_mode": DELIVERY_MODE_PAID_SET,
        "bookmaker_ids": selected_bookmaker_ids,
        "price_rub": price_rub,
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
    event_name: Optional[str] = Form(None, max_length=200),
    outcome: Optional[str] = Form(None, max_length=200),
    coefficient: Optional[Decimal] = Form(None, ge=Decimal("1.0"), le=Decimal("999.99")),
    fair_coefficient: Optional[str] = Form(None, max_length=32),
    sport_type: Optional[str] = Form(None, max_length=120),
    teaser_text: Optional[str] = Form(None, max_length=4000),
    description: Optional[str] = Form(None, max_length=4000),
    match_link: Optional[str] = Form(None, max_length=2048),
    category: Optional[str] = Form(None, max_length=40),
    live_ends_at: Optional[datetime] = Form(None),
    auto_send_interested: bool = Form(False),
    reannounce_new_audience: bool = Form(False),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    Saves the full private forecast after the teaser has been broadcast.
    If enabled, automatically sends it to interested clients and keeps future "take" clicks automatic.
    """
    bet = await _load_private_forecast_bet(db, bet_id, allow_paid_set=True, for_update=True)
    is_paid_set_bet = bet_is_paid_set(bet)
    form_data = await request.form()
    fair_coefficient_provided = "fair_coefficient" in form_data or fair_coefficient is not None
    parsed_fair_coefficient = (
        _parse_optional_fair_coefficient(fair_coefficient)
        if fair_coefficient_provided
        else None
    )
    previous_bookmaker_ids = bet.bookmaker_ids
    selected_bookmaker_ids = await _apply_forecast_bookmakers(
        db,
        bet,
        _bookmaker_ids_from_form(form_data),
    )
    previous_bookmaker_id_set = set(previous_bookmaker_ids)
    added_bookmaker_ids = [
        bookmaker_id
        for bookmaker_id in selected_bookmaker_ids
        if bookmaker_id not in previous_bookmaker_id_set
    ]
    await _apply_full_forecast_fields(
        bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        fair_coefficient=parsed_fair_coefficient,
        fair_coefficient_provided=fair_coefficient_provided,
        teaser_text=teaser_text,
        sport_type=sport_type,
        description=description,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form_data),
        category=category,
        live_ends_at=live_ends_at,
        coupon_image=coupon_image,
    )
    bet.author_id = bet.author_id or current_admin.telegram_id
    if "auto_send_interested" in form_data:
        bet.auto_send_on_interest = bool(auto_send_interested)
    await db.commit()

    auto_send_result = None
    reannounce_result = None
    if reannounce_new_audience and not is_paid_set_bet:
        if added_bookmaker_ids:
            target_users = await _get_smart_target_users(
                db,
                sport_filter=bet.sport_type,
                bookmaker_id=None,
                bookmaker_ids=added_bookmaker_ids,
                delivery_channel="any",
                min_coef=float(bet.coefficient),
            )
            new_requests = await _create_missing_forecast_requests_for_bet(
                db,
                bet=bet,
                users=target_users,
            )
            await db.commit()
        else:
            new_requests = []
        teaser_delivery = await _send_forecast_teasers(
            db,
            forecast_requests=new_requests,
            teaser_text=bet.teaser_text,
            log_prefix="ForecastReannounce",
        )
        reannounce_delivery = teaser_delivery["delivery"]
        reannounce_result = ForecastTeaserDeliveryResponse(
            total_audience=len(new_requests),
            sent=reannounce_delivery["sent"],
            failed=reannounce_delivery["failed"],
            errors=reannounce_delivery["errors"],
            delivery={
                "total_audience": len(new_requests),
                **reannounce_delivery,
            },
        )

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
        queued_count = 0
        failed_count = 0
        errors: List[str] = []

        for forecast_request in interested_requests:
            try:
                await enqueue_forecast_auto_delivery(
                    db,
                    forecast_request,
                    delivery_method=await _client_delivery_method(db, forecast_request.user),
                )
                queued_count += 1
            except HTTPException as exc:
                failed_count += 1
                detail = str(exc.detail or "Не удалось поставить прогноз в очередь")
                errors.append(f"ID {forecast_request.user_id}: {detail}")
            except Exception as exc:
                failed_count += 1
                errors.append(f"ID {forecast_request.user_id}: {exc}")

        auto_send_result = ForecastBulkSendResponse(
            status="success" if failed_count == 0 else "partial" if queued_count else "failed",
            total=len(interested_requests),
            sent=0,
            queued=queued_count,
            failed=failed_count,
            errors=errors[:10],
        )

    refreshed_bet = await _load_private_forecast_bet(db, bet_id, allow_paid_set=True)
    return ForecastBroadcastFullResponse(
        bet=refreshed_bet,
        auto_send_enabled=bool(refreshed_bet.auto_send_on_interest),
        auto_send=auto_send_result,
        reannounce=reannounce_result,
    )


@router.get("/admin/forecast-requests", response_model=List[ForecastRequestResponse])
async def list_forecast_requests(
    request_status: Optional[str] = Query(None, alias="status"),
    bet_id: Optional[UUID] = Query(None),
    current_admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    query = (
        select(ForecastRequest)
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.user).selectinload(User.flat_subscriptions),
            selectinload(ForecastRequest.flat_subscription),
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


@router.get("/admin/forecast-requests-page")
async def list_forecast_requests_page(
    request_status: Optional[str] = Query(None, alias="status"),
    bet_id: Optional[UUID] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    current_admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    cursor_value = _decode_forecast_request_cursor(cursor)
    filters = []
    if request_status:
        filters.append(ForecastRequest.status == request_status)
    if bet_id:
        filters.append(ForecastRequest.bet_id == bet_id)
    count_filters = list(filters)
    if cursor_value:
        cursor_created_at, cursor_id = cursor_value
        filters.append(or_(
            ForecastRequest.created_at < cursor_created_at,
            (ForecastRequest.created_at == cursor_created_at) & (ForecastRequest.id < cursor_id),
        ))

    filtered_total_result = await db.execute(select(func.count(ForecastRequest.id)).filter(*count_filters))
    result = await db.execute(
        select(ForecastRequest)
        .filter(*filters)
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.user).selectinload(User.flat_subscriptions),
            selectinload(ForecastRequest.flat_subscription),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
        .order_by(ForecastRequest.created_at.desc(), ForecastRequest.id.desc())
        .limit(limit + 1)
    )
    fetched_requests = result.scalars().all()
    page_requests = fetched_requests[:limit]
    has_more = len(fetched_requests) > limit
    return {
        "items": [
            ForecastRequestResponse.model_validate(forecast_request).model_dump(mode="json")
            for forecast_request in page_requests
        ],
        "next_cursor": _encode_forecast_request_cursor(page_requests[-1]) if has_more and page_requests else None,
        "has_more": has_more,
        "filtered_total": int(filtered_total_result.scalar() or 0),
    }


@router.post("/admin/forecast-requests/bulk-send", response_model=ForecastBulkSendResponse)
async def send_selected_forecast_requests_from_admin(
    request: Request,
    request_ids: List[UUID] = Form(...),
    event_name: Optional[str] = Form(None, max_length=200),
    outcome: Optional[str] = Form(None, max_length=200),
    coefficient: Optional[Decimal] = Form(None, ge=Decimal("1.0"), le=Decimal("999.99")),
    sport_type: Optional[str] = Form(None, max_length=120),
    description: Optional[str] = Form(None, max_length=4000),
    match_link: Optional[str] = Form(None, max_length=2048),
    category: Optional[str] = Form(None, max_length=40),
    live_ends_at: Optional[datetime] = Form(None),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_privileged_admin),
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
    if any(request_is_paid_set(forecast_request) for forecast_request in forecast_requests):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для набора доступна только ручная продажа в диалоге",
        )

    form_data = await request.form()
    await _apply_full_forecast_fields(
        forecast_requests[0].bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        fair_coefficient=None,
        fair_coefficient_provided=False,
        teaser_text=None,
        sport_type=sport_type,
        description=description,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form_data),
        category=category,
        live_ends_at=live_ends_at,
        coupon_image=coupon_image,
    )
    await db.commit()

    queued_count = 0
    failed_count = missing_count
    errors: List[str] = []
    if missing_count:
        errors.append(f"Не найдено заявок: {missing_count}")

    for forecast_request in forecast_requests:
        try:
            await enqueue_forecast_auto_delivery(
                db,
                forecast_request,
                delivery_method=await _client_delivery_method(db, forecast_request.user),
            )
            queued_count += 1
        except HTTPException as exc:
            failed_count += 1
            detail = str(exc.detail or "Не удалось поставить прогноз в очередь")
            errors.append(f"ID {forecast_request.user_id}: {detail}")
        except Exception as exc:
            failed_count += 1
            errors.append(f"ID {forecast_request.user_id}: {exc}")

    return ForecastBulkSendResponse(
        status="success" if queued_count and failed_count == 0 else "partial" if queued_count else "failed",
        total=len(unique_request_ids),
        sent=0,
        queued=queued_count,
        failed=failed_count,
        errors=errors[:10],
    )


@router.post("/admin/forecast-requests/{request_id}/send", response_model=ForecastRequestResponse)
async def send_forecast_request_from_admin(
    request_id: UUID,
    request: Request,
    event_name: Optional[str] = Form(None, max_length=200),
    outcome: Optional[str] = Form(None, max_length=200),
    coefficient: Optional[Decimal] = Form(None, ge=Decimal("1.0"), le=Decimal("999.99")),
    sport_type: Optional[str] = Form(None, max_length=120),
    description: Optional[str] = Form(None, max_length=4000),
    match_link: Optional[str] = Form(None, max_length=2048),
    category: Optional[str] = Form(None, max_length=40),
    live_ends_at: Optional[datetime] = Form(None),
    coupon_image: Optional[UploadFile] = File(None),
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    if request_is_paid_set(forecast_request):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для набора доступна только ручная продажа в диалоге",
        )
    form_data = await request.form()
    await _apply_full_forecast_fields(
        forecast_request.bet,
        event_name=event_name,
        outcome=outcome,
        coefficient=coefficient,
        fair_coefficient=None,
        fair_coefficient_provided=False,
        teaser_text=None,
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
        commit=True,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-requests/{request_id}/send-saved", response_model=ForecastRequestResponse)
async def send_saved_forecast_request_from_admin(
    request_id: UUID,
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    if request_is_paid_set(forecast_request):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Для набора доступна только ручная продажа в диалоге",
        )
    await deliver_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
        delivery_method=await _client_delivery_method(db, forecast_request.user),
        send_to_client=True,
        commit=True,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-broadcast/{bet_id}/stop")
async def stop_forecast_broadcast_from_admin(
    bet_id: UUID,
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    bet = await _load_private_forecast_bet(db, bet_id, allow_paid_set=True, for_update=True)
    taker_count = await count_client_bet_takers(db, bet.id)

    stoppable_statuses = {
        FORECAST_STATUS_ANNOUNCED,
        FORECAST_STATUS_INTERESTED,
        FORECAST_STATUS_DECLINED,
        FORECAST_STATUS_CANCELLED,
    }
    requests_result = await db.execute(
        select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id)
    )
    forecast_requests = requests_result.scalars().all()
    has_stoppable_requests = any(
        forecast_request.status in stoppable_statuses
        for forecast_request in forecast_requests
    )
    already_stopped = (
        (bet.status == "deleted" and bet.resolved_at is not None)
        or (not bet.auto_send_on_interest and not has_stoppable_requests)
    )
    if bet.status == "deleted" and bet.resolved_at is None:
        bet.status = "pending"
    if bet.status != "deleted":
        bet.auto_send_on_interest = False

    stopped_requests = 0
    skipped_processing = 0
    affected_flat_subscription_ids: set[UUID] = set()
    for forecast_request in forecast_requests:
        if forecast_request.status == FORECAST_STATUS_PROCESSING:
            skipped_processing += 1
            continue
        if forecast_request.status in stoppable_statuses:
            if forecast_request.flat_subscription_id is not None:
                affected_flat_subscription_ids.add(forecast_request.flat_subscription_id)
            forecast_request.status = FORECAST_STATUS_REMOVED
            forecast_request.handled_by = current_admin.telegram_id
            stopped_requests += 1

    await refresh_flat_subscriptions_after_terminal_requests(
        db,
        affected_flat_subscription_ids,
    )

    await enqueue_admin_group_forecast_stopped_notification(
        db,
        bet=bet,
        taker_count=taker_count,
    )
    db.add(AdminAuditLog(
        actor_id=current_admin.telegram_id,
        action="forecast_broadcast_stopped",
        details={
            "bet_id": str(bet.id),
            "event_name": bet.event_name,
            "already_stopped": already_stopped,
            "stopped_requests": stopped_requests,
            "skipped_processing": skipped_processing,
            "taker_count": taker_count,
        },
    ))
    await db.commit()

    return {
        "status": "success",
        "bet_id": str(bet.id),
        "already_stopped": already_stopped,
        "stopped_requests": stopped_requests,
        "skipped_processing": skipped_processing,
        "taker_count": taker_count,
    }


@router.post("/admin/forecast-broadcast/{bet_id}/resume")
async def resume_forecast_broadcast_from_admin(
    bet_id: UUID,
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    await _ensure_broadcasts_are_not_paused(db)
    bet = await _load_private_forecast_bet(db, bet_id, allow_paid_set=True, for_update=True)

    if bet.status in ("win", "loss", "refund"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Нельзя возобновить рассылку для завершённого прогноза",
        )
    if bet.resolved_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Прогноз уже имеет результат, возобновление невозможно",
        )

    requests_result = await db.execute(
        select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id)
    )
    forecast_requests = requests_result.scalars().all()

    reactivated_count = 0
    for forecast_request in forecast_requests:
        if forecast_request.status == FORECAST_STATUS_REMOVED:
            forecast_request.status = FORECAST_STATUS_ANNOUNCED
            reactivated_count += 1

    if bet.status == "deleted":
        bet.status = "pending"
    bet.auto_send_on_interest = True

    bookmaker_ids = [bm.id for bm in bet.bookmakers] if bet.bookmakers else []
    if bet.bookmaker_id and bet.bookmaker_id not in bookmaker_ids:
        bookmaker_ids.append(bet.bookmaker_id)

    target_users = await _get_smart_target_users(
        db,
        sport_filter=bet.sport_type,
        bookmaker_id=None,
        bookmaker_ids=bookmaker_ids,
        delivery_channel="any",
        min_coef=float(bet.coefficient),
    )

    new_requests = await _create_missing_forecast_requests_for_bet(
        db,
        bet=bet,
        users=target_users,
    )

    teaser_result = {"sent": 0, "failed": 0, "errors": []}
    if new_requests:
        t_res = await _send_forecast_teasers(
            db,
            forecast_requests=new_requests,
            teaser_text=bet.teaser_text or "Есть закрытый прогноз под вашу БК. Берете матч?",
            log_prefix="ForecastBroadcastResume",
        )
        teaser_result = t_res["delivery"]

    db.add(AdminAuditLog(
        actor_id=current_admin.telegram_id,
        action="forecast_broadcast_resumed",
        details={
            "bet_id": str(bet.id),
            "event_name": bet.event_name,
            "reactivated_count": reactivated_count,
            "new_requests_count": len(new_requests),
            "sent": teaser_result.get("sent", 0),
        },
    ))
    await db.commit()

    return {
        "status": "success",
        "reactivated_requests": reactivated_count,
        "new_requests": len(new_requests),
        "delivery": teaser_result,
    }


@router.post("/admin/forecast-requests/{request_id}/mark-manual", response_model=ForecastRequestResponse)
async def mark_forecast_request_manual(
    request_id: UUID,
    current_admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    forecast_request = await load_forecast_request(db, request_id)
    await deliver_forecast_request(
        db,
        forecast_request=forecast_request,
        handled_by=current_admin.telegram_id,
        delivery_method="manual",
        send_to_client=False,
        commit=True,
    )
    await db.commit()
    return await load_forecast_request(db, request_id)


@router.post("/admin/forecast-requests/{request_id}/cancel", response_model=ForecastRequestResponse)
async def cancel_forecast_request_from_admin(
    request_id: UUID,
    current_admin: User = Depends(get_current_privileged_admin),
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
    current_admin: User = Depends(get_current_privileged_admin),
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
        affected_flat_subscription_id = forecast_request.flat_subscription_id
        revoke_result = await revoke_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            actor_id=current_admin.telegram_id,
            reason="Client removed from forecast request by admin",
        )
        forecast_request.status = FORECAST_STATUS_REMOVED
        forecast_request.handled_by = current_admin.telegram_id
        await refresh_flat_subscriptions_after_terminal_requests(
            db,
            [affected_flat_subscription_id],
        )
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
    current_admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
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
