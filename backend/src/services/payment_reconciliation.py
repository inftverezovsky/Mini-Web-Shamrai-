import logging
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable, Optional

from sqlalchemy import and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.models.models import (
    CrowdBetParticipant,
    FlatSubscriptionCredit,
    MatchBalanceLog,
    PaymentAttempt,
    Subscription,
    user_bets,
)
from src.schemas.schemas import (
    PaymentReconciliationIssue,
    PaymentReconciliationReport,
    PaymentReconciliationSummary,
)


logger = logging.getLogger("uvicorn")

ISSUE_PENDING_STALE = "pending_stale"
ISSUE_PROCESSING_STALE = "processing_stale"
ISSUE_PROVIDER_SUCCEEDED_LOCAL_NOT_SUCCEEDED = "provider_succeeded_local_not_succeeded"
ISSUE_PROVIDER_LOCAL_AMOUNT_MISMATCH = "provider_local_amount_mismatch"
ISSUE_CHECKOUT_REQUIRES_RECONCILIATION = "checkout_requires_reconciliation"
ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT = "local_succeeded_missing_entitlement"
ISSUE_LOCAL_SUCCEEDED_ENTITLEMENT_MISMATCH = "local_succeeded_entitlement_mismatch"

PURCHASE_SUBSCRIPTION = "subscription"
PURCHASE_SINGLE_BET = "single_bet"
PURCHASE_CROWD_BET = "crowd_bet"
PURCHASE_BET_HINT = "bet_hint"
PURCHASE_UNKNOWN = "unknown"

DEFAULT_PENDING_STALE_MINUTES = 15
DEFAULT_PROCESSING_STALE_MINUTES = 5
DEFAULT_SCAN_WINDOW_HOURS = 48
DEFAULT_ISSUE_LIMIT = 100
MAX_SCAN_WINDOW_HOURS = 24 * 14
MAX_ISSUE_LIMIT = 500


YooKassaStatusFetcher = Callable[[str], dict[str, Any]]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _decimal(value: Any) -> Optional[Decimal]:
    try:
        return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError):
        return None


def _decimal_matches(left: Any, right: Any) -> bool:
    left_decimal = _decimal(left)
    right_decimal = _decimal(right)
    return left_decimal is not None and right_decimal is not None and left_decimal == right_decimal


def _safe_int(value: Any) -> int:
    try:
        return int(Decimal(str(value)))
    except Exception:
        return 0


def _purchase_type(attempt: PaymentAttempt) -> str:
    metadata = attempt.metadata_json or {}
    metadata_purchase_type = str(metadata.get("purchase_type") or "").strip()
    if attempt.plan_id or attempt.entitlement_type_snapshot:
        return PURCHASE_SUBSCRIPTION
    if metadata_purchase_type == PURCHASE_CROWD_BET:
        return PURCHASE_CROWD_BET
    if metadata_purchase_type == PURCHASE_BET_HINT:
        return PURCHASE_BET_HINT
    if attempt.bet_id:
        return PURCHASE_SINGLE_BET
    return PURCHASE_UNKNOWN


def _issue(
    *,
    code: str,
    severity: str,
    message: str,
    attempt: PaymentAttempt,
    purchase_type: str,
    details: Optional[dict[str, Any]] = None,
) -> PaymentReconciliationIssue:
    return PaymentReconciliationIssue(
        code=code,
        severity=severity,
        message=message,
        attempt_id=attempt.id,
        user_id=attempt.user_id,
        provider=attempt.provider,
        provider_payment_id=attempt.provider_payment_id,
        status=attempt.status,
        purchase_type=purchase_type,
        amount=f"{_decimal(attempt.amount) or Decimal('0.00'):.2f}",
        currency=attempt.currency,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at,
        processing_started_at=attempt.processing_started_at,
        processed_at=attempt.processed_at,
        details=details or {},
    )


def _provider_fetcher(fetcher: Optional[YooKassaStatusFetcher]) -> Optional[YooKassaStatusFetcher]:
    if fetcher is not None:
        return fetcher
    try:
        from src.api.payments import _request_yookassa_payment
    except Exception:
        logger.warning("YooKassa reconciliation fetcher is unavailable")
        return None
    return _request_yookassa_payment


