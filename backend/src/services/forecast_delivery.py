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
from sqlalchemy import func, inspect as sa_inspect, or_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.api.go import bookmaker_match_url_for_bet
from src.core.bookmaker_links import normalize_match_url as normalize_bookmaker_match_url
from src.core.config import settings
from src.core.message_templates import (
    TEMPLATE_PAID_SET_TEASER,
    TEMPLATE_FORECAST_FULL,
    TEMPLATE_FORECAST_TEASER,
    default_message_template_body,
    load_message_template_body,
    render_message_template_body,
)
from src.core.roles import STAFF_ROLES, is_admin_role
from src.core.telegram_delivery import is_personal_telegram_user_id, user_can_receive_personal_telegram
from src.core.telegram_text import append_contact_footer, bookmaker_custom_emoji
from src.models.database import AsyncSessionLocal
from src.models.models import Bet, ForecastRequest, User, user_bets
from src.services.delivery_outbox import (
    CHANNEL_FORECAST_ADMIN_FULL_COPY,
    CHANNEL_FORECAST_AUTO_DELIVERY,
    CHANNEL_FORECAST_FULL_DELIVERY,
    CHANNEL_TELEGRAM_MESSAGE,
    enqueue_delivery,
)
from src.services.match_access import (
    UserBetAccessResult,
    load_locked_bet_for_user_access,
    lock_user_balance,
    record_user_bet_access,
    user_has_full_forecast_access,
)
from src.services.signals import deliver_personal_signal
from src.services.telegram_bot import call_telegram_api, call_telegram_api_multipart
from src.services.vk_delivery import (
    html_to_vk_text,
    is_vk_message_permission_error,
    local_static_asset_path as vk_local_static_asset_path,
    mark_vk_messages_denied,
    refresh_vk_delivery_status,
    send_vk_message_to_user,
    user_can_receive_vk_messages,
)

logger = logging.getLogger("uvicorn")

FORECAST_STATUS_ANNOUNCED = "announced"
FORECAST_STATUS_INTERESTED = "interested"
FORECAST_STATUS_DECLINED = "declined"
FORECAST_STATUS_PROCESSING = "processing"
FORECAST_STATUS_SENT = "sent"
FORECAST_STATUS_MANUAL_SENT = "manual_sent"
FORECAST_STATUS_CANCELLED = "cancelled"
FORECAST_STATUS_REMOVED = "removed"

