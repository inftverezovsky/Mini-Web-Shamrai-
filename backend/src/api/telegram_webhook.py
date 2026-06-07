import asyncio
import html
import json
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from src.models.database import AsyncSessionLocal
from src.core.config import settings
from src.core.security import verify_telegram_webhook_secret
from src.core.telegram_text import contact_footer, write_emoji
from src.api.payments import process_telegram_payment_update
from src.services.forecast_delivery import (
    handle_sales_callback,
    notify_sales_manager_for_request,
    set_forecast_request_declined,
    set_forecast_request_interested,
)
import logging

router = APIRouter(prefix="/telegram", tags=["Telegram Webhook"])
logger = logging.getLogger("uvicorn")

SHAMRAI_BUTTON_TEXT = "Открыть Shamrai Analytics"
EMOJI_ID_COMMANDS = {
    "/emoji_ids": "TELEGRAM_CUSTOM_EMOJI_IDS",
    "/emoji_ids_bk": "TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS",
    "/emoji_ids_sport": "TELEGRAM_SPORT_CUSTOM_EMOJI_IDS",
}


def _shamrai_web_app_button(web_app_url: str) -> dict:
    return {
        "text": SHAMRAI_BUTTON_TEXT,
        "web_app": {"url": web_app_url},
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
    return not allowed_ids or int(user_id) in allowed_ids


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


def _answer_callback_query(callback_query_id: Optional[str], text: str, show_alert: bool = False) -> dict:
    if not callback_query_id:
        return {"status": "ok"}
    return {
        "method": "answerCallbackQuery",
        "callback_query_id": callback_query_id,
        "text": text[:190],
        "show_alert": show_alert,
    }


def _run_background(coro) -> None:
    task = asyncio.create_task(coro)

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.exception("[Webhook] Background task failed: %s", exc)

    task.add_done_callback(_log_failure)


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
    try:
        request_id = UUID(parts[2])
    except ValueError:
        return _answer_callback_query(callback_id, "Некорректная заявка", True)

    try:
        if action == "take":
            forecast_request, message, should_notify_sales = await set_forecast_request_interested(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
                notify_sales_manager_now=False,
            )
        elif action == "decline":
            _, message = await set_forecast_request_declined(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
            )
        elif action in {"sales_send", "sales_manual", "sales_cancel"}:
            _, message = await handle_sales_callback(
                db,
                request_id=request_id,
                actor_user_id=int(actor_user_id),
                action=action,
            )
        else:
            return _answer_callback_query(callback_id, "Неизвестное действие", True)

        await db.commit()
        if should_notify_sales:
            _run_background(notify_sales_manager_for_request(forecast_request.id))
        return _answer_callback_query(callback_id, message)
    except HTTPException as exc:
        await db.rollback()
        return _answer_callback_query(callback_id, str(exc.detail), True)
    except Exception as exc:
        await db.rollback()
        logger.exception("[Webhook] Forecast callback failed: %s", exc)
        return _answer_callback_query(callback_id, "Не удалось обработать заявку", True)


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

    logger.info(f"[Webhook] Received message from {chat_id}: {text}")

    emoji_ids_response = _build_emoji_ids_response(message, user.get("id"))
    if emoji_ids_response:
        return {
            "chat_id": chat_id,
            **emoji_ids_response,
        }

    # Welcome message with a button to launch the Mini App
    welcome_text = (
        f"👋 <b>Привет, {user.get('first_name', 'друг')}!</b>\n\n"
        "Добро пожаловать в <b>ШАМРАЙ | ОШИБКИ БК</b> — высокотехнологичную экосистему спортивной аналитики от действующих сотрудников БК.\n\n"
        "📊 Здесь вас ждут профессиональные прогнозы, невероятные ошибки буков и умные уведомления.\n\n"
        f"{contact_footer()}\n\n"
        "👇 Нажмите на кнопку ниже, чтобы открыть для себя то, что вы еще не видели нигде."
    )

    # Prefer the configured Mini App URL, but never send an empty web_app URL.
    web_app_url = settings.FRONTEND_BASE_URL.strip() or request_base_url or settings.API_BASE_URL.strip()

    reply_markup = {
        "inline_keyboard": [
            [
                _shamrai_web_app_button(web_app_url)
            ]
        ]
    }

    return {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": welcome_text,
        "parse_mode": "HTML",
        "reply_markup": reply_markup
    }
