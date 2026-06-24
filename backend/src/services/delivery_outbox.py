import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID, uuid4

from sqlalchemy import and_, func, inspect as sa_inspect, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.models.database import AsyncSessionLocal
from src.models.models import DeliveryOutbox, User
from src.services.system_settings import is_system_setting_enabled
from src.services.telegram_bot import call_telegram_api_async
from src.services.vk_delivery import send_vk_message_to_user

logger = logging.getLogger("uvicorn")

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_RETRY = "retry"
STATUS_SENT = "sent"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"

CHANNEL_TELEGRAM_MESSAGE = "telegram_message"
CHANNEL_VK_MESSAGE = "vk_message"
CHANNEL_WEB_PUSH_SIGNAL = "web_push_signal"
CHANNEL_FORECAST_AUTO_DELIVERY = "forecast_auto_delivery"
CHANNEL_FORECAST_FULL_DELIVERY = "forecast_full_delivery"
PAUSABLE_DELIVERY_CHANNELS = {
    CHANNEL_TELEGRAM_MESSAGE,
    CHANNEL_VK_MESSAGE,
    CHANNEL_FORECAST_AUTO_DELIVERY,
    CHANNEL_FORECAST_FULL_DELIVERY,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return str(error.get("description") or error.get("error") or error)[:2000]
    return str(error or "unknown error")[:2000]


def _next_retry_at(attempt_count: int) -> datetime:
    base_seconds = max(1, int(settings.DELIVERY_OUTBOX_RETRY_BASE_SECONDS))
    delay_seconds = min(base_seconds * (2 ** max(0, attempt_count - 1)), 60 * 60)
    return _now() + timedelta(seconds=delay_seconds)


def _supports_skip_locked(db: AsyncSession) -> bool:
    try:
        return db.get_bind().dialect.name == "postgresql"
    except Exception:
        return False


def _channel_concurrency(channel: str) -> int:
    default_value = max(1, min(int(settings.DELIVERY_OUTBOX_DEFAULT_CONCURRENCY or 1), 50))
    raw_config = str(settings.DELIVERY_OUTBOX_CHANNEL_CONCURRENCY or "")
    for chunk in raw_config.replace(";", ",").split(","):
        if "=" not in chunk:
            continue
        raw_channel, raw_value = chunk.split("=", 1)
        if raw_channel.strip() != channel:
            continue
        try:
            return max(1, min(int(raw_value.strip()), 50))
        except Exception:
            return default_value
    return default_value


def _loaded_attr(instance: Any, attr_name: str) -> Any:
    try:
        state = sa_inspect(instance)
        if attr_name in state.unloaded:
            return None
    except Exception:
        pass
    return getattr(instance, attr_name, None)


async def enqueue_delivery(
    db: AsyncSession,
    *,
    channel: str,
    payload: dict[str, Any],
    user_id: Optional[int] = None,
    personal_signal_id: Optional[int] = None,
    forecast_request_id: Optional[Any] = None,
    dedupe_key: Optional[str] = None,
    max_attempts: int = 5,
    run_after: Optional[datetime] = None,
) -> DeliveryOutbox:
    next_attempt_at = run_after or _now()
    max_attempts_value = max(1, int(max_attempts or 1))
    broadcasts_paused = channel in PAUSABLE_DELIVERY_CHANNELS and await is_system_setting_enabled(db, "PAUSE_BROADCASTS")

    if dedupe_key and hasattr(db, "execute"):
        existing_result = await db.execute(
            select(DeliveryOutbox).filter(DeliveryOutbox.dedupe_key == dedupe_key)
        )
        existing_scalars = getattr(existing_result, "scalars", None)
        existing = existing_scalars().first() if callable(existing_scalars) else None
        if existing:
            return existing

        try:
            dialect_name = db.get_bind().dialect.name
        except Exception:
            dialect_name = ""
        insert_builder = {
            "postgresql": postgresql_insert,
            "sqlite": sqlite_insert,
        }.get(dialect_name)
        if insert_builder is not None:
            delivery_id = uuid4()
            statement = (
                insert_builder(DeliveryOutbox)
                .values(
                    id=delivery_id,
                    channel=channel,
                    status=STATUS_CANCELLED if broadcasts_paused else STATUS_PENDING,
                    user_id=user_id,
                    personal_signal_id=personal_signal_id,
                    forecast_request_id=forecast_request_id,
                    payload=payload,
                    dedupe_key=dedupe_key,
                    attempt_count=0,
                    max_attempts=max_attempts_value,
                    next_attempt_at=next_attempt_at,
                    last_error="Broadcast delivery paused by admin setting" if broadcasts_paused else None,
                )
                .on_conflict_do_nothing(index_elements=["dedupe_key"])
            )
            await db.execute(statement)
            upsert_result = await db.execute(
                select(DeliveryOutbox).filter(DeliveryOutbox.dedupe_key == dedupe_key)
            )
            upserted = upsert_result.scalars().first()
            if upserted:
                return upserted

    delivery = DeliveryOutbox(
        channel=channel,
        status=STATUS_CANCELLED if broadcasts_paused else STATUS_PENDING,
        user_id=user_id,
        personal_signal_id=personal_signal_id,
        forecast_request_id=forecast_request_id,
        payload=payload,
        dedupe_key=dedupe_key,
        max_attempts=max_attempts_value,
        next_attempt_at=next_attempt_at,
        last_error="Broadcast delivery paused by admin setting" if broadcasts_paused else None,
    )
    db.add(delivery)
    return delivery


async def enqueue_signal_external_delivery_batch(
    db: AsyncSession,
    deliveries: list[dict[str, Any]],
) -> list[DeliveryOutbox]:
    outbox_items: list[DeliveryOutbox] = []
    for delivery in deliveries:
        user_id = int(delivery["user_id"])
        signal_payload = dict(delivery["signal_payload"])
        signal_id = signal_payload.get("id")
        personal_signal_id = int(signal_id) if signal_id is not None else None

        if delivery.get("send_telegram") and is_personal_telegram_user_id(user_id):
            outbox_items.append(
                await enqueue_delivery(
                    db,
                    channel=CHANNEL_TELEGRAM_MESSAGE,
                    user_id=user_id,
                    personal_signal_id=personal_signal_id,
                    dedupe_key=f"personal_signal:{personal_signal_id}:telegram" if personal_signal_id else None,
                    payload={
                        "method": "sendMessage",
                        "payload": {
                            "chat_id": user_id,
                            "text": delivery["text"],
                            "disable_web_page_preview": True,
                        },
                    },
                )
            )

        if delivery.get("send_web_push"):
            outbox_items.append(
                await enqueue_delivery(
                    db,
                    channel=CHANNEL_WEB_PUSH_SIGNAL,
                    user_id=user_id,
                    personal_signal_id=personal_signal_id,
                    dedupe_key=f"personal_signal:{personal_signal_id}:web_push" if personal_signal_id else None,
                    payload={
                        "subscription": delivery.get("web_push_subscription"),
                        "signal_payload": signal_payload,
                    },
                )
            )
    return outbox_items


async def claim_due_deliveries(db: AsyncSession, *, limit: int) -> list[DeliveryOutbox]:
    now = _now()
    stale_before = now - timedelta(seconds=max(30, int(settings.DELIVERY_OUTBOX_STALE_LOCK_SECONDS)))
    due_filter = or_(
        and_(DeliveryOutbox.status == STATUS_PENDING, DeliveryOutbox.next_attempt_at <= now),
        and_(DeliveryOutbox.status == STATUS_RETRY, DeliveryOutbox.next_attempt_at <= now),
        and_(DeliveryOutbox.status == STATUS_PROCESSING, DeliveryOutbox.locked_at < stale_before),
    )
    statement = (
        select(DeliveryOutbox)
        .filter(due_filter)
        .order_by(DeliveryOutbox.next_attempt_at.asc(), DeliveryOutbox.created_at.asc())
        .limit(limit)
    )
    if _supports_skip_locked(db):
        statement = statement.with_for_update(skip_locked=True)

    result = await db.execute(statement)
    deliveries = list(result.scalars().all())
    for delivery in deliveries:
        delivery.status = STATUS_PROCESSING
        delivery.locked_at = now
        delivery.updated_at = now
    await db.flush()
    return deliveries


async def mark_delivery_sent(db: AsyncSession, delivery: DeliveryOutbox, result: dict[str, Any]) -> None:
    now = _now()
    delivery.status = STATUS_SENT
    delivery.sent_at = now
    delivery.locked_at = None
    delivery.last_error = None
    delivery.updated_at = now
    payload = dict(delivery.payload or {})
    payload["last_result"] = {key: value for key, value in result.items() if key != "result"}
    delivery.payload = payload


async def mark_delivery_failed(db: AsyncSession, delivery: DeliveryOutbox, error: Any) -> None:
    now = _now()
    delivery.attempt_count = int(delivery.attempt_count or 0) + 1
    delivery.locked_at = None
    delivery.last_error = _error_text(error)
    delivery.updated_at = now
    if delivery.attempt_count >= int(delivery.max_attempts or 1):
        delivery.status = STATUS_FAILED
        delivery.next_attempt_at = now
        return
    delivery.status = STATUS_RETRY
    delivery.next_attempt_at = _next_retry_at(delivery.attempt_count)


async def dispatch_delivery(delivery: DeliveryOutbox, db: Optional[AsyncSession] = None) -> dict[str, Any]:
    payload = delivery.payload or {}
    if delivery.channel == CHANNEL_TELEGRAM_MESSAGE:
        method = str(payload.get("method") or "").strip()
        method_payload = payload.get("payload")
        if not method or not isinstance(method_payload, dict):
            return {"ok": False, "description": "Invalid Telegram outbox payload"}
        return await call_telegram_api_async(method, method_payload)

    if delivery.channel == CHANNEL_VK_MESSAGE:
        message = str(payload.get("message") or "").strip()
        if not message:
            return {"ok": False, "description": "Invalid VK outbox payload"}

        user = _loaded_attr(delivery, "user")
        if user is None and db is not None and delivery.user_id is not None:
            user_result = await db.execute(select(User).filter(User.telegram_id == delivery.user_id))
            user = user_result.scalars().first()
        if user is None:
            return {"ok": False, "description": "VK recipient was not found"}

        return await asyncio.to_thread(
            send_vk_message_to_user,
            user,
            message,
            keyboard=payload.get("keyboard"),
            image_path=payload.get("image_path"),
        )

    if delivery.channel == CHANNEL_WEB_PUSH_SIGNAL:
        from src.services.signals import send_web_push_subscription_to_user

        signal_payload = payload.get("signal_payload")
        if not isinstance(signal_payload, dict):
            return {"ok": False, "description": "Invalid Web Push signal payload"}
        return await send_web_push_subscription_to_user(
            int(delivery.user_id or signal_payload.get("user_id") or 0),
            payload.get("subscription"),
            signal_payload,
        )

    if delivery.channel == CHANNEL_FORECAST_AUTO_DELIVERY:
        from src.services.forecast_delivery import auto_deliver_forecast_request_for_request

        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return {"ok": False, "description": "Invalid forecast auto-delivery payload"}
        return await auto_deliver_forecast_request_for_request(
            UUID(request_id),
            delivery_method=str(payload.get("delivery_method") or "auto"),
        )

    if delivery.channel == CHANNEL_FORECAST_FULL_DELIVERY:
        from src.services.forecast_delivery import dispatch_forecast_full_delivery_from_outbox

        request_id = str(payload.get("request_id") or "").strip()
        if not request_id:
            return {"ok": False, "description": "Invalid forecast full-delivery payload"}
        return await dispatch_forecast_full_delivery_from_outbox(
            UUID(request_id),
            delivery_method=str(payload.get("delivery_method") or "auto"),
        )

    return {"ok": False, "description": f"Unsupported delivery channel: {delivery.channel}"}


async def process_claimed_delivery(delivery_id: UUID) -> str:
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(DeliveryOutbox).filter(DeliveryOutbox.id == delivery_id))
        delivery = result.scalars().first()
        if delivery is None:
            return "failed"

        try:
            dispatch_result = await dispatch_delivery(delivery, db=db)
            if dispatch_result.get("ok"):
                await mark_delivery_sent(db, delivery, dispatch_result)
                await db.commit()
                return "sent"

            await mark_delivery_failed(db, delivery, dispatch_result)
            status_key = "failed" if delivery.status == STATUS_FAILED else "retry"
            await db.commit()
            return status_key
        except Exception as exc:
            logger.exception("[DeliveryOutbox] Dispatch crashed for %s: %s", delivery.id, exc)
            await mark_delivery_failed(db, delivery, exc)
            status_key = "failed" if delivery.status == STATUS_FAILED else "retry"
            await db.commit()
            return status_key


