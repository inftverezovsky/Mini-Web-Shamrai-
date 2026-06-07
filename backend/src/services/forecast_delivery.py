import asyncio
import html
import logging
import mimetypes
import os
import re
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.api.payments import call_telegram_api, call_telegram_api_multipart
from src.core.config import settings
from src.core.telegram_text import append_contact_footer, bookmaker_custom_emoji, sport_custom_emoji
from src.models.database import AsyncSessionLocal
from src.models.models import Bet, ForecastRequest, User
from src.services.match_access import UserBetAccessResult, record_user_bet_access

logger = logging.getLogger("uvicorn")

FORECAST_STATUS_ANNOUNCED = "announced"
FORECAST_STATUS_INTERESTED = "interested"
FORECAST_STATUS_DECLINED = "declined"
FORECAST_STATUS_PROCESSING = "processing"
FORECAST_STATUS_SENT = "sent"
FORECAST_STATUS_MANUAL_SENT = "manual_sent"
FORECAST_STATUS_CANCELLED = "cancelled"

DELIVERED_STATUSES = {FORECAST_STATUS_SENT, FORECAST_STATUS_MANUAL_SENT}
PLACEHOLDER_EVENT_NAME = "Закрытый прогноз"
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
STATIC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _html(value: Optional[object]) -> str:
    return html.escape(str(value or "").strip(), quote=True)


def _coefficient_text(bet: Bet) -> str:
    try:
        return f"{float(bet.coefficient):.2f}"
    except Exception:
        return str(bet.coefficient)


def _bookmakers_for_bet(bet: Bet) -> list:
    bookmakers = list(bet.bookmakers or [])
    if not bookmakers and bet.bookmaker:
        bookmakers = [bet.bookmaker]
    return bookmakers


def bookmaker_names_for_bet(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    return ", ".join(bookmaker.name for bookmaker in bookmakers) or "не указана"


def _bookmaker_label(bookmaker) -> str:
    emoji = bookmaker_custom_emoji(getattr(bookmaker, "code", None))
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}<b>{_html(getattr(bookmaker, 'name', None))}</b>"


def _bookmaker_labels_for_bet(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    if not bookmakers:
        return "<b>не указана</b>"
    return ", ".join(_bookmaker_label(bookmaker) for bookmaker in bookmakers)


def _sport_label(sport_type: Optional[str]) -> str:
    clean_sport = str(sport_type or "").strip()
    if not clean_sport:
        return ""
    emoji = sport_custom_emoji(clean_sport)
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}<b>{_html(clean_sport)}</b>"


def _normalize_match_url(raw_url: Optional[object]) -> str:
    url = str(raw_url or "").strip()
    if not url:
        return ""
    if not url.lower().startswith(("http://", "https://")):
        url = re.sub(r"^[a-z][a-z0-9+.-]*://", "", url, flags=re.IGNORECASE)
        url = f"https://{url}"
    return url


def _bookmaker_button_name(bookmaker: Optional[object]) -> str:
    if not bookmaker:
        return "Ссылка"
    name = str(getattr(bookmaker, "name", None) or getattr(bookmaker, "code", None) or "").strip()
    if not name:
        return "Ссылка"
    text = f"Ссылка {name}"
    return f"{text[:61]}..." if len(text) > 64 else text


