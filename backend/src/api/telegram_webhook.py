import asyncio
import html
import json
import time
from typing import Optional
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import AsyncSessionLocal
from src.models.models import User
from src.core.background_tasks import create_logged_task
from src.core.config import settings
from src.core.message_templates import TEMPLATE_TELEGRAM_WELCOME, render_message_template
from src.core.security import verify_telegram_webhook_secret
from src.core.telegram_emoji_catalog import EmojiCatalogSnapshot, replace_emoji_catalog
from src.core.telegram_text import (
    SHAMRAI_CONTACT_USERNAME,
    contact_footer,
    custom_emoji,
    write_emoji,
)
from src.api.payments import process_telegram_payment_update
from src.services.forecast_delivery import (
    FORECAST_CONTACT_DRAFT_TEXT,
    forecast_request_is_inactive_for_client,
    handle_sales_callback,
    notify_sales_manager_for_request,
    set_forecast_request_declined,
    set_forecast_request_interested,
)
from src.services.telegram_auth import confirm_telegram_bot_auth_session, parse_telegram_auth_start_param
from src.services.telegram_bot import call_telegram_api
from src.services.telegram_emoji_catalog import (
    EmojiAssignment,
    extract_emoji_assignments,
    load_telegram_emoji_catalog,
    save_emoji_assignments,
)
from src.services.telegram_custom_emoji_library import (
    extract_custom_emoji_library_items,
    save_custom_emoji_library_items,
)
from src.services.telegram_custom_emoji_previews import warm_custom_emoji_previews
from src.services.telegram_manual_post import extract_replied_post, publish_telegram_post
from src.services.flat_subscriptions import (
    FlatSubscriptionState,
    clear_forecast_stake_input,
    lookup_forecast_stake_input,
    get_open_flat_subscription,
    parse_stake_amount,
    start_forecast_stake_input,
)
import logging

router = APIRouter(prefix="/telegram", tags=["Telegram Webhook"])
logger = logging.getLogger("uvicorn")

SHAMRAI_BUTTON_TEXT = "Открыть Shamrai"
EMOJI_ID_COMMANDS = {
    "/emoji_ids": "TELEGRAM_CUSTOM_EMOJI_IDS",
    "/emoji_ids_bk": "TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS",
    "/emoji_ids_sport": "TELEGRAM_SPORT_CUSTOM_EMOJI_IDS",
}
EMOJI_CATALOG_COMMANDS = {"/emoji_add", "/emoji_list", "/emoji_help"}
MANUAL_POST_COMMAND = "/publish"


def _telegram_update_type(update: dict) -> str:
    if update.get("callback_query"):
        return "callback_query"
    if update.get("pre_checkout_query"):
        return "pre_checkout_query"
    message = update.get("message") or {}
    if message.get("successful_payment"):
        return "successful_payment"
    if message:
        return "message"
    return "unknown"


def _telegram_update_chat_id(update: dict) -> Optional[int]:
    callback_query = update.get("callback_query") or {}
    callback_message = callback_query.get("message") or {}
    callback_chat = callback_message.get("chat") or {}
    if callback_chat.get("id") is not None:
        return callback_chat.get("id")

    message = update.get("message") or {}
    chat = message.get("chat") or {}
    if chat.get("id") is not None:
        return chat.get("id")

    pre_checkout_query = update.get("pre_checkout_query") or {}
    user = pre_checkout_query.get("from") or {}
    return user.get("id")


async def _mark_private_telegram_chat_joined(db: AsyncSession, message: dict) -> bool:
    chat = message.get("chat") or {}
    if chat.get("type") != "private":
        return False

    sender = message.get("from") or {}
    candidate_id = sender.get("id") or chat.get("id")
    try:
        telegram_id = int(candidate_id)
    except (TypeError, ValueError):
        return False
    if telegram_id <= 0:
        return False

    result = await db.execute(select(User).filter(User.telegram_id == telegram_id))
    user = result.scalars().first()
    if not user or user.tg_chat_joined:
        return False

    user.tg_chat_joined = True
    await db.commit()
    logger.info("[Webhook] marked telegram private chat ready for user_id=%s", telegram_id)
    return True


