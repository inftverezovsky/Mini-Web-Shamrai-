import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, func
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from typing import Optional, Any
from decimal import Decimal, ROUND_HALF_UP
import base64

from uuid import UUID
from src.models.database import get_db
from src.models.models import MatchBalanceLog, User, SubscriptionPlan, PaymentAttempt, PromoCode, PromoCodeRedemption, Bet, user_bets
from src.schemas.schemas import PaymentAttemptStatusResponse
from src.core.config import settings
from src.core.security import verify_telegram_webhook_secret
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.api.deps import get_current_user
from src.services.referrals import apply_referral_reward_for_purchase, get_referral_discount_percent
from src.services.match_access import (
    activate_match_subscription,
    current_match_balance,
    ensure_bet_eligible_for_user,
    load_locked_bet_for_user_access,
    lock_bet_row,
    lock_user_balance,
    lock_user_balances,
    record_user_bet_access,
)
from src.services.flat_subscriptions import (
    activate_flat_subscription_purchase,
    credit_flat_subscription,
    flat_subscription_payload,
    get_latest_flat_subscription,
)
from src.services.crowd_bets import apply_verified_crowd_contribution
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, enqueue_delivery
from src.services.observability_alerts import record_payment_mismatch
from src.services.system_settings import SUBSCRIPTION_PURCHASES_ENABLED_KEY, is_system_setting_enabled
from src.services.telegram_bot import call_telegram_api_async
from src.services.subscription_pricing import effective_subscription_price

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("uvicorn")

PAYMENT_PURCHASE_CROWD_BET = "crowd_bet"
PAYMENT_PURCHASE_BET_HINT = "bet_hint"
PAYMENT_PURCHASE_SINGLE_BET = "single_bet"
YOOKASSA_VERIFICATION_ERROR = "Не удалось проверить платеж YooKassa. Попробуйте позже."
YOOKASSA_CHECKOUT_ERROR = "Не удалось создать платеж YooKassa. Попробуйте позже."
TEGRO_CHECKOUT_ERROR = "Не удалось создать платеж Tegro. Попробуйте позже."
TELEGRAM_STARS_CHECKOUT_ERROR = "Не удалось создать счет Telegram Stars. Попробуйте позже."
SUBSCRIPTION_PURCHASES_DISABLED_ERROR = "Покупка абонементов временно отключена"
CHECKOUT_STATE_CREATING = "creating"
CHECKOUT_STATE_READY_TO_CREATE = "ready_to_create"
CHECKOUT_STATE_READY = "ready"
CHECKOUT_STATE_FAILED = "failed"
CHECKOUT_STATE_COMPLETED = "completed"
CHECKOUT_STATE_REQUIRES_RECONCILIATION = "requires_reconciliation"
TELEGRAM_CHECKOUT_CREATION_STALE_AFTER = timedelta(seconds=30)


def _ensure_flat_plan_purchase_allowed(plan: SubscriptionPlan) -> None:
    if str(plan.entitlement_type or "legacy_match") != "flat" or plan.target_flats is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Тариф недоступен для новой покупки")
PROMO_REWARD_DISCOUNT = "discount"
PROMO_REWARD_MATCHES = "matches"
PROMO_REWARD_FLATS = "flats"
PAYMENT_AUDIT_METADATA_KEYS = {"attempt_id", "purchase_type", "plan_id", "bet_id", "crowd_bet_id"}
PAYMENT_AUDIT_TOP_LEVEL_KEYS = {
    "amount",
    "currency",
    "id",
    "is_test",
    "metadata",
    "object",
    "operation_id",
    "order_id",
    "paid",
    "payment_id",
    "payment_system",
    "shop_id",
    "status",
    "test",
}


def _payment_audit_scalar(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {
            str(key): _payment_audit_scalar(nested_value)
            for key, nested_value in value.items()
            if isinstance(key, str) and key in {"value", "currency"}
        }
    return str(value)


def _processed_payment_payload(provider: str, raw_payload: dict[str, Any]) -> dict[str, Any]:
    sanitized: dict[str, Any] = {"provider": provider}
    for key, value in raw_payload.items():
        if key == "metadata" and isinstance(value, dict):
            metadata = {
                metadata_key: _payment_audit_scalar(metadata_value)
                for metadata_key, metadata_value in value.items()
                if metadata_key in PAYMENT_AUDIT_METADATA_KEYS
            }
            if metadata:
                sanitized["metadata"] = metadata
            continue
        if key in PAYMENT_AUDIT_TOP_LEVEL_KEYS:
            sanitized[key] = _payment_audit_scalar(value)
    return sanitized

class InvoiceRequest(BaseModel):
    plan_id: Optional[int] = None
    bet_id: Optional[UUID] = None
    promo_code: Optional[str] = None


class YooKassaPaymentRequest(BaseModel):
    plan_id: int
    promo_code: Optional[str] = None


class DebugYooKassaCompleteRequest(YooKassaPaymentRequest):
    attempt_id: Optional[UUID] = None


class TegroPaymentRequest(BaseModel):
    plan_id: int
    promo_code: Optional[str] = None


class PromoRedeemRequest(BaseModel):
    code: str = Field(min_length=1, max_length=80)


class DebugTegroCompleteRequest(TegroPaymentRequest):
    attempt_id: Optional[UUID] = None


class TegroWebhookSignatureError(ValueError):
    def __init__(self, code: str):
        super().__init__(f"tegro_webhook_{code}")
        self.code = code


class TelegramInvoiceCreationAmbiguousError(RuntimeError):
    """The invoice request may have reached Telegram but no link was returned."""


def _hidden_plan_allows_user(plan: SubscriptionPlan, user: User) -> bool:
    if not bool(getattr(plan, "is_hidden", False)):
        return False
    return int(user.telegram_id) in plan.allowed_user_ids


async def _ensure_subscription_purchases_enabled(
    db: AsyncSession,
    *,
    plan: Optional[SubscriptionPlan] = None,
    user: Optional[User] = None,
) -> None:
    if plan is not None and bool(getattr(plan, "is_hidden", False)):
        if user is None or not _hidden_plan_allows_user(plan, user):
            # Do not disclose the existence of a hidden launch-gate tariff.
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        if str(plan.entitlement_type or "legacy_match") == "flat":
            # The hidden allowlist is the explicit test-sale gate and remains
            # usable while public subscription purchases stay disabled.
            return
    if await is_system_setting_enabled(db, SUBSCRIPTION_PURCHASES_ENABLED_KEY):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=SUBSCRIPTION_PURCHASES_DISABLED_ERROR,
    )


def _telegram_text(value: str, max_length: int) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= max_length:
        return text
    return text[: max_length - 1].rstrip() + "…"


def _telegram_invoice_payload(attempt: PaymentAttempt) -> str:
    return str(attempt.id)


def _invoice_attempt_id(payload: Optional[str]) -> Optional[UUID]:
    raw_payload = (payload or "").strip()
    if not raw_payload:
        return None

    try:
        return UUID(raw_payload)
    except ValueError:
        pass

    try:
        metadata = json.loads(raw_payload)
        return UUID(str(metadata["attempt_id"]))
    except Exception:
        return None


def _telegram_sender_id(container: Any) -> Optional[int]:
    if not isinstance(container, dict):
        return None
    sender = container.get("from")
    if not isinstance(sender, dict):
        return None
    raw_sender_id = sender.get("id")
    if isinstance(raw_sender_id, bool) or not isinstance(raw_sender_id, int) or raw_sender_id <= 0:
        return None
    return int(raw_sender_id)


def _request_yookassa_payment(payment_id: str) -> dict:
    if not settings.has_yookassa_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YooKassa credentials are not configured",
        )

    auth_raw = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
    headers = {
        "Authorization": "Basic " + base64.b64encode(auth_raw).decode("ascii"),
    }
    req = urllib.request.Request(
        f"https://api.yookassa.ru/v3/payments/{payment_id}",
        headers=headers,
        method="GET",
    )
    try:
        with _open_payment_provider_request(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("YooKassa payment verification HTTP error: status=%s", e.code)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=YOOKASSA_VERIFICATION_ERROR,
        )
    except Exception as e:
        logger.warning("YooKassa payment verification failed: error_type=%s", type(e).__name__)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=YOOKASSA_VERIFICATION_ERROR,
        )


def _open_payment_provider_request(req: urllib.request.Request, timeout: float = 15):
    # Telegram may need a global HTTPS proxy on this VDS, but payment providers should go direct.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(req, timeout=timeout)


def _tegro_api_url(path: str) -> str:
    base = (settings.TEGRO_API_BASE_URL or "https://tegro.money/api").strip().rstrip("/")
    clean_path = path if path.startswith("/") else f"/{path}"
    return f"{base}{clean_path}"


def _tegro_sign_json_body(json_body: str, api_key: str) -> str:
    return hmac.new(api_key.encode("utf-8"), json_body.encode("utf-8"), hashlib.sha256).hexdigest()


def _tegro_payment_form_signature(fields: dict[str, str], secret_key: str) -> str:
    sign_fields = {
        key: fields[key]
        for key in ("shop_id", "amount", "currency", "order_id")
        if key in fields
    }
    if fields.get("test") == "1":
        sign_fields["test"] = fields["test"]
    query = _tegro_signature_query(sign_fields)
    return hashlib.md5((query + secret_key).encode("utf-8")).hexdigest().lower()


def _create_tegro_payment_url(payload: dict[str, Any]) -> str:
    fields: dict[str, str] = {
        "shop_id": str(payload["shop_id"]),
        "amount": str(payload["amount"]),
        "order_id": str(payload["order_id"]),
        "lang": str(payload.get("lang") or "ru"),
        "currency": str(payload.get("currency") or "RUB"),
    }
    if payload.get("test") is not None:
        fields["test"] = str(payload["test"])
    receipt = payload.get("receipt") or {}
    items = receipt.get("items") if isinstance(receipt, dict) else None
    if isinstance(items, list):
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                continue
            prefix = f"receipt[items][{index}]"
            fields[f"{prefix}[name]"] = str(item.get("name") or "")
            fields[f"{prefix}[count]"] = str(item.get("count") or 1)
            fields[f"{prefix}[price]"] = str(item.get("price") or fields["amount"])

    fields["sign"] = _tegro_payment_form_signature(fields, settings.TEGRO_SECRET_KEY)
    return "https://tegro.money/pay/?" + urllib.parse.urlencode(
        [(key, fields[key]) for key in fields],
        doseq=False,
        quote_via=urllib.parse.quote_plus,
    )