def _bookmaker_link_targets(bet: Bet) -> list[dict[str, object]]:
    links_by_bookmaker_id: dict[int, str] = {}
    link_items: list[tuple[int, str]] = []
    for item in bet.bookmaker_links or []:
        if not isinstance(item, dict):
            continue
        try:
            bookmaker_id = int(item.get("bookmaker_id"))
        except (TypeError, ValueError):
            continue
        url = _normalize_match_url(item.get("url"))
        if url:
            links_by_bookmaker_id[bookmaker_id] = url
            link_items.append((bookmaker_id, url))

    targets: list[dict[str, object]] = []
    used_bookmaker_ids: set[int] = set()
    used_urls: set[str] = set()
    bookmakers = _bookmakers_for_bet(bet)
    for bookmaker in bookmakers:
        url = links_by_bookmaker_id.get(bookmaker.id)
        if not url and len(bookmakers) == 1 and len(link_items) == 1:
            url = link_items[0][1]
        if not url:
            continue
        targets.append({"bookmaker": bookmaker, "url": url})
        used_bookmaker_ids.add(bookmaker.id)
        used_urls.add(url)

    for bookmaker_id, url in link_items:
        if bookmaker_id not in used_bookmaker_ids and url not in used_urls:
            targets.append({"bookmaker": None, "url": url})
            used_urls.add(url)
    return targets


def _bookmaker_link_lines(bet: Bet) -> list[str]:
    lines: list[str] = []
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        prefix = bookmaker_custom_emoji(getattr(bookmaker, "code", None)) if bookmaker else ""
        lines.append(f"{prefix or '🔗'} <a href=\"{_html(url)}\">Ссылка</a>")
    return lines


def _bookmaker_links_message(bet: Bet) -> Optional[str]:
    lines = ["<b>Ссылки на матч</b>"]
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        prefix = bookmaker_custom_emoji(getattr(bookmaker, "code", None)) if bookmaker else ""
        lines.append(f"{prefix or '🔗'} <a href=\"{_html(url)}\">Ссылка</a>\n{_html(url)}")
    if len(lines) == 1:
        return None
    return _join_forecast_lines(lines)


def _bookmaker_link_reply_markup(bet: Bet) -> Optional[dict]:
    buttons = []
    for target in _bookmaker_link_targets(bet):
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        buttons.append([
            {
                "text": _bookmaker_button_name(target.get("bookmaker")),
                "url": url,
            }
        ])
    if not buttons:
        return None
    return {"inline_keyboard": buttons}


def _join_forecast_lines(lines: list[str]) -> str:
    return "\n\n".join(line for line in lines if line)


def _plain_text_length(html_text: str) -> int:
    custom_emoji_text = re.sub(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", r"\1", html_text, flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", "", custom_emoji_text)
    return len(html.unescape(without_tags))


def _fits_photo_caption(html_text: str) -> bool:
    return _plain_text_length(html_text) <= TELEGRAM_PHOTO_CAPTION_LIMIT


def _send_bookmaker_links_message(chat_id: int, bet: Bet) -> Optional[dict]:
    links_message = _bookmaker_links_message(bet)
    if not links_message:
        return None
    reply_markup = _bookmaker_link_reply_markup(bet)
    return call_telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": links_message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
        **({"reply_markup": reply_markup} if reply_markup else {}),
    })


def _public_asset_url(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    if path.startswith(("http://", "https://")):
        return path
    base_url = settings.API_BASE_URL.strip().rstrip("/")
    return f"{base_url}{path}" if base_url else path


def _local_static_asset_path(path: Optional[str]) -> Optional[str]:
    if not path or path.startswith(("http://", "https://")):
        return None

    clean_path = path.split("?", 1)[0].replace("\\", "/").lstrip("/")
    if clean_path.startswith("static/"):
        relative_path = clean_path[len("static/"):]
    else:
        return None

    absolute_path = os.path.abspath(os.path.join(STATIC_ROOT, relative_path))
    if os.path.commonpath([STATIC_ROOT, absolute_path]) != STATIC_ROOT:
        return None
    if not os.path.isfile(absolute_path):
        return None
    return absolute_path


def _client_display(user: User) -> str:
    name_parts = [part for part in (user.first_name, user.last_name) if part]
    display_name = " ".join(name_parts).strip()
    if user.username:
        return f"{display_name} (@{user.username})" if display_name else f"@{user.username}"
    return display_name or f"ID {user.telegram_id}"


def _already_taken_access_result(forecast_request: ForecastRequest) -> UserBetAccessResult:
    current_balance = int(forecast_request.user.purchased_bets_balance or forecast_request.user.matches_remaining or 0)
    return UserBetAccessResult(
        status="already_taken",
        already_recorded=True,
        access_type="paid_match",
        match_charged=False,
        balance_before=forecast_request.balance_before if forecast_request.balance_before is not None else current_balance,
        balance_after=forecast_request.balance_after if forecast_request.balance_after is not None else current_balance,
        no_balance_warning=bool(forecast_request.no_balance_warning),
    )


async def load_forecast_request(db: AsyncSession, request_id: UUID) -> ForecastRequest:
    result = await db.execute(
        select(ForecastRequest)
        .filter(ForecastRequest.id == request_id)
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
    )
    forecast_request = result.scalars().first()
    if not forecast_request:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заявка на прогноз не найдена",
        )
    return forecast_request


