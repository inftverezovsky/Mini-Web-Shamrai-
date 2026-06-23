import asyncio
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, Header, HTTPException, status, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, func
from pydantic import BaseModel
from typing import Optional, Any
from decimal import Decimal
import base64

from uuid import UUID
from src.models.database import get_db
from src.models.models import User, SubscriptionPlan, PaymentAttempt, PromoCode, Bet, user_bets
from src.core.config import settings
from src.core.security import verify_telegram_webhook_secret
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.api.deps import get_current_user
from src.services.referrals import get_referral_discount_percent
from src.services.match_access import activate_match_subscription
from src.services.crowd_bets import apply_verified_crowd_contribution
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, enqueue_delivery
from src.services.telegram_bot import call_telegram_api_async

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("uvicorn")

PAYMENT_PURCHASE_CROWD_BET = "crowd_bet"
PAYMENT_PURCHASE_BET_HINT = "bet_hint"
YOOKASSA_VERIFICATION_ERROR = "Не удалось проверить платеж YooKassa. Попробуйте позже."
YOOKASSA_CHECKOUT_ERROR = "Не удалось создать платеж YooKassa. Попробуйте позже."
TEGRO_CHECKOUT_ERROR = "Не удалось создать платеж Tegro. Попробуйте позже."
TELEGRAM_STARS_CHECKOUT_ERROR = "Не удалось создать счет Telegram Stars. Попробуйте позже."

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


class DebugTegroCompleteRequest(TegroPaymentRequest):
    attempt_id: Optional[UUID] = None


class TegroWebhookSignatureError(ValueError):
    def __init__(self, code: str):
        super().__init__(f"tegro_webhook_{code}")
        self.code = code


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
        "amount": amount.quantize(Decimal("0.01")),
        "order_id": order_id,
        "payment_system": str(raw_fields.get("payment_system") or ""),
        "currency": str(raw_fields.get("currency") or "RUB"),
        "payment_id": str(raw_fields.get("payment_id") or "") or None,
        "is_test": str(raw_fields.get("test") or "").lower() in {"1", "true"},
        "raw": raw_fields,
    }


def _decimal_eq(left: Decimal, right: Decimal) -> bool:
    return left.quantize(Decimal("0.01")) == right.quantize(Decimal("0.01"))


async def _validate_promo(
    db: AsyncSession,
    *,
    promo_code: Optional[str],
    user: User,
) -> tuple[Optional[str], int]:
    if not promo_code:
        return None, 0

    normalized = promo_code.strip().upper()
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
    if promo.user_id is not None and promo.user_id != user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод привязан к другому пользователю",
        )
    return normalized, int(promo.discount_percent or 0)


def _apply_percent_discount(amount: Decimal, discount_percent: int, *, minimum: Decimal) -> Decimal:
    if discount_percent <= 0:
        return amount
    discounted = amount * Decimal(100 - discount_percent) / Decimal(100)
    return max(minimum, discounted.quantize(Decimal("0.01")))


