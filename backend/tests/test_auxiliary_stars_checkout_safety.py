import inspect
import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import bets as bets_api
from src.api import crowd_bets as crowd_bets_api
from src.api import payments
from src.models.database import Base
from src.models.models import Bet, CrowdBet, PaymentAttempt, User
from src.schemas.schemas import BetHintRequest, CrowdBetFundRequest


class AuxiliaryStarsCheckoutSafetyTests(unittest.IsolatedAsyncioTestCase):
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
    def _user() -> User:
        return User(telegram_id=88001, username="checkout-owner", role="user")

    @staticmethod
    def _bet() -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Team A - Team B",
            coefficient=Decimal("1.90"),
            status="pending",
            description="Paid analysis",
            price_stars=50,
        )

    async def test_hint_lost_response_is_quarantined_and_never_retried(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()
            intent_id = uuid.uuid4()

            create_link = AsyncMock(side_effect=TimeoutError("response lost"))
            with patch.object(bets_api, "create_telegram_stars_invoice_link", new=create_link):
                with self.assertRaises(HTTPException) as first_error:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        BetHintRequest(amount_xtr=20),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as replay_error:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        BetHintRequest(amount_xtr=20),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as new_key_error:
                    await bets_api.buy_bet_hint(
                        bet.id,
                        BetHintRequest(amount_xtr=20),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(first_error.exception.status_code, 502)
            self.assertEqual(replay_error.exception.status_code, 409)
            self.assertEqual(new_key_error.exception.status_code, 409)
            self.assertEqual(len(attempts), 1)
            self.assertEqual(attempts[0].status, "pending")
            self.assertEqual(attempts[0].checkout_state, "requires_reconciliation")
            self.assertEqual(attempts[0].purchase_type_snapshot, payments.PAYMENT_PURCHASE_BET_HINT)
            create_link.assert_awaited_once()

    async def test_hint_replay_with_same_or_new_key_reuses_one_invoice(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            create_link = AsyncMock(return_value="https://invoice.invalid/hint")
            with patch.object(bets_api, "create_telegram_stars_invoice_link", new=create_link):
                first = await bets_api.buy_bet_hint(
                    bet.id,
                    BetHintRequest(amount_xtr=20),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                same_purchase_new_key = await bets_api.buy_bet_hint(
                    bet.id,
                    BetHintRequest(amount_xtr=20),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = list((await session.execute(select(PaymentAttempt))).scalars().all())
            self.assertEqual(len(attempts), 1)
            self.assertEqual(first.attempt_id, same_purchase_new_key.attempt_id)
            self.assertEqual(first.invoice_url, same_purchase_new_key.invoice_url)
            self.assertEqual(attempts[0].checkout_state, "ready")
            create_link.assert_awaited_once()

    async def test_crowd_active_invoice_replays_and_completed_invoice_allows_next_contribution(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            crowd_bet = CrowdBet(bet=bet, target_amount=500, current_amount=0, status="funding")
            session.add_all([user, bet, crowd_bet])
            await session.commit()
            first_intent = uuid.uuid4()

            create_link = AsyncMock(
                side_effect=["https://invoice.invalid/crowd-one", "https://invoice.invalid/crowd-two"]
            )
            with patch.object(crowd_bets_api, "create_telegram_stars_invoice_link", new=create_link):
                first = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=first_intent,
                    current_user=user,
                    db=session,
                )
                replay = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=first_intent,
                    current_user=user,
                    db=session,
                )
                same_purchase_new_key = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )
                with self.assertRaises(HTTPException) as changed_same_key:
                    await crowd_bets_api.fund_crowd_bet(
                        crowd_bet.id,
                        CrowdBetFundRequest(amount_xtr=60),
                        idempotency_key=first_intent,
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as changed_new_key:
                    await crowd_bets_api.fund_crowd_bet(
                        crowd_bet.id,
                        CrowdBetFundRequest(amount_xtr=60),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )

                first_attempt = await session.get(PaymentAttempt, first.attempt_id)
                first_attempt.status = "succeeded"
                first_attempt.checkout_state = "completed"
                await session.commit()
                second = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempts = list(
                (await session.execute(select(PaymentAttempt).order_by(PaymentAttempt.created_at, PaymentAttempt.id)))
                .scalars()
                .all()
            )
            self.assertEqual(changed_same_key.exception.status_code, 409)
            self.assertEqual(changed_new_key.exception.status_code, 409)
            self.assertEqual(first.attempt_id, replay.attempt_id)
            self.assertEqual(first.attempt_id, same_purchase_new_key.attempt_id)
            self.assertEqual(first.invoice_url, replay.invoice_url)
            self.assertEqual(first.invoice_url, same_purchase_new_key.invoice_url)
            self.assertNotEqual(first.attempt_id, second.attempt_id)
            self.assertEqual(len(attempts), 2)
            self.assertTrue(all(item.crowd_bet_id_snapshot == crowd_bet.id for item in attempts))
            self.assertTrue(all(item.purchase_type_snapshot == payments.PAYMENT_PURCHASE_CROWD_BET for item in attempts))
            self.assertNotEqual(attempts[0].checkout_intent_id, attempts[1].checkout_intent_id)
            self.assertEqual(create_link.await_count, 2)

    async def test_crowd_lost_response_replay_does_not_call_provider_twice(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            crowd_bet = CrowdBet(bet=bet, target_amount=500, current_amount=0, status="funding")
            session.add_all([user, bet, crowd_bet])
            await session.commit()
            intent_id = uuid.uuid4()

            create_link = AsyncMock(side_effect=TimeoutError("response lost"))
            with patch.object(crowd_bets_api, "create_telegram_stars_invoice_link", new=create_link):
                with self.assertRaises(HTTPException) as first_error:
                    await crowd_bets_api.fund_crowd_bet(
                        crowd_bet.id,
                        CrowdBetFundRequest(amount_xtr=50),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as replay_error:
                    await crowd_bets_api.fund_crowd_bet(
                        crowd_bet.id,
                        CrowdBetFundRequest(amount_xtr=50),
                        idempotency_key=intent_id,
                        current_user=user,
                        db=session,
                    )
                with self.assertRaises(HTTPException) as new_key_error:
                    await crowd_bets_api.fund_crowd_bet(
                        crowd_bet.id,
                        CrowdBetFundRequest(amount_xtr=50),
                        idempotency_key=uuid.uuid4(),
                        current_user=user,
                        db=session,
                    )

            attempt = (await session.execute(select(PaymentAttempt))).scalars().one()
            self.assertEqual(first_error.exception.status_code, 502)
            self.assertEqual(replay_error.exception.status_code, 409)
            self.assertEqual(new_key_error.exception.status_code, 409)
            self.assertEqual(attempt.checkout_state, "requires_reconciliation")
            create_link.assert_awaited_once()

    def test_auxiliary_checkout_lock_order_and_crowd_hash_scope(self):
        hint_source = inspect.getsource(bets_api.buy_bet_hint)
        self.assertLess(
            hint_source.index("await _lock_telegram_purchase_scope"),
            hint_source.index("await _load_checkout_attempt"),
        )
        self.assertLess(
            hint_source.index("await _load_checkout_attempt"),
            hint_source.index("await load_locked_bet_for_user_access"),
        )

        crowd_source = inspect.getsource(crowd_bets_api.fund_crowd_bet)
        self.assertLess(
            crowd_source.index("await _load_checkout_attempt"),
            crowd_source.index("select(CrowdBet)"),
        )
        self.assertIn("crowd_bet_id=crowd_bet_id", crowd_source)
        self.assertIn("amount_xtr=int(payload.amount_xtr)", crowd_source)

        common = {
            "provider": "telegram_stars",
            "user_id": 1,
            "purchase_type": payments.PAYMENT_PURCHASE_CROWD_BET,
            "crowd_bet_id": 9,
            "amount_xtr": 50,
        }
        original = payments._checkout_request_hash(**common)
        self.assertNotEqual(original, payments._checkout_request_hash(**{**common, "crowd_bet_id": 10}))
        self.assertNotEqual(original, payments._checkout_request_hash(**{**common, "amount_xtr": 51}))


if __name__ == "__main__":
    unittest.main()
