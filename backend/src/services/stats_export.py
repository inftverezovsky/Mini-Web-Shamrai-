from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Any, Iterable, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.core.config import settings
from src.core.roles import STAFF_ROLES
from src.models.models import Bet, User, user_bets
from src.services.statistics import (
    MONTH_LABELS,
    as_moscow_datetime,
    client_situation,
    is_paid_client_access,
    last_result_codes,
    period_start,
    stat_item_from_bet,
    summarize_items,
)

EXPORT_STATUSES = {"win", "loss", "refund"}
MONEY_FORMAT = '#,##0" ₽"'
FLAT_FORMAT = '0.00" флет"'
PERCENT_FORMAT = "0.0%"
COEF_FORMAT = "0.00"
DATE_FORMAT = "dd.mm.yyyy hh:mm"
CLIENT_EXPORT_STAKE = Decimal("1")

SPORT_ICONS = {
    "Футбол": "⚽",
    "Баскетбол": "🏀",
    "Гандбол": "🤾",
    "Н/Т": "🏓",
    "Футзал": "🥅",
    "Волейбол": "🏐",
    "Киберспорт": "🎮",
    "Теннис": "🎾",
    "Хоккей": "🏒",
    "Бейсбол": "⚾",
    "Ам. футбол": "🏈",
    "Бадминтон": "🏸",
    "Вод. поло": "🤽",
    "Регби": "🏉",
    "Пляж. футб": "🏖",
    "Крикет": "🏏",
    "Бокс": "🥊",
    "Дартс": "🎯",
    "Единоборства": "🥋",
    "Коньки": "⛸",
    "Лыжи/Биатлон": "🎿",
    "Другие": "✨",
}

BOOKMAKER_CODES = {
    "Фонбет": "FB",
    "Винлайн": "WL",
    "ЛигаСтавок": "LS",
    "BetBoom": "BB",
    "Leon": "LN",
    "Olimp": "OL",
    "Olimpbet": "OL",
    "Марафон": "MR",
    "Zenit": "ZN",
}


@dataclass
class StatsExportItem:
    id: str
    resolved_at: datetime
    period_key: str
    period_label: str
    sport_icon: str
    sport_type: str
    event_name: str
    bookmaker_icon: str
    bookmaker_names: list[str]
    coefficient: Decimal
    odds_dropped_to: Optional[Decimal]
    stake_text: str
    status: str
    result_label: str
    turnover: Decimal
    profit: Decimal
    client_name: str = ""
    source_type: str = ""
    access_type: str = ""


@dataclass
class ClientStatsExportGroup:
    user_id: int
    client_name: str
    username: str
    items: list[StatsExportItem]


@dataclass
class ClientInfoExportRow:
    user_id: int
    client_name: str
    username: str
    phone: str
    vk_user_id: str
    is_web_only: bool
    bookmaker_names: str
    client_group: str
    client_tag: str
    created_at: Optional[datetime]
    matches_remaining: int
    guarantee_active: bool
    total_taken_bets: int
    settled_bets: int
    pending_bets: int
    refund_bets: int
    bets: int
    wins: int
    losses: int
    winrate: float
    roi: float
    profit_units: float
    average_coefficient: float
    current_streak: int
    current_streak_type: Optional[str]
    max_win_streak: int
    max_loss_streak: int
    recent_results: list[str]
    situation_code: str
    situation_label: str
    situation_tone: str
    situation_description: str


@dataclass
class ClientRecentBetExportRow:
    user_id: int
    client_name: str
    username: str
    phone: str
    vk_user_id: str
    is_web_only: bool
    client_group: str
    client_tag: str
    matches_remaining: int
    guarantee_active: bool
    taken_at: Optional[datetime]
    event_name: str
    sport_type: str
    bookmaker_names: str
    coefficient: Decimal
    outcome: str
    status: str
    result_label: str
    resolved_at: Optional[datetime]
    source_type: str
    access_type: str
    match_charged: bool
    bet_id: str


def export_unit_stake() -> Decimal:
    return Decimal(str(settings.STATS_EXPORT_UNIT_STAKE_RUB or 10000))


def _round_money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _display_user(user: User) -> str:
    parts = [part for part in [user.first_name, user.last_name] if part]
    if parts:
        return " ".join(parts)
    if user.username:
        return f"@{user.username}"
    return str(user.telegram_id)


def _user_match_balance(user: User) -> int:
    return int(
        user.purchased_bets_balance
        if (user.purchased_bets_balance or 0) != 0
        else (user.matches_remaining or 0)
    )


def _bookmakers_for_user(user: User) -> str:
    names: list[str] = []
    seen = set()
    for bookmaker in list(getattr(user, "bookmakers", None) or []):
        name = str(getattr(bookmaker, "name", "") or "").strip()
        if name and name.lower() not in seen:
            seen.add(name.lower())
            names.append(name)
    other_name = str(getattr(user, "other_bookmaker_name", "") or "").strip()
    if other_name and other_name.lower() not in seen:
        names.append(other_name)
    return ", ".join(names)


