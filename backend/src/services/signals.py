import asyncio
import json
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Iterable, Optional

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.payments import call_telegram_api
from src.core.config import settings
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.models.database import AsyncSessionLocal
from src.models.models import PersonalSignal, User

try:
    from pywebpush import WebPushException, webpush
except Exception:  # pragma: no cover - optional dependency guard for partial local envs.
    WebPushException = Exception
    webpush = None

logger = logging.getLogger("uvicorn")
WEBSOCKET_SEND_TIMEOUT_SECONDS = 1.2
WEBSOCKET_FANOUT_CONCURRENCY = 64
EXTERNAL_DELIVERY_CONCURRENCY = 16
WEB_PUSH_INVALID_STATUS_CODES = {404, 410}


def _run_background_delivery(coro) -> None:
    task = asyncio.create_task(coro)

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            done_task.result()
        except Exception as exc:
            logger.exception("[Signals] Background delivery failed: %s", exc)

    task.add_done_callback(_log_failure)


class SignalStreamHub:
    def __init__(self) -> None:
        self._connections: dict[int, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            self._connections.setdefault(user_id, set()).add(websocket)

    async def disconnect(self, user_id: int, websocket: WebSocket) -> None:
        async with self._lock:
            sockets = self._connections.get(user_id)
            if not sockets:
                return
            sockets.discard(websocket)
            if not sockets:
                self._connections.pop(user_id, None)

    async def send_to_user(self, user_id: int, payload: dict[str, Any]) -> None:
        async with self._lock:
            sockets = list(self._connections.get(user_id, set()))

        if not sockets:
            return

        message = json.dumps(payload, ensure_ascii=False)
        async def _send_one(websocket: WebSocket) -> Optional[WebSocket]:
            try:
                await asyncio.wait_for(
                    websocket.send_text(message),
                    timeout=WEBSOCKET_SEND_TIMEOUT_SECONDS,
                )
                return None
            except Exception:
                return websocket

        stale_sockets = [
            websocket
            for websocket in await asyncio.gather(*(_send_one(websocket) for websocket in sockets))
            if websocket is not None
        ]

        for websocket in stale_sockets:
            await self.disconnect(user_id, websocket)


signal_stream_hub = SignalStreamHub()


def web_push_configured() -> bool:
    return bool(
        webpush
        and settings.WEB_PUSH_VAPID_PUBLIC_KEY.strip()
        and settings.WEB_PUSH_VAPID_PRIVATE_KEY.strip()
    )


def signal_to_payload(signal: PersonalSignal) -> dict[str, Any]:
    return {
        "id": signal.id,
        "user_id": signal.user_id,
        "text": signal.text,
        "type": signal.type,
        "data": signal.data or {},
        "created_at": signal.created_at.isoformat() if signal.created_at else datetime.now(timezone.utc).isoformat(),
    }


def build_live_signal_text(
    *,
    event_name: str,
    coefficient: Decimal,
    brain_score: Optional[int],
) -> str:
    brain_line = f"\n🧠 Brain Score: {brain_score}/10" if brain_score is not None else ""
    return (
        "⚡⚡⚡ SHAMRAI LIVE SIGNAL ALARM ⚡⚡⚡\n\n"
        "Новый срочный Live-прогноз от Shamrai:\n"
        f"🏆 {event_name}\n"
        f"📈 Коэффициент: {float(coefficient):.2f}"
        f"{brain_line}\n\n"
        "Быстрее заходите в приложение Shamrai Analytics Hub!"
    )


def _absolute_url(value: Optional[str]) -> str:
    raw_value = str(value or "").strip()
    if not raw_value:
        return ""
    if raw_value.startswith(("http://", "https://")):
        return raw_value
    frontend_url = settings.FRONTEND_BASE_URL.strip().rstrip("/")
    api_url = settings.API_BASE_URL.strip().rstrip("/")
    if raw_value.startswith("/static"):
        base_url = api_url or frontend_url.removesuffix("/app")
    else:
        base_url = frontend_url or api_url
    if not base_url:
        return raw_value
    clean_value = raw_value if raw_value.startswith("/") else f"/{raw_value}"
    return f"{base_url}{clean_value}"


def _first_non_empty_line(value: Optional[str]) -> str:
    for line in str(value or "").splitlines():
        clean_line = line.strip()
        if clean_line:
            return clean_line
    return ""


def _trim_notification_body(value: Optional[str], limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[:limit - 3].rstrip()}..."


def _notification_title(signal_payload: dict[str, Any]) -> str:
    signal_type = str(signal_payload.get("type") or "")
    signal_data = signal_payload.get("data") or {}
    explicit_title = str(signal_data.get("push_title") or signal_data.get("title") or "").strip()
    if explicit_title:
        return explicit_title
    if signal_type == "forecast_teaser":
        return "Закрытый прогноз Shamrai"
    if signal_type == "forecast_full":
        return "Прогноз готов"
    if signal_type == "announcement":
        return "Анонс Shamrai"
    if signal_type == "live_signal":
        return "Shamrai Live Signal"
    return "Личный бот Shamrai"


def _notification_body(signal_payload: dict[str, Any]) -> str:
    signal_data = signal_payload.get("data") or {}
    body = (
        signal_data.get("push_body")
        or _first_non_empty_line(signal_data.get("message_text"))
        or _first_non_empty_line(signal_payload.get("text"))
        or "Новый персональный сигнал уже в чате."
    )
    return _trim_notification_body(str(body))


def _web_push_exception_status(exc: Exception) -> Optional[int]:
    response = getattr(exc, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code is not None:
        try:
            return int(status_code)
        except (TypeError, ValueError):
            return None
    for attr_name in ("status_code", "code"):
        value = getattr(exc, attr_name, None)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return None


def _web_push_notification_payload(signal_payload: dict[str, Any]) -> str:
    frontend_url = settings.FRONTEND_BASE_URL.strip().rstrip("/") or "/"
    target_url = f"{frontend_url}?open=web-bot-chat"
    signal_data = signal_payload.get("data") or {}
    coupon_image_url = _absolute_url(signal_data.get("coupon_image_url"))
    return json.dumps(
        {
            "title": _notification_title(signal_payload),
            "body": _notification_body(signal_payload),
            "tag": f"personal-signal-{signal_payload['id']}",
            "url": target_url,
            "image": coupon_image_url,
            "signal_id": signal_payload["id"],
            "type": signal_payload["type"],
            "forecast_request_id": signal_data.get("forecast_request_id"),
            "data": {
                "url": target_url,
                "signal_id": signal_payload["id"],
                "type": signal_payload["type"],
                "forecast_request_id": signal_data.get("forecast_request_id"),
            },
        },
        ensure_ascii=False,
    )


def _send_web_push(subscription: dict[str, Any], signal_payload: dict[str, Any]) -> dict[str, Any]:
    if not web_push_configured():
        return {"ok": False, "description": "Web Push VAPID is not configured"}

    try:
        webpush(
            subscription_info=subscription,
            data=_web_push_notification_payload(signal_payload),
            vapid_private_key=settings.WEB_PUSH_VAPID_PRIVATE_KEY.strip(),
            vapid_claims={"sub": settings.WEB_PUSH_VAPID_SUBJECT.strip() or "mailto:support@shamra1.pro"},
            ttl=60 * 60,
        )
        return {"ok": True}
    except WebPushException as exc:
        status_code = _web_push_exception_status(exc)
        return {
            "ok": False,
            "description": str(exc),
            "status_code": status_code,
            "invalid_subscription": status_code in WEB_PUSH_INVALID_STATUS_CODES,
        }


async def clear_invalid_web_push_subscription(
    user_id: int,
    subscription: Optional[dict[str, Any]],
) -> None:
    endpoint = subscription.get("endpoint") if isinstance(subscription, dict) else None
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User).filter(User.telegram_id == user_id))
        user = result.scalars().first()
        if not user:
            return
        current_endpoint = (
            user.web_push_subscription.get("endpoint")
            if isinstance(user.web_push_subscription, dict)
            else None
        )
        if endpoint and current_endpoint and endpoint != current_endpoint:
            return
        user.web_push_subscription = None
        await db.commit()