async def process_delivery_outbox_batch(*, limit: Optional[int] = None) -> dict[str, int]:
    batch_limit = max(1, int(limit or settings.DELIVERY_OUTBOX_BATCH_SIZE))
    async with AsyncSessionLocal() as db:
        deliveries = await claim_due_deliveries(db, limit=batch_limit)
        delivery_refs = [(delivery.id, delivery.channel) for delivery in deliveries]
        await db.commit()

    report = {"claimed": len(delivery_refs), "sent": 0, "retry": 0, "failed": 0}
    if not delivery_refs:
        return report

    semaphores: dict[str, asyncio.Semaphore] = {}

    async def run_one(delivery_id: UUID, channel: str) -> str:
        semaphore = semaphores.setdefault(channel, asyncio.Semaphore(_channel_concurrency(channel)))
        async with semaphore:
            return await process_claimed_delivery(delivery_id)

    results = await asyncio.gather(
        *(run_one(delivery_id, channel) for delivery_id, channel in delivery_refs)
    )
    for status_key in results:
        if status_key in report:
            report[status_key] += 1
    return report


async def get_delivery_outbox_metrics(db: AsyncSession) -> dict[str, Any]:
    now = _now()
    status_rows = await db.execute(
        select(DeliveryOutbox.status, func.count(DeliveryOutbox.id)).group_by(DeliveryOutbox.status)
    )
    by_status = {str(status): int(count or 0) for status, count in status_rows.all()}

    channel_rows = await db.execute(
        select(DeliveryOutbox.channel, DeliveryOutbox.status, func.count(DeliveryOutbox.id))
        .group_by(DeliveryOutbox.channel, DeliveryOutbox.status)
    )
    by_channel: dict[str, dict[str, int]] = {}
    for channel, status, count in channel_rows.all():
        by_channel.setdefault(str(channel), {})[str(status)] = int(count or 0)

    oldest_result = await db.execute(
        select(func.min(DeliveryOutbox.created_at)).filter(
            DeliveryOutbox.status.in_([STATUS_PENDING, STATUS_RETRY])
        )
    )
    oldest_pending_at = oldest_result.scalar()
    oldest_pending_age_seconds = None
    if oldest_pending_at:
        if oldest_pending_at.tzinfo is None:
            oldest_pending_at = oldest_pending_at.replace(tzinfo=timezone.utc)
        oldest_pending_age_seconds = max(0, int((now - oldest_pending_at).total_seconds()))

    queue_depth = int(by_status.get(STATUS_PENDING, 0) + by_status.get(STATUS_RETRY, 0))
    terminal_total = int(by_status.get(STATUS_SENT, 0) + by_status.get(STATUS_FAILED, 0))
    active_total = max(1, queue_depth + int(by_status.get(STATUS_PROCESSING, 0)))

    return {
        "queue_depth": queue_depth,
        "oldest_pending_age_seconds": oldest_pending_age_seconds,
        "retry_rate": round(int(by_status.get(STATUS_RETRY, 0)) / active_total * 100, 2),
        "fail_rate": round(int(by_status.get(STATUS_FAILED, 0)) / terminal_total * 100, 2) if terminal_total else 0.0,
        "by_status": by_status,
        "by_channel": by_channel,
    }


async def delivery_outbox_daemon() -> None:
    print("[Daemon] Delivery outbox daemon initialized.")
    while True:
        try:
            report = await process_delivery_outbox_batch()
            sleep_seconds = 0.2 if report["claimed"] else max(1.0, float(settings.DELIVERY_OUTBOX_IDLE_SECONDS))
        except Exception as exc:
            logger.exception("[DeliveryOutbox] Daemon tick failed: %s", exc)
            sleep_seconds = max(1.0, float(settings.DELIVERY_OUTBOX_IDLE_SECONDS))
        await asyncio.sleep(sleep_seconds)