def _bookmakers_for_bet(bet: Bet) -> list[dict[str, Any]]:
    bookmakers = []
    seen_ids = set()
    for bookmaker in list(getattr(bet, "bookmakers", None) or []):
        if not bookmaker or bookmaker.id in seen_ids:
            continue
        seen_ids.add(bookmaker.id)
        bookmakers.append({"id": bookmaker.id, "name": bookmaker.name, "code": bookmaker.code})

    fallback = getattr(bet, "bookmaker", None)
    if fallback and fallback.id not in seen_ids:
        bookmakers.append({"id": fallback.id, "name": fallback.name, "code": fallback.code})
    return bookmakers


def _month_label(value: datetime) -> str:
    return f"{MONTH_LABELS.get(value.month, value.strftime('%m'))} {value.year}"


def _status_label(status: str) -> str:
    if status == "win":
        return "Победа"
    if status == "loss":
        return "Поражение"
    if status == "pending":
        return "Ожидает расчета"
    return "Возврат"


def _profit_for_status(status: str, coefficient: Decimal, unit_stake: Decimal) -> Decimal:
    if status == "win":
        return _round_money((coefficient - Decimal("1")) * unit_stake)
    if status == "loss":
        return -unit_stake
    return Decimal("0")


def export_item_from_bet(
    bet: Bet,
    *,
    unit_stake: Optional[Decimal] = None,
    client_name: str = "",
    access_type: str = "",
) -> Optional[StatsExportItem]:
    if bet.status not in EXPORT_STATUSES or not bet.resolved_at:
        return None

    resolved_at = as_moscow_datetime(bet.resolved_at)
    if not resolved_at:
        return None
    coefficient = Decimal(str(bet.coefficient or "0"))
    stake = unit_stake if unit_stake is not None else export_unit_stake()
    turnover = stake if bet.status in {"win", "loss"} else Decimal("0")
    profit = _profit_for_status(str(bet.status), coefficient, stake)
    bookmakers = _bookmakers_for_bet(bet)
    bookmaker_names = [bookmaker["name"] for bookmaker in bookmakers] or ["Без БК"]
    first_bookmaker = bookmaker_names[0]
    sport_type = str(getattr(bet, "sport_type", None) or "Без спорта")
    delivery_mode = str(getattr(bet, "delivery_mode", None) or "feed")
    source_type = "feed" if delivery_mode == "feed" else "private"
    odds_dropped_to = getattr(bet, "odds_dropped_to", None)

    return StatsExportItem(
        id=str(bet.id),
        resolved_at=resolved_at,
        period_key=resolved_at.strftime("%Y-%m"),
        period_label=_month_label(resolved_at),
        sport_icon=SPORT_ICONS.get(sport_type, "✨"),
        sport_type=sport_type,
        event_name=str(getattr(bet, "event_name", "") or ""),
        bookmaker_icon=BOOKMAKER_CODES.get(first_bookmaker, ""),
        bookmaker_names=bookmaker_names,
        coefficient=coefficient,
        odds_dropped_to=Decimal(str(odds_dropped_to)) if odds_dropped_to is not None else None,
        stake_text=str(getattr(bet, "outcome", None) or ""),
        status=str(bet.status),
        result_label=_status_label(str(bet.status)),
        turnover=turnover,
        profit=profit,
        client_name=client_name,
        source_type=source_type,
        access_type=access_type,
    )


def summarize_export_items(items: Iterable[StatsExportItem]) -> dict[str, Any]:
    item_list = list(items)
    total = len(item_list)
    wins = sum(1 for item in item_list if item.status == "win")
    losses = sum(1 for item in item_list if item.status == "loss")
    refunds = sum(1 for item in item_list if item.status == "refund")
    turnover = sum((item.turnover for item in item_list), Decimal("0"))
    profit = sum((item.profit for item in item_list), Decimal("0"))
    coefficient_count = sum(1 for item in item_list if item.coefficient > 0)
    coefficient_sum = sum((item.coefficient for item in item_list), Decimal("0"))
    resolved = wins + losses

    return {
        "bets": total,
        "wins": wins,
        "losses": losses,
        "refunds": refunds,
        "buyouts": 0,
        "winrate": (wins / resolved) if resolved else 0,
        "average_coefficient": (coefficient_sum / Decimal(coefficient_count)) if coefficient_count else Decimal("0"),
        "turnover": turnover,
        "profit": profit,
        "roi": (profit / turnover) if turnover else 0,
    }


def _sorted_items(items: Iterable[StatsExportItem]) -> list[StatsExportItem]:
    return sorted(items, key=lambda item: (item.resolved_at, item.event_name))


def _monthly_rows(items: list[StatsExportItem]) -> list[dict[str, Any]]:
    months: dict[str, list[StatsExportItem]] = defaultdict(list)
    for item in items:
        months[item.period_key].append(item)

    rows = []
    for key in sorted(months):
        month_items = months[key]
        summary = summarize_export_items(month_items)
        rows.append({
            "label": month_items[0].period_label,
            **summary,
        })
    rows.append({"label": "ИТОГО", **summarize_export_items(items)})
    return rows