DELIVERED_STATUSES = {FORECAST_STATUS_SENT, FORECAST_STATUS_MANUAL_SENT}
PLACEHOLDER_EVENT_NAME = "Закрытый прогноз"
PAID_SET_PLACEHOLDER_EVENT_NAME = "Платный набор"
DELIVERY_MODE_SALES_PRIVATE = "sales_private"
DELIVERY_MODE_PAID_SET = "paid_set"
FULL_FORECAST_LINKS_REQUIRED_MESSAGE = "Добавьте хотя бы одну ссылку по БК"
FORECAST_CONTACT_DRAFT_TEXT = "хочу получить ставку из анонса."
FORECAST_CONTACT_REQUIRED_MESSAGE = (
    f"Чтобы получить ставку, напишите Shamrai: {FORECAST_CONTACT_DRAFT_TEXT}"
)
FORECAST_INACTIVE_MESSAGE = "Прогноз уже не активен. Реагировать не нужно."
PAID_SET_INACTIVE_MESSAGE = "Набор уже не активен. Реагировать не нужно."
TELEGRAM_PHOTO_CAPTION_LIMIT = 1024
TELEGRAM_MESSAGE_TEXT_LIMIT = 4096
STATIC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
)
BOOKMAKER_LOGO_PATHS = {
    "fonbet": "/bookmakers/transparent/fonbet.png",
    "betboom": "/bookmakers/transparent/betboom.png",
    "winline": "/bookmakers/transparent/winline.png",
    "pari": "/bookmakers/transparent/pari.png",
    "ligastavok": "/bookmakers/transparent/ligastavok.png",
    "marathon": "/bookmakers/transparent/marathon.png",
    "betcity": "/bookmakers/transparent/betcity.png",
    "melbet": "/bookmakers/transparent/melbet.png",
    "leon": "/bookmakers/transparent/leon.png",
    "olimpbet": "/bookmakers/transparent/olimpbet.png",
    "olimp": "/bookmakers/transparent/olimpbet.png",
    "zenit": "/bookmakers/transparent/zenit.png",
    "bettery": "/bookmakers/transparent/bettery.png",
    "other": "/bookmakers/other.jpg",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _html(value: Optional[object]) -> str:
    return html.escape(str(value or "").strip(), quote=True)


def _coefficient_text(bet: Bet) -> str:
    try:
        return f"{float(bet.coefficient):.2f}"
    except Exception:
        return str(bet.coefficient)


def _fair_coefficient_text(bet: Bet) -> Optional[str]:
    fair_coefficient = getattr(bet, "fair_coefficient", None)
    if fair_coefficient is None:
        return None
    try:
        return f"{float(fair_coefficient):.2f}"
    except Exception:
        return str(fair_coefficient)


def _fair_coefficient_line(bet: Bet) -> str:
    fair_text = _fair_coefficient_text(bet)
    return f"Верный: <b>{_html(fair_text)}</b>" if fair_text else ""


def _format_rub_price(value: Optional[object]) -> str:
    try:
        amount = int(value or 0)
    except (TypeError, ValueError):
        amount = 0
    if amount <= 0:
        return "уточним лично"
    return f"{amount:,}".replace(",", " ") + " ₽"


def _trim_text(value: Optional[object], limit: int = 900) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _loaded_attr(instance: object, attr_name: str) -> object:
    try:
        state = sa_inspect(instance)
        if attr_name in state.unloaded:
            return None
    except Exception:
        pass
    return getattr(instance, attr_name, None)


def _bookmakers_for_bet(bet: Bet) -> list:
    bookmakers = list(_loaded_attr(bet, "bookmakers") or [])
    bookmaker = _loaded_attr(bet, "bookmaker")
    if not bookmakers and bookmaker:
        bookmakers = [bookmaker]
    return bookmakers


def bookmaker_names_for_bet(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    return ", ".join(bookmaker.name for bookmaker in bookmakers) or "не указана"


def _paid_set_bookmaker_line(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    if not bookmakers:
        return "<b>не указана</b>"
    labels = []
    for bookmaker in bookmakers:
        emoji = _bookmaker_emoji(bookmaker)
        prefix = f"{emoji} " if emoji else ""
        labels.append(f"{prefix}<b>{_html(bookmaker.name.upper())}</b>")
    return " | ".join(labels)


def bet_is_paid_set(bet: Optional[Bet]) -> bool:
    return str(getattr(bet, "delivery_mode", "") or "") == DELIVERY_MODE_PAID_SET


def request_is_paid_set(forecast_request: ForecastRequest) -> bool:
    return bet_is_paid_set(getattr(forecast_request, "bet", None))


def forecast_request_inactive_message(forecast_request: ForecastRequest) -> str:
    return PAID_SET_INACTIVE_MESSAGE if request_is_paid_set(forecast_request) else FORECAST_INACTIVE_MESSAGE


def forecast_request_is_inactive_for_client(forecast_request: ForecastRequest) -> bool:
    return (
        getattr(forecast_request, "status", None) in {FORECAST_STATUS_CANCELLED, FORECAST_STATUS_REMOVED}
        or getattr(getattr(forecast_request, "bet", None), "status", None) == "deleted"
    )


def _bookmaker_label(bookmaker) -> str:
    emoji = _bookmaker_emoji(bookmaker)
    prefix = f"{emoji} " if emoji else ""
    return f"{prefix}<b>{_html(getattr(bookmaker, 'name', None))}</b>"


def _bookmaker_labels_for_bet(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    if not bookmakers:
        return "<b>не указана</b>"
    return ", ".join(_bookmaker_label(bookmaker) for bookmaker in bookmakers)


def _normalize_match_url(raw_url: Optional[object]) -> str:
    return normalize_bookmaker_match_url(raw_url)


def _bookmaker_emoji(bookmaker: Optional[object]) -> str:
    if not bookmaker:
        return ""
    return bookmaker_custom_emoji(
        getattr(bookmaker, "code", None),
        getattr(bookmaker, "name", None),
    )


def _bookmaker_button_name(bookmaker: Optional[object]) -> str:
    if not bookmaker:
        return "Открыть"
    name = str(getattr(bookmaker, "name", None) or getattr(bookmaker, "code", None) or "").strip()
    if not name:
        return "Открыть"
    return f"{name[:61]}..." if len(name) > 64 else name


def _bookmaker_display_name(bookmaker: Optional[object]) -> str:
    if not bookmaker:
        return "Ссылка на матч"
    name = str(getattr(bookmaker, "name", None) or getattr(bookmaker, "code", None) or "").strip()
    return name or "Ссылка на матч"


def _bookmaker_logo_url(bookmaker: Optional[object]) -> str:
    code = str(getattr(bookmaker, "code", None) or "other").strip().lower()
    return BOOKMAKER_LOGO_PATHS.get(code, BOOKMAKER_LOGO_PATHS["other"])


def _bookmaker_link_items(bet: Bet) -> list[tuple[Optional[int], str]]:
    link_items: list[tuple[Optional[int], str]] = []
    seen_items: set[tuple[Optional[int], str]] = set()

    def add_item(raw_bookmaker_id: Optional[object], raw_url: Optional[object]) -> None:
        bookmaker_id: Optional[int] = None
        if raw_bookmaker_id not in (None, ""):
            try:
                bookmaker_id = int(raw_bookmaker_id)
            except (TypeError, ValueError):
                bookmaker_id = None
        url = _normalize_match_url(raw_url)
        item_key = (bookmaker_id, url)
        if url and item_key not in seen_items:
            link_items.append((bookmaker_id, url))
            seen_items.add(item_key)

    for item in bet.bookmaker_links or []:
        if isinstance(item, dict):
            raw_bookmaker_id = item.get("bookmaker_id") or item.get("bookmakerId") or item.get("id")
            raw_url = item.get("url") or item.get("link") or item.get("match_link")
            add_item(raw_bookmaker_id, raw_url)
            continue
        if hasattr(item, "bookmaker_id") or hasattr(item, "url"):
            add_item(getattr(item, "bookmaker_id", None), getattr(item, "url", None))
            continue
        if isinstance(item, str):
            add_item(None, item)

    fallback_match_url = _normalize_match_url(getattr(bet, "match_link", None))
    if fallback_match_url:
        add_item(getattr(bet, "bookmaker_id", None), fallback_match_url)

    return link_items


def _bookmaker_link_targets(bet: Bet) -> list[dict[str, object]]:
    links_by_bookmaker_id: dict[int, str] = {}
    untargeted_urls: list[str] = []
    link_items = _bookmaker_link_items(bet)
    for bookmaker_id, url in link_items:
        if bookmaker_id is None:
            untargeted_urls.append(url)
            continue
        links_by_bookmaker_id[bookmaker_id] = url

    targets: list[dict[str, object]] = []
    used_bookmaker_ids: set[int] = set()
    used_urls: set[str] = set()
    bookmakers = _bookmakers_for_bet(bet)
    for bookmaker in bookmakers:
        url = links_by_bookmaker_id.get(bookmaker.id)
        if not url and len(bookmakers) == 1 and untargeted_urls:
            url = untargeted_urls.pop(0)
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


def _bookmaker_web_items(bet: Bet) -> list[dict[str, object]]:
    targets = _bookmaker_link_targets(bet)
    target_url_by_bookmaker_id: dict[int, str] = {}
    untargeted_urls: list[str] = []
    for target in targets:
        bookmaker = target.get("bookmaker")
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        if bookmaker:
            try:
                target_url_by_bookmaker_id[int(getattr(bookmaker, "id"))] = url
            except (TypeError, ValueError):
                untargeted_urls.append(url)
        else:
            untargeted_urls.append(url)

    items: list[dict[str, object]] = []
    used_urls: set[str] = set()
    for bookmaker in _bookmakers_for_bet(bet):
        try:
            bookmaker_id = int(getattr(bookmaker, "id"))
        except (TypeError, ValueError):
            continue
        url = target_url_by_bookmaker_id.get(bookmaker_id, "")
        if url:
            used_urls.add(url)
        items.append({
            "id": bookmaker_id,
            "name": _bookmaker_display_name(bookmaker),
            "code": str(getattr(bookmaker, "code", None) or "other"),
            "logo_url": _bookmaker_logo_url(bookmaker),
            "url": url,
        })

    for index, url in enumerate(untargeted_urls):
        if url in used_urls:
            continue
        items.append({
            "id": f"link-{index}",
            "name": "Ссылка на матч",
            "code": "other",
            "logo_url": BOOKMAKER_LOGO_PATHS["other"],
            "url": url,
        })
    return items


def _bookmaker_link_lines(bet: Bet) -> list[str]:
    lines: list[str] = []
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        prefix = _bookmaker_emoji(bookmaker) if bookmaker else ""
        label = _bookmaker_display_name(bookmaker)
        lines.append(f"{prefix or '🔗'} <b>{_html(label)}</b>\nНажмите кнопку ниже, чтобы открыть матч")
    return lines


def _bookmaker_links_message(bet: Bet) -> Optional[str]:
    lines = ["<b>Ссылки на матч</b>"]
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        prefix = _bookmaker_emoji(bookmaker) if bookmaker else ""
        label = _bookmaker_display_name(bookmaker)
        lines.append(f"{prefix or '🔗'} <b>{_html(label)}</b>\nНажмите кнопку ниже, чтобы открыть матч")
    if len(lines) == 1:
        return None
    return _join_forecast_lines(lines)


def _bookmaker_links_plain_text(bet: Bet) -> Optional[str]:
    lines = ["Ссылки на матч"]
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        url = str(target.get("url") or "").strip()
        if not url:
            continue
        label = _bookmaker_display_name(bookmaker)
        lines.append(f"{label}: {url}")
    if len(lines) == 1:
        return None
    return "\n".join(lines)


def _bookmaker_link_reply_markup(bet: Bet) -> Optional[dict]:
    buttons = []
    for target in _bookmaker_link_targets(bet):
        bookmaker = target.get("bookmaker")
        url = _bookmaker_redirect_url(bet, bookmaker)
        if not url:
            continue
        buttons.append([
            {
                "text": _bookmaker_button_name(bookmaker),
                "url": url,
            }
        ])
    if not buttons:
        return None
    return {"inline_keyboard": buttons}


def _bookmaker_redirect_url(bet: Bet, bookmaker: Optional[object]) -> str:
    if not bookmaker:
        return ""
    try:
        bookmaker_id = int(getattr(bookmaker, "id", 0) or 0)
    except (TypeError, ValueError):
        return ""
    if not bookmaker_id or not bookmaker_match_url_for_bet(bet, bookmaker_id):
        return ""

    bet_id = str(getattr(bet, "id", "") or "").strip()
    if not bet_id:
        return ""
    base_url = (settings.API_BASE_URL or settings.FRONTEND_BASE_URL or "").strip().rstrip("/")
    if not base_url:
        return ""
    return f"{base_url}/api/go/bets/{bet_id}/bookmakers/{bookmaker_id}"


def _join_forecast_lines(lines: list[str]) -> str:
    return "\n\n".join(line for line in lines if line)


def _plain_text_length(html_text: str) -> int:
    custom_emoji_text = re.sub(r"<tg-emoji\b[^>]*>(.*?)</tg-emoji>", r"\1", html_text, flags=re.IGNORECASE)
    without_tags = re.sub(r"<[^>]+>", "", custom_emoji_text)
    return len(html.unescape(without_tags))


def _fits_photo_caption(html_text: str) -> bool:
    return _plain_text_length(html_text) <= TELEGRAM_PHOTO_CAPTION_LIMIT


def _fits_text_message(html_text: str) -> bool:
    return _plain_text_length(html_text) <= TELEGRAM_MESSAGE_TEXT_LIMIT


def _short_coupon_caption(event_name: Optional[object]) -> str:
    base = "Купон к прогнозу"
    clean_event_name = str(event_name or "").strip()
    if not clean_event_name:
        return base
    caption = f"{base}: {clean_event_name}"
    if len(caption) <= TELEGRAM_PHOTO_CAPTION_LIMIT:
        return caption
    suffix = "..."
    prefix = f"{base}: "
    event_limit = TELEGRAM_PHOTO_CAPTION_LIMIT - len(prefix) - len(suffix)
    if event_limit <= 0:
        return base[:TELEGRAM_PHOTO_CAPTION_LIMIT]
    return f"{prefix}{clean_event_name[:event_limit].rstrip()}{suffix}"


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


def _is_photo_dimensions_error(result: dict) -> bool:
    description = str(result.get("description") or "").upper()
    return "PHOTO_INVALID_DIMENSIONS" in description


def _send_coupon_document_to_telegram(
    *,
    chat_id: int,
    coupon_caption: str,
    coupon_file_path: Optional[str],
    coupon_url: Optional[str],
    parse_mode: Optional[str],
    reply_markup: Optional[dict] = None,
) -> dict:
    payload = {
        "chat_id": chat_id,
        "caption": coupon_caption,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup

    if coupon_file_path:
        filename = os.path.basename(coupon_file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(coupon_file_path, "rb") as file_obj:
            return call_telegram_api_multipart(
                "sendDocument",
                payload,
                {
                    "document": (filename, file_obj.read(), content_type),
                },
            )

    if coupon_url:
        return call_telegram_api(
            "sendDocument",
            {
                **payload,
                "document": coupon_url,
            },
        )

    return {"ok": False, "description": "Coupon file is missing"}


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


def _telegram_client_dialog_url(user: User) -> Optional[str]:
    username = str(getattr(user, "username", "") or "").strip().lstrip("@")
    if username and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        return f"https://t.me/{username}"
    try:
        telegram_id = int(getattr(user, "telegram_id", 0) or 0)
    except (TypeError, ValueError):
        return None
    if telegram_id <= 0:
        return None
    return f"tg://user?id={telegram_id}"


def _vk_client_dialog_id(user: User) -> Optional[str]:
    raw_vk_user_id = str(getattr(user, "vk_user_id", "") or "").strip()
    match = re.fullmatch(r"(?:vk[-_]?|id)?(\d+)", raw_vk_user_id, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        vk_user_id = int(match.group(1))
    except (TypeError, ValueError):
        return None
    return str(vk_user_id) if vk_user_id > 0 else None


def _vk_client_dialog_url(user: User) -> Optional[str]:
    vk_user_id = _vk_client_dialog_id(user)
    return f"https://vk.com/im?sel={vk_user_id}" if vk_user_id else None


def _admin_web_chat_url(user: User) -> Optional[str]:
    base_url = settings.FRONTEND_BASE_URL.strip().rstrip("/") or "/app"
    try:
        user_id = int(getattr(user, "telegram_id", 0) or 0)
    except (TypeError, ValueError):
        return None
    separator = "&" if "?" in base_url else "?"
    return f"{base_url}{separator}open=admin-web-chat&user_id={user_id}"


def _paid_set_admin_dialog_buttons(user: User) -> list[dict[str, str]]:
    buttons: list[dict[str, str]] = []
    telegram_url = _telegram_client_dialog_url(user)
    if telegram_url:
        buttons.append({"text": "Продажа в диалоге", "url": telegram_url})
    vk_url = _vk_client_dialog_url(user)
    if vk_url:
        buttons.append({"text": "VK диалог", "url": vk_url})
    web_url = _admin_web_chat_url(user)
    if web_url:
        buttons.append({"text": "Web чат", "url": web_url})
    return buttons


def _already_taken_access_result(forecast_request: ForecastRequest) -> UserBetAccessResult:
    current_balance = user_match_balance(forecast_request.user)
    return UserBetAccessResult(
        status="already_taken",
        already_recorded=True,
        access_type="paid_match",
        match_charged=False,
        balance_before=forecast_request.balance_before if forecast_request.balance_before is not None else current_balance,
        balance_after=forecast_request.balance_after if forecast_request.balance_after is not None else current_balance,
        no_balance_warning=bool(forecast_request.no_balance_warning),
    )


def user_match_balance(user: User) -> int:
    return int(user.purchased_bets_balance or user.matches_remaining or 0)


def forecast_request_has_full_access(forecast_request: ForecastRequest) -> bool:
    return request_is_paid_set(forecast_request) or user_has_full_forecast_access(forecast_request.user)


def forecast_request_requires_contact(forecast_request: ForecastRequest) -> bool:
    return not forecast_request_has_full_access(forecast_request)


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


def _ensure_locked_private_forecast_is_deliverable(
    forecast_request: ForecastRequest,
    locked_bet: Bet,
) -> None:
    """Validate lifecycle and surface after acquiring the canonical Bet lock."""
    forecast_request.bet = locked_bet
    if locked_bet.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Прогноз остановлен администратором",
        )

    live_ends_at = getattr(locked_bet, "live_ends_at", None)
    if live_ends_at is not None:
        if live_ends_at.tzinfo is None:
            live_ends_at = live_ends_at.replace(tzinfo=timezone.utc)
        if live_ends_at <= _now():
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=forecast_request_inactive_message(forecast_request),
            )

    if locked_bet.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=forecast_request_inactive_message(forecast_request),
        )
    if str(getattr(locked_bet, "publication_type", "forecast") or "forecast").strip().lower() != "forecast":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текстовую публикацию нельзя доставить как прогноз",
        )
    if locked_bet.delivery_mode not in {DELIVERY_MODE_SALES_PRIVATE, DELIVERY_MODE_PAID_SET}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот прогноз не относится к закрытой рассылке",
        )


async def _lock_forecast_delivery_scope(
    db: AsyncSession,
    forecast_request: ForecastRequest,
) -> ForecastRequest:
    """Lock Bet -> User -> ForecastRequest and refresh every mutable decision input."""
    expected_bet_id = forecast_request.bet_id
    expected_user_id = forecast_request.user_id
    locked_bet = await load_locked_bet_for_user_access(db, expected_bet_id)
    forecast_request.bet = locked_bet
    await lock_user_balance(db, expected_user_id)

    result = await db.execute(
        select(ForecastRequest)
        .filter(ForecastRequest.id == forecast_request.id)
        .with_for_update()
        .options(
            selectinload(ForecastRequest.user).selectinload(User.bookmakers),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmaker),
            selectinload(ForecastRequest.bet).selectinload(Bet.bookmakers),
        )
        .execution_options(populate_existing=True)
    )
    locked_request = result.scalars().first()
    if locked_request is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Заявка на прогноз не найдена",
        )
    if locked_request.bet_id != expected_bet_id or locked_request.user_id != expected_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже изменилась",
        )
    locked_request.bet = locked_bet
    return locked_request


