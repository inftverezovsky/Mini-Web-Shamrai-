from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import delete, func, update
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID
from pydantic import BaseModel

from src.models.database import get_db
from src.models.models import (
    AdminAuditLog,
    Bet,
    Bookmaker,
    CrowdBetParticipant,
    DailyRewardClaim,
    ForecastRequest,
    Marathon,
    MatchBalanceLog,
    PaymentAttempt,
    PromoCode,
    PvPBattleVote,
    Subscription,
    SubscriptionPlan,
    User,
    UserBadge,
    UserNote,
    user_bets,
    user_bookmakers,
)
from src.schemas.schemas import (
    AdminAuditLogResponse,
    AdminGrantRequest,
    AdminUpdateUserPreferences,
    AdminUserListResponse,
    BetResponse,
    UserResponse,
)
from src.api.deps import get_current_admin, get_current_privileged_admin
from src.core.roles import ADMIN_ROLES, ROLE_LABELS, STAFF_ROLES, VALID_ROLES, is_admin_role, is_owner_role, normalize_role
from src.services.forecast_delivery import FORECAST_STATUS_REMOVED
from src.services.match_access import log_match_balance_event, revoke_user_bet_access

router = APIRouter(prefix="/admin", tags=["Admin Operations"])

class PromoCreate(BaseModel):
    code: str
    discount_percent: int
    valid_until: datetime

class MarathonCreateOrUpdate(BaseModel):
    title: Optional[str] = None
    target_multiplier: Optional[float] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    is_active: Optional[bool] = None


async def load_user_response(db: AsyncSession, telegram_id: int) -> User:
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == telegram_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    return result.scalars().first()


def build_admin_user_response(
    user: User,
    recent_match_results: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    match_balance = int(
        user.purchased_bets_balance
        if (user.purchased_bets_balance or 0) != 0
        else (user.matches_remaining or 0)
    )
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "photo_url": user.photo_url,
        "is_web_only": user.is_web_only,
        "role": user.role,
        "stats_display_mode": user.stats_display_mode,
        "has_active_subscription": match_balance > 0 or user.guarantee_active,
        "subscription_end_date": None,
        "purchased_bets_balance": match_balance,
        "matches_remaining": match_balance,
        "guarantee_active": user.guarantee_active,
        "guarantee_opened_from_bet_id": user.guarantee_opened_from_bet_id,
        "guarantee_closed_at": user.guarantee_closed_at,
        "bookmakers": user.bookmakers,
        "other_bookmaker_name": user.other_bookmaker_name,
        "client_group": user.client_group,
        "client_tag": user.client_tag,
        "ab_group": user.ab_group,
        "tg_chat_joined": user.tg_chat_joined,
        "badges": user.badges,
        "recent_match_results": recent_match_results or [],
    }


def add_admin_audit_log(
    db: AsyncSession,
    *,
    actor: User,
    action: str,
    target_user_id: Optional[int] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    db.add(AdminAuditLog(
        actor_id=actor.telegram_id,
        target_user_id=target_user_id,
        action=action,
        details=details or {},
    ))


async def count_users_with_role(db: AsyncSession, role: str) -> int:
    result = await db.execute(select(func.count(User.telegram_id)).filter(User.role == role))
    return result.scalar() or 0


async def count_privileged_admins(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(User.telegram_id)).filter(User.role.in_(list(ADMIN_ROLES))))
    return result.scalar() or 0


async def ensure_role_change_allowed(
    *,
    db: AsyncSession,
    actor: User,
    target: User,
    next_role: str,
) -> None:
    if next_role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Поле role должно иметь одно из значений: {', '.join(VALID_ROLES)}"
        )

    if not is_admin_role(actor.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Только owner/admin могут управлять ролями"
        )

    if target.telegram_id == actor.telegram_id and target.role != next_role:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя менять собственную роль из интерфейса"
        )

    owner_count = await count_users_with_role(db, "owner")
    if next_role == "owner" and not is_owner_role(actor.role) and owner_count > 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Назначать владельцев может только текущий owner"
        )

    if target.role == "owner" and not is_owner_role(actor.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Изменять владельца может только owner"
        )

    if target.role == "owner" and next_role != "owner" and owner_count <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя снять права у последнего владельца"
        )

    if is_admin_role(target.role) and not is_admin_role(next_role):
        privileged_count = await count_privileged_admins(db)
        if privileged_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Нельзя снять права у последнего администратора"
            )


