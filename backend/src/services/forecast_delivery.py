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
from src.api.go import bookmaker_match_url_for_bet
from src.core.bookmaker_links import normalize_match_url as normalize_bookmaker_match_url
from src.core.config import settings
from src.core.telegram_delivery import is_personal_telegram_user_id, user_can_receive_personal_telegram
from src.core.telegram_text import append_contact_footer, bookmaker_custom_emoji
from src.models.database import AsyncSessionLocal
from src.models.models import Bet, ForecastRequest, User
from src.services.match_access import UserBetAccessResult, record_user_bet_access
from src.services.signals import deliver_personal_signal
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
FULL_FORECAST_LINKS_REQUIRED_MESSAGE = "Добавьте хотя бы одну ссылку по БК"
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
    "other": "/bookmakers/other.svg",
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


def _bookmakers_for_bet(bet: Bet) -> list:
    bookmakers = list(bet.bookmakers or [])
    if not bookmakers and bet.bookmaker:
        bookmakers = [bet.bookmaker]
    return bookmakers


def bookmaker_names_for_bet(bet: Bet) -> str:
    bookmakers = _bookmakers_for_bet(bet)
    return ", ".join(bookmaker.name for bookmaker in bookmakers) or "не указана"


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
    if not is_personal_telegram_user_id(forecast_request.user_id):
        return {"ok": False, "description": "Client does not have a personal Telegram chat"}
    return call_telegram_api("sendMessage", build_forecast_teaser_payload(forecast_request, teaser_text))


def _build_full_forecast_message(
    forecast_request: ForecastRequest,
    *,
    include_bookmaker: bool = True,
    include_bookmaker_links: bool = False,
) -> str:
    bet = forecast_request.bet
    lines = [
        f"Матч: <b>{_html(bet.event_name)}</b>",
        f"Исход: <b>{_html(bet.outcome or 'уточняется')}</b>",
        f"Коэффициент: <b>{_html(_coefficient_text(bet))}</b>",
    ]
    if include_bookmaker:
        lines.append(f"БК: {_bookmaker_labels_for_bet(bet)}")
    if bet.description:
        lines.append(_html(bet.description))
    if include_bookmaker_links:
        lines.extend(_bookmaker_link_lines(bet))

    return append_contact_footer(_join_forecast_lines(lines))


def send_full_forecast_to_client(forecast_request: ForecastRequest) -> dict:
    if not is_personal_telegram_user_id(forecast_request.user_id):
        return {"ok": False, "description": "Client does not have a personal Telegram chat"}

    bet = forecast_request.bet
    base_full_message = _build_full_forecast_message(forecast_request)
    full_message_with_links = _build_full_forecast_message(
        forecast_request,
        include_bookmaker_links=True,
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
                "chat_id": forecast_request.user_id,
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
                chat_id=forecast_request.user_id,
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
            "chat_id": forecast_request.user_id,
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
                chat_id=forecast_request.user_id,
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
                links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
                if links_result and not links_result.get("ok"):
                    return links_result
            return photo_result
        text_result = call_telegram_api("sendMessage", {
            "chat_id": forecast_request.user_id,
            "text": full_message,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
            **({"reply_markup": reply_markup} if reply_markup else {}),
        })
        if not text_result.get("ok"):
            return text_result
        if not reply_markup:
            links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
            if links_result and not links_result.get("ok"):
                return links_result
        return text_result

    text_result = call_telegram_api("sendMessage", {
        "chat_id": forecast_request.user_id,
        "text": full_message,
        "parse_mode": "HTML",
        "disable_web_page_preview": False,
        **({"reply_markup": reply_markup} if links_inline and reply_markup else {}),
    })
    if not text_result.get("ok"):
        return text_result
    if not links_inline:
        links_result = _send_bookmaker_links_message(forecast_request.user_id, bet)
        if links_result and not links_result.get("ok"):
            return links_result
    return text_result


def send_full_forecast_to_vk_client(forecast_request: ForecastRequest) -> dict:
    if not user_can_receive_vk_messages(forecast_request.user):
        return {"ok": False, "description": "Client has not allowed VK messages"}

    bet = forecast_request.bet
    full_message = html_to_vk_text(_build_full_forecast_message(forecast_request))
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


def build_web_full_forecast_text(forecast_request: ForecastRequest) -> str:
    return html_to_vk_text(_build_full_forecast_message(forecast_request, include_bookmaker=False))


def build_web_forecast_signal_data(
    forecast_request: ForecastRequest,
    *,
    status_value: Optional[str] = None,
) -> dict[str, object]:
    bet = forecast_request.bet
    return {
        "message_html": _build_full_forecast_message(forecast_request, include_bookmaker=False),
        "message_text": build_web_full_forecast_text(forecast_request),
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
) -> dict[str, object]:
    bet = forecast_request.bet
    return {
        "message_html": build_teaser_message(forecast_request, teaser_text),
        "message_text": html_to_vk_text(build_teaser_message(forecast_request, teaser_text)),
        "coupon_image_url": bet.coupon_image_url,
        "bookmakers": _bookmaker_web_items(bet),
        "event_name": bet.event_name,
        "coefficient": _coefficient_text(bet),
        "sport_type": bet.sport_type,
        "forecast_request_id": str(forecast_request.id),
        "forecast_status": forecast_request.status,
        "bet_id": str(getattr(forecast_request, "bet_id", None) or getattr(bet, "id", "")),
        "actions": ["take", "decline"],
    }


async def deliver_full_forecast_to_web_chat(
    db: AsyncSession,
    forecast_request: ForecastRequest,
) -> None:
    await deliver_personal_signal(
        db,
        user=forecast_request.user,
        text=build_web_full_forecast_text(forecast_request),
        signal_type="forecast_full",
        data=build_web_forecast_signal_data(forecast_request),
        send_telegram=False,
        send_web_push=True,
    )


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
    if not _fits_text_message(_build_full_forecast_message(forecast_request)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сократите текст полного прогноза до 4096 символов",
        )
    if not _bookmaker_link_targets(bet):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=FULL_FORECAST_LINKS_REQUIRED_MESSAGE,
        )