def _create_tegro_order(payload: dict[str, Any]) -> dict[str, Any]:
    if not settings.has_tegro_api_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tegro checkout is not configured",
        )

    json_body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    sign = _tegro_sign_json_body(json_body, settings.TEGRO_API_KEY)
    req = urllib.request.Request(
        _tegro_api_url("/createOrder/"),
        data=json_body.encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {sign}",
        },
        method="POST",
    )

    try:
        with _open_payment_provider_request(req, timeout=15) as response:
            envelope = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        logger.warning("Tegro checkout HTTP error: status=%s", e.code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TEGRO_CHECKOUT_ERROR,
        )
    except Exception as e:
        logger.warning("Tegro checkout request failed: error_type=%s", type(e).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=TEGRO_CHECKOUT_ERROR)

    if envelope.get("type") != "success" or not isinstance(envelope.get("data"), dict):
        logger.warning(
            "Tegro checkout returned unexpected envelope: type=%s has_data=%s",
            envelope.get("type"),
            isinstance(envelope.get("data"), dict),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TEGRO_CHECKOUT_ERROR,
        )
    data = envelope["data"]
    if not data.get("url"):
        logger.warning("Tegro checkout returned success envelope without payment URL")
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=TEGRO_CHECKOUT_ERROR)
    return data


def _tegro_signature_query(fields: dict[str, str]) -> str:
    return urllib.parse.urlencode(
        [(key, fields[key]) for key in sorted(fields)],
        doseq=False,
        quote_via=urllib.parse.quote_plus,
    )


def _verify_tegro_notification(raw_fields: dict[str, Any]) -> dict[str, Any]:
    provided_sign = str(raw_fields.get("sign") or "").strip().lower()
    if not provided_sign:
        raise TegroWebhookSignatureError("missing_sign")

    fields_for_sign: dict[str, str] = {}
    for key, value in raw_fields.items():
        if key == "sign" or value is None:
            continue
        fields_for_sign[str(key)] = str(value)

    query = _tegro_signature_query(fields_for_sign)
    expected = hashlib.md5((query + settings.TEGRO_SECRET_KEY).encode("utf-8")).hexdigest().lower()
    if not hmac.compare_digest(expected, provided_sign):
        raise TegroWebhookSignatureError("bad_signature")

    order_id = str(raw_fields.get("order_id") or "").strip()
    amount_str = str(raw_fields.get("amount") or "").strip()
    if not order_id or not amount_str:
        raise TegroWebhookSignatureError("missing_required_fields")

    try:
        amount = Decimal(amount_str)
    except Exception:
        raise TegroWebhookSignatureError("bad_amount")
    if amount <= 0:
        raise TegroWebhookSignatureError("bad_amount")

    return {
        "shop_id": str(raw_fields.get("shop_id") or ""),
        "amount": amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        "order_id": order_id,
        "payment_system": str(raw_fields.get("payment_system") or ""),
        "currency": str(raw_fields.get("currency") or "RUB"),
        "payment_id": str(raw_fields.get("payment_id") or "") or None,
        "is_test": str(raw_fields.get("test") or "").lower() in {"1", "true"},
        "raw": raw_fields,
    }


def _decimal_eq(left: Decimal, right: Decimal) -> bool:
    return left.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) == right.quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )


def _promo_reward_type(promo: PromoCode) -> str:
    return promo.reward_type or PROMO_REWARD_DISCOUNT


async def _load_active_promo(db: AsyncSession, code: str) -> PromoCode:
    normalized = code.strip().upper()
    promo_res = await db.execute(
        select(PromoCode).filter(
            PromoCode.code == normalized,
            PromoCode.is_active == True,
            PromoCode.valid_until > datetime.now(timezone.utc),
        )
    )
    promo = promo_res.scalars().first()
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный или истекший промокод",
        )
    return promo


def _ensure_promo_available_for_user(promo: PromoCode, user: User) -> None:
    if promo.user_id is not None and promo.user_id != user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод привязан к другому пользователю",
        )


async def _validate_promo(
    db: AsyncSession,
    *,
    promo_code: Optional[str],
    user: User,
) -> tuple[Optional[str], int]:
    if not promo_code:
        return None, 0

    normalized = promo_code.strip().upper()
    promo = await _load_active_promo(db, normalized)
    _ensure_promo_available_for_user(promo, user)
    if _promo_reward_type(promo) != PROMO_REWARD_DISCOUNT:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод начисляет матчи и не применяется как скидка",
        )
    return normalized, int(promo.discount_percent or 0)


def _apply_percent_discount(
    amount: Decimal,
    discount_percent: int,
    *,
    minimum: Decimal,
    quantum: Decimal = Decimal("0.01"),
) -> Decimal:
    if discount_percent <= 0:
        return amount.quantize(quantum, rounding=ROUND_HALF_UP)
    discounted = amount * Decimal(100 - discount_percent) / Decimal(100)
    return max(minimum, discounted.quantize(quantum, rounding=ROUND_HALF_UP))


def _checkout_request_hash(
    *,
    provider: str,
    user_id: int,
    plan_id: Optional[int] = None,
    bet_id: Optional[UUID] = None,
    promo_code: Optional[str] = None,
    purchase_type: Optional[str] = None,
    crowd_bet_id: Optional[int] = None,
    amount_xtr: Optional[int] = None,
) -> str:
    canonical_payload = {
        "provider": provider,
        "user_id": int(user_id),
        "plan_id": int(plan_id) if plan_id is not None else None,
        "bet_id": str(bet_id) if bet_id is not None else None,
        "promo_code": promo_code.strip().upper() if promo_code else None,
    }
    if purchase_type is not None:
        canonical_payload["purchase_type"] = str(purchase_type)
    if crowd_bet_id is not None:
        canonical_payload["crowd_bet_id"] = int(crowd_bet_id)
    if amount_xtr is not None:
        canonical_payload["amount_xtr"] = int(amount_xtr)
    serialized = json.dumps(canonical_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _ensure_matching_checkout_payload(attempt: PaymentAttempt, payload_hash: str) -> None:
    if attempt.checkout_payload_hash == payload_hash:
        return
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail="Idempotency-Key уже использован для другого платежа",
    )


async def _load_checkout_attempt(
    db: AsyncSession,
    *,
    user_id: int,
    provider: str,
    checkout_intent_id: UUID,
    payload_hash: str,
    for_update: bool = False,
) -> Optional[PaymentAttempt]:
    query = select(PaymentAttempt).filter(
        PaymentAttempt.user_id == user_id,
        PaymentAttempt.provider == provider,
        PaymentAttempt.checkout_intent_id == checkout_intent_id,
    )
    if for_update:
        query = query.with_for_update()
    result = await db.execute(query)
    attempt = result.scalars().first()
    if attempt is not None:
        _ensure_matching_checkout_payload(attempt, payload_hash)
    return attempt