def _telegram_response_label(response: dict) -> str:
    if not isinstance(response, dict):
        return type(response).__name__
    return str(response.get("method") or response.get("status") or "dict")


def _shamrai_web_app_button(web_app_url: str) -> dict:
    return {
        "text": SHAMRAI_BUTTON_TEXT,
        "web_app": {"url": web_app_url},
    }


def _is_start_command(text: str) -> bool:
    first_token = (text.strip().split(None, 1)[0] if text.strip() else "").lower()
    return first_token.split("@", 1)[0] == "/start"


def _start_command_param(text: str) -> str:
    parts = (text or "").strip().split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


def _start_web_app_url(request_base_url: Optional[str]) -> str:
    return settings.FRONTEND_BASE_URL.strip() or request_base_url or settings.API_BASE_URL.strip()


def _build_start_response(message: dict, request_base_url: Optional[str] = None) -> dict:
    chat = message.get("chat") or {}
    user = message.get("from") or {}
    web_app_url = _start_web_app_url(request_base_url)
    welcome_text = (
        f"👋 <b>Привет, {user.get('first_name', 'друг')}!</b>\n\n"
        "Добро пожаловать в <b>ШАМРАЙ | ОШИБКИ БК</b>.\n\n"
        "📊 Прогнозы, ошибки БК и умные уведомления уже внутри приложения.\n\n"
        f"{contact_footer()}\n\n"
        "👇 Нажмите кнопку ниже, чтобы открыть Shamrai."
    )
    return {
        "method": "sendMessage",
        "chat_id": chat.get("id"),
        "text": welcome_text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    _shamrai_web_app_button(web_app_url)
                ]
            ]
        },
    }


async def _build_start_response_from_template(
    db: AsyncSession,
    message: dict,
    request_base_url: Optional[str] = None,
) -> dict:
    response = _build_start_response(message, request_base_url=request_base_url)
    user = message.get("from") or {}
    response["text"] = await render_message_template(
        db,
        TEMPLATE_TELEGRAM_WELCOME,
        {
            "first_name": user.get("first_name") or "друг",
            "contact_footer": contact_footer(),
        },
        safe_keys={"contact_footer"},
    )
    return response


def _build_auth_response(message: dict, confirmed: bool, request_base_url: Optional[str] = None) -> dict:
    chat = message.get("chat") or {}
    web_app_url = _start_web_app_url(request_base_url)
    text = (
        "✅ <b>Вход подтвержден.</b>\n\n"
        "Вернитесь на сайт Shamrai: кабинет откроется автоматически."
        if confirmed
        else (
            "Ссылка для входа устарела или уже использована.\n\n"
            "Откройте сайт Shamrai и нажмите «Войти через Telegram» еще раз."
        )
    )
    return {
        "method": "sendMessage",
        "chat_id": chat.get("id"),
        "text": text,
        "parse_mode": "HTML",
        "reply_markup": {
            "inline_keyboard": [
                [
                    _shamrai_web_app_button(web_app_url)
                ]
            ]
        },
    }


def _write_emoji() -> str:
    return write_emoji()


def _utf16_offset_to_index(text: str, offset: int) -> int:
    prefix_bytes = text.encode("utf-16-le")[: max(offset, 0) * 2]
    return len(prefix_bytes.decode("utf-16-le", errors="ignore"))


def _emoji_ids_command(text: str) -> Optional[tuple[str, str]]:
    first_token = (text.strip().split(None, 1)[0] if text.strip() else "").lower()
    command = first_token.split("@", 1)[0]
    env_name = EMOJI_ID_COMMANDS.get(command)
    return (command, env_name) if env_name else None


def _can_use_emoji_id_command(user_id: Optional[int]) -> bool:
    if not user_id:
        return False
    allowed_ids = {
        int(value)
        for value in (settings.OWNER_TELEGRAM_ID, settings.sales_manager_telegram_id)
        if value
    }
    return bool(allowed_ids) and int(user_id) in allowed_ids


def _is_manual_post_command(text: str) -> bool:
    first_token = (text.strip().split(None, 1)[0] if text.strip() else "").lower()
    return first_token.split("@", 1)[0] == MANUAL_POST_COMMAND


