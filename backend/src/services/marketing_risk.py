from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import IdentityDeviceLink, MarketingRewardEvent, ReferralRewardEvent, User


RISK_STATUS_APPROVED = "approved"
RISK_STATUS_HELD = "held"
RISK_STATUS_REJECTED = "rejected"


@dataclass(frozen=True)
class RiskDecision:
    status: str
    score: int
    reasons: list[str]


async def users_share_identity_device(db: AsyncSession, referrer_id: int, referred_id: int) -> bool:
    referrer_result = await db.execute(
        select(IdentityDeviceLink.device_key_hash).filter(IdentityDeviceLink.source_user_id == referrer_id)
    )
    referrer_hashes = {str(item) for item in referrer_result.scalars().all()}
    if not referrer_hashes:
        return False

    referred_result = await db.execute(
        select(IdentityDeviceLink.device_key_hash).filter(IdentityDeviceLink.source_user_id == referred_id)
    )
    referred_hashes = {str(item) for item in referred_result.scalars().all()}
    return bool(referrer_hashes.intersection(referred_hashes))


async def evaluate_referral_purchase_risk(
    db: AsyncSession,
    *,
    referrer: User,
    referred_user: User,
) -> RiskDecision:
    if referrer.telegram_id == referred_user.telegram_id:
        return RiskDecision(RISK_STATUS_REJECTED, 100, ["self_referral"])

    reasons: list[str] = []
    score = 0

    if await users_share_identity_device(db, referrer.telegram_id, referred_user.telegram_id):
        reasons.append("same_identity_device")
        score = max(score, 70)

    one_hour_ago = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_referrals = int((await db.execute(
        select(func.count(ReferralRewardEvent.id)).filter(
            ReferralRewardEvent.referrer_user_id == referrer.telegram_id,
            ReferralRewardEvent.created_at >= one_hour_ago,
        )
    )).scalar() or 0)
    if recent_referrals >= 3:
        reasons.append("rapid_referral_rewards")
        score = max(score, 50)

    if reasons:
        return RiskDecision(RISK_STATUS_HELD, score, reasons)
    return RiskDecision(RISK_STATUS_APPROVED, 0, [])


async def evaluate_marketing_reward_risk(
    db: AsyncSession,
    *,
    user: User,
    widget_key: str,
) -> RiskDecision:
    one_day_ago = datetime.now(timezone.utc) - timedelta(hours=24)
    recent_rewards = int((await db.execute(
        select(func.count(MarketingRewardEvent.id)).filter(
            MarketingRewardEvent.user_id == user.telegram_id,
            MarketingRewardEvent.created_at >= one_day_ago,
        )
    )).scalar() or 0)
    if recent_rewards >= 10:
        return RiskDecision(RISK_STATUS_HELD, 45, [f"high_daily_marketing_reward_volume:{widget_key}"])
    return RiskDecision(RISK_STATUS_APPROVED, 0, [])