async def _lock_telegram_purchase_scope(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    purchase_type: str,
) -> None:
    """Serialize owner-scoped Stars checkout discovery before Bet locking."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    lock_material = f"telegram_stars:{int(user_id)}:{bet_id}:{purchase_type}".encode("utf-8")
    lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def _lock_telegram_crowd_purchase_scope(
    db: AsyncSession,
    *,
    user_id: int,
    crowd_bet_id: int,
) -> None:
    """Serialize active Stars invoices for one user and crowd pool."""
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    lock_material = (
        f"telegram_stars:{int(user_id)}:crowd_bet:{int(crowd_bet_id)}"
    ).encode("utf-8")
    lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
    await db.execute(select(func.pg_advisory_xact_lock(lock_key)))


async def _load_owner_active_telegram_attempts(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    purchase_type: str,
) -> list[PaymentAttempt]:
    result = await db.execute(
        select(PaymentAttempt)
        .filter(
            PaymentAttempt.user_id == user_id,
            PaymentAttempt.provider == "telegram_stars",
            PaymentAttempt.bet_id == bet_id,
            PaymentAttempt.purchase_type_snapshot == purchase_type,
            PaymentAttempt.status.in_(("pending", "processing")),
        )
        .order_by(PaymentAttempt.id)
        .with_for_update()
    )
    return list(result.scalars().all())


async def _recover_owner_active_telegram_attempt(
    db: AsyncSession,
    *,
    user_id: int,
    bet_id: UUID,
    purchase_type: str,
    payload_hash: str,
) -> Optional[PaymentAttempt]:
    attempts = await _load_owner_active_telegram_attempts(
        db,
        user_id=user_id,
        bet_id=bet_id,
        purchase_type=purchase_type,
    )
    if len(attempts) > 1:
        for candidate in attempts:
            candidate.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Найдено несколько активных счетов. Требуется ручная сверка",
        )
    if not attempts:
        return None

    attempt = attempts[0]
    existing_payload_hash = attempt.checkout_payload_hash or _checkout_request_hash(
        provider="telegram_stars",
        user_id=attempt.user_id,
        bet_id=attempt.bet_id,
        promo_code=attempt.promo_code,
    )
    if existing_payload_hash != payload_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Активный счет создан с другими параметрами покупки",
        )
    if attempt.checkout_payload_hash is None:
        attempt.checkout_payload_hash = existing_payload_hash
        await db.flush()
    return attempt


async def _recover_owner_active_telegram_crowd_attempt(
    db: AsyncSession,
    *,
    user_id: int,
    crowd_bet_id: int,
    payload_hash: str,
) -> Optional[PaymentAttempt]:
    result = await db.execute(
        select(PaymentAttempt)
        .filter(
            PaymentAttempt.user_id == user_id,
            PaymentAttempt.provider == "telegram_stars",
            PaymentAttempt.crowd_bet_id_snapshot == crowd_bet_id,
            PaymentAttempt.purchase_type_snapshot == PAYMENT_PURCHASE_CROWD_BET,
            PaymentAttempt.status.in_(("pending", "processing")),
        )
        .order_by(PaymentAttempt.id)
        .with_for_update()
    )
    attempts = list(result.scalars().all())
    if len(attempts) > 1:
        for candidate in attempts:
            candidate.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Найдено несколько активных счетов. Требуется ручная сверка",
        )
    if not attempts:
        return None

    attempt = attempts[0]
    if not attempt.checkout_payload_hash:
        attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Активный счет требует ручной сверки",
        )
    if attempt.checkout_payload_hash != payload_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Активный счет создан для другой суммы вклада",
        )
    return attempt


async def _reload_checkout_attempt_for_update(
    db: AsyncSession,
    attempt_id: UUID,
) -> PaymentAttempt:
    result = await db.execute(
        select(PaymentAttempt)
        .filter(PaymentAttempt.id == attempt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    attempt = result.scalars().first()
    if attempt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платеж не найден")
    return attempt


def _checkout_replay_response(attempt: PaymentAttempt, *, url_field: str) -> dict[str, Any]:
    return {
        "payment_id": attempt.provider_payment_id or str(attempt.id),
        "attempt_id": str(attempt.id),
        url_field: attempt.checkout_url,
        "status": attempt.status,
        "checkout_state": attempt.checkout_state,
        "discount_percent": int(attempt.discount_percent_snapshot or 0),
        "idempotent_replay": True,
        **({"mock": True} if attempt.provider.endswith("_debug") else {}),
    }


def _checkout_creation_can_resume(attempt: PaymentAttempt) -> bool:
    """Return whether a durable checkout intent is safe to finish creating.

    A successful checkout URL is persisted before the endpoint responds.  Thus
    an attempt left in this exact state represents a crash before the caller
    could receive a URL.  Provider/payment activity is deliberately excluded
    from recovery and must go through reconciliation instead.
    """
    return bool(
        attempt.checkout_state == CHECKOUT_STATE_CREATING
        and not attempt.checkout_url
        and attempt.status == "pending"
        and not attempt.provider_payment_id
    )


def _telegram_checkout_ready_to_claim(attempt: PaymentAttempt) -> bool:
    return bool(
        attempt.provider == "telegram_stars"
        and attempt.checkout_state == CHECKOUT_STATE_READY_TO_CREATE
        and not attempt.checkout_url
        and attempt.status == "pending"
        and not attempt.provider_payment_id
    )


def _telegram_checkout_claim_is_stale(attempt: PaymentAttempt) -> bool:
    started_at = attempt.checkout_creation_started_at
    if started_at is None:
        return True
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - started_at >= TELEGRAM_CHECKOUT_CREATION_STALE_AFTER


async def _telegram_checkout_replay_or_wait(
    db: AsyncSession,
    attempt: PaymentAttempt,
) -> Optional[dict[str, Any]]:
    """Resolve a durable Stars attempt without ever repeating createInvoiceLink."""
    if _telegram_checkout_ready_to_claim(attempt):
        return None

    is_unfinished_claim = bool(
        attempt.provider == "telegram_stars"
        and attempt.checkout_state == CHECKOUT_STATE_CREATING
        and not attempt.checkout_url
        and attempt.status == "pending"
        and not attempt.provider_payment_id
    )
    if is_unfinished_claim and not _telegram_checkout_claim_is_stale(attempt):
        # Another request may still be inside Telegram's short API timeout.
        # Never turn a concurrent replay into a second non-idempotent call.
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_425_TOO_EARLY,
            detail="Счет еще создается. Повторите проверку через несколько секунд",
            headers={"Retry-After": "3"},
        )

    if is_unfinished_claim:
        attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        logger.warning(
            "Telegram Stars invoice creation claim quarantined without provider retry: attempt_id=%s",
            attempt.id,
        )

    return _checkout_replay_response(attempt, url_field="invoice_url")


async def _create_payment_attempt(
    db: AsyncSession,
    *,
    user: User,
    provider: str,
    amount: Decimal,
    currency: str,
    plan: Optional[SubscriptionPlan] = None,
    plan_id: Optional[int] = None,
    bet_id: Optional[UUID] = None,
    promo_code: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    checkout_intent_id: Optional[UUID] = None,
    checkout_payload_hash: Optional[str] = None,
    discount_percent: int = 0,
    crowd_bet_id_snapshot: Optional[int] = None,
) -> PaymentAttempt:
    if plan is not None:
        if plan_id is not None and int(plan_id) != int(plan.id):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Тариф платежа не совпадает")
        plan_id = int(plan.id)
    if bool(plan_id) == bool(bet_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Платеж должен быть привязан либо к тарифу, либо к прогнозу",
        )

    attempt_metadata = {**(metadata or {})}
    purchase_type_snapshot = None
    if bet_id is not None:
        purchase_type_snapshot = str(
            attempt_metadata.get("purchase_type") or PAYMENT_PURCHASE_SINGLE_BET
        )

    attempt = PaymentAttempt(
        user_id=user.telegram_id,
        plan_id=plan_id,
        bet_id=bet_id,
        provider=provider,
        amount=amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        currency=currency,
        promo_code=promo_code,
        checkout_intent_id=checkout_intent_id,
        checkout_payload_hash=checkout_payload_hash,
        checkout_state=(
            CHECKOUT_STATE_READY_TO_CREATE
            if checkout_intent_id is not None and provider == "telegram_stars"
            else CHECKOUT_STATE_CREATING
            if checkout_intent_id is not None
            else "legacy"
        ),
        purchase_type_snapshot=purchase_type_snapshot,
        crowd_bet_id_snapshot=(
            int(crowd_bet_id_snapshot)
            if crowd_bet_id_snapshot is not None
            else None
        ),
        plan_name_snapshot=plan.name if plan is not None else None,
        entitlement_type_snapshot=(str(plan.entitlement_type or "legacy_match") if plan is not None else None),
        target_flats_snapshot=(
            Decimal(str(plan.target_flats)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if plan is not None and plan.target_flats is not None
            else None
        ),
        match_count_snapshot=(int(plan.match_count or 0) if plan is not None else None),
        discount_percent_snapshot=max(0, min(100, int(discount_percent or 0))),
        metadata_json=attempt_metadata,
        status="pending",
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def _create_or_reuse_checkout_attempt(
    db: AsyncSession,
    *,
    user: User,
    provider: str,
    checkout_intent_id: UUID,
    checkout_payload_hash: str,
    amount: Decimal,
    currency: str,
    plan: Optional[SubscriptionPlan] = None,
    bet_id: Optional[UUID] = None,
    promo_code: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
    discount_percent: int = 0,
) -> tuple[PaymentAttempt, bool]:
    existing = await _load_checkout_attempt(
        db,
        user_id=user.telegram_id,
        provider=provider,
        checkout_intent_id=checkout_intent_id,
        payload_hash=checkout_payload_hash,
        for_update=True,
    )
    if existing is not None:
        return existing, False

    try:
        async with db.begin_nested():
            attempt = await _create_payment_attempt(
                db,
                user=user,
                provider=provider,
                amount=amount,
                currency=currency,
                plan=plan,
                bet_id=bet_id,
                promo_code=promo_code,
                metadata=metadata,
                checkout_intent_id=checkout_intent_id,
                checkout_payload_hash=checkout_payload_hash,
                discount_percent=discount_percent,
            )
        return attempt, True
    except IntegrityError:
        existing = await _load_checkout_attempt(
            db,
            user_id=user.telegram_id,
            provider=provider,
            checkout_intent_id=checkout_intent_id,
            payload_hash=checkout_payload_hash,
            for_update=True,
        )
        if existing is None:
            raise
        return existing, False


async def create_telegram_stars_invoice_link(
    *,
    attempt: PaymentAttempt,
    title: str,
    description: str,
    label: str,
) -> str:
    if settings.DEBUG_MODE:
        return f"https://t.me/invoice/mock_stars_attempt_{attempt.id}"

    if not settings.has_real_telegram_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Stars billing is not configured",
        )

    tg_payload = {
        "title": _telegram_text(title, 32),
        "description": _telegram_text(description, 255),
        "payload": _telegram_invoice_payload(attempt),
        "provider_token": "",
        "currency": "XTR",
        "prices": [{
            "label": _telegram_text(label, 32),
            "amount": int(Decimal(attempt.amount)),
        }],
    }

    # createInvoiceLink has no provider idempotency key. Never retry it inside
    # the transport: a lost response must be quarantined for reconciliation.
    res = await call_telegram_api_async("createInvoiceLink", tg_payload, retries=0)
    if not res.get("ok"):
        logger.warning(
            "Telegram Stars invoice link creation failed: error_code=%s has_description=%s",
            res.get("error_code"),
            bool(res.get("description")),
        )
        error_code = res.get("error_code")
        http_status = res.get("http_status")
        definitive_code = (
            error_code
            if isinstance(error_code, int) and not isinstance(error_code, bool)
            else http_status
            if isinstance(http_status, int) and not isinstance(http_status, bool)
            else None
        )
        if definitive_code is not None and 400 <= definitive_code < 500:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=TELEGRAM_STARS_CHECKOUT_ERROR)
        raise TelegramInvoiceCreationAmbiguousError("telegram_invoice_response_ambiguous")
    return res["result"]


async def _create_or_replay_telegram_checkout_url(
    db: AsyncSession,
    *,
    attempt: PaymentAttempt,
    title: str,
    description: str,
    label: str,
    invoice_creator: Optional[Any] = None,
) -> PaymentAttempt:
    """Claim one non-idempotent Telegram invoice call and persist its outcome.

    Callers must persist a ``ready_to_create`` attempt before entering here.
    A provider response that may have been lost is quarantined permanently; it
    is never interpreted as permission to create a replacement invoice.
    """
    attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    replay = await _telegram_checkout_replay_or_wait(db, attempt)
    if replay is not None:
        await db.commit()
        return attempt

    attempt.checkout_state = CHECKOUT_STATE_CREATING
    attempt.checkout_creation_started_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        create_invoice = invoice_creator or create_telegram_stars_invoice_link
        invoice_url = await create_invoice(
            attempt=attempt,
            title=title,
            description=description,
            label=label,
        )
    except HTTPException:
        attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if (
            attempt.checkout_state == CHECKOUT_STATE_CREATING
            and attempt.status == "pending"
            and not attempt.checkout_url
            and not attempt.provider_payment_id
        ):
            attempt.status = "failed"
            attempt.checkout_state = CHECKOUT_STATE_FAILED
        await db.commit()
        raise
    except Exception as exc:
        attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if (
            attempt.checkout_state == CHECKOUT_STATE_CREATING
            and attempt.status == "pending"
            and not attempt.checkout_url
            and not attempt.provider_payment_id
        ):
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        logger.warning(
            "Telegram Stars invoice creation requires reconciliation: attempt_id=%s error_type=%s",
            attempt.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TELEGRAM_STARS_CHECKOUT_ERROR,
        ) from exc

    attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    if not (
        attempt.checkout_state == CHECKOUT_STATE_CREATING
        and attempt.status == "pending"
        and not attempt.checkout_url
        and not attempt.provider_payment_id
    ):
        attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        logger.error(
            "Telegram Stars invoice link returned after checkout state changed: attempt_id=%s",
            attempt.id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TELEGRAM_STARS_CHECKOUT_ERROR,
        )
    attempt.checkout_url = invoice_url
    attempt.checkout_state = CHECKOUT_STATE_READY
    await db.commit()
    return attempt


async def _unlock_single_bet(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    access_type: str,
) -> bool:
    access_result = await record_user_bet_access(
        db,
        user=user,
        bet=bet,
        charge_match=False,
        free_access_type=access_type,
    )
    if not access_result.already_recorded and not user.has_used_shield:
        user.has_used_shield = True
    return not access_result.already_recorded


def _subscription_plan_snapshot(attempt: PaymentAttempt) -> Optional[SimpleNamespace]:
    has_snapshot = bool(
        attempt.plan_name_snapshot
        or attempt.entitlement_type_snapshot
        or attempt.target_flats_snapshot is not None
        or attempt.match_count_snapshot is not None
    )
    if attempt.plan_id is None and not has_snapshot:
        return None

    plan_name = str(attempt.plan_name_snapshot or "").strip()
    entitlement_type = str(attempt.entitlement_type_snapshot or "").strip()
    if not plan_name or entitlement_type not in {"flat", "legacy_match"}:
        return None
    if entitlement_type == "flat" and attempt.target_flats_snapshot is None:
        return None
    if entitlement_type == "legacy_match" and attempt.match_count_snapshot is None:
        return None
    return SimpleNamespace(
        id=attempt.plan_id,
        name=plan_name,
        entitlement_type=entitlement_type,
        target_flats=attempt.target_flats_snapshot,
        match_count=attempt.match_count_snapshot,
    )


async def _process_payment_attempt(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    provider: str,
    provider_payment_id: str,
    amount: Decimal,
    currency: str,
    raw_payload: Optional[dict[str, Any]] = None,
    payer_user_id: Optional[int] = None,
) -> dict[str, Any]:
    attempt_res = await db.execute(
        select(PaymentAttempt)
        .filter(PaymentAttempt.id == attempt_id)
        .options(
            selectinload(PaymentAttempt.user),
            selectinload(PaymentAttempt.plan),
            selectinload(PaymentAttempt.bet),
        )
        .with_for_update()
    )
    attempt = attempt_res.scalars().first()
    if not attempt:
        return {"status": "attempt_missing"}

    if attempt.provider != provider:
        record_payment_mismatch(provider, "provider_mismatch", attempt_id=attempt.id, payment_id=provider_payment_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment provider mismatch")

    is_telegram_stars = provider == "telegram_stars"
    if is_telegram_stars:
        if payer_user_id is None or int(payer_user_id) != int(attempt.user_id):
            record_payment_mismatch(
                provider,
                "payer_mismatch",
                attempt_id=attempt.id,
                payment_id=provider_payment_id,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment payer mismatch")
        if (
            attempt.telegram_pre_checkout_user_id is not None
            and int(attempt.telegram_pre_checkout_user_id) != int(payer_user_id)
        ):
            record_payment_mismatch(
                provider,
                "reserved_payer_mismatch",
                attempt_id=attempt.id,
                payment_id=provider_payment_id,
            )
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment payer mismatch")

    if attempt.provider_payment_id and attempt.provider_payment_id != provider_payment_id:
        record_payment_mismatch(provider, "payment_id_mismatch", attempt_id=attempt.id, payment_id=provider_payment_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment id mismatch")

    if attempt.currency != currency:
        record_payment_mismatch(provider, "currency_mismatch", attempt_id=attempt.id, payment_id=provider_payment_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment currency mismatch")

    if not _decimal_eq(Decimal(attempt.amount), amount):
        record_payment_mismatch(provider, "amount_mismatch", attempt_id=attempt.id, payment_id=provider_payment_id)
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment amount mismatch")

    if attempt.status == "succeeded":
        return {"status": "already_processed", "attempt_id": str(attempt.id)}

    is_reserved_telegram_continuation = bool(
        is_telegram_stars
        and attempt.status == "processing"
        and attempt.telegram_pre_checkout_query_id
        and attempt.telegram_pre_checkout_user_id is not None
        and int(attempt.telegram_pre_checkout_user_id) == int(payer_user_id)
    )
    if attempt.status not in ("pending", "failed") and not is_reserved_telegram_continuation:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Payment attempt is {attempt.status}")

    # Preserve compatibility for an invoice created before reservation fields
    # existed, while still binding any successful payment to its actual owner.
    if is_telegram_stars and attempt.telegram_pre_checkout_user_id is None:
        attempt.telegram_pre_checkout_user_id = int(payer_user_id)
        attempt.telegram_pre_checkout_reserved_at = datetime.now(timezone.utc)

    attempt.status = "processing"
    attempt.processing_started_at = attempt.processing_started_at or datetime.now(timezone.utc)
    attempt.provider_payment_id = provider_payment_id

    async def quarantine_charged_attempt(reason: str) -> dict[str, Any]:
        attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        if raw_payload is not None:
            attempt.metadata_json = {
                **(attempt.metadata_json or {}),
                "processed_payload": _processed_payment_payload(provider, raw_payload),
            }
        record_payment_mismatch(
            provider,
            reason,
            attempt_id=attempt.id,
            payment_id=provider_payment_id,
        )
        await db.flush()
        return {"status": reason, "attempt_id": str(attempt.id)}

    user = attempt.user
    if not user:
        return await quarantine_charged_attempt("user_missing")

    result: dict[str, Any] = {
        "status": "success",
        "attempt_id": str(attempt.id),
        "user_id": user.telegram_id,
    }

    bet: Optional[Bet] = None
    plan = _subscription_plan_snapshot(attempt)
    has_plan_reference = bool(
        attempt.plan_id is not None
        or attempt.plan_name_snapshot
        or attempt.entitlement_type_snapshot
        or attempt.target_flats_snapshot is not None
        or attempt.match_count_snapshot is not None
    )
    if has_plan_reference and attempt.bet_id is not None:
        return await quarantine_charged_attempt("ambiguous_purchase_snapshot")
    if plan is None:
        if has_plan_reference:
            return await quarantine_charged_attempt("plan_snapshot_missing")
        if attempt.bet_id:
            bet = attempt.bet
            if not bet:
                return await quarantine_charged_attempt("bet_missing")
        else:
            return await quarantine_charged_attempt("attempt_has_no_item")

    # Canonical mutation lock order: PaymentAttempt -> Bet (when present) ->
    # affected User rows in ascending id order -> FlatSubscription -> UserBet
    # rows in stable identifier order. Paid entitlements are honored after a
    # successful provider charge even if lifecycle changes after checkout.
    if bet is not None:
        await lock_bet_row(db, bet.id)
    affected_user_ids = {int(user.telegram_id)}
    if user.referred_by_user_id is not None:
        affected_user_ids.add(int(user.referred_by_user_id))
    await lock_user_balances(db, affected_user_ids)

    await db.flush()

    if plan:
        if str(plan.entitlement_type or "legacy_match") == "flat":
            subscription = await activate_flat_subscription_purchase(
                db,
                user=user,
                plan=plan,
                payment_provider=provider,
                payment_id=provider_payment_id,
            )
            flat_progress = await get_latest_flat_subscription(db, user.telegram_id)
            result.update({
                "plan_id": plan.id,
                "subscription_id": str(subscription.id),
                "target_flats_added": str(plan.target_flats),
                "flat_setup_required": bool(
                    subscription.status == "pending_setup"
                    and flat_progress is not None
                    and flat_progress.id == subscription.flat_subscription_id
                    and flat_progress.flat_amount_rub is None
                ),
            })
        else:
            subscription = await activate_match_subscription(
                db,
                user=user,
                plan=plan,
                payment_provider=provider,
                payment_id=provider_payment_id,
            )
            result.update({
                "plan_id": plan.id,
                "subscription_id": str(subscription.id),
                "matches_added": plan.match_count,
            })
        referral_event = await apply_referral_reward_for_purchase(
            db,
            referred_user=user,
            payment_attempt=attempt,
            source_type="subscription",
        )
        if referral_event:
            result["referral_reward_matches"] = referral_event.matches_awarded
            result["referral_reward_target_flats"] = str(referral_event.target_flats_awarded or 0)
    elif bet:
        purchase_type = str(attempt.purchase_type_snapshot or "")
        if purchase_type not in {
            PAYMENT_PURCHASE_SINGLE_BET,
            PAYMENT_PURCHASE_BET_HINT,
            PAYMENT_PURCHASE_CROWD_BET,
        }:
            return await quarantine_charged_attempt("purchase_snapshot_missing")
        if purchase_type == PAYMENT_PURCHASE_CROWD_BET:
            crowd_bet_id = int(attempt.crowd_bet_id_snapshot or 0)
            if crowd_bet_id <= 0:
                return await quarantine_charged_attempt("crowd_bet_snapshot_missing")
            crowd_result = await apply_verified_crowd_contribution(
                db,
                user=user,
                crowd_bet_id=crowd_bet_id,
                amount_xtr=amount,
            )
            result.update(crowd_result)
        elif purchase_type == PAYMENT_PURCHASE_BET_HINT:
            result.update({
                "bet_id": str(bet.id),
                "purchase_type": PAYMENT_PURCHASE_BET_HINT,
                "hint_ready": True,
            })
        else:
            unlocked = await _unlock_single_bet(
                db,
                user=user,
                bet=bet,
                access_type=f"{provider}_single_bet",
            )
            result.update({"bet_id": str(bet.id), "already_unlocked": not unlocked})
            referral_event = await apply_referral_reward_for_purchase(
                db,
                referred_user=user,
                payment_attempt=attempt,
                source_type="single_bet",
            )
            if referral_event:
                result["referral_reward_matches"] = referral_event.matches_awarded
                result["referral_reward_target_flats"] = str(referral_event.target_flats_awarded or 0)

    attempt.status = "succeeded"
    attempt.checkout_state = CHECKOUT_STATE_COMPLETED
    attempt.processed_at = datetime.now(timezone.utc)
    if raw_payload is not None:
        attempt.metadata_json = {
            **(attempt.metadata_json or {}),
            "processed_payload": _processed_payment_payload(provider, raw_payload),
        }
    await db.flush()
    return result


def _payment_confirmation_text(result: dict[str, Any]) -> str:
    if result.get("target_flats_added") is not None:
        suffix = " Укажите размер одного флета в приложении." if result.get("flat_setup_required") else ""
        return f"✅ Абонемент оформлен: цель +{result['target_flats_added']} флета.{suffix}"
    if result.get("matches_added") is not None:
        return f"✅ Абонемент успешно оформлен: +{result['matches_added']} матчей!"
    if result.get("purchase_type") == PAYMENT_PURCHASE_BET_HINT:
        return "✅ Подсказка оплачена. Вернитесь в приложение, чтобы открыть аналитику."
    if result.get("crowd_bet_id"):
        return "✅ Вклад в складчину засчитан."
    if result.get("bet_id"):
        return "✅ Прогноз успешно разблокирован!"
    return "✅ Платеж успешно обработан!"


async def _enqueue_payment_confirmation(db: AsyncSession, result: dict[str, Any]) -> None:
    if result.get("status") != "success":
        return
    user_id = result.get("user_id")
    attempt_id = result.get("attempt_id")
    if not attempt_id or not is_personal_telegram_user_id(user_id):
        return
    await enqueue_delivery(
        db,
        channel=CHANNEL_TELEGRAM_MESSAGE,
        user_id=int(user_id),
        dedupe_key=f"payment_attempt:{attempt_id}:telegram_confirmation",
        payload={
            "method": "sendMessage",
            "payload": {
                "chat_id": int(user_id),
                "text": _payment_confirmation_text(result),
            },
        },
    )


@router.get("/attempts/{attempt_id}", response_model=PaymentAttemptStatusResponse)
async def get_payment_attempt_status(
    attempt_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PaymentAttemptStatusResponse:
    result = await db.execute(
        select(PaymentAttempt)
        .options(selectinload(PaymentAttempt.plan))
        .filter(
            PaymentAttempt.id == attempt_id,
            PaymentAttempt.user_id == current_user.telegram_id,
        )
    )
    attempt = result.scalars().first()
    if attempt is None:
        # Hide whether a foreign attempt exists.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Платеж не найден")

    entitlement_type = attempt.entitlement_type_snapshot or (
        str(attempt.plan.entitlement_type or "legacy_match") if attempt.plan is not None else None
    )
    flat_payload = None
    if entitlement_type == "flat":
        flat_subscription = await get_latest_flat_subscription(db, current_user.telegram_id)
        if flat_subscription is not None:
            flat_payload = await flat_subscription_payload(db, flat_subscription)

    metadata = attempt.metadata_json or {}
    purchase_type = "subscription" if (attempt.plan_id is not None or entitlement_type is not None) else str(
        metadata.get("purchase_type") or ("single_bet" if attempt.bet_id is not None else "unknown")
    )
    return PaymentAttemptStatusResponse(
        attempt_id=attempt.id,
        provider=attempt.provider,
        payment_status=attempt.status,
        checkout_state=attempt.checkout_state,
        checkout_url=attempt.checkout_url,
        purchase_type=purchase_type,
        plan_id=attempt.plan_id,
        plan_name=attempt.plan_name_snapshot or (attempt.plan.name if attempt.plan is not None else None),
        entitlement_type=entitlement_type,
        target_flats=(
            attempt.target_flats_snapshot
            if attempt.target_flats_snapshot is not None
            else (attempt.plan.target_flats if attempt.plan is not None else None)
        ),
        match_count=(
            attempt.match_count_snapshot
            if attempt.match_count_snapshot is not None
            else (attempt.plan.match_count if attempt.plan is not None else None)
        ),
        amount=attempt.amount,
        currency=attempt.currency,
        flat_setup_required=bool(
            flat_payload
            and flat_payload.get("status") == "pending_setup"
            and flat_payload.get("flat_amount_rub") is None
        ),
        flat_subscription=flat_payload,
        created_at=attempt.created_at,
        updated_at=attempt.updated_at or attempt.created_at,
    )

# --- GENERATE TELEGRAM STARS INVOICE LINK ---

@router.post("/invoice")
async def create_stars_invoice(
    invoice_data: InvoiceRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    POST /api/payments/invoice
    Generates a Telegram Stars invoice link (currency: XTR) for a single paid forecast.
    Match subscriptions are sold through ruble checkout only.
    """
    if bool(invoice_data.plan_id) == bool(invoice_data.bet_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Необходимо указать только plan_id или bet_id"
        )

    if invoice_data.plan_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Абонементы оплачиваются только в рублях"
        )

    provider = "telegram_stars"
    payload_hash = _checkout_request_hash(
        provider=provider,
        user_id=current_user.telegram_id,
        bet_id=invoice_data.bet_id,
        promo_code=invoice_data.promo_code,
    )

    # The provider has no idempotency primitive for createInvoiceLink.  Lock
    # the owner/purchase scope before discovering attempts and before locking
    # the Bet, so different client-generated keys cannot create two intents.
    await _lock_telegram_purchase_scope(
        db,
        user_id=current_user.telegram_id,
        bet_id=invoice_data.bet_id,
        purchase_type=PAYMENT_PURCHASE_SINGLE_BET,
    )
    existing_checkout = await _load_checkout_attempt(
        db,
        user_id=current_user.telegram_id,
        provider=provider,
        checkout_intent_id=idempotency_key,
        payload_hash=payload_hash,
        for_update=True,
    )
    if existing_checkout is not None and existing_checkout.status in ("pending", "processing"):
        recovered_checkout = await _recover_owner_active_telegram_attempt(
            db,
            user_id=current_user.telegram_id,
            bet_id=invoice_data.bet_id,
            purchase_type=PAYMENT_PURCHASE_SINGLE_BET,
            payload_hash=payload_hash,
        )
        if recovered_checkout is None or recovered_checkout.id != existing_checkout.id:
            existing_checkout.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            if recovered_checkout is not None:
                recovered_checkout.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Активные счета требуют ручной сверки",
            )
    elif existing_checkout is None:
        existing_checkout = await _recover_owner_active_telegram_attempt(
            db,
            user_id=current_user.telegram_id,
            bet_id=invoice_data.bet_id,
            purchase_type=PAYMENT_PURCHASE_SINGLE_BET,
            payload_hash=payload_hash,
        )

    if existing_checkout is not None:
        attempt = existing_checkout
        replay = await _telegram_checkout_replay_or_wait(db, attempt)
        if replay is not None:
            await db.commit()
            return replay
        discount_percent = int(attempt.discount_percent_snapshot or 0)
        promo_code = attempt.promo_code
        if attempt.bet_id is None:
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Снимок покупки требует ручной сверки",
            )
        bet = await load_locked_bet_for_user_access(db, attempt.bet_id)
        ensure_bet_eligible_for_user(bet=bet, user=current_user)
    else:
        promo_code, discount_percent = await _validate_promo(
            db,
            promo_code=invoice_data.promo_code,
            user=current_user,
        )
        bet = await load_locked_bet_for_user_access(db, invoice_data.bet_id)
        ensure_bet_eligible_for_user(bet=bet, user=current_user)
        await lock_user_balance(db, current_user.telegram_id)

        existing_access = await db.execute(
            select(user_bets.c.bet_id).filter(
                user_bets.c.user_id == current_user.telegram_id,
                user_bets.c.bet_id == bet.id,
            )
        )
        if existing_access.first() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Прогноз уже открыт",
            )

        price_amount = Decimal(int(bet.price_stars) if bet.price_stars is not None else 50)
        if discount_percent > 0:
            price_amount = _apply_percent_discount(
                price_amount,
                discount_percent,
                minimum=Decimal("1"),
                quantum=Decimal("1"),
            )

        # The owner-scope advisory lock and the partial unique index make this
        # insert exclusive.  Do not call the generic get-or-create helper here:
        # it would acquire a PaymentAttempt lock after the Bet lock and invert
        # the canonical PaymentAttempt -> Bet order.
        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider=provider,
            checkout_intent_id=idempotency_key,
            checkout_payload_hash=payload_hash,
            amount=price_amount,
            currency="XTR",
            bet_id=bet.id,
            promo_code=promo_code,
            metadata={
                "discount_percent": discount_percent,
                "purchase_type": PAYMENT_PURCHASE_SINGLE_BET,
            },
            discount_percent=discount_percent,
        )
    await db.commit()

    # Persist ready_to_create first, then atomically claim the one permitted
    # non-idempotent provider call.  A survived `creating` state is never
    # interpreted as permission to call Telegram again.
    attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    replay = await _telegram_checkout_replay_or_wait(db, attempt)
    if replay is not None:
        await db.commit()
        return replay
    attempt.checkout_state = CHECKOUT_STATE_CREATING
    attempt.checkout_creation_started_at = datetime.now(timezone.utc)
    await db.commit()

    try:
        invoice_url = await create_telegram_stars_invoice_link(
            attempt=attempt,
            title="Прогноз Shamrai",
            description=f"Разблокировка прогноза. Событие: {bet.event_name}. Коэффициент: {float(bet.coefficient):.2f}.",
            label="Прогноз Shamrai" + (" со скидкой" if discount_percent > 0 else ""),
        )
    except HTTPException:
        attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if (
            attempt.checkout_state == CHECKOUT_STATE_CREATING
            and attempt.status == "pending"
            and not attempt.checkout_url
            and not attempt.provider_payment_id
        ):
            attempt.status = "failed"
            attempt.checkout_state = CHECKOUT_STATE_FAILED
        await db.commit()
        raise
    except Exception as exc:
        attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if (
            attempt.checkout_state == CHECKOUT_STATE_CREATING
            and attempt.status == "pending"
            and not attempt.checkout_url
            and not attempt.provider_payment_id
        ):
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        logger.warning(
            "Telegram Stars invoice creation requires reconciliation: attempt_id=%s error_type=%s",
            attempt.id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TELEGRAM_STARS_CHECKOUT_ERROR,
        ) from exc

    attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    if not (
        attempt.checkout_state == CHECKOUT_STATE_CREATING
        and attempt.status == "pending"
        and not attempt.checkout_url
        and not attempt.provider_payment_id
    ):
        attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        logger.error(
            "Telegram Stars invoice link returned after checkout state changed: attempt_id=%s",
            attempt.id,
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TELEGRAM_STARS_CHECKOUT_ERROR,
        )
    attempt.checkout_url = invoice_url
    attempt.checkout_state = CHECKOUT_STATE_READY
    await db.commit()

    return {
        "invoice_url": invoice_url,
        "attempt_id": str(attempt.id),
        "discount_percent": discount_percent,
        "status": attempt.status,
        "checkout_state": attempt.checkout_state,
        **({"mock": True} if settings.DEBUG_MODE else {}),
    }


