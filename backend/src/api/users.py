import csv
import io
from html import escape
from io import BytesIO, StringIO

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import Any, List, Optional
from pydantic import BaseModel
from datetime import datetime, timedelta, timezone

from src.models.database import get_db, get_read_db
from src.models.models import User, Bookmaker, Subscription, Bet, user_bets
from src.schemas.schemas import (
    BetResponse,
    BookmakerResponse,
    OnboardRequest,
    OnboardResponse,
    SubscriptionResponse,
    UserResponse,
    UserUpdateBankroll,
    UserUpdateBookmakers,
)
from src.api.deps import get_current_user, get_current_user_read, get_current_admin
from src.core.bookmakers import ensure_standard_bookmakers
from src.core.config import settings
from src.core.quiet_hours import (
    DEFAULT_NIGHT_MODE_END,
    DEFAULT_NIGHT_MODE_START,
    normalize_quiet_time,
    quiet_time_or_default,
)
from src.core.roles import is_staff_role
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, enqueue_delivery
from src.services.referrals import get_referral_stats
from src.services.vk_delivery import (
    refresh_vk_delivery_status,
    vk_delivery_configured,
    vk_group_id,
)
from src.services.statistics import (
    build_performance_payload,
    filter_items_by_period,
    is_paid_client_access,
    normalize_period,
    period_start,
    stat_item_from_bet,
    summarize_items,
)

router = APIRouter(tags=["Users"])

EXPERIENCE_LEVELS = {"novice", "amateur", "pro"}
BANKROLL_SIZES = {"micro", "mid", "high"}
RISK_TOLERANCES = {"cautious", "balanced", "aggressive"}
BOOKMAKER_CODES = {
    "fonbet",
    "betboom",
    "winline",
    "pari",
    "ligastavok",
    "marathon",
    "betcity",
    "melbet",
    "leon",
    "olimpbet",
    "zenit",
    "other",
}
ALL_SPORT_LABELS = [
    "Автогонки",
    "Ам. футбол",
    "Бадминтон",
    "Баскетбол",
    "Бейсбол",
    "Бильярд",
    "Бокс",
    "Велоспорт",
    "Вод. поло",
    "Водные виды",
    "Волейбол",
    "Гандбол",
    "Гимнастика",
    "Гольф",
    "Дартс",
    "Другие",
    "Единоборства",
    "Киберспорт",
    "Коньки",
    "Крикет",
    "Л/Атл",
    "Лыжи/Биатлон",
    "Н/Т",
    "Пляж. футб",
    "Регби",
    "Сани/Бобслей",
    "Теннис",
    "Футбол",
    "Футзал",
    "Хоккей",
]
ALERT_MIN_COEF_MIN = 1.0
ALERT_MIN_COEF_MAX = 1.6

EXPERIENCE_LABELS = {
    "novice": "Новичок",
    "amateur": "Любитель",
    "pro": "Профи",
}
BANKROLL_LABELS = {
    "micro": "До 30 000 ₽",
    "mid": "50 000 - 100 000 ₽",
    "high": "Более 100 000 ₽",
}
RISK_LABELS = {
    "cautious": "Осторожная",
    "balanced": "Сбалансированная",
    "aggressive": "Агрессивная",
}

class AdminUserListResponse(BaseModel):
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    role: str
    stats_display_mode: str
    has_active_subscription: bool
    subscription_end_date: Optional[datetime]
    bookmakers: List[BookmakerResponse]

    class Config:
        from_attributes = True


async def load_user_response(db: AsyncSession, telegram_id: int) -> User:
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == telegram_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    return result.scalars().first()