# --- ADMIN STATISTICS / STATS ---

@router.get("/dashboard/stats")
async def get_admin_dashboard_stats(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/dashboard/stats
    Returns aggregate stats metrics: revenue, users with paid balance, ROI, Winrate, and Total Users.
    """
    # 1. Total users
    res_users = await db.execute(select(func.count(User.telegram_id)))
    total_users = res_users.scalar() or 0

    # 2. Active subscribers (users with remaining matches or active guarantee)
    now = datetime.now(timezone.utc)
    res_active = await db.execute(
        select(func.count(User.telegram_id)).filter(
            (User.purchased_bets_balance > 0) | (User.matches_remaining > 0) | (User.guarantee_active == True)
        )
    )
    active_subscribers = res_active.scalar() or 0

    # 3. Total revenue from actually processed payment attempts.
    res_revenue = await db.execute(
        select(func.coalesce(func.sum(PaymentAttempt.amount), 0))
        .filter(PaymentAttempt.status == "succeeded")
    )
    total_revenue = int(res_revenue.scalar() or 0)

    # 4. Bet performance calculations for the channel and the current author.
    res_bets = await db.execute(select(Bet))
    all_bets = res_bets.scalars().all()

    def summarize_bets(source_bets: List[Bet]) -> Dict[str, float]:
        total_bets = len(source_bets)
        settled_bets = [bet for bet in source_bets if bet.status in ("win", "loss", "refund")]
        won = 0
        lost = 0
        profit = Decimal("0.00")
        coefficient_sum = Decimal("0.00")

        for bet in source_bets:
            coefficient_sum += bet.coefficient

        for bet in settled_bets:
            if bet.status == "win":
                won += 1
                profit += bet.coefficient - Decimal("1.00")
            elif bet.status == "loss":
                lost += 1
                profit += Decimal("-1.00")

        resolved = won + lost
        winrate = (won / resolved * 100) if resolved > 0 else 0.0
        roi = (float(profit) / len(settled_bets) * 100) if settled_bets else 0.0
        average_coefficient = float(coefficient_sum / Decimal(total_bets)) if total_bets > 0 else 0.0

        return {
            "total_bets": total_bets,
            "winrate": round(winrate, 2),
            "roi": round(roi, 2),
            "average_coefficient": round(average_coefficient, 2),
        }

    channel_performance = summarize_bets(all_bets)
    author_performance = summarize_bets([bet for bet in all_bets if bet.author_id == admin.telegram_id])

    # 5. Gather monthly revenue points for the chart
    from collections import defaultdict
    res_payments = await db.execute(
        select(PaymentAttempt.processed_at, PaymentAttempt.created_at, PaymentAttempt.amount)
        .filter(PaymentAttempt.status == "succeeded")
    )
    payments_data = res_payments.all()
    
    monthly_revenue = defaultdict(int)
    for processed_at, created_at, amount in payments_data:
        date_ref = processed_at or created_at
        if date_ref:
            m_str = date_ref.strftime("%Y-%m")
            monthly_revenue[m_str] += int(amount or 0)
            
    sorted_rev_months = sorted(monthly_revenue.keys())
    revenue_points = []
    cumulative_revenue = 0
    for m in sorted_rev_months:
        cumulative_revenue += monthly_revenue[m]
        revenue_points.append({
            "month": m,
            "revenue": cumulative_revenue
        })
        
    if not revenue_points:
        current_month = datetime.now().strftime("%Y-%m")
        revenue_points.append({"month": current_month, "revenue": 0})

    # 6. A/B testing split conversion metrics
    res_a_total = await db.execute(select(func.count(User.telegram_id)).filter(User.ab_group == 'A'))
    total_a = res_a_total.scalar() or 0
    
    res_b_total = await db.execute(select(func.count(User.telegram_id)).filter(User.ab_group == 'B'))
    total_b = res_b_total.scalar() or 0
    
    res_a_active = await db.execute(
        select(func.count(User.telegram_id))
        .filter(
            User.ab_group == 'A',
            (User.purchased_bets_balance > 0) | (User.matches_remaining > 0) | (User.guarantee_active == True)
        )
    )
    active_a = res_a_active.scalar() or 0
    
    res_b_active = await db.execute(
        select(func.count(User.telegram_id))
        .filter(
            User.ab_group == 'B',
            (User.purchased_bets_balance > 0) | (User.matches_remaining > 0) | (User.guarantee_active == True)
        )
    )
    active_b = res_b_active.scalar() or 0
    
    conv_a = (active_a / total_a * 100) if total_a > 0 else 0.0
    conv_b = (active_b / total_b * 100) if total_b > 0 else 0.0
    
    # Revenue A
    res_a_rev = await db.execute(
        select(func.coalesce(func.sum(PaymentAttempt.amount), 0))
        .join(User, PaymentAttempt.user_id == User.telegram_id)
        .filter(PaymentAttempt.status == "succeeded", User.ab_group == 'A')
    )
    rev_a = int(res_a_rev.scalar() or 0)
    
    # Revenue B
    res_b_rev = await db.execute(
        select(func.coalesce(func.sum(PaymentAttempt.amount), 0))
        .join(User, PaymentAttempt.user_id == User.telegram_id)
        .filter(PaymentAttempt.status == "succeeded", User.ab_group == 'B')
    )
    rev_b = int(res_b_rev.scalar() or 0)

    return {
        "total_revenue": total_revenue,
        "active_subscribers": active_subscribers,
        "channel_roi": channel_performance["roi"],
        "winrate": channel_performance["winrate"],
        "total_bets_issued": int(channel_performance["total_bets"]),
        "average_coefficient": channel_performance["average_coefficient"],
        "author_total_bets_issued": int(author_performance["total_bets"]),
        "author_winrate": author_performance["winrate"],
        "author_average_coefficient": author_performance["average_coefficient"],
        "author_channel_roi": author_performance["roi"],
        "total_users": total_users,
        "revenue_points": revenue_points,
        "ab_test_metrics": {
            "group_a_users": total_a,
            "group_b_users": total_b,
            "group_a_active_subs": active_a,
            "group_b_active_subs": active_b,
            "group_a_conversion": round(conv_a, 2),
            "group_b_conversion": round(conv_b, 2),
            "group_a_revenue": rev_a,
            "group_b_revenue": rev_b
        }
    }

# --- PENDING FORECASTS FOR RESOLVING ---

@router.get("/bets/pending", response_model=List[BetResponse])
async def get_pending_bets(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/bets/pending
    Lists all non-calculated sports predictions.
    """
    query = (
        select(Bet)
        .filter(Bet.status == "pending")
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.created_at.desc())
    )
    res = await db.execute(query)
    return res.scalars().all()


