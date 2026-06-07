from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Dict, Any
from decimal import Decimal
from datetime import datetime
from collections import defaultdict

from src.models.database import get_db
from src.models.models import Bet

router = APIRouter(prefix="/stats", tags=["Stats"])

@router.get("/global")
async def get_global_stats(db: AsyncSession = Depends(get_db)):
    """
    GET /api/stats/global
    Public endpoint. Calculates global winrate, ROI, and profit trends.
    """
    # Fetch resolved bets (win, loss, refund) sorted chronologically
    query = select(Bet).filter(Bet.status.in_(["win", "loss", "refund"])).order_by(Bet.resolved_at.asc(), Bet.created_at.asc())
    result = await db.execute(query)
    bets = result.scalars().all()
        
    total = len(bets)
    won = 0
    lost = 0
    refunded = 0
    profit = Decimal("0.00")
    
    # Calculate monthly performance
    monthly_profits = defaultdict(Decimal)
    
    for bet in bets:
        bet_profit = Decimal("0.00")
        if bet.status == "win":
            won += 1
            bet_profit = bet.coefficient - Decimal("1.00")
        elif bet.status == "loss":
            lost += 1
            bet_profit = Decimal("-1.00")
        elif bet.status == "refund":
            refunded += 1
            
        profit += bet_profit
        
        # Use resolved_at or created_at for date reference
        date_ref = bet.resolved_at or bet.created_at
        if date_ref:
            month_str = date_ref.strftime("%Y-%m")
            monthly_profits[month_str] += bet_profit

    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / total * 100) if total > 0 else 0.0

    # Build chronological cumulative profit points
    sorted_months = sorted(monthly_profits.keys())
    chart_points = []
    cumulative_profit = Decimal("0.00")
    
    for m in sorted_months:
        cumulative_profit += monthly_profits[m]
        chart_points.append({
            "month": m,
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
