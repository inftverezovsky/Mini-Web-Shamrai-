import os
import uuid as uuid_pkg
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, or_, func
from typing import List, Optional
from decimal import Decimal
from uuid import UUID
from datetime import datetime, timezone

from src.models.database import get_db
from src.models.models import User, Bookmaker, Bet, Subscription, user_bets
from src.schemas.schemas import BetResponse, BetCreate, BetResolve, BetHintRequest, BetHintResponse, UserStats, AdminAnalytics
from src.api.deps import get_current_user, get_current_admin
from src.core.bookmaker_links import normalize_bookmaker_links
from src.core.roles import is_staff_role
from src.core.config import settings
from src.services.match_access import log_match_balance_event, record_user_bet_access
from src.api.payments import call_telegram_api

router = APIRouter(prefix="/bets", tags=["Bets"])

STATIC_COUPONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static",
    "coupons",
)


def _parse_bookmaker_id_values(values: Optional[List[str]]) -> List[int]:
    bookmaker_ids: List[int] = []
    for raw_value in values or []:
        if raw_value is None:
            continue
        for part in str(raw_value).split(","):
            clean = part.strip()
            if not clean:
                continue
            try:
                bookmaker_id = int(clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Некорректный ID букмекера"
                )
            if bookmaker_id not in bookmaker_ids:
                bookmaker_ids.append(bookmaker_id)
    return bookmaker_ids


def _merge_bookmaker_ids(
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
) -> List[int]:
    selected_ids: List[int] = []
    if bookmaker_id:
        selected_ids.append(bookmaker_id)
    for selected_id in bookmaker_ids or []:
        if selected_id and selected_id not in selected_ids:
            selected_ids.append(selected_id)
    return selected_ids


def _bookmaker_links_from_form(form_data) -> Optional[List[str]]:
    values = [
        str(value)
        for value in form_data.getlist("bookmaker_links")
        if value is not None
    ]
    values.extend(
        str(value)
        for value in form_data.getlist("bookmaker_links[]")
        if value is not None
    )
    return values if values else None


async def _store_coupon_image(coupon_image: Optional[UploadFile]) -> Optional[str]:
    if not coupon_image or not coupon_image.filename:
        return None

    allowed_ext = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ext = os.path.splitext(coupon_image.filename)[1].lower()
    if ext not in allowed_ext:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Недопустимый формат файла. Разрешены: {', '.join(sorted(allowed_ext))}"
        )

    contents = await coupon_image.read()
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Файл слишком большой. Максимум 5 МБ."
        )

    os.makedirs(STATIC_COUPONS_DIR, exist_ok=True)
    filename = f"{uuid_pkg.uuid4().hex}{ext}"
    filepath = os.path.join(STATIC_COUPONS_DIR, filename)
    with open(filepath, "wb") as file_obj:
        file_obj.write(contents)
    return f"/static/coupons/{filename}"


async def _build_bet_response(
    db: AsyncSession,
    admin: User,
    *,
    event_name: str,
    coefficient: Decimal,
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
    description: Optional[str],
    category: str,
    live_ends_at: Optional[datetime],
    price_stars: Optional[int],
    brain_score: Optional[int],
    api_match_id: Optional[str],
    sport_type: Optional[str],
    outcome: Optional[str],
    coupon_image_url: Optional[str],
    match_link: Optional[str],
    bookmaker_links: Optional[object] = None,
    delivery_mode: str = "feed",
) -> BetResponse:
    selected_bookmaker_ids = _merge_bookmaker_ids(bookmaker_id, bookmaker_ids)
    selected_bookmakers = []
    if selected_bookmaker_ids:
        bk_res = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(selected_bookmaker_ids)))
        bookmakers_by_id = {bookmaker.id: bookmaker for bookmaker in bk_res.scalars().all()}
        missing_ids = [selected_id for selected_id in selected_bookmaker_ids if selected_id not in bookmakers_by_id]
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Selected bookmaker not found: {', '.join(map(str, missing_ids))}"
            )
        selected_bookmakers = [bookmakers_by_id[selected_id] for selected_id in selected_bookmaker_ids]

    primary_bookmaker_id = selected_bookmaker_ids[0] if selected_bookmaker_ids else None

    bet = Bet(
        event_name=event_name,
        coefficient=coefficient,
        bookmaker_id=primary_bookmaker_id,
        description=description,
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=sport_type,
        outcome=outcome,
        coupon_image_url=coupon_image_url,
        match_link=match_link,
        bookmaker_links=normalize_bookmaker_links(
            bookmaker_links,
            allowed_bookmaker_ids=selected_bookmaker_ids,
        ),
        delivery_mode=delivery_mode,
        status="pending",
        author_id=admin.telegram_id
    )
    bet.bookmakers = selected_bookmakers
    db.add(bet)
    await db.commit()

    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet.id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    return result.scalars().first()


