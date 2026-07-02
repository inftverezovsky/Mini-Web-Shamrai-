import unittest
import uuid
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import bets as bets_api
from src.models.database import Base
from src.models.models import Bet, User


class BetTakeGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_take_rejects_non_pending_feed_bet_before_recording_access(self):
        async with self.Session() as session:
            user = User(
                telegram_id=12345,
                username="client",
                purchased_bets_balance=3,
                matches_remaining=3,
            )
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Team A - Team B",
                coefficient=Decimal("1.90"),
                status="win",
                delivery_mode="feed",
                outcome="Team A win",
            )
            session.add_all([user, bet])
            await session.flush()

            with patch.object(
                bets_api,
                "record_user_bet_access",
                new=AsyncMock(side_effect=AssertionError("inactive bet must not record access")),
            ):
                with self.assertRaises(HTTPException) as raised:
                    await bets_api.take_bet(bet.id, current_user=user, db=session)

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "Прогноз уже не активен. Реагировать не нужно.")