def _breakdown_rows(items: list[StatsExportItem], *, kind: str) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for item in items:
        labels = item.bookmaker_names if kind == "bookmaker" else [item.sport_type]
        for label in labels:
            key = label.lower()
            if key not in groups:
                groups[key] = {
                    "label": label,
                    "icon": BOOKMAKER_CODES.get(label, "") if kind == "bookmaker" else item.sport_icon,
                    "items": [],
                }
            groups[key]["items"].append(item)

    rows = [
        {
            "icon": group["icon"],
            "label": group["label"],
            **summarize_export_items(group["items"]),
        }
        for group in groups.values()
    ]
    return sorted(rows, key=lambda row: (-int(row["bets"]), str(row["label"])))


def _safe_sheet_title(title: str) -> str:
    clean = "".join(ch for ch in title if ch not in r'[]:*?/\\').strip() or "Статистика"
    return clean[:31]


def _style_title(ws, max_col: int) -> None:
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=max_col)
    ws["A1"].font = Font(bold=True, size=18, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="111827")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 30


def _style_range_header(ws, row: int, start_col: int, end_col: int, fill: str = "1F2937") -> None:
    for col in range(start_col, end_col + 1):
        cell = ws.cell(row=row, column=col)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=fill)
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _set_border(ws, min_row: int, max_row: int, min_col: int, max_col: int) -> None:
    side = Side(style="thin", color="D1D5DB")
    border = Border(left=side, right=side, top=side, bottom=side)
    for row in ws.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)


def _format_summary_value(cell, key: str, value_format: str = MONEY_FORMAT) -> None:
    if key in {"winrate", "roi"}:
        cell.number_format = PERCENT_FORMAT
    elif key in {"turnover", "profit"}:
        cell.number_format = value_format
    elif key == "average_coefficient":
        cell.number_format = COEF_FORMAT


def _write_kpi(
    ws,
    start_row: int,
    start_col: int,
    label: str,
    value: Any,
    key: str,
    *,
    value_format: str = MONEY_FORMAT,
) -> None:
    ws.merge_cells(start_row=start_row, start_column=start_col, end_row=start_row, end_column=start_col + 1)
    ws.merge_cells(start_row=start_row + 1, start_column=start_col, end_row=start_row + 1, end_column=start_col + 1)
    label_cell = ws.cell(row=start_row, column=start_col, value=label)
    value_cell = ws.cell(row=start_row + 1, column=start_col, value=value)
    label_cell.font = Font(bold=True, color="FFFFFF")
    label_cell.fill = PatternFill("solid", fgColor="374151")
    label_cell.alignment = Alignment(horizontal="center")
    value_cell.font = Font(bold=True, size=14, color="111827")
    value_cell.fill = PatternFill("solid", fgColor="F3F4F6")
    value_cell.alignment = Alignment(horizontal="center")
    _format_summary_value(value_cell, key, value_format)


