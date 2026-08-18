from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import set_committed_value

from src.core.roles import is_staff_role
from src.models.models import Bet, MatchBalanceLog, Subscription, SubscriptionPlan, User, user_bets
from src.services.flat_subscriptions import lock_flat_subscription_financial_rows


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


@dataclass
class UserFreeBetAccessResult:
    already_recorded: bool
    free_bets_before: int
    free_bets_after: int


@dataclass(frozen=True)
class UserBalanceSnapshot:
    telegram_id: int
    role: str
    purchased_bets_balance: int
    matches_remaining: int
    free_bets_available: int
    guarantee_active: bool


REVOKE_USER_BET_ACCESS_EVENT = "admin_bet_access_revoked"


def current_match_balance(user: User) -> int:
    return int(user.purchased_bets_balance or user.matches_remaining or 0)


def user_has_full_forecast_access(user: User) -> bool:
    return (
        current_match_balance(user) > 0
        or bool(user.guarantee_active)
        or is_staff_role(getattr(user, "role", None))
    )


def _loaded_bookmaker_ids(entity: object) -> set[int]:
    return {
        int(bookmaker.id)
        for bookmaker in entity.__dict__.get("bookmakers", ())
        if getattr(bookmaker, "id", None) is not None
    }


def ensure_bet_eligible_for_user(*, bet: Bet, user: User) -> None:
    """Apply the same lifecycle, surface, and audience boundary to every unlock flow."""
    if not is_staff_role(user.role):
        if bet.delivery_mode != "feed":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Прогноз не найден")

        target_bookmaker_ids = _loaded_bookmaker_ids(bet)
        if bet.bookmaker_id is not None:
            target_bookmaker_ids.add(int(bet.bookmaker_id))
        if target_bookmaker_ids and not target_bookmaker_ids.intersection(_loaded_bookmaker_ids(user)):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Прогноз не найден")

    live_ends_at = getattr(bet, "live_ends_at", None)
    if live_ends_at is not None:
        if live_ends_at.tzinfo is None:
            live_ends_at = live_ends_at.replace(tzinfo=timezone.utc)
        if live_ends_at <= datetime.now(timezone.utc):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Прогноз уже не активен. Реагировать не нужно.",
            )

    if bet.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Прогноз уже не активен. Реагировать не нужно.",
        )
    if str(getattr(bet, "publication_type", "forecast") or "forecast").strip().lower() != "forecast":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текстовую публикацию нельзя открыть как прогноз",
        )


async def lock_bet_row(db: AsyncSession, bet_id: UUID) -> None:
    """Acquire the canonical Bet lock before any User balance/access lock."""
    with db.no_autoflush:
        result = await db.execute(
            select(Bet.id).filter(Bet.id == bet_id).with_for_update()
        )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Прогноз не найден")
    # Only flush tracked User/ledger changes after the canonical Bet row is held.
    await db.flush()


async def load_locked_bet_for_user_access(db: AsyncSession, bet_id: UUID) -> Bet:
    """Lock and freshly load a forecast for lifecycle/audience validation."""
    # Lock the scalar row first, then flush pending edits while that lock is held.
    # Refreshing the ORM identity before this flush would discard dirty Bet fields.
    await lock_bet_row(db, bet_id)
    with db.no_autoflush:
        result = await db.execute(
            select(Bet)
            .filter(Bet.id == bet_id)
            .with_for_update()
            .options(selectinload(Bet.bookmakers))
            .execution_options(populate_existing=True)
        )
    bet = result.scalars().first()
    if bet is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Прогноз не найден")
    return bet