@router.post("/yookassa/create")
async def create_yookassa_payment(
    payment_data: YooKassaPaymentRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider = "yookassa_debug" if settings.DEBUG_MODE else "yookassa"
    payload_hash = _checkout_request_hash(
        provider=provider,
        user_id=current_user.telegram_id,
        plan_id=payment_data.plan_id,
        promo_code=payment_data.promo_code,
    )
    existing_checkout = await _load_checkout_attempt(
        db,
        user_id=current_user.telegram_id,
        provider=provider,
        checkout_intent_id=idempotency_key,
        payload_hash=payload_hash,
        for_update=True,
    )
    if existing_checkout is not None and not _checkout_creation_can_resume(existing_checkout):
        return _checkout_replay_response(existing_checkout, url_field="confirmation_url")

    if existing_checkout is not None:
        attempt = existing_checkout
        plan = _subscription_plan_snapshot(attempt)
        if plan is None or str(plan.entitlement_type or "") != "flat" or plan.target_flats is None:
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Снимок покупки требует ручной сверки",
            )
        amount = Decimal(str(attempt.amount))
        currency = str(attempt.currency or "RUB")
        promo_code = attempt.promo_code
        discount_percent = int(attempt.discount_percent_snapshot or 0)
        metadata = attempt.metadata_json or {}
        referral_discount_percent = int(metadata.get("referral_discount_percent") or 0)
        attempt.checkout_state = CHECKOUT_STATE_CREATING
    else:
        live_plan_result = await db.execute(
            select(SubscriptionPlan)
            .filter(SubscriptionPlan.id == payment_data.plan_id)
            .options(selectinload(SubscriptionPlan.allowed_checkout_users))
        )
        live_plan = live_plan_result.scalars().first()
        if live_plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        await _ensure_subscription_purchases_enabled(db, plan=live_plan, user=current_user)
        plan = live_plan
        if not plan.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        _ensure_flat_plan_purchase_allowed(plan)

        promo_code, discount_percent = await _validate_promo(
            db,
            promo_code=payment_data.promo_code,
            user=current_user,
        )

        referral_discount_percent = 0
        if discount_percent == 0:
            referral_discount_percent = await get_referral_discount_percent(db, current_user.telegram_id)
            discount_percent = referral_discount_percent

        effective_price = await effective_subscription_price(db, plan=plan, user=current_user)
        amount = effective_price.rub
        if discount_percent > 0:
            amount = _apply_percent_discount(amount, discount_percent, minimum=Decimal("1.00"))
        currency = str(plan.currency or "RUB")

        attempt, created = await _create_or_reuse_checkout_attempt(
            db,
            user=current_user,
            provider=provider,
            checkout_intent_id=idempotency_key,
            checkout_payload_hash=payload_hash,
            amount=amount,
            currency=currency,
            plan=plan,
            promo_code=promo_code,
            metadata={
                "discount_percent": discount_percent,
                "referral_discount_percent": referral_discount_percent if not promo_code else 0,
                **effective_price.audit_metadata(),
            },
            discount_percent=discount_percent,
        )
        if not created:
            return _checkout_replay_response(attempt, url_field="confirmation_url")

    if not settings.DEBUG_MODE and not settings.has_yookassa_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YooKassa checkout is not configured",
        )

    await db.commit()

    if settings.DEBUG_MODE:
        mock_payment_id = f"mock_yookassa_{attempt.id}"
        confirmation_url = (
            f"{settings.FRONTEND_BASE_URL}?mock_yookassa_payment={mock_payment_id}"
            f"&plan_id={plan.id}&attempt_id={attempt.id}"
        )
        attempt.checkout_url = confirmation_url
        attempt.checkout_state = CHECKOUT_STATE_READY
        await db.commit()
        return {
            "payment_id": mock_payment_id,
            "attempt_id": str(attempt.id),
            "confirmation_url": confirmation_url,
            "status": attempt.status,
            "checkout_state": attempt.checkout_state,
            "mock": True,
        }
    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": currency},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": settings.YOOKASSA_RETURN_URL or settings.FRONTEND_BASE_URL,
        },
        "description": f"{plan.name}: цель +{plan.target_flats} флета",
        "metadata": {
            "attempt_id": str(attempt.id),
            "user_id": str(current_user.telegram_id),
            "plan_id": str(plan.id),
            "promo_code": promo_code or "",
            "referral_discount_percent": str(referral_discount_percent if not promo_code else 0),
        },
    }

    auth_raw = f"{settings.YOOKASSA_SHOP_ID}:{settings.YOOKASSA_SECRET_KEY}".encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Basic " + base64.b64encode(auth_raw).decode("ascii"),
        "Idempotence-Key": str(attempt.id),
    }
    req = urllib.request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _open_payment_provider_request(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        logger.warning("YooKassa checkout request rejected: http_status=%s", exc.code)
        locked_attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if locked_attempt.checkout_url or locked_attempt.checkout_state in {
            CHECKOUT_STATE_READY,
            CHECKOUT_STATE_COMPLETED,
        }:
            await db.commit()
            return _checkout_replay_response(locked_attempt, url_field="confirmation_url")
        definitive_rejection = 400 <= int(exc.code or 0) < 500
        locked_attempt.status = "failed" if definitive_rejection else "pending"
        locked_attempt.checkout_state = (
            CHECKOUT_STATE_FAILED
            if definitive_rejection
            else CHECKOUT_STATE_REQUIRES_RECONCILIATION
        )
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=YOOKASSA_CHECKOUT_ERROR)
    except Exception as exc:
        logger.warning("YooKassa checkout request failed: error_type=%s", type(exc).__name__)
        locked_attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if locked_attempt.checkout_url or locked_attempt.checkout_state in {
            CHECKOUT_STATE_READY,
            CHECKOUT_STATE_COMPLETED,
        }:
            await db.commit()
            return _checkout_replay_response(locked_attempt, url_field="confirmation_url")
        locked_attempt.status = "pending"
        locked_attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=YOOKASSA_CHECKOUT_ERROR)

    provider_payment_id = data.get("id")
    confirmation_url = data.get("confirmation", {}).get("confirmation_url")
    if not provider_payment_id or not confirmation_url:
        logger.warning("YooKassa checkout response is incomplete")
        locked_attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
        if locked_attempt.checkout_url or locked_attempt.checkout_state in {
            CHECKOUT_STATE_READY,
            CHECKOUT_STATE_COMPLETED,
        }:
            await db.commit()
            return _checkout_replay_response(locked_attempt, url_field="confirmation_url")
        locked_attempt.status = "pending"
        locked_attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=YOOKASSA_CHECKOUT_ERROR)
    locked_attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    if locked_attempt.checkout_url or locked_attempt.checkout_state in {
        CHECKOUT_STATE_READY,
        CHECKOUT_STATE_COMPLETED,
    }:
        await db.commit()
        return _checkout_replay_response(locked_attempt, url_field="confirmation_url")
    if locked_attempt.provider_payment_id and locked_attempt.provider_payment_id != provider_payment_id:
        locked_attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
        await db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ответ YooKassa требует ручной сверки")
    locked_attempt.provider_payment_id = provider_payment_id
    locked_attempt.checkout_url = confirmation_url
    locked_attempt.checkout_state = CHECKOUT_STATE_READY
    await db.commit()

    return {
        "payment_id": provider_payment_id,
        "attempt_id": str(locked_attempt.id),
        "confirmation_url": locked_attempt.checkout_url,
        "status": data.get("status"),
        "checkout_state": locked_attempt.checkout_state,
    }


