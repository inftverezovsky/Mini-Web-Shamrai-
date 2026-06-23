import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException, status

from src.api import payments
from src.models.models import PaymentAttempt


class _ProviderResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self) -> bytes:
        return self._body


class PaymentProviderErrorTests(unittest.TestCase):
    def test_yookassa_verification_error_does_not_leak_provider_details(self):
        with (
            patch.object(payments.settings, "YOOKASSA_SHOP_ID", "shop-id"),
            patch.object(payments.settings, "YOOKASSA_SECRET_KEY", "secret-key"),
            patch.object(
                payments,
                "_open_payment_provider_request",
                side_effect=RuntimeError("provider body with access_token=secret"),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                payments._request_yookassa_payment("payment-id")

        self.assertEqual(context.exception.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(context.exception.detail, payments.YOOKASSA_VERIFICATION_ERROR)
        self.assertNotIn("access_token", context.exception.detail)
        self.assertNotIn("secret", context.exception.detail.lower())

    def test_tegro_checkout_request_error_does_not_leak_provider_details(self):
        with (
            patch.object(payments.settings, "TEGRO_SHOP_ID", "shop-id"),
            patch.object(payments.settings, "TEGRO_API_KEY", "api-key"),
            patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret-key"),
            patch.object(
                payments,
                "_open_payment_provider_request",
                side_effect=RuntimeError("provider body with token=secret"),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                payments._create_tegro_order({"shop_id": "shop-id", "amount": "100", "order_id": "order-1"})

        self.assertEqual(context.exception.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(context.exception.detail, payments.TEGRO_CHECKOUT_ERROR)
        self.assertNotIn("token", context.exception.detail)
        self.assertNotIn("secret", context.exception.detail.lower())

    def test_tegro_checkout_error_envelope_returns_generic_message(self):
        provider_body = b'{"type":"error","message":"access_token=secret","data":{"debug":"secret"}}'

        with (
            patch.object(payments.settings, "TEGRO_SHOP_ID", "shop-id"),
            patch.object(payments.settings, "TEGRO_API_KEY", "api-key"),
            patch.object(payments.settings, "TEGRO_SECRET_KEY", "secret-key"),
            patch.object(
                payments,
                "_open_payment_provider_request",
                return_value=_ProviderResponse(provider_body),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                payments._create_tegro_order({"shop_id": "shop-id", "amount": "100", "order_id": "order-1"})

        self.assertEqual(context.exception.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(context.exception.detail, payments.TEGRO_CHECKOUT_ERROR)
        self.assertNotIn("access_token", context.exception.detail)
        self.assertNotIn("secret", context.exception.detail.lower())


class TelegramStarsProviderErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_stars_checkout_error_does_not_leak_provider_details(self):
        attempt = PaymentAttempt(id=uuid4(), amount=Decimal("10"))

        with (
            patch.object(payments.settings, "DEBUG_MODE", False),
            patch.object(payments.settings, "TELEGRAM_BOT_TOKEN", "987654:real-token"),
            patch.object(
                payments,
                "call_telegram_api_async",
                AsyncMock(return_value={"ok": False, "error_code": 400, "description": "access_token=secret"}),
            ),
        ):
            with self.assertRaises(HTTPException) as context:
                await payments.create_telegram_stars_invoice_link(
                    attempt=attempt,
                    title="Forecast",
                    description="Forecast access",
                    label="Forecast",
                )

        self.assertEqual(context.exception.status_code, status.HTTP_502_BAD_GATEWAY)
        self.assertEqual(context.exception.detail, payments.TELEGRAM_STARS_CHECKOUT_ERROR)
        self.assertNotIn("access_token", context.exception.detail)
        self.assertNotIn("secret", context.exception.detail.lower())


if __name__ == "__main__":
    unittest.main()
