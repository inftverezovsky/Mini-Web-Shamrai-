from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.roles import is_staff_role
from src.models.models import Bet, MatchBalanceLog, Subscription, SubscriptionPlan, User, user_bets


@dataclass
class UserBetAccessResult:
    status: str
    already_recorded: bool
    access_type: str
    match_charged: bool
    balance_before: int
    balance_after: int
    no_balance_warning: bool


@dataclass
class UserBetAccessRevokeResult:
    had_access: bool
    access_type: Optional[str]
    match_charged: bool
    balance_before: int
    balance_after: int
    delta_matches: int
    previous_ledger_delta: int


REVOKE_USER_BET_ACCESS_EVENT = "admin_bet_access_revoked"


def current_match_balance(user: User) -> int:
    return int(user.purchased_bets_balance or user.matches_remaining or 0)


async def activate_match_package(
    db: AsyncSession,
    *,
    user: User,
    plan: SubscriptionPlan,
    payment_provider: str,
    payment_id: str,
) -> Subscription:
    """Activate a purchased/admin-issued match package and write the balance ledger."""
    matches = max(0, int(plan.match_count or 0))
    now = datetime.now(timezone.utc)

    subscription = Subscription(
        user_id=user.telegram_id,
        plan_id=plan.id,
        status="active",
        start_date=now,
        end_date=None,
        payment_provider=payment_provider,
        payment_id=payment_id,
    )
    db.add(subscription)
    await db.flush()

    next_balance = max(0, int(user.purchased_bets_balance or 0)) + matches
    user.purchased_bets_balance = next_balance
    user.matches_remaining = next_balance
    db.add(
        MatchBalanceLog(
            user_id=user.telegram_id,
            subscription_id=subscription.id,
            delta_matches=matches,
            event_type="package_purchase",
            note=f"Activated package '{plan.name}' via {payment_provider}",
        )
    )
    return subscription


def log_match_balance_event(
    *,
    user_id: int,
    event_type: str,
    delta_matches: int = 0,
    bet_id: Optional[UUID] = None,
    subscription_id: Optional[UUID] = None,
    note: Optional[str] = None,
) -> MatchBalanceLog:
    return MatchBalanceLog(
        user_id=user_id,
        bet_id=bet_id,
        subscription_id=subscription_id,
        delta_matches=delta_matches,
        event_type=event_type,
        note=note,
    )


async def record_user_bet_access(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    charge_match: bool,
    allow_negative_balance: bool = False,
    free_access_type: str = "free_bet",
    note: Optional[str] = None,
) -> UserBetAccessResult:
    """
    Add a bet to the user's tracked stats exactly once and optionally debit one match.
    The caller owns commit/rollback.
    """
    balance_before = int(user.purchased_bets_balance or user.matches_remaining or 0)

    existing_res = await db.execute(
        select(user_bets).filter(
            and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id)
        )
    )
    existing = existing_res.first()
    if existing:
        row = existing._mapping
        return UserBetAccessResult(
            status="already_taken",
            already_recorded=True,
            access_type=row.get("access_type") or "paid_match",
            match_charged=bool(row.get("match_charged")),
            balance_before=balance_before,
            balance_after=balance_before,
            no_balance_warning=False,
        )

    access_type = free_access_type
    match_charged = False
    no_balance_warning = False

    if is_staff_role(user.role):
        access_type = "admin"
    elif charge_match:
        if user.guarantee_active:
            access_type = "guarantee_replacement"
            db.add(log_match_balance_event(
                user_id=user.telegram_id,
                bet_id=bet.id,
                event_type="guarantee_replacement_taken",
                note=note or "Free replacement used while guarantee is active",
            ))
        else:
            if balance_before <= 0 and not allow_negative_balance:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="На абонементе не осталось матчей",
                )

            next_balance = balance_before - 1
            user.purchased_bets_balance = next_balance
            user.matches_remaining = next_balance
            access_type = "paid_match"
            match_charged = True
            no_balance_warning = balance_before <= 0
            db.add(log_match_balance_event(
                user_id=user.telegram_id,
                bet_id=bet.id,
                event_type="match_debit",
                delta_matches=-1,
                note=note or "Match debited when user added bet to My Bets",
            ))

    await db.execute(
        user_bets.insert().values(
            user_id=user.telegram_id,
            bet_id=bet.id,
            taken_at=func.now(),
            access_type=access_type,
            match_charged=match_charged,
        )
    )

    balance_after = int(user.purchased_bets_balance or user.matches_remaining or 0)
    return UserBetAccessResult(
        status="success",
        already_recorded=False,
        access_type=access_type,
        match_charged=match_charged,
        balance_before=balance_before,
        balance_after=balance_after,
        no_balance_warning=no_balance_warning,
    )


async def revoke_user_bet_access(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    actor_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> UserBetAccessRevokeResult:
    """
    Remove a user's access to a bet and reverse that bet's net match ledger impact.
    The caller owns commit/rollback.
    """
    balance_before = current_match_balance(user)
    existing_res = await db.execute(
        select(user_bets).filter(
            and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id)
        )
    )
    existing = existing_res.first()
    if not existing:
        return UserBetAccessRevokeResult(
            had_access=False,
            access_type=None,
            match_charged=False,
            balance_before=balance_before,
            balance_after=balance_before,
            delta_matches=0,
            previous_ledger_delta=0,
        )

    row = existing._mapping
    ledger_res = await db.execute(
        select(func.coalesce(func.sum(MatchBalanceLog.delta_matches), 0)).filter(
            MatchBalanceLog.user_id == user.telegram_id,
            MatchBalanceLog.bet_id == bet.id,
            MatchBalanceLog.event_type != REVOKE_USER_BET_ACCESS_EVENT,
        )
    )
    previous_ledger_delta = int(ledger_res.scalar() or 0)
    reverse_delta = -previous_ledger_delta
    balance_after = balance_before + reverse_delta
    user.purchased_bets_balance = balance_after
    user.matches_remaining = balance_after

    actor_note = f" by admin {actor_id}" if actor_id is not None else ""
    reason_note = f": {reason}" if reason else ""
    db.add(log_match_balance_event(
        user_id=user.telegram_id,
        bet_id=bet.id,
        event_type=REVOKE_USER_BET_ACCESS_EVENT,
        delta_matches=reverse_delta,
        note=f"Bet access revoked{actor_note}{reason_note}",
    ))
    await db.execute(
        delete(user_bets).where(
            and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id)
        )
    )

    return UserBetAccessRevokeResult(
        had_access=True,
        access_type=row.get("access_type") or None,
        match_charged=bool(row.get("match_charged")),
        balance_before=balance_before,
        balance_after=balance_after,
        delta_matches=reverse_delta,
        previous_ledger_delta=previous_ledger_delta,
    )