def build_teaser_message(
    forecast_request: ForecastRequest,
    teaser_text: Optional[str],
    *,
    template_body: Optional[str] = None,
) -> str:
    bet = forecast_request.bet
    stored_teaser_text = getattr(bet, "teaser_text", None)
    teaser = (
        (teaser_text if teaser_text is not None else stored_teaser_text) or ""
    ).strip() or "Есть закрытый прогноз под вашу БК. Нажмите, если хотите взять матч."
    return render_message_template_body(
        template_body or default_message_template_body(TEMPLATE_FORECAST_TEASER),
        {
            "bookmaker_labels": _bookmaker_labels_for_bet(bet),
            "coefficient": _coefficient_text(bet),
            "fair_coefficient_line": _fair_coefficient_line(bet),
            "teaser_text": teaser,
            "contact_footer": append_contact_footer("").strip(),
        },
        safe_keys={"bookmaker_labels", "fair_coefficient_line", "contact_footer"},
    )


def build_paid_set_teaser_message(
    forecast_request: ForecastRequest,
    teaser_text: Optional[str],
    *,
    title: Optional[str] = None,
    price_rub: Optional[int] = None,
    template_body: Optional[str] = None,
) -> str:
    bet = forecast_request.bet
    clean_title = (title or bet.event_name or PAID_SET_PLACEHOLDER_EVENT_NAME).strip() or PAID_SET_PLACEHOLDER_EVENT_NAME
    teaser = (teaser_text or bet.description or "").strip() or "Реальный КФ не выше 1.9!"
    price_text = _format_rub_price(price_rub if price_rub is not None else bet.price_stars)
    return render_message_template_body(
        template_body or default_message_template_body(TEMPLATE_PAID_SET_TEASER),
        {
            "title": clean_title,
            "coefficient": _coefficient_text(bet),
            "bookmaker_line": _paid_set_bookmaker_line(bet),
            "body": teaser,
            "price_text": price_text,
            "contact_footer": append_contact_footer("").strip(),
        },
        safe_keys={"bookmaker_line", "contact_footer"},
    )


def build_forecast_teaser_payload(
    forecast_request: ForecastRequest,
    teaser_text: Optional[str],
    *,
    template_body: Optional[str] = None,
    message_text: Optional[str] = None,
) -> dict:
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
        "text": message_text or build_teaser_message(forecast_request, teaser_text, template_body=template_body),
        "parse_mode": "HTML",
        "reply_markup": reply_markup,
    }


def send_forecast_teaser(forecast_request: ForecastRequest, teaser_text: Optional[str]) -> dict:
    if not is_personal_telegram_user_id(forecast_request.user_id):
        return {"ok": False, "description": "Client does not have a personal Telegram chat"}
    return call_telegram_api("sendMessage", build_forecast_teaser_payload(forecast_request, teaser_text))


def _build_full_forecast_message(
    forecast_request: ForecastRequest,
    *,
    include_bookmaker: bool = True,
    include_bookmaker_links: bool = False,
    template_body: Optional[str] = None,
) -> str:
    bet = forecast_request.bet
    return render_message_template_body(
        template_body or default_message_template_body(TEMPLATE_FORECAST_FULL),
        {
            "event_name": bet.event_name,
            "outcome": bet.outcome or "уточняется",
            "coefficient": _coefficient_text(bet),
            "bookmaker_line": f"БК: {_bookmaker_labels_for_bet(bet)}" if include_bookmaker else "",
            "description": bet.description or "",
            "bookmaker_links_block": _join_forecast_lines(_bookmaker_link_lines(bet)) if include_bookmaker_links else "",
            "contact_footer": append_contact_footer("").strip(),
        },
        safe_keys={"bookmaker_line", "bookmaker_links_block", "contact_footer"},
    )


def send_full_forecast_to_telegram_chat(
    forecast_request: ForecastRequest,
    *,
    chat_id: int,
    template_body: Optional[str] = None,
) -> dict:
    bet = forecast_request.bet
    base_full_message = _build_full_forecast_message(forecast_request, template_body=template_body)
    full_message_with_links = _build_full_forecast_message(
        forecast_request,
        include_bookmaker_links=True,
        template_body=template_body,
    )
    links_inline = bool(_bookmaker_link_targets(bet)) and _fits_photo_caption(full_message_with_links)
    reply_markup = _bookmaker_link_reply_markup(bet)
    has_coupon = bool(bet.coupon_image_url)
    # Telegram clients can render long URLs in media captions inconsistently.
    # Keep coupon captions focused on the forecast and always send match links as a separate message.
    full_message = base_full_message if has_coupon else full_message_with_links if links_inline else base_full_message
    use_full_caption = _fits_photo_caption(full_message)
    coupon_caption = full_message if use_full_caption else _short_coupon_caption(bet.event_name)
    coupon_file_path = _local_static_asset_path(bet.coupon_image_url)
    coupon_url = None if coupon_file_path else _public_asset_url(bet.coupon_image_url)

    if coupon_file_path:
        filename = os.path.basename(coupon_file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(coupon_file_path, "rb") as file_obj:
            payload = {
                "chat_id": chat_id,
                "caption": coupon_caption,
            }
            if use_full_caption:
                payload["parse_mode"] = "HTML"
            if reply_markup:
                payload["reply_markup"] = reply_markup
            photo_result = call_telegram_api_multipart(
                "sendPhoto",
                payload,
                {
                    "photo": (filename, file_obj.read(), content_type),
                },
            )
        if not photo_result.get("ok") and _is_photo_dimensions_error(photo_result):
            photo_result = _send_coupon_document_to_telegram(
                chat_id=chat_id,
                coupon_caption=coupon_caption,
                coupon_file_path=coupon_file_path,
                coupon_url=None,
                parse_mode="HTML" if use_full_caption else None,
                reply_markup=reply_markup,
            )
        if not photo_result.get("ok"):
            return photo_result
    elif coupon_url:
        payload = {
            "chat_id": chat_id,
            "photo": coupon_url,
            "caption": coupon_caption,
        }
        if use_full_caption:
            payload["parse_mode"] = "HTML"
        if reply_markup:
            payload["reply_markup"] = reply_markup
        photo_result = call_telegram_api("sendPhoto", payload)
        if not photo_result.get("ok") and _is_photo_dimensions_error(photo_result):
            photo_result = _send_coupon_document_to_telegram(
                chat_id=chat_id,
                coupon_caption=coupon_caption,
                coupon_file_path=None,
                coupon_url=coupon_url,
                parse_mode="HTML" if use_full_caption else None,
                reply_markup=reply_markup,
            )
        if not photo_result.get("ok"):
            return photo_result

    if coupon_file_path or coupon_url:
        if use_full_caption:
            if not reply_markup:
                links_result = _send_bookmaker_links_message(chat_id, bet)
                if links_result and not links_result.get("ok"):
                    return links_result
            return photo_result
        text_result = call_telegram_api("sendMessage", {
            "chat_id": chat_id,
            "text": full_message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            **({"reply_markup": reply_markup} if reply_markup else {}),
        })
        if not text_result.get("ok"):
            return text_result
        if not reply_markup:
            links_result = _send_bookmaker_links_message(chat_id, bet)
            if links_result and not links_result.get("ok"):
                return links_result
        return text_result

    text_result = call_telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": full_message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        **({"reply_markup": reply_markup} if links_inline and reply_markup else {}),
    })
    if not text_result.get("ok"):
        return text_result
    if not links_inline:
        links_result = _send_bookmaker_links_message(chat_id, bet)
        if links_result and not links_result.get("ok"):
            return links_result
    return text_result


