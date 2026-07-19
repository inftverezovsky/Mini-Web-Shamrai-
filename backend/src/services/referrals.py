from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import MatchBalanceLog, PaymentAttempt, ReferralRewardEvent, User
from src.services.marketing_risk import (
    RISK_STATUS_APPROVED,
    RISK_STATUS_REJECTED,
    evaluate_referral_purchase_risk,
)
from src.services.match_access import current_match_balance, lock_user_balance
from src.services.system_settings import get_referral_program_settings


async def get_referral_stats(db: AsyncSession, user_id: int) -> dict:
    referral_settings = await get_referral_program_settings(db)
    invited_res = await db.execute(
        select(User.telegram_id).filter(User.referred_by_user_id == user_id)
    )
    invited_ids = [row[0] for row in invited_res.all()]
    if not invited_ids:
        return {
            "invited_count": 0,
            "purchased_invited_count": 0,
            "program_enabled": bool(referral_settings["program_enabled"]),
            "discount_enabled": bool(referral_settings["discount_enabled"]),
            "discount_step_percent": int(referral_settings["discount_step_percent"]),
            "discount_max_percent": int(referral_settings["discount_max_percent"]),
            "referral_discount_percent": 0,
            "match_reward_enabled": bool(referral_settings["match_reward_enabled"]),
            "match_reward_count": int(referral_settings["match_reward_count"]),
        }

    approved_events_res = await db.execute(
        select(ReferralRewardEvent.referred_user_id)
        .filter(
            ReferralRewardEvent.referrer_user_id == user_id,
            ReferralRewardEvent.referred_user_id.in_(invited_ids),
            ReferralRewardEvent.status == RISK_STATUS_APPROVED,
        )
        .distinct()
    )
    purchased_invited_count = len({row[0] for row in approved_events_res.all()})
    discount_step_percent = int(referral_settings["discount_step_percent"])
    discount_max_percent = int(referral_settings["discount_max_percent"])
    if bool(referral_settings["program_enabled"]) and bool(referral_settings["discount_enabled"]):
        referral_discount_percent = min(
            discount_max_percent,
            purchased_invited_count * discount_step_percent,
        )
    else:
        referral_discount_percent = 0

    return {
        "invited_count": len(invited_ids),
        "purchased_invited_count": purchased_invited_count,
        "program_enabled": bool(referral_settings["program_enabled"]),
        "discount_enabled": bool(referral_settings["discount_enabled"]),
        "discount_step_percent": discount_step_percent,
        "discount_max_percent": discount_max_percent,
        "referral_discount_percent": referral_discount_percent,
        "match_reward_enabled": bool(referral_settings["match_reward_enabled"]),
        "match_reward_count": int(referral_settings["match_reward_count"]),
    }


async def get_referral_discount_percent(db: AsyncSession, user_id: int) -> int:
    stats = await get_referral_stats(db, user_id)
    return int(stats["referral_discount_percent"])