def _send_live_alarm_to_all_users(
    *,
    user_ids: List[int],
    event_name: str,
    coefficient: Decimal,
    brain_score: Optional[int],
):
    alarm_text = (
        "⚡⚡⚡ SHAMRAI LIVE SIGNAL ALARM ⚡⚡⚡\n\n"
        f"Новый срочный Live-прогноз от Shamrai:\n"
        f"🏆 {event_name}\n"
        f"📈 Коэффициент: {float(coefficient):.2f}\n\n"
        "Быстрее заходите в приложение Shamrai Analytics Hub!"
    )
    for user_id in user_ids:
        call_telegram_api("sendMessage", {
            "chat_id": user_id,
            "text": alarm_text
        })

async def has_active_subscription(user: User, db: AsyncSession) -> bool:
    """Compatibility helper: access is now based on match balance or active guarantee."""
    return (
        is_staff_role(user.role)
        or (user.purchased_bets_balance or 0) > 0
        or (user.matches_remaining or 0) > 0
        or bool(user.guarantee_active)
    )

# --- SUBSCRIBER ENDPOINTS ---

@router.get("/feed", response_model=List[BetResponse])
async def get_bet_feed(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Returns active ('pending') bets.
    Filters bets so that the user only sees those matching bookmakers in their profile.
    Computes is_unlocked status.
    """
    user_bk_ids = [bk.id for bk in current_user.bookmakers]
    
    # Filter: select only pending bets
    query = (
        select(Bet)
        .filter(Bet.status == "pending", Bet.delivery_mode == "feed")
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    
    if not is_staff_role(current_user.role):
        untargeted_filter = and_(Bet.bookmaker_id.is_(None), ~Bet.bookmakers.any())
        if user_bk_ids:
            query = query.filter(or_(
                untargeted_filter,
                Bet.bookmaker_id.in_(user_bk_ids),
                Bet.bookmakers.any(Bookmaker.id.in_(user_bk_ids)),
            ))
        else:
            # If no bookmakers selected, show only bets without a specific bookmaker required
            query = query.filter(untargeted_filter)
            
    query = query.order_by(Bet.created_at.desc())
    result = await db.execute(query)
    bets = result.scalars().all()
    
    has_access = await has_active_subscription(current_user, db)
    
    # Get all bet IDs currently unlocked/taken by the user
    taken_bets_res = await db.execute(
        select(user_bets.c.bet_id).filter(user_bets.c.user_id == current_user.telegram_id)
    )
    taken_bet_ids = {r[0] for r in taken_bets_res.all()}
    
    # Set virtual is_unlocked flag and construct response list
    response_bets = []
    for bet in bets:
        if is_staff_role(current_user.role):
            is_unlocked = True
        else:
            if bet.price_stars and bet.price_stars > 0:
                is_unlocked = bet.id in taken_bet_ids
            else:
                # Free bets/subscription bets are unlocked for everyone (or based on subscription)
                is_unlocked = True
                
        # Validate into Pydantic model
        p_bet = BetResponse.model_validate(bet)
        p_bet.is_unlocked = is_unlocked
        p_bet.is_taken = bet.id in taken_bet_ids
        
        # Mask details if locked
        if not is_unlocked:
            p_bet.event_name = "🔒 Прогноз скрыт до покупки"
            p_bet.outcome = "🔒 Скрыто"
            p_bet.description = f"Купите этот прогноз за {bet.price_stars} Stars, чтобы увидеть исход и описание."
            p_bet.match_link = None
            p_bet.coupon_image_url = None
            p_bet.api_match_id = None
            p_bet.brain_score = None
            
        response_bets.append(p_bet)
        
    return response_bets


@router.post("/{bet_id}/take", status_code=status.HTTP_200_OK)
async def take_bet(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Adds a bet to the user's tracking list for stats calculation."""
    bet_res = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = bet_res.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден"
        )
    if bet.delivery_mode != "feed" and not is_staff_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден"
        )

    has_sub = await has_active_subscription(current_user, db)
    is_bet_free = (bet.price_stars is None or bet.price_stars == 0)

    if not has_sub and not is_bet_free:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пополните абонемент матчами или купите этот прогноз за звезды"
        )

    access_result = await record_user_bet_access(
        db,
        user=current_user,
        bet=bet,
        charge_match=has_sub,
        allow_negative_balance=False,
        free_access_type="free_bet",
        note="Match debited when user added bet to My Bets",
    )
    await db.commit()
    if access_result.already_recorded:
        return {"status": "already_taken", "message": "Bet is already tracked in your profile"}
    return {"status": "success", "message": "Bet added to tracking list"}