async def load_user_or_404(db: AsyncSession, telegram_id: int) -> User:
    user = await load_user_response(db, telegram_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user


def build_preferences_payload(user: User) -> dict[str, Any]:
    return {
        "alert_min_coef": min(
            ALERT_MIN_COEF_MAX,
            max(ALERT_MIN_COEF_MIN, user.alert_min_coef or ALERT_MIN_COEF_MIN),
        ),
        "odds_drop_notifications_enabled": user.odds_drop_notifications_enabled,
        "is_night_mode": user.is_night_mode,
        "night_mode_start": quiet_time_or_default(
            getattr(user, "night_mode_start", None),
            DEFAULT_NIGHT_MODE_START,
        ),
        "night_mode_end": quiet_time_or_default(
            getattr(user, "night_mode_end", None),
            DEFAULT_NIGHT_MODE_END,
        ),
        "preferred_sports": user.preferred_sports or ALL_SPORT_LABELS,
        "stats_display_mode": user.stats_display_mode,
    }


async def build_payment_history_payload(db: AsyncSession, user_id: int) -> dict[str, Any]:
    query = (
        select(Subscription)
        .filter(Subscription.user_id == user_id)
        .options(selectinload(Subscription.plan))
        .order_by(Subscription.created_at.desc())
    )
    result = await db.execute(query)
    subs = result.scalars().all()

    transactions = []
    for sub in subs:
        amount_stars = sub.plan.price_stars if sub.plan else None
        transactions.append({
            "id": str(sub.id),
            "plan_name": sub.plan.name if sub.plan else "Неизвестный тариф",
            "amount": amount_stars if amount_stars is not None else "—",
            "amount_currency": f"{sub.plan.price} {sub.plan.currency}" if sub.plan else "—",
            "amount_stars": amount_stars,
            "payment_provider": sub.payment_provider or "—",
            "status": sub.status,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "start_date": sub.start_date.isoformat() if sub.start_date else None,
            "end_date": sub.end_date.isoformat() if sub.end_date else None,
        })

    return {"transactions": transactions, "total": len(transactions)}


async def build_subscription_status_payload(db: AsyncSession, user_id: int) -> Optional[dict[str, Any]]:
    result = await db.execute(
        select(Subscription)
        .filter(Subscription.user_id == user_id)
        .options(selectinload(Subscription.plan))
        .order_by(Subscription.created_at.desc())
    )
    subscription = result.scalars().first()
    if not subscription:
        return None
    return SubscriptionResponse.model_validate(subscription).model_dump(mode="json")


async def build_referral_payload(db: AsyncSession, user: User) -> dict[str, Any]:
    stats = await get_referral_stats(db, user.telegram_id)
    return {
        "referral_code": f"SHAMRAI_{user.telegram_id}",
        "referral_link": f"https://t.me/Shamra1_bot?start=ref_{user.telegram_id}",
        "invited_count": stats["invited_count"],
        "purchased_invited_count": stats["purchased_invited_count"],
        "discount_step_percent": stats["discount_step_percent"],
        "referral_discount_percent": stats["referral_discount_percent"],
        "earned_bonus_days": 0,
        "pending_rewards": 0,
    }


def build_stored_vk_delivery_payload(user: User) -> dict[str, Any]:
    return {
        "vk_user_id": user.vk_user_id,
        "group_id": vk_group_id(),
        "configured": vk_delivery_configured(),
        "group_member": bool(user.vk_group_member),
        "messages_allowed": bool(user.vk_messages_allowed),
        "notifications_allowed": bool(user.vk_notifications_allowed),
    }


def validate_onboarding_payload(data: OnboardRequest) -> None:
    if data.experience_level not in EXPERIENCE_LEVELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid experience_level"
        )
    if data.bankroll_size not in BANKROLL_SIZES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid bankroll_size"
        )
    if data.risk_tolerance not in RISK_TOLERANCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid risk_tolerance"
        )
    selected_bookmakers = set(data.bookmakers or [])
    if data.primary_bookmaker:
        selected_bookmakers.add(data.primary_bookmaker)
    if not selected_bookmakers and not data.bookmaker_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select at least one bookmaker"
        )
    invalid_bookmakers = sorted(code for code in selected_bookmakers if code not in BOOKMAKER_CODES)
    if invalid_bookmakers:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid bookmakers: {invalid_bookmakers}"
        )
    if normalize_currency(data.currency_preference) not in {"RUB", "USD", "FLATS"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid currency_preference"
        )


def validate_onboarding_vk_link(user: User, data: OnboardRequest) -> None:
    payload_vk_user_id = (data.vk_user_id or "").strip()
    if payload_vk_user_id and payload_vk_user_id != (user.vk_user_id or ""):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="vk_user_id must match the linked VK profile",
        )


def normalize_currency(currency: str) -> str:
    value = (currency or "RUB").upper()
    if value == "USDT":
        return "FLATS"
    return value


