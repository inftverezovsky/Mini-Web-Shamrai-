import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api.stats import get_global_stats
from src.models.database import Base
from src.models.models import Bet, HistoricalStatsImportBatch, HistoricalStatsMonthly
from src.services.statistics import period_start


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

    def _bet(
        self,
        *,
        status: str,
        coefficient: str,
        resolved_at: datetime | None = None,
        publication_type: str = "forecast",
    ) -> Bet:
        resolved = resolved_at or datetime(2026, 6, 2, 12, tzinfo=timezone.utc)
        return Bet(
            id=uuid.uuid4(),
            event_name=f"{status} match",
            coefficient=Decimal(coefficient),
            status=status,
            publication_type=publication_type,
            created_at=resolved - timedelta(hours=1),
            resolved_at=resolved,
        )

    async def test_global_stats_returns_average_coefficient_for_settled_bets(self):
        async with self.Session() as session:
            session.add_all([
                self._bet(status="win", coefficient="2.50"),
                self._bet(status="loss", coefficient="1.50"),
                self._bet(status="refund", coefficient="9.99"),
                self._bet(status="win", coefficient="99.00", publication_type="text"),
            ])
            await session.commit()

            response = await get_global_stats(db=session)

        self.assertEqual(response["total_bets"], 3)
        self.assertEqual(response["average_coefficient"], 2.0)
        self.assertEqual(response["period"], "all")
        self.assertEqual(response["period_label"], "Весь период")

    async def test_month_period_filters_global_stats_to_current_month(self):
        current_month_start = period_start("month")
        assert current_month_start is not None
        current_month_win = (current_month_start + timedelta(days=1)).astimezone(timezone.utc)
        current_month_loss = (current_month_start + timedelta(days=2)).astimezone(timezone.utc)
        previous_month_win = (current_month_start - timedelta(days=1)).astimezone(timezone.utc)

        async with self.Session() as session:
            session.add_all([
                self._bet(status="win", coefficient="3.00", resolved_at=previous_month_win),
                self._bet(status="win", coefficient="2.20", resolved_at=current_month_win),
                self._bet(status="loss", coefficient="1.80", resolved_at=current_month_loss),
            ])
            await session.commit()

            response = await get_global_stats(period="month", db=session)

        self.assertEqual(response["period"], "month")
        self.assertEqual(response["period_label"], "Текущий месяц")
        self.assertEqual(response["total_bets"], 2)
        self.assertEqual(response["won_bets"], 1)
        self.assertEqual(response["lost_bets"], 1)
        self.assertEqual(response["net_profit"], 0.2)
        self.assertEqual(response["roi"], 10.0)

    async def test_all_period_merges_historical_baseline_and_filters_live_duplicates(self):
        async with self.Session() as session:
            batch = HistoricalStatsImportBatch(
                source_filename="stats.xlsx",
                source_sha256="sha-test",
                cutoff_at=datetime(2026, 7, 1, tzinfo=timezone.utc),
                unit_stake_rub=Decimal("10000"),
                is_active=True,
                total_bets=4,
                total_wins=2,
                total_losses=2,
                total_refunds=0,
                total_turnover_rub=Decimal("40000"),
                total_profit_rub=Decimal("4000"),
            )
            session.add(batch)
            await session.flush()
            session.add(HistoricalStatsMonthly(
                batch_id=batch.id,
                period_key="2026-06",
                period_label="Июнь 2026",
                period_start=datetime(2026, 6, 1, tzinfo=timezone.utc),
                bets=4,
                wins=2,
                losses=2,
                refunds=0,
                turnover_rub=Decimal("40000"),
                profit_rub=Decimal("4000"),
                average_coefficient=Decimal("2.00"),
                top_sport="Футбол",
                top_bookmaker="Фонбет",
            ))
            session.add_all([
                self._bet(
                    status="win",
                    coefficient="9.00",
                    resolved_at=datetime(2026, 6, 10, tzinfo=timezone.utc),
                ),
                self._bet(
                    status="win",
                    coefficient="2.50",
                    resolved_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                ),
            ])
            await session.commit()

            response = await get_global_stats(db=session)

        self.assertEqual(response["total_bets"], 5)
        self.assertEqual(response["won_bets"], 3)
        self.assertEqual(response["lost_bets"], 2)
        self.assertEqual(response["net_profit"], 1.9)
        self.assertEqual(response["roi"], 38.0)
        self.assertEqual(
            response["chart_points"],
            [
                {"month": "2026-06", "profit": 0.4},
                {"month": "2026-07", "profit": 1.9},
            ],
        )


if __name__ == "__main__":
    unittest.main()