async def _scan_attempts(
    db: AsyncSession,
    *,
    window_hours: int,
) -> list[PaymentAttempt]:
    now = _utc_now()
    window_start = now - timedelta(hours=window_hours)
    result = await db.execute(
        select(PaymentAttempt)
        .options(selectinload(PaymentAttempt.plan))
        .filter(
            or_(
                PaymentAttempt.created_at >= window_start,
                PaymentAttempt.updated_at >= window_start,
                PaymentAttempt.processing_started_at >= window_start,
            )
        )
        .order_by(PaymentAttempt.created_at.desc(), PaymentAttempt.id.desc())
    )
    return list(result.scalars().all())


async def _subscription_issue(
    db: AsyncSession,
    attempt: PaymentAttempt,
    purchase_type: str,
) -> Optional[PaymentReconciliationIssue]:
    if not attempt.provider_payment_id:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded subscription payment has no provider payment id for entitlement lookup.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={"required": ["subscription", "subscription_purchase_ledger"]},
        )

    subscription_result = await db.execute(
        select(Subscription).filter(
            Subscription.payment_provider == attempt.provider,
            Subscription.payment_id == attempt.provider_payment_id,
        )
    )
    subscription = subscription_result.scalars().first()
    if not subscription:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded subscription payment has no matching subscription.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={"required": ["subscription", "subscription_purchase_ledger"]},
        )

    mismatch_details: dict[str, Any] = {}
    if subscription.user_id != attempt.user_id:
        mismatch_details["subscription_user_id"] = subscription.user_id
    if subscription.plan_id != attempt.plan_id:
        mismatch_details["subscription_plan_id"] = subscription.plan_id
    entitlement_type = attempt.entitlement_type_snapshot or (
        attempt.plan.entitlement_type if attempt.plan is not None else None
    )
    expected_target_flats = (
        attempt.target_flats_snapshot
        if attempt.target_flats_snapshot is not None
        else (attempt.plan.target_flats if attempt.plan is not None else None)
    )
    expected_match_count = (
        attempt.match_count_snapshot
        if attempt.match_count_snapshot is not None
        else (attempt.plan.match_count if attempt.plan is not None else 0)
    )
    is_flat_plan = entitlement_type == "flat"
    allowed_statuses = {"pending_setup", "active", "closing", "completed"} if is_flat_plan else {"active"}
    if subscription.status not in allowed_statuses:
        mismatch_details["subscription_status"] = subscription.status

    if is_flat_plan:
        if subscription.flat_subscription_id is None:
            mismatch_details["missing_flat_subscription"] = True
        if not _decimal_matches(subscription.target_flats_snapshot, expected_target_flats):
            mismatch_details["target_flats_snapshot"] = str(subscription.target_flats_snapshot)
            mismatch_details["expected_target_flats"] = str(expected_target_flats)
        credit_result = await db.execute(
            select(FlatSubscriptionCredit).filter(
                FlatSubscriptionCredit.subscription_id == subscription.id,
                FlatSubscriptionCredit.event_type == "subscription_purchase",
            )
        )
        credit = credit_result.scalars().first()
        if credit is None:
            mismatch_details["missing_flat_credit"] = True
        elif not _decimal_matches(credit.delta_target_flats, expected_target_flats):
            mismatch_details["flat_credit_target"] = str(credit.delta_target_flats)

    if not is_flat_plan:
        ledger_result = await db.execute(
            select(MatchBalanceLog).filter(
                MatchBalanceLog.subscription_id == subscription.id,
                MatchBalanceLog.event_type == "subscription_purchase",
            )
        )
        ledger = ledger_result.scalars().first()
        if not ledger:
            mismatch_details["missing_ledger"] = True
        elif int(ledger.delta_matches or 0) != int(expected_match_count or 0):
            mismatch_details["ledger_delta_matches"] = int(ledger.delta_matches or 0)
            mismatch_details["expected_matches"] = int(expected_match_count or 0)

    if mismatch_details:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_ENTITLEMENT_MISMATCH,
            severity="error",
            message="Succeeded subscription payment entitlement is incomplete or mismatched.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={
                "subscription_id": str(subscription.id),
                **mismatch_details,
            },
        )
    return None