def build_teaser_message(forecast_request: ForecastRequest, teaser_text: Optional[str]) -> str:
    bet = forecast_request.bet
    teaser = (teaser_text or "").strip() or "Есть закрытый прогноз под вашу БК. Нажмите, если хотите взять матч."
    lines = [
        "<b>Закрытый анонс прогноза</b>",
        f"БК: {_bookmaker_labels_for_bet(bet)}",
        f"Спорт: {_sport_label(bet.sport_type) or '<b>не указан</b>'}",
        f"Коэффициент: <b>{_html(_coefficient_text(bet))}</b>",
        _html(teaser),
    ]
    return append_contact_footer(_join_forecast_lines(lines))


def build_forecast_teaser_payload(forecast_request: ForecastRequest, teaser_text: Optional[str]) -> dict:
    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "Взять",
                    "callback_data": f"forecast:take:{forecast_request.id}",
                },
                {
                    "text": "Не взять",
                    "callback_data": f"forecast:decline:{forecast_request.id}",
                },
            ]
        ]
    }
    return {
        "chat_id": forecast_request.user_id,
        "text": build_teaser_message(forecast_request, teaser_text),
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    }


def send_forecast_teaser(forecast_request: ForecastRequest, teaser_text: Optional[str]) -> dict:
    return call_telegram_api("sendMessage", build_forecast_teaser_payload(forecast_request, teaser_text))


def _build_full_forecast_message(forecast_request: ForecastRequest) -> str:
    bet = forecast_request.bet
    lines = [
        f"Матч: <b>{_html(bet.event_name)}</b>",
        f"Исход: <b>{_html(bet.outcome or 'уточняется')}</b>",
        f"Коэффициент: <b>{_html(_coefficient_text(bet))}</b>",
    ]
    if bet.description:
        lines.append(_html(bet.description))

    return append_contact_footer(_join_forecast_lines(lines))


