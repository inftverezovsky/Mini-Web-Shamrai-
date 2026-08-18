import unittest
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import payments
from src.models.database import Base
from src.models.models import DeliveryOutbox, PaymentAttempt, Subscription, SubscriptionPlan, User
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


class PaymentOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_process_payment_attempt_is_idempotent_after_success(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as session:
                user = User(telegram_id=12345, purchased_bets_balance=0, matches_remaining=0)
                plan = SubscriptionPlan(
                    id=1,
                    name="Test subscription",
                    duration_days=30,
                    match_count=5,
                    price=Decimal("100.00"),
                    price_stars=0,
                    currency="RUB",
                    is_active=True,
                )
                attempt = PaymentAttempt(
                    user_id=user.telegram_id,
                    plan_id=plan.id,
                    provider="yookassa",
                    amount=Decimal("100.00"),
                    currency="RUB",
                    status="pending",
                    plan_name_snapshot=plan.name,
                    entitlement_type_snapshot="legacy_match",
                    match_count_snapshot=plan.match_count,
                    metadata_json={},
                )
                session.add_all([user, plan, attempt])
                await session.commit()

                first_result = await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt.id,
                    provider="yookassa",
                    provider_payment_id="payment-1",
                    amount=Decimal("100.00"),
                    currency="RUB",
                    raw_payload={"id": "payment-1"},
                )
                await session.commit()

                second_result = await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt.id,
                    provider="yookassa",
                    provider_payment_id="payment-1",
                    amount=Decimal("100.00"),
                    currency="RUB",
                    raw_payload={"id": "payment-1"},
                )
                await session.commit()

                subscription_count = (await session.execute(select(func.count(Subscription.id)))).scalar_one()
                refreshed_user = (
                    await session.execute(select(User).filter(User.telegram_id == user.telegram_id))
                ).scalars().first()
                refreshed_attempt = (
                    await session.execute(select(PaymentAttempt).filter(PaymentAttempt.id == attempt.id))
                ).scalars().first()

            self.assertEqual(first_result["status"], "success")
            self.assertEqual(second_result["status"], "already_processed")
            self.assertEqual(subscription_count, 1)
            self.assertEqual(refreshed_user.matches_remaining, 5)
            self.assertEqual(refreshed_attempt.status, "succeeded")
            self.assertEqual(refreshed_attempt.provider_payment_id, "payment-1")
            self.assertIsNotNone(refreshed_attempt.processing_started_at)
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    async def test_successful_payment_confirmation_is_enqueued(self):
        db = FakeDb()

        await payments._enqueue_payment_confirmation(
            db,
            {
                "status": "success",
                "attempt_id": "attempt-1",
                "user_id": 12345,
                "matches_added": 3,
            },
        )

        self.assertEqual(len(db.added), 1)
        item = db.added[0]
        self.assertIsInstance(item, DeliveryOutbox)
        self.assertEqual(item.channel, CHANNEL_TELEGRAM_MESSAGE)
        self.assertEqual(item.dedupe_key, "payment_attempt:attempt-1:telegram_confirmation")
        self.assertEqual(item.payload["method"], "sendMessage")
        self.assertEqual(item.payload["payload"]["chat_id"], 12345)
        self.assertIn("+3", item.payload["payload"]["text"])

    async def test_payment_confirmation_skips_non_personal_user(self):
        db = FakeDb()

        await payments._enqueue_payment_confirmation(
            db,
            {
                "status": "success",
                "attempt_id": "attempt-1",
                "user_id": -12345,
                "bet_id": "bet-1",
            },
        )

        self.assertEqual(db.added, [])

    async def test_processed_payment_payload_keeps_audit_subset_without_contact_data(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as session:
                user = User(telegram_id=12346, purchased_bets_balance=0, matches_remaining=0)
                plan = SubscriptionPlan(
                    id=1,
                    name="Test subscription",
                    duration_days=30,
                    match_count=5,
                    price=Decimal("100.00"),
                    price_stars=0,
                    currency="RUB",
                    is_active=True,
                )
                attempt = PaymentAttempt(
                    user_id=user.telegram_id,
                    plan_id=plan.id,
                    provider="yookassa",
                    amount=Decimal("100.00"),
                    currency="RUB",
                    status="pending",
                    plan_name_snapshot=plan.name,
                    entitlement_type_snapshot="legacy_match",
                    match_count_snapshot=plan.match_count,
                    metadata_json={},
                )
                session.add_all([user, plan, attempt])
                await session.commit()

                await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt.id,
                    provider="yookassa",
                    provider_payment_id="payment-1",
                    amount=Decimal("100.00"),
                    currency="RUB",
                    raw_payload={
                        "id": "payment-1",
                        "status": "succeeded",
                        "amount": {"value": "100.00", "currency": "RUB"},
                        "metadata": {"attempt_id": str(attempt.id), "phone": "+79990001122"},
                        "customer": {"email": "client@example.com", "phone": "+79990001122"},
                        "payment_method": {"card": {"last4": "1111"}},
                    },
                )
                await session.commit()
                refreshed_attempt = await session.get(PaymentAttempt, attempt.id)

            processed_payload = (refreshed_attempt.metadata_json or {}).get("processed_payload")
            self.assertEqual(processed_payload["provider"], "yookassa")
            self.assertEqual(processed_payload["id"], "payment-1")
            self.assertEqual(processed_payload["metadata"], {"attempt_id": str(attempt.id)})
            serialized = str(processed_payload)
            self.assertNotIn("client@example.com", serialized)
            self.assertNotIn("+79990001122", serialized)
            self.assertNotIn("payment_method", processed_payload)
            self.assertNotIn("customer", processed_payload)
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    def test_processed_payment_payload_drops_freeform_sensitive_strings(self):
        processed_payload = payments._processed_payment_payload(
            "yookassa",
            {
                "id": "payment-1",
                "status": "succeeded",
                "description": "client@example.com paid with token secret-123",
                "debug": "phone +79990001122 bearer token",
                "metadata": {"attempt_id": "attempt-1", "phone": "+79990001122"},
            },
        )

        serialized = str(processed_payload)
        self.assertEqual(processed_payload["id"], "payment-1")
        self.assertEqual(processed_payload["metadata"], {"attempt_id": "attempt-1"})
        self.assertNotIn("description", processed_payload)
        self.assertNotIn("debug", processed_payload)
        self.assertNotIn("client@example.com", serialized)
        self.assertNotIn("+79990001122", serialized)


if __name__ == "__main__":
    unittest.main()
