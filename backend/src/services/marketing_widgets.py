from __future__ import annotations

import hashlib
from copy import deepcopy
from datetime import datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.core.roles import is_staff_role
from src.models.models import MarketingRewardEvent, MarketingWidgetConfig, User
from src.services.marketing_risk import evaluate_marketing_reward_risk


class MarketingWidgetDisabledError(ValueError):
    pass


class MarketingWidgetLimitError(ValueError):
    pass


DEFAULT_MARKETING_WIDGETS: list[dict[str, Any]] = [
    {
        "key": "wheel_of_fortune",
        "title": "Колесо Фортуны",
        "description": "Рулетка с призами. Пользователь крутит колесо раз в неделю.",
        "is_enabled": True,
        "position": 0,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 168,       # 7 дней
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "none",       # Награды настраиваются отдельно в wheel-config
        "reward_value": 0,
        "promo_valid_hours": 168,     # Промокод действует 7 дней
        "settings_json": {},
    },
    {
        "key": "daily_spin",
        "title": "Ежедневный бонус",
        "description": "Ежедневная награда за вход: матч или персональный промокод.",
        "is_enabled": False,
        "position": 10,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 24,        # 1 день
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "mixed",
        "reward_value": 25,
        "promo_valid_hours": 24,
        "settings_json": {"free_bet_weight": 50},
    },
    {
        "key": "swipe",
        "title": "Свайп-прогноз",
        "description": "Свайп карточек матчей. Угадал исход — получил промокод.",
        "is_enabled": False,
        "position": 20,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 0,
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "discount",
        "reward_value": 50,
        "promo_valid_hours": 24,
        "settings_json": {},
    },
    {
        "key": "quiz",
        "title": "Квиз",
        "description": "Тест на знание спорта. За правильные ответы — промокод.",
        "is_enabled": False,
        "position": 30,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 0,
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "discount",
        "reward_value": 30,
        "promo_valid_hours": 48,
        "settings_json": {},
    },
    {
        "key": "pvp",
        "title": "PvP Голосование",
        "description": "Голосование за исход матча между пользователями.",
        "is_enabled": False,
        "position": 40,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 0,
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "none",
        "reward_value": 0,
        "promo_valid_hours": 0,
        "settings_json": {},
    },
    {
        "key": "marathon",
        "title": "Марафон ставок",
        "description": "Серия активностей на протяжении нескольких дней.",
        "is_enabled": False,
        "position": 50,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 0,
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "none",
        "reward_value": 0,
        "promo_valid_hours": 0,
        "settings_json": {},
    },
    {
        "key": "crowd_bet",
        "title": "Совместный прогноз",
        "description": "Пользователи скидываются на VIP-прогноз через Telegram Stars.",
        "is_enabled": False,
        "position": 60,
        "audience": "all",
        "starts_at": None,
        "ends_at": None,
        "cooldown_hours": 0,
        "per_user_limit": 0,
        "global_daily_limit": 0,
        "reward_type": "none",
        "reward_value": 0,
        "promo_valid_hours": 0,
        "settings_json": {},
    },
]

DEFAULT_WIDGETS_BY_KEY = {widget["key"]: widget for widget in DEFAULT_MARKETING_WIDGETS}


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value and hasattr(value, "isoformat") else None


def _day_start(now: datetime) -> datetime:
    return datetime.combine(now.date(), time.min, tzinfo=timezone.utc)


def _row_to_payload(row: MarketingWidgetConfig) -> dict[str, Any]:
    payload = deepcopy(DEFAULT_WIDGETS_BY_KEY.get(row.key, {"key": row.key, "title": row.key, "description": ""}))
    payload.update({
        "key": row.key,
        "is_enabled": bool(row.is_enabled),
        "position": int(row.position or 0),
        "audience": row.audience or "all",
        "starts_at": _serialize_datetime(row.starts_at),
        "ends_at": _serialize_datetime(row.ends_at),
        "cooldown_hours": int(row.cooldown_hours or 0),
        "per_user_limit": int(row.per_user_limit or 0),
        "global_daily_limit": int(row.global_daily_limit or 0),
        "reward_type": row.reward_type or "none",
        "reward_value": int(row.reward_value or 0),
        "promo_valid_hours": int(row.promo_valid_hours or 0),
        "settings_json": row.settings_json or {},
        "updated_by": row.updated_by,
        "updated_at": _serialize_datetime(row.updated_at),
    })
    return payload


