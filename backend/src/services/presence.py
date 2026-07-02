from __future__ import annotations

import logging

from src.core.redis_cache import get_redis_client


logger = logging.getLogger("uvicorn")
PRESENCE_KEY_PREFIX = "presence:user"
PRESENCE_KEY_PATTERN = f"{PRESENCE_KEY_PREFIX}:*"
PRESENCE_TTL_SECONDS = 135


def presence_key(user_id: int) -> str:
    return f"{PRESENCE_KEY_PREFIX}:{int(user_id)}"


async def mark_user_presence(user_id: int) -> bool:
    client = get_redis_client()
    if client is None:
        return False
    try:
        await client.set(presence_key(user_id), "1", ex=PRESENCE_TTL_SECONDS)
    except Exception as exc:
        logger.debug("[Presence] failed to mark user online: %s", exc)
        return False
    return True


async def count_online_users() -> int:
    client = get_redis_client()
    if client is None:
        return 0
    count = 0
    try:
        async for _key in client.scan_iter(match=PRESENCE_KEY_PATTERN, count=200):
            count += 1
    except Exception as exc:
        logger.debug("[Presence] failed to count online users: %s", exc)
        return 0
    return count
