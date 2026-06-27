from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter, deque
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Awaitable, Callable, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings

logger = logging.getLogger("uvicorn")

_lock = RLock()
_queue_depth_history: deque[int] = deque(maxlen=3)
_http_5xx_buckets: Counter[int] = Counter()
_payment_mismatch_events: deque[dict[str, Any]] = deque(maxlen=500)
_integration_failures: Counter[str] = Counter()
_last_alert_logged_at: dict[str, float] = {}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _now_timestamp(now: Optional[datetime] = None) -> float:
    return (now or _utc_now()).timestamp()


def _setting_int(name: str, fallback: int) -> int:
    try:
        return int(getattr(settings, name, fallback))
    except (TypeError, ValueError):
        return fallback


def _setting_float(name: str, fallback: float) -> float:
    try:
        return float(getattr(settings, name, fallback))
    except (TypeError, ValueError):
        return fallback


def _safe_label(value: Any, max_length: int = 120) -> str:
    return " ".join(str(value or "unknown").split())[:max_length]


def _alert(
    *,
    key: str,
    severity: str,
    message: str,
    value: Any,
    threshold: Any,
    labels: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    return {
        "key": key,
        "severity": severity,
        "status": "active",
        "message": message,
        "value": value,
        "threshold": threshold,
        "labels": labels or {},
    }


def _threshold_alert(
    *,
    key: str,
    label: str,
    value: Optional[float],
    warning: float,
    critical: float,
    labels: Optional[dict[str, Any]] = None,
) -> Optional[dict[str, Any]]:
    if value is None:
        return None
    if value >= critical:
        return _alert(
            key=key,
            severity="critical",
            message=f"{label} is above critical threshold",
            value=value,
            threshold=critical,
            labels=labels,
        )
    if value >= warning:
        return _alert(
            key=key,
            severity="warning",
            message=f"{label} is above warning threshold",
            value=value,
            threshold=warning,
            labels=labels,
        )
    return None


def reset_observability_state() -> None:
    with _lock:
        _queue_depth_history.clear()
        _http_5xx_buckets.clear()
        _payment_mismatch_events.clear()
        _integration_failures.clear()
        _last_alert_logged_at.clear()


def record_http_5xx(
    *,
    status_code: int,
    now: Optional[datetime] = None,
    bucket_minute: Optional[int] = None,
) -> None:
    if int(status_code or 0) < 500:
        return
    minute = int(bucket_minute) if bucket_minute is not None else int(_now_timestamp(now) // 60)
    with _lock:
        _http_5xx_buckets[minute] += 1


def get_http_5xx_bucket_snapshot() -> dict[int, int]:
    with _lock:
        return dict(sorted(_http_5xx_buckets.items()))


def record_payment_mismatch(
    provider: str,
    reason: str,
    *,
    attempt_id: Optional[Any] = None,
    payment_id: Optional[Any] = None,
    now: Optional[datetime] = None,
) -> None:
    event = {
        "timestamp": _now_timestamp(now),
        "provider": _safe_label(provider, 40),
        "reason": _safe_label(reason, 80),
        "attempt_id": _safe_label(attempt_id, 80) if attempt_id else None,
        "payment_id": _safe_label(payment_id, 120) if payment_id else None,
    }
    with _lock:
        _payment_mismatch_events.append(event)
    logger.warning(
        "payment_webhook_mismatch",
        extra={
            "event": "payment_webhook_mismatch",
            "provider": event["provider"],
            "reason": event["reason"],
            "attempt_id": event["attempt_id"],
            "payment_id": event["payment_id"],
        },
    )


def record_integration_probe(name: str, *, ok: bool) -> None:
    clean_name = _safe_label(name, 80)
    with _lock:
        if ok:
            _integration_failures.pop(clean_name, None)
        else:
            _integration_failures[clean_name] += 1


def _delivery_alerts(delivery_metrics: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not delivery_metrics:
        return []

    queue_warning = _setting_int("OBSERVABILITY_OUTBOX_QUEUE_WARNING", 50)
    queue_critical = _setting_int("OBSERVABILITY_OUTBOX_QUEUE_CRITICAL", 100)
    oldest_warning = _setting_int("OBSERVABILITY_OUTBOX_OLDEST_PENDING_WARNING_SECONDS", 600)
    oldest_critical = _setting_int("OBSERVABILITY_OUTBOX_OLDEST_PENDING_CRITICAL_SECONDS", 1800)
    retry_warning = _setting_float("OBSERVABILITY_OUTBOX_RETRY_RATE_WARNING", 20.0)
    retry_critical = _setting_float("OBSERVABILITY_OUTBOX_RETRY_RATE_CRITICAL", 40.0)
    fail_warning = _setting_float("OBSERVABILITY_OUTBOX_FAIL_RATE_WARNING", 5.0)
    fail_critical = _setting_float("OBSERVABILITY_OUTBOX_FAIL_RATE_CRITICAL", 10.0)

    alerts: list[dict[str, Any]] = []
    queue_depth = int(delivery_metrics.get("queue_depth") or 0)
    with _lock:
        _queue_depth_history.append(queue_depth)
        queue_depth_history = list(_queue_depth_history)

    for maybe_alert in (
        _threshold_alert(
            key="delivery_outbox.queue_depth",
            label="Delivery outbox queue depth",
            value=queue_depth,
            warning=queue_warning,
            critical=queue_critical,
        ),
        _threshold_alert(
            key="delivery_outbox.oldest_pending_age",
            label="Delivery outbox oldest pending age",
            value=delivery_metrics.get("oldest_pending_age_seconds"),
            warning=oldest_warning,
            critical=oldest_critical,
        ),
        _threshold_alert(
            key="delivery_outbox.retry_rate",
            label="Delivery outbox retry rate",
            value=delivery_metrics.get("retry_rate"),
            warning=retry_warning,
            critical=retry_critical,
        ),
        _threshold_alert(
            key="delivery_outbox.fail_rate",
            label="Delivery outbox fail rate",
            value=delivery_metrics.get("fail_rate"),
            warning=fail_warning,
            critical=fail_critical,
        ),
    ):
        if maybe_alert:
            alerts.append(maybe_alert)

    channel_warning = max(1, int(queue_warning / 2))
    by_channel = delivery_metrics.get("by_channel") or {}
    if isinstance(by_channel, dict):
        for channel, statuses in sorted(by_channel.items()):
            if not isinstance(statuses, dict):
                continue
            channel_depth = int(statuses.get("pending") or 0) + int(statuses.get("retry") or 0)
            maybe_alert = _threshold_alert(
                key="delivery_outbox.channel_queue_depth",
                label="Delivery outbox channel queue depth",
                value=channel_depth,
                warning=channel_warning,
                critical=queue_critical,
                labels={"channel": _safe_label(channel, 80)},
            )
            if maybe_alert:
                alerts.append(maybe_alert)

    if (
        len(queue_depth_history) == 3
        and queue_depth_history[0] < queue_depth_history[1] < queue_depth_history[2]
        and queue_depth_history[2] >= queue_warning
    ):
        alerts.append(
            _alert(
                key="delivery_outbox.queue_growth",
                severity="warning",
                message="Delivery outbox queue depth is growing across consecutive samples",
                value=queue_depth_history[2],
                threshold=queue_warning,
                labels={"samples": queue_depth_history},
            )
        )

    return alerts


def _apply_probe_health(telegram_health: Optional[dict[str, Any]], vk_health: Optional[dict[str, Any]]) -> None:
    if telegram_health is not None and settings.has_real_telegram_token and not settings.TELEGRAM_USE_POLLING:
        webhook_ok = bool(
            telegram_health.get("ok")
            and telegram_health.get("webhook_matches_expected")
            and telegram_health.get("allowed_updates_current")
        )
        record_integration_probe("telegram.webhook", ok=webhook_ok)

    if vk_health is not None and bool(vk_health.get("configured")):
        api_ok = bool(vk_health.get("ok") and vk_health.get("api_probe_ok", vk_health.get("ok")))
        record_integration_probe("vk.api", ok=api_ok)


def _integration_alerts() -> list[dict[str, Any]]:
    consecutive_threshold = _setting_int("OBSERVABILITY_INTEGRATION_FAILURE_CONSECUTIVE", 3)
    alerts: list[dict[str, Any]] = []
    with _lock:
        failures = dict(_integration_failures)
    for name, count in sorted(failures.items()):
        if count < consecutive_threshold:
            continue
        severity = "critical" if count >= consecutive_threshold * 2 else "warning"
        alerts.append(
            _alert(
                key=f"integration.{name}",
                severity=severity,
                message=f"{name} probe failed for {count} consecutive checks",
                value=count,
                threshold=consecutive_threshold,
                labels={"integration": name},
            )
        )
    return alerts


def _payment_mismatch_alerts(now: Optional[datetime]) -> list[dict[str, Any]]:
    window_seconds = _setting_int("OBSERVABILITY_PAYMENT_MISMATCH_WINDOW_SECONDS", 600)
    critical_count = _setting_int("OBSERVABILITY_PAYMENT_MISMATCH_CRITICAL_COUNT", 3)
    cutoff = _now_timestamp(now) - window_seconds
    with _lock:
        while _payment_mismatch_events and float(_payment_mismatch_events[0]["timestamp"]) < cutoff:
            _payment_mismatch_events.popleft()
        events = list(_payment_mismatch_events)
    if not events:
        return []

    providers = sorted({str(event.get("provider") or "unknown") for event in events})
    count = len(events)
    return [
        _alert(
            key="payments.webhook_mismatch",
            severity="critical" if count >= critical_count else "warning",
            message="Payment webhook mismatch events were recorded",
            value=count,
            threshold=critical_count,
            labels={"providers": providers, "window_seconds": window_seconds},
        )
    ]


def _http_5xx_alerts(now: Optional[datetime]) -> list[dict[str, Any]]:
    warning_minutes = _setting_int("OBSERVABILITY_5XX_WARNING_MINUTES", 3)
    critical_minutes = _setting_int("OBSERVABILITY_5XX_CRITICAL_MINUTES", 5)
    current_minute = int(_now_timestamp(now) // 60)
    oldest_kept = current_minute - max(warning_minutes, critical_minutes) - 2
    with _lock:
        for bucket in list(_http_5xx_buckets):
            if bucket < oldest_kept:
                _http_5xx_buckets.pop(bucket, None)
        buckets = dict(_http_5xx_buckets)
    if not buckets:
        return []

    latest_bucket = max(buckets)
    streak = 0
    total = 0
    bucket = latest_bucket
    while buckets.get(bucket, 0) > 0:
        streak += 1
        total += buckets[bucket]
        bucket -= 1

    if streak >= critical_minutes:
        severity = "critical"
        threshold = critical_minutes
    elif streak >= warning_minutes:
        severity = "warning"
        threshold = warning_minutes
    else:
        return []

    return [
        _alert(
            key="http.5xx",
            severity=severity,
            message="HTTP 5xx responses appeared in consecutive minute buckets",
            value=streak,
            threshold=threshold,
            labels={"recent_5xx_count": total},
        )
    ]


def _overall_status(alerts: list[dict[str, Any]]) -> str:
    severities = {alert.get("severity") for alert in alerts}
    if "critical" in severities:
        return "critical"
    if "warning" in severities:
        return "warning"
    return "ok"


def _log_active_alerts(alerts: list[dict[str, Any]], now: Optional[datetime]) -> None:
    cooldown_seconds = _setting_int("OBSERVABILITY_ALERT_LOG_COOLDOWN_SECONDS", 300)
    timestamp = _now_timestamp(now)
    for alert in alerts:
        key = str(alert.get("key") or "unknown")
        with _lock:
            last_logged_at = _last_alert_logged_at.get(key, 0.0)
            if timestamp - last_logged_at < cooldown_seconds:
                continue
            _last_alert_logged_at[key] = timestamp
        logger.log(
            logging.ERROR if alert.get("severity") == "critical" else logging.WARNING,
            "observability_alert_active",
            extra={
                "event": "observability_alert",
                "alert_key": key,
                "severity": alert.get("severity"),
                "value": alert.get("value"),
                "threshold": alert.get("threshold"),
                "labels": alert.get("labels", {}),
            },
        )


def build_observability_alert_payload(
    *,
    delivery_metrics: Optional[dict[str, Any]] = None,
    telegram_health: Optional[dict[str, Any]] = None,
    vk_health: Optional[dict[str, Any]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    now_value = now or _utc_now()
    _apply_probe_health(telegram_health, vk_health)
    alerts = [
        *_delivery_alerts(delivery_metrics),
        *_integration_alerts(),
        *_payment_mismatch_alerts(now_value),
        *_http_5xx_alerts(now_value),
    ]
    _log_active_alerts(alerts, now_value)
    return {
        "generated_at": now_value.isoformat(),
        "overall_status": _overall_status(alerts),
        "alerts": alerts,
    }


async def build_observability_alert_payload_from_db(
    db: AsyncSession,
    *,
    delivery_metrics: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    if delivery_metrics is None:
        from src.services.delivery_outbox import get_delivery_outbox_metrics

        delivery_metrics = await get_delivery_outbox_metrics(db)
    return build_observability_alert_payload(delivery_metrics=delivery_metrics)


async def observability_alert_daemon(
    *,
    delivery_metrics_factory: Callable[[], Awaitable[dict[str, Any]]],
    telegram_health_factory: Callable[[], Awaitable[dict[str, Any]]],
    vk_health_factory: Callable[[], Awaitable[dict[str, Any]]],
) -> None:
    logger.info(
        "observability_alert_daemon_initialized",
        extra={"event": "daemon_initialized", "daemon": "observability_alerts"},
    )
    while True:
        sleep_seconds = max(5.0, _setting_float("OBSERVABILITY_ALERT_INTERVAL_SECONDS", 60.0))
        try:
            delivery_metrics, telegram_health, vk_health = await asyncio.gather(
                delivery_metrics_factory(),
                telegram_health_factory(),
                vk_health_factory(),
            )
            payload = build_observability_alert_payload(
                delivery_metrics=delivery_metrics,
                telegram_health=telegram_health,
                vk_health=vk_health,
            )
            logger.info(
                "observability_alert_daemon_tick",
                extra={
                    "event": "observability_alert_tick",
                    "daemon": "observability_alerts",
                    "overall_status": payload["overall_status"],
                    "alert_count": len(payload["alerts"]),
                },
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(
                "observability_alert_daemon_failed",
                extra={
                    "event": "daemon_tick_failed",
                    "daemon": "observability_alerts",
                    "error_type": type(exc).__name__,
                },
            )
        await asyncio.sleep(sleep_seconds)
