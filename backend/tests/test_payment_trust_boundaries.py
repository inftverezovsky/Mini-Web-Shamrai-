import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import bets as bets_api
from src.api import crowd_bets as crowd_bets_api
from src.api.payments import _process_payment_attempt
from src.models.database import Base
from src.models.models import Bet, CrowdBet, CrowdBetParticipant, PaymentAttempt, User, user_bets
from src.schemas.schemas import BetHintRequest, CrowdBetFundRequest


class PaymentTrustBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _user(self) -> User:
        return User(telegram_id=101, username="client", role="user")

    def _bet(self) -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Team A - Team B",
            coefficient=Decimal("1.90"),
            status="pending",
            description="Paid analysis",
            price_stars=50,
        )

    async def test_crowd_fund_creates_pending_attempt_without_crediting_client_amount(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            crowd_bet = CrowdBet(bet=bet, target_amount=100, current_amount=0, status="funding")
            session.add_all([user, bet, crowd_bet])
            await session.commit()

            with patch.object(crowd_bets_api, "create_telegram_stars_invoice_link", return_value="mock-invoice"):
                response = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            await session.refresh(crowd_bet)
            participant_count = int((await session.execute(select(func.count(CrowdBetParticipant.id)))).scalar() or 0)
            attempt = (await session.execute(select(PaymentAttempt))).scalars().one()

            self.assertEqual(response.status, "invoice_created")
            self.assertEqual(str(response.attempt_id), str(attempt.id))
            self.assertEqual(crowd_bet.current_amount, 0)
            self.assertEqual(participant_count, 0)
            self.assertEqual(attempt.status, "pending")

    async def test_verified_crowd_payment_applies_contribution_and_unlocks_at_target(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            crowd_bet = CrowdBet(bet=bet, target_amount=50, current_amount=0, status="funding")
            decoy_bet = self._bet()
            decoy_crowd_bet = CrowdBet(
                bet=decoy_bet,
                target_amount=50,
                current_amount=0,
                status="funding",
            )
            session.add_all([user, bet, crowd_bet, decoy_bet, decoy_crowd_bet])
            await session.commit()

            with patch.object(crowd_bets_api, "create_telegram_stars_invoice_link", return_value="mock-invoice"):
                response = await crowd_bets_api.fund_crowd_bet(
                    crowd_bet.id,
                    CrowdBetFundRequest(amount_xtr=50),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempt = await session.get(PaymentAttempt, response.attempt_id)
            attempt.metadata_json = {
                **(attempt.metadata_json or {}),
                "crowd_bet_id": decoy_crowd_bet.id,
            }
            await session.commit()
            result = await _process_payment_attempt(
                session,
                attempt_id=response.attempt_id,
                provider="telegram_stars",
                provider_payment_id="telegram-charge-1",
                amount=Decimal("50"),
                currency="XTR",
                raw_payload={"ok": True},
                payer_user_id=user.telegram_id,
            )
            await session.commit()
            await session.refresh(crowd_bet)
            await session.refresh(decoy_crowd_bet)
            access_count = int((await session.execute(select(func.count()).select_from(user_bets))).scalar() or 0)

            self.assertEqual(result["status"], "success")
            self.assertEqual(crowd_bet.current_amount, 50)
            self.assertEqual(decoy_crowd_bet.current_amount, 0)
            self.assertEqual(crowd_bet.status, "opened")
            self.assertEqual(access_count, 1)

    async def test_buy_hint_creates_invoice_without_returning_hint_before_payment(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            session.add_all([user, bet])
            await session.commit()

            with patch.object(bets_api, "create_telegram_stars_invoice_link", return_value="mock-hint-invoice"):
                response = await bets_api.buy_bet_hint(
                    bet.id,
                    BetHintRequest(amount_xtr=1),
                    idempotency_key=uuid.uuid4(),
                    current_user=user,
                    db=session,
                )

            attempt = (await session.execute(select(PaymentAttempt))).scalars().one()
            self.assertEqual(response.status, "invoice_created")
            self.assertEqual(response.price_xtr, bets_api.BET_HINT_PRICE_XTR)
            self.assertEqual(attempt.status, "pending")
            self.assertEqual((attempt.metadata_json or {}).get("purchase_type"), "bet_hint")


if __name__ == "__main__":
    unittest.main()