@router.post("/{bet_id}/unlock_free", status_code=status.HTTP_200_OK)
async def unlock_free_bet(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/bets/{bet_id}/unlock_free
    Unlocks a single bet using the user's free bet balance.
    """
    if current_user.free_bets_available <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="У вас нет доступных бесплатных прогнозов"
        )

    bet_res = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = bet_res.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bet not found"
        )

    # Check if already unlocked (present in user_bets)
    check_query = select(user_bets).filter(
        and_(user_bets.c.user_id == current_user.telegram_id, user_bets.c.bet_id == bet_id)
    )
    existing = (await db.execute(check_query)).first()
    if existing:
        return {"status": "already_unlocked", "message": "Прогноз уже открыт"}

    # Add to user_bets (this serves as the unlocked list)
    insert_stmt = user_bets.insert().values(
        user_id=current_user.telegram_id,
        bet_id=bet_id,
        taken_at=func.now(),
        access_type="free_bet",
        match_charged=False,
    )
    await db.execute(insert_stmt)
    
    # Deduct free bet
    current_user.free_bets_available -= 1
    
    await db.commit()
    return {"status": "success", "message": "Прогноз успешно разблокирован!"}


@router.post("/{bet_id}/buy-hint", response_model=BetHintResponse)
async def buy_bet_hint(
    bet_id: UUID,
    payload: BetHintRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/bets/{bet_id}/buy-hint
    Unlocks only the analytical note without revealing the final pick/outcome.
    """
    if payload.amount_xtr < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Стоимость подсказки должна быть больше 0 XTR"
        )

    result = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден"
        )

    hint = bet.description
    if not hint:
        hint = (
            f"Shamrai Brain видит value в матче '{bet.event_name}': темп, линия и риск сходятся, "
            "но финальный исход остается закрытым до покупки прогноза."
        )

    return BetHintResponse(
        bet_id=bet.id,
        paid_xtr=payload.amount_xtr,
        hint=hint,
    )