async def _create_payment_attempt(
    db: AsyncSession,
    *,
    user: User,
    provider: str,
    amount: Decimal,
    currency: str,
    plan_id: Optional[int] = None,
    bet_id: Optional[UUID] = None,
    promo_code: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> PaymentAttempt:
    if bool(plan_id) == bool(bet_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Платеж должен быть привязан либо к тарифу, либо к прогнозу",
        )

    attempt = PaymentAttempt(
        user_id=user.telegram_id,
        plan_id=plan_id,
        bet_id=bet_id,
        provider=provider,
        amount=amount.quantize(Decimal("0.01")),
        currency=currency,
        promo_code=promo_code,
        metadata_json=metadata or {},
        status="pending",
    )
    db.add(attempt)
    await db.flush()
    return attempt


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

    res = await call_telegram_api_async("createInvoiceLink", tg_payload)
    if not res.get("ok"):
        logger.warning(
            "Telegram Stars invoice link creation failed: error_code=%s has_description=%s",
            res.get("error_code"),
            bool(res.get("description")),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=TELEGRAM_STARS_CHECKOUT_ERROR,
        )
    return res["result"]


async def _unlock_single_bet(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    access_type: str,
) -> bool:
    existing_res = await db.execute(
        select(user_bets).filter(
            and_(user_bets.c.user_id == user.telegram_id, user_bets.c.bet_id == bet.id)
        )
    )
    if existing_res.first():
        return False

    await db.execute(
        user_bets.insert().values(
            user_id=user.telegram_id,
            bet_id=bet.id,
            taken_at=func.now(),
            access_type=access_type,
            match_charged=False,
        )
    )
    if not user.has_used_shield:
        user.has_used_shield = True
    return True


async def _process_payment_attempt(
    db: AsyncSession,
    *,
    attempt_id: UUID,
    provider: str,
    provider_payment_id: str,
    amount: Decimal,
    currency: str,
    raw_payload: Optional[dict[str, Any]] = None,
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
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment provider mismatch")

    if attempt.provider_payment_id and attempt.provider_payment_id != provider_payment_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment id mismatch")

    if attempt.currency != currency:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment currency mismatch")

    if not _decimal_eq(Decimal(attempt.amount), amount):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Payment amount mismatch")

    if attempt.status == "succeeded":
        return {"status": "already_processed", "attempt_id": str(attempt.id)}

    if attempt.status not in ("pending", "failed"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Payment attempt is {attempt.status}")

    user = attempt.user
    if not user:
        return {"status": "user_missing", "attempt_id": str(attempt.id)}

    result: dict[str, Any] = {
        "status": "success",
        "attempt_id": str(attempt.id),
        "user_id": user.telegram_id,
    }

    plan: Optional[SubscriptionPlan] = None
    bet: Optional[Bet] = None
    if attempt.plan_id:
        plan = attempt.plan
        if not plan:
            return {"status": "plan_missing", "attempt_id": str(attempt.id)}
    elif attempt.bet_id:
        bet = attempt.bet
        if not bet:
            return {"status": "bet_missing", "attempt_id": str(attempt.id)}
    else:
        return {"status": "attempt_has_no_item", "attempt_id": str(attempt.id)}

    attempt.status = "processing"
    attempt.provider_payment_id = provider_payment_id
    await db.flush()

    if plan:
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
    elif bet:
        metadata = attempt.metadata_json or {}
        purchase_type = str(metadata.get("purchase_type") or "single_bet")
        if purchase_type == PAYMENT_PURCHASE_CROWD_BET:
            crowd_bet_id = int(metadata.get("crowd_bet_id") or 0)
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

    attempt.status = "succeeded"
    attempt.processed_at = datetime.now(timezone.utc)
    if raw_payload is not None:
        attempt.metadata_json = {
            **(attempt.metadata_json or {}),
            "processed_payload": raw_payload,
        }
    await db.flush()
    return result


def _payment_confirmation_text(result: dict[str, Any]) -> str:
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

# --- GENERATE TELEGRAM STARS INVOICE LINK ---

@router.post("/invoice")
async def create_stars_invoice(
    invoice_data: InvoiceRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
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

    promo_code, discount_percent = await _validate_promo(
        db,
        promo_code=invoice_data.promo_code,
        user=current_user,
    )

    if invoice_data.bet_id:
        bet_res = await db.execute(select(Bet).filter(Bet.id == invoice_data.bet_id))
        bet = bet_res.scalars().first()
        if not bet:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Прогноз не найден"
        )
        
        price_amount = Decimal(int(bet.price_stars) if bet.price_stars is not None else 50)
        if discount_percent > 0:
            price_amount = _apply_percent_discount(price_amount, discount_percent, minimum=Decimal("1.00"))

        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider="telegram_stars",
            amount=price_amount,
            currency="XTR",
            bet_id=bet.id,
            promo_code=promo_code,
            metadata={"discount_percent": discount_percent},
        )
    await db.commit()

    invoice_url = await create_telegram_stars_invoice_link(
        attempt=attempt,
        title="Прогноз Shamrai",
        description=f"Разблокировка прогноза. Событие: {bet.event_name}. Коэффициент: {float(bet.coefficient):.2f}.",
        label="Прогноз Shamrai" + (" со скидкой" if discount_percent > 0 else ""),
    )

    return {
        "invoice_url": invoice_url,
        "attempt_id": str(attempt.id),
        "discount_percent": discount_percent,
        **({"mock": True} if settings.DEBUG_MODE else {}),
    }


@router.post("/yookassa/create")
async def create_yookassa_payment(
    payment_data: YooKassaPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    plan_res = await db.execute(
        select(SubscriptionPlan).filter(
            SubscriptionPlan.id == payment_data.plan_id,
            SubscriptionPlan.is_active == True,
        )
    )
    plan = plan_res.scalars().first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

    promo_code, discount_percent = await _validate_promo(
        db,
        promo_code=payment_data.promo_code,
        user=current_user,
    )

    referral_discount_percent = 0
    if discount_percent == 0:
        referral_discount_percent = await get_referral_discount_percent(db, current_user.telegram_id)
        discount_percent = referral_discount_percent

    amount = Decimal(plan.price)
    if discount_percent > 0:
        amount = _apply_percent_discount(amount, discount_percent, minimum=Decimal("1.00"))

    attempt = await _create_payment_attempt(
        db,
        user=current_user,
        provider="yookassa_debug" if settings.DEBUG_MODE else "yookassa",
        amount=amount,
        currency=plan.currency or "RUB",
        plan_id=plan.id,
        promo_code=promo_code,
        metadata={
            "discount_percent": discount_percent,
            "referral_discount_percent": referral_discount_percent if not promo_code else 0,
        },
    )

    await db.commit()

    if settings.DEBUG_MODE:
        mock_payment_id = f"mock_yookassa_{plan.id}_{current_user.telegram_id}_{int(datetime.now(timezone.utc).timestamp())}"
        return {
            "payment_id": mock_payment_id,
            "attempt_id": str(attempt.id),
            "confirmation_url": f"{settings.FRONTEND_BASE_URL}?mock_yookassa_payment={mock_payment_id}&plan_id={plan.id}&attempt_id={attempt.id}",
            "mock": True,
        }
    if not settings.has_yookassa_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="YooKassa checkout is not configured",
        )

    payload = {
        "amount": {"value": f"{amount:.2f}", "currency": plan.currency or "RUB"},
        "capture": True,
        "confirmation": {
            "type": "redirect",
            "return_url": settings.YOOKASSA_RETURN_URL or settings.FRONTEND_BASE_URL,
        },
        "description": f"{plan.name}: {plan.match_count} матчей",
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
    except Exception as e:
        logger.warning("YooKassa checkout request failed: error_type=%s", type(e).__name__)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=YOOKASSA_CHECKOUT_ERROR)

    attempt.provider_payment_id = data.get("id")
    await db.commit()

    return {
        "payment_id": data.get("id"),
        "attempt_id": str(attempt.id),
        "confirmation_url": data.get("confirmation", {}).get("confirmation_url"),
        "status": data.get("status"),
    }


@router.post("/tegro/create")
async def create_tegro_payment(
    payment_data: TegroPaymentRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan_res = await db.execute(
        select(SubscriptionPlan).filter(
            SubscriptionPlan.id == payment_data.plan_id,
            SubscriptionPlan.is_active == True,
        )
    )
    plan = plan_res.scalars().first()
    if not plan:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")

    promo_code, discount_percent = await _validate_promo(
        db,
        promo_code=payment_data.promo_code,
        user=current_user,
    )

    referral_discount_percent = 0
    if discount_percent == 0:
        referral_discount_percent = await get_referral_discount_percent(db, current_user.telegram_id)
        discount_percent = referral_discount_percent

    amount = Decimal(plan.price)
    if discount_percent > 0:
        amount = _apply_percent_discount(amount, discount_percent, minimum=Decimal("1.00"))
    amount = amount.quantize(Decimal("0.01"))
    currency = plan.currency or "RUB"
    if currency != "RUB":
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tegro поддерживает только рублевые абонементы")

    attempt = await _create_payment_attempt(
        db,
        user=current_user,
        provider="tegro_debug" if settings.DEBUG_MODE else "tegro",
        amount=amount,
        currency=currency,
        plan_id=plan.id,
        promo_code=promo_code,
        metadata={
            "discount_percent": discount_percent,
            "referral_discount_percent": referral_discount_percent if not promo_code else 0,
        },
    )
    await db.commit()

    if settings.DEBUG_MODE:
        mock_payment_id = f"mock_tegro_{plan.id}_{current_user.telegram_id}_{int(datetime.now(timezone.utc).timestamp())}"
        return {
            "payment_id": mock_payment_id,
            "attempt_id": str(attempt.id),
            "confirmation_url": f"{settings.FRONTEND_BASE_URL}?mock_tegro_payment={mock_payment_id}&plan_id={plan.id}&attempt_id={attempt.id}",
            "mock": True,
        }

    if not settings.has_tegro_credentials:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Tegro checkout is not configured",
        )

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
                "name": _telegram_text(f"{plan.name}: {plan.match_count} матчей", 128),
                "count": 1,
                "price": f"{amount:.2f}",
            }],
        },
    }
    confirmation_url = _create_tegro_payment_url(payload)

    attempt.metadata_json = {
        **(attempt.metadata_json or {}),
        "tegro_payment_url_created": True,
    }
    await db.commit()

    return {
        "payment_id": str(attempt.id),
        "attempt_id": str(attempt.id),
        "confirmation_url": confirmation_url,
        "status": "pending",
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
        plan_res = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == payment_data.plan_id))
        plan = plan_res.scalars().first()
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider="yookassa_debug",
            amount=Decimal(plan.price),
            currency=plan.currency or "RUB",
            plan_id=plan.id,
            promo_code=payment_data.promo_code.strip().upper() if payment_data.promo_code else None,
            metadata={"debug_direct_complete": True},
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
        plan_res = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.id == payment_data.plan_id))
        plan = plan_res.scalars().first()
        if not plan:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Тариф не найден")
        attempt = await _create_payment_attempt(
            db,
            user=current_user,
            provider="tegro_debug",
            amount=Decimal(plan.price),
            currency=plan.currency or "RUB",
            plan_id=plan.id,
            promo_code=payment_data.promo_code.strip().upper() if payment_data.promo_code else None,
            metadata={"debug_direct_complete": True},
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
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=e.code)

    if notification["is_test"]:
        return {"status": "test_ignored"}

    if settings.TEGRO_SHOP_ID.strip() and notification["shop_id"] and notification["shop_id"] != settings.TEGRO_SHOP_ID:
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
    Returns its discount percentage if valid.
    """
    normalized_code = code.strip().upper()
    promo_res = await db.execute(
        select(PromoCode).filter(
            PromoCode.code == normalized_code,
            PromoCode.is_active == True,
            PromoCode.valid_until > datetime.now(timezone.utc)
        )
    )
    promo = promo_res.scalars().first()
    if not promo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Неверный или истекший промокод"
        )
    if promo.user_id is not None and promo.user_id != current_user.telegram_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Этот промокод привязан к другому пользователю"
        )
    return {"code": promo.code, "discount_percent": promo.discount_percent}


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

    bet_res = await db.execute(select(Bet).filter(Bet.id == bet_id))
    bet = bet_res.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден"
        )

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
        query_id = query["id"]
        is_valid = True
        error_message = "Платеж не найден или устарел"

        try:
            attempt_id = _invoice_attempt_id(query.get("invoice_payload"))
            if not attempt_id:
                raise ValueError("Invalid invoice payload")
            attempt_res = await db.execute(select(PaymentAttempt).filter(PaymentAttempt.id == attempt_id))
            attempt = attempt_res.scalars().first()
            incoming_amount = Decimal(str(query.get("total_amount", 0)))
            incoming_currency = str(query.get("currency") or "")
            is_valid = bool(
                attempt
                and attempt.status == "pending"
                and attempt.provider == "telegram_stars"
                and attempt.currency == incoming_currency
                and _decimal_eq(Decimal(attempt.amount), incoming_amount)
            )
        except Exception:
            is_valid = False

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
        
        if not payload_str:
            return {"status": "ignored_no_payload"}

        try:
            attempt_id = _invoice_attempt_id(payload_str)
            if not attempt_id:
                raise ValueError("Invalid invoice payload")
            amount = Decimal(str(payment.get("total_amount", 0)))
            currency = str(payment.get("currency") or "XTR")
        except Exception:
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