def send_full_forecast_to_client(forecast_request: ForecastRequest) -> dict:
    bet = forecast_request.bet
    full_message = _build_full_forecast_message(forecast_request)
    use_full_caption = _fits_photo_caption(full_message)
    coupon_caption = full_message if use_full_caption else f"Купон к прогнозу: {bet.event_name}"
    coupon_file_path = _local_static_asset_path(bet.coupon_image_url)
    coupon_url = None if coupon_file_path else _public_asset_url(bet.coupon_image_url)

    if coupon_file_path:
        filename = os.path.basename(coupon_file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(coupon_file_path, "rb") as file_obj:
            payload = {
                "chat_id": forecast_request.user_id,
                "caption": coupon_caption,
            }
            if use_full_caption:
                payload["parse_mode"] = "HTML"
            photo_result = call_telegram_api_multipart(
                "sendPhoto",
                payload,
                {
                    "photo": (filename, file_obj.read(), content_type),
                },
            )
        if not photo_result.get("ok"):
            return photo_result
    elif coupon_url:
        payload = {
            "chat_id": forecast_request.user_id,
            "photo": coupon_url,
            "caption": coupon_caption,
        }
        if use_full_caption:
            payload["parse_mode"] = "HTML"
        photo_result = call_telegram_api("sendPhoto", payload)
        if not photo_result.get("ok"):
            return photo_result

    if coupon_file_path or coupon_url:
        if use_full_caption:
            links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
            if links_result and not links_result.get("ok"):
                return links_result
            return photo_result
        text_result = call_telegram_api("sendMessage", {
            "chat_id": forecast_request.user_id,
            "text": full_message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        })
        if not text_result.get("ok"):
            return text_result
        links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
        if links_result and not links_result.get("ok"):
            return links_result
        return text_result

    text_result = call_telegram_api("sendMessage", {
        "chat_id": forecast_request.user_id,
        "text": full_message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
    })
    if not text_result.get("ok"):
        return text_result
    links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
    if links_result and not links_result.get("ok"):
        return links_result
    return text_result


def _ensure_full_forecast_ready(forecast_request: ForecastRequest) -> None:
    bet = forecast_request.bet
    event_name = str(bet.event_name or "").strip()
    outcome = str(bet.outcome or "").strip()
    if not event_name or event_name == PLACEHOLDER_EVENT_NAME:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Заполните матч перед отправкой прогноза",
        )
    if not outcome:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Заполните исход перед отправкой прогноза",
        )
    if not bet.coupon_image_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Загрузите скрин купона перед отправкой прогноза",
        )


def notify_sales_manager(forecast_request: ForecastRequest) -> dict:
    sales_manager_id = settings.sales_manager_telegram_id
    if not sales_manager_id:
        return {"ok": False, "description": "SALES_MANAGER_TELEGRAM_ID or OWNER_TELEGRAM_ID is not configured"}

    user = forecast_request.user
    bet = forecast_request.bet
    balance = int(user.purchased_bets_balance or user.matches_remaining or 0)
    no_balance_warning = balance <= 0 and not bool(user.guarantee_active)
    warning = ""
    if no_balance_warning:
        warning = "\n\n⚠️ У клиента 0 матчей. Отправка разрешена, баланс уйдет в минус."

    message = (
        "<b>Клиент хочет взять закрытый прогноз</b>\n\n"
        f"Клиент: <b>{_html(_client_display(user))}</b>\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n"
        f"Баланс матчей: <b>{balance}</b>\n\n"
        f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
        f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
        f"Спорт: <b>{_html(bet.sport_type or 'не указан')}</b>"
        f"{warning}"
    )

    reply_markup = {
        "inline_keyboard": [
            [
                {
                    "text": "Отправить прогноз",
                    "callback_data": f"forecast:sales_send:{forecast_request.id}",
                }
            ],
            [
                {
                    "text": "Клиент взял вручную",
                    "callback_data": f"forecast:sales_manual:{forecast_request.id}",
                },
                {
                    "text": "Отменить",
                    "callback_data": f"forecast:sales_cancel:{forecast_request.id}",
                },
            ],
        ]
    }
    return call_telegram_api("sendMessage", {
        "chat_id": sales_manager_id,
        "text": message,
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    })


async def notify_sales_manager_for_request(request_id: UUID) -> dict:
    async with AsyncSessionLocal() as db:
        forecast_request = await load_forecast_request(db, request_id)
        notify_result = await asyncio.to_thread(notify_sales_manager, forecast_request)
        if not notify_result.get("ok"):
            logger.warning(
                "[ForecastDelivery] Sales manager notification failed for %s: %s",
                request_id,
                notify_result.get("description", "unknown error"),
            )
        return notify_result