async def _build_manual_post_response(
    message: dict,
    user_id: Optional[int],
    db: AsyncSession,
) -> dict:
    if not _can_use_emoji_id_command(user_id):
        return {
            "method": "sendMessage",
            "text": "Эта команда доступна только владельцу или менеджеру Shamrai.",
        }

    try:
        actor_user_id = int(user_id)
        draft = extract_replied_post(message, actor_user_id=actor_user_id)
        report = await publish_telegram_post(
            db,
            draft,
            actor_user_id=actor_user_id,
        )
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        return {
            "method": "sendMessage",
            "text": html.escape(str(exc)),
            "parse_mode": "HTML",
        }
    except Exception:
        await db.rollback()
        logger.exception(
            "telegram_manual_post_publish_failed",
            extra={
                "event": "telegram_manual_post_publish_failed",
                "actor_user_id": user_id,
            },
        )
        return {
            "method": "sendMessage",
            "text": "Не удалось опубликовать пост. Попробуйте еще раз.",
        }

    status_line = (
        "Этот пост уже был опубликован."
        if report.already_published
        else "Пост добавлен в Ленту и поставлен в очередь Telegram."
    )
    return {
        "method": "sendMessage",
        "text": (
            f"✅ {status_line}\n"
            f"Получателей в личных сообщениях: {report.telegram_recipients}\n"
            f"Custom emoji в исходном посте: {report.custom_emoji_count}"
        ),
    }


def _label_for_custom_emoji(text: str, entity: dict, index: int) -> str:
    start_index = _utf16_offset_to_index(text, int(entity.get("offset") or 0))
    line_start = text.rfind("\n", 0, start_index) + 1
    prefix = text[line_start:start_index].strip()
    prefix = prefix.strip(" \t:-=—–")
    if prefix.startswith("/"):
        return f"emoji_{index}"
    for marker in ("bk:", "bookmaker:", "sport:", "спорт:", "бк:"):
        if prefix.lower().startswith(marker):
            prefix = prefix[len(marker):].strip()
            break
    return prefix or f"emoji_{index}"


def _build_emoji_ids_response(message: dict, user_id: Optional[int]) -> Optional[dict]:
    text = message.get("text") or message.get("caption") or ""
    command_info = _emoji_ids_command(text)
    if not command_info:
        return None

    if not _can_use_emoji_id_command(user_id):
        return {
            "method": "sendMessage",
            "text": "Команда доступна только администратору.",
        }

    _, env_name = command_info
    entities = list(message.get("entities") or message.get("caption_entities") or [])
    custom_entities = [
        entity for entity in entities
        if entity.get("type") == "custom_emoji" and entity.get("custom_emoji_id")
    ]
    if not custom_entities:
        return {
            "method": "sendMessage",
            "text": (
                "Вставьте кастомные эмодзи в строки после команды.\n\n"
                "Пример:\n"
                "/emoji_ids_bk\n"
                "fonbet <логотип>\n"
                "pari <логотип>"
            ),
        }

    emoji_map = {
        _label_for_custom_emoji(text, entity, index): str(entity["custom_emoji_id"])
        for index, entity in enumerate(custom_entities, start=1)
    }
    env_value = json.dumps(emoji_map, ensure_ascii=False, separators=(",", ":"))
    logger.info("[Webhook] Custom emoji IDs extracted for %s: %s", env_name, env_value)
    return {
        "method": "sendMessage",
        "text": (
            f"<b>{html.escape(env_name)}</b>\n\n"
            f"<code>{html.escape(env_value)}</code>"
        ),
        "parse_mode": "HTML",
    }


def _emoji_catalog_command(text: str) -> Optional[str]:
    first_token = (str(text or "").strip().split(None, 1)[0] if str(text or "").strip() else "").lower()
    command = first_token.split("@", 1)[0]
    return command if command in EMOJI_CATALOG_COMMANDS else None


def _emoji_slot_label(assignment: EmojiAssignment) -> str:
    return assignment.scope if assignment.key is None else f"{assignment.scope}:{assignment.key}"


