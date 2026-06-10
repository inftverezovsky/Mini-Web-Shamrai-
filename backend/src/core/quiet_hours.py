from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from src.core.config import settings


DEFAULT_NIGHT_MODE_START = "23:00"
DEFAULT_NIGHT_MODE_END = "08:00"


def normalize_quiet_time(value: Optional[str], fallback: str = DEFAULT_NIGHT_MODE_START) -> str:
    raw = str(value if value is not None else fallback).strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError("time must use HH:MM format")

    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise ValueError("time must use HH:MM format") from exc

    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise ValueError("time must be between 00:00 and 23:59")

    return f"{hour:02d}:{minute:02d}"


def quiet_time_or_default(value: Optional[str], fallback: str) -> str:
    try:
        return normalize_quiet_time(value, fallback)
    except ValueError:
        return fallback


def quiet_time_to_minutes(value: Optional[str], fallback: str) -> int:
    normalized = normalize_quiet_time(value, fallback)
    hour, minute = normalized.split(":")
    return int(hour) * 60 + int(minute)


def current_notification_time() -> datetime:
    timezone_name = (settings.NOTIFICATION_TIMEZONE or "Europe/Moscow").strip() or "Europe/Moscow"
    try:
        return datetime.now(ZoneInfo(timezone_name))
    except Exception:
        return datetime.now(timezone.utc)


def is_within_quiet_hours(
    now: datetime,
    start: Optional[str],
    end: Optional[str],
) -> bool:
    start_minutes = quiet_time_to_minutes(start, DEFAULT_NIGHT_MODE_START)
    end_minutes = quiet_time_to_minutes(end, DEFAULT_NIGHT_MODE_END)
    if start_minutes == end_minutes:
        return False

    current_minutes = now.hour * 60 + now.minute
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def user_is_in_quiet_hours(user: object, now: Optional[datetime] = None) -> bool:
    if not getattr(user, "is_night_mode", False):
        return False

    return is_within_quiet_hours(
        now or current_notification_time(),
        getattr(user, "night_mode_start", None),
        getattr(user, "night_mode_end", None),
    )