async def lock_user_balance(db: AsyncSession, user_id: int) -> UserBalanceSnapshot:
    """Flush pending state, lock one user row, and return fresh balance scalars.

    Selecting scalars avoids refreshing the tracked ``User`` identity and thereby
    discarding unrelated pending fields when the session has autoflush disabled.
    """
    await db.flush()
    result = await db.execute(
        select(
            User.telegram_id,
            User.role,
            User.purchased_bets_balance,
            User.matches_remaining,
            User.free_bets_available,
            User.guarantee_active,
        )
        .filter(User.telegram_id == user_id)
        .with_for_update()
    )
    row = result.first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    snapshot = UserBalanceSnapshot(
        telegram_id=int(row.telegram_id),
        role=str(row.role or "user"),
        purchased_bets_balance=int(row.purchased_bets_balance or 0),
        matches_remaining=int(row.matches_remaining or 0),
        free_bets_available=int(row.free_bets_available or 0),
        guarantee_active=bool(row.guarantee_active),
    )
    tracked_user = await db.get(User, snapshot.telegram_id)
    if tracked_user is not None:
        _synchronize_tracked_user_balance(tracked_user, snapshot)
    return snapshot


def _synchronize_tracked_user_balance(user: User, snapshot: UserBalanceSnapshot) -> None:
    """Refresh access attributes while preserving unrelated pending User fields."""
    set_committed_value(user, "purchased_bets_balance", snapshot.purchased_bets_balance)
    set_committed_value(user, "matches_remaining", snapshot.matches_remaining)
    set_committed_value(user, "free_bets_available", snapshot.free_bets_available)
    set_committed_value(user, "guarantee_active", snapshot.guarantee_active)


