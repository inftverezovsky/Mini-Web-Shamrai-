import asyncio
import json
import logging
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.config import settings
from src.models.database import AsyncSessionLocal
from src.models.models import User
from src.services.forecast_delivery import (
    auto_deliver_forecast_request_for_request,
    forecast_request_should_auto_deliver,
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
    if payload.get("secret") != expected_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="VK callback secret mismatch",
        )


def _decode_button_payload(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


async def _load_user_by_vk_id(db: AsyncSession, vk_user_id: Any) -> Optional[User]:
    clean_vk_id = str(vk_user_id or "").strip()
    if not clean_vk_id:
        return None
    result = await db.execute(select(User).filter(User.vk_user_id == clean_vk_id))
    return result.scalars().first()


async def _refresh_message_permission_from_message_new(event_object: dict) -> None:
    message = event_object.get("message") or {}
    vk_user_id = message.get("from_id") or event_object.get("user_id")
    if not vk_user_id:
        return

    async with AsyncSessionLocal() as db:
        user = await _load_user_by_vk_id(db, vk_user_id)
        if not user:
            return
        status_payload = await refresh_vk_delivery_status(db, user, refresh_group=False)
        if not status_payload.get("remote_messages_checked") and not user.vk_messages_allowed:
            user.vk_messages_allowed = True
        await db.commit()


def _run_background(coro) -> None:
    task = asyncio.create_task(coro)

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.exception("[VKCallback] Background task failed: %s", exc)

    task.add_done_callback(_log_failure)


async def _handle_forecast_button(event_object: dict, db: AsyncSession) -> dict:
    button_payload = _decode_button_payload(event_object.get("payload"))
    if button_payload.get("type") != "forecast_request":
        return {"status": "ignored"}

    user = await _load_user_by_vk_id(db, event_object.get("user_id"))
    if not user:
        return {"status": "failed", "message": "VK profile is not linked in Shamrai"}

    try:
        request_id = UUID(str(button_payload.get("request_id") or ""))
    except ValueError:
        return {"status": "failed", "message": "Некорректная заявка"}

    action = str(button_payload.get("action") or "").strip()
    should_notify_sales = False
    should_auto_deliver = False
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
            should_auto_deliver = forecast_request_should_auto_deliver(forecast_request)
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
        if should_auto_deliver:
            _run_background(
                auto_deliver_forecast_request_for_request(
                    forecast_request.id,
                    delivery_method="auto",
                )
            )
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


@router.post("/callback")
async def vk_callback(request: Request):
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON update")

    event_type = str(payload.get("type") or "")
    _verify_callback_group(payload.get("group_id"))

    if event_type == "confirmation":
        confirmation_code = settings.VK_CALLBACK_CONFIRMATION_CODE.strip()
        if not confirmation_code:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="VK callback confirmation code is not configured",
            )
        return PlainTextResponse(confirmation_code)

    _verify_callback_secret(payload)

    if event_type == "message_new":
        event_object = payload.get("object") or {}
        message = event_object.get("message") or {}
        logger.info(
            "[VKCallback] message_new user_id=%s peer_id=%s text=%s",
            message.get("from_id") or event_object.get("user_id"),
            message.get("peer_id") or event_object.get("peer_id"),
            str(message.get("text") or "")[:500],
        )
        try:
            await _refresh_message_permission_from_message_new(event_object)
        except Exception as exc:
            logger.warning("[VKCallback] Failed to refresh message permission: %s", exc)
        return PlainTextResponse("ok")

    if event_type != "message_event":
        return PlainTextResponse("ok")

    event_object = payload.get("object") or {}
    try:
        async with AsyncSessionLocal() as db:
            result = await _handle_forecast_button(event_object, db)
    except Exception as exc:
        logger.exception("[VKCallback] message_event failed: %s", exc)
        result = {"status": "failed", "message": "Не удалось обработать кнопку"}

    if result.get("status") == "ignored":
        return PlainTextResponse("ok")
    message = result.get("message") or "Принято"
    event_id = event_object.get("event_id")
    if event_id:
        _run_background(
            asyncio.to_thread(
                answer_vk_message_event,
                user_id=event_object.get("user_id"),
                peer_id=event_object.get("peer_id"),
                event_id=event_id,
                text=message,
            )
        )
    if event_object.get("user_id"):
        _run_background(
            asyncio.to_thread(
                send_vk_message,
                vk_user_id=event_object.get("user_id"),
                message=message,
            )
        )
    return PlainTextResponse("ok")
