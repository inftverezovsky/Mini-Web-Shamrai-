import random
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_
from typing import List, Dict, Any

from src.models.database import get_db
from src.models.models import User, Marathon, LivePulseLog, DailyRewardClaim, PromoCode, Bet, Quiz, PvPBattle, PvPBattleVote
from src.api.deps import get_current_user
from src.core.config import settings
from src.services.marketing_widgets import (
    MarketingWidgetDisabledError,
    MarketingWidgetLimitError,
    active_widget_payloads_for_user,
    ensure_widget_available,
    ensure_widget_reward_allowed,
    record_marketing_reward_event,
    stored_widget_config_count,
)
from src.services.match_access import lock_user_balance
from src.schemas.schemas import (
    MarathonResponse,
    SwipeCandidateResponse,
    SwipeRequest,
    SwipeResponse,
    QuizActiveResponse,
    QuizSubmitRequest,
    QuizSubmitResponse,
    PvPBattleResponse,
    PvPVoteRequest,
    PvPVoteResponse,
    WheelOfFortuneResponse,
)

router = APIRouter(prefix="/marketing", tags=["Marketing"])


def marketing_widget_http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, MarketingWidgetDisabledError):
        return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    if isinstance(exc, MarketingWidgetLimitError):
        return HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc))
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


def normalize_signal(value: str | None) -> str:
    if not value:
        return ""

    normalized = value.strip().lower().replace(" ", "")
    aliases = {
        "п1": "p1",
        "1": "p1",
        "home": "p1",
        "победа1": "p1",
        "п2": "p2",
        "2": "p2",
        "away": "p2",
        "победа2": "p2",
        "x": "draw",
        "х": "draw",
        "ничья": "draw",
        "draw": "draw",
    }
    return aliases.get(normalized, normalized)


def battle_distribution(battle: PvPBattle) -> dict[str, float]:
    total = battle.votes_a + battle.votes_b
    if total == 0:
        return {"percent_a": 50.0, "percent_b": 50.0}
    return {
        "percent_a": round(battle.votes_a / total * 100, 1),
        "percent_b": round(battle.votes_b / total * 100, 1),
    }


async def create_bound_promo(
    db: AsyncSession,
    user: User,
    prefix: str,
    discount_percent: int,
    hours_valid: int = 24
) -> PromoCode:
    suffix = str(user.telegram_id)[-4:]
    for _ in range(5):
        code = f"{prefix}{suffix}{random.randint(100, 999)}".upper()
        existing = await db.execute(select(PromoCode).filter(PromoCode.code == code))
        if not existing.scalars().first():
            promo = PromoCode(
                code=code,
                user_id=user.telegram_id,
                reward_type="discount",
                discount_percent=discount_percent,
                valid_until=datetime.now(timezone.utc) + timedelta(hours=hours_valid),
                is_active=True,
            )
            db.add(promo)
            await db.flush()
            return promo

    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Не удалось сгенерировать персональный промокод"
    )


async def get_conversion_bet(db: AsyncSession, *, allow_seed: bool = False) -> Bet | None:
    result = await db.execute(
        select(Bet)
        .filter(Bet.status == "pending")
        .options(selectinload(Bet.bookmaker))
        .order_by(Bet.created_at.desc())
    )
    bet = result.scalars().first()
    if bet:
        if not bet.outcome:
            bet.outcome = "П1"
            await db.flush()
        return bet

    if not allow_seed:
        return None

    bet = Bet(
        event_name="Реал - Барселона",
        coefficient=Decimal("2.00"),
        description="Темп Реала выше после 60-й минуты, а фланги Барселоны проседают под быстрыми переводами.",
        status="pending",
        outcome="П1",
        price_stars=50,
        brain_score=8,
        sport_type="football",
    )
    db.add(bet)
    await db.flush()
    return bet


