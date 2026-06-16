from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.models import CrowdBet, CrowdBetParticipant, User, user_bets


def _xtr_amount(value: Any) -> int:
    try:
        amount = int(Decimal(str(value)))
    except Exception:
        amount = 0
    return max(0, amount)


async def user_is_crowd_participant(db: AsyncSession, crowd_bet_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(CrowdBetParticipant.id).filter(
            and_(
                CrowdBetParticipant.crowd_bet_id == crowd_bet_id,
                CrowdBetParticipant.user_id == user_id,
            )
        )
    )
    return result.first() is not None


async def unlock_crowd_bet_for_participants(db: AsyncSession, crowd_bet: CrowdBet) -> int:
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


async def apply_verified_crowd_contribution(
    db: AsyncSession,
    *,
    user: User,
    crowd_bet_id: int,
    amount_xtr: Any,
) -> dict[str, Any]:
    amount = _xtr_amount(amount_xtr)
    if amount <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сумма пополнения должна быть больше 0 XTR",
        )

    result = await db.execute(
        select(CrowdBet)
        .filter(CrowdBet.id == crowd_bet_id)
        .with_for_update()
    )
    crowd_bet = result.scalars().first()
    if not crowd_bet:
        return {"status": "crowd_bet_missing", "crowd_bet_id": crowd_bet_id}

    was_opened = crowd_bet.status == "opened"

    participant_result = await db.execute(
        select(CrowdBetParticipant)
        .filter(
            and_(
                CrowdBetParticipant.crowd_bet_id == crowd_bet.id,
                CrowdBetParticipant.user_id == user.telegram_id,
            )
        )
        .with_for_update()
    )
    participant = participant_result.scalars().first()
    if participant:
        participant.contributed_amount += amount
    else:
        db.add(CrowdBetParticipant(
            crowd_bet_id=crowd_bet.id,
            user_id=user.telegram_id,
            contributed_amount=amount,
        ))

    unlocked_count = 0
    if was_opened:
        unlocked_count = await unlock_crowd_bet_for_participants(db, crowd_bet)
    else:
        crowd_bet.current_amount = min(crowd_bet.target_amount, crowd_bet.current_amount + amount)
        if crowd_bet.current_amount >= crowd_bet.target_amount:
            crowd_bet.status = "opened"
            await db.flush()
            unlocked_count = await unlock_crowd_bet_for_participants(db, crowd_bet)

    return {
        "status": "success",
        "crowd_bet_id": crowd_bet.id,
        "bet_id": str(crowd_bet.bet_id),
        "amount_xtr": amount,
        "opened": crowd_bet.status == "opened",
        "unlocked_count": unlocked_count,
    }