async def build_onboarding_recommendation(
    db: AsyncSession,
    data: OnboardRequest,
):
    flat_by_risk = {
        "cautious": 1.5,
        "balanced": 2.5,
        "aggressive": 3.5,
    }
    experience_adjustment = {
        "novice": -0.25,
        "amateur": 0.25,
        "pro": 0.5,
    }
    bankroll_amounts = {
        "micro": 30_000,
        "mid": 75_000,
        "high": 150_000,
    }

    flat_stake_percent = max(
        1.0,
        min(5.0, flat_by_risk[data.risk_tolerance] + experience_adjustment[data.experience_level]),
    )

    since = datetime.now(timezone.utc) - timedelta(hours=24)
    result = await db.execute(
        select(Bet)
        .filter(Bet.status.in_(["win", "loss", "refund"]))
        .filter(func.coalesce(Bet.resolved_at, Bet.created_at) >= since)
    )
    resolved_bets = result.scalars().all()

    unit_profit = 0.0
    for bet in resolved_bets:
        if bet.status == "win":
            unit_profit += max(float(bet.coefficient or 1) - 1.0, 0)
        elif bet.status == "loss":
            unit_profit -= 1.0

    if resolved_bets:
        missed_profit_percent = max(0.0, unit_profit * flat_stake_percent)
        channel_roi = (unit_profit / len(resolved_bets)) * 100
        source = "channel_24h_resolved_bets"
    else:
        fallback_roi_by_risk = {
            "cautious": 4.8,
            "balanced": 7.4,
            "aggressive": 9.2,
        }
        missed_profit_percent = fallback_roi_by_risk[data.risk_tolerance]
        channel_roi = missed_profit_percent / max(flat_stake_percent, 1)
        source = "simulated_from_empty_24h_window"

    experience_multiplier = {
        "novice": 0.88,
        "amateur": 1.0,
        "pro": 1.08,
    }
    risk_multiplier = {
        "cautious": 0.82,
        "balanced": 1.0,
        "aggressive": 1.16,
    }
    monthly_profit_percent = max(
        14.0,
        min(48.0, (abs(channel_roi) * 2.4 + flat_stake_percent * 5.5)
            * experience_multiplier[data.experience_level]
            * risk_multiplier[data.risk_tolerance]),
    )
    missed_profit_amount = bankroll_amounts[data.bankroll_size] * missed_profit_percent / 100

    return {
        "flat_stake_percent": round(flat_stake_percent, 2),
        "monthly_profit_percent": round(monthly_profit_percent, 1),
        "missed_profit_percent_24h": round(missed_profit_percent, 1),
        "missed_profit_amount_24h": round(missed_profit_amount, 2),
        "currency": normalize_currency(data.currency_preference),
        "source": source,
        "resolved_bets_24h": len(resolved_bets),
    }


def _onboarding_report_line(label: str, value: object) -> str:
    rendered = str(value).strip() if value is not None else "не указано"
    return f"<b>{escape(label)}:</b> {escape(rendered or 'не указано')}"


def _format_onboarding_report(
    user: User,
    data: OnboardRequest,
    selected_bookmakers: List[Bookmaker],
    recommendation: dict,
) -> str:
    display_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    username = f"@{user.username}" if user.username else None
    client_label = " / ".join(part for part in [display_name, username] if part) or "без имени"
    selected_bookmaker_names = ", ".join(bookmaker.name for bookmaker in selected_bookmakers) or "не указано"
    pains = "; ".join(data.anti_capper_pains or []) or "не указано"

    lines = [
        "<b>Новая анкета приветственного опроса</b>",
        _onboarding_report_line("Клиент", client_label),
        _onboarding_report_line("Telegram ID", user.telegram_id),
        "",
        _onboarding_report_line("Что раздражает", pains),
        _onboarding_report_line("Опыт", EXPERIENCE_LABELS.get(data.experience_level, data.experience_level)),
        _onboarding_report_line("Банк", BANKROLL_LABELS.get(data.bankroll_size, data.bankroll_size)),
        _onboarding_report_line("Риск", RISK_LABELS.get(data.risk_tolerance, data.risk_tolerance)),
        _onboarding_report_line("БК", selected_bookmaker_names),
        _onboarding_report_line("VK", f"привязан {user.vk_user_id}" if user.vk_user_id else "пропущен"),
    ]

    if data.other_bookmaker_name and data.other_bookmaker_name.strip():
        lines.append(_onboarding_report_line("Другие БК", data.other_bookmaker_name.strip()))

    lines.extend([
        _onboarding_report_line("Валюта", normalize_currency(data.currency_preference)),
        "",
        _onboarding_report_line("Рекомендованный флэт", f"{recommendation['flat_stake_percent']}%"),
        _onboarding_report_line("Потенциал в месяц", f"+{recommendation['monthly_profit_percent']}%"),
        _onboarding_report_line("Упущено за 24 часа", f"+{recommendation['missed_profit_percent_24h']}%"),
    ])

    return "\n".join(lines)


async def enqueue_onboarding_report(
    db: AsyncSession,
    user: User,
    data: OnboardRequest,
    selected_bookmakers: List[Bookmaker],
    recommendation: dict,
) -> None:
    chat_id = settings.SHAMRAI_ONBOARDING_REPORT_CHAT_ID
    if not chat_id:
        return

    message = _format_onboarding_report(user, data, selected_bookmakers, recommendation)
    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=user.telegram_id,
        payload={
            "method": "sendMessage",
            "payload": {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
        },
    )


