from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.models import FlatSubscription, Subscription, SubscriptionPlan, User
from src.services.delivery_outbox import (
    CHANNEL_TELEGRAM_MESSAGE,
    CHANNEL_VK_MESSAGE,
    enqueue_delivery,
)
from src.services.signals import broadcast_personal_signals, signal_stream_hub

SUBSCRIPTION_CREDIT_SIGNAL_TYPE = "subscription_credit"
FLAT_BET_RESULT_SIGNAL_TYPE = "flat_bet_result"
FLAT_ADJUSTMENT_SIGNAL_TYPE = "flat_subscription_adjustment"


def _flat_status_label(value: str) -> str:
    return {
        "pending_setup": "Требуется настройка",
        "active": "Активен",
        "closing": "Закрывается — ждём расчёта открытых ставок",
        "completed": "Цель достигнута, абонемент завершён",
        "cancelled": "Отменён",
    }.get(str(value), str(value))


def _flat_result_label(value: str) -> str:
    return {
        "win": "Выигрыш",
        "loss": "Проигрыш",
        "refund": "Возврат",
    }.get(str(value), str(value))


def _rub(value: Decimal) -> str:
    return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}".replace(",", " ")


async def enqueue_flat_settlement_notification(
    db: AsyncSession,
    *,
    user: User,
    bet: Any,
    flat_subscription: FlatSubscription,
    settlement: Any,
) -> None:
    target = Decimal(str(flat_subscription.target_flats or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total_profit = Decimal(str(flat_subscription.profit_flats or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    remaining = max(Decimal("0"), target - total_profit).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    profit_rub = Decimal(str(settlement.profit_rub)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    profit_flats = Decimal(str(settlement.profit_flats)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    corrected = settlement.previous_result is not None
    heading = "✏️ Расчёт прогноза исправлен" if corrected else "📊 Прогноз рассчитан"
    text = "\n".join(
        [
            heading,
            f"Матч: {getattr(bet, 'event_name', None) or 'Прогноз'}",
            f"Результат: {_flat_result_label(settlement.result_status)}",
            f"Сумма ставки: {_rub(settlement.stake_rub)} ₽ ({Decimal(str(settlement.stake_flats)):.6f} флета)",
            f"Итог ставки: {_rub(profit_rub)} ₽ / {profit_flats:.6f} флета",
            f"Общая прибыль: {_rub(flat_subscription.profit_rub or 0)} ₽ / {total_profit:.6f} флета",
            f"Цель: +{target:.2f} флета",
            f"Осталось до цели: {remaining:.6f} флета",
            f"Статус: {_flat_status_label(flat_subscription.status)}",
        ]
    )
    signal_data = {
        "event_type": FLAT_BET_RESULT_SIGNAL_TYPE,
        "bet_id": str(settlement.bet_id),
        "flat_subscription_id": str(flat_subscription.id),
        "result_status": settlement.result_status,
        "previous_result_status": settlement.previous_result,
        "corrected": corrected,
        "stake_rub": str(settlement.stake_rub),
        "stake_flats": str(settlement.stake_flats),
        "profit_rub": str(profit_rub),
        "profit_flats": str(profit_flats),
        "total_profit_rub": str(flat_subscription.profit_rub or 0),
        "total_profit_flats": str(total_profit),
        "target_flats": str(target),
        "remaining_flats": str(remaining),
        "flat_subscription_status": flat_subscription.status,
        "previous_flat_subscription_status": settlement.previous_subscription_status,
        "revision": settlement.revision,
        "push_title": "Расчёт абонемента исправлен" if corrected else "Прогноз рассчитан",
        "push_body": f"{_flat_result_label(settlement.result_status)}: {profit_flats:.6f} флета. Осталось {remaining:.6f}.",
        "url": "/app",
    }
    await broadcast_personal_signals(
        db,
        users=[user],
        text=text,
        signal_type=FLAT_BET_RESULT_SIGNAL_TYPE,
        data=signal_data,
        send_telegram=True,
        send_web_push=True,
    )
    if user.vk_user_id and user.vk_messages_allowed:
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=(
                f"flat-result:{flat_subscription.id}:{settlement.bet_id}:"
                f"revision:{settlement.revision}:vk"
            ),
            payload={"message": text},
        )


async def enqueue_flat_adjustment_notification(
    db: AsyncSession,
    *,
    user: User,
    flat_subscription: FlatSubscription,
    adjustment_kind: str,
    reason: str,
    bet_id: Optional[str] = None,
) -> None:
    target = Decimal(str(flat_subscription.target_flats or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    profit_flats = Decimal(str(flat_subscription.profit_flats or 0)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    remaining = max(Decimal("0"), target - profit_flats).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    kind_label = "размер флета" if adjustment_kind == "flat_amount" else "сумма ставки"
    text = "\n".join(
        [
            "✏️ Данные флетового абонемента обновлены",
            f"Изменено: {kind_label}",
            f"Причина: {reason}",
            f"Размер флета: {_rub(flat_subscription.flat_amount_rub or 0)} ₽",
            f"Текущая прибыль: {_rub(flat_subscription.profit_rub or 0)} ₽ / {profit_flats:.6f} флета",
            f"Цель: +{target:.2f} флета",
            f"Осталось до цели: {remaining:.6f} флета",
            f"Статус: {_flat_status_label(flat_subscription.status)}",
        ]
    )
    revision = max(1, int(getattr(flat_subscription, "revision", 1) or 1))
    signal_data = {
        "event_type": FLAT_ADJUSTMENT_SIGNAL_TYPE,
        "adjustment_kind": adjustment_kind,
        "flat_subscription_id": str(flat_subscription.id),
        "bet_id": bet_id,
        "flat_amount_rub": str(flat_subscription.flat_amount_rub or 0),
        "profit_rub": str(flat_subscription.profit_rub or 0),
        "profit_flats": str(profit_flats),
        "target_flats": str(target),
        "remaining_flats": str(remaining),
        "flat_subscription_status": flat_subscription.status,
        "revision": revision,
        "push_title": "Абонемент пересчитан",
        "push_body": f"Обновлены данные: {kind_label}. Осталось {remaining:.6f} флета.",
        "url": "/app",
    }
    await broadcast_personal_signals(
        db,
        users=[user],
        text=text,
        signal_type=FLAT_ADJUSTMENT_SIGNAL_TYPE,
        data=signal_data,
        send_telegram=True,
        send_web_push=True,
    )
    if user.vk_user_id and user.vk_messages_allowed:
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=(
                f"flat-adjustment:{flat_subscription.id}:{adjustment_kind}:"
                f"revision:{revision}:vk"
            ),
            payload={"message": text},
        )


def _match_word(count: int) -> str:
    value = abs(int(count))
    if value % 100 in {11, 12, 13, 14}:
        return "матчей"
    if value % 10 == 1:
        return "матч"
    if value % 10 in {2, 3, 4}:
        return "матча"
    return "матчей"


def _client_label(user: User) -> str:
    full_name = " ".join(
        part for part in [user.first_name, user.last_name] if part
    ).strip()
    username = f"@{user.username}" if user.username else ""
    if full_name and username:
        return f"{full_name} ({username}, ID {user.telegram_id})"
    if full_name:
        return f"{full_name} (ID {user.telegram_id})"
    if username:
        return f"{username} (ID {user.telegram_id})"
    if user.vk_user_id:
        return f"VK ID {user.vk_user_id} (ID {user.telegram_id})"
    return f"ID {user.telegram_id}"


def _actor_label(actor: Optional[User]) -> str:
    if actor is None:
        return "система"
    username = f"@{actor.username}" if actor.username else ""
    return f"{username or 'админ'} (ID {actor.telegram_id})"


def build_subscription_credit_client_text(
    *,
    plan: SubscriptionPlan,
    matches_added: int,
    balance_after: int,
    flat_subscription: Optional[FlatSubscription] = None,
) -> str:
    if str(plan.entitlement_type or "legacy_match") == "flat":
        target_added = Decimal(str(plan.target_flats or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total_target = Decimal(str(getattr(flat_subscription, "target_flats", target_added))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        flat_amount = getattr(flat_subscription, "flat_amount_rub", None)
        return "\n".join(
            [
                "✅ Вам начислена цель флетового абонемента.",
                f"Начислено к цели: +{target_added} флета",
                f"Тариф: {plan.name}",
                f"Общая цель: +{total_target} флета",
                (
                    f"Размер одного флета: {Decimal(str(flat_amount)):,.2f} ₽".replace(",", " ")
                    if flat_amount is not None
                    else "Укажите размер одного флета в приложении."
                ),
            ]
        )
    return "\n".join(
        [
            "✅ Вам начислены матчи в абонемент.",
            f"Начислено: +{matches_added} {_match_word(matches_added)}",
            f"Тариф: {plan.name}",
            f"Остаток абонемента: {balance_after} {_match_word(balance_after)}",
        ]
    )


def build_subscription_credit_admin_text(
    *,
    user: User,
    plan: SubscriptionPlan,
    matches_added: int,
    balance_after: int,
    actor: Optional[User] = None,
    flat_subscription: Optional[FlatSubscription] = None,
) -> str:
    if str(plan.entitlement_type or "legacy_match") == "flat":
        target_added = Decimal(str(plan.target_flats or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total_target = Decimal(str(getattr(flat_subscription, "target_flats", target_added))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        flat_amount = getattr(flat_subscription, "flat_amount_rub", None)
        return "\n".join(
            [
                "🔔 Начисление флетового абонемента",
                f"Клиент: {_client_label(user)}",
                f"Начислено к цели: +{target_added} флета",
                f"Общая цель: +{total_target} флета",
                f"Размер флета: {flat_amount or 'не указан'} ₽" if flat_amount is not None else "Размер флета: не указан",
                f"Кем: {_actor_label(actor)}",
            ]
        )
    return "\n".join(
        [
            "🔔 Начисление абонемента",
            f"Клиент: {_client_label(user)}",
            f"Начислено: +{matches_added} {_match_word(matches_added)}",
            (
                f"Тариф: {plan.name} "
                f"({int(plan.match_count or 0)} {_match_word(int(plan.match_count or 0))})"
            ),
            f"Абонемент клиента: {balance_after} {_match_word(balance_after)}",
            f"Кем: {_actor_label(actor)}",
        ]
    )


def build_subscription_credit_signal_data(
    *,
    subscription: Subscription,
    plan: SubscriptionPlan,
    matches_added: int,
    balance_after: int,
    source: str,
    flat_subscription: Optional[FlatSubscription] = None,
) -> dict:
    if str(plan.entitlement_type or "legacy_match") == "flat":
        target_added = Decimal(str(plan.target_flats or 0)).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        total_target = Decimal(str(getattr(flat_subscription, "target_flats", target_added))).quantize(
            Decimal("0.01"),
            rounding=ROUND_HALF_UP,
        )
        return {
            "event_type": SUBSCRIPTION_CREDIT_SIGNAL_TYPE,
            "subscription_id": str(subscription.id),
            "plan_id": plan.id,
            "plan_name": plan.name,
            "entitlement_type": "flat",
            "target_flats_added": str(target_added),
            "target_flats": str(total_target),
            "flat_amount_rub": str(flat_subscription.flat_amount_rub) if flat_subscription and flat_subscription.flat_amount_rub is not None else None,
            "flat_subscription_status": flat_subscription.status if flat_subscription else "pending_setup",
            "source": source,
            "push_title": "Цель абонемента пополнена",
            "push_body": f"К цели добавлено +{target_added} флета. Общая цель: +{total_target}.",
            "url": "/app",
        }
    return {
        "event_type": SUBSCRIPTION_CREDIT_SIGNAL_TYPE,
        "subscription_id": str(subscription.id),
        "plan_id": plan.id,
        "plan_name": plan.name,
        "plan_match_count": int(plan.match_count or 0),
        "matches_added": matches_added,
        "balance_after": balance_after,
        "source": source,
        "push_title": "Абонемент пополнен",
        "push_body": (
            f"+{matches_added} {_match_word(matches_added)}. "
            f"Остаток: {balance_after} {_match_word(balance_after)}."
        ),
        "url": "/app",
    }


def _admin_group_chat_id() -> Optional[int]:
    chat_id = settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
    if chat_id is None:
        return None
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


async def enqueue_subscription_credit_notifications(
    db: AsyncSession,
    *,
    user: User,
    plan: SubscriptionPlan,
    subscription: Subscription,
    actor: Optional[User] = None,
    source: str = "admin_manual",
) -> None:
    flat_subscription = (
        await db.get(FlatSubscription, subscription.flat_subscription_id)
        if str(plan.entitlement_type or "legacy_match") == "flat" and subscription.flat_subscription_id is not None
        else None
    )
    matches_added = max(0, int(plan.match_count or 0))
    balance_after = int(user.purchased_bets_balance or user.matches_remaining or 0)
    client_text = build_subscription_credit_client_text(
        plan=plan,
        matches_added=matches_added,
        balance_after=balance_after,
        flat_subscription=flat_subscription,
    )
    signal_data = build_subscription_credit_signal_data(
        subscription=subscription,
        plan=plan,
        matches_added=matches_added,
        balance_after=balance_after,
        source=source,
        flat_subscription=flat_subscription,
    )

    await broadcast_personal_signals(
        db,
        users=[user],
        text=client_text,
        signal_type=SUBSCRIPTION_CREDIT_SIGNAL_TYPE,
        data=signal_data,
        send_telegram=True,
        send_web_push=True,
    )

    if user.vk_user_id and user.vk_messages_allowed:
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"subscription:{subscription.id}:client_vk_credit",
            payload={"message": client_text},
        )

    admin_group_chat_id = _admin_group_chat_id()
    if admin_group_chat_id:
        await enqueue_delivery(
            db,
            channel=CHANNEL_TELEGRAM_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"subscription:{subscription.id}:admin_group_credit",
            payload={
                "method": "sendMessage",
                "payload": {
                    "chat_id": admin_group_chat_id,
                    "text": build_subscription_credit_admin_text(
                        user=user,
                        plan=plan,
                        matches_added=matches_added,
                        balance_after=balance_after,
                        actor=actor,
                        flat_subscription=flat_subscription,
                    ),
                    "disable_web_page_preview": True,
                },
            },
        )


async def enqueue_flat_target_credit_notifications(
    db: AsyncSession,
    *,
    user: User,
    flat_subscription: FlatSubscription,
    target_flats_added: Decimal,
    actor: Optional[User] = None,
    source: str = "admin_manual",
) -> None:
    added = Decimal(str(target_flats_added)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    total = Decimal(str(flat_subscription.target_flats or 0)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    client_text = "\n".join(
        [
            "✅ Цель флетового абонемента пополнена.",
            f"Начислено к цели: +{added} флета",
            f"Общая цель: +{total} флета",
            f"Текущая прибыль: {Decimal(str(flat_subscription.profit_flats or 0)).quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP)} флета",
        ]
    )
    signal_data = {
        "event_type": SUBSCRIPTION_CREDIT_SIGNAL_TYPE,
        "entitlement_type": "flat",
        "flat_subscription_id": str(flat_subscription.id),
        "target_flats_added": str(added),
        "target_flats": str(total),
        "profit_flats": str(flat_subscription.profit_flats or 0),
        "flat_amount_rub": str(flat_subscription.flat_amount_rub) if flat_subscription.flat_amount_rub is not None else None,
        "flat_subscription_status": flat_subscription.status,
        "source": source,
        "push_title": "Цель абонемента пополнена",
        "push_body": f"К цели добавлено +{added} флета. Общая цель: +{total}.",
        "url": "/app",
    }
    await broadcast_personal_signals(
        db,
        users=[user],
        text=client_text,
        signal_type=SUBSCRIPTION_CREDIT_SIGNAL_TYPE,
        data=signal_data,
        send_telegram=True,
        send_web_push=True,
    )

    dedupe_base = f"flat-credit:{flat_subscription.id}:{source}:{total}"
    if user.vk_user_id and user.vk_messages_allowed:
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"{dedupe_base}:client_vk",
            payload={"message": client_text},
        )

    admin_group_chat_id = _admin_group_chat_id()
    if admin_group_chat_id:
        await enqueue_delivery(
            db,
            channel=CHANNEL_TELEGRAM_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"{dedupe_base}:admin_group",
            payload={
                "method": "sendMessage",
                "payload": {
                    "chat_id": admin_group_chat_id,
                    "text": "\n".join(
                        [
                            "🔔 Начисление цели флетового абонемента",
                            f"Клиент: {_client_label(user)}",
                            f"Начислено: +{added} флета",
                            f"Общая цель: +{total} флета",
                            f"Кем: {_actor_label(actor)}",
                        ]
                    ),
                    "disable_web_page_preview": True,
                },
            },
        )
