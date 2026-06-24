import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.core.redis_cache import cache_delete, cache_get_json, cache_set_json


VK_AUTH_FLOW_TTL = timedelta(minutes=10)
VK_AUTH_FLOW_CACHE_PREFIX = "vk_auth_flow:v1"


@dataclass
class VkAuthFlow:
    action: str
    state: str
    code_verifier: str
    redirect_uri: str
    expires_at: datetime
    source_user_id: Optional[int] = None


_flows: dict[str, VkAuthFlow] = {}
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(flow: VkAuthFlow, now: Optional[datetime] = None) -> bool:
    return (now or _now()) >= flow.expires_at


def _cleanup_expired(now: Optional[datetime] = None) -> None:
    current_time = now or _now()
    expired_states = [
        state
        for state, flow in _flows.items()
        if _is_expired(flow, current_time)
    ]
    for state in expired_states:
        _flows.pop(state, None)


def _cache_key(state: str) -> str:
    return f"{VK_AUTH_FLOW_CACHE_PREFIX}:{state}"


def _datetime_to_iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def _datetime_from_iso(value: Any) -> Optional[datetime]:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _flow_to_payload(flow: VkAuthFlow) -> dict[str, Any]:
    return {
        "action": flow.action,
        "state": flow.state,
        "code_verifier": flow.code_verifier,
        "redirect_uri": flow.redirect_uri,
        "expires_at": _datetime_to_iso(flow.expires_at),
        "source_user_id": flow.source_user_id,
    }


def _flow_from_payload(payload: Any) -> Optional[VkAuthFlow]:
    if not isinstance(payload, dict):
        return None

    state = str(payload.get("state") or "").strip()
    expires_at = _datetime_from_iso(payload.get("expires_at"))
    if not state or expires_at is None:
        return None

    raw_source_user_id = payload.get("source_user_id")
    try:
        source_user_id = int(raw_source_user_id) if raw_source_user_id is not None else None
    except (TypeError, ValueError):
        source_user_id = None

    return VkAuthFlow(
        action=str(payload.get("action") or ""),
        state=state,
        code_verifier=str(payload.get("code_verifier") or ""),
        redirect_uri=str(payload.get("redirect_uri") or ""),
        expires_at=expires_at,
        source_user_id=source_user_id,
    )


def _ttl_seconds(flow: VkAuthFlow) -> int:
    return max(1, int((flow.expires_at - _now()).total_seconds()))


async def _store_flow(flow: VkAuthFlow) -> None:
    _flows[flow.state] = flow
    await cache_set_json(
        _cache_key(flow.state),
        _flow_to_payload(flow),
        ttl_seconds=_ttl_seconds(flow),
    )


async def _load_cached_flow(state: str) -> Optional[VkAuthFlow]:
    flow = _flow_from_payload(await cache_get_json(_cache_key(state)))
    if flow is None:
        return None
    if _is_expired(flow):
        await cache_delete(_cache_key(state))
        return None
    _flows[state] = flow
    return flow


async def store_vk_auth_flow(
    *,
    action: str,
    state: str,
    code_verifier: str,
    redirect_uri: str,
    source_user_id: Optional[int] = None,
) -> VkAuthFlow:
    async with _lock:
        _cleanup_expired()
        flow = VkAuthFlow(
            action=action,
            state=state,
            code_verifier=code_verifier,
            redirect_uri=redirect_uri,
            expires_at=_now() + VK_AUTH_FLOW_TTL,
            source_user_id=source_user_id,
        )
        await _store_flow(flow)
        return flow


async def consume_vk_auth_flow(state: str) -> Optional[VkAuthFlow]:
    clean_state = str(state or "").strip()
    if not clean_state:
        return None

    async with _lock:
        _cleanup_expired()
        flow = _flows.pop(clean_state, None) or await _load_cached_flow(clean_state)
        _flows.pop(clean_state, None)
        await cache_delete(_cache_key(clean_state))

        if not flow or _is_expired(flow):
            return None
        return flow