async def save_onboarding_profile(
    db: AsyncSession,
    user: User,
    data: OnboardRequest,
) -> OnboardResponse:
    validate_onboarding_payload(data)
    validate_onboarding_vk_link(user, data)
    recommendation = await build_onboarding_recommendation(db, data)
    active_bookmakers = await ensure_standard_bookmakers(db)
    bookmakers_by_id = {bookmaker.id: bookmaker for bookmaker in active_bookmakers}
    bookmakers_by_code = {bookmaker.code: bookmaker for bookmaker in active_bookmakers}
    selected_bookmakers = []
    selected_codes = list(dict.fromkeys(data.bookmakers or []))
    if data.primary_bookmaker and data.primary_bookmaker not in selected_codes:
        selected_codes.insert(0, data.primary_bookmaker)

    for code in selected_codes:
        bookmaker = bookmakers_by_code.get(code)
        if bookmaker and bookmaker not in selected_bookmakers:
            selected_bookmakers.append(bookmaker)

    for bookmaker_id in data.bookmaker_ids:
        bookmaker = bookmakers_by_id.get(bookmaker_id)
        if bookmaker and bookmaker not in selected_bookmakers:
            selected_bookmakers.append(bookmaker)

    missing_bookmaker_ids = [
        bookmaker_id
        for bookmaker_id in data.bookmaker_ids
        if bookmaker_id not in bookmakers_by_id
    ]
    if missing_bookmaker_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid bookmaker_ids: {missing_bookmaker_ids}"
        )

    user.experience_level = data.experience_level
    user.bankroll_size = data.bankroll_size
    user.risk_tolerance = data.risk_tolerance
    user.primary_bookmaker = selected_bookmakers[0].code if selected_bookmakers else None
    user.bookmakers = selected_bookmakers
    has_other_bookmaker = any(bookmaker.code == "other" for bookmaker in selected_bookmakers)
    user.other_bookmaker_name = (
        data.other_bookmaker_name.strip()
        if has_other_bookmaker and data.other_bookmaker_name and data.other_bookmaker_name.strip()
        else None
    )
    user.currency_preference = normalize_currency(data.currency_preference)
    user.free_bets_available = 0
    user.favorite_sports = []
    user.preferred_sports = ALL_SPORT_LABELS
    user.is_onboarded = True

    telegram_id = user.telegram_id
    await db.flush()
    hydrated_user = await load_user_or_404(db, telegram_id)
    await enqueue_onboarding_report(db, hydrated_user, data, selected_bookmakers, recommendation)

    return OnboardResponse(
        status="success",
        message="Shamrai neural calibration completed",
        recommendation=recommendation,
        user=hydrated_user,
    )


# --- BOOKMAKERS LIST ENDPOINT ---

@router.get("/bookmakers", response_model=List[BookmakerResponse])
async def list_bookmakers(db: AsyncSession = Depends(get_read_db)):
    """GET /api/bookmakers/ — Returns a list of all active platforms in the system."""
    return await ensure_standard_bookmakers(db)

@router.get("/users/me", response_model=UserResponse)
async def get_my_profile(current_user: User = Depends(get_current_user_read)):
    """GET /api/users/me — Returns the current user profile including badges and bookmakers."""
    return current_user


@router.get("/users/me/profile-dashboard")
async def get_my_profile_dashboard(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_db),
):
    """Aggregated profile payload for fast profile screen boot."""
    user = await load_user_or_404(db, current_user.telegram_id)
    if user.vk_user_id:
        await refresh_vk_delivery_status(db, user, commit=True)
    bookmakers = await ensure_standard_bookmakers(db)
    return {
        "user": UserResponse.model_validate(user).model_dump(mode="json"),
        "bookmakers": [
            BookmakerResponse.model_validate(bookmaker).model_dump(mode="json")
            for bookmaker in bookmakers
        ],
        "selected_bookmaker_ids": [bookmaker.id for bookmaker in user.bookmakers],
        "preferences": build_preferences_payload(user),
        "subscription": await build_subscription_status_payload(db, user.telegram_id),
        "payments": await build_payment_history_payload(db, user.telegram_id),
        "referral": await build_referral_payload(db, user),
        "vk_delivery_status": build_stored_vk_delivery_payload(user),
    }

# --- SUBSCRIBER BOOKMAKERS READ/WRITE ---

@router.get("/users/me/bookmakers", response_model=List[int])
async def get_my_bookmakers(current_user: User = Depends(get_current_user_read)):
    """GET /api/users/me/bookmakers — Returns IDs of bookmakers chosen by user."""
    return [bk.id for bk in current_user.bookmakers]