async def lock_user_balances(
    db: AsyncSession,
    user_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[int, UserBalanceSnapshot]:
    """Lock several user rows in deterministic order to avoid lock-order inversions."""
    clean_ids = sorted({int(user_id) for user_id in user_ids})
    if not clean_ids:
        return {}
    await db.flush()
    result = await db.execute(
        select(
            User.telegram_id,
            User.role,
            User.purchased_bets_balance,
            User.matches_remaining,
            User.free_bets_available,
            User.guarantee_active,
        )
        .filter(User.telegram_id.in_(clean_ids))
        .order_by(User.telegram_id)
        .with_for_update()
    )
    snapshots = {
        int(row.telegram_id): UserBalanceSnapshot(
            telegram_id=int(row.telegram_id),
            role=str(row.role or "user"),
            purchased_bets_balance=int(row.purchased_bets_balance or 0),
            matches_remaining=int(row.matches_remaining or 0),
            free_bets_available=int(row.free_bets_available or 0),
            guarantee_active=bool(row.guarantee_active),
        )
        for row in result.all()
    }
    if len(snapshots) != len(clean_ids):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    for user_id, snapshot in snapshots.items():
        tracked_user = await db.get(User, user_id)
        if tracked_user is not None:
            _synchronize_tracked_user_balance(tracked_user, snapshot)
    return snapshots


async def _existing_user_bet(db: AsyncSession, *, user_id: int, bet_id: UUID):
    result = await db.execute(
        select(user_bets).filter(
            and_(user_bets.c.user_id == user_id, user_bets.c.bet_id == bet_id)
        )
    )
    return result.first()


async def insert_user_bet_once(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    access_type: str,
    match_charged: bool,
):
    """Insert behind a savepoint so a unique race cannot poison or partially debit the outer transaction."""
    await lock_bet_row(db, bet_id)
    try:
        async with db.begin_nested():
            await db.execute(
                user_bets.insert().values(
                    user_id=user_id,
                    bet_id=bet_id,
                    taken_at=func.now(),
                    access_type=access_type,
                    match_charged=match_charged,
                )
            )
    except IntegrityError:
        existing = await _existing_user_bet(db, user_id=user_id, bet_id=bet_id)
        if existing is None:
            raise
        return existing
    return None


async def activate_match_subscription(
    db: AsyncSession,
    *,
    user: User,
    plan: SubscriptionPlan,
    payment_provider: str,
    payment_id: str,
) -> Subscription:
    """Activate a purchased/admin-issued match subscription and write the balance ledger."""
    locked_balance = await lock_user_balance(db, user.telegram_id)
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

    next_balance = max(0, current_match_balance(locked_balance)) + matches
    user.purchased_bets_balance = next_balance
    user.matches_remaining = next_balance
    db.add(
        MatchBalanceLog(
            user_id=user.telegram_id,
            subscription_id=subscription.id,
            delta_matches=matches,
            event_type="subscription_purchase",
            note=f"Activated subscription '{plan.name}' via {payment_provider}",
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
    await lock_bet_row(db, bet.id)
    locked_balance = await lock_user_balance(db, user.telegram_id)
    balance_before = current_match_balance(locked_balance)

    existing = await _existing_user_bet(db, user_id=locked_balance.telegram_id, bet_id=bet.id)
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

    if is_staff_role(locked_balance.role):
        access_type = "admin"
    elif charge_match:
        if locked_balance.guarantee_active:
            access_type = "guarantee_replacement"
        else:
            if balance_before <= 0:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="На абонементе не осталось матчей",
                )

            access_type = "paid_match"
            match_charged = True
            no_balance_warning = False

    raced_existing = await insert_user_bet_once(
        db,
        user_id=locked_balance.telegram_id,
        bet_id=bet.id,
        access_type=access_type,
        match_charged=match_charged,
    )
    if raced_existing is not None:
        row = raced_existing._mapping
        return UserBetAccessResult(
            status="already_taken",
            already_recorded=True,
            access_type=row.get("access_type") or "paid_match",
            match_charged=bool(row.get("match_charged")),
            balance_before=balance_before,
            balance_after=balance_before,
            no_balance_warning=False,
        )

    if access_type == "guarantee_replacement":
        db.add(log_match_balance_event(
            user_id=locked_balance.telegram_id,
            bet_id=bet.id,
            event_type="guarantee_replacement_taken",
            note=note or "Free replacement used while guarantee is active",
        ))
    elif match_charged:
        next_balance = balance_before - 1
        user.purchased_bets_balance = next_balance
        user.matches_remaining = next_balance
        db.add(log_match_balance_event(
            user_id=locked_balance.telegram_id,
            bet_id=bet.id,
            event_type="match_debit",
            delta_matches=-1,
            note=note or "Match debited when user added bet to My Bets",
        ))
        if next_balance <= 0 and not user.guarantee_active:
            from src.services.flat_subscriptions import activate_pending_flat_subscription_if_eligible

            await activate_pending_flat_subscription_if_eligible(db, user=user)

    balance_after = next_balance if match_charged else balance_before
    return UserBetAccessResult(
        status="success",
        already_recorded=False,
        access_type=access_type,
        match_charged=match_charged,
        balance_before=balance_before,
        balance_after=balance_after,
        no_balance_warning=no_balance_warning,
    )


async def record_user_free_bet_access(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
) -> UserFreeBetAccessResult:
    """Atomically consume one free forecast credit while preserving user_bets idempotency."""
    await lock_bet_row(db, bet.id)
    locked_balance = await lock_user_balance(db, user.telegram_id)
    free_bets_before = max(0, int(locked_balance.free_bets_available or 0))

    existing = await _existing_user_bet(db, user_id=locked_balance.telegram_id, bet_id=bet.id)
    if existing is not None:
        return UserFreeBetAccessResult(
            already_recorded=True,
            free_bets_before=free_bets_before,
            free_bets_after=free_bets_before,
        )

    if free_bets_before <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="У вас нет доступных бесплатных прогнозов",
        )

    raced_existing = await insert_user_bet_once(
        db,
        user_id=locked_balance.telegram_id,
        bet_id=bet.id,
        access_type="free_bet",
        match_charged=False,
    )
    if raced_existing is not None:
        return UserFreeBetAccessResult(
            already_recorded=True,
            free_bets_before=free_bets_before,
            free_bets_after=free_bets_before,
        )

    user.free_bets_available = free_bets_before - 1
    return UserFreeBetAccessResult(
        already_recorded=False,
        free_bets_before=free_bets_before,
        free_bets_after=free_bets_before - 1,
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
    await lock_bet_row(db, bet.id)
    locked_balance = await lock_user_balance(db, user.telegram_id)
    balance_before = current_match_balance(locked_balance)
    located_res = await db.execute(
        select(user_bets.c.flat_subscription_id).filter(
            and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id)
        )
    )
    located_flat_subscription_id = located_res.scalar_one_or_none()
    if located_flat_subscription_id is not None:
        await lock_flat_subscription_financial_rows(
            db,
            [located_flat_subscription_id],
        )
    existing_res = await db.execute(
        select(user_bets)
        .filter(and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id))
        .with_for_update()
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
