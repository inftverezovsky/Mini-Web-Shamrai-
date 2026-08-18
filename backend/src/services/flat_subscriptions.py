from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from enum import StrEnum
from typing import Iterable, Optional
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import (
    Bet,
    FlatSubscription,
    FlatSubscriptionCredit,
    ForecastRequest,
    ForecastStakeInputSession,
    Subscription,
    User,
    user_bets,
)


MONEY_QUANT = Decimal("0.01")
FLAT_QUANT = Decimal("0.000001")
TARGET_QUANT = Decimal("0.01")
MIN_MONEY_RUB = Decimal("1.00")
MAX_MONEY_RUB = Decimal("100000000.00")
MAX_TARGET_FLATS = Decimal("10000.00")


class FlatSubscriptionState(StrEnum):
    PENDING_SETUP = "pending_setup"
    ACTIVE = "active"
    CLOSING = "closing"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


OPEN_FLAT_SUBSCRIPTION_STATES = (
    FlatSubscriptionState.PENDING_SETUP.value,
    FlatSubscriptionState.ACTIVE.value,
    FlatSubscriptionState.CLOSING.value,
)


@dataclass(frozen=True)
class FlatBetCalculation:
    stake_rub: Decimal
    flat_amount_rub: Decimal
    stake_flats: Decimal
    coefficient: Decimal
    result_status: str
    profit_rub: Decimal
    profit_flats: Decimal


@dataclass(frozen=True)
class FlatBetAccessResult:
    already_recorded: bool
    flat_subscription_id: UUID
    stake_rub: Decimal
    stake_flats: Decimal


@dataclass(frozen=True)
class FlatSettlementUpdate:
    user_id: int
    bet_id: UUID
    flat_subscription_id: UUID
    stake_rub: Decimal
    stake_flats: Decimal
    profit_rub: Decimal
    profit_flats: Decimal
    previous_result: Optional[str]
    result_status: str
    previous_subscription_status: str
    subscription_status: str
    revision: int
    changed: bool = True


@dataclass(frozen=True)
class ForecastStakeInputLookup:
    session: Optional[ForecastStakeInputSession]
    expired: bool = False


def _current_revision(flat_subscription: FlatSubscription) -> int:
    return max(1, int(getattr(flat_subscription, "revision", 1) or 1))


def _touch_revision(flat_subscription: FlatSubscription) -> int:
    next_revision = _current_revision(flat_subscription) + 1
    flat_subscription.revision = next_revision
    return next_revision


def _assert_expected_revision(
    flat_subscription: FlatSubscription,
    expected_revision: Optional[int],
) -> None:
    if expected_revision is None:
        return
    current_revision = _current_revision(flat_subscription)
    if int(expected_revision) != current_revision:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Данные абонемента изменились. Обновите карточку и повторите действие.",
                "current_revision": current_revision,
            },
        )