async def get_quiz(db: AsyncSession, bet: Bet | None = None, *, allow_seed: bool = False) -> Quiz | None:
    if not bet:
        bet = await get_conversion_bet(db, allow_seed=allow_seed)
    if not bet:
        return None

    result = await db.execute(select(Quiz).filter(Quiz.bet_id == bet.id))
    quiz = result.scalars().first()
    if quiz:
        return quiz

    if not allow_seed:
        return None

    quiz = Quiz(
        bet_id=bet.id,
        discount_reward=30,
        questions=[
            {
                "id": "q1",
                "question": "Что сильнее всего влияет на value в этом матче?",
                "options": ["Форма атаки", "Цвет формы", "Название стадиона"],
                "correct": "Форма атаки",
            },
            {
                "id": "q2",
                "question": "Какой риск-подход ближе к Bankroll Shield?",
                "options": ["Флэт 5-10%", "Ва-банк", "Догон после минуса"],
                "correct": "Флэт 5-10%",
            },
            {
                "id": "q3",
                "question": "Что делает коэффициент привлекательным?",
                "options": ["Разница вероятности и линии", "Просто высокий кэф", "Совет из чата"],
                "correct": "Разница вероятности и линии",
            },
        ],
    )
    db.add(quiz)
    await db.flush()
    return quiz


async def get_battle(db: AsyncSession, bet: Bet | None = None, *, allow_seed: bool = False) -> PvPBattle | None:
    result = await db.execute(select(PvPBattle).order_by(PvPBattle.id.desc()))
    battle = result.scalars().first()
    if battle:
        return battle

    if not bet:
        bet = await get_conversion_bet(db, allow_seed=allow_seed)
    if not bet or not allow_seed:
        return None

    battle = PvPBattle(
        match_name=bet.event_name,
        option_a=bet.outcome or "П1",
        option_b="П2",
        votes_a=12,
        votes_b=8,
    )
    db.add(battle)
    await db.flush()
    return battle


def quiz_public_payload(quiz: Quiz) -> list[dict[str, Any]]:
    return [
        {
            "id": str(question["id"]),
            "question": str(question["question"]),
            "options": list(question.get("options", [])),
        }
        for question in quiz.questions
    ]

@router.get("/pulse", response_model=List[Dict[str, Any]])
async def get_pulse_logs(db: AsyncSession = Depends(get_db)):
    """
    GET /api/marketing/pulse
    Returns the 5 most recent user activity logs.
    """
    query = select(LivePulseLog).order_by(LivePulseLog.created_at.desc()).limit(5)
    result = await db.execute(query)
    logs = result.scalars().all()
    
    if not logs and settings.DEBUG_MODE:
        mock_messages = [
            "@alex*** разблокировал VIP прогноз за 50 ⭐️",
            "@dmit*** оформил Золотую подписку на 180 дней 🏆",
            "@mari*** выиграл бесплатную ставку в Скретч-карте 🎁",
            "@vla*** разблокировал Live-прогноз с кэф 2.45 🔥",
            "@serg*** применил промокод WELCOME20 и получил скидку 💸"
        ]
        now = datetime.now(timezone.utc)
        for i, msg in enumerate(mock_messages):
            db.add(LivePulseLog(text_message=msg, created_at=now - timedelta(minutes=i * 3 + 2)))
        await db.commit()
        
        # Re-fetch
        result = await db.execute(query)
        logs = result.scalars().all()
        
    return [{"id": l.id, "text_message": l.text_message, "created_at": l.created_at} for l in logs]