def _emoji_preview_fallback(scope: str, key: Optional[str]) -> str:
    if scope == "write":
        return "✍️"
    if scope == "shamrai":
        return "⚔️"
    if scope == "bookmaker":
        return "🏦"
    if scope == "sport":
        return "🏟️"
    fallbacks = {
        "forecast": "🔒",
        "match": "🏟️",
        "outcome": "🎯",
        "coefficient": "📊",
        "bookmaker": "🏦",
        "sport": "🏟️",
        "price": "💰",
        "link": "🔗",
        "general": "📢",
        "bet_promo": "🎯",
        "flash_sale": "⚡",
        "urgent": "🚨",
    }
    return fallbacks.get(str(key or "").strip().lower(), "✨")


def _emoji_catalog_help_text() -> str:
    return (
        "<b>Добавление custom emoji</b>\n\n"
        "Отправьте одним сообщением:\n"
        "<code>/emoji_add\n"
        "shamrai &lt;эмодзи&gt;\n"
        "write &lt;эмодзи&gt;\n"
        "bk:winline &lt;эмодзи&gt;\n"
        "sport:football &lt;эмодзи&gt;\n"
        "decor:forecast &lt;эмодзи&gt;</code>\n\n"
        "Или ответьте командой <code>/emoji_add decor:forecast</code> "
        "на одно custom emoji / custom-emoji sticker.\n\n"
        "Декор прогнозов: <code>forecast, match, outcome, coefficient, "
        "bookmaker, sport, price, link</code>.\n"
        "Декор постов: <code>general, bet_promo, flash_sale, urgent</code>.\n\n"
        "Посмотреть набор: /emoji_list"
    )


def _catalog_preview_lines(snapshot: EmojiCatalogSnapshot) -> list[str]:
    lines: list[str] = []
    scalar_values = (
        ("shamrai", None, snapshot.shamrai_id),
        ("write", None, snapshot.write_id),
    )
    for scope, key, custom_id in scalar_values:
        if custom_id:
            fallback = _emoji_preview_fallback(scope, key)
            lines.append(f"{custom_emoji(custom_id, fallback)} <code>{scope}</code>")
    for scope in ("bookmaker", "sport", "decor"):
        for key, custom_id in sorted(snapshot.mapping(scope).items()):
            fallback = _emoji_preview_fallback(scope, key)
            lines.append(f"{custom_emoji(custom_id, fallback)} <code>{scope}:{html.escape(key)}</code>")
    return lines[:80]


async def _build_emoji_catalog_response(
    message: dict,
    user_id: Optional[int],
    db: AsyncSession,
) -> Optional[dict]:
    text = str(message.get("text") or message.get("caption") or "")
    command = _emoji_catalog_command(text)
    if not command:
        return None
    if not _can_use_emoji_id_command(user_id):
        return {
            "method": "sendMessage",
            "text": "Команда доступна только владельцу или менеджеру бота.",
        }
    if command == "/emoji_help":
        return {
            "method": "sendMessage",
            "text": _emoji_catalog_help_text(),
            "parse_mode": "HTML",
        }
    if command == "/emoji_list":
        snapshot = await load_telegram_emoji_catalog(db)
        lines = _catalog_preview_lines(snapshot)
        body = "\n".join(lines) if lines else "Пока не добавлено ни одного custom emoji."
        return {
            "method": "sendMessage",
            "text": f"<b>Набор Shamrai</b>\n\n{body}\n\nДобавить: /emoji_help",
            "parse_mode": "HTML",
        }

    try:
        assignments = extract_emoji_assignments(message)
        snapshot = await save_emoji_assignments(db, assignments)
        await db.commit()
        replace_emoji_catalog(snapshot)
    except ValueError as exc:
        await db.rollback()
        return {
            "method": "sendMessage",
            "text": f"Не удалось добавить эмодзи: {html.escape(str(exc))}\n\n/emoji_help",
            "parse_mode": "HTML",
        }
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "telegram_emoji_catalog_save_failed",
            extra={
                "event": "telegram_emoji_catalog_save_failed",
                "error_type": type(exc).__name__,
            },
        )
        return {
            "method": "sendMessage",
            "text": "Не удалось сохранить набор. Попробуйте ещё раз.",
        }

    saved_lines = [
        (
            f"{custom_emoji(item.custom_emoji_id, _emoji_preview_fallback(item.scope, item.key))} "
            f"<code>{html.escape(_emoji_slot_label(item))}</code>"
        )
        for item in assignments
    ]
    return {
        "method": "sendMessage",
        "text": (
            f"<b>Сохранено: {len(assignments)}</b>\n\n"
            f"{chr(10).join(saved_lines)}\n\n"
            f"Всего слотов: "
            f"{int(bool(snapshot.shamrai_id)) + int(bool(snapshot.write_id)) + len(snapshot.bookmaker_items) + len(snapshot.sport_items) + len(snapshot.decor_items)}"
        ),
        "parse_mode": "HTML",
    }


