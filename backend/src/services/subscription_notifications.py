from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from src.core.config import settings
from src.models.models import Subscription, SubscriptionPlan, User
from src.services.delivery_outbox import (
    CHANNEL_TELEGRAM_MESSAGE,
    CHANNEL_VK_MESSAGE,
    enqueue_delivery,
)
from src.services.signals import broadcast_personal_signals, signal_stream_hub

SUBSCRIPTION_CREDIT_SIGNAL_TYPE = "subscription_credit"


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
) -> str:
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
) -> str:
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
) -> dict:
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
    matches_added = max(0, int(plan.match_count or 0))
    balance_after = int(user.purchased_bets_balance or user.matches_remaining or 0)
    client_text = build_subscription_credit_client_text(
        plan=plan,
        matches_added=matches_added,
        balance_after=balance_after,
    )
    signal_data = build_subscription_credit_signal_data(
        subscription=subscription,
        plan=plan,
        matches_added=matches_added,
        balance_after=balance_after,
        source=source,
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
                    ),
                    "disable_web_page_preview": True,
                },
            },
        )
