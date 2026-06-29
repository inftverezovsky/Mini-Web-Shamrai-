import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.stats import get_global_stats
from src.models.database import Base
from src.models.models import Bet


class GlobalStatsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _bet(self, *, status: str, coefficient: str) -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name=f"{status} match",
            coefficient=Decimal(coefficient),
            status=status,
            created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
            resolved_at=datetime(2026, 6, 2, 12, tzinfo=timezone.utc),
        )

    async def test_global_stats_returns_average_coefficient_for_settled_bets(self):
        async with self.Session() as session:
            session.add_all([
                self._bet(status="win", coefficient="2.50"),
                self._bet(status="loss", coefficient="1.50"),
                self._bet(status="refund", coefficient="9.99"),
            ])
            await session.commit()

            response = await get_global_stats(db=session)

        self.assertEqual(response["total_bets"], 3)
        self.assertEqual(response["average_coefficient"], 2.0)


if __name__ == "__main__":
    unittest.main()