@router.get("/widgets")
async def get_marketing_widgets(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    configured = (await stored_widget_config_count(db)) > 0
    return {
        "configured": configured,
        "widgets": await active_widget_payloads_for_user(db, current_user) if configured else [],
    }

@router.post("/daily-spin")
async def claim_daily_bonus(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/daily-spin
    Validates configured cooldown and limits.
    Randomly awards a promo code or +1 free bet slot.
    """
    now = datetime.now(timezone.utc)
    claim_date = now.date().isoformat()

    try:
        widget_config = await ensure_widget_reward_allowed(db, "daily_spin", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)

    locked_balance = await lock_user_balance(db, current_user.telegram_id)

    existing_daily_claim = (await db.execute(
        select(DailyRewardClaim.id).filter(
            DailyRewardClaim.user_id == current_user.telegram_id,
            DailyRewardClaim.claimed_date == claim_date,
        )
    )).first()
    if existing_daily_claim is not None:
        raise marketing_widget_http_error(
            MarketingWidgetLimitError("Ежедневный бонус уже получен")
        )

    configured_reward = str(widget_config.get("reward_type") or "mixed")
    reward_value = int(widget_config.get("reward_value") or 25)
    promo_valid_hours = int(widget_config.get("promo_valid_hours") or 24)
    reward_type = (
        random.choice(["free_bet", "promo_code"])
        if configured_reward == "mixed"
        else "free_bet" if configured_reward == "free_bet"
        else "promo_code"
    )
    reward_detail = {}
    promo = None
    db.add(DailyRewardClaim(user_id=current_user.telegram_id, claimed_date=claim_date, claimed_at=now))
    await db.flush()
    
    if reward_type == "free_bet":
        free_bets_added = max(1, reward_value or 1)
        current_user.free_bets_available = (
            max(0, locked_balance.free_bets_available) + free_bets_added
        )
        reward_detail = {
            "type": "free_bet",
            "title": "Бесплатная ставка",
            "value": f"{free_bets_added} прогноз",
            "message": f"🎁 Вам начислено прогнозов: {free_bets_added}"
        }
        pulse_msg = f"🎁 @{current_user.username[:4] if current_user.username else 'user'}*** выиграл бесплатный прогноз в Бонусе!"
    else:
        promo = await create_bound_promo(
            db,
            current_user,
            "DAILY",
            max(1, min(100, reward_value or 25)),
            hours_valid=max(1, promo_valid_hours or 24),
        )
            
        reward_detail = {
            "type": "promo_code",
            "title": f"Промокод на скидку {promo.discount_percent}%",
            "value": promo.code,
            "message": f"🎟️ Ваш промокод на скидку {promo.discount_percent}%: {promo.code}"
        }
        pulse_msg = f"🎟️ @{current_user.username[:4] if current_user.username else 'user'}*** выиграл промокод на скидку {promo.discount_percent}%!"

    db.add(LivePulseLog(text_message=pulse_msg, created_at=now))
    await record_marketing_reward_event(
        db,
        user=current_user,
        widget_key="daily_spin",
        reward_type="free_bet" if reward_type == "free_bet" else "discount",
        reward_value=max(1, reward_value or 1),
        promo_code_id=promo.id if promo else None,
    )
    await db.commit()
    
    return reward_detail


@router.get("/swipe-candidate", response_model=SwipeCandidateResponse)
async def get_swipe_candidate(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns a prediction candidate for the Shamrai Swipe interaction."""
    try:
        await ensure_widget_available(db, "swipe", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    bet = await get_conversion_bet(db, allow_seed=settings.DEBUG_MODE)
    if not bet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного прогноза для Swipe")
    await db.commit()
    return SwipeCandidateResponse(
        bet_id=bet.id,
        match_name=bet.event_name,
        bookmaker_name=bet.bookmaker.name if bet.bookmaker else None,
        coefficient=bet.coefficient,
        options=["П1", "Х", "П2"],
    )


@router.post("/swipe", response_model=SwipeResponse)
async def submit_swipe_guess(
    payload: SwipeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/swipe
    Compares the user's instinctive pick with Shamrai's prediction outcome.
    """
    try:
        widget_config = await ensure_widget_available(db, "swipe", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    if payload.bet_id:
        result = await db.execute(select(Bet).filter(Bet.id == payload.bet_id))
        bet = result.scalars().first()
        if not bet:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Прогноз не найден")
    else:
        bet = await get_conversion_bet(db, allow_seed=settings.DEBUG_MODE)
        if not bet:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного прогноза для Swipe")

    matched = normalize_signal(payload.guess) == normalize_signal(bet.outcome or "П1")
    promo = None
    if matched:
        try:
            await ensure_widget_reward_allowed(db, "swipe", current_user)
        except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
            raise marketing_widget_http_error(exc)
        discount_reward = max(1, min(100, int(widget_config.get("reward_value") or 50)))
        promo_valid_hours = max(1, int(widget_config.get("promo_valid_hours") or 24))
        promo = await create_bound_promo(db, current_user, "MIND", discount_reward, hours_valid=promo_valid_hours)
        db.add(LivePulseLog(
            text_message=f"🧠 @{current_user.username[:4] if current_user.username else 'user'}*** совпал с прогнозом Shamrai и забрал скидку {discount_reward}%!",
            created_at=datetime.now(timezone.utc),
        ))
        await record_marketing_reward_event(
            db,
            user=current_user,
            widget_key="swipe",
            reward_type="discount",
            reward_value=discount_reward,
            promo_code_id=promo.id,
        )
    else:
        discount_reward = 0

    await db.commit()
    return SwipeResponse(
        match=matched,
        discount=discount_reward if matched else 0,
        promo_code=promo.code if promo else None,
        message=f"Наши мысли сходятся! Скидка {discount_reward}%" if matched else "Мнение принято. Shamrai Brain думает иначе.",
    )


@router.get("/quiz-active", response_model=QuizActiveResponse)
async def get_active_quiz(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns the active analytical quiz with answers hidden from the client."""
    try:
        await ensure_widget_available(db, "quiz", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    bet = await get_conversion_bet(db, allow_seed=settings.DEBUG_MODE)
    quiz = await get_quiz(db, bet, allow_seed=settings.DEBUG_MODE)
    if not bet or not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного квиза")
    await db.commit()
    return QuizActiveResponse(
        id=quiz.id,
        bet_id=quiz.bet_id,
        discount_reward=quiz.discount_reward,
        questions=quiz_public_payload(quiz),
    )


@router.post("/quiz-submit", response_model=QuizSubmitResponse)
async def submit_quiz_answers(
    payload: QuizSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/quiz-submit
    Checks analytical answers and grants a temporary personal promo when all answers are correct.
    """
    try:
        widget_config = await ensure_widget_available(db, "quiz", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    quiz = None
    if payload.quiz_id:
        result = await db.execute(select(Quiz).filter(Quiz.id == payload.quiz_id))
        quiz = result.scalars().first()
    elif payload.bet_id:
        result = await db.execute(select(Quiz).filter(Quiz.bet_id == payload.bet_id))
        quiz = result.scalars().first()

    if not quiz:
        bet = await get_conversion_bet(db, allow_seed=settings.DEBUG_MODE)
        quiz = await get_quiz(db, bet, allow_seed=settings.DEBUG_MODE)
    if not quiz:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Квиз не найден")

    total = len(quiz.questions)
    score = 0
    for question in quiz.questions:
        question_id = str(question["id"])
        expected = str(question.get("correct", "")).strip()
        answer = str(payload.answers.get(question_id, "")).strip()
        if answer == expected:
            score += 1

    passed = score == total and total >= 3
    promo = None
    reward_discount = int(widget_config.get("reward_value") or quiz.discount_reward)
    if passed:
        try:
            await ensure_widget_reward_allowed(db, "quiz", current_user)
        except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
            raise marketing_widget_http_error(exc)
        reward_discount = max(1, min(100, reward_discount))
        promo_valid_hours = max(1, int(widget_config.get("promo_valid_hours") or 48))
        promo = await create_bound_promo(db, current_user, "LOGIC", reward_discount, hours_valid=promo_valid_hours)
        db.add(LivePulseLog(
            text_message=f"🎓 @{current_user.username[:4] if current_user.username else 'user'}*** прошел Аналитический тест Shamrai!",
            created_at=datetime.now(timezone.utc),
        ))
        await record_marketing_reward_event(
            db,
            user=current_user,
            widget_key="quiz",
            reward_type="discount",
            reward_value=reward_discount,
            promo_code_id=promo.id,
        )

    await db.commit()
    return QuizSubmitResponse(
        passed=passed,
        score=score,
        total=total,
        discount=reward_discount if passed else 0,
        promo_code=promo.code if promo else None,
        message="Тест пройден! Ваша логика безупречна." if passed else "Почти. Разберем линию еще раз?",
    )


@router.get("/pvp-active", response_model=PvPBattleResponse)
async def get_active_pvp_battle(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns the current Battle of Minds voting panel."""
    try:
        await ensure_widget_available(db, "pvp", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    bet = await get_conversion_bet(db, allow_seed=settings.DEBUG_MODE)
    battle = await get_battle(db, bet, allow_seed=settings.DEBUG_MODE)
    if not battle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного PvP баттла")
    await db.commit()
    distribution = battle_distribution(battle)
    return PvPBattleResponse(
        id=battle.id,
        match_name=battle.match_name,
        option_a=battle.option_a,
        option_b=battle.option_b,
        votes_a=battle.votes_a,
        votes_b=battle.votes_b,
        **distribution,
    )


@router.post("/pvp-vote", response_model=PvPVoteResponse)
async def submit_pvp_vote(
    payload: PvPVoteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/pvp-vote
    Records or updates the user's Battle of Minds vote and returns the live distribution.
    """
    try:
        await ensure_widget_available(db, "pvp", current_user)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    if payload.battle_id:
        result = await db.execute(select(PvPBattle).filter(PvPBattle.id == payload.battle_id))
        battle = result.scalars().first()
    else:
        battle = await get_battle(db, allow_seed=settings.DEBUG_MODE)

    if not battle:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Баттл не найден")

    chosen = payload.option.strip()
    if normalize_signal(chosen) == normalize_signal(battle.option_a) or chosen.lower() == "a":
        option_key = "a"
    elif normalize_signal(chosen) == normalize_signal(battle.option_b) or chosen.lower() == "b":
        option_key = "b"
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный вариант голоса")

    vote_result = await db.execute(
        select(PvPBattleVote).filter(
            and_(
                PvPBattleVote.battle_id == battle.id,
                PvPBattleVote.user_id == current_user.telegram_id,
            )
        )
    )
    vote = vote_result.scalars().first()
    if vote and vote.option != option_key:
        if vote.option == "a":
            battle.votes_a = max(0, battle.votes_a - 1)
        else:
            battle.votes_b = max(0, battle.votes_b - 1)
        vote.option = option_key
        if option_key == "a":
            battle.votes_a += 1
        else:
            battle.votes_b += 1
    elif not vote:
        vote = PvPBattleVote(
            battle_id=battle.id,
            user_id=current_user.telegram_id,
            option=option_key,
        )
        db.add(vote)
        if option_key == "a":
            battle.votes_a += 1
        else:
            battle.votes_b += 1

    await db.commit()
    distribution = battle_distribution(battle)
    return PvPVoteResponse(
        id=battle.id,
        match_name=battle.match_name,
        option_a=battle.option_a,
        option_b=battle.option_b,
        votes_a=battle.votes_a,
        votes_b=battle.votes_b,
        selected_option=option_key,
        message="Узнай, что думает нейросеть Shamrai",
        **distribution,
    )

@router.get("/marathon", response_model=MarathonResponse)
async def get_active_marathon(db: AsyncSession = Depends(get_db)):
    """
    GET /api/marketing/marathon
    Returns the currently active betting marathon context.
    Seeds a default demo marathon only in DEBUG_MODE.
    """
    try:
        await ensure_widget_available(db, "marathon", None)
    except (MarketingWidgetDisabledError, MarketingWidgetLimitError) as exc:
        raise marketing_widget_http_error(exc)
    result = await db.execute(select(Marathon).filter(Marathon.is_active == True))
    marathon = result.scalars().first()
    
    if not marathon and settings.DEBUG_MODE:
        marathon = Marathon(
            title="Марафон: Путь к х10 от банка",
            target_multiplier=10.00,
            current_step=5,
            total_steps=10,
            is_active=True
        )
        db.add(marathon)
        await db.commit()
        
        result = await db.execute(select(Marathon).filter(Marathon.is_active == True))
        marathon = result.scalars().first()

    if not marathon:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активного марафона")
        
    return marathon

@router.get("/wheel-of-fortune/status")
async def get_wheel_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Check if the user can spin the wheel of fortune right now."""
    from src.models.models import MarketingRewardEvent
    
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    last_spin = await db.execute(
        select(MarketingRewardEvent)
        .filter(
            MarketingRewardEvent.user_id == current_user.telegram_id,
            MarketingRewardEvent.widget_key == "wheel_of_fortune"
        )
        .order_by(MarketingRewardEvent.created_at.desc())
    )
    last_event = last_spin.scalars().first()
    
    if last_event and last_event.created_at >= seven_days_ago:
        next_spin = last_event.created_at + timedelta(days=7)
        return {"can_spin": False, "next_spin_at": next_spin.isoformat()}
    return {"can_spin": True, "next_spin_at": None}

@router.post("/wheel-of-fortune", response_model=WheelOfFortuneResponse)
async def spin_wheel_of_fortune(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/wheel-of-fortune
    Spins the wheel and returns a random prize based on weighted probabilities.
    Allowed once per week per user.
    """
    from src.models.models import MarketingRewardEvent
    
    # Блокируем баланс пользователя, чтобы предотвратить параллельные запросы (рейс-кондишены)
    await lock_user_balance(db, current_user.telegram_id)
    
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    last_spin = await db.execute(
        select(MarketingRewardEvent)
        .filter(
            MarketingRewardEvent.user_id == current_user.telegram_id,
            MarketingRewardEvent.widget_key == "wheel_of_fortune",
            MarketingRewardEvent.created_at >= seven_days_ago
        )
    )
    if last_spin.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Вы уже получали бонус на этой неделе."
        )

    # Распределение: Топ Ошибка 50%, Скидка 50% 15%, Скидка 70% 20%, 1000 бонусов 15%
    r = random.random()
    if r < 0.50:
        prize_type = "post_payment_top_error"
    elif r < 0.65:
        prize_type = "discount_50"
    elif r < 0.85:
        prize_type = "discount_70"
    else:
        prize_type = "bonus_1000"

    promo = None
    message = ""
    reward_type = prize_type

    if prize_type == "post_payment_top_error":
        promo = await create_bound_promo(db, current_user, "WHEEL", 0, hours_valid=168)
        promo.reward_type = "post_payment_match"
        message = "🎉 Поздравляем! Вы выиграли Топ Ошибку на послеоплату."
    elif prize_type == "discount_50":
        promo = await create_bound_promo(db, current_user, "WHEEL", 50, hours_valid=168)
        message = "🎉 Поздравляем! Вы выиграли скидку 50% на абонемент."
    elif prize_type == "discount_70":
        promo = await create_bound_promo(db, current_user, "WHEEL", 70, hours_valid=168)
        message = "🎉 Поздравляем! Вы выиграли скидку 70% на абонемент."
    elif prize_type == "bonus_1000":
        promo = await create_bound_promo(db, current_user, "WHEEL", 0, hours_valid=168)
        promo.reward_type = "bonus_1000"
        message = "🎉 Поздравляем! Вы выиграли 1000 бонусов на Топ Ошибку."

    db.add(LivePulseLog(
        text_message=f"🎡 @{current_user.username[:4] if current_user.username else 'user'}*** крутит Колесо Фортуны и забирает приз!",
        created_at=datetime.now(timezone.utc),
    ))

    await record_marketing_reward_event(
        db,
        user=current_user,
        widget_key="wheel_of_fortune",
        reward_type=prize_type,
        reward_value=1,
        promo_code_id=promo.id if promo else None,
    )

    await db.commit()

    return WheelOfFortuneResponse(
        reward_type=reward_type,
        promo_code=promo.code if promo else None,
        message=message
    )
