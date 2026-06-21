import unittest
import uuid
from datetime import datetime, timedelta, timezone
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
    CLIENT_EXPORT_STAKE,
    FLAT_FORMAT,
    build_client_info_export_workbook,
    build_stats_export_workbook,
    export_item_from_bet,
    load_client_recent_bet_export_rows,
    load_client_info_export_rows,
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

    def test_shamrai_workbook_uses_rub_stake_and_contains_odds_drop_column(self):
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
        self.assertIn("Оборот, ₽", headers)
        self.assertIn("Прибыль, ₽", headers)
        odds_col = headers.index("Упал до") + 1
        profit_col = headers.index("Прибыль, ₽") + 1
        self.assertEqual(detail.cell(row=2, column=odds_col).value, 1.55)
        self.assertEqual(detail.cell(row=2, column=profit_col).value, 9000)

    def test_client_workbook_uses_flat_labels_and_values(self):
        item = export_item_from_bet(
            _bet(status="win", coefficient="1.90"),
            unit_stake=CLIENT_EXPORT_STAKE,
            client_name="Client",
        )
        content = build_stats_export_workbook(
            [item],
            title="СТАТИСТИКА КЛИЕНТОВ SHAMRAI",
            period_label="Весь период",
            include_client=True,
            value_format=FLAT_FORMAT,
            value_label="флеты",
        )

        wb = load_workbook(BytesIO(content))
        detail = wb["Детально"]
        headers = [cell.value for cell in detail[1]]
        self.assertIn("Оборот, флеты", headers)
        self.assertIn("Прибыль, флеты", headers)
        profit_col = headers.index("Прибыль, флеты") + 1
        self.assertEqual(detail.cell(row=2, column=profit_col).value, 0.9)


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

    def _bet(self, index: int, *, status: str = "win", coefficient: str = "1.90") -> Bet:
        created_at = datetime(2026, 6, 1, 12, tzinfo=timezone.utc) + timedelta(days=index - 1)
        return Bet(
            id=uuid.uuid4(),
            event_name=f"Team {index} A - Team {index} B",
            coefficient=Decimal(coefficient),
            status=status,
            created_at=created_at,
            resolved_at=created_at + timedelta(hours=3),
            sport_type="Футбол",
            outcome="П1",
        )

    async def _add_access(
        self,
        session,
        *,
        user: User,
        bet: Bet,
        access_type: str,
        match_charged: bool,
        taken_at: datetime | None = None,
    ) -> None:
        await session.execute(
            user_bets.insert().values(
                user_id=user.telegram_id,
                bet_id=bet.id,
                access_type=access_type,
                match_charged=match_charged,
                taken_at=taken_at or func.now(),
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

    async def test_client_info_export_rows_include_all_non_staff_clients(self):
        async with self.Session() as session:
            paid_user = self._user(101)
            paid_user.matches_remaining = 4
            paid_user.phone = "+79990000001"
            paid_user.vk_user_id = "vk-101"
            paid_user.other_bookmaker_name = "Custom BK"
            paid_user.client_group = "VIP"
            paid_user.client_tag = "контроль"
            no_stats_user = self._user(202)
            no_stats_user.purchased_bets_balance = 2
            staff_user = self._user(900, role="admin")
            bets = [
                self._bet(1, status="win", coefficient="1.90"),
                self._bet(2, status="loss", coefficient="1.90"),
                self._bet(3, status="pending", coefficient="1.90"),
            ]
            bets[2].resolved_at = None
            session.add_all([paid_user, no_stats_user, staff_user, *bets])
            await session.flush()

            await self._add_access(session, user=paid_user, bet=bets[0], access_type="paid_match", match_charged=True)
            await self._add_access(session, user=paid_user, bet=bets[1], access_type="paid_match", match_charged=True)
            await self._add_access(session, user=paid_user, bet=bets[2], access_type="free_bet", match_charged=False)
            await session.commit()

            rows = await load_client_info_export_rows(session, "all")

        rows_by_id = {row.user_id: row for row in rows}
        self.assertEqual(set(rows_by_id), {101, 202})
        self.assertEqual(rows_by_id[101].matches_remaining, 4)
        self.assertEqual(rows_by_id[101].phone, "+79990000001")
        self.assertEqual(rows_by_id[101].vk_user_id, "vk-101")
        self.assertEqual(rows_by_id[101].bookmaker_names, "Custom BK")
        self.assertEqual(rows_by_id[101].client_group, "VIP")
        self.assertEqual(rows_by_id[101].wins, 1)
        self.assertEqual(rows_by_id[101].losses, 1)
        self.assertEqual(rows_by_id[101].settled_bets, 2)
        self.assertEqual(rows_by_id[101].pending_bets, 1)
        self.assertEqual(rows_by_id[101].total_taken_bets, 3)
        self.assertEqual(rows_by_id[101].recent_results, ["loss", "win"])
        self.assertEqual(rows_by_id[101].situation_label, "Рабочая просадка")
        self.assertEqual(rows_by_id[202].matches_remaining, 2)
        self.assertEqual(rows_by_id[202].bets, 0)
        self.assertEqual(rows_by_id[202].situation_label, "Нет расчетов")

        content = build_client_info_export_workbook(rows, period_label="Весь период")
        wb = load_workbook(BytesIO(content))
        self.assertEqual(wb.sheetnames, ["Инфа", "Последние 50"])
        ws = wb["Инфа"]
        headers = [cell.value for cell in ws[1]]
        self.assertIn("Матчей осталось", headers)
        self.assertIn("Телефон", headers)
        self.assertIn("VK ID", headers)
        self.assertIn("БК клиента", headers)
        self.assertIn("Взял матчей всего", headers)
        self.assertIn("Ожидают расчета", headers)
        self.assertIn("Победы", headers)
        self.assertIn("Поражения", headers)
        self.assertIn("Ситуация", headers)
        self.assertEqual(ws.max_row, 3)

    async def test_client_recent_bet_export_rows_limit_each_client_to_latest_50_taken_bets(self):
        async with self.Session() as session:
            user = self._user(101)
            user.phone = "+79990000101"
            user.vk_user_id = "vk-101"
            user.matches_remaining = 7
            staff_user = self._user(900, role="moderator")
            base_taken_at = datetime(2026, 6, 1, 12, tzinfo=timezone.utc)
            bets = []
            for index in range(60):
                if index == 57:
                    status = "pending"
                    resolved_at = None
                elif index == 58:
                    status = "refund"
                    resolved_at = base_taken_at + timedelta(minutes=index, hours=2)
                elif index % 2 == 0:
                    status = "win"
                    resolved_at = base_taken_at + timedelta(minutes=index, hours=2)
                else:
                    status = "loss"
                    resolved_at = base_taken_at + timedelta(minutes=index, hours=2)
                bet = self._bet(index + 1, status=status)
                bet.resolved_at = resolved_at
                bets.append(bet)
            staff_bet = self._bet(99, status="win")
            session.add_all([user, staff_user, *bets, staff_bet])
            await session.flush()

            for index, bet in enumerate(bets):
                await self._add_access(
                    session,
                    user=user,
                    bet=bet,
                    access_type="free_bet" if index == 59 else "paid_match",
                    match_charged=index % 3 != 0,
                    taken_at=base_taken_at + timedelta(minutes=index),
                )
            await self._add_access(
                session,
                user=staff_user,
                bet=staff_bet,
                access_type="paid_match",
                match_charged=True,
                taken_at=base_taken_at + timedelta(minutes=61),
            )
            await session.commit()

            info_rows = await load_client_info_export_rows(session, "all")
            recent_rows = await load_client_recent_bet_export_rows(session, "all", limit_per_client=50)

        self.assertEqual(len(recent_rows), 50)
        self.assertTrue(all(row.user_id == 101 for row in recent_rows))
        self.assertEqual(recent_rows[0].event_name, "Team 60 A - Team 60 B")
        self.assertEqual(recent_rows[-1].event_name, "Team 11 A - Team 11 B")
        self.assertNotIn("Team 10 A - Team 10 B", [row.event_name for row in recent_rows])
        self.assertIn("pending", {row.status for row in recent_rows})
        self.assertIn("refund", {row.status for row in recent_rows})
        self.assertIn("win", {row.status for row in recent_rows})
        self.assertIn("loss", {row.status for row in recent_rows})
        self.assertEqual(recent_rows[0].phone, "+79990000101")
        self.assertEqual(recent_rows[0].vk_user_id, "vk-101")
        self.assertEqual(recent_rows[0].matches_remaining, 7)
        self.assertEqual(recent_rows[0].access_type, "free_bet")

        content = build_client_info_export_workbook(info_rows, recent_rows=recent_rows, period_label="Весь период")
        wb = load_workbook(BytesIO(content))
        self.assertEqual(wb.sheetnames, ["Инфа", "Последние 50"])
        history = wb["Последние 50"]
        headers = [cell.value for cell in history[1]]
        self.assertIn("Дата взятия", headers)
        self.assertIn("Тип доступа", headers)
        self.assertIn("Матч списан", headers)
        self.assertEqual(history.max_row, 51)


if __name__ == "__main__":
    unittest.main()