@router.post("/tegro/create")
async def create_tegro_payment(
    payment_data: TegroPaymentRequest,
    idempotency_key: UUID = Header(alias="Idempotency-Key"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    provider = "tegro_debug" if settings.DEBUG_MODE else "tegro"
    payload_hash = _checkout_request_hash(
        provider=provider,
        user_id=current_user.telegram_id,
        plan_id=payment_data.plan_id,
        promo_code=payment_data.promo_code,
    )
    existing_checkout = await _load_checkout_attempt(
        db,
        user_id=current_user.telegram_id,
        provider=provider,
        checkout_intent_id=idempotency_key,
        payload_hash=payload_hash,
        for_update=True,
    )
    if existing_checkout is not None and not _checkout_creation_can_resume(existing_checkout):
        return _checkout_replay_response(existing_checkout, url_field="confirmation_url")

    if existing_checkout is not None:
        attempt = existing_checkout
        purchase_plan = _subscription_plan_snapshot(attempt)
        if (
            purchase_plan is None
            or str(purchase_plan.entitlement_type or "") != "flat"
            or purchase_plan.target_flats is None
        ):
            attempt.checkout_state = CHECKOUT_STATE_REQUIRES_RECONCILIATION
            await db.commit()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Снимок покупки требует ручной сверки",
            )
        amount = Decimal(str(attempt.amount)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        currency = str(attempt.currency or "RUB")
        promo_code = attempt.promo_code
        discount_percent = int(attempt.discount_percent_snapshot or 0)
        referral_discount_percent = int(
            (attempt.metadata_json or {}).get("referral_discount_percent") or 0
        )
    else:
        plan_res = await db.execute(
            select(SubscriptionPlan)
            .filter(SubscriptionPlan.id == payment_data.plan_id)
            .options(selectinload(SubscriptionPlan.allowed_checkout_users))
        )
        plan = plan_res.scalars().first()
        if plan is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        await _ensure_subscription_purchases_enabled(db, plan=plan, user=current_user)
        if not plan.is_active:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        _ensure_flat_plan_purchase_allowed(plan)

        promo_code, discount_percent = await _validate_promo(
            db,
            promo_code=payment_data.promo_code,
            user=current_user,
        )

        referral_discount_percent = 0
        if discount_percent == 0:
            referral_discount_percent = await get_referral_discount_percent(db, current_user.telegram_id)
            discount_percent = referral_discount_percent

        effective_price = await effective_subscription_price(db, plan=plan, user=current_user)
        amount = effective_price.rub
        if discount_percent > 0:
            amount = _apply_percent_discount(amount, discount_percent, minimum=Decimal("1.00"))
        amount = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        currency = plan.currency or "RUB"
        purchase_plan = plan

    if currency != "RUB":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tegro поддерживает только рублевые абонементы")

    if not settings.DEBUG_MODE and not settings.has_tegro_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tegro checkout is not configured",
        )

    if existing_checkout is None:
        attempt, created = await _create_or_reuse_checkout_attempt(
            db,
            user=current_user,
            provider=provider,
            checkout_intent_id=idempotency_key,
            checkout_payload_hash=payload_hash,
            amount=amount,
            currency=currency,
            plan=plan,
            promo_code=promo_code,
            metadata={
                "discount_percent": discount_percent,
                "referral_discount_percent": referral_discount_percent if not promo_code else 0,
                **effective_price.audit_metadata(),
            },
            discount_percent=discount_percent,
        )
        if not created and not _checkout_creation_can_resume(attempt):
            return _checkout_replay_response(attempt, url_field="confirmation_url")
    await db.commit()

    # Serialize checkout creation for both a new intent and crash recovery.
    attempt = await _reload_checkout_attempt_for_update(db, attempt.id)
    if not _checkout_creation_can_resume(attempt):
        response = _checkout_replay_response(attempt, url_field="confirmation_url")
        await db.commit()
        return response

    if settings.DEBUG_MODE:
        mock_payment_id = f"mock_tegro_{attempt.id}"
        confirmation_url = (
            f"{settings.FRONTEND_BASE_URL}?mock_tegro_payment={mock_payment_id}"
            f"&plan_id={attempt.plan_id}&attempt_id={attempt.id}"
        )
        attempt.checkout_url = confirmation_url
        attempt.checkout_state = CHECKOUT_STATE_READY
        await db.commit()
        return {
            "payment_id": mock_payment_id,
            "attempt_id": str(attempt.id),
            "confirmation_url": confirmation_url,
            "status": attempt.status,
            "checkout_state": attempt.checkout_state,
            "mock": True,
        }

    payload = {
        "shop_id": settings.TEGRO_SHOP_ID,
        "nonce": int(time.time() * 1000),
        "currency": currency,
        "amount": f"{amount:.2f}",
        "order_id": str(attempt.id),
        "lang": "ru",
        **({"fields": {"phone": current_user.phone}} if current_user.phone else {}),
        "receipt": {
            "items": [{
                "name": _telegram_text(
                    f"{purchase_plan.name}: цель +{purchase_plan.target_flats} флета",
                    128,
                ),
                "count": 1,
                "price": f"{amount:.2f}",
            }],
        },
    }
    try:
        confirmation_url = _create_tegro_payment_url(payload)
    except Exception:
        attempt.status = "failed"
        attempt.checkout_state = CHECKOUT_STATE_FAILED
        await db.commit()
        raise

    attempt.metadata_json = {
        **(attempt.metadata_json or {}),
        "tegro_payment_url_created": True,
    }
    attempt.checkout_url = confirmation_url
    attempt.checkout_state = CHECKOUT_STATE_READY
    await db.commit()

    return {
        "payment_id": str(attempt.id),
        "attempt_id": str(attempt.id),
        "confirmation_url": confirmation_url,
        "status": "pending",
        "checkout_state": attempt.checkout_state,
    }


@router.post("/yookassa/debug-complete")
async def complete_debug_yookassa_payment(
    payment_data: DebugYooKassaCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug checkout is disabled")

    if payment_data.attempt_id:
        attempt_res = await db.execute(select(PaymentAttempt).filter(PaymentAttempt.id == payment_data.attempt_id))
        attempt = attempt_res.scalars().first()
        if not attempt or attempt.user_id != current_user.telegram_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug payment attempt not found")
    else:
        plan_res = await db.execute(
            select(SubscriptionPlan)
            .filter(SubscriptionPlan.id == payment_data.plan_id)
            .options(selectinload(SubscriptionPlan.allowed_checkout_users))
        )
        plan = plan_res.scalars().first()
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        await _ensure_subscription_purchases_enabled(db, plan=plan, user=current_user)
        effective_price = await effective_subscription_price(db, plan=plan, user=current_user)
        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider="yookassa_debug",
            amount=effective_price.rub,
            currency=plan.currency or "RUB",
            plan=plan,
            promo_code=payment_data.promo_code.strip().upper() if payment_data.promo_code else None,
            metadata={"debug_direct_complete": True, **effective_price.audit_metadata()},
        )

    payment_id = f"debug_yookassa_{attempt.id}"
    result = await _process_payment_attempt(
        db,
        attempt_id=attempt.id,
        provider="yookassa_debug",
        provider_payment_id=payment_id,
        amount=Decimal(attempt.amount),
        currency=attempt.currency,
        raw_payload={"debug": True},
    )
    await db.commit()
    return result


@router.post("/tegro/debug-complete")
async def complete_debug_tegro_payment(
    payment_data: DebugTegroCompleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not settings.DEBUG_MODE:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug checkout is disabled")

    if payment_data.attempt_id:
        attempt_res = await db.execute(select(PaymentAttempt).filter(PaymentAttempt.id == payment_data.attempt_id))
        attempt = attempt_res.scalars().first()
        if not attempt or attempt.user_id != current_user.telegram_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Debug payment attempt not found")
    else:
        plan_res = await db.execute(
            select(SubscriptionPlan)
            .filter(SubscriptionPlan.id == payment_data.plan_id)
            .options(selectinload(SubscriptionPlan.allowed_checkout_users))
        )
        plan = plan_res.scalars().first()
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        await _ensure_subscription_purchases_enabled(db, plan=plan, user=current_user)
        effective_price = await effective_subscription_price(db, plan=plan, user=current_user)
        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider="tegro_debug",
            amount=effective_price.rub,
            currency=plan.currency or "RUB",
            plan=plan,
            promo_code=payment_data.promo_code.strip().upper() if payment_data.promo_code else None,
            metadata={"debug_direct_complete": True, **effective_price.audit_metadata()},
        )

    payment_id = f"debug_tegro_{attempt.id}"
    result = await _process_payment_attempt(
        db,
        attempt_id=attempt.id,
        provider="tegro_debug",
        provider_payment_id=payment_id,
        amount=Decimal(attempt.amount),
        currency=attempt.currency,
        raw_payload={"debug": True},
    )
    await db.commit()
    return result


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    update = await request.json()
    if update.get("event") != "payment.succeeded":
        return {"status": "ignored_event"}

    payment_obj = update.get("object", {})
    payment_id = payment_obj.get("id")
    if not payment_id:
        return {"status": "ignored_no_payment_id"}

    verified_payment = await asyncio.to_thread(_request_yookassa_payment, payment_id)
    if verified_payment.get("id") != payment_id or verified_payment.get("status") != "succeeded":
        record_payment_mismatch("yookassa", "verification_mismatch", payment_id=payment_id)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="YooKassa payment is not verified as succeeded",
        )
    metadata = verified_payment.get("metadata") or {}

    try:
        attempt_id = UUID(str(metadata["attempt_id"]))
        amount = Decimal(str(verified_payment["amount"]["value"]))
        currency = str(verified_payment["amount"]["currency"])
    except Exception:
        return {"status": "ignored_invalid_metadata"}

    result = await _process_payment_attempt(
        db,
        attempt_id=attempt_id,
        provider="yookassa",
        provider_payment_id=payment_id,
        amount=amount,
        currency=currency,
        raw_payload=verified_payment,
    )
    await db.commit()
    return result