def _message_has_custom_emoji(message: dict) -> bool:
    entities = message.get("entities") or message.get("caption_entities") or []
    if any(
        isinstance(entity, dict)
        and entity.get("type") == "custom_emoji"
        and entity.get("custom_emoji_id")
        for entity in entities
    ):
        return True
    return bool((message.get("sticker") or {}).get("custom_emoji_id"))


async def _build_custom_emoji_library_upload_response(
    message: dict,
    user_id: Optional[int],
    db: AsyncSession,
) -> Optional[dict]:
    if not _message_has_custom_emoji(message):
        return None
    if not _can_use_emoji_id_command(user_id):
        return {
            "method": "sendMessage",
            "text": "Добавлять custom emoji в библиотеку может только владелец или менеджер бота.",
        }
    try:
        incoming = extract_custom_emoji_library_items(message)
        library = await save_custom_emoji_library_items(db, incoming)
        await db.commit()
        _run_background(
            asyncio.to_thread(
                warm_custom_emoji_previews,
                [item.custom_emoji_id for item in incoming],
            )
        )
    except ValueError as exc:
        await db.rollback()
        return {
            "method": "sendMessage",
            "text": f"Не удалось добавить эмодзи: {html.escape(str(exc))}",
            "parse_mode": "HTML",
        }
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "telegram_custom_emoji_library_save_failed",
            extra={
                "event": "telegram_custom_emoji_library_save_failed",
                "error_type": type(exc).__name__,
            },
        )
        return {
            "method": "sendMessage",
            "text": "Не удалось сохранить эмодзи. Попробуйте ещё раз.",
        }
    return {
        "method": "sendMessage",
        "text": (
            f"Добавлено: {len(incoming)}. Всего в библиотеке: {len(library)}.\n\n"
            "Теперь эмодзи доступны в приложении: Панель → Лента → Текст."
        ),
    }


def _answer_callback_query(callback_query_id: Optional[str], text: str, show_alert: bool = False) -> dict:
    if not callback_query_id:
        return {"status": "ok"}
    return {
        "method": "answerCallbackQuery",
        "callback_query_id": callback_query_id,
        "text": text[:190],
        "show_alert": show_alert,
    }


def _forecast_contact_required(message: str, forecast_status: Optional[str]) -> bool:
    return forecast_status == "announced" and FORECAST_CONTACT_DRAFT_TEXT in str(message or "")


def _telegram_forecast_contact_url() -> str:
    username = SHAMRAI_CONTACT_USERNAME.strip().lstrip("@") or "Shamrai_Osnova"
    return f"https://t.me/{username}?text={quote(FORECAST_CONTACT_DRAFT_TEXT)}"


def _send_forecast_contact_cta(chat_id: int, message: str) -> dict:
    return call_telegram_api(
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": message,
            "reply_markup": {
                "inline_keyboard": [
                    [
                        {
                            "text": "Написать Shamrai",
                            "url": _telegram_forecast_contact_url(),
                        }
                    ]
                ]
            },
        },
    )


def _run_background(coro) -> None:
    create_logged_task(coro, logger=logger, failure_message="[Webhook] Background task failed")


def _clear_forecast_client_message(callback_query: dict) -> None:
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    message_id = message.get("message_id")
    if not chat_id or not message_id:
        return

    delete_result = call_telegram_api(
        "deleteMessage",
        {
            "chat_id": chat_id,
            "message_id": message_id,
        },
    )
    if delete_result.get("ok"):
        return

    edit_result = call_telegram_api(
        "editMessageReplyMarkup",
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        },
    )
    if not edit_result.get("ok"):
        logger.warning(
            "[Webhook] Failed to clear forecast client buttons for message %s/%s: %s",
            chat_id,
            message_id,
            edit_result.get("description") or delete_result.get("description"),
        )