def send_full_forecast_to_client(
    forecast_request: ForecastRequest,
    *,
    template_body: Optional[str] = None,
) -> dict:
    if not is_personal_telegram_user_id(forecast_request.user_id):
        return {"ok": False, "description": "Client does not have a personal Telegram chat"}
    return send_full_forecast_to_telegram_chat(
        forecast_request,
        chat_id=forecast_request.user_id,
        template_body=template_body,
    )


def send_full_forecast_to_vk_client(
    forecast_request: ForecastRequest,
    *,
    template_body: Optional[str] = None,
) -> dict:
    if not user_can_receive_vk_messages(forecast_request.user):
        return {"ok": False, "description": "Client has not allowed VK messages"}

    bet = forecast_request.bet
    full_message = html_to_vk_text(_build_full_forecast_message(forecast_request, template_body=template_body))
    links_message = _bookmaker_links_plain_text(bet) or ""
    coupon_file_path = vk_local_static_asset_path(bet.coupon_image_url)
    coupon_url = None if coupon_file_path else _public_asset_url(bet.coupon_image_url)

    message_parts = [full_message]
    if links_message:
        message_parts.append(links_message)
    if coupon_url:
        message_parts.append(f"Купон: {coupon_url}")

    return send_vk_message_to_user(
        forecast_request.user,
        "\n\n".join(part for part in message_parts if part),
        image_path=coupon_file_path,
    )


def build_web_full_forecast_text(
    forecast_request: ForecastRequest,
    *,
    template_body: Optional[str] = None,
) -> str:
    return html_to_vk_text(_build_full_forecast_message(
        forecast_request,
        include_bookmaker=False,
        template_body=template_body,
    ))


def build_web_forecast_signal_data(
    forecast_request: ForecastRequest,
    *,
    status_value: Optional[str] = None,
    template_body: Optional[str] = None,
) -> dict[str, object]:
    bet = forecast_request.bet
    return {
        "message_html": _build_full_forecast_message(
            forecast_request,
            include_bookmaker=False,
            template_body=template_body,
        ),
        "message_text": build_web_full_forecast_text(forecast_request, template_body=template_body),
        "coupon_image_url": bet.coupon_image_url,
        "bookmakers": _bookmaker_web_items(bet),
        "event_name": bet.event_name,
        "outcome": bet.outcome,
        "coefficient": _coefficient_text(bet),
        "sport_type": bet.sport_type,
        "description": bet.description,
        "forecast_request_id": str(forecast_request.id),
        "forecast_status": status_value or forecast_request.status,
        "bet_id": str(getattr(forecast_request, "bet_id", None) or getattr(bet, "id", "")),
    }


def build_web_teaser_signal_data(
    forecast_request: ForecastRequest,
    teaser_text: Optional[str],
    *,
    template_body: Optional[str] = None,
) -> dict[str, object]:
    bet = forecast_request.bet
    message_html = build_teaser_message(forecast_request, teaser_text, template_body=template_body)
    return {
        "message_html": message_html,
        "message_text": html_to_vk_text(message_html),
        "coupon_image_url": None,
        "bookmakers": _bookmaker_web_items(bet),
        "event_name": bet.event_name,
        "coefficient": _coefficient_text(bet),
        "fair_coefficient": _fair_coefficient_text(bet),
        "sport_type": bet.sport_type,
        "forecast_request_id": str(forecast_request.id),
        "forecast_status": forecast_request.status,
        "bet_id": str(getattr(forecast_request, "bet_id", None) or getattr(bet, "id", "")),
        "actions": ["take", "decline"],
    }


def build_web_paid_set_signal_data(
    forecast_request: ForecastRequest,
    teaser_text: Optional[str],
    *,
    title: Optional[str] = None,
    price_rub: Optional[int] = None,
    template_body: Optional[str] = None,
) -> dict[str, object]:
    bet = forecast_request.bet
    message_html = build_paid_set_teaser_message(
        forecast_request,
        teaser_text,
        title=title,
        price_rub=price_rub,
        template_body=template_body,
    )
    return {
        "message_html": message_html,
        "message_text": html_to_vk_text(message_html),
        "coupon_image_url": None,
        "bookmakers": _bookmaker_web_items(bet),
        "event_name": bet.event_name,
        "coefficient": _coefficient_text(bet),
        "sport_type": bet.sport_type,
        "forecast_request_id": str(forecast_request.id),
        "forecast_status": forecast_request.status,
        "bet_id": str(getattr(forecast_request, "bet_id", None) or getattr(bet, "id", "")),
        "request_kind": "paid_set",
        "price_text": _format_rub_price(price_rub if price_rub is not None else bet.price_stars),
        "actions": ["take", "decline"],
    }


def build_paid_set_sale_message(forecast_request: ForecastRequest) -> str:
    bet = forecast_request.bet
    description = _trim_text(getattr(bet, "description", None), 1400)
    lines = [
        "✅ <b>Набор оформлен</b>",
        f"Матч: <b>{_html(bet.event_name or PAID_SET_PLACEHOLDER_EVENT_NAME)}</b>",
        f"Исход: <b>{_html(bet.outcome or 'уточняется')}</b>",
        f"КФ: <b>{_html(_coefficient_text(bet))}</b>",
        f"Стоимость: <b>{_html(_format_rub_price(getattr(bet, 'price_stars', None)))}</b>",
        f"БК: {_bookmaker_labels_for_bet(bet)}",
        f"Спорт: <b>{_html(bet.sport_type or 'не указан')}</b>",
    ]
    if description:
        lines.append(_html(description))
    contact_footer = append_contact_footer("").strip()
    if contact_footer:
        lines.append(contact_footer)
    return "\n\n".join(lines)


def build_web_paid_set_sale_text(forecast_request: ForecastRequest) -> str:
    return html_to_vk_text(build_paid_set_sale_message(forecast_request))


def build_web_paid_set_sale_signal_data(
    forecast_request: ForecastRequest,
    *,
    status_value: Optional[str] = None,
) -> dict[str, object]:
    bet = forecast_request.bet
    message_html = build_paid_set_sale_message(forecast_request)
    return {
        "message_html": message_html,
        "message_text": html_to_vk_text(message_html),
        "coupon_image_url": bet.coupon_image_url,
        "bookmakers": _bookmaker_web_items(bet),
        "event_name": bet.event_name,
        "outcome": bet.outcome,
        "coefficient": _coefficient_text(bet),
        "sport_type": bet.sport_type,
        "description": bet.description,
        "forecast_request_id": str(forecast_request.id),
        "forecast_status": status_value or forecast_request.status,
        "bet_id": str(getattr(forecast_request, "bet_id", None) or getattr(bet, "id", "")),
        "request_kind": "paid_set",
        "price_text": _format_rub_price(getattr(bet, "price_stars", None)),
    }