@router.post("/users/me/bookmakers")
async def update_my_bookmakers(
    data=Body(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """POST /api/users/me/bookmakers — Updates active bookmaker profiles for the current user."""
    if isinstance(data, list):
        bookmaker_ids = data
        other_bookmaker_name = None
    else:
        parsed = UserUpdateBookmakers(**data)
        bookmaker_ids = parsed.bookmaker_ids
        other_bookmaker_name = parsed.other_bookmaker_name

    result = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(bookmaker_ids)))
    selected_bks = result.scalars().all()
    
    current_user.bookmakers = selected_bks
    has_other_bookmaker = any(bk.code == "other" for bk in selected_bks)
    current_user.other_bookmaker_name = (
        other_bookmaker_name.strip() if has_other_bookmaker and other_bookmaker_name else None
    )
    await db.commit()
    return {"status": "success", "message": "Bookmakers list updated successfully"}

@router.post("/users/me/onboard", response_model=OnboardResponse)
async def onboard_user(
    data: OnboardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/users/me/onboard
    Processes the current user's neural calibration quiz.
    """
    return await save_onboarding_profile(db, current_user, data)


@router.post("/users/me/onboard/skip", response_model=UserResponse)
async def skip_my_onboarding(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/users/me/onboard/skip
    Marks the current user's welcome quiz as skipped while keeping broad feed access.
    """
    active_bookmakers = await ensure_standard_bookmakers(db)

    if not current_user.bookmakers:
        current_user.bookmakers = active_bookmakers
    if not current_user.preferred_sports:
        current_user.preferred_sports = ALL_SPORT_LABELS

    current_user.is_onboarded = True
    current_user.free_bets_available = 0
    telegram_id = current_user.telegram_id
    await db.commit()

    return await load_user_or_404(db, telegram_id)


@router.post("/users/{user_id}/onboard", response_model=OnboardResponse)
async def onboard_user_by_id(
    user_id: int,
    data: OnboardRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/users/{user_id}/onboard
    Saves onboarding answers, marks the profile as onboarded, and calibrates recommendations.
    """
    if user_id != current_user.telegram_id and not is_staff_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You can only onboard your own profile"
        )

    target_user = current_user if user_id == current_user.telegram_id else await load_user_or_404(db, user_id)
    return await save_onboarding_profile(db, target_user, data)

# --- SUBSCRIBER BANKROLL READ/WRITE ---

@router.put("/users/me/bankroll", response_model=UserResponse)
async def update_my_bankroll(
    data: UserUpdateBankroll,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """PUT /api/users/me/bankroll — Updates the bankroll for the current subscriber."""
    if data.bankroll < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Bankroll size cannot be negative"
        )
    current_user.bankroll = data.bankroll
    telegram_id = current_user.telegram_id
    await db.commit()
    return await load_user_response(db, telegram_id)

@router.get("/users/me/bets", response_model=List[BetResponse])
async def get_my_taken_bets(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """GET /api/users/me/bets — Returns a list of all bets currently tracked by the user."""
    query = (
        select(Bet)
        .join(user_bets)
        .filter(user_bets.c.user_id == current_user.telegram_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.created_at.desc())
    )
    result = await db.execute(query)
    return result.scalars().all()


@router.get("/users/me/bets/timeline")
async def get_my_taken_bets_timeline(
    period: str = Query("all", pattern="^(week|month|quarter|all)$"),
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Resolved paid client bets grouped by settlement month/day with flat-stake ROI stats."""
    normalized_period = normalize_period(period)
    query = (
        select(Bet, user_bets.c.access_type, user_bets.c.match_charged, user_bets.c.taken_at)
        .join(user_bets, user_bets.c.bet_id == Bet.id)
        .filter(
            user_bets.c.user_id == current_user.telegram_id,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    start = period_start(normalized_period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    rows = (await db.execute(query)).all()
    paid_items = []
    excluded_items = []
    for bet, access_type, match_charged, taken_at in rows:
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
        )
        if not item:
            continue
        if is_paid_client_access(access_type, match_charged):
            paid_items.append(item)
        else:
            excluded_items.append(item)

    payload = build_performance_payload(paid_items, include_bets=True, period=normalized_period)
    filtered_excluded_items = filter_items_by_period(excluded_items, normalized_period)
    payload["excluded_summary"] = summarize_items(filtered_excluded_items)
    payload["excluded_bets"] = filtered_excluded_items
    return payload

def _user_export_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append({
            "resolved_at": item.get("resolved_at") or "",
            "event_name": item.get("event_name") or "",
            "status": item.get("status") or "",
            "coefficient": item.get("coefficient") or 0,
            "profit_units": item.get("profit_units") or 0,
            "roi_percent": round(float(item.get("profit_units") or 0) * 100, 2),
            "source": item.get("source_type") or "",
            "sport": item.get("sport_type") or "",
            "bookmakers": ", ".join(item.get("bookmaker_names") or []),
            "outcome": item.get("outcome") or "",
        })
    return rows


def _user_csv_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    fieldnames = [
        "resolved_at",
        "event_name",
        "status",
        "coefficient",
        "profit_units",
        "roi_percent",
        "source",
        "sport",
        "bookmakers",
        "outcome",
    ]
    buffer = StringIO()
    buffer.write("\ufeff")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _user_xlsx_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    from openpyxl import Workbook

    headers = [
        ("resolved_at", "Дата расчета"),
        ("event_name", "Матч"),
        ("status", "Результат"),
        ("coefficient", "КФ"),
        ("profit_units", "Прибыль, флеты"),
        ("roi_percent", "ROI ставки, %"),
        ("source", "Источник"),
        ("sport", "Спорт"),
        ("bookmakers", "БК"),
        ("outcome", "Исход"),
    ]
    workbook = Workbook()
    worksheet = workbook.active
    worksheet.title = "My Bets"
    worksheet.append([label for _key, label in headers])
    for row in rows:
        worksheet.append([row.get(key, "") for key, _label in headers])
    for column in worksheet.columns:
        max_length = max(len(str(cell.value or "")) for cell in column)
        worksheet.column_dimensions[column[0].column_letter].width = min(max(max_length + 2, 10), 48)

    buffer = BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/users/me/bets/timeline/export")
async def export_my_taken_bets_timeline(
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    period: str = Query("all", pattern="^(week|month|quarter|all)$"),
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    normalized_period = normalize_period(period)
    query = (
        select(Bet, user_bets.c.access_type, user_bets.c.match_charged, user_bets.c.taken_at)
        .join(user_bets, user_bets.c.bet_id == Bet.id)
        .filter(
            user_bets.c.user_id == current_user.telegram_id,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    start = period_start(normalized_period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    rows = (await db.execute(query)).all()
    paid_items = []
    for bet, access_type, match_charged, taken_at in rows:
        if not is_paid_client_access(access_type, match_charged):
            continue
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
        )
        if item:
            paid_items.append(item)
    export_rows = _user_export_rows(filter_items_by_period(paid_items, normalized_period))
    filename = f"shamrai_my_bets_{normalized_period}.{format}"
    return _user_xlsx_response(export_rows, filename) if format == "xlsx" else _user_csv_response(export_rows, filename)

@router.get("/users/me/report")
async def generate_user_pdf_report(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """
    GET /api/users/me/report
    Generates a premium, branded PDF report containing the user's capper performance.
    """
    # 1. Fetch user stats (similar to /api/bets/stats)
    query = (
        select(Bet, user_bets.c.access_type, user_bets.c.match_charged)
        .join(user_bets, user_bets.c.bet_id == Bet.id)
        .filter(
            user_bets.c.user_id == current_user.telegram_id,
            Bet.status.in_(["win", "loss", "refund"]),
            Bet.resolved_at.isnot(None),
        )
        .order_by(Bet.created_at.desc())
    )
    result = await db.execute(query)
    bets = [
        bet
        for bet, access_type, match_charged in result.all()
        if is_paid_client_access(access_type, match_charged)
    ]
    
    total = len(bets)
    won = 0
    lost = 0
    refunded = 0
    profit = 0.0
    
    for bet in bets:
        if bet.status == "win":
            won += 1
            profit += float(bet.coefficient - 1)
        elif bet.status == "loss":
            lost += 1
            profit -= 1.0
        elif bet.status == "refund":
            refunded += 1
            
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (profit / resolved * 100) if resolved > 0 else 0.0

    # 2. Build PDF Document using reportlab
    from reportlab.lib.pagesizes import letter
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib import colors
    
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )
    
    story = []
    styles = getSampleStyleSheet()
    
    # Custom Brand Colors (Shamrai Aesthetics)
    pink_color = colors.HexColor("#ff007f")
    blue_color = colors.HexColor("#00d2ff")
    dark_bg = colors.HexColor("#0C1226")
    text_white = colors.HexColor("#F8FAFC")
    
    title_style = ParagraphStyle(
        'BrandTitle',
        parent=styles['Heading1'],
        fontSize=24,
        textColor=pink_color,
        spaceAfter=15,
        alignment=1 # Center
    )
    
    subtitle_style = ParagraphStyle(
        'BrandSubTitle',
        parent=styles['Normal'],
        fontSize=10,
        textColor=blue_color,
        spaceAfter=30,
        alignment=1 # Center
    )
    
    body_style = ParagraphStyle(
        'BrandBody',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=12
    )

    header_style = ParagraphStyle(
        'TableHeader',
        parent=styles['Normal'],
        fontSize=10,
        textColor=colors.white,
        fontWeight='bold'
    )
    
    story.append(Paragraph("SHAMRAI ANALYTICS HUB", title_style))
    story.append(Paragraph("OFFICIAL PERFORMANCE COCKPIT REPORT", subtitle_style))
    
    # Metadata Block
    meta_data = [
        [Paragraph("<b>User Profile:</b>", body_style), Paragraph(f"{current_user.first_name or ''} {current_user.last_name or ''} (@{current_user.username or 'none'})", body_style)],
        [Paragraph("<b>Telegram ID:</b>", body_style), Paragraph(str(current_user.telegram_id), body_style)],
        [Paragraph("<b>Report Generated:</b>", body_style), Paragraph(datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC"), body_style)],
        [Paragraph("<b>A/B Test Group:</b>", body_style), Paragraph(current_user.ab_group or "A", body_style)]
    ]
    t_meta = Table(meta_data, colWidths=[150, 400])
    t_meta.setStyle(TableStyle([
        ('BOTTOMPADDING', (0,0), (-1,-1), 4),
        ('TOPPADDING', (0,0), (-1,-1), 4),
    ]))
    story.append(t_meta)
    story.append(Spacer(1, 20))
    
    # Metrics Dashboard Grid
    metrics_data = [
        [
            Paragraph("<b>Total Forecasts</b>", body_style),
            Paragraph("<b>Wins</b>", body_style),
            Paragraph("<b>Losses</b>", body_style),
            Paragraph("<b>Net Profit</b>", body_style),
            Paragraph("<b>ROI</b>", body_style)
        ],
        [
            Paragraph(str(total), body_style),
            Paragraph(str(won), body_style),
            Paragraph(str(lost), body_style),
            Paragraph(f"{profit:+.2f} Flat", body_style),
            Paragraph(f"{roi:+.2f}%", body_style)
        ]
    ]
    t_metrics = Table(metrics_data, colWidths=[110, 110, 110, 110, 110])
    t_metrics.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), pink_color),
        ('TEXTCOLOR', (0,0), (-1,0), text_white),
        ('ALIGN', (0,0), (-1,-1), 'CENTER'),
        ('BOTTOMPADDING', (0,0), (-1,-1), 8),
        ('TOPPADDING', (0,0), (-1,-1), 8),
        ('GRID', (0,0), (-1,-1), 1, colors.grey)
    ]))
    story.append(t_metrics)
    story.append(Spacer(1, 30))
    
    # Forecasts History Table
    story.append(Paragraph("<b>FORECAST HISTORY DETAILS</b>", ParagraphStyle('SectionHeader', parent=styles['Heading2'], textColor=pink_color, spaceAfter=10)))
    
    hist_headers = [
        Paragraph("<b>Date</b>", header_style),
        Paragraph("<b>Event Description</b>", header_style),
        Paragraph("<b>Odds</b>", header_style),
        Paragraph("<b>Result</b>", header_style)
    ]
    hist_rows = [hist_headers]
    
    for bet in bets[:15]: # Show up to last 15 bets to fit nicely in pages
        hist_rows.append([
            Paragraph(bet.created_at.strftime("%Y-%m-%d"), body_style),
            Paragraph(bet.event_name, body_style),
            Paragraph(f"{float(bet.coefficient):.2f}", body_style),
            Paragraph(bet.status.upper(), ParagraphStyle('ResultCol', parent=body_style, textColor=pink_color if bet.status == 'win' else colors.red if bet.status == 'loss' else colors.grey))
        ])
        
    t_hist = Table(hist_rows, colWidths=[100, 270, 80, 100])
    t_hist.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), dark_bg),
        ('BOTTOMPADDING', (0,0), (-1,-1), 6),
        ('TOPPADDING', (0,0), (-1,-1), 6),
        ('GRID', (0,0), (-1,-1), 0.5, colors.lightgrey),
        ('VALIGN', (0,0), (-1,-1), 'MIDDLE')
    ]))
    story.append(t_hist)
    
    doc.build(story)
    buffer.seek(0)
    
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename=shamrai_report_{current_user.telegram_id}.pdf"}
    )