@router.get("/stats", response_model=UserStats)
async def get_user_stats(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Calculates statistics for dashboard display."""
    query = select(Bet).join(user_bets).filter(user_bets.c.user_id == current_user.telegram_id)
    result = await db.execute(query)
    bets = result.scalars().all()
    
    total = len(bets)
    won = 0
    lost = 0
    refunded = 0
    profit = Decimal("0.00")
    coefficient_sum = Decimal("0.00")
    
    for bet in bets:
        coefficient_sum += bet.coefficient
        if bet.status == "win":
            won += 1
            profit += (bet.coefficient - Decimal("1.00"))
        elif bet.status == "loss":
            lost += 1
            profit -= Decimal("1.00")
        elif bet.status == "refund":
            refunded += 1
            
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / total * 100) if total > 0 else 0.0
    average_coefficient = float(coefficient_sum / Decimal(total)) if total > 0 else 0.0
    
    return UserStats(
        total_bets_taken=total,
        won_bets=won,
        lost_bets=lost,
        refund_bets=refunded,
        net_profit=profit,
        winrate=round(winrate, 2),
        roi=round(roi, 2),
        average_coefficient=round(average_coefficient, 2)
    )

# --- ADMIN ENDPOINTS ---

@router.post("/", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
@router.post("", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
async def create_bet(
    bet_data: BetCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Publish a new bet recommendation."""
    bet = await _build_bet_response(
        db,
        admin,
        event_name=bet_data.event_name,
        coefficient=bet_data.coefficient,
        bookmaker_id=bet_data.bookmaker_id,
        bookmaker_ids=bet_data.bookmaker_ids or bet_data.target_bookmaker_ids,
        description=bet_data.description,
        category=bet_data.category,
        live_ends_at=bet_data.live_ends_at,
        price_stars=bet_data.price_stars,
        brain_score=bet_data.brain_score,
        api_match_id=bet_data.api_match_id,
        sport_type=bet_data.sport_type,
        outcome=bet_data.outcome,
        coupon_image_url=bet_data.coupon_image_url,
        match_link=bet_data.match_link,
        bookmaker_links=bet_data.bookmaker_links,
    )

    # Process Live Alarm
    if bet_data.live_alarm:
        res_users = await db.execute(select(User.telegram_id))
        user_ids = res_users.scalars().all()
        _send_live_alarm_to_all_users(
            user_ids=user_ids,
            event_name=bet_data.event_name,
            coefficient=bet_data.coefficient,
            brain_score=bet_data.brain_score,
        )

    return bet


@router.post("/with-coupon", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
async def create_bet_with_coupon(
    request: Request,
    event_name: str = Form(...),
    coefficient: Decimal = Form(...),
    bookmaker_id: Optional[int] = Form(None),
    description: Optional[str] = Form(None),
    category: str = Form("prematch"),
    live_ends_at: Optional[datetime] = Form(None),
    price_stars: Optional[int] = Form(None),
    brain_score: Optional[int] = Form(5),
    api_match_id: Optional[str] = Form(None),
    sport_type: Optional[str] = Form(None),
    outcome: Optional[str] = Form(None),
    match_link: Optional[str] = Form(None),
    live_alarm: Optional[bool] = Form(False),
    coupon_image: Optional[UploadFile] = File(None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Publish a bet recommendation with an uploaded coupon screenshot."""
    coupon_image_url = await _store_coupon_image(coupon_image)
    form = await request.form()
    selected_bookmaker_ids = _parse_bookmaker_id_values(form.getlist("bookmaker_ids"))
    selected_bookmaker_ids.extend(
        selected_id
        for selected_id in _parse_bookmaker_id_values(form.getlist("target_bookmaker_ids"))
        if selected_id not in selected_bookmaker_ids
    )

    bet = await _build_bet_response(
        db,
        admin,
        event_name=event_name,
        coefficient=coefficient,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        description=description,
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=sport_type,
        outcome=outcome,
        coupon_image_url=coupon_image_url,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form),
    )

    if live_alarm:
        res_users = await db.execute(select(User.telegram_id))
        user_ids = res_users.scalars().all()
        _send_live_alarm_to_all_users(
            user_ids=user_ids,
            event_name=event_name,
            coefficient=coefficient,
            brain_score=brain_score,
        )

    return bet

@router.put("/{bet_id}/resolve", response_model=BetResponse)
async def resolve_bet(
    bet_id: UUID,
    resolution: BetResolve,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Settle bet as win, loss, or refund."""
    if resolution.status not in ["win", "loss", "refund"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution status must be one of: win, loss, refund"
        )
        
    result = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bet not found"
        )
        
    previous_status = bet.status
    bet.status = resolution.status
    bet.resolved_at = datetime.now(timezone.utc)

    supercompensation_count = 0
    refund_count = 0
    if previous_status == "pending":
        takers_res = await db.execute(
            select(
                user_bets.c.user_id,
                user_bets.c.match_charged,
                user_bets.c.access_type,
            ).filter(user_bets.c.bet_id == bet_id)
        )
        takers = takers_res.all()
        taker_ids = [row[0] for row in takers]
        users_by_id = {}
        if taker_ids:
            users_res = await db.execute(select(User).filter(User.telegram_id.in_(taker_ids)))
            users_by_id = {u.telegram_id: u for u in users_res.scalars().all()}

        for user_id, match_charged, access_type in takers:
            user = users_by_id.get(user_id)
            if not user or is_staff_role(user.role):
                continue

            if resolution.status == "loss" and match_charged and access_type == "paid_match":
                current_balance = int(user.purchased_bets_balance or user.matches_remaining or 0)
                next_balance = current_balance + 2
                user.purchased_bets_balance = next_balance
                user.matches_remaining = next_balance
                supercompensation_count += 1
                db.add(log_match_balance_event(
                    user_id=user_id,
                    bet_id=bet_id,
                    event_type="supercompensation_loss",
                    delta_matches=2,
                    note="Loss supercompensation: charged stake returned and +1 bonus stake added",
                ))
                call_telegram_api("sendMessage", {
                    "chat_id": user_id,
                    "text": (
                        "⚡ Сверхкомпенсация Shamrai активирована.\n\n"
                        f"Прогноз «{bet.event_name}» закрыт минусом, поэтому мы вернули списанную ставку "
                        "и начислили +1 бонусную ставку сверху. Баланс пакета увеличен на 2."
                    ),
                })
            elif resolution.status == "win":
                call_telegram_api("sendMessage", {
                    "chat_id": user_id,
                    "text": (
                        "🔥 Прогноз Shamrai рассчитан в плюс!\n\n"
                        f"Матч «{bet.event_name}» успешно закрыт победой. 🧠 "
                        "Списание купона произведено честно, ваш банк увеличен. Работаем дальше.🤝"
                    ),
                })

    await db.commit()

    # Trigger badges achievements calculation for each user who took this bet
    if resolution.status == "win":
        # Fetch user IDs of all takers
        takers_res = await db.execute(
            select(user_bets.c.user_id).filter(user_bets.c.bet_id == bet_id)
        )
        taker_ids = [r[0] for r in takers_res.all()]
        
        from src.models.models import UserBadge
        for u_id in taker_ids:
            # Query user's last 5 resolved taken bets
            last_bets_query = (
                select(Bet.status)
                .join(user_bets)
                .filter(user_bets.c.user_id == u_id, Bet.status.in_(["win", "loss", "refund"]))
                .order_by(user_bets.c.taken_at.desc())
                .limit(5)
            )
            last_bets_res = await db.execute(last_bets_query)
            statuses = last_bets_res.scalars().all()
            
            # Check if they have 5 consecutive wins
            if len(statuses) >= 5 and all(s == "win" for s in statuses):
                # Check if badge already exists
                badge_check = await db.execute(
                    select(UserBadge).filter(UserBadge.user_id == u_id, UserBadge.icon_type == "sharp_mind")
                )
                if not badge_check.scalars().first():
                    # Award the badge
                    new_badge = UserBadge(
                        user_id=u_id,
                        title="Острый Ум",
                        icon_type="sharp_mind"
                    )
                    db.add(new_badge)
                    
        await db.commit()
    
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    resolved_bet = result.scalars().first()
    resolved_bet.guarantee_count = supercompensation_count
    resolved_bet.supercompensation_count = supercompensation_count
    resolved_bet.refund_count = refund_count
    return resolved_bet

@router.get("/analytics", response_model=AdminAnalytics)
async def get_admin_analytics(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Fetch platform-wide analytics."""
    user_count_res = await db.execute(select(func.count(User.telegram_id)))
    total_users = user_count_res.scalar() or 0
    
    sub_count_res = await db.execute(
        select(func.count(User.telegram_id)).filter(
            (User.purchased_bets_balance > 0) | (User.matches_remaining > 0) | (User.guarantee_active == True)
        )
    )
    active_subs = sub_count_res.scalar() or 0
    
    bets_res = await db.execute(select(Bet))
    bets = bets_res.scalars().all()
    
    total_bets = len(bets)
    won = 0
    lost = 0
    profit = Decimal("0.00")
    coefficient_sum = Decimal("0.00")
    
    for bet in bets:
        coefficient_sum += bet.coefficient
        if bet.status == "win":
            won += 1
            profit += (bet.coefficient - Decimal("1.00"))
        elif bet.status == "loss":
            lost += 1
            profit -= Decimal("1.00")
            
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / total_bets * 100) if total_bets > 0 else 0.0
    average_coefficient = float(coefficient_sum / Decimal(total_bets)) if total_bets > 0 else 0.0
    
    return AdminAnalytics(
        total_subscribers=total_users,
        active_subscriptions=active_subs,
        total_bets_issued=total_bets,
        winrate=round(winrate, 2),
        roi=round(roi, 2),
        net_profit=profit,
        average_coefficient=round(average_coefficient, 2)
    )


from src.models.models import UserNote
from src.schemas.schemas import UserNoteCreate, UserNoteResponse
from typing import Optional

@router.get("/{bet_id}/notes", response_model=Optional[UserNoteResponse])
async def get_bet_note(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """GET /api/bets/{bet_id}/notes — Retrieve capper diary note for this prediction."""
    query = select(UserNote).filter(UserNote.user_id == current_user.telegram_id, UserNote.bet_id == bet_id)
    res = await db.execute(query)
    return res.scalars().first()


@router.post("/{bet_id}/notes", response_model=UserNoteResponse)
async def save_bet_note(
    bet_id: UUID,
    note_data: UserNoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """POST /api/bets/{bet_id}/notes — Create or update capper diary note for this prediction."""
    query = select(UserNote).filter(UserNote.user_id == current_user.telegram_id, UserNote.bet_id == bet_id)
    res = await db.execute(query)
    note = res.scalars().first()
    
    if note:
        note.text = note_data.text
        note.emotion_score = note_data.emotion_score
    else:
        note = UserNote(
            user_id=current_user.telegram_id,
            bet_id=bet_id,
            text=note_data.text,
            emotion_score=note_data.emotion_score
        )
        db.add(note)
        
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/match/{api_match_id}")
async def get_live_match_score(api_match_id: str):
    """
    GET /api/bets/match/{api_match_id}
    Debug-only live score simulator. Production must use a real live-score provider.
    """
    if not settings.DEBUG_MODE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live score provider is not configured",
        )

    import random
    import time
    
    seed_val = sum(ord(c) for c in api_match_id)
    random.seed(seed_val)
    
    teams = [
        ("Реал Мадрид", "Бавария"),
        ("Ливерпуль", "Манчестер Сити"),
        ("Барселона", "ПСЖ"),
        ("Арсенал", "Челси"),
        ("Ювентус", "Милан")
    ]
    
    team_a, team_b = random.choice(teams)
    
    # Minute increases based on time
    sec = int(time.time())
    minute = 65 + (sec % 25) # simulates min 65 to 90
    
    score_a = (seed_val + (sec // 60)) % 3
    score_b = (seed_val // 2 + (sec // 90)) % 3
    
    return {
        "api_match_id": api_match_id,
        "team_a": team_a,
        "team_b": team_b,
        "score_a": score_a,
        "score_b": score_b,
        "minute": minute,
        "status": "Live" if minute < 90 else "Finished"
    }
