import base64
import csv
import json
import re
from io import BytesIO, StringIO
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import String, case, cast, delete, func, or_, update
from typing import List, Dict, Any, Optional
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from pydantic import BaseModel, Field

from src.models.database import get_db, get_read_db
from src.api.auth import _merge_web_only_user_into_telegram
from src.models.models import (
    AdminAuditLog,
    Bet,
    Bookmaker,
    CrowdBetParticipant,
    DailyRewardClaim,
    ForecastRequest,
    FlatSubscription,
    Marathon,
    MarketingRewardEvent,
    MatchBalanceLog,
    PaymentAttempt,
    PromoCode,
    PromoCodeRedemption,
    PvPBattleVote,
    ReferralRewardEvent,
    Subscription,
    SubscriptionPlan,
    User,
    UserBadge,
    UserNote,
    user_bets,
    user_bookmakers,
    Quiz,
    PvPBattle,
    CrowdBet,
)
from src.schemas.schemas import (
    AdminAuditLogResponse,
    AdminGrantRequest,
    AdminUpdateUserPreferences,
    AdminUserListResponse,
    BetResponse,
    IntegrationDiagnosticsRequest,
    IntegrationSettingsUnlockRequest,
    IntegrationSettingsUnlockResponse,
    MessageTemplateResponse,
    MessageTemplateUpdate,
    MonitoringLogsResponse,
    OnlineUsersResponse,
    ParserStatusResponse,
    PaymentReconciliationReport,
    ResetSessionsResponse,
    SystemSettingUpdate,
    SystemSettingsResponse,
    UserResponse,
    PromoCodeResponse,
    WheelConfigPayload,
    QuizCreate,
    QuizResponse,
    PvPBattleCreate,
    PvPBattleResponse,
    CrowdBetCreate,
    CrowdBetResponse,
    FlatStakeCorrectionRequest,
    FlatSubscriptionConfigureRequest,
    FlatSubscriptionCreditRequest,
    FlatSubscriptionResponse,
)
from src.api.deps import get_current_admin, get_current_admin_read, get_current_owner, get_current_privileged_admin
from src.core.config import settings
from src.core.roles import ADMIN_ROLES, ROLE_LABELS, STAFF_ROLES, VALID_ROLES, is_admin_role, is_owner_role, normalize_role
from src.core.message_templates import (
    list_message_templates,
    reset_message_template,
    upsert_message_template,
)
from src.core.security_limits import get_security_rate_limit_metrics
from src.services.forecast_delivery import FORECAST_STATUS_REMOVED
from src.services.delivery_outbox import get_delivery_outbox_metrics
from src.services.observability_alerts import build_observability_alert_payload
from src.services import subscription_notifications
from src.services.match_access import (
    current_match_balance,
    lock_user_balance,
    lock_user_balances,
    log_match_balance_event,
    revoke_user_bet_access,
)
from src.services.flat_subscriptions import (
    activate_pending_flat_subscription_if_eligible,
    configure_flat_subscription,
    correct_flat_bet_stake,
    credit_flat_subscription,
    flat_subscription_payload,
    get_latest_flat_subscription,
    get_open_flat_subscription,
    lock_flat_subscription_financial_rows,
    preview_flat_amount_change,
    preview_flat_bet_stake,
    refresh_flat_subscriptions_after_terminal_requests,
    reopen_flat_subscription,
)
from src.services.payment_reconciliation import build_payment_reconciliation_report, build_payment_reconciliation_summary
from src.services.statistics import (
    build_performance_payload,
    client_situation,
    filter_items_by_period,
    is_paid_client_access,
    last_result_codes,
    normalize_period,
    period_end,
    period_start,
    stat_item_from_bet,
    summarize_items,
)
from src.services.google_drive_export import get_drive_export_job, start_crm_drive_export_job, start_drive_export_job
from src.services.historical_stats import load_active_historical_stats_snapshot, merge_historical_performance_payload
from src.services.system_settings import (
    create_integration_unlock_token,
    get_admin_system_settings,
    get_referral_program_settings,
    get_unlocked_integration_settings,
    run_integration_diagnostics,
    reset_user_session_cache,
    update_admin_system_settings,
    verify_integrations_password,
)
from src.services.marketing_risk import RISK_STATUS_APPROVED, RISK_STATUS_HELD, RISK_STATUS_REJECTED
from src.services.marketing_widgets import list_effective_widget_configs, stored_widget_config_count, upsert_widget_configs
from src.services.referrals import approve_referral_reward_event, reject_referral_reward_event
from src.services.presence import count_online_users
from src.services.stats_export import (
    ClientInfoExportRow,
    ClientRecentBetExportRow,
    build_client_info_export_workbook,
    build_stats_export_workbook,
    filter_client_info_export_rows,
    filter_client_recent_export_rows,
    load_author_export_items,
    load_client_info_export_rows,
    load_client_recent_bet_export_rows,
    load_clients_export_items,
    load_shamrai_export_items,
    stats_export_period_label,
)

router = APIRouter(prefix="/admin", tags=["Admin Operations"])
PUBLICATION_TYPE_FORECAST = "forecast"
STATS_PERIOD_QUERY_PATTERN = r"^(week|month|quarter|all|[1-9]\d{3}-(0[1-9]|1[0-2]))$"