# --- SUBSCRIBER PREFERENCES MANAGEMENT ---

class UserPreferencesUpdate(BaseModel):
    alert_min_coef: Optional[float] = None
    odds_drop_notifications_enabled: Optional[bool] = None
    is_night_mode: Optional[bool] = None
    night_mode_start: Optional[str] = None
    night_mode_end: Optional[str] = None
    preferred_sports: Optional[List[str]] = None
    stats_display_mode: Optional[str] = None


class VkDeliveryStatusUpdate(BaseModel):
    group_member: Optional[bool] = None
    messages_allowed: Optional[bool] = None
    notifications_allowed: Optional[bool] = None


@router.get("/users/me/preferences")
async def get_my_preferences(current_user: User = Depends(get_current_user_read)):
    """GET /api/users/me/preferences — Returns current notification and display preferences."""
    return build_preferences_payload(current_user)


@router.put("/users/me/preferences")
async def update_my_preferences(
    data: UserPreferencesUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """PUT /api/users/me/preferences — Updates smart notification settings and display preferences."""
    if data.alert_min_coef is not None:
        if data.alert_min_coef < ALERT_MIN_COEF_MIN or data.alert_min_coef > ALERT_MIN_COEF_MAX:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Минимальный коэффициент должен быть от 1.0 до 1.6"
            )
        current_user.alert_min_coef = data.alert_min_coef

    if data.odds_drop_notifications_enabled is not None:
        current_user.odds_drop_notifications_enabled = data.odds_drop_notifications_enabled

    if data.is_night_mode is not None:
        current_user.is_night_mode = data.is_night_mode

    next_night_mode_start = quiet_time_or_default(
        getattr(current_user, "night_mode_start", None),
        DEFAULT_NIGHT_MODE_START,
    )
    next_night_mode_end = quiet_time_or_default(
        getattr(current_user, "night_mode_end", None),
        DEFAULT_NIGHT_MODE_END,
    )
    try:
        if data.night_mode_start is not None:
            next_night_mode_start = normalize_quiet_time(data.night_mode_start, DEFAULT_NIGHT_MODE_START)
        if data.night_mode_end is not None:
            next_night_mode_end = normalize_quiet_time(data.night_mode_end, DEFAULT_NIGHT_MODE_END)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Время ночного режима должно быть в формате HH:MM",
        )
    if data.night_mode_start is not None or data.night_mode_end is not None:
        if next_night_mode_start == next_night_mode_end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Начало и конец ночного режима должны отличаться",
            )
        current_user.night_mode_start = next_night_mode_start
        current_user.night_mode_end = next_night_mode_end

    if data.preferred_sports is not None:
        current_user.preferred_sports = data.preferred_sports

    if data.stats_display_mode is not None:
        if data.stats_display_mode not in ("percent", "flat"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Допустимые режимы: percent, flat"
            )
        current_user.stats_display_mode = data.stats_display_mode

    await db.commit()
    await db.refresh(current_user)

    return {
        "status": "success",
        "preferences": {
            "alert_min_coef": current_user.alert_min_coef,
            "odds_drop_notifications_enabled": current_user.odds_drop_notifications_enabled,
            "is_night_mode": current_user.is_night_mode,
            "night_mode_start": quiet_time_or_default(
                getattr(current_user, "night_mode_start", None),
                DEFAULT_NIGHT_MODE_START,
            ),
            "night_mode_end": quiet_time_or_default(
                getattr(current_user, "night_mode_end", None),
                DEFAULT_NIGHT_MODE_END,
            ),
            "preferred_sports": current_user.preferred_sports or ALL_SPORT_LABELS,
            "stats_display_mode": current_user.stats_display_mode
        }
    }