@router.delete("/bets/{bet_id}")
async def delete_bet_from_admin(
    bet_id: UUID,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    DELETE /api/admin/bets/{bet_id}
    Soft-deletes a forecast and removes all client access/balance impact.
    """
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден",
        )

    users_result = await db.execute(
        select(User)
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .filter(user_bets.c.bet_id == bet.id)
    )
    takers = users_result.scalars().all()
    revoke_results = []
    revoke_results_by_user_id = {}
    for user in takers:
        revoke_result = await revoke_user_bet_access(
            db,
            user=user,
            bet=bet,
            actor_id=admin.telegram_id,
            reason="Forecast deleted by admin",
        )
        revoke_results.append(revoke_result)
        revoke_results_by_user_id[user.telegram_id] = revoke_result

    requests_result = await db.execute(
        select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id)
    )
    marked_requests = 0
    for forecast_request in requests_result.scalars().all():
        if forecast_request.status != FORECAST_STATUS_REMOVED:
            marked_requests += 1
        forecast_request.status = FORECAST_STATUS_REMOVED
        forecast_request.handled_by = admin.telegram_id
        revoke_result = revoke_results_by_user_id.get(forecast_request.user_id)
        if revoke_result and revoke_result.had_access:
            if forecast_request.balance_before is None:
                forecast_request.balance_before = revoke_result.balance_before
            forecast_request.balance_after = revoke_result.balance_after
            forecast_request.no_balance_warning = False

    already_deleted = bet.status == "deleted"
    bet.status = "deleted"
    bet.resolved_at = bet.resolved_at or datetime.now(timezone.utc)

    revoked_count = sum(1 for item in revoke_results if item.had_access)
    balance_delta_total = sum(item.delta_matches for item in revoke_results)
    add_admin_audit_log(
        db,
        actor=admin,
        action="bet_deleted",
        details={
            "bet_id": str(bet.id),
            "event_name": bet.event_name,
            "already_deleted": already_deleted,
            "revoked_count": revoked_count,
            "marked_requests": marked_requests,
            "balance_delta_total": balance_delta_total,
        },
    )
    await db.commit()
    return {
        "status": "success",
        "bet_id": str(bet.id),
        "revoked_count": revoked_count,
        "marked_requests": marked_requests,
        "balance_delta_total": balance_delta_total,
    }

# --- PROMO CODES MANAGER ---

@router.post("/promo")
async def create_promo(
    data: PromoCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/promo/
    Creates a new active promotional discount code.
    """
    exists = await db.execute(select(PromoCode).filter(PromoCode.code == data.code))
    if exists.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Промокод с таким названием уже существует"
        )
        
    promo = PromoCode(
        code=data.code.upper(),
        discount_percent=data.discount_percent,
        valid_until=data.valid_until,
        is_active=True
    )
    db.add(promo)
    add_admin_audit_log(
        db,
        actor=admin,
        action="promo_created",
        details={
            "code": promo.code,
            "discount_percent": promo.discount_percent,
            "valid_until": promo.valid_until.isoformat(),
        },
    )
    await db.commit()
    await db.refresh(promo)
    return promo