async def set_forecast_request_interested(
    db: AsyncSession,
    *,
    request_id: UUID,
    actor_user_id: int,
    notify_sales_manager_now: bool = True,
) -> tuple[ForecastRequest, str, bool]:
    forecast_request = await load_forecast_request(db, request_id)
    if forecast_request.user_id != actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта кнопка привязана к другому клиенту",
        )

    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Прогноз уже оформлен.", False
    if forecast_request.status == FORECAST_STATUS_INTERESTED:
        return forecast_request, "Заявка уже отправлена продажнику.", False
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        return forecast_request, "Заявка уже обрабатывается.", False
    if forecast_request.status == FORECAST_STATUS_DECLINED:
        return forecast_request, "Отказ уже учтен.", False
    if forecast_request.status == FORECAST_STATUS_CANCELLED:
        return forecast_request, "Заявка отменена.", False

    responded_at = forecast_request.responded_at or _now()
    lock_result = await db.execute(
        update(ForecastRequest)
        .where(
            ForecastRequest.id == request_id,
            ForecastRequest.status == FORECAST_STATUS_ANNOUNCED,
        )
        .values(
            status=FORECAST_STATUS_INTERESTED,
            responded_at=responded_at,
        )
    )
    if lock_result.rowcount != 1:
        latest_request = await load_forecast_request(db, request_id)
        if latest_request.status == FORECAST_STATUS_INTERESTED:
            return latest_request, "Заявка уже отправлена продажнику.", False
        if latest_request.status in DELIVERED_STATUSES:
            return latest_request, "Прогноз уже оформлен.", False
        if latest_request.status == FORECAST_STATUS_DECLINED:
            return latest_request, "Отказ уже учтен.", False
        if latest_request.status == FORECAST_STATUS_PROCESSING:
            return latest_request, "Заявка уже обрабатывается.", False
        if latest_request.status == FORECAST_STATUS_CANCELLED:
            return latest_request, "Заявка отменена.", False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже изменила статус",
        )

    forecast_request.status = FORECAST_STATUS_INTERESTED
    forecast_request.responded_at = responded_at
    if notify_sales_manager_now:
        notify_result = await asyncio.to_thread(notify_sales_manager, forecast_request)
        if not notify_result.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Продажник не уведомлен: {notify_result.get('description', 'unknown error')}",
            )
        return forecast_request, "Заявка отправлена продажнику.", False
    return forecast_request, "Заявка отправлена продажнику.", True


async def set_forecast_request_declined(
    db: AsyncSession,
    *,
    request_id: UUID,
    actor_user_id: int,
) -> tuple[ForecastRequest, str]:
    forecast_request = await load_forecast_request(db, request_id)
    if forecast_request.user_id != actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта кнопка привязана к другому клиенту",
        )

    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Прогноз уже оформлен."
    if forecast_request.status == FORECAST_STATUS_INTERESTED:
        return forecast_request, "Заявка уже у продажника."
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        return forecast_request, "Заявка уже обрабатывается."
    if forecast_request.status == FORECAST_STATUS_DECLINED:
        return forecast_request, "Отказ уже учтен."
    if forecast_request.status == FORECAST_STATUS_CANCELLED:
        return forecast_request, "Заявка отменена."

    forecast_request.status = FORECAST_STATUS_DECLINED
    forecast_request.responded_at = forecast_request.responded_at or _now()
    return forecast_request, "Ок, не берем."


