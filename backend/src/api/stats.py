from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, extract, func
from sqlalchemy.future import select
from decimal import Decimal
from datetime import datetime

from src.models.database import get_read_db
from src.models.models import Bet
from src.services.historical_stats import load_active_historical_stats_snapshot
from src.services.statistics import PERIOD_LABELS, normalize_period, period_start
from src.services.stats_export import build_bookmaker_logo_png

router = APIRouter(prefix="/stats", tags=["Stats"])


@router.get("/export/bookmaker-logo.png")
async def get_export_bookmaker_logo(codes: str = Query(..., min_length=1)):
    """
    Public image endpoint for Google Sheets IMAGE() cells in stats exports.
    Only known bookmaker logo codes are rendered.
    """
    logo = build_bookmaker_logo_png(code.strip() for code in codes.split(",") if code.strip())
    if not logo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bookmaker logo not found")
    return Response(
        content=logo.data,
        media_type="image/png",
        headers={
            "Cache-Control": "public, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )

@router.get("/global")
async def get_global_stats(
    period: str = Query("all", pattern="^(all|month)$"),
    db: AsyncSession = Depends(get_read_db),
):
    """
    GET /api/stats/global
    Public endpoint. Calculates global winrate, ROI, and profit trends.
    """
    normalized_period = normalize_period(period)
    historical = await load_active_historical_stats_snapshot(db, normalized_period)
    resolved_statuses = ["win", "loss", "refund"]
    profit_expr = case(
        (Bet.status == "win", Bet.coefficient - Decimal("1.00")),
        (Bet.status == "loss", Decimal("-1.00")),
        else_=Decimal("0.00"),
    )
    won_expr = case((Bet.status == "win", 1), else_=0)
    lost_expr = case((Bet.status == "loss", 1), else_=0)
    refund_expr = case((Bet.status == "refund", 1), else_=0)
    resolved_coefficient_expr = case((Bet.status.in_(["win", "loss"]), Bet.coefficient), else_=None)
    date_ref = func.coalesce(Bet.resolved_at, Bet.created_at)
    filters = [Bet.publication_type == "forecast", Bet.status.in_(resolved_statuses)]
    start = period_start(normalized_period)
    if start:
        filters.append(date_ref >= start)
    elif historical:
        filters.append(date_ref >= historical.cutoff_at)

    summary_result = await db.execute(
        select(
            func.count(Bet.id),
            func.coalesce(func.sum(won_expr), 0),
            func.coalesce(func.sum(lost_expr), 0),
            func.coalesce(func.sum(refund_expr), 0),
            func.coalesce(func.sum(profit_expr), Decimal("0.00")),
            func.coalesce(func.avg(resolved_coefficient_expr), Decimal("0.00")),
            func.count(resolved_coefficient_expr),
        )
        .filter(*filters)
    )
    total, won, lost, refunded, profit, average_coefficient, coefficient_count = summary_result.one()
    total = int(total or 0)
    won = int(won or 0)
    lost = int(lost or 0)
    refunded = int(refunded or 0)
    profit = Decimal(str(profit or "0.00"))
    coefficient_count = int(coefficient_count or 0)
    coefficient_sum = Decimal(str(average_coefficient or "0")) * Decimal(coefficient_count)
    if historical:
        summary = historical.summary
        total += int(summary.get("bets") or 0)
        won += int(summary.get("wins") or 0)
        lost += int(summary.get("losses") or 0)
        refunded += int(summary.get("refunds") or 0)
        profit += Decimal(str(summary.get("profit_units") or "0"))
        historical_coefficient_count = sum(int(row.get("bets") or 0) for row in historical.monthly)
        historical_coefficient_sum = sum(
            Decimal(str(row.get("average_coefficient") or "0")) * Decimal(int(row.get("bets") or 0))
            for row in historical.monthly
        )
        coefficient_count += historical_coefficient_count
        coefficient_sum += historical_coefficient_sum
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / resolved * 100) if resolved > 0 else 0.0
    average_coefficient_value = (coefficient_sum / Decimal(coefficient_count)) if coefficient_count else Decimal("0")

    year_part = extract("year", date_ref)
    month_part = extract("month", date_ref)
    monthly_result = await db.execute(
        select(
            year_part.label("year"),
            month_part.label("month"),
            func.coalesce(func.sum(profit_expr), Decimal("0.00")).label("profit"),
        )
        .filter(*filters, date_ref.isnot(None))
        .group_by(year_part, month_part)
        .order_by(year_part.asc(), month_part.asc())
    )
    monthly_profit: dict[str, Decimal] = {}
    if historical:
        unit = Decimal(str(historical.unit_stake_rub or "10000"))
        for row in historical.monthly:
            key = str(row.get("period_key") or "")
            if key:
                monthly_profit[key] = monthly_profit.get(key, Decimal("0")) + (Decimal(str(row.get("profit") or "0")) / unit)

    for year, month, month_profit in monthly_result.all():
        month_key = f"{int(year):04d}-{int(month):02d}"
        monthly_profit[month_key] = monthly_profit.get(month_key, Decimal("0")) + Decimal(str(month_profit or "0.00"))

    chart_points = []
    cumulative_profit = Decimal("0.00")
    for month_key in sorted(monthly_profit):
        cumulative_profit += monthly_profit[month_key]
        chart_points.append({
            "month": month_key,
            "profit": round(float(cumulative_profit), 2)
        })

    # If no data exists, output placeholder for layout rendering
    if not chart_points:
        current_month = datetime.now().strftime("%Y-%m")
        chart_points.append({"month": current_month, "profit": 0.0})

    return {
        "period": normalized_period,
        "period_label": PERIOD_LABELS[normalized_period],
        "winrate": round(winrate, 2),
        "roi": round(roi, 2),
        "net_profit": round(float(profit), 2),
        "average_coefficient": round(float(average_coefficient_value or 0), 2),
        "total_bets": total,
        "won_bets": won,
        "lost_bets": lost,
        "refund_bets": refunded,
        "chart_points": chart_points
    }