def send_paid_set_sale_to_telegram_chat(
    forecast_request: ForecastRequest,
    *,
    chat_id: int,
) -> dict:
    bet = forecast_request.bet
    message = build_paid_set_sale_message(forecast_request)
    reply_markup = _bookmaker_link_reply_markup(bet)
    coupon_file_path = _local_static_asset_path(bet.coupon_image_url)
    coupon_url = None if coupon_file_path else _public_asset_url(bet.coupon_image_url)
    use_full_caption = _fits_photo_caption(message)
    coupon_caption = message if use_full_caption else _short_coupon_caption(bet.event_name)

    if coupon_file_path:
        filename = os.path.basename(coupon_file_path)
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        with open(coupon_file_path, "rb") as file_obj:
            payload = {
                "chat_id": chat_id,
                "caption": coupon_caption,
            }
            if use_full_caption:
                payload["parse_mode"] = "HTML"
            if reply_markup:
                payload["reply_markup"] = reply_markup
            photo_result = call_telegram_api_multipart(
                "sendPhoto",
                payload,
                {
                    "photo": (filename, file_obj.read(), content_type),
                },
            )
        if not photo_result.get("ok") and _is_photo_dimensions_error(photo_result):
            photo_result = _send_coupon_document_to_telegram(
                chat_id=chat_id,
                coupon_caption=coupon_caption,
                coupon_file_path=coupon_file_path,
                coupon_url=None,
                parse_mode="HTML" if use_full_caption else None,
                reply_markup=reply_markup,
            )
        if not photo_result.get("ok"):
            return photo_result
        if use_full_caption:
            return photo_result

    elif coupon_url:
        payload = {
            "chat_id": chat_id,
            "photo": coupon_url,
            "caption": coupon_caption,
        }
        if use_full_caption:
            payload["parse_mode"] = "HTML"
        if reply_markup:
            payload["reply_markup"] = reply_markup
        photo_result = call_telegram_api("sendPhoto", payload)
        if not photo_result.get("ok") and _is_photo_dimensions_error(photo_result):
            photo_result = _send_coupon_document_to_telegram(
                chat_id=chat_id,
                coupon_caption=coupon_caption,
                coupon_file_path=None,
                coupon_url=coupon_url,
                parse_mode="HTML" if use_full_caption else None,
                reply_markup=reply_markup,
            )
        if not photo_result.get("ok"):
            return photo_result
        if use_full_caption:
            return photo_result

    return call_telegram_api("sendMessage", {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        **({"reply_markup": reply_markup} if reply_markup else {}),
    })


def send_paid_set_sale_to_client(
    forecast_request: ForecastRequest,
) -> dict:
    if not is_personal_telegram_user_id(forecast_request.user_id):
        return {"ok": False, "description": "Client does not have a personal Telegram chat"}
    return send_paid_set_sale_to_telegram_chat(forecast_request, chat_id=forecast_request.user_id)


def send_paid_set_sale_to_vk_client(forecast_request: ForecastRequest) -> dict:
    if not user_can_receive_vk_messages(forecast_request.user):
        return {"ok": False, "description": "Client has not allowed VK messages"}

    bet = forecast_request.bet
    message = html_to_vk_text(build_paid_set_sale_message(forecast_request))
    links_message = _bookmaker_links_plain_text(bet) or ""
    coupon_file_path = vk_local_static_asset_path(bet.coupon_image_url)
    coupon_url = None if coupon_file_path else _public_asset_url(bet.coupon_image_url)
    message_parts = [message]
    if links_message:
        message_parts.append(links_message)
    if coupon_url:
        message_parts.append(f"Купон: {coupon_url}")

    return send_vk_message_to_user(
        forecast_request.user,
        "\n\n".join(part for part in message_parts if part),
        image_path=coupon_file_path,
    )


async def deliver_paid_set_sale_to_web_chat(
    db: AsyncSession,
    forecast_request: ForecastRequest,
) -> None:
    await deliver_personal_signal(
        db,
        user=forecast_request.user,
        text=build_web_paid_set_sale_text(forecast_request),
        signal_type="forecast_full",
        data=build_web_paid_set_sale_signal_data(forecast_request),
        send_telegram=False,
        send_web_push=True,
    )


async def deliver_full_forecast_to_web_chat(
    db: AsyncSession,
    forecast_request: ForecastRequest,
    *,
    template_body: Optional[str] = None,
) -> None:
    await deliver_personal_signal(
        db,
        user=forecast_request.user,
        text=build_web_full_forecast_text(forecast_request, template_body=template_body),
        signal_type="forecast_full",
        data=build_web_forecast_signal_data(forecast_request, template_body=template_body),
        send_telegram=False,
        send_web_push=True,
    )


async def send_full_forecast_to_external_channels(
    forecast_request: ForecastRequest,
    *,
    delivery_method: str,
    db: Optional[AsyncSession] = None,
) -> dict:
    if request_is_paid_set(forecast_request):
        if delivery_method == "vk":
            result = await asyncio.to_thread(send_paid_set_sale_to_vk_client, forecast_request)
            return {"ok": bool(result.get("ok")), "channel": "vk", "result": result}

        if delivery_method == "vk_bot":
            vk_result = await asyncio.to_thread(send_paid_set_sale_to_vk_client, forecast_request)
            if not vk_result.get("ok"):
                return {"ok": False, "channel": "vk", "result": vk_result}
            telegram_result = await asyncio.to_thread(send_paid_set_sale_to_client, forecast_request)
            if not telegram_result.get("ok"):
                logger.warning(
                    "[ForecastDelivery] Telegram duplicate failed for paid set request %s after VK success: %s",
                    forecast_request.id,
                    telegram_result.get("description", "unknown error"),
                )
            return {"ok": True, "channel": "vk_bot", "result": vk_result, "telegram_result": telegram_result}

        if delivery_method == "bot":
            result = await asyncio.to_thread(send_paid_set_sale_to_client, forecast_request)
            return {"ok": bool(result.get("ok")), "channel": "telegram", "result": result}

        if delivery_method == "web":
            return {"ok": True, "channel": "web", "result": {"ok": True}}

        return {"ok": False, "channel": delivery_method, "result": {"description": "Unknown delivery method"}}

    template_body = (
        await load_message_template_body(db, TEMPLATE_FORECAST_FULL)
        if db is not None
        else default_message_template_body(TEMPLATE_FORECAST_FULL)
    )
    if delivery_method == "vk":
        result = await asyncio.to_thread(send_full_forecast_to_vk_client, forecast_request, template_body=template_body)
        return {"ok": bool(result.get("ok")), "channel": "vk", "result": result}

    if delivery_method == "vk_bot":
        vk_result = await asyncio.to_thread(send_full_forecast_to_vk_client, forecast_request, template_body=template_body)
        if not vk_result.get("ok"):
            return {"ok": False, "channel": "vk", "result": vk_result}
        telegram_result = await asyncio.to_thread(send_full_forecast_to_client, forecast_request, template_body=template_body)
        if not telegram_result.get("ok"):
            logger.warning(
                "[ForecastDelivery] Telegram duplicate failed for request %s after VK success: %s",
                forecast_request.id,
                telegram_result.get("description", "unknown error"),
            )
        return {"ok": True, "channel": "vk_bot", "result": vk_result, "telegram_result": telegram_result}

    if delivery_method == "bot":
        result = await asyncio.to_thread(send_full_forecast_to_client, forecast_request, template_body=template_body)
        return {"ok": bool(result.get("ok")), "channel": "telegram", "result": result}

    if delivery_method == "web":
        return {"ok": True, "channel": "web", "result": {"ok": True}}

    return {"ok": False, "channel": delivery_method, "result": {"description": "Unknown delivery method"}}


def client_delivery_method(user: User) -> str:
    can_receive_telegram = user_can_receive_personal_telegram(user)
    can_receive_vk = user_can_receive_vk_messages(user)
    if can_receive_telegram and can_receive_vk:
        return "vk_bot"
    if can_receive_vk:
        return "vk"
    if can_receive_telegram:
        return "bot"
    return "web"


def _best_client_delivery_method(user: User) -> str:
    return client_delivery_method(user)


async def refreshed_client_delivery_method(db: AsyncSession, user: User) -> str:
    if getattr(user, "vk_user_id", None):
        await refresh_vk_delivery_status(db, user, refresh_group=False)
    return client_delivery_method(user)


def _status_for_delivery_method(delivery_method: str) -> str:
    return (
        FORECAST_STATUS_SENT
        if delivery_method in {"bot", "vk", "vk_bot", "web"}
        else FORECAST_STATUS_MANUAL_SENT
    )


def _ensure_full_forecast_ready(forecast_request: ForecastRequest) -> None:
    bet = forecast_request.bet
    outcome = str(bet.outcome or "").strip()
    if not outcome:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Заполните исход перед отправкой прогноза",
        )
    if not _fits_text_message(_build_full_forecast_message(forecast_request)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сократите текст полного прогноза до 4096 символов",
        )

def _full_forecast_ready_error(forecast_request: ForecastRequest) -> Optional[str]:
    try:
        _ensure_full_forecast_ready(forecast_request)
    except HTTPException as exc:
        return str(exc.detail)
    return None


def _build_take_admin_notification_content(forecast_request: ForecastRequest) -> tuple[str, dict]:
    user = forecast_request.user
    bet = forecast_request.bet
    is_paid_set = request_is_paid_set(forecast_request)
    balance = user_match_balance(user)
    delivery_blocked = forecast_request_requires_contact(forecast_request)
    warning = ""
    if delivery_blocked and not is_paid_set:
        warning = "\n\n⚠️ У клиента 0 матчей. Отправка прогноза недоступна до оплаты."

    if is_paid_set:
        message = (
            "<b>Клиент хочет взять платный набор</b>\n\n"
            f"Клиент: <b>{_html(_client_display(user))}</b>\n"
            f"Telegram ID: <code>{user.telegram_id}</code>\n\n"
            f"Набор: <b>{_html(bet.event_name or PAID_SET_PLACEHOLDER_EVENT_NAME)}</b>\n"
            f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
            f"Стоимость: <b>{_html(_format_rub_price(bet.price_stars))}</b>\n"
            f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
            f"Спорт: <b>{_html(bet.sport_type or 'не указан')}</b>\n\n"
            "Свяжитесь с клиентом лично и обсудите покупку."
        )
    else:
        message = (
            "<b>Клиент хочет взять закрытый прогноз</b>\n\n"
            f"Клиент: <b>{_html(_client_display(user))}</b>\n"
            f"Telegram ID: <code>{user.telegram_id}</code>\n"
            f"Баланс матчей: <b>{balance}</b>\n\n"
            f"Прогноз: <b>{_html(bet.event_name or PLACEHOLDER_EVENT_NAME)}</b>\n"
            f"Исход: <b>{_html(bet.outcome or 'уточняется')}</b>\n"
            f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
            f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
            f"Спорт: <b>{_html(bet.sport_type or 'не указан')}</b>"
            f"{warning}"
        )

    inline_keyboard = []
    if is_paid_set:
        dialog_buttons = _paid_set_admin_dialog_buttons(user)
        inline_keyboard.extend(
            [dialog_buttons[index:index + 2] for index in range(0, len(dialog_buttons), 2)]
        )
    elif not delivery_blocked:
        inline_keyboard.append([
            {
                "text": "Отправить прогноз",
                "callback_data": f"forecast:sales_send:{forecast_request.id}",
            }
        ])

    inline_keyboard.append([
        {
            "text": "Взял" if is_paid_set else "Клиент взял вручную",
            "callback_data": f"forecast:sales_manual:{forecast_request.id}",
        },
        {
            "text": "Отменить",
            "callback_data": f"forecast:sales_cancel:{forecast_request.id}",
        },
    ])

    reply_markup = {"inline_keyboard": inline_keyboard}
    return message, reply_markup