async def _process_sales_send_callback(
    callback_query: dict,
    *,
    request_id: UUID,
    actor_user_id: int,
) -> None:
    chat = (callback_query.get("message") or {}).get("chat") or {}
    target_chat_id = chat.get("id") or actor_user_id
    try:
        async with AsyncSessionLocal() as db:
            try:
                _, message = await handle_sales_callback(
                    db,
                    request_id=request_id,
                    actor_user_id=actor_user_id,
                    action="sales_send",
                )
                await db.commit()
            except HTTPException as exc:
                await db.rollback()
                message = str(exc.detail)
            except Exception:
                await db.rollback()
                raise
    except HTTPException as exc:
        message = str(exc.detail)
    except Exception as exc:
        logger.exception("[Webhook] Sales send callback failed: %s", exc)
        message = "Не удалось отправить прогноз клиенту"

    await asyncio.to_thread(
        call_telegram_api,
        "sendMessage",
        {
            "chat_id": target_chat_id,
            "text": message,
        },
    )


async def _handle_forecast_callback(callback_query: dict, db: AsyncSession) -> dict:
    callback_id = callback_query.get("id")
    data = callback_query.get("data") or ""
    if not data.startswith("forecast:"):
        return {"status": "ok"}

    actor_user_id = callback_query.get("from", {}).get("id")
    if not actor_user_id:
        return _answer_callback_query(callback_id, "Не удалось определить пользователя", True)

    parts = data.split(":")
    if len(parts) != 3:
        return _answer_callback_query(callback_id, "Некорректная кнопка", True)

    action = parts[1]
    should_notify_sales = False
    forecast_request = None
    try:
        request_id = UUID(parts[2])
    except ValueError:
        return _answer_callback_query(callback_id, "Некорректная заявка", True)

    if action == "sales_send":
        _run_background(
            _process_sales_send_callback(
                callback_query,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
            )
        )
        return _answer_callback_query(callback_id, "Принято, отправляем прогноз.")

    if action == "take":
        actor_user = (
            await db.get(User, int(actor_user_id))
            if callable(getattr(db, "get", None))
            else None
        )
        legacy_access_active = bool(
            actor_user
            and (
                max(
                    int(getattr(actor_user, "purchased_bets_balance", 0) or 0),
                    int(getattr(actor_user, "matches_remaining", 0) or 0),
                ) > 0
                or bool(getattr(actor_user, "guarantee_active", False))
            )
        )
        supports_flat_lookup = callable(getattr(db, "execute", None))
        if actor_user is None and not supports_flat_lookup:
            legacy_access_active = True
        flat_subscription = (
            await get_open_flat_subscription(db, int(actor_user_id))
            if not legacy_access_active and supports_flat_lookup
            else None
        )
        if flat_subscription is not None and not legacy_access_active:
            if flat_subscription.status != FlatSubscriptionState.ACTIVE.value:
                return _answer_callback_query(
                    callback_id,
                    "Флетовый абонемент пока не принимает новые ставки",
                    True,
                )
            await start_forecast_stake_input(
                db,
                channel="telegram",
                user_id=int(actor_user_id),
                forecast_request_id=request_id,
            )
            await db.commit()
            chat_id = (
                ((callback_query.get("message") or {}).get("chat") or {}).get("id")
                or actor_user_id
            )
            await asyncio.to_thread(
                call_telegram_api,
                "sendMessage",
                {
                    "chat_id": int(chat_id),
                    "text": (
                        f"Размер вашего флета: {flat_subscription.flat_amount_rub} ₽.\n"
                        "Напишите сумму фактической ставки, например 5000 или 5 тыс.\n"
                        "Для отмены отправьте /cancel."
                    ),
                },
            )
            return _answer_callback_query(callback_id, "Жду сумму ставки")

    try:
        if action == "take":
            forecast_request, message, should_notify_sales = await set_forecast_request_interested(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
                notify_sales_manager_now=False,
                auto_delivery_now=False,
            )
        elif action == "decline":
            forecast_request, message = await set_forecast_request_declined(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
            )
        elif action in {"sales_manual", "sales_cancel"}:
            _, message = await handle_sales_callback(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
                action=action,
            )
        else:
            return _answer_callback_query(callback_id, "Неизвестное действие", True)

        await db.commit()
        contact_required = (
            action == "take"
            and _forecast_contact_required(message, getattr(forecast_request, "status", None))
        )
        if contact_required:
            callback_message = message
            chat_id = (
                ((callback_query.get("message") or {}).get("chat") or {}).get("id")
                or actor_user_id
            )
            await asyncio.to_thread(_send_forecast_contact_cta, int(chat_id), message)
            return _answer_callback_query(callback_id, callback_message)

        inactive = (
            action in {"take", "decline"}
            and forecast_request is not None
            and forecast_request_is_inactive_for_client(forecast_request)
        )
        if action in {"take", "decline"}:
            _run_background(asyncio.to_thread(_clear_forecast_client_message, callback_query))
        if inactive:
            return _answer_callback_query(callback_id, message, True)
        if should_notify_sales:
            _run_background(notify_sales_manager_for_request(forecast_request.id))
        callback_message = "Принято" if action in {"take", "decline"} else message
        if action == "take" and "Прогноз" in message:
            callback_message = message
        return _answer_callback_query(callback_id, callback_message)
    except HTTPException as exc:
        await db.rollback()
        return _answer_callback_query(callback_id, str(exc.detail), True)
    except Exception as exc:
        await db.rollback()
        logger.exception("[Webhook] Forecast callback failed: %s", exc)
        return _answer_callback_query(callback_id, "Не удалось обработать заявку", True)