@router.post("/tegro/webhook")
async def tegro_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.has_tegro_credentials:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Tegro webhook is not configured")

    content_type = (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    if content_type == "application/json":
        raw_fields = await request.json()
    else:
        form = await request.form()
        raw_fields = {key: value for key, value in form.items()}

    try:
        notification = _verify_tegro_notification(dict(raw_fields))
    except TegroWebhookSignatureError as e:
        record_payment_mismatch("tegro", e.code)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.code)

    if notification["is_test"]:
        return {"status": "test_ignored"}

    if settings.TEGRO_SHOP_ID.strip() and notification["shop_id"] and notification["shop_id"] != settings.TEGRO_SHOP_ID:
        record_payment_mismatch("tegro", "shop_id_mismatch", payment_id=notification.get("order_id"))
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tegro shop id mismatch")

    try:
        attempt_id = UUID(notification["order_id"])
    except Exception:
        return {"status": "ignored_invalid_order_id"}

    provider_payment_id = f"tegro_order_{notification['order_id']}"
    result = await _process_payment_attempt(
        db,
        attempt_id=attempt_id,
        provider="tegro",
        provider_payment_id=provider_payment_id,
        amount=notification["amount"],
        currency=notification["currency"],
        raw_payload=notification["raw"],
    )
    await _enqueue_payment_confirmation(db, result)
    await db.commit()
    return result