def build_sales_manager_notification_delivery(forecast_request: ForecastRequest) -> Optional[dict[str, object]]:
    sales_manager_id = settings.sales_manager_telegram_id
    if not sales_manager_id:
        return None

    message, reply_markup = _build_take_admin_notification_content(forecast_request)
    return {
        "method": "sendMessage",
        "payload": {
            "chat_id": sales_manager_id,
            "text": message,
            "parse_mode": "HTML",
            "reply_markup": reply_markup,
        },
    }


def _admin_group_chat_id() -> Optional[int]:
    chat_id = settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
    if chat_id is None:
        return None
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


def send_admin_group_full_forecast_copy(
    forecast_request: ForecastRequest,
    *,
    template_body: Optional[str] = None,
) -> dict:
    chat_id = _admin_group_chat_id()
    if not chat_id:
        return {"ok": False, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}
    if request_is_paid_set(forecast_request):
        return send_paid_set_sale_to_telegram_chat(forecast_request, chat_id=chat_id)
    return send_full_forecast_to_telegram_chat(
        forecast_request,
        chat_id=chat_id,
        template_body=template_body,
    )


def build_admin_group_forecast_response_delivery(
    forecast_request: ForecastRequest,
    *,
    action: str,
) -> Optional[dict[str, object]]:
    chat_id = _admin_group_chat_id()
    if not chat_id:
        return None

    if action == "take":
        message, reply_markup = _build_take_admin_notification_content(forecast_request)
        return {
            "method": "sendMessage",
            "payload": {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "reply_markup": reply_markup,
            },
        }

    if action != "decline":
        return None

    user = forecast_request.user
    bet = forecast_request.bet
    request_label = "платного набора" if request_is_paid_set(forecast_request) else "закрытого прогноза"
    message = (
        f"<b>Клиент отказался от {request_label}</b>\n\n"
        f"Клиент: <b>{_html(_client_display(user))}</b>\n"
        f"Telegram ID: <code>{user.telegram_id}</code>\n\n"
        f"Прогноз: <b>{_html(bet.event_name or PLACEHOLDER_EVENT_NAME)}</b>\n"
        f"Исход: <b>{_html(bet.outcome or 'уточняется')}</b>\n"
        f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
        f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
        f"Спорт: <b>{_html(bet.sport_type or 'не указан')}</b>"
    )
    return {
        "method": "sendMessage",
        "payload": {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
        },
    }


async def enqueue_admin_group_forecast_response_notification(
    db: AsyncSession,
    forecast_request: ForecastRequest,
    *,
    action: str,
) -> dict:
    delivery = build_admin_group_forecast_response_delivery(forecast_request, action=action)
    if not delivery:
        return {"ok": False, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}

    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=forecast_request.user_id,
        forecast_request_id=forecast_request.id,
        dedupe_key=f"forecast_request:{forecast_request.id}:admin_group:{action}",
        payload=delivery,
    )
    return {"ok": True, "queued": True}


async def count_client_bet_takers(db: AsyncSession, bet_id: UUID) -> int:
    result = await db.execute(
        select(func.count(func.distinct(user_bets.c.user_id)))
        .select_from(user_bets)
        .join(User, User.telegram_id == user_bets.c.user_id)
        .filter(
            user_bets.c.bet_id == bet_id,
            or_(User.role.is_(None), ~User.role.in_(STAFF_ROLES)),
        )
    )
    return max(0, int(result.scalar() or 0))


def _forecast_result_label(status_value: str) -> str:
    return {
        "win": "Выигрыш",
        "loss": "Проигрыш",
        "refund": "Возврат",
    }.get(str(status_value or "").strip().lower(), str(status_value or "не указан"))


def build_admin_group_forecast_result_delivery(
    *,
    bet: Bet,
    status_value: str,
    taker_count: int,
) -> Optional[dict[str, object]]:
    chat_id = _admin_group_chat_id()
    if not chat_id:
        return None

    forecast_text = _trim_text(getattr(bet, "description", None), 1200)
    forecast_block = f"\n\n<b>Прогноз:</b>\n{_html(forecast_text)}" if forecast_text else ""
    message = (
        "<b>Результат прогноза</b>\n\n"
        f"Матч: <b>{_html(getattr(bet, 'event_name', None) or PLACEHOLDER_EVENT_NAME)}</b>\n"
        f"Исход: <b>{_html(getattr(bet, 'outcome', None) or 'уточняется')}</b>\n"
        f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
        f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
        f"Спорт: <b>{_html(getattr(bet, 'sport_type', None) or 'не указан')}</b>\n"
        f"Результат: <b>{_html(_forecast_result_label(status_value))}</b>\n"
        f"Взяли: <b>{max(0, int(taker_count or 0))}</b>"
        f"{forecast_block}"
    )
    return {
        "method": "sendMessage",
        "payload": {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    }


async def enqueue_admin_group_forecast_result_notification(
    db: AsyncSession,
    *,
    bet: Bet,
    status_value: str,
    taker_count: int,
) -> dict:
    delivery = build_admin_group_forecast_result_delivery(
        bet=bet,
        status_value=status_value,
        taker_count=taker_count,
    )
    if not delivery:
        return {"ok": False, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}

    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=None,
        forecast_request_id=None,
        dedupe_key=f"bet:{bet.id}:admin_group:result:{status_value}",
        payload=delivery,
    )
    return {"ok": True, "queued": True}


def build_admin_group_forecast_stopped_delivery(
    *,
    bet: Bet,
    taker_count: int,
) -> Optional[dict[str, object]]:
    chat_id = _admin_group_chat_id()
    if not chat_id:
        return None

    is_paid_set = bet_is_paid_set(bet)
    forecast_type = "набор" if is_paid_set else "прогноз"
    placeholder_event_name = PAID_SET_PLACEHOLDER_EVENT_NAME if is_paid_set else PLACEHOLDER_EVENT_NAME
    forecast_text = _trim_text(getattr(bet, "description", None), 1200)
    forecast_block = f"\n\n<b>Прогноз:</b>\n{_html(forecast_text)}" if forecast_text else ""
    message = (
        "<b>Раздача остановлена</b>\n\n"
        f"Формат: <b>{forecast_type}</b>\n"
        f"Матч: <b>{_html(getattr(bet, 'event_name', None) or placeholder_event_name)}</b>\n"
        f"Исход: <b>{_html(getattr(bet, 'outcome', None) or 'уточняется')}</b>\n"
        f"КФ: <b>{_html(_coefficient_text(bet))}</b>\n"
        f"БК: <b>{_html(bookmaker_names_for_bet(bet))}</b>\n"
        f"Спорт: <b>{_html(getattr(bet, 'sport_type', None) or 'не указан')}</b>\n"
        f"Взяли: <b>{max(0, int(taker_count or 0))}</b>\n"
        "Результат ещё не проставлен."
        f"{forecast_block}"
    )
    return {
        "method": "sendMessage",
        "payload": {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
    }


async def enqueue_admin_group_forecast_stopped_notification(
    db: AsyncSession,
    *,
    bet: Bet,
    taker_count: int,
) -> dict:
    delivery = build_admin_group_forecast_stopped_delivery(
        bet=bet,
        taker_count=taker_count,
    )
    if not delivery:
        return {"ok": False, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}

    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=None,
        forecast_request_id=None,
        dedupe_key=f"bet:{bet.id}:admin_group:stopped",
        payload=delivery,
    )
    return {"ok": True, "queued": True}


def notify_sales_manager(forecast_request: ForecastRequest) -> dict:
    delivery = build_sales_manager_notification_delivery(forecast_request)
    if not delivery:
        return {"ok": False, "description": "SALES_MANAGER_TELEGRAM_ID or OWNER_TELEGRAM_ID is not configured"}
    return call_telegram_api(str(delivery["method"]), delivery["payload"])


async def enqueue_sales_manager_notification(
    db: AsyncSession,
    forecast_request: ForecastRequest,
) -> dict:
    delivery = build_sales_manager_notification_delivery(forecast_request)
    if not delivery:
        return {"ok": False, "description": "SALES_MANAGER_TELEGRAM_ID or OWNER_TELEGRAM_ID is not configured"}

    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=forecast_request.user_id,
        forecast_request_id=forecast_request.id,
        dedupe_key=f"forecast_request:{forecast_request.id}:sales_manager:interested",
        payload=delivery,
    )
    return {"ok": True, "queued": True}


