import asyncio
import json
import logging
import mimetypes
import time
import urllib.request
import urllib.error
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
import secrets

from uuid import UUID
from src.models.database import get_db
from src.models.models import User, SubscriptionPlan, PaymentAttempt, PromoCode, Bet, user_bets
from src.core.config import settings
from src.core.security import verify_telegram_webhook_secret
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.api.deps import get_current_user
from src.services.referrals import get_referral_discount_percent
from src.services.match_access import activate_match_package

router = APIRouter(prefix="/payments", tags=["Payments"])
logger = logging.getLogger("uvicorn")

class InvoiceRequest(BaseModel):
    plan_id: Optional[int] = None
    bet_id: Optional[UUID] = None
    promo_code: Optional[str] = None


class YooKassaPaymentRequest(BaseModel):
    plan_id: int
    promo_code: Optional[str] = None


class DebugYooKassaCompleteRequest(YooKassaPaymentRequest):
    attempt_id: Optional[UUID] = None


def call_telegram_api(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    """Helper to perform synchronous POST calls to the Telegram Bot API."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    headers = {"Content-Type": "application/json"}
    req_body = json.dumps(payload).encode("utf-8")
    opener = urllib.request.build_opener()
    if settings.HTTPS_PROXY.strip():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({
                "http": settings.HTTPS_PROXY.strip(),
                "https": settings.HTTPS_PROXY.strip(),
            })
        )
    
    retry_count = settings.TELEGRAM_API_RETRIES if retries is None else retries
    attempts = max(1, int(retry_count or 0) + 1)
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_description = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
            with opener.open(req, timeout=request_timeout) as response:
                res_data = json.loads(response.read().decode("utf-8"))
                return res_data
        except urllib.error.HTTPError as e:
            try:
                error_payload = json.loads(e.read().decode("utf-8"))
                description = error_payload.get("description") or f"HTTP {e.code}"
            except Exception:
                description = f"HTTP {e.code}: {e.reason}"
            print(f"Telegram Bot API call failed: {method}: {description}")
            return {"ok": False, "description": description}
        except Exception as e:
            last_description = str(e)
            if attempt < attempts:
                print(f"Telegram Bot API call retrying: {method}: attempt {attempt}/{attempts}: {e}")
                time.sleep(min(0.8 * attempt, 2.0))
                continue
            print(f"Telegram Bot API call failed: {method}: {e}")
            return {"ok": False, "description": last_description}

    return {"ok": False, "description": last_description}


async def call_telegram_api_async(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    return await asyncio.to_thread(call_telegram_api, method, payload, timeout, retries)


def run_telegram_api_background(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> None:
    task = asyncio.create_task(call_telegram_api_async(method, payload, timeout, retries))

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            result = done_task.result()
            if not result.get("ok"):
                logger.info(
                    "[Telegram] Background %s failed: %s",
                    method,
                    result.get("description", "unknown error"),
                )
        except Exception as exc:
            logger.exception("[Telegram] Background %s crashed: %s", method, exc)

    task.add_done_callback(_log_failure)


def _telegram_multipart_field_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def call_telegram_api_multipart(
    method: str,
    payload: dict,
    files: dict[str, tuple[str, bytes, str]],
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    """Helper to perform synchronous multipart/form-data calls to the Telegram Bot API."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    boundary = f"----shamrai-telegram-{secrets.token_hex(16)}"
    body_parts: list[bytes] = []

    for key, value in payload.items():
        if value is None:
            continue
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
            _telegram_multipart_field_value(value).encode("utf-8"),
            b"\r\n",
        ])

    for field_name, (filename, contents, content_type) in files.items():
        clean_filename = filename or field_name
        guessed_type = content_type or mimetypes.guess_type(clean_filename)[0] or "application/octet-stream"
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{clean_filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {guessed_type}\r\n\r\n".encode("utf-8"),
            contents,
            b"\r\n",
        ])

    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req_body = b"".join(body_parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    opener = urllib.request.build_opener()
    if settings.HTTPS_PROXY.strip():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({
                "http": settings.HTTPS_PROXY.strip(),
                "https": settings.HTTPS_PROXY.strip(),
            })
        )

    retry_count = settings.TELEGRAM_API_RETRIES if retries is None else retries
    attempts = max(1, int(retry_count or 0) + 1)
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_description = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
            with opener.open(req, timeout=request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                error_payload = json.loads(e.read().decode("utf-8"))
                description = error_payload.get("description") or f"HTTP {e.code}"
            except Exception:
                description = f"HTTP {e.code}: {e.reason}"
            print(f"Telegram Bot API multipart call failed: {method}: {description}")
            return {"ok": False, "description": description}
        except Exception as e:
            last_description = str(e)
            if attempt < attempts:
                print(f"Telegram Bot API multipart call retrying: {method}: attempt {attempt}/{attempts}: {e}")
                time.sleep(min(0.8 * attempt, 2.0))
                continue
            print(f"Telegram Bot API multipart call failed: {method}: {e}")
            return {"ok": False, "description": last_description}

    return {"ok": False, "description": last_description}


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
        with urllib.request.urlopen(req, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"YooKassa payment verification failed: HTTP {e.code}",
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"YooKassa payment verification failed: {e}",
        )


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

    if attempt.plan_id:
        plan = attempt.plan
        if not plan:
            return {"status": "plan_missing", "attempt_id": str(attempt.id)}
        subscription = await activate_match_package(
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
    elif attempt.bet_id:
        bet = attempt.bet
        if not bet:
            return {"status": "bet_missing", "attempt_id": str(attempt.id)}
        unlocked = await _unlock_single_bet(
            db,
            user=user,
            bet=bet,
            access_type=f"{provider}_single_bet",
        )
        result.update({"bet_id": str(bet.id), "already_unlocked": not unlocked})
    else:
        return {"status": "attempt_has_no_item", "attempt_id": str(attempt.id)}

    attempt.status = "succeeded"
    attempt.provider_payment_id = provider_payment_id
    attempt.processed_at = datetime.now(timezone.utc)
    if raw_payload is not None:
        attempt.metadata_json = {
            **(attempt.metadata_json or {}),
            "processed_payload": raw_payload,
        }
    await db.flush()
    return result

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
    Match packages are sold through ruble checkout only.
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
        title = "Прогноз Shamrai"
        description = _telegram_text(
            f"Разблокировка прогноза. Событие: {bet.event_name}. Коэффициент: {float(bet.coefficient):.2f}.",
            255,
        )
        prices = [{
            "label": "Прогноз Shamrai" + (" со скидкой" if discount_percent > 0 else ""),
            "amount": int(price_amount),
        }]

        tg_payload = {
            "title": title,
            "description": description,
            "payload": _telegram_invoice_payload(attempt),
            "provider_token": "",
            "currency": "XTR",
            "prices": prices
        }

    if settings.DEBUG_MODE:
        return {
            "invoice_url": f"https://t.me/invoice/mock_stars_attempt_{attempt.id}",
            "attempt_id": str(attempt.id),
            "discount_percent": discount_percent,
            "mock": True,
        }
    if not settings.has_real_telegram_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram Stars billing is not configured",
        )

    res = await call_telegram_api_async("createInvoiceLink", tg_payload)
    if not res.get("ok"):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Telegram billing interface error: {res.get('description', 'Unknown error')}"
        )

    return {"invoice_url": res["result"], "attempt_id": str(attempt.id), "discount_percent": discount_percent}


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
        "Idempotence-Key": secrets.token_hex(16),
    }
    req = urllib.request.Request(
        "https://api.yookassa.ru/v3/payments",
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"ЮKassa checkout error: {e}")

    attempt.provider_payment_id = data.get("id")
    await db.flush()

    return {
        "payment_id": data.get("id"),
        "attempt_id": str(attempt.id),
        "confirmation_url": data.get("confirmation", {}).get("confirmation_url"),
        "status": data.get("status"),
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


@router.post("/yookassa/webhook")
async def yookassa_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    update = await request.json()
    if update.get("event") != "payment.succeeded":
        return {"status": "ignored_event"}

    payment_obj = update.get("object", {})
    payment_id = payment_obj.get("id")
    if not payment_id:
        return {"status": "ignored_no_payment_id"}

    verified_payment = _request_yookassa_payment(payment_id)
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
        await db.commit()

        if result.get("status") == "success":
            user_id = result.get("user_id")
            if result.get("matches_added") is not None:
                confirm_text = f"✅ Абонемент успешно оформлен: +{result['matches_added']} матчей!"
            elif result.get("bet_id"):
                confirm_text = "✅ Прогноз успешно разблокирован!"
            else:
                confirm_text = "✅ Платеж успешно обработан!"
            if is_personal_telegram_user_id(user_id):
                run_telegram_api_background("sendMessage", {"chat_id": user_id, "text": confirm_text})

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
