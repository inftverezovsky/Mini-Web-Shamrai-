from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable, Optional
from zoneinfo import ZoneInfo

from src.models.models import Bet


MOSCOW_TZ = ZoneInfo("Europe/Moscow")
RESULT_STATUSES = {"win", "loss"}
PAID_ACCESS_TYPES = {"paid_match", "single_bet_purchase", "manual_paid_set"}
EXCLUDED_CLIENT_ACCESS_TYPES = {"admin", "free_bet", "guarantee_replacement", "crowd_pool"}
PERIOD_OPTIONS = {"week", "month", "quarter", "all"}
PERIOD_LABELS = {
    "week": "Текущая неделя",
    "month": "Текущий месяц",
    "quarter": "Текущий квартал",
    "all": "Весь период",
}

MONTH_LABELS = {
    1: "Январь",
    2: "Февраль",
    3: "Март",
    4: "Апрель",
    5: "Май",
    6: "Июнь",
    7: "Июль",
    8: "Август",
    9: "Сентябрь",
    10: "Октябрь",
    11: "Ноябрь",
    12: "Декабрь",
}


def _normalise_bookmaker_key(value: str) -> str:
    return "".join(ch for ch in str(value or "").casefold().replace("ё", "е") if ch.isalnum())


def _prefer_fonbet_bookmakers(bookmakers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    fonbet_items = [
        bookmaker
        for bookmaker in bookmakers
        if _normalise_bookmaker_key(str(bookmaker.get("code", "") or "")) == "fonbet"
        or _normalise_bookmaker_key(str(bookmaker.get("name", "") or "")) == "фонбет"
    ]
    return fonbet_items[:1] if fonbet_items else bookmakers


def _round_decimal(value: Decimal, places: str = "0.01") -> float:
    return float(value.quantize(Decimal(places), rounding=ROUND_HALF_UP))


def _round_percent(value: float) -> float:
    return round(float(value), 2)


def as_moscow_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(MOSCOW_TZ)


def current_period_keys() -> dict[str, str]:
    now = datetime.now(MOSCOW_TZ)
    return {
        "month": now.strftime("%Y-%m"),
        "day": now.strftime("%Y-%m-%d"),
    }


def normalize_period(period: Optional[str]) -> str:
    clean = str(period or "all").strip().lower()
    return clean if clean in PERIOD_OPTIONS else "all"


def period_start(period: Optional[str], *, now: Optional[datetime] = None) -> Optional[datetime]:
    normalized = normalize_period(period)
    if normalized == "all":
        return None

    current = as_moscow_datetime(now or datetime.now(MOSCOW_TZ))
    if not current:
        current = datetime.now(MOSCOW_TZ)

    if normalized == "week":
        week_start = current - timedelta(days=current.weekday())
        return week_start.replace(hour=0, minute=0, second=0, microsecond=0)
    if normalized == "month":
        return current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    quarter_month = ((current.month - 1) // 3) * 3 + 1
    return current.replace(month=quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)


def filter_items_by_period(
    items: Iterable[dict[str, Any]],
    period: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    start = period_start(period, now=now)
    item_list = list(items)
    if not start:
        return item_list

    filtered = []
    for item in item_list:
        try:
            resolved_at = datetime.fromisoformat(item["resolved_at"])
        except (KeyError, TypeError, ValueError):
            continue
        if as_moscow_datetime(resolved_at) >= start:
            filtered.append(item)
    return filtered


def is_paid_client_access(access_type: Optional[str], match_charged: Optional[bool]) -> bool:
    clean_type = str(access_type or "").strip()
    if clean_type in EXCLUDED_CLIENT_ACCESS_TYPES:
        return False
    if bool(match_charged):
        return True
    return clean_type in PAID_ACCESS_TYPES or clean_type.endswith("_single_bet")


def bet_profit_units(status: str, coefficient: Any) -> Decimal:
    if status == "win":
        return Decimal(str(coefficient or "1")) - Decimal("1")
    if status == "loss":
        return Decimal("-1")
    return Decimal("0")


def _bookmaker_items_for_bet(bet: Bet) -> list[dict[str, Any]]:
    bookmakers = []
    seen_ids = set()
    for bookmaker in list(getattr(bet, "bookmakers", None) or []):
        if not bookmaker or bookmaker.id in seen_ids:
            continue
        seen_ids.add(bookmaker.id)
        bookmakers.append({
            "id": bookmaker.id,
            "name": bookmaker.name,
            "code": bookmaker.code,
        })

    fallback = getattr(bet, "bookmaker", None)
    if fallback and fallback.id not in seen_ids:
        bookmakers.append({
            "id": fallback.id,
            "name": fallback.name,
            "code": fallback.code,
        })
    return _prefer_fonbet_bookmakers(bookmakers)


def stat_item_from_bet(
    bet: Bet,
    *,
    access_type: Optional[str] = None,
    match_charged: Optional[bool] = None,
    taken_at: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    if str(getattr(bet, "publication_type", "forecast") or "forecast") != "forecast":
        return None
    if bet.status not in RESULT_STATUSES or not bet.resolved_at:
        return None

    resolved_at_msk = as_moscow_datetime(bet.resolved_at)
    created_at_msk = as_moscow_datetime(bet.created_at)
    taken_at_msk = as_moscow_datetime(taken_at)
    if not resolved_at_msk:
        return None

    coefficient = Decimal(str(bet.coefficient or "0"))
    profit = bet_profit_units(str(bet.status), coefficient)
    bookmakers = _bookmaker_items_for_bet(bet)
    delivery_mode = str(getattr(bet, "delivery_mode", None) or "feed")
    if delivery_mode == "feed":
        source_type = "feed"
    elif delivery_mode == "paid_set":
        source_type = "paid_set"
    else:
        source_type = "private"

    return {
        "id": str(bet.id),
        "event_name": bet.event_name,
        "status": bet.status,
        "coefficient": _round_decimal(coefficient),
        "bookmaker_id": getattr(bet, "bookmaker_id", None),
        "description": getattr(bet, "description", None),
        "profit_units": _round_decimal(profit),
        "resolved_at": resolved_at_msk.isoformat(),
        "created_at": created_at_msk.isoformat() if created_at_msk else None,
        "taken_at": taken_at_msk.isoformat() if taken_at_msk else None,
        "delivery_mode": delivery_mode,
        "source_type": source_type,
        "sport_type": bet.sport_type,
        "outcome": bet.outcome,
        "match_link": getattr(bet, "match_link", None),
        "bookmakers": bookmakers,
        "bookmaker_names": [bookmaker["name"] for bookmaker in bookmakers],
        "bookmaker_links": list(getattr(bet, "bookmaker_links", None) or []),
        "access_type": access_type,
        "match_charged": bool(match_charged) if match_charged is not None else None,
    }


def _empty_summary() -> dict[str, Any]:
    return {
        "bets": 0,
        "wins": 0,
        "losses": 0,
        "winrate": 0.0,
        "roi": 0.0,
        "profit_units": 0.0,
        "average_coefficient": 0.0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
        "current_streak": 0,
        "current_streak_type": None,
    }


def summarize_items(items: Iterable[dict[str, Any]]) -> dict[str, Any]:
    resolved_items = list(items)
    if not resolved_items:
        return _empty_summary()

    total = len(resolved_items)
    wins = sum(1 for item in resolved_items if item["status"] == "win")
    losses = sum(1 for item in resolved_items if item["status"] == "loss")
    profit = sum((Decimal(str(item.get("profit_units") or 0)) for item in resolved_items), Decimal("0"))
    coefficient_sum = sum((Decimal(str(item.get("coefficient") or 0)) for item in resolved_items), Decimal("0"))

    current_type = None
    current_count = 0
    max_win_streak = 0
    max_loss_streak = 0
    last_type = None
    streak_count = 0

    for item in sorted(resolved_items, key=lambda value: value["resolved_at"]):
        result_type = "win" if item["status"] == "win" else "loss"
        if result_type == last_type:
            streak_count += 1
        else:
            last_type = result_type
            streak_count = 1
        if result_type == "win":
            max_win_streak = max(max_win_streak, streak_count)
        else:
            max_loss_streak = max(max_loss_streak, streak_count)
        current_type = result_type
        current_count = streak_count

    return {
        "bets": total,
        "wins": wins,
        "losses": losses,
        "winrate": _round_percent((wins / total * 100) if total else 0),
        "roi": _round_percent((float(profit) / total * 100) if total else 0),
        "profit_units": _round_decimal(profit),
        "average_coefficient": _round_decimal(coefficient_sum / Decimal(total)) if total else 0.0,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "current_streak": current_count,
        "current_streak_type": current_type,
    }


def build_timeline(items: Iterable[dict[str, Any]], *, include_bets: bool = True) -> list[dict[str, Any]]:
    month_days: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
    for item in items:
        resolved_at = datetime.fromisoformat(item["resolved_at"])
        month_key = resolved_at.strftime("%Y-%m")
        day_key = resolved_at.strftime("%Y-%m-%d")
        month_days[month_key][day_key].append(item)

    months = []
    for month_key in sorted(month_days.keys(), reverse=True):
        days_payload = []
        month_items = []
        year, month = [int(part) for part in month_key.split("-")]
        for day_key in sorted(month_days[month_key].keys(), reverse=True):
            day_items = sorted(month_days[month_key][day_key], key=lambda value: value["resolved_at"], reverse=True)
            month_items.extend(day_items)
            days_payload.append({
                "key": day_key,
                "label": datetime.fromisoformat(day_items[0]["resolved_at"]).strftime("%d.%m.%Y"),
                "summary": summarize_items(day_items),
                "bets": day_items if include_bets else [],
            })
        months.append({
            "key": month_key,
            "label": f"{MONTH_LABELS.get(month, month_key)} {year}",
            "summary": summarize_items(month_items),
            "days": days_payload,
        })
    return months


def build_breakdown(
    items: Iterable[dict[str, Any]],
    *,
    key_name: str,
    empty_label: str,
) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        raw_value = item.get(key_name)
        values: list[tuple[str, str]]
        if key_name == "bookmaker_names":
            names = [str(name).strip() for name in raw_value or [] if str(name or "").strip()]
            values = [(name.lower(), name) for name in names] or [("none", empty_label)]
        else:
            label = str(raw_value or "").strip() or empty_label
            values = [(label.lower(), label)]

        for key, label in values:
            if key not in groups:
                groups[key] = {"key": key, "label": label, "items": []}
            groups[key]["items"].append(item)

    return [
        {
            "key": group["key"],
            "label": group["label"],
            "summary": summarize_items(group["items"]),
        }
        for group in sorted(groups.values(), key=lambda value: (-len(value["items"]), value["label"]))
    ]


def build_source_split(items: Iterable[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    item_list = list(items)
    return {
        "all": summarize_items(item_list),
        "feed": summarize_items([item for item in item_list if item.get("source_type") == "feed"]),
        "private": summarize_items([item for item in item_list if item.get("source_type") == "private"]),
        "paid_set": summarize_items([item for item in item_list if item.get("source_type") == "paid_set"]),
    }


def build_performance_payload(
    items: Iterable[dict[str, Any]],
    *,
    include_bets: bool = True,
    period: Optional[str] = "all",
) -> dict[str, Any]:
    normalized_period = normalize_period(period)
    item_list = sorted(filter_items_by_period(items, normalized_period), key=lambda value: value["resolved_at"], reverse=True)
    period_keys = current_period_keys()
    return {
        "period": normalized_period,
        "period_label": PERIOD_LABELS[normalized_period],
        "summary": summarize_items(item_list),
        "source_split": build_source_split(item_list),
        "timeline": build_timeline(item_list, include_bets=include_bets),
        "bookmaker_breakdown": build_breakdown(item_list, key_name="bookmaker_names", empty_label="Без БК"),
        "sport_breakdown": build_breakdown(item_list, key_name="sport_type", empty_label="Без спорта"),
        "default_expanded_month_key": period_keys["month"],
        "default_expanded_day_key": period_keys["day"],
    }


def last_result_codes(items: Iterable[dict[str, Any]], limit: int = 5) -> list[str]:
    return [
        item["status"]
        for item in sorted(items, key=lambda value: value["resolved_at"], reverse=True)[:limit]
    ]


def client_situation(summary: dict[str, Any]) -> dict[str, str]:
    roi = float(summary.get("roi") or 0)
    current_streak = int(summary.get("current_streak") or 0)
    current_type = summary.get("current_streak_type")

    if summary.get("bets", 0) == 0:
        return {
            "code": "no_data",
            "label": "Нет расчетов",
            "tone": "neutral",
            "description": "Пока нет купленных рассчитанных прогнозов.",
        }
    if current_type == "loss" and current_streak >= 3:
        return {
            "code": "attention",
            "label": "Нужно внимание",
            "tone": "danger",
            "description": "Идет серия минусов, клиенту стоит уделить внимание.",
        }
    if roi > 15:
        return {
            "code": "strong_plus",
            "label": "Хороший плюс",
            "tone": "success",
            "description": "Клиент уверенно в плюсе по купленным прогнозам.",
        }
    if roi >= 0:
        return {
            "code": "plus",
            "label": "В плюсе",
            "tone": "success",
            "description": "Положительная динамика без критичной просадки.",
        }
    if roi > -15:
        return {
            "code": "drawdown",
            "label": "Рабочая просадка",
            "tone": "warning",
            "description": "Небольшой минус, ситуация рабочая.",
        }
    return {
        "code": "deep_drawdown",
        "label": "Глубокая просадка",
        "tone": "danger",
        "description": "ROI ниже -15%, клиент в зоне риска.",
    }