def _encode_admin_user_cursor(user: User) -> str:
    payload = {
        "created_at": user.created_at.isoformat() if user.created_at else "",
        "telegram_id": user.telegram_id,
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_admin_user_cursor(cursor: Optional[str]) -> tuple[datetime, int] | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * ((4 - len(cursor) % 4) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        return datetime.fromisoformat(str(payload["created_at"])), int(payload["telegram_id"])
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный cursor клиентов",
        )

class PromoCreate(BaseModel):
    code: str = Field(min_length=1, max_length=80)
    reward_type: str = Field(default="discount", pattern="^(discount|flats)$")
    discount_percent: Optional[int] = Field(default=None, ge=0, le=100)
    matches_count: Optional[int] = Field(default=None, ge=0, le=1000)
    target_flats: Optional[Decimal] = Field(default=None, ge=Decimal("0.01"), le=Decimal("10000.00"), decimal_places=2)
    valid_until: datetime


class MarketingWidgetConfigUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    is_enabled: bool = True
    position: int = Field(default=0, ge=0, le=1000)
    audience: str = Field(default="all", pattern="^(all|staff|clients|referrals)$")
    starts_at: Optional[datetime] = None
    ends_at: Optional[datetime] = None
    cooldown_hours: int = Field(default=0, ge=0, le=24 * 365)
    per_user_limit: int = Field(default=0, ge=0, le=100000)
    global_daily_limit: int = Field(default=0, ge=0, le=100000)
    reward_type: str = Field(default="none", pattern="^(none|mixed|discount|matches|free_bet)$")
    reward_value: int = Field(default=0, ge=0, le=100000)
    promo_valid_hours: int = Field(default=0, ge=0, le=24 * 365)
    settings_json: Dict[str, Any] = Field(default_factory=dict)


class MarketingWidgetConfigUpdatePayload(BaseModel):
    configs: List[MarketingWidgetConfigUpdate]


class MarathonCreateOrUpdate(BaseModel):
    title: Optional[str] = None
    target_multiplier: Optional[float] = None
    current_step: Optional[int] = None
    total_steps: Optional[int] = None
    is_active: Optional[bool] = None


class AdminStatsDriveExportRequest(BaseModel):
    scope: str = "all"
    period: str = "all"
    formats: List[str] = Field(default_factory=lambda: ["google_sheet"])


class AdminCrmDriveExportRequest(BaseModel):
    q: Optional[str] = None
    activity: str = "all"
    group: Optional[str] = None
    tag: Optional[str] = None
    bookmaker_id: Optional[int] = Field(default=None, ge=1)
    formats: List[str] = Field(default_factory=lambda: ["google_sheet"])


class AdminUserMergeRequest(BaseModel):
    target_user_id: int = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=400)


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
    web_push_subscription = getattr(user, "web_push_subscription", None)
    web_push_enabled = isinstance(web_push_subscription, dict) and bool(web_push_subscription.get("endpoint"))
    telegram_connected = user.telegram_id > 0
    vk_connected = bool(user.vk_user_id)
    loaded_flat_subscriptions = user.__dict__.get("flat_subscriptions", ())
    flat_subscription = max(
        loaded_flat_subscriptions,
        key=lambda item: (item.created_at or datetime.min.replace(tzinfo=timezone.utc), str(item.id)),
        default=None,
    )
    flat_summary = None
    if flat_subscription is not None:
        profit_flats = Decimal(str(flat_subscription.profit_flats or 0))
        target_flats = Decimal(str(flat_subscription.target_flats or 0))
        flat_summary = {
            "id": flat_subscription.id,
            "user_id": flat_subscription.user_id,
            "status": flat_subscription.status,
            "flat_amount_rub": flat_subscription.flat_amount_rub,
            "target_flats": target_flats,
            "profit_rub": flat_subscription.profit_rub,
            "profit_flats": profit_flats,
            "remaining_flats": max(Decimal("0"), target_flats - profit_flats),
            "pending_bets": 0,
            "activated_at": flat_subscription.activated_at,
            "completed_at": flat_subscription.completed_at,
        }
    has_open_flat_subscription = bool(
        flat_subscription and flat_subscription.status in {"pending_setup", "active", "closing"}
    )
    return {
        "telegram_id": user.telegram_id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "photo_url": user.photo_url,
        "vk_photo_url": user.vk_photo_url,
        "is_web_only": user.is_web_only,
        "identity_providers": user.identity_providers,
        "missing_identity_providers": user.missing_identity_providers,
        "telegram_connected": telegram_connected,
        "telegram_delivery_enabled": telegram_connected and bool(user.tg_chat_joined),
        "vk_user_id": user.vk_user_id,
        "vk_group_member": bool(user.vk_group_member),
        "vk_messages_allowed": bool(user.vk_messages_allowed),
        "vk_notifications_allowed": bool(user.vk_notifications_allowed),
        "vk_connected": vk_connected,
        "vk_delivery_enabled": vk_connected and bool(user.vk_messages_allowed),
        "web_push_enabled": web_push_enabled,
        "role": user.role,
        "stats_display_mode": user.stats_display_mode,
        "has_active_subscription": match_balance > 0 or user.guarantee_active or has_open_flat_subscription,
        "subscription_end_date": None,
        "purchased_bets_balance": match_balance,
        "matches_remaining": match_balance,
        "guarantee_active": user.guarantee_active,
        "guarantee_opened_from_bet_id": user.guarantee_opened_from_bet_id,
        "guarantee_closed_at": user.guarantee_closed_at,
        "flat_subscription": flat_summary,
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


def ensure_privileged_admin(actor: User) -> None:
    if not is_admin_role(actor.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Admin permissions required",
        )


# --- ADMIN STATISTICS / STATS ---

@router.get("/dashboard/stats")
async def get_admin_dashboard_stats(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
):
    """
    GET /api/admin/dashboard/stats
    Returns aggregate stats metrics: users with paid balance, ROI, Winrate, and Total Users.
    """
    # 1. Total users
    res_users = await db.execute(select(func.count(User.telegram_id)))
    total_users = res_users.scalar() or 0

    # 2. Active subscribers (legacy access or an open flat programme)
    now = datetime.now(timezone.utc)
    open_flat_exists = select(FlatSubscription.id).where(
        FlatSubscription.user_id == User.telegram_id,
        FlatSubscription.status.in_(["pending_setup", "active", "closing"]),
    ).exists()
    res_active = await db.execute(
        select(func.count(User.telegram_id)).filter(
            (User.purchased_bets_balance > 0)
            | (User.matches_remaining > 0)
            | (User.guarantee_active == True)
            | open_flat_exists
        )
    )
    active_subscribers = res_active.scalar() or 0

    # 3. Bet performance calculations for the channel and the current author.
    profit_expr = case(
        (Bet.status == "win", Bet.coefficient - Decimal("1.00")),
        (Bet.status == "loss", Decimal("-1.00")),
        else_=Decimal("0.00"),
    )
    won_expr = case((Bet.status == "win", 1), else_=0)
    lost_expr = case((Bet.status == "loss", 1), else_=0)
    resolved_expr = case((Bet.status.in_(["win", "loss"]), 1), else_=0)
    resolved_coefficient_expr = case((Bet.status.in_(["win", "loss"]), Bet.coefficient), else_=None)

    async def summarize_bets(author_id: Optional[int] = None) -> Dict[str, float]:
        filters = [Bet.publication_type == PUBLICATION_TYPE_FORECAST]
        if author_id is not None:
            filters.append(Bet.author_id == author_id)
        result = await db.execute(
            select(
                func.count(Bet.id),
                func.coalesce(func.sum(won_expr), 0),
                func.coalesce(func.sum(lost_expr), 0),
                func.coalesce(func.sum(resolved_expr), 0),
                func.coalesce(func.sum(profit_expr), Decimal("0.00")),
                func.coalesce(func.avg(resolved_coefficient_expr), Decimal("0.00")),
            )
            .filter(*filters)
        )
        total_bets, won, lost, resolved_total, profit, average_coefficient = result.one()
        total_bets = int(total_bets or 0)
        won = int(won or 0)
        lost = int(lost or 0)
        resolved_total = int(resolved_total or 0)
        profit = Decimal(str(profit or "0.00"))
        resolved = won + lost

        return {
            "total_bets": total_bets,
            "winrate": round((won / resolved * 100) if resolved else 0.0, 2),
            "roi": round((float(profit) / resolved_total * 100) if resolved_total else 0.0, 2),
            "average_coefficient": round(float(average_coefficient or 0), 2),
        }

    channel_performance = await summarize_bets()
    author_performance = await summarize_bets(admin.telegram_id)

    # 4. A/B testing split conversion metrics
    res_a_total = await db.execute(select(func.count(User.telegram_id)).filter(User.ab_group == 'A'))
    total_a = res_a_total.scalar() or 0

    res_b_total = await db.execute(select(func.count(User.telegram_id)).filter(User.ab_group == 'B'))
    total_b = res_b_total.scalar() or 0

    res_a_active = await db.execute(
        select(func.count(User.telegram_id))
        .filter(
            User.ab_group == 'A',
            (User.purchased_bets_balance > 0)
            | (User.matches_remaining > 0)
            | (User.guarantee_active == True)
            | open_flat_exists
        )
    )
    active_a = res_a_active.scalar() or 0

    res_b_active = await db.execute(
        select(func.count(User.telegram_id))
        .filter(
            User.ab_group == 'B',
            (User.purchased_bets_balance > 0)
            | (User.matches_remaining > 0)
            | (User.guarantee_active == True)
            | open_flat_exists
        )
    )
    active_b = res_b_active.scalar() or 0

    conv_a = (active_a / total_a * 100) if total_a > 0 else 0.0
    conv_b = (active_b / total_b * 100) if total_b > 0 else 0.0

    return {
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
        "ab_test_metrics": {
            "group_a_users": total_a,
            "group_b_users": total_b,
            "group_a_active_subs": active_a,
            "group_b_active_subs": active_b,
            "group_a_conversion": round(conv_a, 2),
            "group_b_conversion": round(conv_b, 2),
        }
    }


@router.get("/delivery-outbox/metrics")
async def admin_delivery_outbox_metrics(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Lightweight queue metrics for background delivery health checks."""
    return await get_delivery_outbox_metrics(db)


@router.get("/security/rate-limit/metrics")
async def admin_security_rate_limit_metrics(
    admin: User = Depends(get_current_admin_read),
) -> dict[str, Any]:
    """In-memory security limiter counters for the current backend process."""
    return get_security_rate_limit_metrics()


@router.get("/monitoring/alerts")
async def admin_monitoring_alerts(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Current safe operational alerts for the admin operations center."""
    delivery_metrics = await get_delivery_outbox_metrics(db)
    return build_observability_alert_payload(delivery_metrics=delivery_metrics)


@router.post("/payment-reconciliation/run", response_model=PaymentReconciliationReport)
async def admin_payment_reconciliation_run(
    window_hours: int = Query(48, ge=1, le=336),
    include_provider_checks: bool = Query(False),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> PaymentReconciliationReport:
    """Read-only payment/access reconciliation report. Never applies fixes."""
    return await build_payment_reconciliation_report(
        db,
        window_hours=window_hours,
        include_provider_checks=include_provider_checks,
        limit=limit,
    )


@router.get("/settings", response_model=SystemSettingsResponse)
async def admin_list_system_settings(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    """Return cached admin system settings without exposing stored secret values."""
    return await get_admin_system_settings(db)


@router.put("/settings", response_model=SystemSettingsResponse)
async def admin_update_system_settings(
    payload: List[SystemSettingUpdate],
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """Bulk update admin system settings and invalidate the Redis response cache."""
    try:
        response = await update_admin_system_settings(
            db,
            [item.model_dump() for item in payload],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    add_admin_audit_log(
        db,
        actor=admin,
        action="system_settings_updated",
        details={"updated_keys": [item.key for item in payload]},
    )
    return response


@router.post("/settings/integrations/unlock", response_model=IntegrationSettingsUnlockResponse)
async def admin_unlock_integration_settings(
    payload: IntegrationSettingsUnlockRequest,
    admin: User = Depends(get_current_owner),
    db: AsyncSession = Depends(get_db),
):
    """Reveal runtime integration secrets only to the owner after an extra password check."""
    if not verify_integrations_password(payload.password):
        add_admin_audit_log(
            db,
            actor=admin,
            action="integration_settings_unlock_failed",
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Неверный пароль интеграций")

    add_admin_audit_log(
        db,
        actor=admin,
        action="integration_settings_unlocked",
    )
    token_payload = await create_integration_unlock_token(admin_id=admin.telegram_id)
    unlocked_settings = await get_unlocked_integration_settings(
        db,
        unlock_token=token_payload["unlock_token"],
        admin_id=admin.telegram_id,
    )
    return {
        **unlocked_settings,
        "unlock_token": token_payload["unlock_token"],
        "expires_at": token_payload["expires_at"],
    }


@router.post("/settings/integrations/diagnostics")
async def admin_integration_diagnostics(
    payload: IntegrationDiagnosticsRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_read_db),
):
    """Read-only integration diagnostics. Never returns secret values."""
    try:
        response = await run_integration_diagnostics(
            db,
            unlock_token=payload.unlock_token,
            admin_id=admin.telegram_id,
            group=payload.group,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    return response


@router.post("/settings/reset-sessions", response_model=ResetSessionsResponse)
async def admin_reset_user_sessions(
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """Clear temporary auth session keys from Redis."""
    response = await reset_user_session_cache()
    add_admin_audit_log(
        db,
        actor=admin,
        action="user_sessions_reset",
        details={"deleted": response.get("deleted", 0)},
    )
    return response


@router.get("/monitoring/online", response_model=OnlineUsersResponse)
async def admin_monitoring_online(
    admin: User = Depends(get_current_admin_read),
):
    """Current online counter from Redis presence heartbeats."""
    return {"online_users": await count_online_users()}


def _redact_sensitive_text(value: str) -> str:
    redacted = str(value or "")
    redacted = re.sub(r"(?i)(token|secret|password|authorization|api[_-]?key)=([^\s,;]+)", r"\1=[redacted]", redacted)
    redacted = re.sub(r"(?i)(postgres(?:ql)?|mysql|redis)://[^\s]+", r"\1://[redacted]", redacted)
    redacted = re.sub(r"\b\d{6,}:[A-Za-z0-9_-]{12,}\b", "[telegram-token-redacted]", redacted)
    redacted = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{12,}", "Bearer [redacted]", redacted)
    return redacted


def _monitoring_log_candidates() -> list[Path]:
    return [
        Path(str(getattr(settings, "ADMIN_MONITORING_LOG_PATH", "") or "")),
        Path("logs/backend.log"),
        Path("/opt/shamrai-mini-app/logs/backend.log"),
        Path("/var/log/shamrai/backend.log"),
    ]


def _tail_log_lines(path: Path, limit: int = 50, max_bytes: int = 64 * 1024) -> list[str]:
    try:
        if not path or not path.is_file():
            return []
        clean_limit = max(0, int(limit))
        if clean_limit == 0:
            return []
        clean_max_bytes = max(1024, int(max_bytes))
        size = path.stat().st_size
        start = max(0, size - clean_max_bytes)
        with path.open("rb") as file:
            file.seek(start)
            raw_chunk = file.read(clean_max_bytes)
        lines = raw_chunk.decode("utf-8", errors="replace").splitlines()
        if start > 0 and lines:
            lines = lines[1:]
        return [_redact_sensitive_text(line) for line in lines[-clean_limit:]]
    except Exception:
        return []


async def _recent_audit_summary(db: AsyncSession, limit: int = 5) -> list[dict[str, Any]]:
    result = await db.execute(
        select(AdminAuditLog)
        .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": log.id,
            "actor_id": log.actor_id,
            "action": log.action,
            "created_at": log.created_at.isoformat() if hasattr(log.created_at, "isoformat") else log.created_at,
        }
        for log in result.scalars().all()
    ]


@router.get("/monitoring/summary")
async def admin_monitoring_summary(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Aggregated admin operations center payload with no secret-bearing data."""
    database_status = "ok"
    database_error = None
    try:
        await db.execute(select(1))
    except Exception as exc:
        database_status = "error"
        database_error = type(exc).__name__

    parser_status = await admin_monitoring_parser_status(admin=admin)
    delivery_metrics = await get_delivery_outbox_metrics(db)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "online": {"online_users": await count_online_users()},
        "health": {
            "api": "ok",
            "database": database_status,
            "database_error": database_error,
        },
        "delivery_outbox": delivery_metrics,
        "payment_reconciliation": (
            await build_payment_reconciliation_summary(db)
        ).model_dump(mode="json"),
        "rate_limit": get_security_rate_limit_metrics(),
        "alerts": build_observability_alert_payload(delivery_metrics=delivery_metrics),
        "parser": {
            "status": parser_status["status"],
            "last_sync": parser_status["last_sync"].isoformat()
            if hasattr(parser_status["last_sync"], "isoformat")
            else parser_status["last_sync"],
        },
        "audit": await _recent_audit_summary(db),
    }


def _admin_user_label(user: User | None, fallback_id: int) -> str:
    if not user:
        return f"ID {fallback_id}"
    name = " ".join([item for item in [user.first_name, user.last_name] if item]).strip()
    return name or (f"@{user.username}" if user.username else f"ID {fallback_id}")


@router.get("/referrals/summary")
async def admin_referrals_summary(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Admin control payload for referral program settings, totals, and recent rewards."""
    referral_settings = await get_referral_program_settings(db)

    invited_count = int((await db.execute(
        select(func.count(User.telegram_id)).filter(User.referred_by_user_id.is_not(None))
    )).scalar() or 0)
    qualified_purchase_events = int((await db.execute(
        select(func.count(ReferralRewardEvent.id)).filter(ReferralRewardEvent.status == RISK_STATUS_APPROVED)
    )).scalar() or 0)
    active_referrers = int((await db.execute(
        select(func.count(func.distinct(ReferralRewardEvent.referrer_user_id))).filter(
            ReferralRewardEvent.status == RISK_STATUS_APPROVED
        )
    )).scalar() or 0)
    matches_awarded_total = int((await db.execute(
        select(func.coalesce(func.sum(ReferralRewardEvent.matches_awarded), 0)).filter(
            ReferralRewardEvent.status == RISK_STATUS_APPROVED
        )
    )).scalar() or 0)
    target_flats_awarded_total = Decimal(str((await db.execute(
        select(func.coalesce(func.sum(ReferralRewardEvent.target_flats_awarded), 0)).filter(
            ReferralRewardEvent.status == RISK_STATUS_APPROVED
        )
    )).scalar() or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    held_events = int((await db.execute(
        select(func.count(ReferralRewardEvent.id)).filter(ReferralRewardEvent.status == RISK_STATUS_HELD)
    )).scalar() or 0)
    rejected_events = int((await db.execute(
        select(func.count(ReferralRewardEvent.id)).filter(ReferralRewardEvent.status == RISK_STATUS_REJECTED)
    )).scalar() or 0)

    recent_result = await db.execute(
        select(ReferralRewardEvent)
        .options(selectinload(ReferralRewardEvent.referrer), selectinload(ReferralRewardEvent.referred_user))
        .order_by(ReferralRewardEvent.created_at.desc(), ReferralRewardEvent.id.desc())
        .limit(10)
    )

    recent_events = []
    for event in recent_result.scalars().all():
        recent_events.append({
            "id": event.id,
            "referrer_user_id": event.referrer_user_id,
            "referrer_label": _admin_user_label(event.referrer, event.referrer_user_id),
            "referred_user_id": event.referred_user_id,
            "referred_label": _admin_user_label(event.referred_user, event.referred_user_id),
            "source_payment_attempt_id": str(event.source_payment_attempt_id) if event.source_payment_attempt_id else None,
            "source_type": event.source_type,
            "discount_percent_snapshot": event.discount_percent_snapshot,
            "matches_awarded": event.matches_awarded,
            "target_flats_awarded": Decimal(str(event.target_flats_awarded or 0)).quantize(
                Decimal("0.01"),
                rounding=ROUND_HALF_UP,
            ),
            "status": event.status,
            "risk_score": event.risk_score,
            "risk_reasons": event.risk_reasons or [],
            "created_at": event.created_at.isoformat() if hasattr(event.created_at, "isoformat") else event.created_at,
        })

    return {
        **referral_settings,
        "invited_count": invited_count,
        "qualified_purchase_events": qualified_purchase_events,
        "active_referrers": active_referrers,
        "matches_awarded_total": matches_awarded_total,
        "target_flats_awarded_total": target_flats_awarded_total,
        "held_events": held_events,
        "rejected_events": rejected_events,
        "recent_events": recent_events,
    }


@router.get("/marketing/widgets")
async def admin_marketing_widgets(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    return {
        "configured": (await stored_widget_config_count(db)) > 0,
        "widgets": await list_effective_widget_configs(db),
    }


@router.patch("/marketing/widgets")
async def admin_update_marketing_widgets(
    payload: MarketingWidgetConfigUpdatePayload,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    widgets = await upsert_widget_configs(
        db,
        configs=[item.model_dump() for item in payload.configs],
        updated_by=admin.telegram_id,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="marketing_widgets_updated",
        details={"keys": [item.key for item in payload.configs]},
    )
    await db.commit()
    return {
        "configured": True,
        "widgets": widgets,
    }
@router.get("/marketing/wheel-config", response_model=WheelConfigPayload)
async def admin_get_wheel_config(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    from src.api.marketing import get_wheel_prizes_from_db
    prizes = await get_wheel_prizes_from_db(db)
    return WheelConfigPayload(prizes=prizes)


@router.post("/marketing/wheel-config", response_model=WheelConfigPayload)
async def admin_update_wheel_config(
    payload: WheelConfigPayload,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    from src.models.models import MarketingWidgetConfig
    import json

    # Check probabilities sum to 100
    total_prob = sum(p.probability for p in payload.prizes)
    if total_prob != 100:
        raise HTTPException(status_code=400, detail=f"Сумма вероятностей должна быть 100% (сейчас {total_prob}%)")

    config = await db.execute(select(MarketingWidgetConfig).filter_by(key="wheel_of_fortune"))
    config = config.scalars().first()

    if not config:
        config = MarketingWidgetConfig(
            key="wheel_of_fortune",
            is_enabled=True,
            settings_json={},
            updated_by=admin.telegram_id
        )
        db.add(config)

    settings = dict(config.settings_json) if config.settings_json else {}
    settings["prizes"] = [p.dict() for p in payload.prizes]
    config.settings_json = settings
    config.updated_by = admin.telegram_id

    add_admin_audit_log(
        db,
        actor=admin,
        action="marketing_wheel_config_updated",
        details={"prizes_count": len(payload.prizes)}
    )

    await db.commit()
    return payload


@router.get("/marketing/reward-events")
async def admin_marketing_reward_events(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    result = await db.execute(
        select(MarketingRewardEvent)
        .options(selectinload(MarketingRewardEvent.user), selectinload(MarketingRewardEvent.promo_code))
        .order_by(MarketingRewardEvent.created_at.desc(), MarketingRewardEvent.id.desc())
        .limit(limit)
    )
    events = []
    for event in result.scalars().all():
        events.append({
            "id": event.id,
            "user_id": event.user_id,
            "user_label": _admin_user_label(event.user, event.user_id),
            "widget_key": event.widget_key,
            "reward_type": event.reward_type,
            "reward_value": event.reward_value,
            "promo_code_id": event.promo_code_id,
            "promo_code": event.promo_code.code if event.promo_code else None,
            "risk_status": event.risk_status,
            "risk_reasons": event.risk_reasons or [],
            "created_at": event.created_at.isoformat() if hasattr(event.created_at, "isoformat") else event.created_at,
        })
    return {"events": events}


def _parse_marketing_risk_event_id(raw_id: str) -> tuple[str, int]:
    if ":" in raw_id:
        kind, event_id = raw_id.split(":", 1)
    else:
        kind, event_id = "referral", raw_id
    if kind not in {"referral", "marketing"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный тип события")
    try:
        return kind, int(event_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный ID события")


@router.get("/marketing/risk-queue")
async def admin_marketing_risk_queue(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    referral_result = await db.execute(
        select(ReferralRewardEvent)
        .options(selectinload(ReferralRewardEvent.referrer), selectinload(ReferralRewardEvent.referred_user))
        .filter(ReferralRewardEvent.status == RISK_STATUS_HELD)
        .order_by(ReferralRewardEvent.created_at.desc(), ReferralRewardEvent.id.desc())
        .limit(50)
    )
    marketing_result = await db.execute(
        select(MarketingRewardEvent)
        .options(selectinload(MarketingRewardEvent.user), selectinload(MarketingRewardEvent.promo_code))
        .filter(MarketingRewardEvent.risk_status == RISK_STATUS_HELD)
        .order_by(MarketingRewardEvent.created_at.desc(), MarketingRewardEvent.id.desc())
        .limit(50)
    )
    items = []
    for event in referral_result.scalars().all():
        items.append({
            "id": f"referral:{event.id}",
            "kind": "referral",
            "title": f"{_admin_user_label(event.referrer, event.referrer_user_id)} / {_admin_user_label(event.referred_user, event.referred_user_id)}",
            "status": event.status,
            "risk_score": event.risk_score,
            "risk_reasons": event.risk_reasons or [],
            "created_at": event.created_at.isoformat() if hasattr(event.created_at, "isoformat") else event.created_at,
            "payload": {
                "referrer_user_id": event.referrer_user_id,
                "referred_user_id": event.referred_user_id,
                "source_type": event.source_type,
            },
        })
    for event in marketing_result.scalars().all():
        items.append({
            "id": f"marketing:{event.id}",
            "kind": "marketing",
            "title": f"{_admin_user_label(event.user, event.user_id)} / {event.widget_key}",
            "status": event.risk_status,
            "risk_score": 0,
            "risk_reasons": event.risk_reasons or [],
            "created_at": event.created_at.isoformat() if hasattr(event.created_at, "isoformat") else event.created_at,
            "payload": {
                "user_id": event.user_id,
                "widget_key": event.widget_key,
                "reward_type": event.reward_type,
                "reward_value": event.reward_value,
            },
        })
    items.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return {"items": items, "held_count": len(items)}


@router.post("/marketing/risk-queue/{event_id}/approve")
async def admin_approve_marketing_risk_event(
    event_id: str,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kind, parsed_id = _parse_marketing_risk_event_id(event_id)
    if kind == "referral":
        event = await db.get(ReferralRewardEvent, parsed_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
        await approve_referral_reward_event(db, event=event, reviewer_user_id=admin.telegram_id)
    else:
        event = await db.get(MarketingRewardEvent, parsed_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
        event.risk_status = RISK_STATUS_APPROVED
    add_admin_audit_log(db, actor=admin, action="marketing_risk_approved", details={"id": event_id})
    await db.commit()
    return {"status": "approved", "id": event_id}


@router.post("/marketing/risk-queue/{event_id}/reject")
async def admin_reject_marketing_risk_event(
    event_id: str,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    kind, parsed_id = _parse_marketing_risk_event_id(event_id)
    if kind == "referral":
        event = await db.get(ReferralRewardEvent, parsed_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
        await reject_referral_reward_event(db, event=event, reviewer_user_id=admin.telegram_id)
    else:
        event = await db.get(MarketingRewardEvent, parsed_id)
        if not event:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Событие не найдено")
        event.risk_status = RISK_STATUS_REJECTED
    add_admin_audit_log(db, actor=admin, action="marketing_risk_rejected", details={"id": event_id})
    await db.commit()
    return {"status": "rejected", "id": event_id}


@router.get("/monitoring/logs", response_model=MonitoringLogsResponse)
async def admin_monitoring_logs(
    admin: User = Depends(get_current_admin_read),
):
    """Recent error log preview with sensitive fragments redacted."""
    for candidate in _monitoring_log_candidates():
        lines = _tail_log_lines(candidate, limit=50)
        if lines:
            return {"logs": lines}

    now = datetime.now(timezone.utc).isoformat()
    return {
        "logs": [
            f"{now} [warning] Parser latency probe: bookmaker feed delayed by 1.4s",
            f"{now} [error] Mock error stream: no persistent error log source configured yet",
            f"{now} [info] Delivery outbox monitor heartbeat completed",
        ]
    }


@router.get("/monitoring/parser-status", response_model=ParserStatusResponse)
async def admin_monitoring_parser_status(
    admin: User = Depends(get_current_admin_read),
):
    """Synthetic parser status until a durable parser state source is connected."""
    return {
        "status": "active",
        "last_sync": datetime.now(timezone.utc),
    }


@router.get("/monitoring/diagnostic-report")
async def admin_monitoring_diagnostic_report(
    format: str = Query("txt", pattern="^(txt|json)$"),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> Response:
    """Download sanitized monitoring diagnostics without secrets or env values."""
    summary = await admin_monitoring_summary(admin=admin, db=db)
    logs = await admin_monitoring_logs(admin=admin)
    report_payload = {
        "summary": summary,
        "logs": logs.get("logs", []),
    }
    if format == "json":
        return Response(
            content=json.dumps(report_payload, ensure_ascii=False, indent=2, default=str),
            media_type="application/json",
            headers={"Content-Disposition": "attachment; filename=shamrai-monitoring-diagnostic.json"},
        )

    lines = [
        "Shamrai Analytics Hub diagnostic report",
        f"generated_at: {summary.get('generated_at')}",
        "",
        "[summary]",
        json.dumps(summary, ensure_ascii=False, default=str),
        "",
        "[logs]",
        *[str(line) for line in logs.get("logs", [])],
    ]
    text_report = _redact_sensitive_text("\n".join(lines))
    return Response(
        content=text_report,
        media_type="text/plain",
        headers={"Content-Disposition": "attachment; filename=shamrai-monitoring-diagnostic.txt"},
    )


@router.get("/message-templates", response_model=List[MessageTemplateResponse])
async def admin_list_message_templates(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    """Admin editor source for client-facing Telegram, VK, and web-chat texts."""
    return await list_message_templates(db)


@router.put("/message-templates/{template_key}", response_model=MessageTemplateResponse)
async def admin_update_message_template(
    template_key: str,
    payload: MessageTemplateUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Save an edited message template."""
    response = await upsert_message_template(
        db,
        template_key=template_key,
        body=payload.body,
        actor_id=admin.telegram_id,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="message_template_updated",
        details={"template_key": template_key},
    )
    return response


@router.post("/message-templates/{template_key}/reset", response_model=MessageTemplateResponse)
async def admin_reset_message_template(
    template_key: str,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset a template body back to the built-in default."""
    response = await reset_message_template(
        db,
        template_key=template_key,
        actor_id=admin.telegram_id,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="message_template_reset",
        details={"template_key": template_key},
    )
    return response


def _admin_user_display(user: User) -> str:
    parts = [part for part in [user.first_name, user.last_name] if part]
    if parts:
        return " ".join(parts)
    if user.username:
        return f"@{user.username}"
    return str(user.telegram_id)


@router.get("/stats/author-timeline")
async def get_admin_author_timeline_stats(
    period: str = Query("all", pattern=STATS_PERIOD_QUERY_PATTERN),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Resolved author bets grouped by result date. Refunds and pending bets are excluded."""
    query = (
        select(Bet)
        .filter(
            Bet.author_id == admin.telegram_id,
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    start = period_start(period)
    end = period_end(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    if end:
        query = query.filter(Bet.resolved_at < end)
    result = await db.execute(query)
    items = [
        item
        for bet in result.scalars().all()
        if (item := stat_item_from_bet(bet)) is not None
    ]
    payload = build_performance_payload(items, include_bets=True, period=period)
    payload["author"] = {
        "telegram_id": admin.telegram_id,
        "name": _admin_user_display(admin),
        "username": admin.username,
    }
    return payload


@router.get("/stats/shamrai-timeline")
async def get_admin_shamrai_timeline_stats(
    period: str = Query("all", pattern=STATS_PERIOD_QUERY_PATTERN),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Resolved channel bets plus the imported all-time Shamrai baseline."""
    normalized_period = normalize_period(period)
    historical = await load_active_historical_stats_snapshot(db, normalized_period)
    items = await _shamrai_export_items(db, normalized_period)
    payload = build_performance_payload(items, include_bets=True, period=normalized_period)
    return merge_historical_performance_payload(payload, historical)


async def _load_client_stat_rows(
    db: AsyncSession,
    *,
    user_id: Optional[int] = None,
    period: str = "all",
) -> list[tuple[User, Bet, str, bool, datetime, Optional[Decimal], Optional[Decimal], Optional[Decimal], Optional[Decimal]]]:
    query = (
        select(
            User,
            Bet,
            user_bets.c.access_type,
            user_bets.c.match_charged,
            user_bets.c.taken_at,
            user_bets.c.stake_rub,
            user_bets.c.stake_flats,
            user_bets.c.profit_rub,
            user_bets.c.profit_flats,
        )
        .join(user_bets, user_bets.c.user_id == User.telegram_id)
        .join(Bet, Bet.id == user_bets.c.bet_id)
        .filter(
            User.role.notin_(list(STAFF_ROLES)),
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    if user_id is not None:
        query = query.filter(User.telegram_id == user_id)
    start = period_start(period)
    end = period_end(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    if end:
        query = query.filter(Bet.resolved_at < end)
    return (await db.execute(query)).all()


async def _client_counts(db: AsyncSession) -> dict[str, int]:
    open_flat_exists = select(FlatSubscription.id).where(
        FlatSubscription.user_id == User.telegram_id,
        FlatSubscription.status.in_(["pending_setup", "active", "closing"]),
    ).exists()
    total_res = await db.execute(
        select(func.count(User.telegram_id)).filter(User.role.notin_(list(STAFF_ROLES)))
    )
    active_res = await db.execute(
        select(func.count(User.telegram_id)).filter(
            User.role.notin_(list(STAFF_ROLES)),
            (User.purchased_bets_balance > 0)
            | (User.matches_remaining > 0)
            | (User.guarantee_active == True)
            | open_flat_exists,
        )
    )
    return {
        "clients_count": int(total_res.scalar() or 0),
        "active_clients_count": int(active_res.scalar() or 0),
    }


@router.get("/stats/clients")
async def get_admin_client_stats(
    period: str = Query("all", pattern=STATS_PERIOD_QUERY_PATTERN),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Per-client paid settled performance summary for the admin stats workspace."""
    normalized_period = normalize_period(period)
    rows = await _load_client_stat_rows(db, period=normalized_period)
    grouped: dict[int, dict[str, Any]] = {}
    for user, bet, access_type, match_charged, taken_at, stake_rub, stake_flats, profit_rub, profit_flats in rows:
        if not is_paid_client_access(access_type, match_charged):
            continue
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
            stake_rub=stake_rub,
            stake_flats=stake_flats,
            profit_rub=profit_rub,
            profit_flats=profit_flats,
        )
        if not item:
            continue
        if not filter_items_by_period([item], normalized_period):
            continue
        if user.telegram_id not in grouped:
            grouped[user.telegram_id] = {
                "user": {
                    "telegram_id": user.telegram_id,
                    "username": user.username,
                    "first_name": user.first_name,
                    "last_name": user.last_name,
                    "photo_url": user.photo_url,
                    "name": _admin_user_display(user),
                    "matches_remaining": int(user.purchased_bets_balance or user.matches_remaining or 0),
                    "guarantee_active": bool(user.guarantee_active),
                    "client_group": user.client_group,
                    "client_tag": user.client_tag,
                },
                "items": [],
            }
        grouped[user.telegram_id]["items"].append(item)

    clients = []
    all_items = []
    for group in grouped.values():
        items = group["items"]
        all_items.extend(items)
        summary = summarize_items(items)
        clients.append({
            **group["user"],
            "summary": summary,
            "source_split": build_performance_payload(items, include_bets=False, period=normalized_period)["source_split"],
            "recent_results": last_result_codes(items),
            "situation": client_situation(summary),
        })

    clients.sort(key=lambda client: (
        client["situation"]["tone"] != "danger",
        -float(client["summary"]["profit_units"]),
        -int(client["summary"]["bets"]),
    ))
    counts = await _client_counts(db)
    return {
        "period": normalized_period,
        "period_label": build_performance_payload([], include_bets=False, period=normalized_period)["period_label"],
        **counts,
        "active_clients_with_stats_count": len(clients),
        "summary": summarize_items(all_items),
        "clients": clients,
    }


@router.get("/stats/clients/{user_id}")
async def get_admin_client_timeline_stats(
    user_id: int,
    period: str = Query("all", pattern=STATS_PERIOD_QUERY_PATTERN),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> dict[str, Any]:
    """Detailed timeline for a single client's paid settled bets."""
    normalized_period = normalize_period(period)
    rows = await _load_client_stat_rows(db, user_id=user_id, period=normalized_period)
    if not rows:
        target_res = await db.execute(select(User).filter(User.telegram_id == user_id))
        target = target_res.scalars().first()
        if not target:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Клиент не найден")
    else:
        target = rows[0][0]

    paid_items = []
    excluded_items = []
    for _user, bet, access_type, match_charged, taken_at, stake_rub, stake_flats, profit_rub, profit_flats in rows:
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
            stake_rub=stake_rub,
            stake_flats=stake_flats,
            profit_rub=profit_rub,
            profit_flats=profit_flats,
        )
        if not item:
            continue
        if is_paid_client_access(access_type, match_charged):
            paid_items.append(item)
        else:
            excluded_items.append(item)

    payload = build_performance_payload(paid_items, include_bets=True, period=normalized_period)
    payload["user"] = {
        "telegram_id": target.telegram_id,
        "username": target.username,
        "first_name": target.first_name,
        "last_name": target.last_name,
        "photo_url": target.photo_url,
        "name": _admin_user_display(target),
        "matches_remaining": int(target.purchased_bets_balance or target.matches_remaining or 0),
        "guarantee_active": bool(target.guarantee_active),
        "client_group": target.client_group,
        "client_tag": target.client_tag,
    }
    payload["situation"] = client_situation(payload["summary"])
    filtered_paid_items = filter_items_by_period(paid_items, normalized_period)
    payload["recent_results"] = last_result_codes(filtered_paid_items)
    filtered_excluded_items = filter_items_by_period(excluded_items, normalized_period)
    payload["excluded_summary"] = summarize_items(filtered_excluded_items)
    payload["excluded_bets"] = filtered_excluded_items
    return payload


def _export_rows_from_items(items: list[dict[str, Any]], *, scope: str, client_name: Optional[str] = None) -> list[dict[str, Any]]:
    rows = []
    for item in items:
        rows.append({
            "scope": scope,
            "client": client_name or "",
            "resolved_at": item.get("resolved_at") or "",
            "event_name": item.get("event_name") or "",
            "status": item.get("status") or "",
            "coefficient": item.get("coefficient") or 0,
            "profit_units": item.get("profit_units") or 0,
            "roi_percent": round(float(item.get("profit_units") or 0) * 100, 2),
            "source": item.get("source_type") or "",
            "sport": item.get("sport_type") or "",
            "bookmakers": ", ".join(item.get("bookmaker_names") or []),
            "outcome": item.get("outcome") or "",
        })
    return rows


def _csv_response(rows: list[dict[str, Any]], filename: str) -> StreamingResponse:
    fieldnames = [
        "scope",
        "client",
        "resolved_at",
        "event_name",
        "status",
        "coefficient",
        "profit_units",
        "roi_percent",
        "source",
        "sport",
        "bookmakers",
        "outcome",
    ]
    buffer = StringIO()
    buffer.write("\ufeff")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _yes_no_csv(value: bool) -> str:
    return "Да" if value else "Нет"


def _client_export_datetime(value: Optional[datetime]) -> str:
    if not value:
        return ""
    return value.isoformat()


def _client_export_streak_label(kind: Optional[str], count: int) -> str:
    if count <= 0 or not kind:
        return ""
    label = "побед" if kind == "win" else "пораж."
    return f"{count} {label}"


def _client_info_csv_response(rows: list[ClientInfoExportRow], filename: str) -> StreamingResponse:
    fieldnames = [
        "ID",
        "Клиент",
        "Username",
        "Телефон",
        "VK ID",
        "Web/VK клиент",
        "Группа",
        "Тег",
        "Активные бонусы",
        "БК клиента",
        "Дата регистрации",
        "Матчей осталось",
        "Гарантия",
        "Взял матчей всего",
        "Рассчитано матчей",
        "Ожидают расчета",
        "Возвраты",
        "Ставки",
        "Победы",
        "Поражения",
        "Проход, %",
        "ROI, %",
        "Профит, флеты",
        "Ср. кф",
        "Текущая серия",
        "Макс. побед",
        "Макс. пораж.",
        "Последние исходы",
        "Ситуация",
        "Описание",
    ]
    buffer = StringIO()
    buffer.write("\ufeff")
    writer = csv.DictWriter(buffer, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "ID": row.user_id,
            "Клиент": row.client_name,
            "Username": f"@{row.username}" if row.username else "",
            "Телефон": row.phone,
            "VK ID": row.vk_user_id,
            "Web/VK клиент": _yes_no_csv(row.is_web_only),
            "Группа": row.client_group,
            "Тег": row.client_tag,
            "Активные бонусы": row.active_bonuses,
            "БК клиента": row.bookmaker_names,
            "Дата регистрации": _client_export_datetime(row.created_at),
            "Матчей осталось": row.matches_remaining,
            "Гарантия": _yes_no_csv(row.guarantee_active),
            "Взял матчей всего": row.total_taken_bets,
            "Рассчитано матчей": row.settled_bets,
            "Ожидают расчета": row.pending_bets,
            "Возвраты": row.refund_bets,
            "Ставки": row.bets,
            "Победы": row.wins,
            "Поражения": row.losses,
            "Проход, %": round(row.winrate, 2),
            "ROI, %": round(row.roi, 2),
            "Профит, флеты": round(row.profit_units, 2),
            "Ср. кф": round(row.average_coefficient, 3),
            "Текущая серия": _client_export_streak_label(row.current_streak_type, row.current_streak),
            "Макс. побед": row.max_win_streak,
            "Макс. пораж.": row.max_loss_streak,
            "Последние исходы": " ".join(row.recent_results),
            "Ситуация": row.situation_label,
            "Описание": row.situation_description,
        })
    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _xlsx_bytes_response(content: bytes, filename: str) -> StreamingResponse:
    buffer = BytesIO(content)
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


async def _author_export_items(db: AsyncSession, admin: User, period: str, source: str) -> list[dict[str, Any]]:
    query = (
        select(Bet)
        .filter(
            Bet.author_id == admin.telegram_id,
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    start = period_start(period)
    end = period_end(period)
    if start:
        query = query.filter(Bet.resolved_at >= start)
    if end:
        query = query.filter(Bet.resolved_at < end)
    result = await db.execute(query)
    items = [
        item
        for bet in result.scalars().all()
        if (item := stat_item_from_bet(bet)) is not None
    ]
    items = filter_items_by_period(items, period)
    if source in {"feed", "private", "paid_set"}:
        items = [item for item in items if item.get("source_type") == source]
    return items


async def _clients_export_items(db: AsyncSession, period: str) -> list[dict[str, Any]]:
    rows = await _load_client_stat_rows(db, period=period)
    items = []
    for user, bet, access_type, match_charged, taken_at, stake_rub, stake_flats, profit_rub, profit_flats in rows:
        if not is_paid_client_access(access_type, match_charged):
            continue
        item = stat_item_from_bet(
            bet,
            access_type=access_type,
            match_charged=match_charged,
            taken_at=taken_at,
            stake_rub=stake_rub,
            stake_flats=stake_flats,
            profit_rub=profit_rub,
            profit_flats=profit_flats,
        )
        if item:
            item["client_name"] = _admin_user_display(user)
            items.append(item)
    return filter_items_by_period(items, period)


async def _shamrai_export_items(db: AsyncSession, period: str) -> list[dict[str, Any]]:
    historical = await load_active_historical_stats_snapshot(db, period)
    query = (
        select(Bet)
        .filter(
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            Bet.status.in_(["win", "loss"]),
            Bet.resolved_at.isnot(None),
        )
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
        .order_by(Bet.resolved_at.desc())
    )
    start = period_start(period)
    end = period_end(period)
    lower_bound = start
    if historical and (lower_bound is None or historical.cutoff_at > lower_bound):
        lower_bound = historical.cutoff_at
    if lower_bound:
        query = query.filter(Bet.resolved_at >= lower_bound)
    if end:
        query = query.filter(Bet.resolved_at < end)
    result = await db.execute(query)
    return [
        item
        for bet in result.scalars().all()
        if (item := stat_item_from_bet(bet)) is not None
    ]


@router.get("/stats/export")
async def export_admin_stats(
    scope: str = Query("author", pattern="^(author|clients|shamrai)$"),
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    period: str = Query("all", pattern=STATS_PERIOD_QUERY_PATTERN),
    source: str = Query("all", pattern="^(all|feed|private|paid_set)$"),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> Response:
    normalized_period = normalize_period(period)
    if format == "xlsx":
        period_label = stats_export_period_label(normalized_period)
        if scope == "shamrai":
            export_items = await load_shamrai_export_items(db, normalized_period)
            historical = await load_active_historical_stats_snapshot(db, normalized_period)
            content = build_stats_export_workbook(
                export_items,
                title="СТАТИСТИКА SHAMRAI",
                period_label=period_label,
                include_client=False,
                historical=historical,
            )
        elif scope == "clients":
            client_info_rows = await load_client_info_export_rows(db, normalized_period)
            client_recent_rows = await load_client_recent_bet_export_rows(
                db,
                normalized_period,
                limit_per_client=None,
            )
            content = build_client_info_export_workbook(
                client_info_rows,
                recent_rows=client_recent_rows,
                period_label=period_label,
            )
        else:
            export_items = await load_author_export_items(
                db,
                author_id=admin.telegram_id,
                period=normalized_period,
                source=source,
            )
            source_label = {
                "feed": "ЛЕНТА",
                "private": "ЗАКРЫТАЯ ВЫДАЧА",
                "paid_set": "НАБОРЫ",
                "all": "ВСЕ ПРОГНОЗЫ",
            }.get(source, "ВСЕ ПРОГНОЗЫ")
            content = build_stats_export_workbook(
                export_items,
                title=f"СТАТИСТИКА АВТОРА: {source_label}",
                period_label=period_label,
                include_client=False,
            )
        filename = (
            f"shamrai_clients_info_{normalized_period}.xlsx"
            if scope == "clients"
            else f"shamrai_stats_{scope}_{normalized_period}.xlsx"
        )
        return _xlsx_bytes_response(content, filename)

    if scope == "clients":
        items = await _clients_export_items(db, normalized_period)
        rows = _export_rows_from_items(items, scope="clients")
        for row, item in zip(rows, items):
            row["client"] = item.get("client_name", "")
    elif scope == "shamrai":
        items = await _shamrai_export_items(db, normalized_period)
        rows = _export_rows_from_items(items, scope="shamrai")
    else:
        items = await _author_export_items(db, admin, normalized_period, source)
        rows = _export_rows_from_items(items, scope=f"author:{source}")

    filename = f"shamrai_stats_{scope}_{normalized_period}.{format}"
    return _csv_response(rows, filename)


@router.post("/stats/drive-export")
async def create_admin_stats_drive_export(
    payload: AdminStatsDriveExportRequest,
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    scope = str(payload.scope or "all").strip().lower()
    if scope not in {"shamrai", "clients", "all"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="scope должен быть shamrai, clients или all")

    period = normalize_period(payload.period)
    formats = [str(item).strip().lower() for item in (payload.formats or [])]
    if not formats:
        formats = ["google_sheet"]
    invalid_formats = [item for item in formats if item not in {"xlsx", "google_sheet"}]
    if invalid_formats:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="formats может содержать только xlsx и google_sheet")

    return start_drive_export_job(scope=scope, period=period, formats=list(dict.fromkeys(formats)))


@router.get("/stats/drive-export/{job_id}")
async def get_admin_stats_drive_export(
    job_id: str,
    admin: User = Depends(get_current_admin_read),
) -> dict[str, Any]:
    job = get_drive_export_job(job_id)
    if not job:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача выгрузки не найдена")
    return job

# --- PENDING FORECASTS FOR RESOLVING ---

@router.get("/bets/pending", response_model=List[BetResponse])
async def get_pending_bets(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
):
    """
    GET /api/admin/bets/pending
    Lists all non-calculated sports predictions.
    """
    legacy_stopped_private_filter = (
        (Bet.status == "deleted")
        & Bet.resolved_at.is_(None)
        & Bet.delivery_mode.in_(["sales_private", "paid_set"])
    )
    query = (
        select(Bet)
        .filter(
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            or_(Bet.status == "pending", legacy_stopped_private_filter),
        )
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
        .with_for_update()
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
        .order_by(User.telegram_id)
    )
    takers = users_result.scalars().all()
    await lock_user_balances(db, [user.telegram_id for user in takers])
    linked_flat_subscription_ids = {
        row.flat_subscription_id
        for row in (
            await db.execute(
                select(user_bets.c.flat_subscription_id).filter(
                    user_bets.c.bet_id == bet.id,
                    user_bets.c.flat_subscription_id.is_not(None),
                )
            )
        ).all()
    }
    await lock_flat_subscription_financial_rows(db, linked_flat_subscription_ids)
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
        if forecast_request.flat_subscription_id is not None:
            linked_flat_subscription_ids.add(forecast_request.flat_subscription_id)
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

    await refresh_flat_subscriptions_after_terminal_requests(
        db,
        linked_flat_subscription_ids,
    )

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
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/promo/
    Creates a new active promotional code for discounts or match credits.
    """
    code = data.code.strip().upper()
    reward_type = data.reward_type or "discount"
    discount_percent = int(data.discount_percent or 0)
    matches_count = int(data.matches_count or 0)
    target_flats = data.target_flats

    if reward_type == "discount":
        if discount_percent <= 0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите скидку от 1 до 100%",
            )
        matches_count = 0
        target_flats = None
    elif reward_type == "flats":
        if target_flats is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите цель во флетах для промокода",
            )
        discount_percent = 0
        matches_count = 0
    else:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный тип промокода",
        )

    exists = await db.execute(select(PromoCode).filter(PromoCode.code == code))
    if exists.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Промокод с таким названием уже существует"
        )
        
    promo = PromoCode(
        code=code,
        reward_type=reward_type,
        discount_percent=discount_percent,
        matches_count=matches_count,
        target_flats=target_flats,
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
            "reward_type": promo.reward_type,
            "discount_percent": promo.discount_percent,
            "matches_count": promo.matches_count,
            "target_flats": str(promo.target_flats) if promo.target_flats is not None else None,
            "valid_until": promo.valid_until.isoformat(),
        },
    )
    await db.commit()
    await db.refresh(promo)
    return promo

@router.get("/promo/list")
async def list_promos(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
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
    admin: User = Depends(get_current_privileged_admin),
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
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
):
    """
    GET /api/admin/users
    Retrieves CRM profiles for all subscribers in the system.
    """
    result = await db.execute(
        select(User)
        .filter(User.role == "user")
        .options(selectinload(User.bookmakers), selectinload(User.badges), selectinload(User.flat_subscriptions))
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
            .filter(
                user_bets.c.user_id.in_(user_ids),
                Bet.publication_type == PUBLICATION_TYPE_FORECAST,
                Bet.status.in_(["win", "loss"]),
            )
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


@router.get("/users-page")
async def admin_list_users_page(
    limit: int = Query(50, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    q: Optional[str] = Query(None),
    activity: str = Query("all", pattern="^(all|active|empty|guarantee)$"),
    group: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    bookmaker_id: Optional[int] = Query(None, ge=1),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    """Cursor-paginated CRM directory for responsive admin UI."""
    cursor_value = _decode_admin_user_cursor(cursor)
    filters = [User.role == "user"]
    clean_q = (q or "").strip()
    if clean_q:
        like_q = f"%{clean_q.lower()}%"
        filters.append(or_(
            func.lower(func.coalesce(User.username, "")).like(like_q),
            func.lower(func.coalesce(User.first_name, "")).like(like_q),
            func.lower(func.coalesce(User.last_name, "")).like(like_q),
            func.lower(func.coalesce(User.client_group, "")).like(like_q),
            func.lower(func.coalesce(User.client_tag, "")).like(like_q),
            func.lower(func.coalesce(User.other_bookmaker_name, "")).like(like_q),
            cast(User.telegram_id, String).like(f"%{clean_q}%"),
        ))
    open_flat_exists = select(FlatSubscription.id).where(
        FlatSubscription.user_id == User.telegram_id,
        FlatSubscription.status.in_(["pending_setup", "active", "closing"]),
    ).exists()
    if activity == "active":
        filters.append(
            (User.purchased_bets_balance > 0)
            | (User.matches_remaining > 0)
            | (User.guarantee_active == True)
            | open_flat_exists
        )
    elif activity == "empty":
        filters.append(User.guarantee_active == False)
        filters.append(User.purchased_bets_balance <= 0)
        filters.append(User.matches_remaining <= 0)
        filters.append(~open_flat_exists)
    elif activity == "guarantee":
        filters.append(User.guarantee_active == True)
    if group and group != "all":
        filters.append(User.client_group == group)
    if tag and tag != "all":
        filters.append(User.client_tag == tag)
    clean_bookmaker_id = bookmaker_id if isinstance(bookmaker_id, int) and bookmaker_id > 0 else None
    if clean_bookmaker_id:
        filters.append(User.bookmakers.any(Bookmaker.id == clean_bookmaker_id))
    count_filters = list(filters)
    if cursor_value:
        cursor_created_at, cursor_telegram_id = cursor_value
        filters.append(or_(
            User.created_at < cursor_created_at,
            (User.created_at == cursor_created_at) & (User.telegram_id < cursor_telegram_id),
        ))

    total_result = await db.execute(select(func.count(User.telegram_id)).filter(*filters[:1]))
    total_users = int(total_result.scalar() or 0)
    filtered_total_result = await db.execute(select(func.count(User.telegram_id)).filter(*count_filters))
    filtered_total = int(filtered_total_result.scalar() or 0)

    result = await db.execute(
        select(User)
        .filter(*filters)
        .options(selectinload(User.bookmakers), selectinload(User.badges), selectinload(User.flat_subscriptions))
        .order_by(User.created_at.desc(), User.telegram_id.desc())
        .limit(limit + 1)
    )
    fetched_users = result.scalars().all()
    page_users = fetched_users[:limit]
    has_more = len(fetched_users) > limit
    user_ids = [user.telegram_id for user in page_users]
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
            .filter(
                user_bets.c.user_id.in_(user_ids),
                Bet.publication_type == PUBLICATION_TYPE_FORECAST,
                Bet.status.in_(["win", "loss"]),
            )
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

    return {
        "items": [
            build_admin_user_response(user, recent_results_by_user.get(user.telegram_id, []))
            for user in page_users
        ],
        "next_cursor": _encode_admin_user_cursor(page_users[-1]) if has_more and page_users else None,
        "has_more": has_more,
        "total": total_users,
        "filtered_total": filtered_total,
    }


@router.get("/admins", response_model=List[AdminUserListResponse])
async def admin_list_admins(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
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
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
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


@router.get("/users/export")
async def export_admin_users(
    format: str = Query("xlsx", pattern="^(csv|xlsx)$"),
    q: Optional[str] = Query(None),
    activity: str = Query("all", pattern="^(all|active|empty|guarantee)$"),
    group: Optional[str] = Query(None),
    tag: Optional[str] = Query(None),
    bookmaker_id: Optional[int] = Query(None, ge=1),
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
) -> Response:
    """Download CRM client situation export from the Clients tab."""
    client_info_rows = await load_client_info_export_rows(db, "all")
    filtered_info_rows = filter_client_info_export_rows(
        client_info_rows,
        q=q,
        activity=activity,
        group=group,
        tag=tag,
        bookmaker_id=bookmaker_id if isinstance(bookmaker_id, int) and bookmaker_id > 0 else None,
    )
    filtered_user_ids = {row.user_id for row in filtered_info_rows}

    if format == "csv":
        return _client_info_csv_response(filtered_info_rows, "shamrai_clients_crm.csv")

    recent_rows = await load_client_recent_bet_export_rows(db, "all", limit_per_client=None)
    filtered_recent_rows = filter_client_recent_export_rows(recent_rows, filtered_user_ids)
    content = build_client_info_export_workbook(
        filtered_info_rows,
        recent_rows=filtered_recent_rows,
        period_label="CRM: клиенты",
    )
    return _xlsx_bytes_response(content, "shamrai_clients_crm.xlsx")


@router.post("/users/drive-export")
async def create_admin_users_drive_export(
    payload: AdminCrmDriveExportRequest,
    admin: User = Depends(get_current_admin),
) -> dict[str, Any]:
    activity = str(payload.activity or "all").strip().lower()
    if activity not in {"all", "active", "empty", "guarantee"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="activity должен быть all, active, empty или guarantee")

    formats = [str(item).strip().lower() for item in (payload.formats or [])]
    if not formats:
        formats = ["google_sheet"]
    invalid_formats = [item for item in formats if item not in {"xlsx", "google_sheet"}]
    if invalid_formats:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="formats может содержать только xlsx и google_sheet")

    return start_crm_drive_export_job(
        q=payload.q,
        activity=activity,
        group=payload.group,
        tag=payload.tag,
        bookmaker_id=payload.bookmaker_id,
        formats=list(dict.fromkeys(formats)),
    )


@router.get("/users/drive-export/{job_id}")
async def get_admin_users_drive_export(
    job_id: str,
    admin: User = Depends(get_current_admin_read),
) -> dict[str, Any]:
    job = get_drive_export_job(job_id)
    if not job or job.get("scope") != "crm":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Задача CRM-выгрузки не найдена")
    return job


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

    sensitive_fields_requested = (
        data.role is not None
        or (data.matches_delta is not None and data.matches_delta != 0)
        or bool(data.close_guarantee)
        or data.subscription_end_date is not None
    )
    if sensitive_fields_requested:
        ensure_privileged_admin(admin)

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
        locked_balance = await lock_user_balance(db, user.telegram_id)
        previous_matches = current_match_balance(locked_balance)
        next_matches = max(0, previous_matches + data.matches_delta)
        applied_delta = next_matches - previous_matches
        user.purchased_bets_balance = next_matches
        user.matches_remaining = next_matches
        audit_changes["matches_remaining"] = {
            "from": previous_matches,
            "to": next_matches,
            "delta": applied_delta,
        }
        db.add(log_match_balance_event(
            user_id=user.telegram_id,
            event_type="admin_match_adjustment",
            delta_matches=applied_delta,
            note=f"Admin {admin.telegram_id} adjusted matches by {applied_delta}",
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

    if data.close_guarantee or (data.matches_delta is not None and data.matches_delta < 0):
        await activate_pending_flat_subscription_if_eligible(db, user=user)
                
    telegram_id = user.telegram_id
    await db.commit()
    return await load_user_response(db, telegram_id)


class FlatSubscriptionReopenRequest(BaseModel):
    note: Optional[str] = Field(default=None, max_length=500)


@router.get("/users/{user_id}/flat-subscription", response_model=Optional[FlatSubscriptionResponse])
async def admin_get_user_flat_subscription(
    user_id: int,
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    flat_subscription = await get_latest_flat_subscription(db, user_id)
    if flat_subscription is None:
        return None
    return await flat_subscription_payload(db, flat_subscription)


@router.post("/users/{user_id}/flat-subscription/credit", response_model=FlatSubscriptionResponse)
async def admin_credit_user_flat_subscription(
    user_id: int,
    payload: FlatSubscriptionCreditRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    user = await db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден")
    current = await get_open_flat_subscription(db, user_id)
    if current is None and payload.flat_amount_rub is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Для первого начисления укажите размер одного флета клиента",
        )
    flat_subscription = await credit_flat_subscription(
        db,
        user=user,
        target_flats=payload.target_flats,
        flat_amount_rub=payload.flat_amount_rub,
        event_type="admin_target_credit",
        actor_id=admin.telegram_id,
        note=payload.note,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="flat_subscription_credited",
        target_user_id=user_id,
        details={
            "flat_subscription_id": str(flat_subscription.id),
            "target_flats_added": str(payload.target_flats),
            "flat_amount_rub": str(payload.flat_amount_rub) if payload.flat_amount_rub is not None else None,
        },
    )
    await subscription_notifications.enqueue_flat_target_credit_notifications(
        db,
        user=user,
        flat_subscription=flat_subscription,
        target_flats_added=payload.target_flats,
        actor=admin,
        source="admin_manual",
    )
    await db.commit()
    await db.refresh(flat_subscription)
    return await flat_subscription_payload(db, flat_subscription)


@router.patch(
    "/users/{user_id}/flat-subscriptions/{flat_subscription_id}/flat-amount",
    response_model=FlatSubscriptionResponse,
)
async def admin_change_flat_amount(
    user_id: int,
    flat_subscription_id: UUID,
    payload: FlatSubscriptionConfigureRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    note = str(getattr(payload, "note", None) or "").strip()
    expected_revision = getattr(payload, "expected_revision", None)
    if not note:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите причину изменения")
    if expected_revision is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Обновите карточку клиента перед изменением")
    preview = await preview_flat_amount_change(
        db,
        user_id=user_id,
        flat_subscription_id=flat_subscription_id,
        flat_amount_rub=payload.flat_amount_rub,
        expected_revision=expected_revision,
    )
    if preview["before"]["flat_amount_rub"] == preview["after"]["flat_amount_rub"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Размер флета уже имеет это значение")
    flat_subscription = await configure_flat_subscription(
        db,
        user_id=user_id,
        flat_subscription_id=flat_subscription_id,
        flat_amount_rub=payload.flat_amount_rub,
        actor_id=admin.telegram_id,
        expected_revision=expected_revision,
        note=note,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="flat_amount_changed",
        target_user_id=user_id,
        details=jsonable_encoder(
            {
                "flat_subscription_id": str(flat_subscription.id),
                "reason": note,
                "preview": preview,
                "applied_revision": flat_subscription.revision,
            },
            custom_encoder={Decimal: lambda value: format(value, "f")},
        ),
    )
    client = await db.get(User, user_id)
    if client is not None:
        await subscription_notifications.enqueue_flat_adjustment_notification(
            db,
            user=client,
            flat_subscription=flat_subscription,
            adjustment_kind="flat_amount",
            reason=note,
        )
    await db.commit()
    await db.refresh(flat_subscription)
    return await flat_subscription_payload(db, flat_subscription)


@router.post(
    "/users/{user_id}/flat-subscriptions/{flat_subscription_id}/flat-amount/preview",
)
async def admin_preview_flat_amount_change(
    user_id: int,
    flat_subscription_id: UUID,
    payload: FlatSubscriptionConfigureRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    del admin
    expected_revision = getattr(payload, "expected_revision", None)
    if expected_revision is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Обновите карточку клиента перед изменением")
    return await preview_flat_amount_change(
        db,
        user_id=user_id,
        flat_subscription_id=flat_subscription_id,
        flat_amount_rub=payload.flat_amount_rub,
        expected_revision=expected_revision,
    )


@router.patch("/users/{user_id}/bets/{bet_id}/stake", response_model=FlatSubscriptionResponse)
async def admin_correct_user_flat_stake(
    user_id: int,
    bet_id: UUID,
    payload: FlatStakeCorrectionRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    note = str(payload.note or "").strip()
    expected_revision = getattr(payload, "expected_revision", None)
    if not note:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Укажите причину изменения")
    if expected_revision is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Обновите карточку клиента перед изменением")
    preview = await preview_flat_bet_stake(
        db,
        user_id=user_id,
        bet_id=bet_id,
        stake_rub=payload.stake_rub,
        expected_revision=expected_revision,
    )
    if preview["before"]["stake_rub"] == preview["after"]["stake_rub"]:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Сумма ставки уже имеет это значение")
    flat_subscription = await correct_flat_bet_stake(
        db,
        user_id=user_id,
        bet_id=bet_id,
        stake_rub=payload.stake_rub,
        actor_id=admin.telegram_id,
        note=note,
        expected_revision=expected_revision,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="flat_stake_corrected",
        target_user_id=user_id,
        details=jsonable_encoder(
            {
                "bet_id": str(bet_id),
                "reason": note,
                "preview": preview,
                "applied_revision": flat_subscription.revision,
            },
            custom_encoder={Decimal: lambda value: format(value, "f")},
        ),
    )
    client = await db.get(User, user_id)
    if client is not None:
        await subscription_notifications.enqueue_flat_adjustment_notification(
            db,
            user=client,
            flat_subscription=flat_subscription,
            adjustment_kind="stake",
            reason=note,
            bet_id=str(bet_id),
        )
    await db.commit()
    await db.refresh(flat_subscription)
    return await flat_subscription_payload(db, flat_subscription)


@router.post("/users/{user_id}/bets/{bet_id}/stake/preview")
async def admin_preview_user_flat_stake(
    user_id: int,
    bet_id: UUID,
    payload: FlatStakeCorrectionRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    del admin
    expected_revision = getattr(payload, "expected_revision", None)
    if expected_revision is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Обновите карточку клиента перед изменением")
    return await preview_flat_bet_stake(
        db,
        user_id=user_id,
        bet_id=bet_id,
        stake_rub=payload.stake_rub,
        expected_revision=expected_revision,
    )


@router.post("/flat-subscriptions/{flat_subscription_id}/reopen", response_model=FlatSubscriptionResponse)
async def admin_reopen_user_flat_subscription(
    flat_subscription_id: UUID,
    payload: FlatSubscriptionReopenRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    flat_subscription = await reopen_flat_subscription(
        db,
        flat_subscription_id=flat_subscription_id,
        actor_id=admin.telegram_id,
        note=payload.note,
    )
    add_admin_audit_log(
        db,
        actor=admin,
        action="flat_subscription_reopened",
        target_user_id=flat_subscription.user_id,
        details={"flat_subscription_id": str(flat_subscription.id)},
    )
    await db.commit()
    await db.refresh(flat_subscription)
    return await flat_subscription_payload(db, flat_subscription)


@router.post("/users/{source_user_id}/merge", response_model=UserResponse)
async def admin_merge_user(
    source_user_id: int,
    data: AdminUserMergeRequest,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /api/admin/users/{source_user_id}/merge
    Safely merges a legacy shadow/VK-only client profile into a canonical Telegram client.
    """
    if source_user_id == data.target_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя слить профиль сам в себя",
        )

    source = await load_user_response(db, source_user_id)
    target = await load_user_response(db, data.target_user_id)
    if not source or not target:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Source или target пользователь не найден",
        )

    if normalize_role(source.role) != "user" or normalize_role(target.role) != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Можно сливать только клиентские профили без staff-ролей",
        )
    if source.telegram_id > 0 and target.telegram_id > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Два реальных Telegram-профиля нельзя сливать автоматически",
        )
    if target.telegram_id < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Target должен быть каноническим Telegram-профилем",
        )
    if source.vk_user_id and target.vk_user_id and source.vk_user_id != target.vk_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="VK ID source и target отличаются. Выберите правильный target вручную",
        )

    source_details = {
        "source_user_id": source.telegram_id,
        "target_user_id": target.telegram_id,
        "source_vk_user_id": source.vk_user_id,
        "target_vk_user_id": target.vk_user_id,
        "reason": data.reason,
    }
    await _merge_web_only_user_into_telegram(db, source, target)
    add_admin_audit_log(
        db,
        actor=admin,
        action="user_merged",
        target_user_id=target.telegram_id,
        details=source_details,
    )

    target_id = target.telegram_id
    await db.commit()
    return await load_user_response(db, target_id)


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
    await db.execute(delete(PromoCodeRedemption).where(PromoCodeRedemption.user_id == user.telegram_id))
    await db.execute(delete(PromoCode).where(PromoCode.user_id == user.telegram_id))
    await db.execute(delete(ReferralRewardEvent).where(
        or_(
            ReferralRewardEvent.referrer_user_id == user.telegram_id,
            ReferralRewardEvent.referred_user_id == user.telegram_id,
        )
    ))
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
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/admin/chats/generate-link
    Admin-only: Trigger generation of a fresh single-use VIP Telegram invite link.
    """
    from src.core.config import settings
    chat_id = settings.TELEGRAM_VIP_CHAT_ID
    
    from src.services.telegram_bot import call_telegram_api_async
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
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
):
    """GET /api/admin/abtest — Retrieves all defined A/B split configs."""
    res = await db.execute(select(ABTestConfig))
    return res.scalars().all()


@router.post("/abtest", response_model=ABTestConfigResponse)
async def create_or_update_ab_config(
    data: ABTestConfigCreate,
    admin: User = Depends(get_current_privileged_admin),
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

@router.get("/users/{user_id}/bonuses", response_model=List[PromoCodeResponse])
async def get_user_bonuses(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_admin_read)
):
    """
    GET /api/admin/users/{user_id}/bonuses
    Returns all active bonuses (promo codes) for a user.
    """
    target_user = await db.get(User, user_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Пользователь не найден")

    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(PromoCode)
        .filter(
            PromoCode.user_id == target_user.telegram_id,
            # Return both active and inactive (used) bonuses, maybe filter out old expired ones?
            # Actually, just returning all for now, or just valid_until > now.
            PromoCode.valid_until > now
        )
        .order_by(PromoCode.valid_until.asc())
    )
    return result.scalars().all()

@router.post("/bonuses/{bonus_id}/mark-used")
async def mark_bonus_used(
    bonus_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_privileged_admin)
):
    """
    POST /api/admin/bonuses/{bonus_id}/mark-used
    Marks a promo code (bonus) as used (inactive) so it disappears from the client's profile.
    """
    bonus = await db.get(PromoCode, bonus_id)
    if not bonus:
        raise HTTPException(status_code=404, detail="Бонус не найден")
    
    bonus.is_active = False
    await db.commit()

    # Send telegram notification to user
    from src.services.signals import send_telegram_signal_to_user
    user = await db.get(User, bonus.user_id)
    if user and user.telegram_id and user.telegram_id > 0:
        prize_name = bonus.description or bonus.code
        text = f"🎁 Ваш бонус «{prize_name}» был успешно использован администратором!"
        await send_telegram_signal_to_user(user.telegram_id, text)

    return {"status": "success", "message": "Бонус отмечен как использованный"}

# ==========================================
# PROMO CONTENT MANAGEMENT (CMS)
# ==========================================

@router.post("/quizzes", response_model=QuizResponse)
async def create_quiz(
    payload: QuizCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_privileged_admin)
):
    quiz = Quiz(
        bet_id=payload.bet_id,
        discount_reward=payload.discount_reward,
        questions=[q.model_dump() for q in payload.questions]
    )
    db.add(quiz)
    await db.commit()
    await db.refresh(quiz)
    return quiz

@router.get("/quizzes", response_model=list[QuizResponse])
async def get_quizzes(
    db: AsyncSession = Depends(get_read_db),
    admin: User = Depends(get_current_admin_read)
):
    result = await db.execute(select(Quiz).order_by(Quiz.created_at.desc()))
    return result.scalars().all()

@router.delete("/quizzes/{quiz_id}")
async def delete_quiz(
    quiz_id: int,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_privileged_admin)
):
    quiz = await db.get(Quiz, quiz_id)
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    quiz.is_active = False
    await db.commit()
    return {"status": "success"}

@router.post("/pvp", response_model=PvPBattleResponse)
async def create_pvp_battle(
    payload: PvPBattleCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_privileged_admin)
):
    battle = PvPBattle(
        match_name=payload.match_name,
        option_a=payload.option_a,
        option_b=payload.option_b,
        votes_a=0,
        votes_b=0,
        is_active=True
    )
    db.add(battle)
    await db.commit()
    await db.refresh(battle)
    return battle

@router.get("/pvp", response_model=list[PvPBattleResponse])
async def get_pvp_battles(
    db: AsyncSession = Depends(get_read_db),
    admin: User = Depends(get_current_admin_read)
):
    result = await db.execute(select(PvPBattle).order_by(PvPBattle.created_at.desc()))
    return result.scalars().all()

@router.post("/crowd-bets", response_model=CrowdBetResponse)
async def create_crowd_bet(
    payload: CrowdBetCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(get_current_privileged_admin)
):
    bet = await db.get(Bet, payload.bet_id)
    if not bet:
        raise HTTPException(status_code=404, detail="Bet not found")

    cb = CrowdBet(
        bet_id=payload.bet_id,
        target_amount=payload.target_amount,
        current_amount=0,
        is_active=True,
        is_completed=False
    )
    db.add(cb)
    await db.commit()
    await db.refresh(cb)
    return cb

@router.get("/crowd-bets", response_model=list[CrowdBetResponse])
async def get_crowd_bets(
    db: AsyncSession = Depends(get_read_db),
    admin: User = Depends(get_current_admin_read)
):
    result = await db.execute(select(CrowdBet).order_by(CrowdBet.created_at.desc()))
    return result.scalars().all()