def _write_monthly_table(
    ws,
    start_row: int,
    items: list[StatsExportItem],
    *,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> int:
    ws.cell(row=start_row, column=1, value="Помесячная сводка").font = Font(bold=True, size=13)
    header_row = start_row + 1
    headers = [
        "Месяц",
        "Ставки",
        "Побед",
        "Пораж.",
        "Возврат",
        "Выкуп",
        "Проход",
        "Ср. коэфф.",
        f"Оборот, {value_label}",
        f"Прибыль, {value_label}",
        "ROI",
    ]
    for col, header in enumerate(headers, 1):
        ws.cell(row=header_row, column=col, value=header)
    _style_range_header(ws, header_row, 1, len(headers), "0F766E")

    for offset, row_data in enumerate(_monthly_rows(items), 1):
        row = header_row + offset
        values = [
            row_data["label"],
            row_data["bets"],
            row_data["wins"],
            row_data["losses"],
            row_data["refunds"],
            row_data["buyouts"],
            row_data["winrate"],
            row_data["average_coefficient"],
            row_data["turnover"],
            row_data["profit"],
            row_data["roi"],
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=row, column=col, value=value)
        for col, key in [(7, "winrate"), (8, "average_coefficient"), (9, "turnover"), (10, "profit"), (11, "roi")]:
            _format_summary_value(ws.cell(row=row, column=col), key, value_format)
        if row_data["label"] == "ИТОГО":
            for col in range(1, len(headers) + 1):
                ws.cell(row=row, column=col).font = Font(bold=True)
                ws.cell(row=row, column=col).fill = PatternFill("solid", fgColor="ECFDF5")

    end_row = header_row + len(_monthly_rows(items))
    _set_border(ws, header_row, end_row, 1, len(headers))
    return end_row + 3


def _write_compact_breakdowns(
    ws,
    start_row: int,
    items: list[StatsExportItem],
    *,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> None:
    bookmaker_rows = _breakdown_rows(items, kind="bookmaker")
    sport_rows = _breakdown_rows(items, kind="sport")

    ws.cell(row=start_row, column=1, value="БУКМЕКЕРЫ").font = Font(bold=True, size=12)
    ws.cell(row=start_row, column=8, value="ВИДЫ СПОРТА").font = Font(bold=True, size=12)
    headers = ["Иконка", "БК", "Ставки", "Проход", f"Прибыль, {value_label}", "ROI"]
    sport_headers = ["Иконка", "Вид спорта", "Ставки", "Проход", f"Прибыль, {value_label}", "ROI"]
    for idx, header in enumerate(headers, 1):
        ws.cell(row=start_row + 1, column=idx, value=header)
    for idx, header in enumerate(sport_headers, 8):
        ws.cell(row=start_row + 1, column=idx, value=header)
    _style_range_header(ws, start_row + 1, 1, 6, "0F766E")
    _style_range_header(ws, start_row + 1, 8, 13, "1D4ED8")

    for offset, row_data in enumerate(bookmaker_rows, 2):
        row = start_row + offset
        values = [row_data["icon"], row_data["label"], row_data["bets"], row_data["winrate"], row_data["profit"], row_data["roi"]]
        for col, value in enumerate(values, 1):
            ws.cell(row=row, column=col, value=value)
        for col, key in [(4, "winrate"), (5, "profit"), (6, "roi")]:
            _format_summary_value(ws.cell(row=row, column=col), key, value_format)

    for offset, row_data in enumerate(sport_rows, 2):
        row = start_row + offset
        values = [row_data["icon"], row_data["label"], row_data["bets"], row_data["winrate"], row_data["profit"], row_data["roi"]]
        for col, value in enumerate(values, 8):
            ws.cell(row=row, column=col, value=value)
        for col, key in [(11, "winrate"), (12, "profit"), (13, "roi")]:
            _format_summary_value(ws.cell(row=row, column=col), key, value_format)

    max_rows = max(len(bookmaker_rows), len(sport_rows)) + start_row + 1
    _set_border(ws, start_row + 1, max_rows, 1, 6)
    _set_border(ws, start_row + 1, max_rows, 8, 13)


def _setup_statistics_sheet(
    wb: Workbook,
    title: str,
    period_label: str,
    items: list[StatsExportItem],
    *,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> None:
    ws = wb.active
    ws.title = "Статистика"
    ws.sheet_view.showGridLines = False
    ws["A1"] = title
    _style_title(ws, 13)
    ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=13)
    ws["A2"] = period_label
    ws["A2"].alignment = Alignment(horizontal="center")
    ws["A2"].font = Font(italic=True, color="4B5563")

    summary = summarize_export_items(items)
    _write_kpi(ws, 4, 1, "Всего ставок", summary["bets"], "bets", value_format=value_format)
    _write_kpi(ws, 4, 4, "Проходимость", summary["winrate"], "winrate", value_format=value_format)
    _write_kpi(ws, 4, 7, f"Прибыль, {value_label}", summary["profit"], "profit", value_format=value_format)
    _write_kpi(ws, 4, 10, "ROI", summary["roi"], "roi", value_format=value_format)
    _write_kpi(ws, 7, 1, f"Оборот, {value_label}", summary["turnover"], "turnover", value_format=value_format)
    _write_kpi(ws, 7, 4, "Ср. коэфф.", summary["average_coefficient"], "average_coefficient", value_format=value_format)
    _write_kpi(ws, 7, 7, "Побед", summary["wins"], "wins", value_format=value_format)
    _write_kpi(ws, 7, 10, "Поражений", summary["losses"], "losses", value_format=value_format)

    next_row = _write_monthly_table(ws, 11, items, value_format=value_format, value_label=value_label)
    _write_compact_breakdowns(ws, next_row, items, value_format=value_format, value_label=value_label)
    _autosize(ws)
    ws.freeze_panes = "A12"


def _write_breakdown_sheet(
    wb: Workbook,
    items: list[StatsExportItem],
    *,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> None:
    ws = wb.create_sheet("Свод по БК и спорту")
    ws.sheet_view.showGridLines = False
    ws["A1"] = "СВОД ПО БУКМЕКЕРАМ"
    ws["H1"] = "СВОД ПО ВИДАМ СПОРТА"
    for cell in ["A1", "H1"]:
        ws[cell].font = Font(bold=True, size=14, color="111827")

    headers = ["Иконка", "БК", "Ставки", "Побед", "Пораж.", "Возврат", "Проход", "Ср. коэфф.", f"Оборот, {value_label}", f"Прибыль, {value_label}", "ROI"]
    sport_headers = ["Иконка", "Вид спорта", "Ставки", "Побед", "Пораж.", "Возврат", "Проход", "Ср. коэфф.", f"Оборот, {value_label}", f"Прибыль, {value_label}", "ROI"]
    for col, header in enumerate(headers, 1):
        ws.cell(row=2, column=col, value=header)
    for col, header in enumerate(sport_headers, 13):
        ws.cell(row=2, column=col, value=header)
    _style_range_header(ws, 2, 1, 11, "0F766E")
    _style_range_header(ws, 2, 13, 23, "1D4ED8")

    for offset, row_data in enumerate(_breakdown_rows(items, kind="bookmaker"), 3):
        _write_breakdown_detail_row(ws, offset, 1, row_data, value_format=value_format)
    for offset, row_data in enumerate(_breakdown_rows(items, kind="sport"), 3):
        _write_breakdown_detail_row(ws, offset, 13, row_data, value_format=value_format)

    max_row = max(len(_breakdown_rows(items, kind="bookmaker")), len(_breakdown_rows(items, kind="sport"))) + 2
    _set_border(ws, 2, max_row, 1, 11)
    _set_border(ws, 2, max_row, 13, 23)
    _autosize(ws)
    ws.freeze_panes = "A3"


def _write_breakdown_detail_row(
    ws,
    row: int,
    start_col: int,
    row_data: dict[str, Any],
    *,
    value_format: str = MONEY_FORMAT,
) -> None:
    values = [
        row_data["icon"],
        row_data["label"],
        row_data["bets"],
        row_data["wins"],
        row_data["losses"],
        row_data["refunds"],
        row_data["winrate"],
        row_data["average_coefficient"],
        row_data["turnover"],
        row_data["profit"],
        row_data["roi"],
    ]
    for index, value in enumerate(values):
        ws.cell(row=row, column=start_col + index, value=value)
    for offset, key in [(6, "winrate"), (7, "average_coefficient"), (8, "turnover"), (9, "profit"), (10, "roi")]:
        _format_summary_value(ws.cell(row=row, column=start_col + offset), key, value_format)


def _write_detail_sheet(
    wb: Workbook,
    items: list[StatsExportItem],
    *,
    include_client: bool,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> None:
    ws = wb.create_sheet("Детально")
    ws.sheet_view.showGridLines = False
    headers = [
        "Период",
        "№",
        "Вид",
        "Вид спорта",
        "Матч",
        "Иконка БК",
        "БК",
        "Коэфф.",
        "Упал до",
        "Ставка",
        "Исход",
        f"Оборот, {value_label}",
        f"Прибыль, {value_label}",
    ]
    if include_client:
        headers.append("Клиент")
    headers.extend(["Источник", "ID"])

    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
    _style_range_header(ws, 1, 1, len(headers), "111827")

    month_counts: dict[str, int] = defaultdict(int)
    for row_idx, item in enumerate(_sorted_items(items), 2):
        month_counts[item.period_key] += 1
        values = [
            item.period_label,
            month_counts[item.period_key],
            item.sport_icon,
            item.sport_type,
            item.event_name,
            item.bookmaker_icon,
            ", ".join(item.bookmaker_names),
            item.coefficient,
            item.odds_dropped_to,
            item.stake_text,
            item.result_label,
            item.turnover,
            item.profit,
        ]
        if include_client:
            values.append(item.client_name)
        values.extend([item.source_type, item.id])
        for col, value in enumerate(values, 1):
            ws.cell(row=row_idx, column=col, value=value)
        ws.cell(row=row_idx, column=8).number_format = COEF_FORMAT
        ws.cell(row=row_idx, column=9).number_format = COEF_FORMAT
        money_start_col = 12
        ws.cell(row=row_idx, column=money_start_col).number_format = value_format
        ws.cell(row=row_idx, column=money_start_col + 1).number_format = value_format
        if item.status == "win":
            fill = PatternFill("solid", fgColor="ECFDF5")
        elif item.status == "loss":
            fill = PatternFill("solid", fgColor="FEF2F2")
        else:
            fill = PatternFill("solid", fgColor="F8FAFC")
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col).fill = fill

    if items:
        _set_border(ws, 1, len(items) + 1, 1, len(headers))
    _autosize(ws)
    ws.freeze_panes = "A2"


def _autosize(ws) -> None:
    for column_cells in ws.columns:
        letter = get_column_letter(column_cells[0].column)
        max_length = max(len(str(cell.value or "")) for cell in column_cells)
        ws.column_dimensions[letter].width = min(max(max_length + 2, 10), 48)


def _result_label_short(result: str) -> str:
    if result == "win":
        return "П"
    if result == "loss":
        return "Л"
    return str(result or "")


def _streak_label(streak_type: Optional[str], count: int) -> str:
    if not streak_type or count <= 0:
        return ""
    label = "побед" if streak_type == "win" else "пораж."
    return f"{count} {label}"


def _yes_no(value: bool) -> str:
    return "Да" if value else "Нет"


def _excel_datetime(value: Optional[datetime]) -> Optional[datetime]:
    moscow_value = as_moscow_datetime(value)
    return moscow_value.replace(tzinfo=None) if moscow_value else None


def build_client_info_export_workbook(
    rows: Iterable[ClientInfoExportRow],
    *,
    period_label: str,
    recent_rows: Optional[Iterable[ClientRecentBetExportRow]] = None,
) -> bytes:
    row_list = list(rows)
    recent_row_list = list(recent_rows or [])
    wb = Workbook()
    ws = wb.active
    ws.title = "Инфа"
    ws.sheet_view.showGridLines = False
    headers = [
        "Период",
        "ID",
        "Клиент",
        "Username",
        "Телефон",
        "VK ID",
        "Web/VK клиент",
        "Группа",
        "Тег",
        "БК клиента",
        "Дата регистрации",
        "Матчей осталось",
        "Гарантия",
        "Взял матчей всего",
        "Рассчитано матчей",
        "Ожидают расчета",
        "Возвраты",
        "Ставки",
        "Победы",
        "Поражения",
        "Winrate",
        "ROI",
        "Профит, флеты",
        "Ср. кф",
        "Текущая серия",
        "Макс. побед",
        "Макс. пораж.",
        "Последние исходы",
        "Ситуация",
        "Описание",
    ]
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
    _style_range_header(ws, 1, 1, len(headers), "111827")

    for row_idx, row_data in enumerate(row_list, 2):
        values = [
            period_label,
            row_data.user_id,
            row_data.client_name,
            f"@{row_data.username}" if row_data.username else "",
            row_data.phone,
            row_data.vk_user_id,
            _yes_no(row_data.is_web_only),
            row_data.client_group,
            row_data.client_tag,
            row_data.bookmaker_names,
            _excel_datetime(row_data.created_at),
            row_data.matches_remaining,
            _yes_no(row_data.guarantee_active),
            row_data.total_taken_bets,
            row_data.settled_bets,
            row_data.pending_bets,
            row_data.refund_bets,
            row_data.bets,
            row_data.wins,
            row_data.losses,
            row_data.winrate / 100,
            row_data.roi / 100,
            row_data.profit_units,
            row_data.average_coefficient,
            _streak_label(row_data.current_streak_type, row_data.current_streak),
            row_data.max_win_streak,
            row_data.max_loss_streak,
            " ".join(_result_label_short(result) for result in row_data.recent_results),
            row_data.situation_label,
            row_data.situation_description,
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=row_idx, column=col, value=value)
        ws.cell(row=row_idx, column=11).number_format = DATE_FORMAT
        ws.cell(row=row_idx, column=21).number_format = PERCENT_FORMAT
        ws.cell(row=row_idx, column=22).number_format = PERCENT_FORMAT
        ws.cell(row=row_idx, column=23).number_format = FLAT_FORMAT
        ws.cell(row=row_idx, column=24).number_format = COEF_FORMAT
        if row_data.situation_tone == "danger":
            fill = PatternFill("solid", fgColor="FEF2F2")
        elif row_data.situation_tone == "warning":
            fill = PatternFill("solid", fgColor="FFFBEB")
        elif row_data.situation_tone == "success":
            fill = PatternFill("solid", fgColor="ECFDF5")
        else:
            fill = PatternFill("solid", fgColor="F8FAFC")
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col).fill = fill

    if row_list:
        _set_border(ws, 1, len(row_list) + 1, 1, len(headers))
    else:
        _set_border(ws, 1, 1, 1, len(headers))
    _autosize(ws)
    ws.freeze_panes = "A2"
    _write_client_recent_bets_sheet(wb, recent_row_list)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def _write_client_recent_bets_sheet(wb: Workbook, rows: list[ClientRecentBetExportRow]) -> None:
    ws = wb.create_sheet("Последние 50")
    ws.sheet_view.showGridLines = False
    headers = [
        "ID",
        "Клиент",
        "Username",
        "Телефон",
        "VK ID",
        "Web/VK клиент",
        "Группа",
        "Тег",
        "Матчей осталось",
        "Гарантия",
        "Дата взятия",
        "Матч",
        "Вид спорта",
        "БК",
        "Коэфф.",
        "Ставка",
        "Статус",
        "Дата расчета",
        "Источник",
        "Тип доступа",
        "Матч списан",
        "ID ставки",
    ]
    for col, header in enumerate(headers, 1):
        ws.cell(row=1, column=col, value=header)
    _style_range_header(ws, 1, 1, len(headers), "111827")

    for row_idx, row_data in enumerate(rows, 2):
        values = [
            row_data.user_id,
            row_data.client_name,
            f"@{row_data.username}" if row_data.username else "",
            row_data.phone,
            row_data.vk_user_id,
            _yes_no(row_data.is_web_only),
            row_data.client_group,
            row_data.client_tag,
            row_data.matches_remaining,
            _yes_no(row_data.guarantee_active),
            _excel_datetime(row_data.taken_at),
            row_data.event_name,
            row_data.sport_type,
            row_data.bookmaker_names,
            row_data.coefficient,
            row_data.outcome,
            row_data.result_label,
            _excel_datetime(row_data.resolved_at),
            row_data.source_type,
            row_data.access_type,
            _yes_no(row_data.match_charged),
            row_data.bet_id,
        ]
        for col, value in enumerate(values, 1):
            ws.cell(row=row_idx, column=col, value=value)
        ws.cell(row=row_idx, column=11).number_format = DATE_FORMAT
        ws.cell(row=row_idx, column=15).number_format = COEF_FORMAT
        ws.cell(row=row_idx, column=18).number_format = DATE_FORMAT
        if row_data.status == "win":
            fill = PatternFill("solid", fgColor="ECFDF5")
        elif row_data.status == "loss":
            fill = PatternFill("solid", fgColor="FEF2F2")
        elif row_data.status == "pending":
            fill = PatternFill("solid", fgColor="FFFBEB")
        else:
            fill = PatternFill("solid", fgColor="F8FAFC")
        for col in range(1, len(headers) + 1):
            ws.cell(row=row_idx, column=col).fill = fill

    if rows:
        _set_border(ws, 1, len(rows) + 1, 1, len(headers))
    else:
        _set_border(ws, 1, 1, 1, len(headers))
    _autosize(ws)
    ws.freeze_panes = "A2"


def build_stats_export_workbook(
    items: Iterable[StatsExportItem],
    *,
    title: str,
    period_label: str,
    include_client: bool = False,
    value_format: str = MONEY_FORMAT,
    value_label: str = "₽",
) -> bytes:
    item_list = _sorted_items(items)
    wb = Workbook()
    _setup_statistics_sheet(wb, title, period_label, item_list, value_format=value_format, value_label=value_label)
    _write_breakdown_sheet(wb, item_list, value_format=value_format, value_label=value_label)
    _write_detail_sheet(wb, item_list, include_client=include_client, value_format=value_format, value_label=value_label)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def stats_export_period_label(period: str) -> str:
    labels = {
        "week": "Текущая неделя",
        "month": "Текущий месяц",
        "quarter": "Текущий квартал",
        "all": "Весь период",
    }
    return labels.get(period, labels["all"])


async def load_shamrai_export_items(db: AsyncSession, period: str) -> list[StatsExportItem]:
    query = (
        select(Bet)
        .filter(Bet.status.in_(list(EXPORT_STATUSES)), Bet.resolved_at.isnot(None))
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.asc())
    )
    start = period_start(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    result = await db.execute(query)
    return [
        item
        for bet in result.scalars().all()
        if (item := export_item_from_bet(bet)) is not None
    ]


async def load_author_export_items(
    db: AsyncSession,
    *,
    author_id: int,
    period: str,
    source: str = "all",
) -> list[StatsExportItem]:
    query = (
        select(Bet)
        .filter(Bet.author_id == author_id, Bet.status.in_(list(EXPORT_STATUSES)), Bet.resolved_at.isnot(None))
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.asc())
    )
    start = period_start(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    result = await db.execute(query)
    items = [
        item
        for bet in result.scalars().all()
        if (item := export_item_from_bet(bet)) is not None
    ]
    if source in {"feed", "private"}:
        items = [item for item in items if item.source_type == source]
    return items


async def load_client_export_groups(db: AsyncSession, period: str) -> list[ClientStatsExportGroup]:
    query = (
        select(User, Bet, user_bets.c.access_type, user_bets.c.match_charged, user_bets.c.taken_at)
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .join(Bet, Bet.id == user_bets.c.bet_id)
        .filter(
            User.role.notin_(list(STAFF_ROLES)),
            Bet.status.in_(list(EXPORT_STATUSES)),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(User.telegram_id.asc(), Bet.resolved_at.asc())
    )
    start = period_start(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)

    grouped: dict[int, ClientStatsExportGroup] = {}
    for user, bet, access_type, match_charged, _taken_at in (await db.execute(query)).all():
        if not is_paid_client_access(access_type, match_charged):
            continue
        client_name = _display_user(user)
        item = export_item_from_bet(
            bet,
            unit_stake=CLIENT_EXPORT_STAKE,
            client_name=client_name,
            access_type=str(access_type or ""),
        )
        if not item:
            continue
        if user.telegram_id not in grouped:
            grouped[user.telegram_id] = ClientStatsExportGroup(
                user_id=user.telegram_id,
                client_name=client_name,
                username=user.username or "",
                items=[],
            )
        grouped[user.telegram_id].items.append(item)
    return list(grouped.values())


async def load_clients_export_items(db: AsyncSession, period: str) -> list[StatsExportItem]:
    groups = await load_client_export_groups(db, period)
    return [item for group in groups for item in group.items]


async def load_client_info_export_rows(db: AsyncSession, period: str) -> list[ClientInfoExportRow]:
    users_result = await db.execute(
        select(User)
        .filter(User.role.notin_(list(STAFF_ROLES)))
        .options(selectinload(User.bookmakers))
        .order_by(User.created_at.desc(), User.telegram_id.desc())
    )
    users = users_result.scalars().all()
    grouped_items: dict[int, list[dict[str, Any]]] = {user.telegram_id: [] for user in users}
    taken_counts: dict[int, dict[str, int]] = {
        user.telegram_id: {
            "total": 0,
            "settled": 0,
            "pending": 0,
            "refund": 0,
        }
        for user in users
    }

    counts_query = (
        select(User.telegram_id, Bet.status, user_bets.c.access_type, user_bets.c.match_charged)
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .join(Bet, Bet.id == user_bets.c.bet_id)
        .filter(User.role.notin_(list(STAFF_ROLES)))
    )
    for user_id, bet_status, access_type, match_charged in (await db.execute(counts_query)).all():
        counts = taken_counts.setdefault(user_id, {"total": 0, "settled": 0, "pending": 0, "refund": 0})
        counts["total"] += 1
        if bet_status in {"win", "loss"}:
            counts["settled"] += 1
        elif bet_status == "pending":
            counts["pending"] += 1
        elif bet_status == "refund":
            counts["refund"] += 1

    query = (
        select(User, Bet, user_bets.c.access_type, user_bets.c.match_charged, user_bets.c.taken_at)
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .join(Bet, Bet.id == user_bets.c.bet_id)
        .filter(
            User.role.notin_(list(STAFF_ROLES)),
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(User.telegram_id.asc(), Bet.resolved_at.desc())
    )
    start = period_start(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)

    for user, bet, access_type, match_charged, taken_at in (await db.execute(query)).all():
        if not is_paid_client_access(access_type, match_charged):
            continue
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
        )
        if item:
            grouped_items.setdefault(user.telegram_id, []).append(item)

    rows: list[ClientInfoExportRow] = []
    for user in users:
        items = grouped_items.get(user.telegram_id, [])
        summary = summarize_items(items)
        situation = client_situation(summary)
        rows.append(ClientInfoExportRow(
            user_id=user.telegram_id,
            client_name=_display_user(user),
            username=user.username or "",
            phone=user.phone or "",
            vk_user_id=user.vk_user_id or "",
            is_web_only=bool(user.is_web_only),
            bookmaker_names=_bookmakers_for_user(user),
            client_group=user.client_group or "",
            client_tag=user.client_tag or "",
            created_at=user.created_at,
            matches_remaining=_user_match_balance(user),
            guarantee_active=bool(user.guarantee_active),
            total_taken_bets=int(taken_counts.get(user.telegram_id, {}).get("total", 0)),
            settled_bets=int(taken_counts.get(user.telegram_id, {}).get("settled", 0)),
            pending_bets=int(taken_counts.get(user.telegram_id, {}).get("pending", 0)),
            refund_bets=int(taken_counts.get(user.telegram_id, {}).get("refund", 0)),
            bets=int(summary["bets"]),
            wins=int(summary["wins"]),
            losses=int(summary["losses"]),
            winrate=float(summary["winrate"]),
            roi=float(summary["roi"]),
            profit_units=float(summary["profit_units"]),
            average_coefficient=float(summary["average_coefficient"]),
            current_streak=int(summary["current_streak"]),
            current_streak_type=summary["current_streak_type"],
            max_win_streak=int(summary["max_win_streak"]),
            max_loss_streak=int(summary["max_loss_streak"]),
            recent_results=last_result_codes(items),
            situation_code=situation["code"],
            situation_label=situation["label"],
            situation_tone=situation["tone"],
            situation_description=situation["description"],
        ))

    return sorted(rows, key=lambda row: (
        row.situation_tone != "danger",
        -row.matches_remaining,
        -row.profit_units,
        row.client_name.lower(),
    ))


async def load_client_recent_bet_export_rows(
    db: AsyncSession,
    period: str,
    *,
    limit_per_client: int = 50,
) -> list[ClientRecentBetExportRow]:
    query = (
        select(User, Bet, user_bets.c.access_type, user_bets.c.match_charged, user_bets.c.taken_at)
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .join(Bet, Bet.id == user_bets.c.bet_id)
        .filter(
            User.role.notin_(list(STAFF_ROLES)),
            Bet.status.in_(["pending", "win", "loss", "refund"]),
        )
        .options(selectinload(User.bookmakers), selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(User.telegram_id.asc(), user_bets.c.taken_at.desc(), Bet.created_at.desc())
    )
    start = period_start(period)
    if start:
        query = query.filter(user_bets.c.taken_at >= start)

    rows: list[ClientRecentBetExportRow] = []
    per_user_counts: dict[int, int] = defaultdict(int)
    for user, bet, access_type, match_charged, taken_at in (await db.execute(query)).all():
        if per_user_counts[user.telegram_id] >= limit_per_client:
            continue
        per_user_counts[user.telegram_id] += 1

        bookmakers = _bookmakers_for_bet(bet)
        bookmaker_names = ", ".join(bookmaker["name"] for bookmaker in bookmakers) or "Без БК"
        delivery_mode = str(getattr(bet, "delivery_mode", None) or "feed")
        rows.append(ClientRecentBetExportRow(
            user_id=user.telegram_id,
            client_name=_display_user(user),
            username=user.username or "",
            phone=user.phone or "",
            vk_user_id=user.vk_user_id or "",
            is_web_only=bool(user.is_web_only),
            client_group=user.client_group or "",
            client_tag=user.client_tag or "",
            matches_remaining=_user_match_balance(user),
            guarantee_active=bool(user.guarantee_active),
            taken_at=taken_at,
            event_name=str(getattr(bet, "event_name", "") or ""),
            sport_type=str(getattr(bet, "sport_type", None) or "Без спорта"),
            bookmaker_names=bookmaker_names,
            coefficient=Decimal(str(getattr(bet, "coefficient", None) or "0")),
            outcome=str(getattr(bet, "outcome", None) or ""),
            status=str(getattr(bet, "status", "") or ""),
            result_label=_status_label(str(getattr(bet, "status", "") or "")),
            resolved_at=getattr(bet, "resolved_at", None),
            source_type="feed" if delivery_mode == "feed" else "private",
            access_type=str(access_type or ""),
            match_charged=bool(match_charged),
            bet_id=str(bet.id),
        ))
    return rows
