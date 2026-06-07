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
)

router = APIRouter(prefix="/marketing", tags=["Marketing"])


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

@router.post("/daily-spin")
async def claim_daily_bonus(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/marketing/daily-spin
    Validates 24-hour limit (bypassed in debug mode).
    Randomly awards a promo code or +1 free bet slot.
    """
    now = datetime.now(timezone.utc)
    
    # Check cooldown constraint
    last_claim_res = await db.execute(
        select(DailyRewardClaim)
        .filter(DailyRewardClaim.user_id == current_user.telegram_id)
        .order_by(DailyRewardClaim.claimed_at.desc())
    )
    last_claim = last_claim_res.scalars().first()
    
    if last_claim and not settings.DEBUG_MODE:
        elapsed = now - last_claim.claimed_at
        if elapsed < timedelta(hours=24):
            remaining = timedelta(hours=24) - elapsed
            minutes_left = int(remaining.total_seconds() / 60)
            hours_left = minutes_left // 60
            mins_left = minutes_left % 60
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Следующая попытка доступна через {hours_left} ч. {mins_left} мин."
            )
            
    # Roll reward type: 50% Free Bet, 50% Promo Code
    claim_date = now.date().isoformat()
    reward_type = random.choice(["free_bet", "promo_code"])
    reward_detail = {}
    
    if reward_type == "free_bet":
        current_user.free_bets_available += 1
        reward_detail = {
            "type": "free_bet",
            "title": "Бесплатная ставка",
            "value": "1 прогноз",
            "message": "🎁 Вам начислен 1 бесплатный прогноз!"
        }
        pulse_msg = f"🎁 @{current_user.username[:4] if current_user.username else 'user'}*** выиграл бесплатный прогноз в Бонусе!"
    else:
        # Fetch any active promo code to grant
        promo_res = await db.execute(
            select(PromoCode).filter(PromoCode.is_active == True, PromoCode.user_id.is_(None))
        )
        promo = promo_res.scalars().first()
        
        if not promo:
            promo = await create_bound_promo(db, current_user, "DAILY", 25, hours_valid=24)
            
        reward_detail = {
            "type": "promo_code",
            "title": f"Промокод на скидку {promo.discount_percent}%",
            "value": promo.code,
            "message": f"🎟️ Ваш промокод на скидку {promo.discount_percent}%: {promo.code}"
        }
        pulse_msg = f"🎟️ @{current_user.username[:4] if current_user.username else 'user'}*** выиграл промокод на скидку {promo.discount_percent}%!"

    # Record log
    db.add(DailyRewardClaim(user_id=current_user.telegram_id, claimed_date=claim_date, claimed_at=now))
    db.add(LivePulseLog(text_message=pulse_msg, created_at=now))
    await db.commit()
    
    return reward_detail


@router.get("/swipe-candidate", response_model=SwipeCandidateResponse)
async def get_swipe_candidate(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns a prediction candidate for the Shamrai Swipe interaction."""
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
        promo = await create_bound_promo(db, current_user, "MIND", 50, hours_valid=24)
        db.add(LivePulseLog(
            text_message=f"🧠 @{current_user.username[:4] if current_user.username else 'user'}*** совпал с прогнозом Shamrai и забрал скидку 50%!",
            created_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    return SwipeResponse(
        match=matched,
        discount=50 if matched else 0,
        promo_code=promo.code if promo else None,
        message="Наши мысли сходятся! Скидка 50%" if matched else "Мнение принято. Shamrai Brain думает иначе.",
    )


@router.get("/quiz-active", response_model=QuizActiveResponse)
async def get_active_quiz(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns the active analytical quiz with answers hidden from the client."""
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
    if passed:
        promo = await create_bound_promo(db, current_user, "LOGIC", quiz.discount_reward, hours_valid=48)
        db.add(LivePulseLog(
            text_message=f"🎓 @{current_user.username[:4] if current_user.username else 'user'}*** прошел Аналитический тест Shamrai!",
            created_at=datetime.now(timezone.utc),
        ))

    await db.commit()
    return QuizSubmitResponse(
        passed=passed,
        score=score,
        total=total,
        discount=quiz.discount_reward if passed else 0,
        promo_code=promo.code if promo else None,
        message="Тест пройден! Ваша логика безупречна." if passed else "Почти. Разберем линию еще раз?",
    )


@router.get("/pvp-active", response_model=PvPBattleResponse)
async def get_active_pvp_battle(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Returns the current Battle of Minds voting panel."""
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
