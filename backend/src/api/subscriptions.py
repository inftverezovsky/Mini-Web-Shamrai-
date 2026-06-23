from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from src.models.database import get_db, get_read_db
from src.models.models import User, SubscriptionPlan, Subscription, ABTestConfig, AdminAuditLog, PaymentAttempt
from src.schemas.schemas import (
    SubscriptionPlanResponse,
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionResponse,
    SubscriptionCreate,
    SubscriptionManualAssign
)
from src.api.deps import get_current_user, get_current_admin, get_current_privileged_admin, get_optional_user_read
from src.core.roles import is_staff_role
from src.core.config import settings
from src.services.match_access import activate_match_subscription
from src.services.telegram_bot import call_telegram_api_async

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# --- USER ENDPOINTS ---

@router.get("/plans", response_model=List[SubscriptionPlanResponse])
async def list_plans(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_read_db),
    current_user: Optional[User] = Depends(get_optional_user_read)
):
    """Retrieve available subscription plans and apply A/B pricing."""
    all_result = await db.execute(select(SubscriptionPlan))
    all_plans = all_result.scalars().all()

    can_view_inactive = bool(include_inactive and current_user and is_staff_role(current_user.role))
    plans = all_plans if can_view_inactive else [plan for plan in all_plans if plan.is_active]
        
    # Apply A/B pricing if active for each plan
    response_plans = []
    for plan in plans:
        ab_res = await db.execute(
            select(ABTestConfig).filter(ABTestConfig.plan_id == plan.id, ABTestConfig.is_active == True)
        )
        ab_config = ab_res.scalars().first()
        
        plan_copy = SubscriptionPlan(
            id=plan.id,
            name=plan.name,
            duration_days=plan.duration_days,
            match_count=plan.match_count,
            price=plan.price,
            price_stars=plan.price_stars,
            currency=plan.currency,
            is_active=plan.is_active
        )
        
        if ab_config and current_user:
            if current_user.ab_group == 'B':
                plan_copy.price_stars = ab_config.price_group_b
                plan_copy.price = Decimal(ab_config.price_group_b * 10)
            else:
                plan_copy.price_stars = ab_config.price_group_a
                plan_copy.price = Decimal(ab_config.price_group_a * 10)
                
        response_plans.append(plan_copy)
        
    return response_plans


def add_subscription_audit_log(
    db: AsyncSession,
    *,
    actor: User,
    action: str,
    target_user_id: Optional[int] = None,
    details: Optional[dict] = None,
) -> None:
    db.add(AdminAuditLog(
        actor_id=actor.telegram_id,
        target_user_id=target_user_id,
        action=action,
        details=details or {},
    ))

@router.post("/buy", response_model=SubscriptionResponse, status_code=status.HTTP_201_CREATED)
async def purchase_subscription(
    purchase: SubscriptionCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Debug-only direct subscription activation.
    Production purchases must go through /payments/invoice or /payments/yookassa/create.
    """
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug purchase is disabled")

    # Fetch plan details
    plan_res = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == purchase.plan_id))
    plan = plan_res.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription plan not found"
        )
        
    now = datetime.now(timezone.utc)
    subscription = await activate_match_subscription(
        db,
        user=current_user,
        plan=plan,
        payment_provider="debug_subscription_buy",
        payment_id=f"debug_buy_{current_user.telegram_id}_{int(now.timestamp())}",
    )
    
    db.add(subscription)
    await db.commit()
    
    # Reload relation fields
    result = await db.execute(
        select(Subscription)
        .filter(Subscription.id == subscription.id)
        .options(selectinload(Subscription.plan))
    )
    return result.scalars().first()

@router.get("/my-status", response_model=Optional[SubscriptionResponse])
async def get_my_subscription_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Retrieve the most recently active subscription status of the current user."""
    result = await db.execute(
        select(Subscription)
        .filter(Subscription.user_id == current_user.telegram_id)
        .options(selectinload(Subscription.plan))
        .order_by(Subscription.created_at.desc())
    )
    return result.scalars().first()

# --- ADMIN ENDPOINTS ---

@router.post("/plans", response_model=SubscriptionPlanResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: SubscriptionPlanCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Create a new billing subscription plan."""
    plan = SubscriptionPlan(**plan_data.model_dump())
    db.add(plan)
    add_subscription_audit_log(
        db,
        actor=admin,
        action="plan_created",
        details=plan_data.model_dump(mode="json"),
    )
    await db.commit()
    await db.refresh(plan)
    return plan


@router.put("/plans/{plan_id}", response_model=SubscriptionPlanResponse)
async def update_plan(
    plan_id: int,
    plan_data: SubscriptionPlanUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Update a match subscription plan."""
    result = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id))
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    update_data = plan_data.model_dump(exclude_unset=True)
    audit_changes = {}
    for field, value in update_data.items():
        audit_changes[field] = {"from": getattr(plan, field), "to": value}
        setattr(plan, field, value)
    if audit_changes:
        add_subscription_audit_log(
            db,
            actor=admin,
            action="plan_updated",
            details={"plan_id": plan_id, "changes": audit_changes},
        )
    await db.commit()
    await db.refresh(plan)
    return plan


