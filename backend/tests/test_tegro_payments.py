import hashlib
import hmac
import json
import urllib.parse
import unittest
from decimal import Decimal
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import payments
from src.models.database import Base
from src.models.models import PaymentAttempt, Subscription, SubscriptionPlan, User


def signed_tegro_payload(fields: dict[str, str], secret: str) -> dict[str, str]:
    query = payments._tegro_signature_query(fields)
    return {
        **fields,
        "sign": hashlib.md5((query + secret).encode("utf-8")).hexdigest(),
    }


class TegroSigningTests(unittest.TestCase):
    def test_tegro_json_request_signature_is_hmac_sha256(self):
        body = json.dumps({"shop_id": "shop", "nonce": 1}, separators=(",", ":"))
        expected = hmac.new(b"api-key", body.encode("utf-8"), hashlib.sha256).hexdigest()

        self.assertEqual(payments._tegro_sign_json_body(body, "api-key"), expected)

    def test_tegro_webhook_signature_uses_sorted_url_encoded_fields(self):
        payload = signed_tegro_payload(
            {
                "shop_id": "shop-1",
                "amount": "100.00",
                "order_id": str(uuid4()),
                "payment_system": "sbp",
                "currency": "RUB",
            },
            "secret",
        )

        with patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret"):
            result = payments._verify_tegro_notification(payload)

        self.assertEqual(result["amount"], Decimal("100.00"))
        self.assertEqual(result["currency"], "RUB")

    def test_tegro_webhook_rejects_bad_signature(self):
        payload = signed_tegro_payload(
            {
                "shop_id": "shop-1",
                "amount": "100.00",
                "order_id": str(uuid4()),
                "currency": "RUB",
            },
            "secret",
        )
        payload["amount"] = "200.00"

        with patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret"):
            with self.assertRaises(payments.TegroWebhookSignatureError) as ctx:
                payments._verify_tegro_notification(payload)

        self.assertEqual(ctx.exception.code, "bad_signature")

    def test_tegro_payment_form_url_uses_secret_signature(self):
        with patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret"):
            with patch.object(payments.settings, "TEGRO_SHOP_ID", "shop-1"):
                url = payments._create_tegro_payment_url(
                    {
                        "shop_id": "shop-1",
                        "amount": "100.00",
                        "order_id": "order-1",
                        "lang": "ru",
                        "currency": "RUB",
                        "receipt": {"items": [{"name": "Pack", "count": 1, "price": "100.00"}]},
                    }
                )

        self.assertTrue(url.startswith("https://tegro.money/pay/?"))
        self.assertIn("shop_id=shop-1", url)
        self.assertIn("order_id=order-1", url)
        self.assertIn("sign=", url)

    def test_tegro_payment_form_signature_uses_only_required_fields(self):
        with patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret"):
            url = payments._create_tegro_payment_url(
                {
                    "shop_id": "shop-1",
                    "amount": "100.00",
                    "order_id": "order-1",
                    "lang": "ru",
                    "currency": "RUB",
                    "receipt": {"items": [{"name": "Pack", "count": 1, "price": "100.00"}]},
                }
            )

        params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        signed_fields = {
            "amount": "100.00",
            "currency": "RUB",
            "order_id": "order-1",
            "shop_id": "shop-1",
        }
        expected_sign = hashlib.md5(
            (payments._tegro_signature_query(signed_fields) + "secret").encode("utf-8")
        ).hexdigest()

        self.assertEqual(params["sign"], expected_sign)


class TegroPaymentProcessingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_verified_tegro_attempt_activates_match_subscription_once(self):
        async with self.Session() as session:
            user = User(telegram_id=303, username="client", matches_remaining=0)
            plan = SubscriptionPlan(
                id=1,
                name="Starter",
                duration_days=30,
                match_count=3,
                price=Decimal("900.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                plan_id=plan.id,
                provider="tegro",
                amount=Decimal("900.00"),
                currency="RUB",
                status="pending",
                metadata_json={},
            )
            session.add_all([user, plan, attempt])
            await session.commit()

            first = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="tegro",
                provider_payment_id="tegro-payment-1",
                amount=Decimal("900.00"),
                currency="RUB",
                raw_payload={"order_id": str(attempt.id)},
            )
            await session.commit()
            second = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="tegro",
                provider_payment_id="tegro-payment-1",
                amount=Decimal("900.00"),
                currency="RUB",
                raw_payload={"order_id": str(attempt.id)},
            )
            await session.commit()

            subscription_count = (await session.execute(select(func.count(Subscription.id)))).scalar_one()
            refreshed_user = (
                await session.execute(select(User).filter(User.telegram_id == user.telegram_id))
            ).scalars().first()

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "already_processed")
        self.assertEqual(subscription_count, 1)
        self.assertEqual(refreshed_user.matches_remaining, 3)

    async def test_tegro_attempt_rejects_amount_mismatch(self):
        async with self.Session() as session:
            user = User(telegram_id=304, username="client")
            plan = SubscriptionPlan(
                id=1,
                name="Starter",
                duration_days=30,
                match_count=3,
                price=Decimal("900.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                plan_id=plan.id,
                provider="tegro",
                amount=Decimal("900.00"),
                currency="RUB",
                status="pending",
                metadata_json={},
            )
            session.add_all([user, plan, attempt])
            await session.commit()

            with self.assertRaises(HTTPException) as ctx:
                await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt.id,
                    provider="tegro",
                    provider_payment_id="tegro-payment-1",
                    amount=Decimal("899.00"),
                    currency="RUB",
                    raw_payload={"order_id": str(attempt.id)},
                )

        self.assertEqual(ctx.exception.status_code, 403)


class SubscriptionPurchaseGuardTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscription_purchase_guard_rejects_disabled_checkout(self):
        with patch.object(payments, "is_system_setting_enabled", new=AsyncMock(return_value=False)):
            with self.assertRaises(HTTPException) as ctx:
                await payments._ensure_subscription_purchases_enabled(object())

        self.assertEqual(ctx.exception.status_code, 403)

    async def test_subscription_purchase_guard_allows_enabled_checkout(self):
        with patch.object(payments, "is_system_setting_enabled", new=AsyncMock(return_value=True)) as enabled:
            await payments._ensure_subscription_purchases_enabled(object())

        enabled.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
