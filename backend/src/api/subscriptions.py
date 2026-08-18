from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import List, Optional
from decimal import Decimal
from uuid import uuid4

from src.models.database import get_db, get_read_db
from src.models.models import User, SubscriptionPlan, Subscription, AdminAuditLog
from src.schemas.schemas import (
    FlatSubscriptionConfigureRequest,
    FlatSubscriptionResponse,
    SubscriptionPlanResponse,
    SubscriptionPlanAdminResponse,
    SubscriptionPlanAllowlistResponse,
    SubscriptionPlanAllowlistUpdate,
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    SubscriptionResponse,
    SubscriptionCreate,
    SubscriptionManualAssign
)
from src.api.deps import get_current_user, get_current_admin, get_current_privileged_admin, get_optional_user_read
from src.core.roles import is_staff_role
from src.core.config import settings
from src.services import subscription_notifications
from src.services.match_access import activate_match_subscription
from src.services.flat_subscriptions import (
    FlatSubscriptionState,
    activate_flat_subscription_purchase,
    configure_flat_subscription,
    flat_subscription_payload,
    get_latest_flat_subscription,
    get_open_flat_subscription,
)
from src.services.telegram_bot import call_telegram_api_async
from src.services.subscription_pricing import effective_subscription_price

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

# --- USER ENDPOINTS ---

@router.get("/plans", response_model=List[SubscriptionPlanResponse])
async def list_plans(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_read_db),
    current_user: Optional[User] = Depends(get_optional_user_read)
):
    """Retrieve public plans plus hidden test plans visible to this user."""
    all_result = await db.execute(
        select(SubscriptionPlan).options(selectinload(SubscriptionPlan.allowed_checkout_users))
    )
    all_plans = all_result.scalars().all()

    can_view_inactive = bool(include_inactive and current_user and is_staff_role(current_user.role))
    if can_view_inactive:
        plans = all_plans
    else:
        current_user_id = int(current_user.telegram_id) if current_user is not None else None
        plans = [
            plan
            for plan in all_plans
            if plan.is_active
            and str(plan.entitlement_type or "legacy_match") == "flat"
            and (
                not plan.is_hidden
                or current_user_id is not None
                and current_user_id in plan.allowed_user_ids
            )
        ]
        
    # The same helper is used by YooKassa/Tegro before freezing attempt.amount.
    response_plans = []
    for plan in plans:
        effective_price = await effective_subscription_price(db, plan=plan, user=current_user)
        plan_copy = SubscriptionPlan(
            id=plan.id,
            name=plan.name,
            duration_days=plan.duration_days,
            match_count=plan.match_count,
            entitlement_type=plan.entitlement_type,
            target_flats=plan.target_flats,
            price=effective_price.rub,
            price_stars=effective_price.stars,
            currency=plan.currency,
            is_active=plan.is_active,
            is_hidden=plan.is_hidden,
        )
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
        
    if str(plan.entitlement_type or "legacy_match") == "flat":
        subscription = await activate_flat_subscription_purchase(
            db,
            user=current_user,
            plan=plan,
            payment_provider="debug_subscription_buy",
            payment_id=f"debug_buy_{current_user.telegram_id}_{uuid4().hex}",
        )
    else:
        subscription = await activate_match_subscription(
            db,
            user=current_user,
            plan=plan,
            payment_provider="debug_subscription_buy",
            payment_id=f"debug_buy_{current_user.telegram_id}_{uuid4().hex}",
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


@router.get("/flats/current", response_model=Optional[FlatSubscriptionResponse])
async def get_current_flat_subscription(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_read_db),
):
    flat_subscription = await get_latest_flat_subscription(db, current_user.telegram_id)
    if flat_subscription is None:
        return None
    return await flat_subscription_payload(db, flat_subscription)


