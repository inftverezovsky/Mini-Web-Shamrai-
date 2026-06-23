from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import case, extract, func
from sqlalchemy.future import select
from decimal import Decimal
from datetime import datetime

from src.models.database import get_read_db
from src.models.models import Bet
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
async def get_global_stats(db: AsyncSession = Depends(get_read_db)):
    """
    GET /api/stats/global
    Public endpoint. Calculates global winrate, ROI, and profit trends.
    """
    resolved_statuses = ["win", "loss", "refund"]
    profit_expr = case(
        (Bet.status == "win", Bet.coefficient - Decimal("1.00")),
        (Bet.status == "loss", Decimal("-1.00")),
        else_=Decimal("0.00"),
    )
    won_expr = case((Bet.status == "win", 1), else_=0)
    lost_expr = case((Bet.status == "loss", 1), else_=0)
    refund_expr = case((Bet.status == "refund", 1), else_=0)

    summary_result = await db.execute(
        select(
            func.count(Bet.id),
            func.coalesce(func.sum(won_expr), 0),
            func.coalesce(func.sum(lost_expr), 0),
            func.coalesce(func.sum(refund_expr), 0),
            func.coalesce(func.sum(profit_expr), Decimal("0.00")),
        )
        .filter(Bet.status.in_(resolved_statuses))
    )
    total, won, lost, refunded, profit = summary_result.one()
    total = int(total or 0)
    won = int(won or 0)
    lost = int(lost or 0)
    refunded = int(refunded or 0)
    profit = Decimal(str(profit or "0.00"))
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / resolved * 100) if resolved > 0 else 0.0

    date_ref = func.coalesce(Bet.resolved_at, Bet.created_at)
    year_part = extract("year", date_ref)
    month_part = extract("month", date_ref)
    monthly_result = await db.execute(
        select(
            year_part.label("year"),
            month_part.label("month"),
            func.coalesce(func.sum(profit_expr), Decimal("0.00")).label("profit"),
        )
        .filter(Bet.status.in_(resolved_statuses), date_ref.isnot(None))
        .group_by(year_part, month_part)
        .order_by(year_part.asc(), month_part.asc())
    )
    chart_points = []
    cumulative_profit = Decimal("0.00")

    for year, month, month_profit in monthly_result.all():
        cumulative_profit += Decimal(str(month_profit or "0.00"))
        month_key = f"{int(year):04d}-{int(month):02d}"
        chart_points.append({
            "month": month_key,
            "profit": round(float(cumulative_profit), 2)
        })

    # If no data exists, output placeholder for layout rendering
    if not chart_points:
        current_month = datetime.now().strftime("%Y-%m")
        chart_points.append({"month": current_month, "profit": 0.0})

    return {
        "winrate": round(winrate, 2),
        "roi": round(roi, 2),
        "net_profit": round(float(profit), 2),
        "total_bets": total,
        "won_bets": won,
        "lost_bets": lost,
        "refund_bets": refunded,
        "chart_points": chart_points
    }