def _default_payload(key: str) -> dict[str, Any]:
    return deepcopy(DEFAULT_WIDGETS_BY_KEY[key])


def _coerce_datetime(value: str | datetime | None) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _is_in_time_window(payload: dict[str, Any], now: datetime) -> bool:
    starts_at = _coerce_datetime(payload.get("starts_at"))
    ends_at = _coerce_datetime(payload.get("ends_at"))
    if starts_at and now < starts_at:
        return False
    if ends_at and now > ends_at:
        return False
    return True


def _is_in_audience(payload: dict[str, Any], user: User | None) -> bool:
    audience = str(payload.get("audience") or "all")
    if audience == "all":
        return True
    if audience == "staff":
        return bool(user and is_staff_role(user.role))
    if audience == "clients":
        return bool(user and ((user.matches_remaining or 0) > 0 or bool(user.guarantee_active)))
    if audience == "referrals":
        return bool(user and user.referred_by_user_id)
    return True


async def stored_widget_config_count(db: AsyncSession) -> int:
    return int((await db.execute(select(func.count(MarketingWidgetConfig.key)))).scalar() or 0)


async def get_effective_widget_config(db: AsyncSession, widget_key: str) -> dict[str, Any]:
    row = await db.get(MarketingWidgetConfig, widget_key)
    if row:
        return _row_to_payload(row)
    if widget_key not in DEFAULT_WIDGETS_BY_KEY:
        raise MarketingWidgetDisabledError("Неизвестный маркетинговый виджет")
    return _default_payload(widget_key)


async def list_effective_widget_configs(db: AsyncSession) -> list[dict[str, Any]]:
    result = await db.execute(select(MarketingWidgetConfig))
    stored = {row.key: _row_to_payload(row) for row in result.scalars().all()}
    payloads = []
    for default in DEFAULT_MARKETING_WIDGETS:
        payloads.append(stored.get(default["key"], _default_payload(default["key"])))
    for key, payload in stored.items():
        if key not in DEFAULT_WIDGETS_BY_KEY:
            payloads.append(payload)
    return sorted(payloads, key=lambda item: (int(item.get("position") or 0), str(item.get("key") or "")))


async def active_widget_payloads_for_user(db: AsyncSession, user: User | None = None) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    payloads = []
    for payload in await list_effective_widget_configs(db):
        if bool(payload.get("is_enabled")) and _is_in_time_window(payload, now) and _is_in_audience(payload, user):
            payloads.append(payload)
    return payloads


async def is_widget_available_for_user(db: AsyncSession, widget_key: str, user: User | None = None) -> bool:
    now = datetime.now(timezone.utc)
    payload = await get_effective_widget_config(db, widget_key)
    return bool(payload.get("is_enabled")) and _is_in_time_window(payload, now) and _is_in_audience(payload, user)


async def ensure_widget_available(db: AsyncSession, widget_key: str, user: User | None = None) -> dict[str, Any]:
    payload = await get_effective_widget_config(db, widget_key)
    if not await is_widget_available_for_user(db, widget_key, user):
        raise MarketingWidgetDisabledError("Маркетинговый виджет сейчас выключен")
    return payload