async def deliver_forecast_request(
    db: AsyncSession,
    *,
    forecast_request: ForecastRequest,
    handled_by: Optional[int],
    delivery_method: str,
    send_to_client: bool,
) -> tuple[ForecastRequest, UserBetAccessResult]:
    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, _already_taken_access_result(forecast_request)

    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже обрабатывается",
        )

    if forecast_request.status == FORECAST_STATUS_ANNOUNCED:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Клиент еще не нажал «Беру»",
        )

    if forecast_request.status in {FORECAST_STATUS_DECLINED, FORECAST_STATUS_CANCELLED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Эту заявку нельзя доставить: клиент отказался или заявка отменена",
        )

    if send_to_client:
        _ensure_full_forecast_ready(forecast_request)

    lock_result = await db.execute(
        update(ForecastRequest)
        .where(
            ForecastRequest.id == forecast_request.id,
            ForecastRequest.status == FORECAST_STATUS_INTERESTED,
        )
        .values(
            status=FORECAST_STATUS_PROCESSING,
            handled_by=handled_by,
        )
    )
    if lock_result.rowcount != 1:
        await db.rollback()
        latest_request = await load_forecast_request(db, forecast_request.id)
        if latest_request.status in DELIVERED_STATUSES:
            return latest_request, _already_taken_access_result(latest_request)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже обрабатывается или изменила статус",
        )

    forecast_request.status = FORECAST_STATUS_PROCESSING
    forecast_request.handled_by = handled_by
    await db.commit()

    try:
        if send_to_client:
            send_result = send_full_forecast_to_client(forecast_request)
            if not send_result.get("ok"):
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Telegram не доставил прогноз клиенту: {send_result.get('description', 'unknown error')}",
                )

        access_result = await record_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            charge_match=True,
            allow_negative_balance=True,
            note=f"Private forecast activated via {delivery_method}",
        )
        forecast_request.status = FORECAST_STATUS_SENT if delivery_method == "bot" else FORECAST_STATUS_MANUAL_SENT
        forecast_request.delivery_method = delivery_method
        forecast_request.handled_by = handled_by
        forecast_request.delivered_at = forecast_request.delivered_at or _now()
        forecast_request.balance_before = access_result.balance_before
        forecast_request.balance_after = access_result.balance_after
        forecast_request.no_balance_warning = access_result.no_balance_warning
        await db.commit()
        return forecast_request, access_result
    except Exception:
        await db.rollback()
        forecast_request.status = FORECAST_STATUS_INTERESTED
        forecast_request.handled_by = None
        await db.commit()
        raise


async def cancel_forecast_request(
    db: AsyncSession,
    *,
    forecast_request: ForecastRequest,
    handled_by: Optional[int],
) -> tuple[ForecastRequest, str]:
    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Заявка уже доставлена."
    if forecast_request.status == FORECAST_STATUS_CANCELLED:
        return forecast_request, "Заявка уже отменена."
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже обрабатывается, отмена недоступна",
        )

    forecast_request.status = FORECAST_STATUS_CANCELLED
    forecast_request.handled_by = handled_by
    return forecast_request, "Заявка отменена."


async def handle_sales_callback(
    db: AsyncSession,
    *,
    request_id: UUID,
    actor_user_id: int,
    action: str,
) -> tuple[ForecastRequest, str]:
    sales_manager_id = settings.sales_manager_telegram_id
    if not sales_manager_id or actor_user_id != sales_manager_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта кнопка доступна только назначенному продажнику",
        )

    forecast_request = await load_forecast_request(db, request_id)
    if action == "sales_send":
        forecast_request, access_result = await deliver_forecast_request(
            db,
            forecast_request=forecast_request,
            handled_by=actor_user_id,
            delivery_method="bot",
            send_to_client=True,
        )
        if access_result.already_recorded:
            return forecast_request, "Прогноз уже был отправлен."
        return forecast_request, "Прогноз отправлен клиенту."

    if action == "sales_manual":
        forecast_request, access_result = await deliver_forecast_request(
            db,
            forecast_request=forecast_request,
            handled_by=actor_user_id,
            delivery_method="manual",
            send_to_client=False,
        )
        if access_result.already_recorded:
            return forecast_request, "Ручная отправка уже была отмечена."
        return forecast_request, "Клиент отмечен как взявший прогноз."

    if action == "sales_cancel":
        _, message = await cancel_forecast_request(
            db,
            forecast_request=forecast_request,
            handled_by=actor_user_id,
        )
        return forecast_request, message

    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail="Неизвестное действие заявки",
    )
