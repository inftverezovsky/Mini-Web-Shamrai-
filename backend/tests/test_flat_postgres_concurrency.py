import asyncio
import os
import re
import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import payments
from src.models.database import Base
from src.models.models import (
    Bet,
    FlatSubscription,
    FlatSubscriptionCredit,
    PaymentAttempt,
    Subscription,
    SubscriptionPlan,
    User,
    user_bets,
)
from src.services.flat_subscriptions import (
    correct_flat_bet_stake,
    credit_flat_subscription,
    record_user_flat_bet_access,
    settle_flat_bet_takers,
)


_POSTGRES_URL = os.environ.get("SHAMRAI_TEST_POSTGRES_URL", "").strip()
_RUN_POSTGRES = os.environ.get("SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS") == "1"


def _postgres_async_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


@unittest.skipUnless(
    _POSTGRES_URL and _RUN_POSTGRES,
    "Set SHAMRAI_TEST_POSTGRES_URL and SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS=1",
)
class FlatPostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.schema = f"shamrai_flat_test_{uuid.uuid4().hex}"
        if not re.fullmatch(r"shamrai_flat_test_[0-9a-f]{32}", self.schema):
            raise RuntimeError("Unsafe PostgreSQL test schema name")
        postgres_url = _postgres_async_url(_POSTGRES_URL)
        self.admin_engine = create_async_engine(postgres_url, future=True)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            postgres_url,
            future=True,
            connect_args={"server_settings": {"search_path": f'"{self.schema}",public'}},
        )
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    @staticmethod
    def _plan() -> SubscriptionPlan:
        return SubscriptionPlan(
            name="Скрытый тест +3",
            duration_days=0,
            match_count=0,
            entitlement_type="flat",
            target_flats=Decimal("3.00"),
            price=Decimal("1490.00"),
            currency="RUB",
            is_active=True,
        )

    async def test_parallel_checkout_with_same_intent_creates_one_attempt(self):
        async with self.Session() as session:
            user = User(telegram_id=8101, role="user", purchased_bets_balance=0, matches_remaining=0)
            plan = self._plan()
            session.add_all([user, plan])
            await session.commit()
            plan_id = plan.id

        intent = uuid.uuid4()
        payload_hash = payments._checkout_request_hash(
            provider="yookassa",
            user_id=8101,
            plan_id=plan_id,
        )
        barrier = asyncio.Barrier(2)

        async def create_checkout():
            async with self.Session() as session:
                user = await session.get(User, 8101)
                plan = await session.get(SubscriptionPlan, plan_id)
                await barrier.wait()
                attempt, created = await payments._create_or_reuse_checkout_attempt(
                    session,
                    user=user,
                    provider="yookassa",
                    checkout_intent_id=intent,
                    checkout_payload_hash=payload_hash,
                    amount=Decimal("1490.00"),
                    currency="RUB",
                    plan=plan,
                )
                await session.commit()
                return attempt.id, created

        results = await asyncio.wait_for(
            asyncio.gather(create_checkout(), create_checkout()),
            timeout=15,
        )
        async with self.Session() as session:
            count = int((await session.execute(select(func.count(PaymentAttempt.id)))).scalar() or 0)

        self.assertEqual(results[0][0], results[1][0])
        self.assertEqual(sum(1 for _, created in results if created), 1)
        self.assertEqual(count, 1)

    async def test_parallel_stars_checkout_with_different_intents_creates_one_attempt_and_invoice(self):
        async with self.Session() as session:
            user = User(telegram_id=8110, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.commit()
            bet_id = bet.id

        start_barrier = asyncio.Barrier(2)
        provider_started = asyncio.Event()
        release_provider = asyncio.Event()

        async def create_provider_link(**_kwargs):
            provider_started.set()
            await release_provider.wait()
            return "https://invoice.invalid/postgres-one"

        invoice_link = AsyncMock(side_effect=create_provider_link)

        async def create_checkout(intent_id: uuid.UUID):
            async with self.Session() as session:
                user = await session.get(User, 8110)
                await start_barrier.wait()
                try:
                    response = await payments.create_stars_invoice(
                        payments.InvoiceRequest(bet_id=bet_id),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )
                    return ("ok", response)
                except HTTPException as exc:
                    await session.rollback()
                    return ("error", exc.status_code)

        with patch.object(payments, "create_telegram_stars_invoice_link", new=invoice_link):
            tasks = [
                asyncio.create_task(create_checkout(uuid.uuid4())),
                asyncio.create_task(create_checkout(uuid.uuid4())),
            ]
            await asyncio.wait_for(provider_started.wait(), timeout=10)
            await asyncio.sleep(0.2)
            release_provider.set()
            results = await asyncio.wait_for(asyncio.gather(*tasks), timeout=15)

        async with self.Session() as session:
            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())

        successful = [payload for outcome, payload in results if outcome == "ok"]
        errors = [payload for outcome, payload in results if outcome == "error"]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(invoice_link.await_count, 1)
        self.assertGreaterEqual(len(successful), 1)
        self.assertTrue(all(item["attempt_id"] == str(attempts[0].id) for item in successful))
        self.assertTrue(all(code == 425 for code in errors))
        self.assertEqual(attempts[0].checkout_state, "ready")

    async def test_parallel_stars_precheckout_queries_reserve_only_one(self):
        async with self.Session() as session:
            user = User(telegram_id=8111, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.80"),
                status="pending",
                price_stars=50,
            )
            session.add_all([user, bet])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                bet_id=bet.id,
                provider="telegram_stars",
                amount=Decimal("50.00"),
                currency="XTR",
                checkout_state="ready",
                checkout_url="https://invoice.invalid/postgres-reservation",
                purchase_type_snapshot=payments.PAYMENT_PURCHASE_SINGLE_BET,
                metadata_json={"purchase_type": "single_bet"},
                status="pending",
            )
            session.add(attempt)
            await session.commit()
            attempt_id = attempt.id

        start_barrier = asyncio.Barrier(2)
        answers: list[tuple[str, bool]] = []

        async def answer_precheckout(_method, payload, *_args):
            answers.append((payload["pre_checkout_query_id"], bool(payload["ok"])))
            return {"ok": True}

        async def reserve(query_id: str):
            async with self.Session() as session:
                await start_barrier.wait()
                return await payments.process_telegram_payment_update(
                    {
                        "pre_checkout_query": {
                            "id": query_id,
                            "from": {"id": 8111},
                            "invoice_payload": str(attempt_id),
                            "total_amount": 50,
                            "currency": "XTR",
                        }
                    },
                    session,
                )

        telegram_api = AsyncMock(side_effect=answer_precheckout)
        with patch.object(payments, "call_telegram_api_async", new=telegram_api):
            results = await asyncio.wait_for(
                asyncio.gather(reserve("postgres-query-a"), reserve("postgres-query-b")),
                timeout=15,
            )

        async with self.Session() as session:
            attempt = await session.get(PaymentAttempt, attempt_id)

        self.assertEqual(
            sorted(result["status"] for result in results),
            ["pre_checkout_answered", "pre_checkout_rejected"],
        )
        self.assertEqual(sorted(ok for _query_id, ok in answers), [False, True])
        self.assertEqual(attempt.status, "processing")
        self.assertIn(
            attempt.telegram_pre_checkout_query_id,
            {"postgres-query-a", "postgres-query-b"},
        )
        self.assertEqual(attempt.telegram_pre_checkout_user_id, 8111)

    async def test_parallel_webhook_is_once_and_uses_archived_plan_snapshot(self):
        async with self.Session() as session:
            user = User(telegram_id=8102, role="user", purchased_bets_balance=0, matches_remaining=0)
            plan = self._plan()
            session.add_all([user, plan])
            await session.flush()
            attempt = PaymentAttempt(
                user_id=user.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("1490.00"),
                currency="RUB",
                status="pending",
                checkout_state="ready",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=0,
                discount_percent_snapshot=0,
            )
            session.add(attempt)
            await session.flush()
            attempt_id = attempt.id
            plan.is_active = False
            plan.entitlement_type = "legacy_match"
            plan.target_flats = None
            plan.match_count = 99
            await session.commit()

        barrier = asyncio.Barrier(2)

        async def process_webhook():
            async with self.Session() as session:
                await barrier.wait()
                result = await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt_id,
                    provider="yookassa",
                    provider_payment_id="postgres-idempotent-payment",
                    amount=Decimal("1490.00"),
                    currency="RUB",
                    raw_payload={"status": "succeeded"},
                )
                await session.commit()
                return result["status"]

        statuses = await asyncio.wait_for(
            asyncio.gather(process_webhook(), process_webhook()),
            timeout=15,
        )
        async with self.Session() as session:
            subscription_count = int((await session.execute(select(func.count(Subscription.id)))).scalar() or 0)
            credit_count = int((await session.execute(select(func.count(FlatSubscriptionCredit.id)))).scalar() or 0)
            flat_subscription = (await session.execute(select(FlatSubscription))).scalars().one()

        self.assertEqual(sorted(statuses), ["already_processed", "success"])
        self.assertEqual(subscription_count, 1)
        self.assertEqual(credit_count, 1)
        self.assertEqual(flat_subscription.target_flats, Decimal("3.00"))

    async def test_parallel_distinct_purchases_extend_one_subscription(self):
        async with self.Session() as session:
            user = User(telegram_id=8103, role="user", purchased_bets_balance=0, matches_remaining=0)
            plan = self._plan()
            session.add_all([user, plan])
            await session.flush()
            attempts = [
                PaymentAttempt(
                    user_id=user.telegram_id,
                    plan_id=plan.id,
                    provider="yookassa",
                    amount=Decimal("1490.00"),
                    currency="RUB",
                    status="pending",
                    checkout_state="ready",
                    plan_name_snapshot=plan.name,
                    entitlement_type_snapshot="flat",
                    target_flats_snapshot=Decimal("3.00"),
                    match_count_snapshot=0,
                    discount_percent_snapshot=0,
                )
                for _ in range(2)
            ]
            session.add_all(attempts)
            await session.flush()
            attempt_ids = [attempt.id for attempt in attempts]
            await session.commit()

        barrier = asyncio.Barrier(2)

        async def process_purchase(attempt_id: uuid.UUID, payment_id: str):
            async with self.Session() as session:
                await barrier.wait()
                result = await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt_id,
                    provider="yookassa",
                    provider_payment_id=payment_id,
                    amount=Decimal("1490.00"),
                    currency="RUB",
                )
                await session.commit()
                return result["status"]

        statuses = await asyncio.wait_for(
            asyncio.gather(
                process_purchase(attempt_ids[0], "parallel-purchase-1"),
                process_purchase(attempt_ids[1], "parallel-purchase-2"),
            ),
            timeout=15,
        )
        async with self.Session() as session:
            flat_subscriptions = list((await session.execute(select(FlatSubscription))).scalars().all())
            credit_count = int((await session.execute(select(func.count(FlatSubscriptionCredit.id)))).scalar() or 0)

        self.assertEqual(statuses, ["success", "success"])
        self.assertEqual(len(flat_subscriptions), 1)
        self.assertEqual(flat_subscriptions[0].target_flats, Decimal("6.00"))
        self.assertEqual(credit_count, 2)

    async def test_parallel_settlement_and_stake_correction_remain_consistent(self):
        async with self.Session() as session:
            user = User(telegram_id=8104, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = Bet(
                event_name="A — B",
                coefficient=Decimal("1.70"),
                status="pending",
                delivery_mode="feed",
                publication_type="forecast",
            )
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000.00"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=bet,
                stake_rub=Decimal("5000.00"),
            )
            await session.commit()
            initial_revision = subscription.revision
            bet_id = bet.id

        barrier = asyncio.Barrier(2)

        async def settle():
            async with self.Session() as session:
                bet = await session.get(Bet, bet_id)
                await barrier.wait()
                await settle_flat_bet_takers(session, bet=bet, result_status="win")
                await session.commit()
                return "settled"

        async def correct():
            async with self.Session() as session:
                await barrier.wait()
                try:
                    await correct_flat_bet_stake(
                        session,
                        user_id=8104,
                        bet_id=bet_id,
                        stake_rub=Decimal("7500.00"),
                        expected_revision=initial_revision,
                        actor_id=None,
                        note="PostgreSQL race test",
                    )
                    await session.commit()
                    return "corrected"
                except HTTPException as exc:
                    await session.rollback()
                    return f"conflict:{exc.status_code}"

        outcomes = await asyncio.wait_for(asyncio.gather(settle(), correct()), timeout=15)
        async with self.Session() as session:
            row = (
                await session.execute(
                    select(user_bets).filter(
                        user_bets.c.user_id == 8104,
                        user_bets.c.bet_id == bet_id,
                    )
                )
            ).one()._mapping
            subscription = (await session.execute(select(FlatSubscription))).scalars().one()

        expected_profit = (Decimal(str(row["stake_rub"])) * Decimal("0.70")).quantize(Decimal("0.01"))
        self.assertIn(outcomes[1], {"corrected", "conflict:409"})
        self.assertEqual(row["settled_status"], "win")
        self.assertEqual(row["profit_rub"], expected_profit)
        self.assertEqual(subscription.profit_rub, expected_profit)
        self.assertGreater(subscription.revision, initial_revision)


if __name__ == "__main__":
    unittest.main()
