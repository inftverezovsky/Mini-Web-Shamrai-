from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterable
from typing import Any

from src.core.config import settings

try:
    from redis.asyncio import Redis
except Exception:  # pragma: no cover - allows local test envs without redis installed yet.
    Redis = None  # type: ignore[assignment]


logger = logging.getLogger("uvicorn")
_redis_client: "Redis | None" = None
_SIGNAL_PAGE_PREFIX = "hot:v1:personal-signals"
_SIGNAL_INVALIDATION_SESSION_KEY = "redis_signal_page_invalidation_user_ids"
_CACHE_FAILURE_BACKOFF_SECONDS = 5.0
_cache_suspended_until = 0.0


def _redis_enabled() -> bool:
    return bool(settings.REDIS_CACHE_ENABLED and settings.REDIS_URL.strip() and Redis is not None)


def _cache_available_now() -> bool:
    return time.monotonic() >= _cache_suspended_until


def _suspend_cache_temporarily(key: str, exc: Exception) -> None:
    global _cache_suspended_until
    _cache_suspended_until = time.monotonic() + _CACHE_FAILURE_BACKOFF_SECONDS
    logger.debug("[Redis] cache temporarily bypassed after %s failed: %s", key, exc)


def _safe_limit(limit: int | None, default: int = 50) -> int:
    return max(1, min(int(limit or default), 100))


def get_redis_client() -> "Redis | None":
    global _redis_client
    if not _redis_enabled() or not _cache_available_now():
        return None
    if _redis_client is None:
        _redis_client = Redis.from_url(
            settings.REDIS_URL,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=0.08,
            socket_timeout=0.08,
            retry_on_timeout=False,
            health_check_interval=30,
        )
    return _redis_client


def signal_page_cache_key(*, user_id: int, before_id: int | None, limit: int | None) -> str:
    page_cursor = before_id if before_id is not None else "latest"
    return f"{_SIGNAL_PAGE_PREFIX}:{int(user_id)}:before:{page_cursor}:limit:{_safe_limit(limit)}"


def signal_page_cache_pattern(user_id: int) -> str:
    return f"{_SIGNAL_PAGE_PREFIX}:{int(user_id)}:*"


async def cache_get_json(key: str) -> Any | None:
    client = get_redis_client()
    if client is None:
        return None
    try:
        raw_value = await client.get(key)
    except Exception as exc:
        _suspend_cache_temporarily(key, exc)
        return None
    if raw_value is None:
        return None
    try:
        return json.loads(raw_value)
    except json.JSONDecodeError:
        await cache_delete(key)
        return None


async def cache_set_json(key: str, value: Any, ttl_seconds: int | None = None) -> None:
    client = get_redis_client()
    if client is None:
        return
    ttl = max(1, int(ttl_seconds or settings.REDIS_HOT_CACHE_TTL_SECONDS))
    try:
        await client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ttl)
    except Exception as exc:
        _suspend_cache_temporarily(key, exc)


async def cache_delete(*keys: str) -> None:
    client = get_redis_client()
    clean_keys = [key for key in keys if key]
    if client is None or not clean_keys:
        return
    try:
        await client.delete(*clean_keys)
    except Exception as exc:
        _suspend_cache_temporarily(",".join(clean_keys), exc)


async def cache_delete_pattern(pattern: str) -> int:
    client = get_redis_client()
    if client is None:
        return 0
    deleted = 0
    try:
        batch: list[str] = []
        async for key in client.scan_iter(match=pattern, count=100):
            batch.append(str(key))
            if len(batch) >= 100:
                deleted += int(await client.delete(*batch))
                batch.clear()
        if batch:
            deleted += int(await client.delete(*batch))
    except Exception as exc:
        _suspend_cache_temporarily(pattern, exc)
        return 0
    return deleted


async def invalidate_signal_page_cache_for_user(user_id: int) -> int:
    return await cache_delete_pattern(signal_page_cache_pattern(user_id))


async def invalidate_signal_page_cache_for_users(user_ids: Iterable[int]) -> int:
    deleted = 0
    seen: set[int] = set()
    for raw_user_id in user_ids:
        user_id = int(raw_user_id)
        if user_id in seen:
            continue
        seen.add(user_id)
        deleted += await invalidate_signal_page_cache_for_user(user_id)
    return deleted


def queue_signal_page_cache_invalidation(db_session: Any, user_ids: Iterable[int]) -> None:
    session_info = getattr(db_session, "info", None)
    if session_info is None:
        return
    queued = session_info.setdefault(_SIGNAL_INVALIDATION_SESSION_KEY, set())
    queued.update(int(user_id) for user_id in user_ids)


async def flush_signal_page_cache_invalidations(db_session: Any) -> int:
    session_info = getattr(db_session, "info", None)
    if session_info is None:
        return 0
    queued = session_info.pop(_SIGNAL_INVALIDATION_SESSION_KEY, set())
    if not queued:
        return 0
    return await invalidate_signal_page_cache_for_users(queued)