@router.get("/promo/validate")
async def validate_promo_code(
    code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /api/payments/promo/validate?code=...
    Checks if a promo code exists, is active, and is not expired.
    Returns its reward type and value if valid.
    """
    normalized_code = code.strip().upper()
    promo = await _load_active_promo(db, normalized_code)
    _ensure_promo_available_for_user(promo, current_user)

    reward_type = _promo_reward_type(promo)
    if reward_type in {PROMO_REWARD_MATCHES, PROMO_REWARD_FLATS}:
        existing = await db.execute(
            select(PromoCodeRedemption).filter(
                PromoCodeRedemption.promo_code_id == promo.id,
                PromoCodeRedemption.user_id == current_user.telegram_id,
            )
        )
        if existing.scalars().first():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Этот промокод уже применен",
            )

    return {
        "code": promo.code,
        "reward_type": reward_type,
        "discount_percent": int(promo.discount_percent or 0),
        "matches_count": int(promo.matches_count or 0),
        "target_flats": str(promo.target_flats) if promo.target_flats is not None else None,
    }


@router.post("/promo/redeem")
async def redeem_promo_code(
    data: PromoRedeemRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Redeem a flat-target promo; legacy match codes remain redeemable for historical compatibility."""
    normalized_code = data.code.strip().upper()
    promo = await _load_active_promo(db, normalized_code)
    _ensure_promo_available_for_user(promo, current_user)

    reward_type = _promo_reward_type(promo)
    if reward_type not in {PROMO_REWARD_MATCHES, PROMO_REWARD_FLATS}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод предназначен для скидки",
        )

    matches_added = int(promo.matches_count or 0)
    target_flats_added = Decimal(str(promo.target_flats or 0)).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP,
    )
    if reward_type == PROMO_REWARD_FLATS and target_flats_added <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="У промокода не задана цель во флетах")
    if reward_type == PROMO_REWARD_MATCHES and matches_added <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="У старого промокода не задано количество матчей")

    existing = await db.execute(
        select(PromoCodeRedemption).filter(
            PromoCodeRedemption.promo_code_id == promo.id,
            PromoCodeRedemption.user_id == current_user.telegram_id,
        )
    )
    if existing.scalars().first():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод уже применен",
        )

    balance_before = current_match_balance(current_user)
    balance_after = balance_before
    flat_subscription = None
    if reward_type == PROMO_REWARD_FLATS:
        flat_subscription = await credit_flat_subscription(
            db,
            user=current_user,
            target_flats=target_flats_added,
            event_type="promo_target_credit",
            note=f"Redeemed promo code {promo.code}",
        )
    else:
        locked_balance = await lock_user_balance(db, current_user.telegram_id)
        balance_before = current_match_balance(locked_balance)
        balance_after = balance_before + matches_added
        current_user.purchased_bets_balance = balance_after
        current_user.matches_remaining = balance_after

    db.add(PromoCodeRedemption(
        promo_code_id=promo.id,
        user_id=current_user.telegram_id,
        matches_added=matches_added,
        target_flats_added=target_flats_added if reward_type == PROMO_REWARD_FLATS else Decimal("0"),
    ))
    if reward_type == PROMO_REWARD_MATCHES:
        db.add(MatchBalanceLog(
            user_id=current_user.telegram_id,
            delta_matches=matches_added,
            event_type="promo_match_credit",
            note=f"Redeemed legacy promo code {promo.code}",
        ))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод уже применен",
        )

    return {
        "code": promo.code,
        "reward_type": reward_type,
        "matches_added": matches_added,
        "target_flats_added": str(target_flats_added) if reward_type == PROMO_REWARD_FLATS else None,
        "flat_subscription_status": flat_subscription.status if flat_subscription is not None else None,
        "balance_before": balance_before,
        "balance_after": balance_after,
    }


