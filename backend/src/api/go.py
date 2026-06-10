import html
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.bookmaker_links import normalize_match_url
from src.models.database import get_db
from src.models.models import Bet

router = APIRouter(prefix="/go", tags=["Redirects"])


def bookmaker_match_url_for_bet(bet: Bet, bookmaker_id: int) -> str:
    for item in bet.bookmaker_links or []:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("bookmaker_id") or item.get("bookmakerId") or item.get("id")
        try:
            item_bookmaker_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if item_bookmaker_id != int(bookmaker_id):
            continue
        url = normalize_match_url(item.get("url") or item.get("link") or item.get("match_link"))
        if url:
            return url

    try:
        primary_bookmaker_id = int(getattr(bet, "bookmaker_id", 0) or 0)
    except (TypeError, ValueError):
        primary_bookmaker_id = 0
    if primary_bookmaker_id == int(bookmaker_id):
        return normalize_match_url(getattr(bet, "match_link", None))
    return ""


def unavailable_link_response(*, status_code: int = 404, reason: Optional[str] = None) -> HTMLResponse:
    clean_reason = html.escape(reason or "Ссылка на матч недоступна")
    return HTMLResponse(
        status_code=status_code,
        content=f"""
<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ссылка недоступна</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #101114;
      color: #fff;
      font-family: Arial, sans-serif;
    }}
    main {{
      max-width: 420px;
      padding: 24px;
      text-align: center;
    }}
    h1 {{
      font-size: 24px;
      margin: 0 0 12px;
    }}
    p {{
      color: #cbd5e1;
      font-size: 16px;
      line-height: 1.45;
      margin: 0;
    }}
  </style>
</head>
<body>
  <main>
    <h1>Ссылка на матч недоступна</h1>
    <p>{clean_reason}. Напишите менеджеру Shamrai, и мы быстро обновим переход.</p>
  </main>
</body>
</html>
""",
    )


@router.head("/bets/{bet_id}/bookmakers/{bookmaker_id}", include_in_schema=False)
@router.get("/bets/{bet_id}/bookmakers/{bookmaker_id}", include_in_schema=False)
async def redirect_to_bookmaker_match(
    bet_id: UUID,
    bookmaker_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = result.scalars().first()
    if not bet:
        return unavailable_link_response(reason="Прогноз не найден")

    url = bookmaker_match_url_for_bet(bet, bookmaker_id)
    if not url:
        return unavailable_link_response()
    return RedirectResponse(url=url, status_code=302)