async def _single_bet_issue(
    db: AsyncSession,
    attempt: PaymentAttempt,
    purchase_type: str,
) -> Optional[PaymentReconciliationIssue]:
    if not attempt.bet_id:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded single bet payment has no bet id.",
            attempt=attempt,
            purchase_type=purchase_type,
        )

    access_result = await db.execute(
        select(user_bets).filter(
            and_(
                user_bets.c.user_id == attempt.user_id,
                user_bets.c.bet_id == attempt.bet_id,
            )
        )
    )
    if not access_result.first():
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded single bet payment has no user_bets access row.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={"bet_id": str(attempt.bet_id)},
        )
    return None


async def _crowd_bet_issue(
    db: AsyncSession,
    attempt: PaymentAttempt,
    purchase_type: str,
) -> Optional[PaymentReconciliationIssue]:
    metadata = attempt.metadata_json or {}
    crowd_bet_id = _safe_int(metadata.get("crowd_bet_id"))
    if crowd_bet_id <= 0:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded crowd payment has no valid crowd bet id.",
            attempt=attempt,
            purchase_type=purchase_type,
        )

    participant_result = await db.execute(
        select(CrowdBetParticipant).filter(
            and_(
                CrowdBetParticipant.crowd_bet_id == crowd_bet_id,
                CrowdBetParticipant.user_id == attempt.user_id,
            )
        )
    )
    participant = participant_result.scalars().first()
    if not participant:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
            severity="error",
            message="Succeeded crowd payment has no participant contribution.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={"crowd_bet_id": crowd_bet_id},
        )

    expected_amount = _safe_int(attempt.amount)
    if int(participant.contributed_amount or 0) < expected_amount:
        return _issue(
            code=ISSUE_LOCAL_SUCCEEDED_ENTITLEMENT_MISMATCH,
            severity="error",
            message="Succeeded crowd payment contribution is lower than the paid amount.",
            attempt=attempt,
            purchase_type=purchase_type,
            details={
                "crowd_bet_id": crowd_bet_id,
                "contributed_amount": int(participant.contributed_amount or 0),
                "expected_at_least": expected_amount,
            },
        )
    return None


async def _local_entitlement_issue(
    db: AsyncSession,
    attempt: PaymentAttempt,
    purchase_type: str,
) -> Optional[PaymentReconciliationIssue]:
    if purchase_type == PURCHASE_SUBSCRIPTION:
        return await _subscription_issue(db, attempt, purchase_type)
    if purchase_type == PURCHASE_SINGLE_BET:
        return await _single_bet_issue(db, attempt, purchase_type)
    if purchase_type == PURCHASE_CROWD_BET:
        return await _crowd_bet_issue(db, attempt, purchase_type)
    if purchase_type == PURCHASE_BET_HINT:
        return None
    return _issue(
        code=ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
        severity="error",
        message="Succeeded payment does not map to a supported purchase type.",
        attempt=attempt,
        purchase_type=purchase_type,
    )


def _provider_issues(
    attempt: PaymentAttempt,
    purchase_type: str,
    provider_payment: dict[str, Any],
) -> list[PaymentReconciliationIssue]:
    if provider_payment.get("status") != "succeeded":
        return []

    issues: list[PaymentReconciliationIssue] = []
    amount_info = provider_payment.get("amount") or {}
    provider_amount = _decimal(amount_info.get("value"))
    provider_currency = str(amount_info.get("currency") or "")
    safe_details = {
        "provider_status": provider_payment.get("status"),
        "provider_amount": f"{provider_amount:.2f}" if provider_amount is not None else None,
        "provider_currency": provider_currency or None,
    }

    if attempt.status != "succeeded":
        issues.append(_issue(
            code=ISSUE_PROVIDER_SUCCEEDED_LOCAL_NOT_SUCCEEDED,
            severity="error",
            message="Provider reports succeeded payment while local attempt is not succeeded.",
            attempt=attempt,
            purchase_type=purchase_type,
            details=safe_details,
        ))

    if provider_amount is None or not _decimal_matches(attempt.amount, provider_amount) or attempt.currency != provider_currency:
        issues.append(_issue(
            code=ISSUE_PROVIDER_LOCAL_AMOUNT_MISMATCH,
            severity="error",
            message="Provider succeeded payment amount or currency differs from the local attempt.",
            attempt=attempt,
            purchase_type=purchase_type,
            details=safe_details,
        ))
    return issues