async def enqueue_forecast_auto_delivery(
    db: AsyncSession,
    forecast_request: ForecastRequest,
    *,
    delivery_method: str = "auto",
) -> dict:
    if forecast_request_requires_contact(forecast_request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Отправка прогноза недоступна до оплаты",
        )
    await enqueue_delivery(
        db,
        channel=CHANNEL_FORECAST_AUTO_DELIVERY,
        user_id=forecast_request.user_id,
        forecast_request_id=forecast_request.id,
        dedupe_key=f"forecast_request:{forecast_request.id}:auto_delivery",
        payload={
            "request_id": str(forecast_request.id),
            "delivery_method": delivery_method,
        },
    )
    return {"ok": True, "queued": True}


def _admin_group_full_copy_dedupe_key(forecast_request: ForecastRequest) -> str:
    bet_id = str(getattr(forecast_request, "bet_id", None) or getattr(forecast_request.bet, "id", "") or "").strip()
    if bet_id:
        return f"bet:{bet_id}:admin_group:full_copy"
    return f"forecast_request:{forecast_request.id}:admin_group:full_copy"


async def enqueue_admin_group_full_forecast_copy(
    db: AsyncSession,
    forecast_request: ForecastRequest,
) -> dict:
    if not _admin_group_chat_id():
        return {"ok": False, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}

    await enqueue_delivery(
        db,
        channel=CHANNEL_FORECAST_ADMIN_FULL_COPY,
        user_id=forecast_request.user_id,
        forecast_request_id=forecast_request.id,
        dedupe_key=_admin_group_full_copy_dedupe_key(forecast_request),
        payload={
            "request_id": str(forecast_request.id),
        },
    )
    return {"ok": True, "queued": True}


async def enqueue_forecast_full_delivery(
    db: AsyncSession,
    forecast_request: ForecastRequest,
    *,
    delivery_method: str,
) -> dict:
    await enqueue_delivery(
        db,
        channel=CHANNEL_FORECAST_FULL_DELIVERY,
        user_id=forecast_request.user_id,
        forecast_request_id=forecast_request.id,
        dedupe_key=f"forecast_request:{forecast_request.id}:full_delivery:{delivery_method}",
        payload={
            "request_id": str(forecast_request.id),
            "delivery_method": delivery_method,
        },
    )
    await enqueue_admin_group_full_forecast_copy(db, forecast_request)
    return {"ok": True, "queued": True}


async def dispatch_forecast_full_delivery_from_outbox(
    request_id: UUID,
    *,
    delivery_method: str = "auto",
) -> dict:
    async with AsyncSessionLocal() as db:
        forecast_request = await load_forecast_request(db, request_id)
        if forecast_request.status not in DELIVERED_STATUSES:
            return {
                "ok": True,
                "skipped": True,
                "description": f"Forecast request is {forecast_request.status}, not delivered",
            }
        payload_delivery_method = (
            await refreshed_client_delivery_method(db, forecast_request.user)
            if delivery_method == "auto"
            else delivery_method
        )
        resolved_delivery_method = (
            payload_delivery_method
            if request_is_paid_set(forecast_request) and forecast_request.delivery_method == "manual"
            else forecast_request.delivery_method or payload_delivery_method
        )
        if resolved_delivery_method == "manual":
            return {"ok": True, "skipped": True, "description": "Manual delivery has no client channel"}

        send_report = await send_full_forecast_to_external_channels(
            forecast_request,
            delivery_method=resolved_delivery_method,
            db=db,
        )
        if not send_report.get("ok"):
            send_result = send_report.get("result") if isinstance(send_report.get("result"), dict) else {}
            if resolved_delivery_method in {"vk", "vk_bot"} and is_vk_message_permission_error(send_result):
                await mark_vk_messages_denied(db, forecast_request.user)
                await db.commit()
            description = send_result.get("description") if isinstance(send_result, dict) else None
            return {
                "ok": False,
                "description": description or f"{resolved_delivery_method} did not deliver the forecast",
            }

        if request_is_paid_set(forecast_request):
            await deliver_paid_set_sale_to_web_chat(db, forecast_request)
        else:
            template_body = await load_message_template_body(db, TEMPLATE_FORECAST_FULL)
            await deliver_full_forecast_to_web_chat(db, forecast_request, template_body=template_body)
        await db.commit()
        return {"ok": True, "delivery_method": resolved_delivery_method}


async def dispatch_admin_group_full_forecast_copy_from_outbox(request_id: UUID) -> dict:
    async with AsyncSessionLocal() as db:
        forecast_request = await load_forecast_request(db, request_id)
        if not _admin_group_chat_id():
            return {"ok": True, "skipped": True, "description": "TELEGRAM_ADMIN_GROUP_CHAT_ID is not configured"}
        template_body = (
            None
            if request_is_paid_set(forecast_request)
            else await load_message_template_body(db, TEMPLATE_FORECAST_FULL)
        )
        result = await asyncio.to_thread(
            send_admin_group_full_forecast_copy,
            forecast_request,
            template_body=template_body,
        )
        if not result.get("ok"):
            return {
                "ok": False,
                "description": result.get("description") or "Admin group full forecast copy was not delivered",
            }
        return {"ok": True}


async def notify_sales_manager_for_request(request_id: UUID) -> dict:
    async with AsyncSessionLocal() as db:
        forecast_request = await load_forecast_request(db, request_id)
        notify_result = await enqueue_sales_manager_notification(db, forecast_request)
        await db.commit()
        if not notify_result.get("ok"):
            logger.warning(
                "[ForecastDelivery] Sales manager notification was not queued for %s: %s",
                request_id,
                notify_result.get("description", "unknown error"),
            )
        return notify_result


def forecast_request_should_auto_deliver(forecast_request: ForecastRequest) -> bool:
    return bool(
        getattr(forecast_request.bet, "status", None) != "deleted"
        and not request_is_paid_set(forecast_request)
        and not forecast_request_requires_contact(forecast_request)
        and getattr(forecast_request.bet, "auto_send_on_interest", False)
        and _full_forecast_ready_error(forecast_request) is None
    )


async def auto_deliver_forecast_request_for_request(
    request_id: UUID,
    *,
    delivery_method: str = "auto",
) -> dict:
    async with AsyncSessionLocal() as db:
        forecast_request = await load_forecast_request(db, request_id)
        if not forecast_request_should_auto_deliver(forecast_request):
            return {"ok": False, "description": "Auto delivery is not ready"}
        resolved_delivery_method = (
            await refreshed_client_delivery_method(db, forecast_request.user)
            if delivery_method == "auto"
            else delivery_method
        )
        try:
            _, access_result = await deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=None,
                delivery_method=resolved_delivery_method,
                send_to_client=True,
                commit=True,
            )
            return {"ok": True, "already_recorded": access_result.already_recorded}
        except HTTPException as exc:
            logger.warning(
                "[ForecastDelivery] Auto delivery failed for request %s via %s: %s",
                request_id,
                resolved_delivery_method,
                exc.detail,
            )
            try:
                await notify_sales_manager_for_request(request_id)
            except Exception as notify_exc:
                logger.exception(
                    "[ForecastDelivery] Sales fallback failed for request %s: %s",
                    request_id,
                    notify_exc,
                )
            return {"ok": False, "description": str(exc.detail)}