async def _handle_pending_telegram_stake(message: dict, user_id: int) -> Optional[dict]:
    text = str(message.get("text") or "").strip()
    if not text:
        return None
    async with AsyncSessionLocal() as db:
        input_lookup = await lookup_forecast_stake_input(
            db,
            channel="telegram",
            user_id=user_id,
            for_update=True,
        )
        input_session = input_lookup.session
        if input_session is None:
            await db.commit()
            if input_lookup.expired:
                return {
                    "method": "sendMessage",
                    "chat_id": message["chat"]["id"],
                    "text": "Время ввода суммы истекло. Нажмите «Взять» у нужного прогноза ещё раз.",
                }
            return None
        if text.casefold() in {"/cancel", "отмена", "отменить"}:
            await clear_forecast_stake_input(db, channel="telegram", user_id=user_id)
            await db.commit()
            return {"method": "sendMessage", "chat_id": message["chat"]["id"], "text": "Ввод суммы отменён."}
        try:
            stake_rub = parse_stake_amount(text)
            forecast_request, response_message, should_notify_sales = await set_forecast_request_interested(
                db,
                request_id=input_session.forecast_request_id,
                actor_user_id=user_id,
                notify_sales_manager_now=False,
                auto_delivery_now=False,
                stake_rub=stake_rub,
                input_channel="telegram",
            )
            await clear_forecast_stake_input(db, channel="telegram", user_id=user_id)
            await db.commit()
            if should_notify_sales:
                _run_background(notify_sales_manager_for_request(forecast_request.id))
            return {
                "method": "sendMessage",
                "chat_id": message["chat"]["id"],
                "text": f"Сумма {stake_rub:,.2f} ₽ принята. {response_message}".replace(",", " "),
            }
        except ValueError as exc:
            await db.rollback()
            return {
                "method": "sendMessage",
                "chat_id": message["chat"]["id"],
                "text": f"{exc}. Попробуйте ещё раз или отправьте /cancel.",
            }
        except HTTPException as exc:
            await db.rollback()
            await clear_forecast_stake_input(db, channel="telegram", user_id=user_id)
            await db.commit()
            return {
                "method": "sendMessage",
                "chat_id": message["chat"]["id"],
                "text": f"{exc.detail}. Нажмите «Взять» у прогноза повторно.",
            }


@router.post("/webhook")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
):
    """
    Handles incoming messages from Telegram Bot API.
    Replies to '/start' or any message with a link/button to open the WebApp.
    """
    verify_telegram_webhook_secret(x_telegram_bot_api_secret_token)

    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON update")

    return await handle_telegram_update(update, request_base_url=str(request.base_url).rstrip("/"))


