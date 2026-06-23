import asyncio
import hmac
import json
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.background_tasks import create_logged_task
from src.core.config import settings
from src.models.database import AsyncSessionLocal
from src.models.models import ForecastRequest, User
from src.services.forecast_delivery import (
    FORECAST_CONTACT_DRAFT_TEXT,
    FORECAST_STATUS_ANNOUNCED,
    notify_sales_manager_for_request,
    set_forecast_request_declined,
    set_forecast_request_interested,
)
from src.services.vk_delivery import (
    answer_vk_message_event,
    refresh_vk_delivery_status,
    send_vk_message,
    vk_group_id,
)

logger = logging.getLogger("uvicorn")
router = APIRouter(prefix="/vk", tags=["VK Callback"])

VK_FORECAST_TAKE_TEXTS = {"взять", "беру", "take"}
VK_FORECAST_DECLINE_TEXTS = {"не взять", "не беру", "decline"}
NO_ACTIVE_FORECAST_MESSAGE = "Не нашёл активный анонс прогноза."
MULTIPLE_ACTIVE_FORECASTS_MESSAGE = "У вас несколько активных анонсов, нажмите кнопку в нужном сообщении."
VK_DEFAULT_REPLY_MESSAGE = (
    "VK сообщения подключены. Если хотите взять прогноз, нажмите кнопку «Взять» "
    "в анонсе или напишите «Взять»."
)
VK_PROFILE_NOT_LINKED_MESSAGE = (
    "VK получил сообщение, но профиль не привязан к Shamrai. "
    "Откройте профиль в мини-приложении и привяжите VK."
)


def _forecast_contact_required(message: str, forecast_status: Optional[str]) -> bool:
    return forecast_status == FORECAST_STATUS_ANNOUNCED and FORECAST_CONTACT_DRAFT_TEXT in str(message or "")


def _vk_forecast_contact_message(message: str) -> str:
    group_id = vk_group_id()
    dialog_url = f"https://vk.me/club{group_id}" if group_id else ""
    url_line = f"\n\nНаписать Shamrai: {dialog_url}" if dialog_url else ""
    return f"{message}{url_line}"


def _verify_callback_group(raw_group_id: Any) -> None:
    configured_group_id = vk_group_id()
    if not configured_group_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VK callback group id is not configured",
        )
    try:
        incoming_group_id = int(raw_group_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="VK group mismatch")
    if incoming_group_id != configured_group_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="VK group mismatch")


def _verify_callback_secret(payload: dict) -> None:
    expected_secret = settings.VK_CALLBACK_SECRET.strip()
    if not expected_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="VK callback secret is not configured",
        )
    if not hmac.compare_digest(str(payload.get("secret") or ""), expected_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="VK callback secret mismatch",
        )


def _log_confirmation_group_mismatch(raw_group_id: Any) -> None:
    configured_group_id = vk_group_id()
    if not configured_group_id:
        logger.warning("[VKCallback] confirmation accepted while VK_GROUP_ID is not configured")
        return
    try:
        incoming_group_id = int(raw_group_id)
    except (TypeError, ValueError):
        logger.warning(
            "[VKCallback] confirmation accepted with unreadable group_id=%s configured_group_id=%s",
            raw_group_id,
            configured_group_id,
        )
        return
    if incoming_group_id != configured_group_id:
        logger.warning(
            "[VKCallback] confirmation accepted for group_id=%s while configured_group_id=%s",
            incoming_group_id,
            configured_group_id,
        )


def _decode_button_payload(value: Any) -> dict:
    current = value
    for _ in range(3):
        if isinstance(current, dict):
            nested_payload = current.get("payload")
            if "type" not in current and nested_payload is not None:
                current = nested_payload
                continue
            return current
        if not isinstance(current, str) or not current.strip():
            return {}
        text = current.strip()
        if text.startswith("forecast:"):
            parts = text.split(":")
            if len(parts) == 3:
                return {
                    "type": "forecast_request",
                    "action": parts[1],
                    "request_id": parts[2],
                }
        try:
            current = json.loads(text)
        except json.JSONDecodeError:
            return {}
    return current if isinstance(current, dict) else {}


