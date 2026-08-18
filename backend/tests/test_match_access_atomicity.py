import asyncio
import os
import re
import unittest
import uuid
from contextlib import nullcontext
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.api import bets as bets_api
from src.models.database import Base
from src.models.models import (
    Bet,
    ForecastRequest,
    MatchBalanceLog,
    Subscription,
    SubscriptionPlan,
    User,
    user_bets,
)
from src.services import forecast_delivery
from src.services.match_access import (
    activate_match_subscription,
    load_locked_bet_for_user_access,
    lock_bet_row,
    record_user_bet_access,
    record_user_free_bet_access,
)


class MatchAccessAtomicityUnitTests(unittest.IsolatedAsyncioTestCase):
    """Transaction-shape and idempotency tests; SQLite does not prove row-lock behavior."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    @staticmethod
    def _bet() -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Team A - Team B",
            coefficient=Decimal("1.90"),
            status="pending",
            delivery_mode="feed",
            publication_type="forecast",
        )

    async def test_bet_lock_is_acquired_before_pending_state_is_flushed(self):
        events = []

        class FakeResult:
            @staticmethod
            def scalar_one_or_none():
                return uuid.uuid4()

        class FakeDb:
            no_autoflush = nullcontext()

            async def execute(self, _query):
                events.append("bet_lock")
                return FakeResult()

            async def flush(self):
                events.append("flush")

        await lock_bet_row(FakeDb(), uuid.uuid4())

        self.assertEqual(events, ["bet_lock", "flush"])

    async def test_fresh_bet_reload_preserves_pending_fields_by_flushing_after_lock(self):
        async with self.Session() as session:
            bet = self._bet()
            session.add(bet)
            await session.commit()

            bet.description = "pending admin edit"
            locked_bet = await load_locked_bet_for_user_access(session, bet.id)
            await session.commit()

            self.assertIs(locked_bet, bet)
            self.assertEqual(locked_bet.description, "pending admin edit")

        async with self.Session() as verification_session:
            persisted_bet = await verification_session.get(Bet, bet.id)
            self.assertEqual(persisted_bet.description, "pending admin edit")

    async def test_match_credit_is_debited_once_with_consistent_access_and_ledger(self):
        async with self.Session() as session:
            user = User(
                telegram_id=101,
                purchased_bets_balance=1,
                matches_remaining=1,
            )
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            first = await record_user_bet_access(
                session,
                user=user,
                bet=bet,
                charge_match=True,
            )
            await session.commit()
            second = await record_user_bet_access(
                session,
                user=user,
                bet=bet,
                charge_match=True,
            )
            await session.commit()

            await session.refresh(user)
            access_entries = (await session.execute(select(user_bets))).all()
            ledger_rows = (
                await session.execute(
                    select(MatchBalanceLog).filter(MatchBalanceLog.event_type == "match_debit")
                )
            ).scalars().all()

            self.assertFalse(first.already_recorded)
            self.assertTrue(second.already_recorded)
            self.assertEqual(user.purchased_bets_balance, 0)
            self.assertEqual(user.matches_remaining, 0)
            self.assertEqual(len(access_entries), 1)
            self.assertEqual(access_entries[0]._mapping["access_type"], "paid_match")
            self.assertTrue(access_entries[0]._mapping["match_charged"])
            self.assertEqual(len(ledger_rows), 1)
            self.assertEqual(ledger_rows[0].delta_matches, -1)
            self.assertEqual(ledger_rows[0].bet_id, bet.id)

    async def test_zero_match_balance_leaves_no_partial_access_or_ledger(self):
        async with self.Session() as session:
            user = User(
                telegram_id=102,
                purchased_bets_balance=0,
                matches_remaining=0,
            )
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            with self.assertRaises(HTTPException) as raised:
                await record_user_bet_access(
                    session,
                    user=user,
                    bet=bet,
                    charge_match=True,
                )
            await session.rollback()

            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            ledger_rows = int(
                (await session.execute(select(func.count(MatchBalanceLog.id)))).scalar() or 0
            )
            await session.refresh(user)

            self.assertEqual(raised.exception.status_code, 403)
            self.assertEqual(access_rows, 0)
            self.assertEqual(ledger_rows, 0)
            self.assertEqual(user.purchased_bets_balance, 0)
            self.assertEqual(user.matches_remaining, 0)

    async def test_free_credit_is_debited_once_and_never_becomes_negative(self):
        async with self.Session() as session:
            user = User(telegram_id=103, free_bets_available=1)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            first = await record_user_free_bet_access(session, user=user, bet=bet)
            await session.commit()
            second = await record_user_free_bet_access(session, user=user, bet=bet)
            await session.commit()

            await session.refresh(user)
            access_entries = (await session.execute(select(user_bets))).all()

            self.assertFalse(first.already_recorded)
            self.assertTrue(second.already_recorded)
            self.assertEqual(user.free_bets_available, 0)
            self.assertEqual(len(access_entries), 1)
            self.assertEqual(access_entries[0]._mapping["access_type"], "free_bet")
            self.assertFalse(access_entries[0]._mapping["match_charged"])

    async def test_zero_free_balance_leaves_no_partial_access(self):
        async with self.Session() as session:
            user = User(telegram_id=104, free_bets_available=0)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            with self.assertRaises(HTTPException) as raised:
                await record_user_free_bet_access(session, user=user, bet=bet)
            await session.rollback()

            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            await session.refresh(user)

            self.assertEqual(raised.exception.status_code, 400)
            self.assertEqual(access_rows, 0)
            self.assertEqual(user.free_bets_available, 0)

    async def test_balance_lock_preserves_pending_unrelated_user_fields(self):
        async with self.Session() as session:
            user = User(
                telegram_id=105,
                purchased_bets_balance=1,
                matches_remaining=1,
                vk_messages_allowed=False,
            )
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            user.vk_messages_allowed = True
            result = await record_user_bet_access(
                session,
                user=user,
                bet=bet,
                charge_match=True,
            )
            await session.commit()

        async with self.Session() as verification_session:
            persisted = await verification_session.get(User, 105)
            self.assertFalse(result.already_recorded)
            self.assertTrue(persisted.vk_messages_allowed)
            self.assertEqual(persisted.purchased_bets_balance, 0)
            self.assertEqual(persisted.matches_remaining, 0)

    async def test_fresh_lock_snapshot_marks_stale_equal_balance_for_update(self):
        async with self.Session() as session:
            user = User(telegram_id=106, purchased_bets_balance=1, matches_remaining=1)
            plan = SubscriptionPlan(
                name="One match",
                duration_days=0,
                price=Decimal("10.00"),
                currency="RUB",
                match_count=1,
                is_active=True,
            )
            session.add_all([user, plan])
            await session.commit()

            async with self.Session() as external_session:
                await external_session.execute(
                    update(User)
                    .where(User.telegram_id == user.telegram_id)
                    .values(purchased_bets_balance=0, matches_remaining=0)
                )
                await external_session.commit()

            self.assertEqual(user.purchased_bets_balance, 1)
            await activate_match_subscription(
                session,
                user=user,
                plan=plan,
                payment_provider="unit_test",
                payment_id=uuid.uuid4().hex,
            )
            await session.commit()

        async with self.Session() as verification_session:
            persisted = await verification_session.get(User, 106)
            self.assertEqual(persisted.purchased_bets_balance, 1)
            self.assertEqual(persisted.matches_remaining, 1)


_POSTGRES_URL = os.environ.get("SHAMRAI_TEST_POSTGRES_URL", "").strip()
_RUN_POSTGRES = os.environ.get("SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS") == "1"


def _postgres_async_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


@unittest.skipUnless(
    _POSTGRES_URL and _RUN_POSTGRES,
    "Set SHAMRAI_TEST_POSTGRES_URL and SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS=1 for PostgreSQL row-lock tests",
)
class MatchAccessPostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Real PostgreSQL tests for SELECT FOR UPDATE serialization."""

    async def asyncSetUp(self):
        self.schema = f"shamrai_access_test_{uuid.uuid4().hex}"
        if not re.fullmatch(r"shamrai_access_test_[0-9a-f]{32}", self.schema):
            raise RuntimeError("Unsafe PostgreSQL test schema name")

        postgres_url = _postgres_async_url(_POSTGRES_URL)
        self.admin_engine = create_async_engine(postgres_url, future=True)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))

        self.engine = create_async_engine(
            postgres_url,
            future=True,
            connect_args={
                "server_settings": {
                    "search_path": f'"{self.schema}",public',
                    "application_name": self.schema,
                }
            },
        )
        required_tables = {
            "bookmakers",
            "users",
            "user_bookmakers",
            "subscription_plans",
            "subscription_plan_checkout_allowlist",
            "flat_subscriptions",
            "subscriptions",
            "bets",
            "bet_bookmakers",
            "user_bets",
            "match_balance_logs",
            "forecast_requests",
        }
        tables = [table for table in Base.metadata.sorted_tables if table.name in required_tables]
        async with self.engine.begin() as connection:
            await connection.run_sync(lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables))
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        leaked_connection_count = 0
        async with self.admin_engine.begin() as connection:
            leaked_connection_count = int(
                (
                    await connection.execute(
                        text(
                            "SELECT count(*) FROM pg_stat_activity "
                            "WHERE application_name = :application_name "
                            "AND pid <> pg_backend_pid()"
                        ),
                        {"application_name": self.schema},
                    )
                ).scalar_one()
            )
            if leaked_connection_count:
                await connection.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE application_name = :application_name "
                        "AND pid <> pg_backend_pid()"
                    ),
                    {"application_name": self.schema},
                )
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()
        self.assertEqual(leaked_connection_count, 0, "PostgreSQL test leaked a connection")

    @staticmethod
    def _bet() -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Concurrent forecast",
            coefficient=Decimal("1.90"),
            status="pending",
            delivery_mode="feed",
            publication_type="forecast",
        )

    async def _run_parallel(self, operation, bet_ids):
        barrier = asyncio.Barrier(len(bet_ids))

        async def run_one(bet_id):
            async with self.Session() as session:
                user_result = await session.execute(
                    select(User)
                    .filter(User.telegram_id == 501)
                    .options(selectinload(User.bookmakers))
                )
                user = user_result.scalars().one()
                await barrier.wait()
                try:
                    result = await operation(session, user, bet_id)
                    await session.commit()
                    return result
                except HTTPException as exc:
                    await session.rollback()
                    return exc

        return await asyncio.gather(*(run_one(bet_id) for bet_id in bet_ids))

    async def test_one_match_credit_cannot_unlock_two_different_bets(self):
        bet_one, bet_two = self._bet(), self._bet()
        async with self.Session() as session:
            session.add_all([
                User(telegram_id=501, purchased_bets_balance=1, matches_remaining=1),
                bet_one,
                bet_two,
            ])
            await session.commit()

        async def take(session, user, bet_id):
            return await bets_api.take_bet(bet_id, current_user=user, db=session)

        results = await self._run_parallel(take, [bet_one.id, bet_two.id])

        async with self.Session() as session:
            user = await session.get(User, 501)
            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            ledger_rows = int(
                (
                    await session.execute(
                        select(func.count(MatchBalanceLog.id)).filter(
                            MatchBalanceLog.event_type == "match_debit"
                        )
                    )
                ).scalar()
                or 0
            )

        self.assertEqual(sum(not isinstance(result, HTTPException) for result in results), 1)
        self.assertEqual(sum(isinstance(result, HTTPException) and result.status_code == 403 for result in results), 1)
        self.assertEqual(user.purchased_bets_balance, 0)
        self.assertEqual(user.matches_remaining, 0)
        self.assertEqual(access_rows, 1)
        self.assertEqual(ledger_rows, 1)

    async def test_one_free_credit_cannot_unlock_two_different_bets(self):
        bet_one, bet_two = self._bet(), self._bet()
        async with self.Session() as session:
            session.add_all([User(telegram_id=501, free_bets_available=1), bet_one, bet_two])
            await session.commit()

        async def unlock(session, user, bet_id):
            return await bets_api.unlock_free_bet(bet_id, current_user=user, db=session)

        results = await self._run_parallel(unlock, [bet_one.id, bet_two.id])

        async with self.Session() as session:
            user = await session.get(User, 501)
            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )

        self.assertEqual(sum(not isinstance(result, HTTPException) for result in results), 1)
        self.assertEqual(sum(isinstance(result, HTTPException) and result.status_code == 400 for result in results), 1)
        self.assertEqual(user.free_bets_available, 0)
        self.assertEqual(access_rows, 1)

    async def test_parallel_requests_for_same_bet_are_idempotent(self):
        bet = self._bet()
        async with self.Session() as session:
            session.add_all([
                User(telegram_id=501, purchased_bets_balance=1, matches_remaining=1),
                bet,
            ])
            await session.commit()

        async def take(session, user, bet_id):
            return await bets_api.take_bet(bet_id, current_user=user, db=session)

        results = await self._run_parallel(take, [bet.id, bet.id])

        async with self.Session() as session:
            user = await session.get(User, 501)
            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            ledger_rows = int(
                (await session.execute(select(func.count(MatchBalanceLog.id)))).scalar() or 0
            )

        self.assertEqual(sum(result.get("status") == "already_taken" for result in results), 1)
        self.assertEqual(user.purchased_bets_balance, 0)
        self.assertEqual(user.matches_remaining, 0)
        self.assertEqual(access_rows, 1)
        self.assertEqual(ledger_rows, 1)

    async def test_subscription_credit_and_bet_debit_do_not_overwrite_each_other(self):
        bet = self._bet()
        async with self.Session() as session:
            plan = SubscriptionPlan(
                name="Concurrent plan",
                duration_days=0,
                price=Decimal("100.00"),
                currency="RUB",
                match_count=2,
                is_active=True,
            )
            session.add_all([
                User(telegram_id=501, purchased_bets_balance=1, matches_remaining=1),
                bet,
                plan,
            ])
            await session.commit()
            plan_id = plan.id

        barrier = asyncio.Barrier(2)

        async def take_one():
            async with self.Session() as session:
                user = await session.get(User, 501)
                await barrier.wait()
                result = await bets_api.take_bet(bet.id, current_user=user, db=session)
                await session.commit()
                return result

        async def credit_subscription():
            async with self.Session() as session:
                user = await session.get(User, 501)
                plan = await session.get(SubscriptionPlan, plan_id)
                await barrier.wait()
                await activate_match_subscription(
                    session,
                    user=user,
                    plan=plan,
                    payment_provider="concurrency_test",
                    payment_id=uuid.uuid4().hex,
                )
                await session.commit()

        await asyncio.gather(take_one(), credit_subscription())

        async with self.Session() as session:
            user = await session.get(User, 501)
            access_rows = int(
                (await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            subscription_rows = int(
                (await session.execute(select(func.count(Subscription.id)))).scalar() or 0
            )
            ledger_deltas = list(
                (
                    await session.execute(
                        select(MatchBalanceLog.delta_matches).order_by(MatchBalanceLog.created_at)
                    )
                ).scalars().all()
            )

        self.assertEqual(user.purchased_bets_balance, 2)
        self.assertEqual(user.matches_remaining, 2)
        self.assertEqual(access_rows, 1)
        self.assertEqual(subscription_rows, 1)
        self.assertCountEqual(ledger_deltas, [-1, 2])

    async def test_delivery_waits_for_bet_lock_and_rejects_fresh_deleted_lifecycle(self):
        bet = Bet(
            id=uuid.uuid4(),
            event_name="Concurrent private forecast",
            coefficient=Decimal("1.90"),
            status="pending",
            delivery_mode="sales_private",
            publication_type="forecast",
        )
        request = ForecastRequest(
            id=uuid.uuid4(),
            bet_id=bet.id,
            user_id=501,
            status="interested",
        )
        async with self.Session() as setup_session:
            setup_session.add_all([
                User(telegram_id=501, purchased_bets_balance=1, matches_remaining=1),
                bet,
                request,
            ])
            await setup_session.commit()

        async with self.Session() as delivery_session, self.Session() as admin_session:
            stale_request = await forecast_delivery.load_forecast_request(delivery_session, request.id)
            locked_bet = (
                await admin_session.execute(
                    select(Bet).filter(Bet.id == bet.id).with_for_update()
                )
            ).scalars().one()
            locked_request = (
                await admin_session.execute(
                    select(ForecastRequest)
                    .filter(ForecastRequest.id == request.id)
                    .with_for_update()
                )
            ).scalars().one()
            locked_bet.status = "deleted"
            locked_request.status = "removed"
            await admin_session.flush()

            entered_bet_lock = asyncio.Event()
            original_loader = forecast_delivery.load_locked_bet_for_user_access

            async def instrumented_loader(db, bet_id):
                entered_bet_lock.set()
                return await original_loader(db, bet_id)

            forecast_delivery.load_locked_bet_for_user_access = instrumented_loader
            try:
                delivery_task = asyncio.create_task(
                    forecast_delivery.deliver_forecast_request(
                        delivery_session,
                        forecast_request=stale_request,
                        handled_by=900,
                        delivery_method="manual",
                        send_to_client=False,
                        commit=False,
                    )
                )
                await asyncio.wait_for(entered_bet_lock.wait(), timeout=2)
                await admin_session.commit()
                with self.assertRaises(HTTPException) as raised:
                    await asyncio.wait_for(delivery_task, timeout=5)
                await delivery_session.rollback()
            finally:
                forecast_delivery.load_locked_bet_for_user_access = original_loader

        async with self.Session() as verification_session:
            persisted_user = await verification_session.get(User, 501)
            persisted_request = await verification_session.get(ForecastRequest, request.id)
            access_rows = int(
                (await verification_session.execute(select(func.count()).select_from(user_bets))).scalar() or 0
            )
            debit_rows = int(
                (
                    await verification_session.execute(
                        select(func.count(MatchBalanceLog.id)).filter(
                            MatchBalanceLog.event_type == "match_debit"
                        )
                    )
                ).scalar()
                or 0
            )

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(persisted_request.status, "removed")
        self.assertEqual(persisted_user.purchased_bets_balance, 1)
        self.assertEqual(persisted_user.matches_remaining, 1)
        self.assertEqual(access_rows, 0)
        self.assertEqual(debit_rows, 0)


if __name__ == "__main__":
    unittest.main()