@router.delete("/plans/{plan_id}", status_code=status.HTTP_200_OK)
async def delete_plan(
    plan_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Permanently delete a match subscription plan."""
    result = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id))
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    plan_name = plan.name
    was_active = bool(plan.is_active)
    await db.execute(update(Subscription).where(Subscription.plan_id == plan_id).values(plan_id=None))
    await db.execute(update(PaymentAttempt).where(PaymentAttempt.plan_id == plan_id).values(plan_id=None))
    await db.execute(delete(ABTestConfig).where(ABTestConfig.plan_id == plan_id))
    await db.delete(plan)
    add_subscription_audit_log(
        db,
        actor=admin,
        action="plan_deleted",
        details={"plan_id": plan_id, "name": plan_name, "was_active": was_active},
    )
    await db.commit()
    return {"status": "success", "message": "Plan deleted"}

@router.post("/assign", response_model=SubscriptionResponse)
async def manually_assign_subscription(
    assignment: SubscriptionManualAssign,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Forcefully issue or extend a subscription for a subscriber."""
    # Check target user
    user_res = await db.execute(select(User).filter(User.telegram_id == assignment.user_id))
    user = user_res.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscriber not found"
        )
        
    # Check plan
    plan_res = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == assignment.plan_id))
    plan = plan_res.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )
        
    now = datetime.now(timezone.utc)
    subscription = await activate_match_subscription(
        db,
        user=user,
        plan=plan,
        payment_provider="admin_manual",
        payment_id=f"admin_assign_{admin.telegram_id}_{int(now.timestamp())}",
    )
    add_subscription_audit_log(
        db,
        actor=admin,
        action="subscription_assigned",
        target_user_id=user.telegram_id,
        details={"plan_id": plan.id, "matches_added": plan.match_count},
    )
    
    db.add(subscription)
    await db.commit()
    
    result = await db.execute(
        select(Subscription)
        .filter(Subscription.id == subscription.id)
        .options(selectinload(Subscription.plan))
    )
    return result.scalars().first()


@router.post("/invite-link")
async def generate_vip_invite_link(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/subscriptions/invite-link
    Generates a single-use Telegram Chat invite link for active VIP subscribers.
    """
    if (
        not is_staff_role(current_user.role)
        and (current_user.purchased_bets_balance or current_user.matches_remaining or 0) <= 0
        and not current_user.guarantee_active
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Требуется активный абонемент по матчам"
        )
        
    chat_id = settings.TELEGRAM_VIP_CHAT_ID
    
    payload = {
        "chat_id": chat_id,
        "member_limit": 1
    }
    res = await call_telegram_api_async("createChatInviteLink", payload)
    
    if res.get("ok"):
        invite_link = res["result"]["invite_link"]
        current_user.tg_chat_joined = True
        await db.commit()
        return {"invite_link": invite_link}

    if settings.DEBUG_MODE:
        mock_link = f"https://t.me/joinchat/mock_vip_link_{current_user.telegram_id}"
        current_user.tg_chat_joined = True
        await db.commit()
        return {"invite_link": mock_link, "note": "Mock link bypass"}

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Telegram invite link failed: {res.get('description', 'Unknown error')}",
    )