async def send_telegram_signal_to_user(user_id: int, text: str) -> dict[str, Any]:
    telegram_result = await asyncio.to_thread(
        call_telegram_api,
        "sendMessage",
        {
            "chat_id": user_id,
            "text": text,
            "disable_web_page_preview": True,
        },
    )
    if not telegram_result.get("ok"):
        logger.info(
            "[Signals] Telegram signal failed for user %s: %s",
            user_id,
            telegram_result.get("description", "unknown error"),
        )
    return telegram_result


async def send_web_push_subscription_to_user(
    user_id: int,
    subscription: Optional[dict[str, Any]],
    signal_payload: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(subscription, dict) or not subscription.get("endpoint"):
        return {"ok": False, "description": "User has no Web Push subscription"}

    result = await asyncio.to_thread(_send_web_push, subscription, signal_payload)
    if not result.get("ok"):
        logger.info(
            "[Signals] Web Push skipped/failed for user %s: %s",
            user_id,
            result.get("description", "unknown error"),
        )
        if result.get("invalid_subscription"):
            await clear_invalid_web_push_subscription(user_id, subscription)
    return result


async def send_web_push_to_user(user: User, signal_payload: dict[str, Any]) -> dict[str, Any]:
    return await send_web_push_subscription_to_user(
        user.telegram_id,
        user.web_push_subscription,
        signal_payload,
    )


async def dispatch_signal_external_deliveries(
    *,
    user_id: int,
    text: str,
    signal_payload: dict[str, Any],
    web_push_subscription: Optional[dict[str, Any]],
    send_telegram: bool,
    send_web_push: bool,
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if send_telegram and is_personal_telegram_user_id(user_id):
        result["telegram"] = await send_telegram_signal_to_user(user_id, text)
    if send_web_push:
        result["web_push"] = await send_web_push_subscription_to_user(user_id, web_push_subscription, signal_payload)
    return result


async def dispatch_signal_external_delivery_batch(deliveries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(EXTERNAL_DELIVERY_CONCURRENCY)

    async def _deliver_one(delivery: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await dispatch_signal_external_deliveries(**delivery)

    if deliveries:
        return list(await asyncio.gather(*(_deliver_one(delivery) for delivery in deliveries)))
    return []


async def dispatch_websocket_delivery_batch(deliveries: list[tuple[int, dict[str, Any]]]) -> None:
    semaphore = asyncio.Semaphore(WEBSOCKET_FANOUT_CONCURRENCY)

    async def _send_one(user_id: int, payload: dict[str, Any]) -> None:
        async with semaphore:
            await signal_stream_hub.send_to_user(user_id, payload)

    if deliveries:
        await asyncio.gather(*(_send_one(user_id, payload) for user_id, payload in deliveries))


async def deliver_personal_signal(
    db: AsyncSession,
    *,
    user: User,
    text: str,
    signal_type: str = "signal",
    data: Optional[dict[str, Any]] = None,
    send_telegram: bool = True,
    send_web_push: bool = True,
) -> PersonalSignal:
    signal = PersonalSignal(
        user_id=user.telegram_id,
        text=text,
        type=signal_type,
        data=data or {},
        created_at=datetime.now(timezone.utc),
    )
    db.add(signal)
    await db.flush()

    payload = signal_to_payload(signal)
    await signal_stream_hub.send_to_user(user.telegram_id, payload)
    _run_background_delivery(
        dispatch_signal_external_delivery_batch([
            {
                "user_id": user.telegram_id,
                "text": text,
                "signal_payload": payload,
                "web_push_subscription": user.web_push_subscription,
                "send_telegram": send_telegram,
                "send_web_push": send_web_push,
            }
        ])
    )
    return signal


async def broadcast_personal_signals(
    db: AsyncSession,
    *,
    users: Iterable[User],
    text: str,
    signal_type: str = "signal",
    data: Optional[dict[str, Any]] = None,
    data_by_user_id: Optional[dict[int, dict[str, Any]]] = None,
    send_telegram: bool = False,
    send_web_push: bool = True,
    return_report: bool = False,
) -> Any:
    user_list = list({user.telegram_id: user for user in users}.values())
    if not user_list:
        if return_report:
            return {
                "created": 0,
                "web_push_audience": 0,
                "web_push_sent": 0,
                "web_push_failed": 0,
                "web_push_missing_permission": 0,
                "web_push_errors": [],
            }
        return 0

    created_at = datetime.now(timezone.utc)
    signals = [
        PersonalSignal(
            user_id=user.telegram_id,
            text=text,
            type=signal_type,
            data=(data_by_user_id or {}).get(user.telegram_id, data or {}),
            created_at=created_at,
        )
        for user in user_list
    ]
    for signal in signals:
        db.add(signal)
    await db.flush()

    websocket_deliveries: list[tuple[int, dict[str, Any]]] = []
    external_deliveries: list[dict[str, Any]] = []
    web_push_missing_permission = 0
    for user, signal in zip(user_list, signals):
        payload = signal_to_payload(signal)
        has_web_push_subscription = (
            isinstance(user.web_push_subscription, dict)
            and bool(user.web_push_subscription.get("endpoint"))
        )
        if send_web_push and not has_web_push_subscription:
            web_push_missing_permission += 1
        websocket_deliveries.append((user.telegram_id, payload))
        external_deliveries.append({
            "user_id": user.telegram_id,
            "text": text,
            "signal_payload": payload,
            "web_push_subscription": user.web_push_subscription,
            "send_telegram": send_telegram,
            "send_web_push": send_web_push and has_web_push_subscription,
        })

    await dispatch_websocket_delivery_batch(websocket_deliveries)
    if return_report:
        external_results = await dispatch_signal_external_delivery_batch(external_deliveries)
        web_push_results = [
            item.get("web_push")
            for item in external_results
            if isinstance(item.get("web_push"), dict)
        ]
        web_push_errors = [
            str(item.get("description") or "unknown error")
            for item in web_push_results
            if not item.get("ok")
        ]
        return {
            "created": len(signals),
            "web_push_audience": len(user_list) - web_push_missing_permission,
            "web_push_sent": sum(1 for item in web_push_results if item.get("ok")),
            "web_push_failed": sum(1 for item in web_push_results if not item.get("ok")),
            "web_push_missing_permission": web_push_missing_permission,
            "web_push_errors": list(dict.fromkeys(web_push_errors))[:5],
        }
    _run_background_delivery(dispatch_signal_external_delivery_batch(external_deliveries))
    return len(signals)


async def broadcast_live_signal(
    db: AsyncSession,
    *,
    users: Iterable[User],
    event_name: str,
    coefficient: Decimal,
    brain_score: Optional[int],
) -> int:
    text = build_live_signal_text(
        event_name=event_name,
        coefficient=coefficient,
        brain_score=brain_score,
    )
    user_list = list(users)
    signals = [
        PersonalSignal(
            user_id=user.telegram_id,
            text=text,
            type="live_signal",
            created_at=datetime.now(timezone.utc),
        )
        for user in user_list
    ]
    db.add_all(signals)
    await db.flush()

    websocket_deliveries: list[tuple[int, dict[str, Any]]] = []
    external_deliveries: list[dict[str, Any]] = []
    for user, signal in zip(user_list, signals):
        payload = signal_to_payload(signal)
        websocket_deliveries.append((user.telegram_id, payload))
        external_deliveries.append({
            "user_id": user.telegram_id,
            "text": text,
            "signal_payload": payload,
            "web_push_subscription": user.web_push_subscription,
            "send_telegram": True,
            "send_web_push": True,
        })
    await dispatch_websocket_delivery_batch(websocket_deliveries)
    _run_background_delivery(dispatch_signal_external_delivery_batch(external_deliveries))
    return len(signals)
