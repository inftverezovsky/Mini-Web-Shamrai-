from decimal import Decimal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.api.deps import get_current_user
from src.api.payments import (
    CHECKOUT_STATE_FAILED,
    CHECKOUT_STATE_REQUIRES_RECONCILIATION,
    PAYMENT_PURCHASE_CROWD_BET,
    _checkout_request_hash,
    _create_payment_attempt,
    _create_or_replay_telegram_checkout_url,
    _load_checkout_attempt,
    _lock_telegram_crowd_purchase_scope,
    _recover_owner_active_telegram_crowd_attempt,
    _telegram_checkout_replay_or_wait,
    create_telegram_stars_invoice_link,
)
from src.core.config import settings
from src.models.database import get_db
from src.models.models import Bet, CrowdBet, User
from src.schemas.schemas import CrowdBetFundRequest, CrowdBetFundResponse, CrowdBetResponse
from src.services.crowd_bets import user_is_crowd_participant

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


@router.get("/active", response_model=CrowdBetResponse)
async def get_active_crowd_bet(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    crowd_bet = await get_active_or_debug_seed_crowd_bet(db)
    if not crowd_bet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Нет активной складчины")
    is_participant = await user_is_crowd_participant(db, crowd_bet.id, current_user.telegram_id)
    await db.commit()
    return crowd_bet_response(crowd_bet, current_user.telegram_id, is_participant)


@router.post("/{crowd_bet_id}/fund", response_model=CrowdBetFundResponse)
async def fund_crowd_bet(
    crowd_bet_id: int,
    payload: CrowdBetFundRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /api/crowd-bets/{id}/fund
    Creates a Telegram Stars invoice for a pooled VIP forecast.
    The contribution is applied only after a verified successful_payment webhook.
    """
    if payload.amount_xtr <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сумма пополнения должна быть больше 0 XTR",
        )

    provider = "telegram_stars"
    payload_hash = _checkout_request_hash(
        provider=provider,
        user_id=current_user.telegram_id,
        purchase_type=PAYMENT_PURCHASE_CROWD_BET,
        crowd_bet_id=crowd_bet_id,
        amount_xtr=int(payload.amount_xtr),
    )
    await _lock_telegram_crowd_purchase_scope(
        db,
        user_id=current_user.telegram_id,
        crowd_bet_id=crowd_bet_id,
    )
    existing_checkout = await _load_checkout_attempt(
        db,
        user_id=current_user.telegram_id,
        provider=provider,
        checkout_intent_id=idempotency_key,
        payload_hash=payload_hash,
        for_update=True,
    )

    if existing_checkout is not None and existing_checkout.status in ("pending", "processing"):
        recovered_checkout = await _recover_owner_active_telegram_crowd_attempt(
            db,
            user_id=current_user.telegram_id,
            crowd_bet_id=crowd_bet_id,
            payload_hash=payload_hash,
        )
        if recovered_checkout is None or recovered_checkout.id != existing_checkout.id:
            existing_checkout.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            if recovered_checkout is not None:
                recovered_checkout.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Активные счета требуют ручной сверки",
            )
    elif existing_checkout is None:
        existing_checkout = await _recover_owner_active_telegram_crowd_attempt(
            db,
            user_id=current_user.telegram_id,
            crowd_bet_id=crowd_bet_id,
            payload_hash=payload_hash,
        )

    attempt = existing_checkout
    replay = None
    if attempt is not None:
        if (
            attempt.purchase_type_snapshot != PAYMENT_PURCHASE_CROWD_BET
            or attempt.crowd_bet_id_snapshot != crowd_bet_id
        ):
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Снимок покупки требует ручной сверки",
            )
        replay = await _telegram_checkout_replay_or_wait(db, attempt)
        if replay is not None and not attempt.checkout_url:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Счет требует ручной сверки",
            )

    # PaymentAttempt discovery/locking always precedes the mutable pool row.
    result = await db.execute(
        select(CrowdBet)
        .filter(CrowdBet.id == crowd_bet_id)
        .with_for_update()
    )
    crowd_bet = result.scalars().first()
    if not crowd_bet:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Складчина не найдена")

    if attempt is None:
        if crowd_bet.status == "opened":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Складчина уже открыта")

        remaining = max(0, int(crowd_bet.target_amount or 0) - int(crowd_bet.current_amount or 0))
        amount_xtr = min(int(payload.amount_xtr), remaining) if remaining else int(payload.amount_xtr)
        if amount_xtr <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Сумма пополнения должна быть больше 0 XTR",
            )

        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider=provider,
            checkout_intent_id=idempotency_key,
            checkout_payload_hash=payload_hash,
            amount=Decimal(amount_xtr),
            currency="XTR",
            bet_id=crowd_bet.bet_id,
            crowd_bet_id_snapshot=crowd_bet.id,
            metadata={
                "purchase_type": PAYMENT_PURCHASE_CROWD_BET,
                "crowd_bet_id": crowd_bet.id,
                "requested_amount_xtr": int(payload.amount_xtr),
                "amount_xtr": amount_xtr,
            },
        )
        await db.commit()
    else:
        amount_xtr = int(Decimal(attempt.amount))
        if replay is None and crowd_bet.status == "opened":
            attempt.status = "failed"
            attempt.checkout_state = CHECKOUT_STATE_FAILED
            await db.commit()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Складчина уже открыта")

    if replay is None:
        attempt = await _create_or_replay_telegram_checkout_url(
            db,
            attempt=attempt,
            title="Складчина Shamrai",
            description=f"Вклад в VIP-прогноз: {amount_xtr} XTR.",
            label="Вклад в складчину",
            invoice_creator=create_telegram_stars_invoice_link,
        )
    if not attempt.checkout_url:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Счет требует ручной сверки",
        )

    is_participant = await user_is_crowd_participant(db, crowd_bet.id, current_user.telegram_id)
    return CrowdBetFundResponse(
        crowd_bet=crowd_bet_response(crowd_bet, current_user.telegram_id, is_participant),
        attempt_id=attempt.id,
        invoice_url=attempt.checkout_url,
        amount_xtr=amount_xtr,
    )
