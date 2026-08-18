from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.models.models import ABTestConfig, SubscriptionPlan, User


MONEY_QUANTUM = Decimal("0.01")
RUB_PER_AB_PRICE_UNIT = Decimal("10.00")


@dataclass(frozen=True)
class EffectiveSubscriptionPrice:
    rub: Decimal
    stars: int
    ab_config_id: Optional[int] = None
    ab_group: Optional[str] = None

    def audit_metadata(self) -> dict[str, object]:
        return {
            "ab_test_config_id": self.ab_config_id,
            "ab_group": self.ab_group,
            "effective_amount_rub": f"{self.rub:.2f}",
        }


async def effective_subscription_price(
    db: AsyncSession,
    *,
    plan: SubscriptionPlan,
    user: Optional[User],
) -> EffectiveSubscriptionPrice:
    """Return the one canonical displayed/charged RUB price for a user.

    Historical A/B configs store price units used by the existing UI, where
    one unit equals ten rubles.  The same conversion is now used for plan
    listing and both RUB checkout providers, and PaymentAttempt.amount freezes
    the result before contacting a provider.
    """
    base_rub = Decimal(str(plan.price)).quantize(MONEY_QUANTUM, rounding=ROUND_HALF_UP)
    base_stars = int(plan.price_stars or 0)
    if user is None or str(plan.currency or "RUB").upper() != "RUB":
        return EffectiveSubscriptionPrice(rub=base_rub, stars=base_stars)

    result = await db.execute(
        select(ABTestConfig)
        .filter(ABTestConfig.plan_id == plan.id, ABTestConfig.is_active.is_(True))
        .order_by(ABTestConfig.id.desc())
        .limit(1)
    )
    config = result.scalars().first()
    if config is None:
        return EffectiveSubscriptionPrice(rub=base_rub, stars=base_stars)

    group = "B" if str(user.ab_group or "A").upper() == "B" else "A"
    price_units = int(config.price_group_b if group == "B" else config.price_group_a)
    effective_rub = (Decimal(price_units) * RUB_PER_AB_PRICE_UNIT).quantize(
        MONEY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )
    return EffectiveSubscriptionPrice(
        rub=effective_rub,
        stars=price_units,
        ab_config_id=int(config.id),
        ab_group=group,
    )