def _full_forecast_ready_error(forecast_request: ForecastRequest) -> Optional[str]:
    try:
        _ensure_full_forecast_ready(forecast_request)
    except HTTPException as exc:
        return str(exc.detail)
    return None


def notify_sales_manager(forecast_request: ForecastRequest) -> dict:
    sales_manager_id = settings.sales_manager_telegram_id
    if not sales_manager_id:
        return {"ok": False, "description": "SALES_MANAGER_TELEGRAM_ID or OWNER_TELEGRAM_ID is not configured"}

    user = forecast_request.user
    bet = forecast_request.bet
    balance = user_match_balance(user)
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


def forecast_request_should_auto_deliver(forecast_request: ForecastRequest) -> bool:
    return bool(
        getattr(forecast_request.bet, "status", None) != "deleted"
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
    if getattr(forecast_request.bet, "status", None) == "deleted":
        return forecast_request, "Прогноз остановлен администратором.", False

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
    if forecast_request.status == FORECAST_STATUS_REMOVED:
        return forecast_request, "Заявка удалена администратором.", False

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
        if latest_request.status == FORECAST_STATUS_REMOVED:
            return latest_request, "Заявка удалена администратором.", False
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Заявка уже изменила статус",
        )

    forecast_request.status = FORECAST_STATUS_INTERESTED
    forecast_request.responded_at = responded_at
    if forecast_request_should_auto_deliver(forecast_request):
        resolved_auto_delivery_method = (
            await refreshed_client_delivery_method(db, forecast_request.user)
            if auto_delivery_method == "auto"
            else auto_delivery_method
        )
        if not auto_delivery_now:
            return forecast_request, "Принято. Готовим прогноз.", False
        try:
            forecast_request, access_result = await deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=None,
                delivery_method=resolved_auto_delivery_method,
                send_to_client=True,
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
    if getattr(forecast_request.bet, "status", None) == "deleted":
        return forecast_request, "Прогноз остановлен администратором."

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
    if forecast_request.status == FORECAST_STATUS_REMOVED:
        return forecast_request, "Заявка удалена администратором."

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
    if delivery_method == "auto":
        delivery_method = await refreshed_client_delivery_method(db, forecast_request.user)

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

    if forecast_request.status in {FORECAST_STATUS_DECLINED, FORECAST_STATUS_CANCELLED, FORECAST_STATUS_REMOVED}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Эту заявку нельзя доставить: клиент отказался, заявка отменена или удалена",
        )
    if getattr(forecast_request.bet, "status", None) == "deleted":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Прогноз остановлен администратором",
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
            if delivery_method == "vk":
                send_result = await asyncio.to_thread(send_full_forecast_to_vk_client, forecast_request)
                delivery_label = "VK"
            elif delivery_method == "vk_bot":
                send_result = await asyncio.to_thread(send_full_forecast_to_vk_client, forecast_request)
                delivery_label = "VK"
                if send_result.get("ok"):
                    telegram_result = await asyncio.to_thread(send_full_forecast_to_client, forecast_request)
                    if not telegram_result.get("ok"):
                        logger.warning(
                            "[ForecastDelivery] Telegram duplicate failed for request %s after VK success: %s",
                            forecast_request.id,
                            telegram_result.get("description", "unknown error"),
                        )
            elif delivery_method == "bot":
                send_result = await asyncio.to_thread(send_full_forecast_to_client, forecast_request)
                delivery_label = "Telegram"
            else:
                send_result = {"ok": True}
                delivery_label = "Web"
            if not send_result.get("ok"):
                if delivery_method in {"vk", "vk_bot"} and is_vk_message_permission_error(send_result):
                    await mark_vk_messages_denied(db, forecast_request.user)
                    await db.commit()
                    raise HTTPException(
                        status_code=status.HTTP_502_BAD_GATEWAY,
                        detail=(
                            "VK не разрешает отправлять этому клиенту личные сообщения. "
                            "Попросите клиента открыть диалог VK и нажать проверку доступа."
                        ),
                    )
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"{delivery_label} не доставил прогноз клиенту: {send_result.get('description', 'unknown error')}",
                )

        access_result = await record_user_bet_access(
            db,
            user=forecast_request.user,
            bet=forecast_request.bet,
            charge_match=True,
            allow_negative_balance=True,
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
            await deliver_full_forecast_to_web_chat(db, forecast_request)
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
    if forecast_request.status == FORECAST_STATUS_REMOVED:
        return forecast_request, "Заявка удалена администратором."
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
            delivery_method=await refreshed_client_delivery_method(db, forecast_request.user),
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