@router.get("/promo/list")
async def list_promos(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/promo/list
    Retrieves all registered discount codes.
    """
    res = await db.execute(select(PromoCode).order_by(PromoCode.id.desc()))
    return res.scalars().all()

@router.post("/promo/{code_id}/deactivate")
async def deactivate_promo(
    code_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/promo/{code_id}/deactivate
    Deactivates a promotional code.
    """
    res = await db.execute(select(PromoCode).filter(PromoCode.id == code_id))
    promo = res.scalars().first()
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Промокод не найден"
        )
    if promo.is_active:
        promo.is_active = False
        add_admin_audit_log(
            db,
            actor=admin,
            action="promo_deactivated",
            details={"code": promo.code, "code_id": promo.id},
        )
    await db.commit()
    return {"status": "success", "message": "Промокод деактивирован"}

# --- MARATHON step UPDATE ---

@router.post("/marathon")
async def create_or_update_marathon(
    data: MarathonCreateOrUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/marathon/
    Updates the active marathon challenge, or creates a new one.
    """
    res = await db.execute(select(Marathon).filter(Marathon.is_active == True))
    marathon = res.scalars().first()
    
    if marathon:
        changes: Dict[str, Any] = {}
        # Update active marathon details
        if data.title is not None:
            changes["title"] = {"from": marathon.title, "to": data.title}
            marathon.title = data.title
        if data.target_multiplier is not None:
            changes["target_multiplier"] = {"from": marathon.target_multiplier, "to": data.target_multiplier}
            marathon.target_multiplier = data.target_multiplier
        if data.current_step is not None:
            changes["current_step"] = {"from": marathon.current_step, "to": data.current_step}
            marathon.current_step = data.current_step
        if data.total_steps is not None:
            changes["total_steps"] = {"from": marathon.total_steps, "to": data.total_steps}
            marathon.total_steps = data.total_steps
        if data.is_active is not None:
            changes["is_active"] = {"from": marathon.is_active, "to": data.is_active}
            marathon.is_active = data.is_active
        if changes:
            add_admin_audit_log(
                db,
                actor=admin,
                action="marathon_updated",
                details={"marathon_id": marathon.id, "changes": changes},
            )
    else:
        # Create a new default active marathon
        marathon = Marathon(
            title=data.title or "Марафон: Путь к х10",
            target_multiplier=data.target_multiplier or 10.0,
            current_step=data.current_step or 1,
            total_steps=data.total_steps or 10,
            is_active=True if data.is_active is None else data.is_active
        )
        db.add(marathon)
        add_admin_audit_log(
            db,
            actor=admin,
            action="marathon_created",
            details=data.model_dump(exclude_unset=True),
        )
        
    await db.commit()
    await db.refresh(marathon)
    return marathon

# --- CRM DIRECTORY & ACCESS OVERRIDES ---

@router.get("/users", response_model=List[AdminUserListResponse])
async def admin_list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/users
    Retrieves CRM profiles for all subscribers in the system.
    """
    result = await db.execute(
        select(User)
        .filter(User.role == "user")
        .options(selectinload(User.bookmakers), selectinload(User.badges))
        .order_by(User.created_at.desc())
    )
    users = result.scalars().all()
    user_ids = [user.telegram_id for user in users]
    recent_results_by_user: Dict[int, List[Dict[str, Any]]] = {user_id: [] for user_id in user_ids}

    if user_ids:
        recent_result = await db.execute(
            select(
                user_bets.c.user_id,
                user_bets.c.bet_id,
                user_bets.c.taken_at,
                Bet.status,
            )
            .join(Bet, Bet.id == user_bets.c.bet_id)
            .filter(user_bets.c.user_id.in_(user_ids), Bet.status.in_(["win", "loss"]))
            .order_by(user_bets.c.taken_at.desc())
        )

        for user_id, bet_id, taken_at, bet_status in recent_result.all():
            user_results = recent_results_by_user.setdefault(user_id, [])
            if len(user_results) < 10:
                user_results.append({
                    "bet_id": bet_id,
                    "status": bet_status,
                    "taken_at": taken_at,
                })

    return [
        build_admin_user_response(user, recent_results_by_user.get(user.telegram_id, []))
        for user in users
    ]


@router.get("/admins", response_model=List[AdminUserListResponse])
async def admin_list_admins(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/admins
    Lists staff accounts with owner/admin/moderator roles.
    """
    result = await db.execute(
        select(User)
        .filter(User.role.in_(list(STAFF_ROLES)))
        .options(selectinload(User.bookmakers), selectinload(User.badges))
        .order_by(User.role.desc(), User.created_at.desc())
    )
    users = result.scalars().all()
    return [build_admin_user_response(user) for user in users]


@router.get("/audit-log", response_model=List[AdminAuditLogResponse])
async def admin_audit_log(
    limit: int = Query(50, ge=1, le=200),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/admin/audit-log
    Shows recent administrative actions.
    """
    result = await db.execute(
        select(AdminAuditLog)
        .options(selectinload(AdminAuditLog.actor), selectinload(AdminAuditLog.target_user))
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
    )
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "actor_id": log.actor_id,
            "actor_username": log.actor.username if log.actor else None,
            "actor_first_name": log.actor.first_name if log.actor else None,
            "target_user_id": log.target_user_id,
            "target_username": log.target_user.username if log.target_user else None,
            "target_first_name": log.target_user.first_name if log.target_user else None,
            "action": log.action,
            "details": log.details or {},
            "created_at": log.created_at,
        }
        for log in logs
    ]