async def _lock_referral_reward_event(
    db: AsyncSession,
    event_id: int,
) -> ReferralRewardEvent:
    await db.flush()
    result = await db.execute(
        select(ReferralRewardEvent)
        .filter(ReferralRewardEvent.id == event_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    locked_event = result.scalars().first()
    if locked_event is None:
        raise ValueError("Событие реферальной награды не найдено")
    return locked_event


async def apply_referral_reward_for_purchase(
    db: AsyncSession,
    *,
    referred_user: User,
    payment_attempt: PaymentAttempt,
    source_type: str,
) -> ReferralRewardEvent | None:
    """Record the first qualifying referral purchase and award configured referrer matches once."""
    referrer_id = getattr(referred_user, "referred_by_user_id", None)
    if not referrer_id:
        return None

    referral_settings = await get_referral_program_settings(db)
    if not bool(referral_settings["program_enabled"]):
        return None

    existing_res = await db.execute(
        select(ReferralRewardEvent).filter(
            ReferralRewardEvent.referrer_user_id == referrer_id,
            ReferralRewardEvent.referred_user_id == referred_user.telegram_id,
        )
    )
    existing = existing_res.scalars().first()
    if existing:
        return existing

    referrer = referred_user if referrer_id == referred_user.telegram_id else await db.get(User, referrer_id)
    if not referrer:
        return None
    locked_balance = await lock_user_balance(db, referrer_id)

    risk_decision = await evaluate_referral_purchase_risk(
        db,
        referrer=referrer,
        referred_user=referred_user,
    )
    discount_snapshot = 0
    matches_awarded = 0
    if risk_decision.status == RISK_STATUS_APPROVED:
        discount_snapshot = await get_referral_discount_percent(db, referrer.telegram_id)
        matches_awarded = (
            int(referral_settings["match_reward_count"])
            if bool(referral_settings["match_reward_enabled"])
            else 0
        )

    if matches_awarded > 0:
        balance_before = current_match_balance(locked_balance)
        balance_after = max(0, balance_before) + matches_awarded
        referrer.purchased_bets_balance = balance_after
        referrer.matches_remaining = balance_after
        db.add(MatchBalanceLog(
            user_id=referrer.telegram_id,
            delta_matches=matches_awarded,
            event_type="referral_match_reward",
            note=f"Referral reward for user {referred_user.telegram_id} via {source_type}",
        ))

    event = ReferralRewardEvent(
        referrer_user_id=referrer.telegram_id,
        referred_user_id=referred_user.telegram_id,
        source_payment_attempt_id=payment_attempt.id,
        source_type=source_type,
        discount_percent_snapshot=discount_snapshot,
        matches_awarded=matches_awarded,
        status=risk_decision.status,
        risk_score=risk_decision.score,
        risk_reasons=risk_decision.reasons,
    )
    db.add(event)
    await db.flush()
    return event


async def approve_referral_reward_event(
    db: AsyncSession,
    *,
    event: ReferralRewardEvent,
    reviewer_user_id: int | None = None,
) -> ReferralRewardEvent:
    event = await _lock_referral_reward_event(db, event.id)
    if event.status != RISK_STATUS_APPROVED:
        event.status = RISK_STATUS_APPROVED
        event.risk_score = 0
        event.risk_reasons = []
        event.reviewed_by = reviewer_user_id
        event.reviewed_at = datetime.now(timezone.utc)
    # Production sessions disable autoflush. Persist the approval before the
    # discount query counts approved referral events.
    await db.flush()

    referral_settings = await get_referral_program_settings(db)
    match_count = int(referral_settings["match_reward_count"])
    should_award_matches = (
        bool(referral_settings["program_enabled"])
        and bool(referral_settings["match_reward_enabled"])
        and match_count > 0
        and int(event.matches_awarded or 0) <= 0
    )
    if should_award_matches:
        locked_balance = await lock_user_balance(db, event.referrer_user_id)
        referrer = await db.get(User, event.referrer_user_id)
        if referrer:
            balance_before = current_match_balance(locked_balance)
            balance_after = max(0, balance_before) + match_count
            referrer.purchased_bets_balance = balance_after
            referrer.matches_remaining = balance_after
            event.matches_awarded = match_count
            db.add(MatchBalanceLog(
                user_id=referrer.telegram_id,
                delta_matches=match_count,
                event_type="referral_match_reward",
                note=f"Approved held referral reward for user {event.referred_user_id}",
            ))

    if bool(referral_settings["program_enabled"]):
        event.discount_percent_snapshot = await get_referral_discount_percent(db, event.referrer_user_id)
    await db.flush()
    return event


async def reject_referral_reward_event(
    db: AsyncSession,
    *,
    event: ReferralRewardEvent,
    reviewer_user_id: int | None = None,
) -> ReferralRewardEvent:
    event = await _lock_referral_reward_event(db, event.id)
    event.status = RISK_STATUS_REJECTED
    event.reviewed_by = reviewer_user_id
    event.reviewed_at = datetime.now(timezone.utc)
    await db.flush()
    return event