@router.post("/debug/complete-bet/{bet_id}")
async def complete_debug_single_bet_purchase(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Local browser-only checkout fallback for single-bet purchases.
    Disabled when DEBUG_MODE is false.
    """
    if not settings.DEBUG_MODE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Debug checkout is disabled"
        )

    bet = await load_locked_bet_for_user_access(db, bet_id)

    ensure_bet_eligible_for_user(bet=bet, user=current_user)

    attempt = await _create_payment_attempt(
        db,
        user=current_user,
        provider="debug_single_bet",
        amount=Decimal(int(bet.price_stars) if bet.price_stars is not None else 50),
        currency="XTR",
        bet_id=bet.id,
        metadata={"debug_direct_complete": True},
    )
    result = await _process_payment_attempt(
        db,
        attempt_id=attempt.id,
        provider="debug_single_bet",
        provider_payment_id=f"debug_single_bet_{attempt.id}",
        amount=Decimal(attempt.amount),
        currency=attempt.currency,
        raw_payload={"debug": True},
    )
    await db.commit()
    if result.get("status") == "success":
        return {"status": "success", "bet_id": str(bet.id), "attempt_id": str(attempt.id)}
    return result

# --- TELEGRAM BOT WEBHOOK HANDLER ---


async def process_telegram_payment_update(update: dict, db: AsyncSession) -> dict:
    """
    Processes Telegram PreCheckoutQueries and SuccessfulPayments.
    Returns a status object; non-payment updates are ignored.
    """
    if "pre_checkout_query" in update:
        query = update["pre_checkout_query"]
        query_id_raw = query.get("id") if isinstance(query, dict) else None
        query_id = query_id_raw.strip() if isinstance(query_id_raw, str) else ""
        payer_user_id = _telegram_sender_id(query)
        is_valid = False
        error_message = "Платеж не найден или устарел"
        attempt_id = None

        try:
            if not query_id or len(query_id) > 255 or payer_user_id is None:
                raise ValueError("Invalid pre-checkout identity")
            attempt_id = _invoice_attempt_id(query.get("invoice_payload"))
            if not attempt_id:
                raise ValueError("Invalid invoice payload")
            attempt_res = await db.execute(
                select(PaymentAttempt)
                .filter(PaymentAttempt.id == attempt_id)
                .options(
                    selectinload(PaymentAttempt.user).selectinload(User.bookmakers),
                    selectinload(PaymentAttempt.bet).selectinload(Bet.bookmakers),
                )
                .with_for_update()
            )
            attempt = attempt_res.scalars().first()
            if attempt is None:
                raise ValueError("Payment attempt missing")
            incoming_amount = Decimal(str(query.get("total_amount", 0)))
            incoming_currency = str(query.get("currency") or "")
            if (
                attempt.provider != "telegram_stars"
                or int(attempt.user_id) != payer_user_id
                or attempt.currency != incoming_currency
                or not _decimal_eq(Decimal(attempt.amount), incoming_amount)
                or attempt.checkout_state == CHECKOUT_STATE_REQUIRES_RECONCILIATION
            ):
                raise ValueError("Pre-checkout mismatch")

            if attempt.telegram_pre_checkout_query_id is not None:
                is_valid = bool(
                    attempt.status == "processing"
                    and attempt.telegram_pre_checkout_query_id == query_id
                    and attempt.telegram_pre_checkout_user_id is not None
                    and int(attempt.telegram_pre_checkout_user_id) == payer_user_id
                )
                if not is_valid:
                    raise ValueError("Payment attempt already reserved")
            else:
                if attempt.status != "pending" or attempt.provider_payment_id:
                    raise ValueError("Payment attempt is not reservable")
                purchase_type = str(
                    attempt.purchase_type_snapshot
                    or (attempt.metadata_json or {}).get("purchase_type")
                    or PAYMENT_PURCHASE_SINGLE_BET
                )
                if purchase_type in {PAYMENT_PURCHASE_SINGLE_BET, PAYMENT_PURCHASE_BET_HINT}:
                    if attempt.bet is None or attempt.user is None:
                        raise ValueError("Payment item missing")
                    bet = await load_locked_bet_for_user_access(db, attempt.bet.id)
                    ensure_bet_eligible_for_user(bet=bet, user=attempt.user)

                reserved_at = datetime.now(timezone.utc)
                attempt.telegram_pre_checkout_query_id = query_id
                attempt.telegram_pre_checkout_user_id = payer_user_id
                attempt.telegram_pre_checkout_reserved_at = reserved_at
                attempt.status = "processing"
                attempt.processing_started_at = reserved_at
                await db.flush()
                is_valid = True

            # Release the PaymentAttempt/Bet locks and durably reserve the
            # invoice before Telegram is told that it may charge the payer.
            await db.commit()
        except Exception:
            await db.rollback()
            is_valid = False

        if not is_valid:
            record_payment_mismatch("telegram_stars", "pre_checkout_mismatch", attempt_id=attempt_id)

        await call_telegram_api_async(
            "answerPreCheckoutQuery",
            {
                "pre_checkout_query_id": query_id,
                "ok": is_valid,
                **({} if is_valid else {"error_message": error_message}),
            },
            2,
            0,
        )
        return {"status": "pre_checkout_answered" if is_valid else "pre_checkout_rejected"}

    message = update.get("message", {})
    if "successful_payment" in message:
        payment = message["successful_payment"]
        payload_str = payment.get("invoice_payload")
        payer_user_id = _telegram_sender_id(message)
        
        if not payload_str:
            return {"status": "ignored_no_payload"}
        if payer_user_id is None:
            record_payment_mismatch("telegram_stars", "invalid_successful_payment_payer")
            return {"status": "ignored_invalid_payer"}

        try:
            attempt_id = _invoice_attempt_id(payload_str)
            if not attempt_id:
                raise ValueError("Invalid invoice payload")
            amount = Decimal(str(payment.get("total_amount", 0)))
            currency = str(payment.get("currency") or "XTR")
        except Exception:
            record_payment_mismatch("telegram_stars", "invalid_successful_payment_payload")
            return {"status": "ignored_invalid_payload"}

        payment_id = payment.get("telegram_payment_charge_id") or f"telegram_stars_{attempt_id}"
        result = await _process_payment_attempt(
            db,
            attempt_id=attempt_id,
            provider="telegram_stars",
            provider_payment_id=payment_id,
            amount=amount,
            currency=currency,
            raw_payload=payment,
            payer_user_id=payer_user_id,
        )
        await _enqueue_payment_confirmation(db, result)
        await db.commit()

        return result

    return {"status": "ignored_update_type"}


@router.post("/telegram-webhook")
async def telegram_bot_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db),
):
    """
    Backward-compatible Telegram payment webhook endpoint.
    The canonical Telegram webhook route is /api/telegram/webhook.
    """
    verify_telegram_webhook_secret(x_telegram_bot_api_secret_token)
    try:
        update = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON update")
    return await process_telegram_payment_update(update, db)