@router.put("/users/{user_id}", response_model=UserResponse)
async def admin_update_user(
    user_id: int,
    data: AdminUpdateUserPreferences,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    PUT /api/admin/users/{user_id}
    Updates subscriber stats displays, books filters, and prolongs or revokes subscriptions.
    """
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == user_id)
        .options(selectinload(User.bookmakers))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )

    audit_changes: Dict[str, Any] = {}

    if data.role is not None:
        next_role = normalize_role(data.role)
        previous_role = user.role or "user"
        await ensure_role_change_allowed(db=db, actor=admin, target=user, next_role=next_role)
        if previous_role != next_role:
            user.role = next_role
            add_admin_audit_log(
                db,
                actor=admin,
                action="role_changed",
                target_user_id=user.telegram_id,
                details={
                    "from": previous_role,
                    "to": next_role,
                    "from_label": ROLE_LABELS.get(previous_role, previous_role),
                    "to_label": ROLE_LABELS.get(next_role, next_role),
                },
            )
        
    if data.stats_display_mode is not None:
        if data.stats_display_mode not in ["percent", "flat"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Поле stats_display_mode должно иметь значение 'percent' или 'flat'"
            )
        if user.stats_display_mode != data.stats_display_mode:
            audit_changes["stats_display_mode"] = {
                "from": user.stats_display_mode,
                "to": data.stats_display_mode,
            }
            user.stats_display_mode = data.stats_display_mode
        
    if data.bookmaker_ids is not None:
        bk_result = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(data.bookmaker_ids)))
        previous_bookmaker_ids = [bookmaker.id for bookmaker in user.bookmakers]
        selected_bookmakers = bk_result.scalars().all()
        user.bookmakers = selected_bookmakers
        next_bookmaker_ids = [bookmaker.id for bookmaker in selected_bookmakers]
        if previous_bookmaker_ids != next_bookmaker_ids:
            audit_changes["bookmaker_ids"] = {
                "from": previous_bookmaker_ids,
                "to": next_bookmaker_ids,
            }

        has_other_bookmaker = any(bookmaker.code == "other" for bookmaker in selected_bookmakers)
        if not has_other_bookmaker and user.other_bookmaker_name:
            audit_changes["other_bookmaker_name"] = {
                "from": user.other_bookmaker_name,
                "to": None,
            }
            user.other_bookmaker_name = None

    if "other_bookmaker_name" in data.model_fields_set:
        has_other_bookmaker = any(bookmaker.code == "other" for bookmaker in user.bookmakers)
        next_other_bookmaker_name = (
            data.other_bookmaker_name.strip()
            if has_other_bookmaker and data.other_bookmaker_name
            else None
        )
        if user.other_bookmaker_name != next_other_bookmaker_name:
            audit_changes["other_bookmaker_name"] = {
                "from": user.other_bookmaker_name,
                "to": next_other_bookmaker_name,
            }
            user.other_bookmaker_name = next_other_bookmaker_name

    if "client_group" in data.model_fields_set:
        next_client_group = data.client_group.strip() if data.client_group else None
        if user.client_group != next_client_group:
            audit_changes["client_group"] = {
                "from": user.client_group,
                "to": next_client_group,
            }
            user.client_group = next_client_group

    if "client_tag" in data.model_fields_set:
        next_client_tag = data.client_tag.strip() if data.client_tag else None
        if user.client_tag != next_client_tag:
            audit_changes["client_tag"] = {
                "from": user.client_tag,
                "to": next_client_tag,
            }
            user.client_tag = next_client_tag
        
    if data.matches_delta is not None and data.matches_delta != 0:
        previous_matches = int(
            user.purchased_bets_balance
            if (user.purchased_bets_balance or 0) != 0
            else (user.matches_remaining or 0)
        )
        next_matches = max(0, previous_matches + data.matches_delta)
        user.purchased_bets_balance = next_matches
        user.matches_remaining = next_matches
        audit_changes["matches_remaining"] = {
            "from": previous_matches,
            "to": next_matches,
            "delta": data.matches_delta,
        }
        db.add(log_match_balance_event(
            user_id=user.telegram_id,
            event_type="admin_match_adjustment",
            delta_matches=data.matches_delta,
            note=f"Admin {admin.telegram_id} adjusted matches by {data.matches_delta}",
        ))

    if data.close_guarantee:
        audit_changes["guarantee_active"] = {
            "from": user.guarantee_active,
            "to": False,
        }
        user.guarantee_active = False
        user.guarantee_closed_at = datetime.now(timezone.utc)

    if data.subscription_end_date is not None:
        audit_changes["subscription_end_date"] = data.subscription_end_date.isoformat()
        now = datetime.now(timezone.utc)
        # Fetch active sub
        active_sub_res = await db.execute(
            select(Subscription)
            .filter(
                Subscription.user_id == user.telegram_id,
                Subscription.status == "active",
                Subscription.end_date > now
            )
            .order_by(Subscription.end_date.desc())
        )
        active_sub = active_sub_res.scalars().first()
        
        # If the date is past/now, revoke active subscriptions
        if data.subscription_end_date <= now:
            if active_sub:
                active_sub.status = "expired"
                active_sub.end_date = now
        else:
            # Update end date of current active sub, or create one if none exist
            if active_sub:
                active_sub.end_date = data.subscription_end_date
            else:
                new_sub = Subscription(
                    user_id=user.telegram_id,
                    plan_id=1,  # Link to standard bronze plan as base link template
                    status="active",
                    start_date=now,
                    end_date=data.subscription_end_date,
                    payment_provider="admin_override",
                    payment_id=f"admin_assign_{admin.telegram_id}_{int(now.timestamp())}"
                )
                db.add(new_sub)

    if audit_changes:
        add_admin_audit_log(
            db,
            actor=admin,
            action="user_updated",
            target_user_id=user.telegram_id,
            details=audit_changes,
        )
                
    telegram_id = user.telegram_id
    await db.commit()
    return await load_user_response(db, telegram_id)


@router.delete("/users/{user_id}", status_code=status.HTTP_200_OK)
async def admin_delete_user(
    user_id: int,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    DELETE /api/admin/users/{user_id}
    Permanently removes a CRM client and their linked client-side records.
    """
    if user_id == admin.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя удалить собственный аккаунт"
        )

    result = await db.execute(select(User).filter(User.telegram_id == user_id))
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Пользователь не найден"
        )

    if normalize_role(user.role) != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Можно удалять только клиентов. Сначала снимите роль сотрудника в доступах"
        )

    deleted_user_details = {
        "deleted_user_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_web_only": user.is_web_only,
        "client_group": user.client_group,
        "client_tag": user.client_tag,
    }

    await db.execute(delete(user_bookmakers).where(user_bookmakers.c.user_id == user.telegram_id))
    await db.execute(delete(user_bets).where(user_bets.c.user_id == user.telegram_id))
    await db.execute(delete(MatchBalanceLog).where(MatchBalanceLog.user_id == user.telegram_id))
    await db.execute(delete(Subscription).where(Subscription.user_id == user.telegram_id))
    await db.execute(delete(PaymentAttempt).where(PaymentAttempt.user_id == user.telegram_id))
    await db.execute(delete(ForecastRequest).where(ForecastRequest.user_id == user.telegram_id))
    await db.execute(delete(CrowdBetParticipant).where(CrowdBetParticipant.user_id == user.telegram_id))
    await db.execute(delete(DailyRewardClaim).where(DailyRewardClaim.user_id == user.telegram_id))
    await db.execute(delete(PvPBattleVote).where(PvPBattleVote.user_id == user.telegram_id))
    await db.execute(delete(PromoCode).where(PromoCode.user_id == user.telegram_id))
    await db.execute(delete(UserBadge).where(UserBadge.user_id == user.telegram_id))
    await db.execute(delete(UserNote).where(UserNote.user_id == user.telegram_id))

    await db.execute(update(Bet).where(Bet.author_id == user.telegram_id).values(author_id=None))
    await db.execute(update(User).where(User.referred_by_user_id == user.telegram_id).values(referred_by_user_id=None))
    await db.execute(update(ForecastRequest).where(ForecastRequest.handled_by == user.telegram_id).values(handled_by=None))
    await db.execute(update(AdminAuditLog).where(AdminAuditLog.actor_id == user.telegram_id).values(actor_id=None))
    await db.execute(update(AdminAuditLog).where(AdminAuditLog.target_user_id == user.telegram_id).values(target_user_id=None))

    add_admin_audit_log(
        db,
        actor=admin,
        action="user_deleted",
        details=deleted_user_details,
    )
    await db.delete(user)
    await db.commit()
    return {"status": "success", "deleted_user_id": user_id}


