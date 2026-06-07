from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import Subscription, User, user_bets


REFERRAL_DISCOUNT_STEP_PERCENT = 5
REFERRAL_DISCOUNT_MAX_PERCENT = 100
PAID_REFERRAL_SUBSCRIPTION_EXCLUDED_PROVIDERS = {
    "admin_manual",
    "admin_override",
}


async def get_referral_stats(db: AsyncSession, user_id: int) -> dict:
    invited_res = await db.execute(
        select(User.telegram_id).filter(User.referred_by_user_id == user_id)
    )
    invited_ids = [row[0] for row in invited_res.all()]
    if not invited_ids:
        return {
            "invited_count": 0,
            "purchased_invited_count": 0,
            "discount_step_percent": REFERRAL_DISCOUNT_STEP_PERCENT,
            "referral_discount_percent": 0,
        }

    subscription_buyers_res = await db.execute(
        select(Subscription.user_id)
        .filter(
            Subscription.user_id.in_(invited_ids),
            Subscription.status == "active",
            or_(
                Subscription.payment_provider.is_(None),
                ~Subscription.payment_provider.in_(
                    PAID_REFERRAL_SUBSCRIPTION_EXCLUDED_PROVIDERS
                ),
            ),
        )
        .distinct()
    )
    subscription_buyers = {row[0] for row in subscription_buyers_res.all()}

    single_bet_buyers_res = await db.execute(
        select(user_bets.c.user_id)
        .filter(
            user_bets.c.user_id.in_(invited_ids),
            user_bets.c.access_type == "single_bet_purchase",
        )
        .distinct()
    )
    single_bet_buyers = {row[0] for row in single_bet_buyers_res.all()}

    purchased_invited_count = len(subscription_buyers | single_bet_buyers)
    referral_discount_percent = min(
        REFERRAL_DISCOUNT_MAX_PERCENT,
        purchased_invited_count * REFERRAL_DISCOUNT_STEP_PERCENT,
    )

    return {
        "invited_count": len(invited_ids),
        "purchased_invited_count": purchased_invited_count,
        "discount_step_percent": REFERRAL_DISCOUNT_STEP_PERCENT,
        "referral_discount_percent": referral_discount_percent,
    }


async def get_referral_discount_percent(db: AsyncSession, user_id: int) -> int:
    stats = await get_referral_stats(db, user_id)
    return int(stats["referral_discount_percent"])