@router.post("/flats/current/configure", response_model=FlatSubscriptionResponse)
async def configure_current_flat_subscription(
    payload: FlatSubscriptionConfigureRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    current_flat = await get_open_flat_subscription(db, current_user.telegram_id, for_update=True)
    if current_flat is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Флетовый абонемент не найден")
    if current_flat.status != FlatSubscriptionState.PENDING_SETUP.value or current_flat.flat_amount_rub is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Размер флета уже настроен. Для изменения обратитесь к администратору",
        )
    flat_subscription = await configure_flat_subscription(
        db,
        user_id=current_user.telegram_id,
        flat_amount_rub=payload.flat_amount_rub,
        actor_id=current_user.telegram_id,
        flat_subscription_id=current_flat.id,
        expected_revision=payload.expected_revision,
        note=payload.note,
    )
    await db.commit()
    await db.refresh(flat_subscription)
    return await flat_subscription_payload(db, flat_subscription)

# --- ADMIN ENDPOINTS ---

async def _resolve_plan_allowlist_users(db: AsyncSession, user_ids: List[int]) -> List[User]:
    normalized_ids = list(dict.fromkeys(int(user_id) for user_id in user_ids))
    if not normalized_ids:
        return []
    result = await db.execute(select(User).filter(User.telegram_id.in_(normalized_ids)))
    users_by_id = {int(user.telegram_id): user for user in result.scalars().all()}
    missing_ids = [user_id for user_id in normalized_ids if user_id not in users_by_id]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"message": "Пользователи allowlist не найдены", "user_ids": missing_ids},
        )
    return [users_by_id[user_id] for user_id in normalized_ids]


def _validate_hidden_plan(*, entitlement_type: str, is_hidden: bool) -> None:
    if is_hidden and entitlement_type != "flat":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Скрытый тестовый тариф может быть только флетовым",
        )


@router.post("/plans", response_model=SubscriptionPlanAdminResponse, status_code=status.HTTP_201_CREATED)
async def create_plan(
    plan_data: SubscriptionPlanCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Create a new billing subscription plan."""
    if plan_data.entitlement_type == "flat" and plan_data.target_flats is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите цель в флетах")
    if plan_data.entitlement_type == "legacy_match" and plan_data.match_count < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Для старого тарифа укажите число матчей")
    _validate_hidden_plan(entitlement_type=plan_data.entitlement_type, is_hidden=plan_data.is_hidden)
    plan_fields = plan_data.model_dump()
    allowed_user_ids = plan_fields.pop("allowed_user_ids", [])
    allowed_users = await _resolve_plan_allowlist_users(db, allowed_user_ids)
    plan = SubscriptionPlan(**plan_fields)
    plan.allowed_checkout_users = allowed_users
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


@router.put("/plans/{plan_id}", response_model=SubscriptionPlanAdminResponse)
async def update_plan(
    plan_id: int,
    plan_data: SubscriptionPlanUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Update a match subscription plan."""
    result = await db.execute(
        select(SubscriptionPlan)
        .filter(SubscriptionPlan.id == plan_id)
        .options(selectinload(SubscriptionPlan.allowed_checkout_users))
    )
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    update_data = plan_data.model_dump(exclude_unset=True)
    next_entitlement_type = update_data.get("entitlement_type", plan.entitlement_type)
    next_target_flats = update_data.get("target_flats", plan.target_flats)
    if next_entitlement_type == "flat" and next_target_flats is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите цель в флетах")
    next_match_count = update_data.get("match_count", plan.match_count)
    if next_entitlement_type == "legacy_match" and int(next_match_count or 0) < 1:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Для старого тарифа укажите число матчей")
    next_is_hidden = bool(update_data.get("is_hidden", plan.is_hidden))
    _validate_hidden_plan(entitlement_type=next_entitlement_type, is_hidden=next_is_hidden)
    allowed_user_ids = update_data.pop("allowed_user_ids", None)
    audit_changes = {}
    for field, value in update_data.items():
        audit_changes[field] = {"from": getattr(plan, field), "to": value}
        setattr(plan, field, value)
    if allowed_user_ids is not None:
        old_user_ids = plan.allowed_user_ids
        plan.allowed_checkout_users = await _resolve_plan_allowlist_users(db, allowed_user_ids)
        audit_changes["allowed_user_ids"] = {"from": old_user_ids, "to": allowed_user_ids}
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


@router.get("/plans/{plan_id}/allowlist", response_model=SubscriptionPlanAllowlistResponse)
async def get_plan_allowlist(
    plan_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_read_db),
):
    result = await db.execute(
        select(SubscriptionPlan)
        .filter(SubscriptionPlan.id == plan_id)
        .options(selectinload(SubscriptionPlan.allowed_checkout_users))
    )
    plan = result.scalars().first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    return SubscriptionPlanAllowlistResponse(
        plan_id=plan.id,
        is_hidden=bool(plan.is_hidden),
        allowed_user_ids=plan.allowed_user_ids,
    )