def _money(value: Decimal | int | str) -> Decimal:
    try:
        parsed = Decimal(str(value)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Введите корректную сумму ставки") from exc
    if not parsed.is_finite():
        raise ValueError("Введите корректную сумму ставки")
    if parsed < MIN_MONEY_RUB or parsed > MAX_MONEY_RUB:
        raise ValueError("Сумма должна быть от 1 до 100 000 000 ₽")
    return parsed


def normalize_target_flats(value: Decimal | int | str) -> Decimal:
    try:
        parsed = Decimal(str(value)).quantize(TARGET_QUANT, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Введите корректное количество флетов") from exc
    if not parsed.is_finite():
        raise ValueError("Введите корректное количество флетов")
    if parsed < TARGET_QUANT or parsed > MAX_TARGET_FLATS:
        raise ValueError("Цель должна быть от 0.01 до 10 000 флетов")
    return parsed


def parse_stake_amount(value: str | int | float | Decimal) -> Decimal:
    if isinstance(value, Decimal):
        return _money(value)
    if isinstance(value, (int, float)):
        return _money(value)

    normalized = str(value or "").strip().lower().replace("₽", "").replace("рублей", "").replace("руб", "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    multiplier = Decimal("1")
    suffix_match = re.fullmatch(r"(.+?)\s*(к|k|тыс\.?)(?:\s*)", normalized)
    if suffix_match:
        normalized = suffix_match.group(1)
        multiplier = Decimal("1000")
    normalized = normalized.replace(" ", "").replace(",", ".")
    try:
        parsed = Decimal(normalized) * multiplier
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("Введите сумму, например 5000 или 5 тыс") from exc
    return _money(parsed)


def calculate_flat_result(
    *,
    stake_rub: Decimal,
    flat_amount_rub: Decimal,
    coefficient: Decimal,
    result_status: str,
) -> FlatBetCalculation:
    stake = _money(stake_rub)
    flat_amount = _money(flat_amount_rub)
    try:
        odds = Decimal(str(coefficient))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Введите корректный коэффициент") from exc
    normalized_status = str(result_status or "").strip().lower()
    if not odds.is_finite():
        raise ValueError("Введите корректный коэффициент")
    if odds < Decimal("1"):
        raise ValueError("Коэффициент должен быть не меньше 1")
    if normalized_status not in {"win", "loss", "refund"}:
        raise ValueError("Результат должен быть win, loss или refund")

    stake_flats = (stake / flat_amount).quantize(FLAT_QUANT, rounding=ROUND_HALF_UP)
    if normalized_status == "win":
        profit_rub = (stake * (odds - Decimal("1"))).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    elif normalized_status == "loss":
        profit_rub = -stake
    else:
        profit_rub = Decimal("0.00")
    profit_flats = (profit_rub / flat_amount).quantize(FLAT_QUANT, rounding=ROUND_HALF_UP)
    return FlatBetCalculation(
        stake_rub=stake,
        flat_amount_rub=flat_amount,
        stake_flats=stake_flats,
        coefficient=odds,
        result_status=normalized_status,
        profit_rub=profit_rub,
        profit_flats=profit_flats,
    )


def next_flat_subscription_status(
    *,
    current_status: FlatSubscriptionState | str,
    profit_flats: Decimal,
    target_flats: Decimal,
    pending_bets: int,
) -> FlatSubscriptionState:
    current = FlatSubscriptionState(str(current_status))
    if current in {FlatSubscriptionState.COMPLETED, FlatSubscriptionState.CANCELLED}:
        return current
    if current == FlatSubscriptionState.PENDING_SETUP:
        return current
    if Decimal(str(profit_flats)) < Decimal(str(target_flats)):
        return FlatSubscriptionState.ACTIVE
    if max(0, int(pending_bets)) > 0:
        return FlatSubscriptionState.CLOSING
    return FlatSubscriptionState.COMPLETED


async def get_open_flat_subscription(
    db: AsyncSession,
    user_id: int,
    *,
    for_update: bool = False,
) -> Optional[FlatSubscription]:
    query = (
        select(FlatSubscription)
        .filter(
            FlatSubscription.user_id == user_id,
            FlatSubscription.status.in_(OPEN_FLAT_SUBSCRIPTION_STATES),
        )
        .order_by(FlatSubscription.created_at.desc(), FlatSubscription.id.desc())
    )
    if for_update:
        query = query.with_for_update()
    return (await db.execute(query)).scalars().first()


async def get_latest_flat_subscription(db: AsyncSession, user_id: int) -> Optional[FlatSubscription]:
    return (
        await db.execute(
            select(FlatSubscription)
            .filter(FlatSubscription.user_id == user_id)
            .order_by(FlatSubscription.created_at.desc(), FlatSubscription.id.desc())
        )
    ).scalars().first()


async def count_pending_flat_bets(db: AsyncSession, flat_subscription_id: UUID) -> int:
    recorded_result = await db.execute(
        select(func.count())
        .select_from(user_bets)
        .filter(
            user_bets.c.flat_subscription_id == flat_subscription_id,
            user_bets.c.settled_status.is_(None),
        )
    )
    reserved_result = await db.execute(
        select(func.count(ForecastRequest.id))
        .select_from(ForecastRequest)
        .outerjoin(
            user_bets,
            and_(
                user_bets.c.user_id == ForecastRequest.user_id,
                user_bets.c.bet_id == ForecastRequest.bet_id,
            ),
        )
        .filter(
            ForecastRequest.flat_subscription_id == flat_subscription_id,
            ForecastRequest.stake_rub.is_not(None),
            ForecastRequest.status.in_(["interested", "processing"]),
            user_bets.c.bet_id.is_(None),
        )
    )
    recorded = max(0, int(recorded_result.scalar() or 0))
    reserved = max(0, int(reserved_result.scalar() or 0))
    return recorded + reserved


async def _has_legacy_activation_blocker(db: AsyncSession, user: User) -> bool:
    await db.flush()
    locked_access = (
        await db.execute(
            select(
                User.purchased_bets_balance,
                User.matches_remaining,
                User.guarantee_active,
            )
            .filter(User.telegram_id == user.telegram_id)
            .with_for_update()
        )
    ).first()
    if locked_access is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Пользователь не найден")
    if max(
        int(locked_access.purchased_bets_balance or 0),
        int(locked_access.matches_remaining or 0),
    ) > 0:
        return True
    if bool(locked_access.guarantee_active):
        return True
    pending_legacy_bets = int(
        (
            await db.execute(
                select(func.count())
                .select_from(user_bets)
                .join(Bet, Bet.id == user_bets.c.bet_id)
                .filter(
                    user_bets.c.user_id == user.telegram_id,
                    user_bets.c.access_type.in_(["paid_match", "guarantee_replacement"]),
                    Bet.status == "pending",
                )
            )
        ).scalar()
        or 0
    )
    return pending_legacy_bets > 0


async def refresh_flat_subscription_totals(
    db: AsyncSession,
    flat_subscription: FlatSubscription,
    *,
    allow_completed_reopen: bool = False,
    force_revision: bool = False,
) -> FlatSubscription:
    previous_profit_rub = Decimal(str(flat_subscription.profit_rub or 0)).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )
    previous_profit_flats = Decimal(str(flat_subscription.profit_flats or 0)).quantize(
        FLAT_QUANT,
        rounding=ROUND_HALF_UP,
    )
    previous_status = str(flat_subscription.status)
    await db.flush()
    totals = (
        await db.execute(
            select(
                func.coalesce(func.sum(user_bets.c.profit_rub), 0),
                func.coalesce(func.sum(user_bets.c.profit_flats), 0),
            ).filter(user_bets.c.flat_subscription_id == flat_subscription.id)
        )
    ).one()
    flat_subscription.profit_rub = Decimal(str(totals[0] or 0)).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    )
    flat_subscription.profit_flats = Decimal(str(totals[1] or 0)).quantize(
        FLAT_QUANT,
        rounding=ROUND_HALF_UP,
    )
    pending_bets = await count_pending_flat_bets(db, flat_subscription.id)
    current = FlatSubscriptionState(flat_subscription.status)
    lifecycle_current = FlatSubscriptionState.ACTIVE if allow_completed_reopen and current == FlatSubscriptionState.COMPLETED else current
    next_status = next_flat_subscription_status(
        current_status=lifecycle_current,
        profit_flats=flat_subscription.profit_flats,
        target_flats=flat_subscription.target_flats,
        pending_bets=pending_bets,
    )
    flat_subscription.status = next_status.value
    if next_status == FlatSubscriptionState.COMPLETED:
        flat_subscription.completed_at = flat_subscription.completed_at or datetime.now(timezone.utc)
    elif allow_completed_reopen:
        flat_subscription.completed_at = None
    if force_revision or (
        previous_profit_rub != flat_subscription.profit_rub
        or previous_profit_flats != flat_subscription.profit_flats
        or previous_status != flat_subscription.status
    ):
        _touch_revision(flat_subscription)
    await db.execute(
        update(Subscription)
        .where(Subscription.flat_subscription_id == flat_subscription.id)
        .values(status=flat_subscription.status)
    )
    return flat_subscription


async def lock_flat_subscription_financial_rows(
    db: AsyncSession,
    flat_subscription_ids: Iterable[Optional[UUID]],
) -> list[FlatSubscription]:
    """Lock subscriptions before their client stake rows in deterministic order."""
    subscription_ids = sorted(
        {item for item in flat_subscription_ids if item is not None},
        key=str,
    )
    if not subscription_ids:
        return []

    # A terminal ForecastRequest status may already be dirty in this session.
    # Never autoflush that row before the financial locks: stake preparation
    # takes the inverse resource path only if Request is flushed first.
    with db.no_autoflush:
        locked_subscriptions = (
            await db.execute(
                select(FlatSubscription)
                .filter(FlatSubscription.id.in_(subscription_ids))
                .order_by(FlatSubscription.id)
                .with_for_update()
            )
        ).scalars().all()
        await db.execute(
            select(
                user_bets.c.flat_subscription_id,
                user_bets.c.user_id,
                user_bets.c.bet_id,
            )
            .filter(user_bets.c.flat_subscription_id.in_(subscription_ids))
            .order_by(
                user_bets.c.flat_subscription_id,
                user_bets.c.user_id,
                user_bets.c.bet_id,
            )
            .with_for_update()
        )
    return list(locked_subscriptions)


async def refresh_flat_subscriptions_after_terminal_requests(
    db: AsyncSession,
    flat_subscription_ids: Iterable[Optional[UUID]],
) -> list[FlatSubscription]:
    """Recompute subscriptions after linked requests stop reserving a stake.

    The canonical ``FlatSubscription -> UserBet`` locks are taken before dirty
    terminal request updates are flushed. Repeating the operation is safe because
    the subscription revision changes only when totals or lifecycle status
    actually change.
    """
    flat_subscriptions = await lock_flat_subscription_financial_rows(
        db,
        flat_subscription_ids,
    )
    for flat_subscription in flat_subscriptions:
        await refresh_flat_subscription_totals(db, flat_subscription)
    return flat_subscriptions


async def credit_flat_subscription(
    db: AsyncSession,
    *,
    user: User,
    target_flats: Decimal,
    event_type: str,
    flat_amount_rub: Optional[Decimal] = None,
    subscription: Optional[Subscription] = None,
    actor_id: Optional[int] = None,
    note: Optional[str] = None,
) -> FlatSubscription:
    target = normalize_target_flats(target_flats)
    # Serialize all target credits for one client. Different payment attempts may
    # be confirmed concurrently, but they must extend the same open programme.
    await db.execute(
        select(User.telegram_id)
        .filter(User.telegram_id == user.telegram_id)
        .with_for_update()
    )
    flat_subscription = await get_open_flat_subscription(db, user.telegram_id, for_update=True)
    if flat_subscription is None:
        legacy_access_active = await _has_legacy_activation_blocker(db, user)
        unit = _money(flat_amount_rub) if flat_amount_rub is not None else None
        now = datetime.now(timezone.utc)
        flat_subscription = FlatSubscription(
            user_id=user.telegram_id,
            status=(
                FlatSubscriptionState.ACTIVE.value
                if unit is not None and not legacy_access_active
                else FlatSubscriptionState.PENDING_SETUP.value
            ),
            flat_amount_rub=unit,
            target_flats=target,
            profit_rub=Decimal("0.00"),
            profit_flats=Decimal("0.000000"),
            activated_at=now if unit is not None and not legacy_access_active else None,
            revision=1,
        )
        db.add(flat_subscription)
        await db.flush()
        await lock_flat_subscription_financial_rows(db, [flat_subscription.id])
    else:
        await lock_flat_subscription_financial_rows(db, [flat_subscription.id])
        flat_subscription.target_flats = (
            Decimal(str(flat_subscription.target_flats or 0)) + target
        ).quantize(TARGET_QUANT, rounding=ROUND_HALF_UP)
        legacy_access_active = await _has_legacy_activation_blocker(db, user)
        if flat_subscription.flat_amount_rub is None and flat_amount_rub is not None:
            flat_subscription.flat_amount_rub = _money(flat_amount_rub)
            if not legacy_access_active:
                flat_subscription.status = FlatSubscriptionState.ACTIVE.value
                flat_subscription.activated_at = datetime.now(timezone.utc)
        await refresh_flat_subscription_totals(
            db,
            flat_subscription,
            force_revision=True,
        )

    if subscription is not None:
        subscription.flat_subscription_id = flat_subscription.id
        subscription.target_flats_snapshot = target
        subscription.status = flat_subscription.status
    if (
        flat_subscription.status == FlatSubscriptionState.PENDING_SETUP.value
        and flat_subscription.flat_amount_rub is not None
    ):
        await activate_pending_flat_subscription_if_eligible(db, user=user)
        if subscription is not None:
            subscription.status = flat_subscription.status
    db.add(
        FlatSubscriptionCredit(
            flat_subscription_id=flat_subscription.id,
            subscription_id=subscription.id if subscription is not None else None,
            delta_target_flats=target,
            event_type=event_type,
            actor_id=actor_id,
            note=note,
        )
    )
    await db.execute(
        update(Subscription)
        .where(Subscription.flat_subscription_id == flat_subscription.id)
        .values(status=flat_subscription.status)
    )
    await db.flush()
    return flat_subscription


async def activate_flat_subscription_purchase(
    db: AsyncSession,
    *,
    user: User,
    plan,
    payment_provider: str,
    payment_id: str,
    flat_amount_rub: Optional[Decimal] = None,
    actor_id: Optional[int] = None,
    credit_event_type: str = "subscription_purchase",
) -> Subscription:
    if str(getattr(plan, "entitlement_type", "legacy_match")) != "flat" or plan.target_flats is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Тариф не настроен как флетовый")
    now = datetime.now(timezone.utc)
    subscription = Subscription(
        user_id=user.telegram_id,
        plan_id=plan.id,
        status=FlatSubscriptionState.PENDING_SETUP.value,
        start_date=now,
        end_date=None,
        payment_provider=payment_provider,
        payment_id=payment_id,
        target_flats_snapshot=normalize_target_flats(plan.target_flats),
    )
    db.add(subscription)
    await db.flush()
    flat_subscription = await credit_flat_subscription(
        db,
        user=user,
        target_flats=plan.target_flats,
        flat_amount_rub=flat_amount_rub,
        event_type=credit_event_type,
        subscription=subscription,
        actor_id=actor_id,
        note=f"Activated flat subscription '{plan.name}' via {payment_provider}",
    )
    subscription.status = flat_subscription.status
    return subscription


def _preview_lifecycle_status(
    flat_subscription: FlatSubscription,
    *,
    profit_flats: Decimal,
    pending_bets: int,
) -> str:
    return next_flat_subscription_status(
        current_status=flat_subscription.status,
        profit_flats=profit_flats,
        target_flats=Decimal(str(flat_subscription.target_flats)),
        pending_bets=pending_bets,
    ).value


async def preview_flat_amount_change(
    db: AsyncSession,
    *,
    user_id: int,
    flat_subscription_id: UUID,
    flat_amount_rub: Decimal,
    expected_revision: Optional[int] = None,
) -> dict:
    flat_subscription = (
        await db.execute(
            select(FlatSubscription).filter(
                FlatSubscription.id == flat_subscription_id,
                FlatSubscription.user_id == user_id,
            )
        )
    ).scalars().first()
    if flat_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    _assert_expected_revision(flat_subscription, expected_revision)
    unit = _money(flat_amount_rub)
    rows = (
        await db.execute(
            select(user_bets)
            .filter(user_bets.c.flat_subscription_id == flat_subscription.id)
            .order_by(user_bets.c.user_id, user_bets.c.bet_id)
        )
    ).all()
    next_profit_rub = Decimal("0.00")
    next_profit_flats = Decimal("0.000000")
    for row in rows:
        values = row._mapping
        if values["settled_status"] is None:
            continue
        calculation = calculate_flat_result(
            stake_rub=Decimal(str(values["stake_rub"])),
            flat_amount_rub=unit,
            coefficient=Decimal(str(values["coefficient_snapshot"])),
            result_status=values["settled_status"],
        )
        next_profit_rub += calculation.profit_rub
        next_profit_flats += calculation.profit_flats
    next_profit_rub = next_profit_rub.quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    next_profit_flats = next_profit_flats.quantize(FLAT_QUANT, rounding=ROUND_HALF_UP)
    pending_bets = await count_pending_flat_bets(db, flat_subscription.id)
    return {
        "kind": "flat_amount",
        "flat_subscription_id": str(flat_subscription.id),
        "current_revision": _current_revision(flat_subscription),
        "affected_bets": len(rows),
        "before": {
            "flat_amount_rub": flat_subscription.flat_amount_rub,
            "profit_rub": Decimal(str(flat_subscription.profit_rub or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            "profit_flats": Decimal(str(flat_subscription.profit_flats or 0)).quantize(FLAT_QUANT, rounding=ROUND_HALF_UP),
            "status": flat_subscription.status,
        },
        "after": {
            "flat_amount_rub": unit,
            "profit_rub": next_profit_rub,
            "profit_flats": next_profit_flats,
            "status": _preview_lifecycle_status(
                flat_subscription,
                profit_flats=next_profit_flats,
                pending_bets=pending_bets,
            ),
        },
    }


async def preview_flat_bet_stake(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    stake_rub: Decimal,
    expected_revision: Optional[int] = None,
) -> dict:
    row = (
        await db.execute(
            select(user_bets).filter(
                user_bets.c.user_id == user_id,
                user_bets.c.bet_id == bet_id,
                user_bets.c.flat_subscription_id.is_not(None),
            )
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовая ставка клиента не найдена")
    values = row._mapping
    flat_subscription = await db.get(FlatSubscription, values["flat_subscription_id"])
    if flat_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    _assert_expected_revision(flat_subscription, expected_revision)
    calculation = calculate_flat_result(
        stake_rub=parse_stake_amount(stake_rub),
        flat_amount_rub=Decimal(str(values["flat_amount_rub_snapshot"])),
        coefficient=Decimal(str(values["coefficient_snapshot"])),
        result_status=values["settled_status"] or "refund",
    )
    current_profit_rub = Decimal(str(flat_subscription.profit_rub or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
    current_profit_flats = Decimal(str(flat_subscription.profit_flats or 0)).quantize(FLAT_QUANT, rounding=ROUND_HALF_UP)
    next_profit_rub = current_profit_rub
    next_profit_flats = current_profit_flats
    if values["settled_status"] is not None:
        next_profit_rub = (
            current_profit_rub
            - Decimal(str(values["profit_rub"] or 0))
            + calculation.profit_rub
        ).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        next_profit_flats = (
            current_profit_flats
            - Decimal(str(values["profit_flats"] or 0))
            + calculation.profit_flats
        ).quantize(FLAT_QUANT, rounding=ROUND_HALF_UP)
    pending_bets = await count_pending_flat_bets(db, flat_subscription.id)
    return {
        "kind": "stake",
        "flat_subscription_id": str(flat_subscription.id),
        "bet_id": str(bet_id),
        "current_revision": _current_revision(flat_subscription),
        "affected_bets": 1,
        "before": {
            "stake_rub": Decimal(str(values["stake_rub"])).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP),
            "profit_rub": current_profit_rub,
            "profit_flats": current_profit_flats,
            "status": flat_subscription.status,
        },
        "after": {
            "stake_rub": calculation.stake_rub,
            "profit_rub": next_profit_rub,
            "profit_flats": next_profit_flats,
            "status": _preview_lifecycle_status(
                flat_subscription,
                profit_flats=next_profit_flats,
                pending_bets=pending_bets,
            ),
        },
    }


async def configure_flat_subscription(
    db: AsyncSession,
    *,
    user_id: int,
    flat_amount_rub: Decimal,
    actor_id: Optional[int] = None,
    flat_subscription_id: Optional[UUID] = None,
    expected_revision: Optional[int] = None,
    note: Optional[str] = None,
) -> FlatSubscription:
    if flat_subscription_id is not None:
        flat_subscription = (
            await db.execute(
                select(FlatSubscription)
                .filter(
                    FlatSubscription.id == flat_subscription_id,
                    FlatSubscription.user_id == user_id,
                )
                .with_for_update()
            )
        ).scalars().first()
    else:
        flat_subscription = await get_open_flat_subscription(db, user_id, for_update=True)
    if flat_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    _assert_expected_revision(flat_subscription, expected_revision)
    unit = _money(flat_amount_rub)
    old_unit = flat_subscription.flat_amount_rub
    flat_subscription.flat_amount_rub = unit
    await db.flush()
    user = await db.get(User, user_id)
    if user is not None:
        await activate_pending_flat_subscription_if_eligible(db, user=user)
    if old_unit is not None and Decimal(str(old_unit)).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    ) == unit:
        return flat_subscription

    rows = (
        await db.execute(
            select(user_bets)
            .filter(user_bets.c.flat_subscription_id == flat_subscription.id)
            .order_by(user_bets.c.user_id, user_bets.c.bet_id)
            .with_for_update()
        )
    ).all()
    for row in rows:
        values = row._mapping
        calculation = calculate_flat_result(
            stake_rub=Decimal(str(values["stake_rub"])),
            flat_amount_rub=unit,
            coefficient=Decimal(str(values["coefficient_snapshot"])),
            result_status=values["settled_status"] or "refund",
        )
        update_values = {
            "flat_amount_rub_snapshot": unit,
            "stake_flats": calculation.stake_flats,
        }
        if values["settled_status"] is not None:
            update_values.update(
                profit_rub=calculation.profit_rub,
                profit_flats=calculation.profit_flats,
            )
        await db.execute(
            update(user_bets)
            .where(
                user_bets.c.user_id == values["user_id"],
                user_bets.c.bet_id == values["bet_id"],
            )
            .values(**update_values)
        )
        await db.execute(
            update(ForecastRequest)
            .where(
                ForecastRequest.user_id == values["user_id"],
                ForecastRequest.bet_id == values["bet_id"],
                ForecastRequest.flat_subscription_id == flat_subscription.id,
            )
            .values(stake_flats=calculation.stake_flats)
        )
    await refresh_flat_subscription_totals(db, flat_subscription, force_revision=True)
    reason_suffix = f"; reason: {note.strip()}" if note and note.strip() else ""
    db.add(
        FlatSubscriptionCredit(
            flat_subscription_id=flat_subscription.id,
            delta_target_flats=Decimal("0.00"),
            event_type="flat_amount_changed" if old_unit is not None else "flat_amount_configured",
            actor_id=actor_id,
            note=(
                f"Flat amount changed from {old_unit} to {unit}{reason_suffix}"
                if old_unit is not None
                else f"Flat amount configured as {unit}{reason_suffix}"
            ),
        )
    )
    return flat_subscription


async def activate_pending_flat_subscription_if_eligible(
    db: AsyncSession,
    *,
    user: User,
) -> Optional[FlatSubscription]:
    if await _has_legacy_activation_blocker(db, user):
        return None
    flat_subscription = await get_open_flat_subscription(db, user.telegram_id, for_update=True)
    if (
        flat_subscription is None
        or flat_subscription.status != FlatSubscriptionState.PENDING_SETUP.value
        or flat_subscription.flat_amount_rub is None
    ):
        return flat_subscription
    flat_subscription.status = FlatSubscriptionState.ACTIVE.value
    flat_subscription.activated_at = flat_subscription.activated_at or datetime.now(timezone.utc)
    _touch_revision(flat_subscription)
    await db.execute(
        update(Subscription)
        .where(Subscription.flat_subscription_id == flat_subscription.id)
        .values(status=flat_subscription.status)
    )
    return flat_subscription


async def record_user_flat_bet_access(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    stake_rub: Decimal,
    forecast_request: Optional[ForecastRequest] = None,
    input_channel: str = "web",
) -> FlatBetAccessResult:
    flat_subscription = await get_open_flat_subscription(db, user.telegram_id, for_update=True)
    reserved_request = (
        forecast_request is not None
        and getattr(forecast_request, "flat_subscription_id", None) == getattr(flat_subscription, "id", None)
        and getattr(forecast_request, "stake_rub", None) is not None
        and getattr(forecast_request, "status", None) in {"interested", "processing"}
    )
    accepts_new_bet = (
        flat_subscription is not None
        and (
            flat_subscription.status == FlatSubscriptionState.ACTIVE.value
            or (flat_subscription.status == FlatSubscriptionState.CLOSING.value and reserved_request)
        )
    )
    if not accepts_new_bet:
        detail = "Абонемент достиг цели и ожидает расчёта открытых ставок" if flat_subscription and flat_subscription.status == FlatSubscriptionState.CLOSING.value else "Нет активного флетового абонемента"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if flat_subscription.flat_amount_rub is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сначала укажите размер одного флета")
    await lock_flat_subscription_financial_rows(db, [flat_subscription.id])

    stake = parse_stake_amount(stake_rub)
    calculation = calculate_flat_result(
        stake_rub=stake,
        flat_amount_rub=Decimal(str(flat_subscription.flat_amount_rub)),
        coefficient=Decimal(str(bet.coefficient)),
        result_status="refund",
    )
    existing = (
        await db.execute(
            select(user_bets)
            .filter(
                user_bets.c.user_id == user.telegram_id,
                user_bets.c.bet_id == bet.id,
            )
            .with_for_update()
        )
    ).first()
    if existing is not None:
        values = existing._mapping
        return FlatBetAccessResult(
            already_recorded=True,
            flat_subscription_id=values["flat_subscription_id"] or flat_subscription.id,
            stake_rub=Decimal(str(values["stake_rub"] or stake)),
            stake_flats=Decimal(str(values["stake_flats"] or calculation.stake_flats)),
        )

    try:
        async with db.begin_nested():
            await db.execute(
                user_bets.insert().values(
                    user_id=user.telegram_id,
                    bet_id=bet.id,
                    taken_at=func.now(),
                    access_type="flat_subscription",
                    match_charged=False,
                    flat_subscription_id=flat_subscription.id,
                    stake_rub=stake,
                    flat_amount_rub_snapshot=flat_subscription.flat_amount_rub,
                    stake_flats=calculation.stake_flats,
                    coefficient_snapshot=bet.coefficient,
                )
            )
    except IntegrityError:
        existing = (
            await db.execute(
                select(user_bets).filter(
                    user_bets.c.user_id == user.telegram_id,
                    user_bets.c.bet_id == bet.id,
                )
            )
        ).first()
        if existing is None:
            raise
        values = existing._mapping
        return FlatBetAccessResult(
            already_recorded=True,
            flat_subscription_id=values["flat_subscription_id"],
            stake_rub=Decimal(str(values["stake_rub"])),
            stake_flats=Decimal(str(values["stake_flats"])),
        )

    if forecast_request is not None:
        forecast_request.flat_subscription_id = flat_subscription.id
        forecast_request.stake_rub = stake
        forecast_request.stake_flats = calculation.stake_flats
        forecast_request.stake_input_channel = input_channel
        forecast_request.stake_submitted_at = datetime.now(timezone.utc)
    _touch_revision(flat_subscription)
    return FlatBetAccessResult(
        already_recorded=False,
        flat_subscription_id=flat_subscription.id,
        stake_rub=stake,
        stake_flats=calculation.stake_flats,
    )


async def prepare_forecast_request_flat_stake(
    db: AsyncSession,
    *,
    forecast_request: ForecastRequest,
    flat_subscription: FlatSubscription,
    stake_rub: Decimal,
    input_channel: str,
) -> FlatSubscription:
    """Persist a stake after the caller locks Bet/User/Flat/UserBet/Request."""
    if flat_subscription.user_id != forecast_request.user_id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка привязана к другому абонементу")
    if forecast_request.flat_subscription_id not in {None, flat_subscription.id}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка уже привязана к другому абонементу")
    if forecast_request.status != "announced":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Заявка уже изменила статус")
    if flat_subscription.status != FlatSubscriptionState.ACTIVE.value:
        detail = "Абонемент достиг цели и ожидает расчёта открытых ставок" if flat_subscription.status == FlatSubscriptionState.CLOSING.value else "Нет активного флетового абонемента"
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if flat_subscription.flat_amount_rub is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сначала укажите размер одного флета")
    calculation = calculate_flat_result(
        stake_rub=parse_stake_amount(stake_rub),
        flat_amount_rub=Decimal(str(flat_subscription.flat_amount_rub)),
        coefficient=Decimal(str(forecast_request.bet.coefficient)),
        result_status="refund",
    )
    forecast_request.flat_subscription_id = flat_subscription.id
    forecast_request.flat_subscription = flat_subscription
    forecast_request.stake_rub = calculation.stake_rub
    forecast_request.stake_flats = calculation.stake_flats
    forecast_request.stake_input_channel = input_channel
    forecast_request.stake_submitted_at = datetime.now(timezone.utc)
    _touch_revision(flat_subscription)
    return flat_subscription


async def settle_flat_bet_takers(
    db: AsyncSession,
    *,
    bet: Bet,
    result_status: str,
    actor_id: Optional[int] = None,
) -> list[FlatSettlementUpdate]:
    discovered_rows = (
        await db.execute(
            select(user_bets.c.flat_subscription_id).filter(
                user_bets.c.bet_id == bet.id,
                user_bets.c.flat_subscription_id.is_not(None),
            )
        )
    ).all()
    subscription_ids = sorted({row.flat_subscription_id for row in discovered_rows}, key=str)
    subscriptions = {}
    if subscription_ids:
        locked = await db.execute(
            select(FlatSubscription)
            .filter(FlatSubscription.id.in_(subscription_ids))
            .order_by(FlatSubscription.id)
            .with_for_update()
        )
        subscriptions = {item.id: item for item in locked.scalars().all()}

    rows = (
        await db.execute(
            select(user_bets)
            .filter(
                user_bets.c.bet_id == bet.id,
                user_bets.c.flat_subscription_id.in_(subscription_ids),
            )
            .order_by(user_bets.c.flat_subscription_id, user_bets.c.user_id, user_bets.c.bet_id)
            .with_for_update()
        )
    ).all() if subscription_ids else []

    changed_rows: list[dict] = []
    affected_subscription_ids: set[UUID] = set()
    previous_subscription_statuses = {
        item_id: item.status for item_id, item in subscriptions.items()
    }
    for row in rows:
        values = row._mapping
        calculation = calculate_flat_result(
            stake_rub=Decimal(str(values["stake_rub"])),
            flat_amount_rub=Decimal(str(values["flat_amount_rub_snapshot"])),
            coefficient=Decimal(str(values["coefficient_snapshot"])),
            result_status=result_status,
        )
        result_changed = values["settled_status"] != calculation.result_status
        amounts_changed = (
            Decimal(str(values["profit_rub"] or 0)).quantize(
                MONEY_QUANT,
                rounding=ROUND_HALF_UP,
            )
            != calculation.profit_rub
            or Decimal(str(values["profit_flats"] or 0)).quantize(
                FLAT_QUANT,
                rounding=ROUND_HALF_UP,
            )
            != calculation.profit_flats
        )
        if result_changed or amounts_changed:
            await db.execute(
                update(user_bets)
                .where(
                    user_bets.c.user_id == values["user_id"],
                    user_bets.c.bet_id == values["bet_id"],
                )
                .values(
                    settled_status=calculation.result_status,
                    profit_rub=calculation.profit_rub,
                    profit_flats=calculation.profit_flats,
                    settled_at=datetime.now(timezone.utc),
                )
            )
            affected_subscription_ids.add(values["flat_subscription_id"])
            changed_rows.append(
                {
                    "user_id": values["user_id"],
                    "bet_id": values["bet_id"],
                    "flat_subscription_id": values["flat_subscription_id"],
                    "stake_rub": calculation.stake_rub,
                    "stake_flats": calculation.stake_flats,
                    "profit_rub": calculation.profit_rub,
                    "profit_flats": calculation.profit_flats,
                    "previous_result": values["settled_status"],
                    "result_status": calculation.result_status,
                }
            )
        previous_result = values["settled_status"]
        if result_changed:
            db.add(
                FlatSubscriptionCredit(
                    flat_subscription_id=values["flat_subscription_id"],
                    delta_target_flats=Decimal("0.00"),
                    event_type="bet_result_settled" if previous_result is None else "bet_result_corrected",
                    actor_id=actor_id,
                    note=f"Bet {bet.id} result changed from {previous_result or 'pending'} to {calculation.result_status}",
                )
            )
    await db.flush()
    for subscription_id in sorted(affected_subscription_ids, key=str):
        flat_subscription = subscriptions[subscription_id]
        await refresh_flat_subscription_totals(db, flat_subscription, force_revision=True)
    return [
        FlatSettlementUpdate(
            **values,
            previous_subscription_status=previous_subscription_statuses[values["flat_subscription_id"]],
            subscription_status=subscriptions[values["flat_subscription_id"]].status,
            revision=_current_revision(subscriptions[values["flat_subscription_id"]]),
        )
        for values in sorted(changed_rows, key=lambda item: (int(item["user_id"]), str(item["bet_id"])))
    ]


async def correct_flat_bet_stake(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    stake_rub: Decimal,
    actor_id: int,
    note: Optional[str] = None,
    expected_revision: Optional[int] = None,
) -> FlatSubscription:
    located = (
        await db.execute(
            select(user_bets.c.flat_subscription_id)
            .filter(
                user_bets.c.user_id == user_id,
                user_bets.c.bet_id == bet_id,
                user_bets.c.flat_subscription_id.is_not(None),
            )
        )
    ).first()
    if located is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовая ставка клиента не найдена")
    flat_subscription = (
        await db.execute(
            select(FlatSubscription)
            .filter(FlatSubscription.id == located.flat_subscription_id)
            .with_for_update()
        )
    ).scalars().first()
    if flat_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    _assert_expected_revision(flat_subscription, expected_revision)
    row = (
        await db.execute(
            select(user_bets)
            .filter(
                user_bets.c.user_id == user_id,
                user_bets.c.bet_id == bet_id,
                user_bets.c.flat_subscription_id == flat_subscription.id,
            )
            .with_for_update()
        )
    ).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовая ставка клиента не найдена")
    values = row._mapping
    result_status = values["settled_status"] or "refund"
    calculation = calculate_flat_result(
        stake_rub=parse_stake_amount(stake_rub),
        flat_amount_rub=Decimal(str(values["flat_amount_rub_snapshot"])),
        coefficient=Decimal(str(values["coefficient_snapshot"])),
        result_status=result_status,
    )
    if Decimal(str(values["stake_rub"])).quantize(
        MONEY_QUANT,
        rounding=ROUND_HALF_UP,
    ) == calculation.stake_rub:
        return flat_subscription
    update_values = {
        "stake_rub": calculation.stake_rub,
        "stake_flats": calculation.stake_flats,
    }
    if values["settled_status"] is not None:
        update_values.update(
            profit_rub=calculation.profit_rub,
            profit_flats=calculation.profit_flats,
        )
    await db.execute(
        update(user_bets)
        .where(user_bets.c.user_id == user_id, user_bets.c.bet_id == bet_id)
        .values(**update_values)
    )
    await db.execute(
        update(ForecastRequest)
        .where(ForecastRequest.user_id == user_id, ForecastRequest.bet_id == bet_id)
        .values(
            stake_rub=calculation.stake_rub,
            stake_flats=calculation.stake_flats,
            stake_submitted_at=datetime.now(timezone.utc),
        )
    )
    reason = note.strip() if note and note.strip() else "not specified"
    db.add(
        FlatSubscriptionCredit(
            flat_subscription_id=flat_subscription.id,
            delta_target_flats=Decimal("0.00"),
            event_type="stake_corrected",
            actor_id=actor_id,
            note=(
                f"Stake for bet {bet_id} changed from {values['stake_rub']} "
                f"to {calculation.stake_rub}; reason: {reason}"
            ),
        )
    )
    await refresh_flat_subscription_totals(db, flat_subscription, force_revision=True)
    return flat_subscription


async def reopen_flat_subscription(
    db: AsyncSession,
    *,
    flat_subscription_id: UUID,
    actor_id: int,
    note: Optional[str] = None,
) -> FlatSubscription:
    flat_subscription = (
        await db.execute(
            select(FlatSubscription)
            .filter(FlatSubscription.id == flat_subscription_id)
            .with_for_update()
        )
    ).scalars().first()
    if flat_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    if flat_subscription.status != FlatSubscriptionState.COMPLETED.value:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Повторно открыть можно только закрытый абонемент")
    if Decimal(str(flat_subscription.profit_flats or 0)) >= Decimal(str(flat_subscription.target_flats or 0)):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Сначала увеличьте цель или исправьте расчёт: текущая прибыль уже достигла цели",
        )
    competing = await get_open_flat_subscription(db, flat_subscription.user_id, for_update=True)
    if competing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="У клиента уже есть другой открытый абонемент")
    flat_subscription.status = FlatSubscriptionState.ACTIVE.value
    flat_subscription.completed_at = None
    flat_subscription.activated_at = flat_subscription.activated_at or datetime.now(timezone.utc)
    _touch_revision(flat_subscription)
    await db.execute(
        update(Subscription)
        .where(Subscription.flat_subscription_id == flat_subscription.id)
        .values(status=flat_subscription.status)
    )
    db.add(
        FlatSubscriptionCredit(
            flat_subscription_id=flat_subscription.id,
            delta_target_flats=Decimal("0.00"),
            event_type="subscription_reopened",
            actor_id=actor_id,
            note=note or "Completed flat subscription reopened by administrator",
        )
    )
    return flat_subscription


async def flat_subscription_payload(db: AsyncSession, flat_subscription: FlatSubscription) -> dict:
    pending_bets = await count_pending_flat_bets(db, flat_subscription.id)
    rows = (
        await db.execute(
            select(
                user_bets.c.bet_id,
                user_bets.c.taken_at,
                user_bets.c.stake_rub,
                user_bets.c.stake_flats,
                user_bets.c.coefficient_snapshot,
                user_bets.c.settled_status,
                user_bets.c.profit_rub,
                user_bets.c.profit_flats,
                user_bets.c.settled_at,
                Bet.event_name,
                Bet.outcome,
            )
            .join(Bet, Bet.id == user_bets.c.bet_id)
            .filter(user_bets.c.flat_subscription_id == flat_subscription.id)
            .order_by(user_bets.c.taken_at.desc())
        )
    ).all()
    credit_rows = (
        await db.execute(
            select(FlatSubscriptionCredit)
            .filter(FlatSubscriptionCredit.flat_subscription_id == flat_subscription.id)
            .order_by(FlatSubscriptionCredit.created_at.desc(), FlatSubscriptionCredit.id.desc())
        )
    ).scalars().all()
    profit_flats = Decimal(str(flat_subscription.profit_flats or 0))
    target_flats = Decimal(str(flat_subscription.target_flats or 0))
    return {
        "id": flat_subscription.id,
        "user_id": flat_subscription.user_id,
        "status": flat_subscription.status,
        "revision": _current_revision(flat_subscription),
        "flat_amount_rub": flat_subscription.flat_amount_rub,
        "target_flats": target_flats,
        "profit_rub": flat_subscription.profit_rub,
        "profit_flats": profit_flats,
        "remaining_flats": max(Decimal("0"), target_flats - profit_flats),
        "pending_bets": pending_bets,
        "activated_at": flat_subscription.activated_at,
        "completed_at": flat_subscription.completed_at,
        "created_at": flat_subscription.created_at,
        "updated_at": flat_subscription.updated_at,
        "bets": [
            {
                "bet_id": row.bet_id,
                "event_name": row.event_name,
                "outcome": row.outcome,
                "taken_at": row.taken_at,
                "stake_rub": row.stake_rub,
                "stake_flats": row.stake_flats,
                "coefficient": row.coefficient_snapshot,
                "status": row.settled_status or "pending",
                "profit_rub": row.profit_rub,
                "profit_flats": row.profit_flats,
                "settled_at": row.settled_at,
            }
            for row in rows
        ],
        "credits": [
            {
                "id": credit.id,
                "event_type": credit.event_type,
                "delta_target_flats": credit.delta_target_flats,
                "actor_id": credit.actor_id,
                "note": credit.note,
                "created_at": credit.created_at,
            }
            for credit in credit_rows
        ],
    }


async def start_forecast_stake_input(
    db: AsyncSession,
    *,
    channel: str,
    user_id: int,
    forecast_request_id: UUID,
    ttl_minutes: int = 15,
) -> ForecastStakeInputSession:
    normalized_channel = str(channel).strip().lower()
    if normalized_channel not in {"telegram", "vk"}:
        raise ValueError("Unsupported stake input channel")
    await db.execute(
        select(User.telegram_id)
        .filter(User.telegram_id == user_id)
        .with_for_update()
    )
    await db.execute(
        delete(ForecastStakeInputSession).where(
            ForecastStakeInputSession.channel == normalized_channel,
            ForecastStakeInputSession.user_id == user_id,
        )
    )
    session = ForecastStakeInputSession(
        channel=normalized_channel,
        user_id=user_id,
        forecast_request_id=forecast_request_id,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=max(1, ttl_minutes)),
    )
    db.add(session)
    await db.flush()
    return session


async def get_forecast_stake_input(
    db: AsyncSession,
    *,
    channel: str,
    user_id: int,
    for_update: bool = False,
) -> Optional[ForecastStakeInputSession]:
    lookup = await lookup_forecast_stake_input(
        db,
        channel=channel,
        user_id=user_id,
        for_update=for_update,
    )
    return lookup.session


async def lookup_forecast_stake_input(
    db: AsyncSession,
    *,
    channel: str,
    user_id: int,
    for_update: bool = False,
) -> ForecastStakeInputLookup:
    query = select(ForecastStakeInputSession).filter(
        ForecastStakeInputSession.channel == str(channel).strip().lower(),
        ForecastStakeInputSession.user_id == user_id,
    )
    if for_update:
        query = query.with_for_update()
    input_session = (await db.execute(query)).scalars().first()
    if input_session is None:
        return ForecastStakeInputLookup(session=None, expired=False)
    expires_at = input_session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= datetime.now(timezone.utc):
        await db.delete(input_session)
        return ForecastStakeInputLookup(session=None, expired=True)
    return ForecastStakeInputLookup(session=input_session, expired=False)


async def clear_forecast_stake_input(
    db: AsyncSession,
    *,
    channel: str,
    user_id: int,
) -> None:
    await db.execute(
        delete(ForecastStakeInputSession).where(
            ForecastStakeInputSession.channel == str(channel).strip().lower(),
            ForecastStakeInputSession.user_id == user_id,
        )
    )