def _vk_event_user_id(event_object: dict) -> Any:
    message = event_object.get("message") or {}
    return event_object.get("user_id") or message.get("from_id")


def _vk_event_peer_id(event_object: dict) -> Any:
    message = event_object.get("message") or {}
    return event_object.get("peer_id") or message.get("peer_id") or _vk_event_user_id(event_object)


def _vk_event_button_payload(event_object: dict) -> dict:
    message = event_object.get("message") or {}
    return _decode_button_payload(event_object.get("payload") or message.get("payload"))


def _normalize_vk_message_text(value: Any) -> str:
    text = " ".join(str(value or "").strip().casefold().split())
    return text.strip(" \t\r\n.,!?:;\"'«»")


def _forecast_action_from_message_text(event_object: dict) -> Optional[str]:
    message = event_object.get("message") or {}
    normalized_text = _normalize_vk_message_text(message.get("text"))
    if normalized_text in VK_FORECAST_TAKE_TEXTS:
        return "take"
    if normalized_text in VK_FORECAST_DECLINE_TEXTS:
        return "decline"
    return None


def _with_forecast_payload(event_object: dict, *, action: str, request_id: UUID) -> dict:
    synthetic_event = dict(event_object)
    message = dict(synthetic_event.get("message") or {})
    payload = json.dumps(
        {
            "type": "forecast_request",
            "action": action,
            "request_id": str(request_id),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    message["payload"] = payload
    synthetic_event["message"] = message
    synthetic_event["payload"] = payload
    return synthetic_event


def _should_reply_to_plain_text_message(event_object: dict) -> bool:
    message = event_object.get("message") or {}
    if str(message.get("out") or "").strip() in {"1", "true", "True"}:
        return False
    if not _normalize_vk_message_text(message.get("text")):
        return False
    return _vk_event_user_id(event_object) is not None


async def _load_user_by_vk_id(db: AsyncSession, vk_user_id: Any) -> Optional[User]:
    clean_vk_id = str(vk_user_id or "").strip()
    if not clean_vk_id:
        return None
    result = await db.execute(select(User).filter(User.vk_user_id == clean_vk_id))
    return result.scalars().first()


async def _find_announced_forecast_request_ids_for_user(db: AsyncSession, user: User) -> list[UUID]:
    result = await db.execute(
        select(ForecastRequest.id)
        .filter(
            ForecastRequest.user_id == user.telegram_id,
            ForecastRequest.status == FORECAST_STATUS_ANNOUNCED,
        )
        .order_by(ForecastRequest.created_at.desc())
        .limit(2)
    )
    return list(result.scalars().all())


async def _refresh_message_permission_from_message_new(event_object: dict) -> None:
    vk_user_id = _vk_event_user_id(event_object)
    if not vk_user_id:
        return

    async with AsyncSessionLocal() as db:
        user = await _load_user_by_vk_id(db, vk_user_id)
        if not user:
            return
        status_payload = await refresh_vk_delivery_status(db, user, refresh_group=False)
        if not status_payload.get("messages_allowed") and not user.vk_messages_allowed:
            user.vk_messages_allowed = True
        await db.commit()


async def _set_message_permission_from_callback(event_object: dict, *, allowed: bool) -> None:
    vk_user_id = _vk_event_user_id(event_object)
    if not vk_user_id:
        logger.warning("[VKCallback] message permission event has no user_id: %s", event_object)
        return

    async with AsyncSessionLocal() as db:
        user = await _load_user_by_vk_id(db, vk_user_id)
        if not user:
            logger.info(
                "[VKCallback] message permission event for unlinked vk_user_id=%s allowed=%s",
                vk_user_id,
                allowed,
            )
            return
        user.vk_messages_allowed = allowed
        await db.commit()
        logger.info(
            "[VKCallback] vk_messages_allowed updated from callback vk_user_id=%s allowed=%s",
            vk_user_id,
            allowed,
        )


def _run_background(coro) -> None:
    create_logged_task(coro, logger=logger, failure_message="[VKCallback] Background task failed")


async def _handle_forecast_button(event_object: dict, db: AsyncSession) -> dict:
    button_payload = _vk_event_button_payload(event_object)
    if button_payload.get("type") != "forecast_request":
        return {"status": "ignored"}

    vk_user_id = _vk_event_user_id(event_object)
    logger.info(
        "[VKCallback] forecast_button received user_id=%s peer_id=%s action=%s request_id=%s event_id=%s",
        vk_user_id,
        _vk_event_peer_id(event_object),
        button_payload.get("action"),
        button_payload.get("request_id"),
        event_object.get("event_id"),
    )
    if not vk_user_id:
        return {"status": "failed", "message": "VK не передал пользователя"}

    user = await _load_user_by_vk_id(db, vk_user_id)
    if not user:
        return {"status": "failed", "message": "VK profile is not linked in Shamrai"}

    try:
        request_id = UUID(str(button_payload.get("request_id") or ""))
    except ValueError:
        return {"status": "failed", "message": "Некорректная заявка"}

    action = str(button_payload.get("action") or "").strip()
    should_notify_sales = False
    try:
        if action == "take":
            forecast_request, message, should_notify_sales = await set_forecast_request_interested(
                db,
                request_id=request_id,
                actor_user_id=user.telegram_id,
                notify_sales_manager_now=False,
                auto_delivery_method="auto",
                auto_delivery_now=False,
            )
        elif action == "decline":
            _, message = await set_forecast_request_declined(
                db,
                request_id=request_id,
                actor_user_id=user.telegram_id,
            )
            forecast_request = None
        else:
            return {"status": "failed", "message": "Неизвестная кнопка"}

        await db.commit()
        if (
            action == "take"
            and forecast_request is not None
            and _forecast_contact_required(message, getattr(forecast_request, "status", None))
        ):
            return {"status": "contact_required", "message": _vk_forecast_contact_message(message)}
        if should_notify_sales and forecast_request is not None:
            _run_background(notify_sales_manager_for_request(forecast_request.id))
        return {"status": "ok", "message": message}
    except HTTPException as exc:
        await db.rollback()
        return {"status": "failed", "message": str(exc.detail)}
    except Exception as exc:
        await db.rollback()
        logger.exception("[VKCallback] Forecast button failed: %s", exc)
        return {"status": "failed", "message": "Не удалось обработать заявку"}


def _answer_forecast_button_event(event_object: dict, message: str) -> None:
    event_id = event_object.get("event_id")
    if event_id:
        _run_background(
            asyncio.to_thread(
                answer_vk_message_event,
                user_id=_vk_event_user_id(event_object),
                peer_id=_vk_event_peer_id(event_object),
                event_id=event_id,
                text=message,
            )
        )


def _send_forecast_button_message(event_object: dict, message: str) -> None:
    vk_user_id = _vk_event_user_id(event_object)
    if vk_user_id:
        _run_background(
            asyncio.to_thread(
                send_vk_message,
                vk_user_id=vk_user_id,
                message=message,
            )
        )


async def _process_forecast_button_event(event_object: dict) -> None:
    try:
        async with AsyncSessionLocal() as db:
            result = await _handle_forecast_button(event_object, db)
    except Exception as exc:
        logger.exception("[VKCallback] message_event failed: %s", exc)
        result = {"status": "failed", "message": "Не удалось обработать кнопку"}

    if result.get("status") == "ignored":
        return

    message = result.get("message") or "Принято"
    _answer_forecast_button_event(event_object, message)
    _send_forecast_button_message(event_object, message)


async def _process_plain_text_forecast_message(event_object: dict) -> None:
    action = _forecast_action_from_message_text(event_object)
    if not action:
        return

    vk_user_id = _vk_event_user_id(event_object)
    if not vk_user_id:
        return

    try:
        async with AsyncSessionLocal() as db:
            user = await _load_user_by_vk_id(db, vk_user_id)
            if not user:
                _send_forecast_button_message(event_object, "VK профиль не привязан к Shamrai.")
                return

            request_ids = await _find_announced_forecast_request_ids_for_user(db, user)
            if not request_ids:
                _send_forecast_button_message(event_object, NO_ACTIVE_FORECAST_MESSAGE)
                return
            if len(request_ids) > 1:
                _send_forecast_button_message(event_object, MULTIPLE_ACTIVE_FORECASTS_MESSAGE)
                return

            result = await _handle_forecast_button(
                _with_forecast_payload(event_object, action=action, request_id=request_ids[0]),
                db,
            )
    except Exception as exc:
        logger.exception("[VKCallback] plain text forecast message failed: %s", exc)
        result = {"status": "failed", "message": "Не удалось обработать заявку"}

    if result.get("status") == "ignored":
        return

    _send_forecast_button_message(event_object, result.get("message") or "Принято")


async def _process_plain_text_status_message(event_object: dict) -> None:
    if not _should_reply_to_plain_text_message(event_object):
        return
    if _forecast_action_from_message_text(event_object):
        return

    vk_user_id = _vk_event_user_id(event_object)
    try:
        async with AsyncSessionLocal() as db:
            user = await _load_user_by_vk_id(db, vk_user_id)
    except Exception as exc:
        logger.exception("[VKCallback] plain text status message failed: %s", exc)
        user = None

    if user:
        _send_forecast_button_message(event_object, VK_DEFAULT_REPLY_MESSAGE)
    else:
        _send_forecast_button_message(event_object, VK_PROFILE_NOT_LINKED_MESSAGE)


async def handle_vk_message_new_event(event_object: dict, *, source: str = "callback") -> None:
    message = event_object.get("message") or {}
    button_payload = _vk_event_button_payload(event_object)
    logger.info(
        "[VKCallback] message_new source=%s user_id=%s peer_id=%s text=%s forecast_action=%s request_id=%s",
        source,
        message.get("from_id") or event_object.get("user_id"),
        message.get("peer_id") or event_object.get("peer_id"),
        str(message.get("text") or "")[:500],
        button_payload.get("action") if button_payload.get("type") == "forecast_request" else None,
        button_payload.get("request_id") if button_payload.get("type") == "forecast_request" else None,
    )
    _run_background(_refresh_message_permission_from_message_new(event_object))
    if button_payload.get("type") == "forecast_request":
        _run_background(_process_forecast_button_event(event_object))
    elif _forecast_action_from_message_text(event_object):
        _run_background(_process_plain_text_forecast_message(event_object))
    elif _should_reply_to_plain_text_message(event_object):
        _run_background(_process_plain_text_status_message(event_object))


@router.post("/callback")
async def vk_callback(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON update")

    event_type = str(payload.get("type") or "")

    if event_type == "confirmation":
        confirmation_code = settings.VK_CALLBACK_CONFIRMATION_CODE.strip()
        if not confirmation_code:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VK callback confirmation code is not configured",
            )
        _log_confirmation_group_mismatch(payload.get("group_id"))
        return PlainTextResponse(confirmation_code)

    _verify_callback_group(payload.get("group_id"))
    _verify_callback_secret(payload)

    if event_type == "message_allow":
        _run_background(_set_message_permission_from_callback(payload.get("object") or {}, allowed=True))
        return PlainTextResponse("ok")

    if event_type == "message_deny":
        _run_background(_set_message_permission_from_callback(payload.get("object") or {}, allowed=False))
        return PlainTextResponse("ok")

    if event_type == "message_new":
        event_object = payload.get("object") or {}
        await handle_vk_message_new_event(event_object, source="callback")
        return PlainTextResponse("ok")

    if event_type != "message_event":
        return PlainTextResponse("ok")

    event_object = payload.get("object") or {}

    button_payload = _vk_event_button_payload(event_object)
    if button_payload.get("type") != "forecast_request":
        return PlainTextResponse("ok")

    _answer_forecast_button_event(event_object, "Принято, обрабатываем.")
    _run_background(_process_forecast_button_event(event_object))
    return PlainTextResponse("ok")
