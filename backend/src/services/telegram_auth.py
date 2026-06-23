import asyncio
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


TELEGRAM_AUTH_START_PREFIX = "auth_"
TELEGRAM_AUTH_SESSION_TTL = timedelta(minutes=5)


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
        _sessions[auth_token] = session
        return session


async def get_telegram_bot_auth_session(auth_token: str) -> Optional[TelegramBotAuthSession]:
    async with _lock:
        _cleanup_expired()
        session = _sessions.get(auth_token)
        if not session:
            return None
        if _is_expired(session):
            _sessions.pop(auth_token, None)
            session.status = "expired"
        return session


async def confirm_telegram_bot_auth_session(auth_token: str, telegram_user: dict[str, Any]) -> bool:
    async with _lock:
        _cleanup_expired()
        session = _sessions.get(auth_token)
        if not session or _is_expired(session):
            return False

        telegram_id = int(telegram_user.get("id") or 0)
        if telegram_id <= 0:
            return False

        session.telegram_user = dict(telegram_user)
        session.status = "confirmed"
        session.confirmed_at = _now()
        return True


async def consume_telegram_bot_auth_session(auth_token: str) -> None:
    async with _lock:
        session = _sessions.get(auth_token)
        if session:
            session.status = "consumed"
        _sessions.pop(auth_token, None)


def telegram_auth_start_param(auth_token: str) -> str:
    return f"{TELEGRAM_AUTH_START_PREFIX}{auth_token}"


def parse_telegram_auth_start_param(start_param: str) -> Optional[str]:
    clean_param = (start_param or "").strip()
    if not clean_param.startswith(TELEGRAM_AUTH_START_PREFIX):
        return None
    auth_token = clean_param.removeprefix(TELEGRAM_AUTH_START_PREFIX).strip()
    return auth_token or None