def _marketing_reward_lock_id(widget_key: str) -> int:
    digest = hashlib.sha256(f"shamrai:marketing-reward:{widget_key}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


async def _lock_widget_reward_scope(db: AsyncSession, widget_key: str) -> None:
    """Serialize reward eligibility and insertion for one widget transaction."""
    await db.flush()
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        await db.execute(select(func.pg_advisory_xact_lock(_marketing_reward_lock_id(widget_key))))
    await db.execute(
        select(MarketingWidgetConfig.key)
        .filter(MarketingWidgetConfig.key == widget_key)
        .with_for_update()
    )


async def ensure_widget_reward_allowed(db: AsyncSession, widget_key: str, user: User) -> dict[str, Any]:
    await _lock_widget_reward_scope(db, widget_key)
    payload = await ensure_widget_available(db, widget_key, user)
    now = datetime.now(timezone.utc)
    cooldown_hours = max(0, int(payload.get("cooldown_hours") or 0))
    if cooldown_hours > 0:
        last_event = (await db.execute(
            select(MarketingRewardEvent)
            .filter(
                MarketingRewardEvent.user_id == user.telegram_id,
                MarketingRewardEvent.widget_key == widget_key,
            )
            .order_by(MarketingRewardEvent.created_at.desc(), MarketingRewardEvent.id.desc())
        )).scalars().first()
        last_created_at = _coerce_datetime(last_event.created_at) if last_event and last_event.created_at else None
        if last_created_at and last_created_at >= now - timedelta(hours=cooldown_hours):
            raise MarketingWidgetLimitError("Награда по этому виджету пока на cooldown")

    per_user_limit = max(0, int(payload.get("per_user_limit") or 0))
    if per_user_limit > 0:
        user_events = int((await db.execute(
            select(func.count(MarketingRewardEvent.id)).filter(
                MarketingRewardEvent.user_id == user.telegram_id,
                MarketingRewardEvent.widget_key == widget_key,
            )
        )).scalar() or 0)
        if user_events >= per_user_limit:
            raise MarketingWidgetLimitError("Лимит наград по этому виджету для пользователя исчерпан")

    global_daily_limit = max(0, int(payload.get("global_daily_limit") or 0))
    if global_daily_limit > 0:
        day_events = int((await db.execute(
            select(func.count(MarketingRewardEvent.id)).filter(
                MarketingRewardEvent.widget_key == widget_key,
                MarketingRewardEvent.created_at >= _day_start(now),
            )
        )).scalar() or 0)
        if day_events >= global_daily_limit:
            raise MarketingWidgetLimitError("Дневной лимит наград по этому виджету исчерпан")

    return payload


async def record_marketing_reward_event(
    db: AsyncSession,
    *,
    user: User,
    widget_key: str,
    reward_type: str,
    reward_value: int,
    promo_code_id: int | None,
) -> MarketingRewardEvent:
    decision = await evaluate_marketing_reward_risk(db, user=user, widget_key=widget_key)
    event = MarketingRewardEvent(
        user_id=user.telegram_id,
        widget_key=widget_key,
        reward_type=reward_type,
        reward_value=reward_value,
        promo_code_id=promo_code_id,
        risk_status=decision.status,
        risk_reasons=decision.reasons,
    )
    db.add(event)
    await db.flush()
    return event


async def upsert_widget_configs(
    db: AsyncSession,
    *,
    configs: list[dict[str, Any]],
    updated_by: int | None,
) -> list[dict[str, Any]]:
    for item in configs:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        base = deepcopy(DEFAULT_WIDGETS_BY_KEY.get(key, {}))
        merged = {**base, **item}
        row = await db.get(MarketingWidgetConfig, key)
        if not row:
            row = MarketingWidgetConfig(key=key)
            db.add(row)
        row.is_enabled = bool(merged.get("is_enabled"))
        row.position = int(merged.get("position") or 0)
        row.audience = str(merged.get("audience") or "all")[:32]
        row.starts_at = _coerce_datetime(merged.get("starts_at"))
        row.ends_at = _coerce_datetime(merged.get("ends_at"))
        row.cooldown_hours = max(0, int(merged.get("cooldown_hours") or 0))
        row.per_user_limit = max(0, int(merged.get("per_user_limit") or 0))
        row.global_daily_limit = max(0, int(merged.get("global_daily_limit") or 0))
        row.reward_type = str(merged.get("reward_type") or "none")[:32]
        row.reward_value = max(0, int(merged.get("reward_value") or 0))
        row.promo_valid_hours = max(0, int(merged.get("promo_valid_hours") or 0))
        row.settings_json = merged.get("settings_json") if isinstance(merged.get("settings_json"), dict) else {}
        row.updated_by = updated_by
    await db.flush()
    return await list_effective_widget_configs(db)
