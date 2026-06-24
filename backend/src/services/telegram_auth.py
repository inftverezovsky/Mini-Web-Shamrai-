import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.core.redis_cache import cache_delete, cache_get_json, cache_set_json


TELEGRAM_AUTH_START_PREFIX = "auth_"
TELEGRAM_AUTH_SESSION_TTL = timedelta(minutes=5)
TELEGRAM_AUTH_SESSION_CACHE_PREFIX = "telegram_auth_session:v1"


@dataclass
class TelegramBotAuthSession:
    auth_token: str
    expires_at: datetime
    status: str = "pending"
    telegram_user: Optional[dict[str, Any]] = None
    source_user_id: Optional[int] = None
    confirmed_at: Optional[datetime] = None


_sessions: dict[str, TelegramBotAuthSession] = {}
_lock = asyncio.Lock()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(session: TelegramBotAuthSession, now: Optional[datetime] = None) -> bool:
    return (now or _now()) >= session.expires_at


def _cleanup_expired(now: Optional[datetime] = None) -> None:
    current_time = now or _now()
    expired_tokens = [
        token
        for token, session in _sessions.items()
        if _is_expired(session, current_time) or session.status == "consumed"
    ]
    for token in expired_tokens:
        _sessions.pop(token, None)


def _cache_key(auth_token: str) -> str:
    return f"{TELEGRAM_AUTH_SESSION_CACHE_PREFIX}:{auth_token}"


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


def _session_to_payload(session: TelegramBotAuthSession) -> dict[str, Any]:
    return {
        "auth_token": session.auth_token,
        "expires_at": _datetime_to_iso(session.expires_at),
        "status": session.status,
        "telegram_user": session.telegram_user,
        "source_user_id": session.source_user_id,
        "confirmed_at": _datetime_to_iso(session.confirmed_at),
    }


def _session_from_payload(payload: Any) -> Optional[TelegramBotAuthSession]:
    if not isinstance(payload, dict):
        return None

    auth_token = str(payload.get("auth_token") or "").strip()
    expires_at = _datetime_from_iso(payload.get("expires_at"))
    if not auth_token or expires_at is None:
        return None

    telegram_user = payload.get("telegram_user")
    raw_source_user_id = payload.get("source_user_id")
    try:
        source_user_id = int(raw_source_user_id) if raw_source_user_id is not None else None
    except (TypeError, ValueError):
        source_user_id = None
    return TelegramBotAuthSession(
        auth_token=auth_token,
        expires_at=expires_at,
        status=str(payload.get("status") or "pending"),
        telegram_user=telegram_user if isinstance(telegram_user, dict) else None,
        source_user_id=source_user_id,
        confirmed_at=_datetime_from_iso(payload.get("confirmed_at")),
    )


def _ttl_seconds(session: TelegramBotAuthSession) -> int:
    return max(1, int((session.expires_at - _now()).total_seconds()))


async def _store_session(session: TelegramBotAuthSession) -> None:
    _sessions[session.auth_token] = session
    await cache_set_json(
        _cache_key(session.auth_token),
        _session_to_payload(session),
        ttl_seconds=_ttl_seconds(session),
    )


async def _load_cached_session(auth_token: str) -> Optional[TelegramBotAuthSession]:
    session = _session_from_payload(await cache_get_json(_cache_key(auth_token)))
    if session is None:
        return None
    if _is_expired(session):
        await cache_delete(_cache_key(auth_token))
        return None
    _sessions[auth_token] = session
    return session


async def create_telegram_bot_auth_session(source_user_id: Optional[int] = None) -> TelegramBotAuthSession:
    async with _lock:
        _cleanup_expired()
        auth_token = secrets.token_urlsafe(24)
        while auth_token in _sessions:
            auth_token = secrets.token_urlsafe(24)

        session = TelegramBotAuthSession(
            auth_token=auth_token,
            expires_at=_now() + TELEGRAM_AUTH_SESSION_TTL,
            source_user_id=source_user_id,
        )
        await _store_session(session)
        return session


async def get_telegram_bot_auth_session(auth_token: str) -> Optional[TelegramBotAuthSession]:
    async with _lock:
        _cleanup_expired()
        session = _sessions.get(auth_token) or await _load_cached_session(auth_token)
        if not session:
            return None
        if _is_expired(session):
            _sessions.pop(auth_token, None)
            await cache_delete(_cache_key(auth_token))
            session.status = "expired"
        return session


async def confirm_telegram_bot_auth_session(auth_token: str, telegram_user: dict[str, Any]) -> bool:
    async with _lock:
        _cleanup_expired()
        session = _sessions.get(auth_token) or await _load_cached_session(auth_token)
        if not session or _is_expired(session):
            return False

        telegram_id = int(telegram_user.get("id") or 0)
        if telegram_id <= 0:
            return False

        session.telegram_user = dict(telegram_user)
        session.status = "confirmed"
        session.confirmed_at = _now()
        await _store_session(session)
        return True


async def consume_telegram_bot_auth_session(auth_token: str) -> None:
    async with _lock:
        session = _sessions.get(auth_token)
        if session:
            session.status = "consumed"
        _sessions.pop(auth_token, None)
        await cache_delete(_cache_key(auth_token))


def telegram_auth_start_param(auth_token: str) -> str:
    return f"{TELEGRAM_AUTH_START_PREFIX}{auth_token}"


def parse_telegram_auth_start_param(start_param: str) -> Optional[str]:
    clean_param = (start_param or "").strip()
    if not clean_param.startswith(TELEGRAM_AUTH_START_PREFIX):
        return None
    auth_token = clean_param.removeprefix(TELEGRAM_AUTH_START_PREFIX).strip()
    return auth_token or None