@router.post("/admins/grant", response_model=UserResponse)
async def grant_admin_role(
    data: AdminGrantRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: promote an existing user or pre-create a staff account by Telegram ID."""
    if data.telegram_id <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Telegram ID должен быть положительным числом"
        )
    requested_role = normalize_role(data.role)
    if requested_role == "user" or requested_role not in VALID_ROLES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Можно назначить только роли owner, admin или moderator"
        )

    result = await db.execute(
        select(User)
        .filter(User.telegram_id == data.telegram_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    user = result.scalars().first()

    if not user:
        user = User(
            telegram_id=data.telegram_id,
            username=data.username,
            first_name=data.first_name,
            last_name=data.last_name,
            role="user",
            stats_display_mode="percent",
            tg_chat_joined=False,
        )
        db.add(user)
        await db.flush()

    previous_role = user.role or "user"
    await ensure_role_change_allowed(db=db, actor=admin, target=user, next_role=requested_role)
    user.role = requested_role
    if user.telegram_id != admin.telegram_id and previous_role != requested_role:
        add_admin_audit_log(
            db,
            actor=admin,
            action="staff_granted",
            target_user_id=user.telegram_id,
            details={
                "from": previous_role,
                "to": requested_role,
                "to_label": ROLE_LABELS.get(requested_role, requested_role),
            },
        )

    if user:
        if data.username:
            user.username = data.username
        if data.first_name:
            user.first_name = data.first_name
        if data.last_name:
            user.last_name = data.last_name

    telegram_id = user.telegram_id
    await db.commit()
    return await load_user_response(db, telegram_id)


from src.models.models import ABTestConfig
from src.schemas.schemas import ABTestConfigCreate, ABTestConfigResponse

@router.post("/chats/generate-link")
async def admin_chats_generate_link(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/chats/generate-link
    Admin-only: Trigger generation of a fresh single-use VIP Telegram invite link.
    """
    from src.core.config import settings
    chat_id = settings.TELEGRAM_VIP_CHAT_ID
    
    from src.api.payments import call_telegram_api_async
    payload = {
        "chat_id": chat_id,
        "member_limit": 1
    }
    res = await call_telegram_api_async("createChatInviteLink", payload)
    
    if res.get("ok"):
        return {"invite_link": res["result"]["invite_link"]}

    if settings.DEBUG_MODE:
        mock_link = f"https://t.me/joinchat/mock_vip_link_admin_preview"
        return {"invite_link": mock_link, "note": "Bypassed using mock link"}

    raise HTTPException(
        status_code=status.HTTP_502_BAD_GATEWAY,
        detail=f"Telegram invite link failed: {res.get('description', 'Unknown error')}",
    )


@router.get("/abtest", response_model=List[ABTestConfigResponse])
async def list_ab_configs(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """GET /api/admin/abtest — Retrieves all defined A/B split configs."""
    res = await db.execute(select(ABTestConfig))
    return res.scalars().all()


@router.post("/abtest", response_model=ABTestConfigResponse)
async def create_or_update_ab_config(
    data: ABTestConfigCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/abtest
    Creates or toggles split-test parameters for a plan.
    """
    # Deactivate other configurations for this plan
    await db.execute(
        ABTestConfig.__table__.update()
        .where(ABTestConfig.plan_id == data.plan_id)
        .values(is_active=False)
    )
    
    config = ABTestConfig(
        plan_id=data.plan_id,
        price_group_a=data.price_group_a,
        price_group_b=data.price_group_b,
        is_active=data.is_active
    )
    db.add(config)
    add_admin_audit_log(
        db,
        actor=admin,
        action="abtest_config_created",
        details=data.model_dump(),
    )
    await db.commit()
    await db.refresh(config)
    return config