@router.put("/plans/{plan_id}/allowlist", response_model=SubscriptionPlanAllowlistResponse)
async def update_plan_allowlist(
    plan_id: int,
    payload: SubscriptionPlanAllowlistUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(SubscriptionPlan)
        .filter(SubscriptionPlan.id == plan_id)
        .options(selectinload(SubscriptionPlan.allowed_checkout_users))
    )
    plan = result.scalars().first()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan not found")
    old_user_ids = plan.allowed_user_ids
    plan.allowed_checkout_users = await _resolve_plan_allowlist_users(db, payload.allowed_user_ids)
    add_subscription_audit_log(
        db,
        actor=admin,
        action="plan_allowlist_updated",
        details={
            "plan_id": plan.id,
            "from": old_user_ids,
            "to": payload.allowed_user_ids,
        },
    )
    await db.commit()
    return SubscriptionPlanAllowlistResponse(
        plan_id=plan.id,
        is_hidden=bool(plan.is_hidden),
        allowed_user_ids=plan.allowed_user_ids,
    )


@router.delete("/plans/{plan_id}", status_code=status.HTTP_200_OK)
async def delete_plan(
    plan_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Archive a plan without breaking historical payment references."""
    result = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id))
    plan = result.scalars().first()
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Plan not found"
        )

    was_active = bool(plan.is_active)
    plan.is_active = False
    add_subscription_audit_log(
        db,
        actor=admin,
        action="plan_archived",
        details={"plan_id": plan_id, "name": plan.name, "was_active": was_active},
    )
    await db.commit()
    return {"status": "success", "message": "Plan archived"}

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
        
    if str(plan.entitlement_type or "legacy_match") == "flat":
        existing_flat = await get_open_flat_subscription(db, user.telegram_id)
        if existing_flat is None and assignment.flat_amount_rub is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Для первого начисления укажите размер одного флета клиента",
            )
        subscription = await activate_flat_subscription_purchase(
            db,
            user=user,
            plan=plan,
            flat_amount_rub=assignment.flat_amount_rub,
            actor_id=admin.telegram_id,
            credit_event_type="manual_credit",
            payment_provider="admin_manual",
            payment_id=f"admin_assign_{admin.telegram_id}_{uuid4().hex}",
        )
    else:
        subscription = await activate_match_subscription(
            db,
            user=user,
            plan=plan,
            payment_provider="admin_manual",
            payment_id=f"admin_assign_{admin.telegram_id}_{uuid4().hex}",
        )
    add_subscription_audit_log(
        db,
        actor=admin,
        action="subscription_assigned",
        target_user_id=user.telegram_id,
        details={
            "plan_id": plan.id,
            "matches_added": plan.match_count if plan.entitlement_type == "legacy_match" else 0,
            "target_flats_added": str(plan.target_flats or 0) if plan.entitlement_type == "flat" else "0",
        },
    )
    await subscription_notifications.enqueue_subscription_credit_notifications(
        db,
        user=user,
        plan=plan,
        subscription=subscription,
        actor=admin,
        source="admin_manual",
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
        flat_subscription = await get_open_flat_subscription(db, current_user.telegram_id)
        if flat_subscription is None or flat_subscription.status not in {
            FlatSubscriptionState.ACTIVE.value,
            FlatSubscriptionState.CLOSING.value,
        }:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Требуется активный абонемент"
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
