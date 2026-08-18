import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import bets as bets_api
from src.api import payments as payments_api
from src.models.database import Base
from src.models.models import Bet, Bookmaker, PaymentAttempt, User, user_bets


class BetEligibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    @staticmethod
    def _bet(**overrides) -> Bet:
        values = {
            "id": uuid.uuid4(),
            "event_name": "Team A - Team B",
            "coefficient": Decimal("1.90"),
            "status": "pending",
            "delivery_mode": "feed",
            "publication_type": "forecast",
            "price_stars": 50,
        }
        values.update(overrides)
        return Bet(**values)

    async def test_unlock_free_rejects_ineligible_forecasts_without_spending_credit(self):
        cases = [
            ({"status": "win"}, 409),
            ({"live_ends_at": datetime.now(timezone.utc) - timedelta(minutes=1)}, 409),
            ({"delivery_mode": "sales_private"}, 404),
            ({"publication_type": "text"}, 400),
        ]

        for index, (overrides, expected_status) in enumerate(cases, start=1):
            with self.subTest(overrides=overrides):
                async with self.Session() as session:
                    user = User(telegram_id=1000 + index, free_bets_available=1)
                    bet = self._bet(**overrides)
                    session.add_all([user, bet])
                    await session.commit()

                    with self.assertRaises(HTTPException) as raised:
                        await bets_api.unlock_free_bet(bet.id, current_user=user, db=session)
                    await session.rollback()
                    await session.refresh(user)

                    access_rows = int(
                        (
                            await session.execute(
                                select(func.count()).select_from(user_bets).filter(
                                    user_bets.c.user_id == user.telegram_id
                                )
                            )
                        ).scalar()
                        or 0
                    )
                    self.assertEqual(raised.exception.status_code, expected_status)
                    self.assertEqual(user.free_bets_available, 1)
                    self.assertEqual(access_rows, 0)

    async def test_take_and_free_unlock_hide_forecast_for_other_bookmaker_audience(self):
        async with self.Session() as session:
            selected = Bookmaker(name="Selected", code="selected")
            other = Bookmaker(name="Other", code="other")
            user = User(
                telegram_id=2001,
                purchased_bets_balance=1,
                matches_remaining=1,
                free_bets_available=1,
                bookmakers=[selected],
            )
            paid_bet = self._bet(bookmakers=[other])
            free_bet = self._bet(bookmakers=[other])
            session.add_all([user, paid_bet, free_bet])
            await session.commit()

            with self.assertRaises(HTTPException) as take_error:
                await bets_api.take_bet(paid_bet.id, current_user=user, db=session)
            with self.assertRaises(HTTPException) as free_error:
                await bets_api.unlock_free_bet(free_bet.id, current_user=user, db=session)
            await session.rollback()

            self.assertEqual(take_error.exception.status_code, 404)
            self.assertEqual(free_error.exception.status_code, 404)

    async def test_valid_free_forecast_unlocks_once(self):
        async with self.Session() as session:
            user = User(telegram_id=3001, free_bets_available=1)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            first = await bets_api.unlock_free_bet(bet.id, current_user=user, db=session)
            second = await bets_api.unlock_free_bet(bet.id, current_user=user, db=session)
            await session.refresh(user)

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "already_unlocked")
            self.assertEqual(user.free_bets_available, 0)

    async def test_paid_purchase_uses_same_audience_guard_before_creating_attempt(self):
        async with self.Session() as session:
            selected = Bookmaker(name="Purchase selected", code="purchase-selected")
            other = Bookmaker(name="Purchase other", code="purchase-other")
            user = User(telegram_id=4001, bookmakers=[selected])
            bet = self._bet(bookmakers=[other])
            session.add_all([user, bet])
            await session.commit()

            with patch.object(
                payments_api,
                "create_telegram_stars_invoice_link",
                new=AsyncMock(side_effect=AssertionError("ineligible purchase must not create invoice")),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await payments_api.create_stars_invoice(
                        payments_api.InvoiceRequest(bet_id=bet.id),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )
            await session.rollback()

            attempt_count = int(
                (await session.execute(select(func.count(PaymentAttempt.id)))).scalar() or 0
            )
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(attempt_count, 0)

    async def test_paid_hint_purchase_uses_same_audience_guard_before_creating_attempt(self):
        async with self.Session() as session:
            selected = Bookmaker(name="Hint selected", code="hint-selected")
            other = Bookmaker(name="Hint other", code="hint-other")
            user = User(telegram_id=4002, bookmakers=[selected])
            bet = self._bet(bookmakers=[other])
            session.add_all([user, bet])
            await session.commit()

            with patch.object(
                bets_api,
                "create_telegram_stars_invoice_link",
                new=AsyncMock(side_effect=AssertionError("ineligible hint must not create invoice")),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        bets_api.BetHintRequest(amount_xtr=20),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )
            await session.rollback()

            attempt_count = int(
                (await session.execute(select(func.count(PaymentAttempt.id)))).scalar() or 0
            )
            self.assertEqual(raised.exception.status_code, 404)
            self.assertEqual(attempt_count, 0)

    async def test_single_bet_invoice_rejects_existing_access(self):
        async with self.Session() as session:
            user = User(telegram_id=4003)
            bet = self._bet()
            session.add_all([user, bet])
            await session.flush()
            await session.execute(
                user_bets.insert().values(
                    user_id=user.telegram_id,
                    bet_id=bet.id,
                    access_type="paid_match",
                    match_charged=True,
                )
            )
            await session.commit()

            with self.assertRaises(HTTPException) as raised:
                await payments_api.create_stars_invoice(
                    payments_api.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
            await session.rollback()

            attempt_count = int(
                (await session.execute(select(func.count(PaymentAttempt.id)))).scalar() or 0
            )
            self.assertEqual(raised.exception.status_code, 409)
            self.assertEqual(attempt_count, 0)

    async def test_single_bet_invoice_recovers_active_attempt_for_new_client_key(self):
        async with self.Session() as session:
            user = User(telegram_id=4004)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            with patch.object(
                payments_api,
                "create_telegram_stars_invoice_link",
                new=AsyncMock(return_value="https://invoice.invalid/first"),
            ):
                first = await payments_api.create_stars_invoice(
                    payments_api.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                recovered = await payments_api.create_stars_invoice(
                    payments_api.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
            await session.rollback()

            attempt_count = int(
                (await session.execute(select(func.count(PaymentAttempt.id)))).scalar() or 0
            )
            self.assertTrue(first["attempt_id"])
            self.assertEqual(recovered["attempt_id"], first["attempt_id"])
            self.assertEqual(recovered["invoice_url"], first["invoice_url"])
            self.assertTrue(recovered["idempotent_replay"])
            self.assertEqual(attempt_count, 1)

    async def test_single_bet_invoice_ambiguous_failure_is_quarantined_and_safely_replayed(self):
        async with self.Session() as session:
            user = User(telegram_id=4005)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            invoice_mock = AsyncMock(side_effect=RuntimeError("provider unavailable"))
            with patch.object(payments_api, "create_telegram_stars_invoice_link", new=invoice_mock):
                with self.assertRaises(HTTPException) as first_error:
                    await payments_api.create_stars_invoice(
                        payments_api.InvoiceRequest(bet_id=bet.id),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )
                retry = await payments_api.create_stars_invoice(
                    payments_api.InvoiceRequest(bet_id=bet.id),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = (
                await session.execute(
                    select(PaymentAttempt).order_by(PaymentAttempt.created_at, PaymentAttempt.id)
                )
            ).scalars().all()

            self.assertEqual(first_error.exception.status_code, 502)
            self.assertEqual(retry["attempt_id"], str(attempts[0].id))
            self.assertEqual(retry["checkout_state"], "requires_reconciliation")
            self.assertIsNone(retry["invoice_url"])
            self.assertEqual(invoice_mock.await_count, 1)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].status, "pending")
            self.assertEqual(attempts[0].checkout_state, "requires_reconciliation")

    async def test_paid_hint_ambiguous_provider_failure_is_quarantined_without_retry(self):
        async with self.Session() as session:
            user = User(telegram_id=4006)
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            invoice_mock = AsyncMock(side_effect=RuntimeError("provider unavailable"))
            with patch.object(bets_api, "create_telegram_stars_invoice_link", new=invoice_mock):
                with self.assertRaises(HTTPException) as first_error:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        bets_api.BetHintRequest(amount_xtr=20),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as retry_error:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        bets_api.BetHintRequest(amount_xtr=20),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )

            attempts = (
                await session.execute(
                    select(PaymentAttempt).order_by(PaymentAttempt.created_at, PaymentAttempt.id)
                )
            ).scalars().all()

            self.assertEqual(first_error.exception.status_code, 502)
            self.assertEqual(retry_error.exception.status_code, 409)
            self.assertEqual(invoice_mock.await_count, 1)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].status, "pending")
            self.assertEqual(attempts[0].checkout_state, "requires_reconciliation")

    async def test_paid_hint_entitlement_survives_resolution_after_payment(self):
        async with self.Session() as session:
            user = User(telegram_id=4007)
            bet = self._bet(description="Paid hint text")
            session.add_all([user, bet])
            await session.flush()
            attempt = await payments_api._create_payment_attempt(
                session,
                user=user,
                provider="telegram_stars",
                amount=Decimal("20"),
                currency="XTR",
                bet_id=bet.id,
                metadata={"purchase_type": payments_api.PAYMENT_PURCHASE_BET_HINT},
            )
            attempt.status = "succeeded"
            await session.commit()

            bet.status = "win"
            bet.resolved_at = datetime.now(timezone.utc)
            await session.commit()

            response = await bets_api.get_paid_bet_hint(
                bet.id,
                attempt_id=attempt.id,
                current_user=user,
                db=session,
            )

            self.assertEqual(response.bet_id, bet.id)
            self.assertEqual(response.hint, "Paid hint text")


if __name__ == "__main__":
    unittest.main()