async def set_forecast_request_interested(
    db: AsyncSession,
    *,
    request_id: UUID,
    actor_user_id: int,
    notify_sales_manager_now: bool = True,
    auto_delivery_method: str = "auto",
    auto_delivery_now: bool = True,
) -> tuple[ForecastRequest, str, bool]:
    forecast_request = await load_forecast_request(db, request_id)
    if forecast_request.user_id != actor_user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта кнопка привязана к другому клиенту",
        )
    forecast_request = await _lock_forecast_delivery_scope(db, forecast_request)
    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Прогноз уже оформлен.", False
    if forecast_request_is_inactive_for_client(forecast_request):
        return forecast_request, forecast_request_inactive_message(forecast_request), False
    try:
        _ensure_locked_private_forecast_is_deliverable(forecast_request, forecast_request.bet)
    except HTTPException as exc:
        if exc.detail in {
            FORECAST_INACTIVE_MESSAGE,
            PAID_SET_INACTIVE_MESSAGE,
            "Прогноз остановлен администратором",
        }:
            return forecast_request, forecast_request_inactive_message(forecast_request), False
        raise

    if forecast_request.status == FORECAST_STATUS_INTERESTED:
        return forecast_request, "Заявка уже отправлена Shamrai.", False
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        return forecast_request, "Заявка уже обрабатывается.", False
    if forecast_request.status == FORECAST_STATUS_DECLINED:
        return forecast_request, "Отказ уже учтен.", False

    if forecast_request_requires_contact(forecast_request):
        return forecast_request, FORECAST_CONTACT_REQUIRED_MESSAGE, False

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
        if forecast_request_is_inactive_for_client(latest_request):
            return latest_request, forecast_request_inactive_message(latest_request), False
        if latest_request.status == FORECAST_STATUS_INTERESTED:
            return latest_request, "Заявка уже отправлена Shamrai.", False
        if latest_request.status in DELIVERED_STATUSES:
            return latest_request, "Прогноз уже оформлен.", False
        if latest_request.status == FORECAST_STATUS_DECLINED:
            return latest_request, "Отказ уже учтен.", False
        if latest_request.status == FORECAST_STATUS_PROCESSING:
            return latest_request, "Заявка уже обрабатывается.", False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже изменила статус",
        )

    forecast_request.status = FORECAST_STATUS_INTERESTED
    forecast_request.responded_at = responded_at
    await enqueue_admin_group_forecast_response_notification(db, forecast_request, action="take")
    if forecast_request_should_auto_deliver(forecast_request):
        resolved_auto_delivery_method = (
            await refreshed_client_delivery_method(db, forecast_request.user)
            if auto_delivery_method == "auto"
            else auto_delivery_method
        )
        if not auto_delivery_now:
            await enqueue_forecast_auto_delivery(
                db,
                forecast_request,
                delivery_method=resolved_auto_delivery_method,
            )
            return forecast_request, "Принято. Готовим прогноз.", False
        try:
            forecast_request, access_result = await deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=None,
                delivery_method=resolved_auto_delivery_method,
                send_to_client=True,
                commit=True,
            )
            if access_result.already_recorded:
                return forecast_request, "Прогноз уже был отправлен.", False
            channel_name = (
                "VK и личные сообщения"
                if resolved_auto_delivery_method == "vk_bot"
                else "VK"
                if resolved_auto_delivery_method == "vk"
                else "личный веб-чат"
                if resolved_auto_delivery_method == "web"
                else "личные сообщения"
            )
            return forecast_request, f"Прогноз отправлен в {channel_name}.", False
        except HTTPException as exc:
            if exc.status_code < 500:
                raise
            logger.warning(
                "[ForecastDelivery] Auto-send failed for request %s, falling back to sales manager: %s",
                request_id,
                exc.detail,
            )

    if notify_sales_manager_now:
        notify_result = await enqueue_sales_manager_notification(db, forecast_request)
        if not notify_result.get("ok"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Shamrai не поставлен в очередь уведомлений: {notify_result.get('description', 'unknown error')}",
            )
        return forecast_request, "Заявка отправлена Shamrai.", False
    return forecast_request, "Заявка отправлена Shamrai.", True


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
    forecast_request = await _lock_forecast_delivery_scope(db, forecast_request)
    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Прогноз уже оформлен."
    if forecast_request_is_inactive_for_client(forecast_request):
        return forecast_request, forecast_request_inactive_message(forecast_request)
    try:
        _ensure_locked_private_forecast_is_deliverable(forecast_request, forecast_request.bet)
    except HTTPException as exc:
        if exc.detail in {
            FORECAST_INACTIVE_MESSAGE,
            PAID_SET_INACTIVE_MESSAGE,
            "Прогноз остановлен администратором",
        }:
            return forecast_request, forecast_request_inactive_message(forecast_request)
        raise

    if forecast_request.status == FORECAST_STATUS_INTERESTED:
        return forecast_request, "Заявка уже у Shamrai."
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        return forecast_request, "Заявка уже обрабатывается."
    if forecast_request.status == FORECAST_STATUS_DECLINED:
        return forecast_request, "Отказ уже учтен."

    forecast_request.status = FORECAST_STATUS_DECLINED
    forecast_request.responded_at = forecast_request.responded_at or _now()
    await enqueue_admin_group_forecast_response_notification(db, forecast_request, action="decline")
    if request_is_paid_set(forecast_request):
        return forecast_request, "Ок, набор не берем."
    return forecast_request, "Ок, не берем."


async def deliver_forecast_request(
    db: AsyncSession,
    *,
    forecast_request: ForecastRequest,
    handled_by: Optional[int],
    delivery_method: str,
    send_to_client: bool,
    commit: bool,
) -> tuple[ForecastRequest, UserBetAccessResult]:
    """Deliver a private forecast and own the transaction boundary when commit=True."""
    forecast_request = await _lock_forecast_delivery_scope(db, forecast_request)

    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, _already_taken_access_result(forecast_request)

    _ensure_locked_private_forecast_is_deliverable(forecast_request, forecast_request.bet)

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

    if forecast_request.status in {FORECAST_STATUS_DECLINED, FORECAST_STATUS_CANCELLED, FORECAST_STATUS_REMOVED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Эту заявку нельзя доставить: клиент отказался, заявка отменена или удалена",
        )
    if delivery_method == "auto":
        delivery_method = await refreshed_client_delivery_method(db, forecast_request.user)

    if request_is_paid_set(forecast_request):
        if send_to_client:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Набор продается вручную, отправка прогноза недоступна",
            )
        if delivery_method not in {"manual", "web", "bot", "vk", "vk_bot"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неизвестный способ обработки заявки",
            )
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
            if commit:
                await db.rollback()
            latest_request = await load_forecast_request(db, forecast_request.id)
            if latest_request.status in DELIVERED_STATUSES:
                return latest_request, _already_taken_access_result(latest_request)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Заявка уже обрабатывается или изменила статус",
            )

        access_result = await record_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            charge_match=False,
            free_access_type="manual_paid_set",
            note="Paid set marked as sold manually",
        )
        forecast_request.status = FORECAST_STATUS_MANUAL_SENT
        forecast_request.delivery_method = "manual"
        forecast_request.handled_by = handled_by
        forecast_request.delivered_at = forecast_request.delivered_at or _now()
        forecast_request.balance_before = access_result.balance_before
        forecast_request.balance_after = access_result.balance_after
        forecast_request.no_balance_warning = False
        delivery_method = await refreshed_client_delivery_method(db, forecast_request.user)
        await enqueue_forecast_full_delivery(
            db,
            forecast_request,
            delivery_method=delivery_method,
        )
        if commit:
            await db.commit()
        else:
            await db.flush()
        return forecast_request, access_result

    if forecast_request_requires_contact(forecast_request):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Отправка прогноза недоступна до оплаты",
        )

    if send_to_client:
        _ensure_full_forecast_ready(forecast_request)
        if delivery_method in {"vk", "vk_bot"}:
            await refresh_vk_delivery_status(db, forecast_request.user, refresh_group=False)
            if not user_can_receive_vk_messages(forecast_request.user):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="У клиента нет разрешения на доставку VK",
                )
        if delivery_method in {"bot", "vk_bot"}:
            if not is_personal_telegram_user_id(forecast_request.user_id):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="У клиента нет Telegram-чата для доставки ботом",
                )
        if delivery_method not in {"bot", "vk", "vk_bot", "web"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Неизвестный способ доставки прогноза",
            )

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
        if commit:
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

    try:
        access_result = await record_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            charge_match=True,
            allow_negative_balance=False,
            note=f"Private forecast activated via {delivery_method}",
        )
        forecast_request.status = _status_for_delivery_method(delivery_method)
        forecast_request.delivery_method = delivery_method
        forecast_request.handled_by = handled_by
        forecast_request.delivered_at = forecast_request.delivered_at or _now()
        forecast_request.balance_before = access_result.balance_before
        forecast_request.balance_after = access_result.balance_after
        forecast_request.no_balance_warning = access_result.no_balance_warning
        if send_to_client:
            await enqueue_forecast_full_delivery(
                db,
                forecast_request,
                delivery_method=delivery_method,
            )
        elif delivery_method == "manual" and _full_forecast_ready_error(forecast_request) is None:
            await enqueue_admin_group_full_forecast_copy(db, forecast_request)
        if commit:
            await db.commit()
        else:
            await db.flush()
        return forecast_request, access_result
    except Exception:
        if commit:
            await db.rollback()
        raise


async def cancel_forecast_request(
    db: AsyncSession,
    *,
    forecast_request: ForecastRequest,
    handled_by: Optional[int],
) -> tuple[ForecastRequest, str]:
    forecast_request = await _lock_forecast_delivery_scope(db, forecast_request)
    if forecast_request.status in DELIVERED_STATUSES:
        return forecast_request, "Заявка уже доставлена."
    if forecast_request.status == FORECAST_STATUS_CANCELLED:
        return forecast_request, "Заявка уже отменена."
    if forecast_request.status == FORECAST_STATUS_REMOVED:
        return forecast_request, "Заявка удалена администратором."
    try:
        _ensure_locked_private_forecast_is_deliverable(forecast_request, forecast_request.bet)
    except HTTPException as exc:
        if exc.detail in {
            FORECAST_INACTIVE_MESSAGE,
            PAID_SET_INACTIVE_MESSAGE,
            "Прогноз остановлен администратором",
        }:
            return forecast_request, "Заявка больше не активна."
        raise
    if forecast_request.status == FORECAST_STATUS_PROCESSING:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже обрабатывается, отмена недоступна",
        )

    forecast_request.status = FORECAST_STATUS_CANCELLED
    forecast_request.handled_by = handled_by
    return forecast_request, "Заявка отменена."


async def _actor_can_handle_sales_callback(db: AsyncSession, actor_user_id: int) -> bool:
    sales_manager_id = settings.sales_manager_telegram_id
    if sales_manager_id and actor_user_id == sales_manager_id:
        return True

    result = await db.execute(select(User.role).filter(User.telegram_id == actor_user_id))
    role = result.scalars().first()
    return is_admin_role(getattr(role, "role", role))


async def handle_sales_callback(
    db: AsyncSession,
    *,
    request_id: UUID,
    actor_user_id: int,
    action: str,
) -> tuple[ForecastRequest, str]:
    if not await _actor_can_handle_sales_callback(db, actor_user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Эта кнопка доступна только Shamrai",
        )

    forecast_request = await load_forecast_request(db, request_id)
    if action == "sales_send":
        if request_is_paid_set(forecast_request):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Набор продается вручную, отправка прогноза недоступна",
            )
        forecast_request, access_result = await deliver_forecast_request(
            db,
            forecast_request=forecast_request,
            handled_by=actor_user_id,
            delivery_method=await refreshed_client_delivery_method(db, forecast_request.user),
            send_to_client=True,
            commit=True,
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
            commit=True,
        )
        if access_result.already_recorded:
            return forecast_request, "Ручная отправка уже была отмечена."
        if request_is_paid_set(forecast_request):
            return forecast_request, "Клиент отмечен, набор отправляем в доступные каналы."
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
