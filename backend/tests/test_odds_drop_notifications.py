import unittest
import uuid
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import bets
from src.models.database import Base
from src.models.models import Bet, Bookmaker, DeliveryOutbox, ForecastRequest, User, user_bets
from src.schemas.schemas import BetOddsDropUpdate, BetResolve, BetUpdate
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, CHANNEL_VK_MESSAGE


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

    async def test_update_bet_edits_main_fields_bookmakers_and_links(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            bet = self._bet()
            fonbet = Bookmaker(id=1, name="Фонбет (Fonbet)", code="fonbet", is_active=True)
            pari = Bookmaker(id=2, name="Пари (Pari)", code="pari", is_active=True)
            session.add_all([admin, bet, fonbet, pari])
            await session.commit()

            response = await bets.update_bet(
                bet.id,
                BetUpdate(
                    event_name="Team A - Team B",
                    coefficient=Decimal("2.75"),
                    outcome="П1",
                    sport_type="Футбол",
                    bookmaker_id=fonbet.id,
                    bookmaker_ids=[fonbet.id, pari.id],
                    bookmaker_links=[
                        {"bookmaker_id": fonbet.id, "url": "fonbet.ru/match/123"},
                        {"bookmaker_id": pari.id, "url": "https://pari.example/match"},
                    ],
                ),
                admin=admin,
                db=session,
            )

            self.assertEqual(response.event_name, "Team A - Team B")
            self.assertEqual(response.coefficient, Decimal("2.75"))
            self.assertEqual(response.outcome, "П1")
            self.assertEqual(response.sport_type, "Футбол")
            self.assertEqual(response.bookmaker_id, fonbet.id)
            self.assertEqual([bookmaker.id for bookmaker in response.bookmakers], [fonbet.id, pari.id])
            self.assertEqual(
                response.bookmaker_links,
                [
                    {"bookmaker_id": fonbet.id, "url": "https://fonbet.ru/match/123"},
                    {"bookmaker_id": pari.id, "url": "https://pari.example/match"},
                ],
            )

    async def test_notify_odds_drop_queues_only_users_with_access(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            taker = self._user(101)
            outsider = self._user(102)
            bet = self._bet()
            session.add_all([admin, taker, outsider, bet])
            await session.flush()
            await self._add_access(session, user=taker, bet=bet)
            await session.commit()

            response = await bets.notify_bet_odds_drop(
                bet.id,
                BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                admin=admin,
                db=session,
            )

            self.assertEqual(response.total, 1)
            self.assertEqual(response.sent, 0)
            self.assertEqual(response.queued, 1)
            self.assertEqual(response.failed, 0)
            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            self.assertEqual(len(outbox_items), 1)
            self.assertEqual(outbox_items[0].channel, CHANNEL_TELEGRAM_MESSAGE)
            self.assertEqual(outbox_items[0].payload["method"], "sendMessage")
            self.assertEqual(outbox_items[0].payload["payload"]["chat_id"], taker.telegram_id)
            self.assertIn("1.50", outbox_items[0].payload["payload"]["text"])

            refreshed = (await session.execute(select(Bet).filter(Bet.id == bet.id))).scalars().first()
            self.assertEqual(refreshed.odds_dropped_to, Decimal("1.50"))
            self.assertIsNotNone(refreshed.odds_drop_notified_at)

    async def test_notify_odds_drop_queues_vk_for_vk_only_recipient(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            vk_taker = self._user(-101)
            vk_taker.vk_user_id = "123456"
            vk_taker.vk_messages_allowed = True
            bet = self._bet()
            session.add_all([admin, vk_taker, bet])
            await session.flush()
            await self._add_access(session, user=vk_taker, bet=bet)
            await session.commit()

            response = await bets.notify_bet_odds_drop(
                bet.id,
                BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                admin=admin,
                db=session,
            )

            self.assertEqual(response.queued, 1)
            self.assertEqual(response.failed, 0)
            outbox_item = (await session.execute(select(DeliveryOutbox))).scalars().one()
            self.assertEqual(outbox_item.channel, CHANNEL_VK_MESSAGE)
            self.assertEqual(outbox_item.user_id, vk_taker.telegram_id)
            self.assertIn("1.50", outbox_item.payload["message"])

    async def test_notify_odds_drop_skips_users_who_disabled_odds_drop_alerts(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            enabled_taker = self._user(101)
            disabled_taker = self._user(102)
            disabled_taker.odds_drop_notifications_enabled = False
            bet = self._bet()
            session.add_all([admin, enabled_taker, disabled_taker, bet])
            await session.flush()
            await self._add_access(session, user=enabled_taker, bet=bet)
            await self._add_access(session, user=disabled_taker, bet=bet)
            await session.commit()

            response = await bets.notify_bet_odds_drop(
                bet.id,
                BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                admin=admin,
                db=session,
            )

            self.assertEqual(response.total, 1)
            self.assertEqual(response.sent, 0)
            self.assertEqual(response.queued, 1)
            self.assertEqual(response.failed, 0)
            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            self.assertEqual([item.user_id for item in outbox_items], [enabled_taker.telegram_id])

    async def test_notify_odds_drop_requires_enabled_recipients(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            disabled_taker = self._user(101)
            disabled_taker.odds_drop_notifications_enabled = False
            bet = self._bet()
            session.add_all([admin, disabled_taker, bet])
            await session.flush()
            await self._add_access(session, user=disabled_taker, bet=bet)
            await session.commit()

            with self.assertRaises(HTTPException) as exc:
                await bets.notify_bet_odds_drop(
                    bet.id,
                    BetOddsDropUpdate(odds_dropped_to=Decimal("1.50")),
                    admin=admin,
                    db=session,
                )

            self.assertEqual(exc.exception.status_code, 400)
            self.assertIn("разрешены уведомления", str(exc.exception.detail))
            outbox_count = (await session.execute(select(func.count(DeliveryOutbox.id)))).scalar_one()
            self.assertEqual(outbox_count, 0)

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

    async def test_resolve_bet_queues_admin_group_result_summary(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            taker_one = self._user(101)
            taker_two = self._user(102)
            bet = self._bet()
            request_one = ForecastRequest(
                bet_id=bet.id,
                user_id=taker_one.telegram_id,
                status="sent",
            )
            request_two = ForecastRequest(
                bet_id=bet.id,
                user_id=taker_two.telegram_id,
                status="manual_sent",
            )
            session.add_all([admin, taker_one, taker_two, bet, request_one, request_two])
            await session.flush()
            await self._add_access(session, user=taker_one, bet=bet)
            await self._add_access(session, user=taker_two, bet=bet)
            await session.commit()

            previous_chat_id = bets.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
            try:
                bets.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
                await bets.resolve_bet(
                    bet.id,
                    BetResolve(status="win"),
                    admin=admin,
                    db=session,
                )
            finally:
                bets.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            group_items = [
                item
                for item in outbox_items
                if item.payload["payload"]["chat_id"] == -100555
            ]
            self.assertEqual(len(group_items), 1)
            self.assertIn("Результат прогноза", group_items[0].payload["payload"]["text"])
            self.assertIn("Выигрыш", group_items[0].payload["payload"]["text"])
            self.assertIn("Взяли: <b>2</b>", group_items[0].payload["payload"]["text"])
