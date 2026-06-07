from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from uuid import UUID

from src.api.deps import get_current_user
from src.core.config import settings
from src.models.database import get_db
from src.models.models import Bet, CrowdBet, CrowdBetParticipant, User, user_bets
from src.schemas.schemas import CrowdBetFundRequest, CrowdBetResponse

router = APIRouter(prefix="/crowd-bets", tags=["Crowd Bets"])


def crowd_bet_response(crowd_bet: CrowdBet, user_id: int, is_participant: bool) -> CrowdBetResponse:
    progress = 0.0
    if crowd_bet.target_amount > 0:
        progress = min(100.0, round(crowd_bet.current_amount / crowd_bet.target_amount * 100, 1))

    return CrowdBetResponse(
        id=crowd_bet.id,
        bet_id=crowd_bet.bet_id,
        target_amount=crowd_bet.target_amount,
        current_amount=crowd_bet.current_amount,
        status=crowd_bet.status,
        progress_percent=progress,
        is_participant=is_participant,
    )


async def get_active_or_debug_seed_crowd_bet(db: AsyncSession) -> CrowdBet | None:
    result = await db.execute(
        select(CrowdBet)
        .filter(CrowdBet.status == "funding")
        .order_by(CrowdBet.id.desc())
    )
    crowd_bet = result.scalars().first()
    if crowd_bet:
        return crowd_bet

    if not settings.DEBUG_MODE:
        return None

    bet_result = await db.execute(
        select(Bet)
        .filter(Bet.status == "pending")
        .order_by(Bet.created_at.desc())
    )
    bet = bet_result.scalars().first()
    if not bet:
        bet = Bet(
            event_name="VIP экспресс Shamrai",
            coefficient=2.35,
            description="Пуловый прогноз с акцентом на value-линию и управляемый риск.",
            status="pending",
            outcome="П1",
            price_stars=50,
            brain_score=8,
        )
        db.add(bet)
        await db.flush()

    crowd_bet = CrowdBet(
        bet_id=bet.id,
        target_amount=1000,
        current_amount=350,
        status="funding",
    )
    db.add(crowd_bet)
    await db.flush()
    return crowd_bet


async def user_is_participant(db: AsyncSession, crowd_bet_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(CrowdBetParticipant.id).filter(
            and_(
                CrowdBetParticipant.crowd_bet_id == crowd_bet_id,
                CrowdBetParticipant.user_id == user_id,
            )
        )
    )
    return result.first() is not None


async def unlock_for_participants(db: AsyncSession, crowd_bet: CrowdBet) -> int:
    participants_result = await db.execute(
        select(CrowdBetParticipant.user_id).filter(CrowdBetParticipant.crowd_bet_id == crowd_bet.id)
    )
    participant_ids = [row[0] for row in participants_result.all()]

    unlocked_count = 0
    for user_id in participant_ids:
        existing_result = await db.execute(
            select(user_bets).filter(
                and_(user_bets.c.user_id == user_id, user_bets.c.bet_id == crowd_bet.bet_id)
            )
        )
        if existing_result.first():
            continue

        await db.execute(
            user_bets.insert().values(
                user_id=user_id,
                bet_id=crowd_bet.bet_id,
                taken_at=func.now(),
                access_type="crowd_pool",
                match_charged=False,
            )
        )
        unlocked_count += 1

    return unlocked_count


@router.get("/active", response_model=CrowdBetResponse)
async def get_active_crowd_bet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    crowd_bet = await get_active_or_debug_seed_crowd_bet(db)
    if not crowd_bet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активной складчины")
    is_participant = await user_is_participant(db, crowd_bet.id, current_user.telegram_id)
    await db.commit()
    return crowd_bet_response(crowd_bet, current_user.telegram_id, is_participant)


@router.post("/{crowd_bet_id}/fund", response_model=CrowdBetResponse)
async def fund_crowd_bet(
    crowd_bet_id: int,
    payload: CrowdBetFundRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /api/crowd-bets/{id}/fund
    Adds XTR to a pooled VIP forecast. When target is reached, unlocks the bet for all participants.
    """
    if payload.amount_xtr <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сумма пополнения должна быть больше 0 XTR",
        )

    result = await db.execute(select(CrowdBet).filter(CrowdBet.id == crowd_bet_id))
    crowd_bet = result.scalars().first()
    if not crowd_bet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Складчина не найдена")

    if crowd_bet.status == "opened":
        is_participant = await user_is_participant(db, crowd_bet.id, current_user.telegram_id)
        return crowd_bet_response(crowd_bet, current_user.telegram_id, is_participant)

    participant_result = await db.execute(
        select(CrowdBetParticipant).filter(
            and_(
                CrowdBetParticipant.crowd_bet_id == crowd_bet.id,
                CrowdBetParticipant.user_id == current_user.telegram_id,
            )
        )
    )
    participant = participant_result.scalars().first()
    if participant:
        participant.contributed_amount += payload.amount_xtr
    else:
        participant = CrowdBetParticipant(
            crowd_bet_id=crowd_bet.id,
            user_id=current_user.telegram_id,
            contributed_amount=payload.amount_xtr,
        )
        db.add(participant)

    crowd_bet.current_amount += payload.amount_xtr
    if crowd_bet.current_amount >= crowd_bet.target_amount:
        crowd_bet.current_amount = crowd_bet.target_amount
        crowd_bet.status = "opened"
        await db.flush()
        await unlock_for_participants(db, crowd_bet)

    await db.commit()
    return crowd_bet_response(crowd_bet, current_user.telegram_id, True)