@router.get("/users/me/vk-delivery-status")
async def get_my_vk_delivery_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Returns VK delivery readiness for the current profile."""
    status_payload = await refresh_vk_delivery_status(db, current_user, commit=True)

    group_id = vk_group_id()
    return {
        "vk_user_id": current_user.vk_user_id,
        "group_id": group_id,
        "configured": vk_delivery_configured(),
        "group_member": bool(status_payload["group_member"]),
        "messages_allowed": bool(status_payload["messages_allowed"]),
        "notifications_allowed": bool(current_user.vk_notifications_allowed),
    }


@router.put("/users/me/vk-delivery-status")
async def update_my_vk_delivery_status(
    data: VkDeliveryStatusUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Stores VK notification intent and refreshes verified permissions from VK."""
    if not current_user.vk_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="VK ID is not linked to this profile",
        )

    if data.messages_allowed is False:
        current_user.vk_messages_allowed = False

    if data.notifications_allowed is not None:
        current_user.vk_notifications_allowed = bool(data.notifications_allowed)

    await refresh_vk_delivery_status(db, current_user)
    await db.commit()
    await db.refresh(current_user)

    return {
        "status": "success",
        "vk_user_id": current_user.vk_user_id,
        "group_id": vk_group_id(),
        "configured": vk_delivery_configured(),
        "group_member": bool(current_user.vk_group_member),
        "messages_allowed": bool(current_user.vk_messages_allowed),
        "notifications_allowed": bool(current_user.vk_notifications_allowed),
    }


# --- SUBSCRIBER PAYMENT / TRANSACTION HISTORY ---

@router.get("/users/me/payments")
async def get_my_payment_history(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """GET /api/users/me/payments — Returns all subscription payment transactions for the user."""
    return await build_payment_history_payload(db, current_user.telegram_id)


# --- SUBSCRIBER REFERRAL STATS (stub) ---

@router.get("/users/me/referral")
async def get_my_referral_stats(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    """GET /api/users/me/referral — Returns referral stats and subscription discount."""
    return await build_referral_payload(db, current_user)
