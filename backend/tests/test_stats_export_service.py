import unittest
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from openpyxl import load_workbook
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.database import Base
from src.models.models import Bet, User, user_bets
from src.services.stats_export import (
    build_stats_export_workbook,
    export_item_from_bet,
    load_client_export_groups,
    summarize_export_items,
)


def _bet(*, status, coefficient="2.00", odds_dropped_to=None):
    bookmaker = SimpleNamespace(id=1, name="Фонбет", code="fonbet")
    return SimpleNamespace(
        id=uuid4(),
        event_name="Team A - Team B",
        status=status,
        coefficient=Decimal(coefficient),
        created_at=datetime(2026, 6, 1, 12, tzinfo=timezone.utc),
        resolved_at=datetime(2026, 6, 10, 12, tzinfo=timezone.utc),
        delivery_mode="feed",
        sport_type="Футбол",
        outcome="П1",
        odds_dropped_to=Decimal(str(odds_dropped_to)) if odds_dropped_to is not None else None,
        bookmakers=[bookmaker],
        bookmaker=bookmaker,
    )


class StatsExportServiceTests(unittest.TestCase):
    def test_export_summary_uses_rub_unit_stake_and_refunds(self):
        items = [
            export_item_from_bet(_bet(status="win", coefficient="2.50")),
            export_item_from_bet(_bet(status="loss", coefficient="1.80")),
            export_item_from_bet(_bet(status="refund", coefficient="2.00")),
        ]
        summary = summarize_export_items([item for item in items if item])

        self.assertEqual(summary["bets"], 3)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["refunds"], 1)
        self.assertEqual(summary["turnover"], Decimal("20000"))
        self.assertEqual(summary["profit"], Decimal("5000.00"))
        self.assertEqual(summary["winrate"], 0.5)
        self.assertEqual(summary["roi"], Decimal("0.25"))

    def test_workbook_contains_expected_sheets_and_odds_drop_column(self):
        items = [export_item_from_bet(_bet(status="win", coefficient="1.90", odds_dropped_to="1.55"))]
        content = build_stats_export_workbook(
            [item for item in items if item],
            title="СТАТИСТИКА SHAMRAI",
            period_label="Весь период",
            include_client=False,
        )

        wb = load_workbook(BytesIO(content))
        self.assertEqual(wb.sheetnames, ["Статистика", "Свод по БК и спорту", "Детально"])
        detail = wb["Детально"]
        headers = [cell.value for cell in detail[1]]
        self.assertIn("Упал до", headers)
        odds_col = headers.index("Упал до") + 1
        self.assertEqual(detail.cell(row=2, column=odds_col).value, 1.55)


class StatsExportClientAccessTests(unittest.IsolatedAsyncioTestCase):
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
            first_name=f"User {telegram_id}",
            role=role,
        )

    def _bet(self, index: int) -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name=f"Team {index} A - Team {index} B",
            coefficient=Decimal("1.90"),
            status="win",
            created_at=datetime(2026, 6, index, 12, tzinfo=timezone.utc),
            resolved_at=datetime(2026, 6, index, 15, tzinfo=timezone.utc),
            sport_type="Футбол",
            outcome="П1",
        )

    async def _add_access(self, session, *, user: User, bet: Bet, access_type: str, match_charged: bool) -> None:
        await session.execute(
            user_bets.insert().values(
                user_id=user.telegram_id,
                bet_id=bet.id,
                access_type=access_type,
                match_charged=match_charged,
                taken_at=func.now(),
            )
        )

    async def test_client_export_groups_include_only_paid_non_staff_access(self):
        async with self.Session() as session:
            paid_user = self._user(101)
            staff_user = self._user(900, role="admin")
            users = [paid_user, staff_user]
            bets = [self._bet(index) for index in range(1, 8)]
            session.add_all(users + bets)
            await session.flush()

            await self._add_access(session, user=paid_user, bet=bets[0], access_type="paid_match", match_charged=True)
            await self._add_access(session, user=paid_user, bet=bets[1], access_type="telegram_stars_single_bet", match_charged=False)
            await self._add_access(session, user=paid_user, bet=bets[2], access_type="free_bet", match_charged=False)
            await self._add_access(session, user=paid_user, bet=bets[3], access_type="guarantee_replacement", match_charged=False)
            await self._add_access(session, user=paid_user, bet=bets[4], access_type="crowd_pool", match_charged=False)
            await self._add_access(session, user=paid_user, bet=bets[5], access_type="admin", match_charged=True)
            await self._add_access(session, user=staff_user, bet=bets[6], access_type="paid_match", match_charged=True)
            await session.commit()

            groups = await load_client_export_groups(session, "all")

        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].user_id, paid_user.telegram_id)
        self.assertEqual([item.access_type for item in groups[0].items], ["paid_match", "telegram_stars_single_bet"])


if __name__ == "__main__":
    unittest.main()
