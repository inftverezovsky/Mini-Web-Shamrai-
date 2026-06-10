import unittest
import uuid
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import bets
from src.models.database import Base
from src.models.models import Bet, User, user_bets
from src.schemas.schemas import BetOddsDropUpdate


class OddsDropNotificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _user(self, telegram_id: int, role: str = "user") -> User:
        return User(
            telegram_id=telegram_id,
            username=f"user_{telegram_id}",
            role=role,
            purchased_bets_balance=3,
            matches_remaining=3,
        )

    def _bet(self) -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="France - Northern Ireland",
            coefficient=Decimal("5.00"),
            status="pending",
            sport_type="Football",
            outcome="Total over 3.5",
        )

    async def _add_access(self, session, *, user: User, bet: Bet) -> None:
        await session.execute(
            user_bets.insert().values(
                user_id=user.telegram_id,
                bet_id=bet.id,
                access_type="paid_match",
                match_charged=True,
                taken_at=func.now(),
            )
        )
        await session.flush()

    def test_build_odds_drop_message_mentions_original_and_dropped_odds(self):
        bet = self._bet()
        bet.odds_dropped_to = Decimal("1.50")

        message = bets.build_odds_drop_message(bet)

        self.assertIn("кф. <b>5.00</b>", message)
        self.assertIn("упала до <b>1.50</b>", message)
        self.assertIn("France - Northern Ireland", message)
        self.assertIn("Total over 3.5", message)

    async def test_update_odds_drop_saves_value(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            bet = self._bet()
            session.add_all([admin, bet])
            await session.commit()

            response = await bets.update_bet_odds_drop(
                bet.id,
                BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                admin=admin,
                db=session,
            )

            self.assertEqual(response.odds_dropped_to, Decimal("1.50"))

    async def test_notify_odds_drop_sends_only_to_users_with_access(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            taker = self._user(101)
            outsider = self._user(102)
            bet = self._bet()
            session.add_all([admin, taker, outsider, bet])
            await session.flush()
            await self._add_access(session, user=taker, bet=bet)
            await session.commit()

            sent_payloads = []

            def fake_call(method, payload, *args, **kwargs):
                sent_payloads.append((method, payload))
                return {"ok": True}

            with patch("src.api.bets.call_telegram_api", fake_call):
                response = await bets.notify_bet_odds_drop(
                    bet.id,
                    BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                    admin=admin,
                    db=session,
                )

            self.assertEqual(response.total, 1)
            self.assertEqual(response.sent, 1)
            self.assertEqual(response.failed, 0)
            self.assertEqual(sent_payloads[0][0], "sendMessage")
            self.assertEqual(sent_payloads[0][1]["chat_id"], taker.telegram_id)
            self.assertIn("1.50", sent_payloads[0][1]["text"])

            refreshed = (await session.execute(select(Bet).filter(Bet.id == bet.id))).scalars().first()
            self.assertEqual(refreshed.odds_dropped_to, Decimal("1.50"))
            self.assertIsNotNone(refreshed.odds_drop_notified_at)

    async def test_notify_odds_drop_requires_recipient_access(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            bet = self._bet()
            session.add_all([admin, bet])
            await session.commit()

            with self.assertRaises(HTTPException) as exc:
                await bets.notify_bet_odds_drop(
                    bet.id,
                    BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                    admin=admin,
                    db=session,
                )

            self.assertEqual(exc.exception.status_code, 400)
            self.assertIn("Нет клиентов", str(exc.exception.detail))