async def handle_telegram_update(update: dict, request_base_url: Optional[str] = None) -> dict:
    """
    Handles one Telegram update and returns a Bot API webhook-style response.
    The same function is used by the public webhook and by the polling fallback.
    """
    started_at = time.perf_counter()
    update_id = update.get("update_id")
    update_type = _telegram_update_type(update)
    chat_id = _telegram_update_chat_id(update)
    try:
        response = await _handle_telegram_update_inner(update, request_base_url=request_base_url)
    except Exception:
        duration_ms = round((time.perf_counter() - started_at) * 1000)
        logger.exception(
            "[Webhook] update failed update_id=%s type=%s chat_id=%s elapsed_ms=%s",
            update_id,
            update_type,
            chat_id,
            duration_ms,
        )
        raise

    duration_ms = round((time.perf_counter() - started_at) * 1000)
    logger.info(
        "[Webhook] update handled update_id=%s type=%s chat_id=%s elapsed_ms=%s response=%s",
        update_id,
        update_type,
        chat_id,
        duration_ms,
        _telegram_response_label(response),
    )
    return response


async def _handle_telegram_update_inner(update: dict, request_base_url: Optional[str] = None) -> dict:
    callback_query = update.get("callback_query")
    if callback_query:
        async with AsyncSessionLocal() as db:
            return await _handle_forecast_callback(callback_query, db)

    message = update.get("message") or {}
    if "pre_checkout_query" in update or "successful_payment" in message:
        async with AsyncSessionLocal() as db:
            return await process_telegram_payment_update(update, db)

    if not message:
        return {"status": "ok"}

    chat = message.get("chat")
    if not chat:
        return {"status": "ok"}

    chat_id = chat.get("id")
    text = message.get("text", "")
    user = message.get("from", {})

    logger.debug("[Webhook] message payload chat_id=%s text_len=%s", chat_id, len(text or ""))

    if _is_start_command(text):
        auth_token = parse_telegram_auth_start_param(_start_command_param(text))
        if auth_token:
            confirmed = await confirm_telegram_bot_auth_session(
                auth_token,
                {
                    "id": user.get("id") or chat_id,
                    "username": user.get("username"),
                    "first_name": user.get("first_name"),
                    "last_name": user.get("last_name"),
                    "start_param": _start_command_param(text),
                },
            )
            async with AsyncSessionLocal() as db:
                await _mark_private_telegram_chat_joined(db, message)
            return _build_auth_response(message, confirmed, request_base_url=request_base_url)
        async with AsyncSessionLocal() as db:
            await _mark_private_telegram_chat_joined(db, message)
            return await _build_start_response_from_template(db, message, request_base_url=request_base_url)

    pending_stake_response = await _handle_pending_telegram_stake(message, int(user.get("id") or chat_id))
    if pending_stake_response is not None:
        return pending_stake_response

    if _is_manual_post_command(text):
        async with AsyncSessionLocal() as db:
            manual_post_response = await _build_manual_post_response(
                message,
                user.get("id"),
                db,
            )
        return {
            "chat_id": chat_id,
            **manual_post_response,
        }

    if _emoji_catalog_command(text):
        async with AsyncSessionLocal() as db:
            emoji_catalog_response = await _build_emoji_catalog_response(message, user.get("id"), db)
        if emoji_catalog_response:
            return {
                "chat_id": chat_id,
                **emoji_catalog_response,
            }

    if _message_has_custom_emoji(message):
        async with AsyncSessionLocal() as db:
            library_response = await _build_custom_emoji_library_upload_response(
                message,
                user.get("id"),
                db,
            )
        if library_response:
            return {
                "chat_id": chat_id,
                **library_response,
            }

    emoji_ids_response = _build_emoji_ids_response(message, user.get("id"))
    if emoji_ids_response:
        return {
            "chat_id": chat_id,
            **emoji_ids_response,
        }

    if _can_use_emoji_id_command(user.get("id")) and (
        str(message.get("text") or message.get("caption") or "").strip()
    ):
        return {
            "method": "sendMessage",
            "chat_id": chat_id,
            "text": (
                "Чтобы добавить custom emoji в библиотеку приложения, просто отправьте "
                "их отдельным сообщением без подписи."
            ),
        }

    async with AsyncSessionLocal() as db:
        await _mark_private_telegram_chat_joined(db, message)
        return await _build_start_response_from_template(db, message, request_base_url=request_base_url)