async def build_payment_reconciliation_report(
    db: AsyncSession,
    *,
    window_hours: int = DEFAULT_SCAN_WINDOW_HOURS,
    include_provider_checks: bool = False,
    limit: int = DEFAULT_ISSUE_LIMIT,
    yookassa_status_fetcher: Optional[YooKassaStatusFetcher] = None,
) -> PaymentReconciliationReport:
    now = _utc_now()
    normalized_window_hours = min(MAX_SCAN_WINDOW_HOURS, max(1, int(window_hours or DEFAULT_SCAN_WINDOW_HOURS)))
    normalized_limit = min(MAX_ISSUE_LIMIT, max(1, int(limit or DEFAULT_ISSUE_LIMIT)))
    pending_stale_before = now - timedelta(minutes=DEFAULT_PENDING_STALE_MINUTES)
    processing_stale_before = now - timedelta(minutes=DEFAULT_PROCESSING_STALE_MINUTES)
    provider_fetcher = _provider_fetcher(yookassa_status_fetcher) if include_provider_checks else None

    attempts = await _scan_attempts(db, window_hours=normalized_window_hours)
    issues: list[PaymentReconciliationIssue] = []

    async def add_issue(issue: Optional[PaymentReconciliationIssue]) -> bool:
        if issue is None:
            return len(issues) < normalized_limit
        if len(issues) < normalized_limit:
            issues.append(issue)
        return len(issues) < normalized_limit

    for attempt in attempts:
        purchase_type = _purchase_type(attempt)
        created_at = _as_utc(attempt.created_at) or now
        processing_started_at = _as_utc(attempt.processing_started_at) or _as_utc(attempt.updated_at) or created_at

        if attempt.checkout_state == "requires_reconciliation":
            if not await add_issue(_issue(
                code=ISSUE_CHECKOUT_REQUIRES_RECONCILIATION,
                severity="error",
                message="Payment attempt lost its purchase reference and requires manual reconciliation.",
                attempt=attempt,
                purchase_type=purchase_type,
                details={"checkout_state": "requires_reconciliation"},
            )):
                break

        if attempt.status == "pending" and created_at < pending_stale_before:
            if not await add_issue(_issue(
                code=ISSUE_PENDING_STALE,
                severity="warning",
                message="Payment attempt has been pending longer than the configured threshold.",
                attempt=attempt,
                purchase_type=purchase_type,
                details={"threshold_minutes": DEFAULT_PENDING_STALE_MINUTES},
            )):
                break

        if attempt.status == "processing" and processing_started_at < processing_stale_before:
            if not await add_issue(_issue(
                code=ISSUE_PROCESSING_STALE,
                severity="error",
                message="Payment attempt has been processing longer than the configured threshold.",
                attempt=attempt,
                purchase_type=purchase_type,
                details={"threshold_minutes": DEFAULT_PROCESSING_STALE_MINUTES},
            )):
                break

        if attempt.status == "succeeded":
            if not await add_issue(await _local_entitlement_issue(db, attempt, purchase_type)):
                break

        if provider_fetcher and attempt.provider == "yookassa" and attempt.provider_payment_id:
            try:
                provider_payment = provider_fetcher(attempt.provider_payment_id)
            except Exception as exc:
                logger.warning("Payment reconciliation provider check failed: provider=yookassa error_type=%s", type(exc).__name__)
                provider_payment = {}
            for provider_issue in _provider_issues(attempt, purchase_type, provider_payment):
                if not await add_issue(provider_issue):
                    break
            if len(issues) >= normalized_limit:
                break

    by_code = Counter(issue.code for issue in issues)
    by_severity = Counter(issue.severity for issue in issues)
    summary = PaymentReconciliationSummary(
        generated_at=now,
        window_hours=normalized_window_hours,
        total_attempts_scanned=len(attempts),
        total_issues=len(issues),
        provider_checks_included=bool(include_provider_checks),
        by_code=dict(by_code),
        by_severity=dict(by_severity),
    )
    return PaymentReconciliationReport(summary=summary, issues=issues)


async def build_payment_reconciliation_summary(
    db: AsyncSession,
    *,
    window_hours: int = DEFAULT_SCAN_WINDOW_HOURS,
    limit: int = DEFAULT_ISSUE_LIMIT,
) -> PaymentReconciliationSummary:
    report = await build_payment_reconciliation_report(
        db,
        window_hours=window_hours,
        include_provider_checks=False,
        limit=limit,
    )
    return report.summary
