import unittest
import json
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import admin_broadcast
from src.models.database import Base
from src.models.models import Bet, Bookmaker, ForecastRequest, User


class FakeFormData(dict):
    def getlist(self, key: str) -> list:
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


class AdminForecastReannounceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _user(self, telegram_id: int, bookmakers: list[Bookmaker], *, balance: int = 5, role: str = "user") -> User:
        user = User(
            telegram_id=telegram_id,
            username=f"user_{telegram_id}",
            role=role,
            purchased_bets_balance=balance,
            matches_remaining=balance,
            alert_min_coef=1.0,
        )
        user.bookmakers = bookmakers
        return user

    async def test_reannounce_creates_requests_only_for_users_without_any_existing_request(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            betboom = Bookmaker(id=2, name="БетБум", code="betboom", is_active=True)
            winline = Bookmaker(id=3, name="Винлайн", code="winline", is_active=True)
            existing_statuses = ["announced", "interested", "sent", "declined", "removed"]
            existing_users = [
                self._user(100 + index, [fonbet, betboom, winline])
                for index, _status in enumerate(existing_statuses)
            ]
            new_betboom_user = self._user(201, [betboom])
            new_winline_user = self._user(202, [winline])
            unrelated_user = self._user(203, [fonbet])
            admin = User(telegram_id=900, username="admin", role="admin")
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Team A - Team B",
                coefficient=Decimal("2.10"),
                fair_coefficient=Decimal("1.74"),
                teaser_text="Есть закрытый прогноз под вашу БК.",
                status="pending",
                delivery_mode="sales_private",
                sport_type="Футбол",
                outcome="П1",
                coupon_image_url="/static/coupons/coupon.png",
                bookmaker_links=[
                    {"bookmaker_id": 1, "url": "https://fonbet.ru/match/1"},
                    {"bookmaker_id": 2, "url": "https://betboom.ru/match/1"},
                    {"bookmaker_id": 3, "url": "https://winline.ru/match/1"},
                ],
            )
            bet.bookmakers = [fonbet]
            session.add_all([
                fonbet,
                betboom,
                winline,
                admin,
                bet,
                *existing_users,
                new_betboom_user,
                new_winline_user,
                unrelated_user,
            ])
            await session.flush()
            for user, request_status in zip(existing_users, existing_statuses):
                session.add(ForecastRequest(
                    bet_id=bet.id,
                    user_id=user.telegram_id,
                    status=request_status,
                ))
            await session.commit()

            target_users = [*existing_users, new_betboom_user, new_winline_user]
            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1", "2", "3"],
                    "bookmaker_links": [],
                }))
            )

            with (
                patch.object(admin_broadcast, "_get_smart_target_users", AsyncMock(return_value=target_users)) as get_targets,
                patch.object(
                    admin_broadcast,
                    "_send_forecast_teasers",
                    AsyncMock(return_value={
                        "delivery": {
                            "sent": 2,
                            "failed": 0,
                            "errors": [],
                            "telegram": {},
                            "vk_messages": {},
                            "web_push": {},
                        },
                        "sent": 2,
                        "failed": 0,
                        "errors": [],
                    }),
                ) as send_teasers,
            ):
                response = await admin_broadcast.prepare_forecast_broadcast_full(
                    bet.id,
                    fake_request,
                    event_name="Team A - Team B",
                    outcome="П1",
                    coefficient=Decimal("2.10"),
                    fair_coefficient=Decimal("1.74"),
                    sport_type="Футбол",
                    teaser_text="Есть закрытый прогноз под вашу БК.",
                    description="Описание",
                    match_link=None,
                    category=None,
                    live_ends_at=None,
                    auto_send_interested=False,
                    reannounce_new_audience=True,
                    coupon_image=None,
                    current_admin=admin,
                    db=session,
                )

            self.assertEqual(response.reannounce.total_audience, 2)
            self.assertEqual(get_targets.await_args.kwargs["bookmaker_ids"], [2, 3])
            request_rows = (await session.execute(
                select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id)
            )).scalars().all()
            request_user_ids = {request.user_id for request in request_rows}
            self.assertEqual(len(request_rows), len(existing_statuses) + 2)
            self.assertTrue({new_betboom_user.telegram_id, new_winline_user.telegram_id}.issubset(request_user_ids))
            self.assertNotIn(unrelated_user.telegram_id, request_user_ids)
            total_rows = await session.execute(select(func.count()).select_from(ForecastRequest))
            self.assertEqual(int(total_rows.scalar() or 0), len(existing_statuses) + 2)
            send_teasers.assert_awaited_once()
            sent_requests = send_teasers.await_args.kwargs["forecast_requests"]
            self.assertEqual({request.user_id for request in sent_requests}, {201, 202})

    async def test_smart_target_users_include_zero_balance_matching_clients(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            betboom = Bookmaker(id=2, name="БетБум", code="betboom", is_active=True)
            zero_balance_user = self._user(301, [fonbet], balance=0)
            active_user = self._user(302, [fonbet], balance=4)
            wrong_bookmaker_user = self._user(303, [betboom], balance=0)
            staff_user = self._user(304, [fonbet], balance=0, role="admin")
            session.add_all([fonbet, betboom, zero_balance_user, active_user, wrong_bookmaker_user, staff_user])
            await session.commit()

            target_users = await admin_broadcast._get_smart_target_users(
                session,
                sport_filter="Футбол",
                bookmaker_id=None,
                bookmaker_ids=[fonbet.id],
                delivery_channel="any",
                min_coef=2.0,
            )

        target_ids = {user.telegram_id for user in target_users}
        self.assertIn(zero_balance_user.telegram_id, target_ids)
        self.assertIn(active_user.telegram_id, target_ids)
        self.assertNotIn(wrong_bookmaker_user.telegram_id, target_ids)
        self.assertNotIn(staff_user.telegram_id, target_ids)

    async def test_paid_set_broadcast_does_not_save_bookmaker_links_from_teaser_form(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            betboom = Bookmaker(id=2, name="БетБум", code="betboom", is_active=True)
            admin = User(telegram_id=900, username="admin", role="admin")
            client = self._user(301, [fonbet, betboom], balance=0)
            session.add_all([fonbet, betboom, admin, client])
            await session.commit()

            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1", "2"],
                    "bookmaker_links": [json.dumps([
                        {"bookmaker_id": 1, "url": "fonbet.ru/match/1"},
                        {"bookmaker_id": 2, "url": "https://betboom.ru/match/2"},
                    ])],
                }))
            )

            with (
                patch.object(admin_broadcast, "_get_smart_target_users", AsyncMock(return_value=[client])),
                patch.object(admin_broadcast, "broadcast_personal_signals", AsyncMock(return_value={"created": 1})),
                patch.object(admin_broadcast, "_send_telegram_jobs", AsyncMock(return_value=(0, 0, []))),
                patch.object(admin_broadcast, "_refresh_vk_audience", AsyncMock(return_value=[])),
                patch.object(admin_broadcast, "_send_vk_jobs", AsyncMock(return_value=(0, 0, []))),
            ):
                await admin_broadcast.create_paid_set_broadcast(
                    fake_request,
                    title="ПЛАТНЫЙ НАБОР",
                    event_name="Team A - Team B",
                    outcome="П1",
                    coefficient=Decimal("3.90"),
                    price_rub=3000,
                    bookmaker_id=None,
                    sport_type="Футбол",
                    teaser_text="Реальный КФ не выше 1.9!",
                    coupon_image=None,
                    current_admin=admin,
                    db=session,
                )

            bet = (await session.execute(select(Bet).filter(Bet.delivery_mode == "paid_set"))).scalars().one()
            self.assertEqual(bet.bookmaker_links, [])

    async def test_paid_set_broadcast_allows_missing_match_sport_and_coupon(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            admin = User(telegram_id=900, username="admin", role="admin")
            client = self._user(301, [fonbet], balance=0)
            session.add_all([fonbet, admin, client])
            await session.commit()

            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1"],
                    "bookmaker_links": [],
                }))
            )

            with (
                patch.object(admin_broadcast, "_get_smart_target_users", AsyncMock(return_value=[client])),
                patch.object(admin_broadcast, "broadcast_personal_signals", AsyncMock(return_value={"created": 1})),
                patch.object(admin_broadcast, "_send_telegram_jobs", AsyncMock(return_value=(0, 0, []))),
                patch.object(admin_broadcast, "_refresh_vk_audience", AsyncMock(return_value=[])),
                patch.object(admin_broadcast, "_send_vk_jobs", AsyncMock(return_value=(0, 0, []))),
            ):
                await admin_broadcast.create_paid_set_broadcast(
                    fake_request,
                    title="ПЛАТНЫЙ НАБОР",
                    event_name="",
                    outcome="П1",
                    coefficient=Decimal("3.90"),
                    price_rub=3000,
                    bookmaker_id=None,
                    sport_type=None,
                    teaser_text="Реальный КФ не выше 1.9!",
                    coupon_image=None,
                    current_admin=admin,
                    db=session,
                )

            bet = (await session.execute(select(Bet).filter(Bet.delivery_mode == "paid_set"))).scalars().one()
            self.assertEqual(bet.event_name, "Платный набор")
            self.assertIsNone(bet.sport_type)
            self.assertIsNone(bet.coupon_image_url)

    async def test_paid_set_full_forecast_save_adds_optional_bookmaker_links_for_sale_delivery(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            betboom = Bookmaker(id=2, name="БетБум", code="betboom", is_active=True)
            admin = User(telegram_id=900, username="admin", role="admin")
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Team A - Team B",
                coefficient=Decimal("3.90"),
                price_stars=3000,
                status="pending",
                delivery_mode="paid_set",
                sport_type="Футбол",
                outcome="П1",
                description="Реальный КФ не выше 1.9!",
                coupon_image_url="/static/coupons/old.png",
                bookmaker_links=[],
            )
            bet.bookmakers = [fonbet, betboom]
            session.add_all([fonbet, betboom, admin, bet])
            await session.commit()

            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1", "2"],
                    "bookmaker_links": [json.dumps([
                        {"bookmaker_id": 1, "url": "fonbet.ru/match/1"},
                        {"bookmaker_id": 2, "url": "https://betboom.ru/match/2"},
                    ])],
                }))
            )

            response = await admin_broadcast.prepare_forecast_broadcast_full(
                bet.id,
                fake_request,
                event_name="Team A - Team B",
                outcome="П1",
                coefficient=Decimal("3.90"),
                fair_coefficient="",
                sport_type="Футбол",
                teaser_text="Реальный КФ не выше 1.9!",
                description="Полная аналитика после оплаты",
                match_link=None,
                category=None,
                live_ends_at=None,
                auto_send_interested=False,
                reannounce_new_audience=False,
                coupon_image=None,
                current_admin=admin,
                db=session,
            )

            self.assertEqual(
                [link.model_dump() for link in response.bet.bookmaker_links],
                [
                    {"bookmaker_id": 1, "url": "https://fonbet.ru/match/1"},
                    {"bookmaker_id": 2, "url": "https://betboom.ru/match/2"},
                ],
            )

    async def test_full_forecast_save_allows_missing_match_sport_and_coupon(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            admin = User(telegram_id=900, username="admin", role="admin")
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Закрытый прогноз",
                coefficient=Decimal("2.10"),
                status="pending",
                delivery_mode="sales_private",
                outcome=None,
                coupon_image_url=None,
                bookmaker_links=[],
            )
            bet.bookmakers = [fonbet]
            session.add_all([fonbet, admin, bet])
            await session.commit()

            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1"],
                    "bookmaker_links": [],
                }))
            )

            response = await admin_broadcast.prepare_forecast_broadcast_full(
                bet.id,
                fake_request,
                event_name="",
                outcome="П1",
                coefficient=Decimal("2.10"),
                fair_coefficient="",
                sport_type="",
                teaser_text="Есть закрытый прогноз под вашу БК.",
                description="Описание",
                match_link=None,
                category=None,
                live_ends_at=None,
                auto_send_interested=False,
                reannounce_new_audience=False,
                coupon_image=None,
                current_admin=admin,
                db=session,
            )

            self.assertEqual(response.bet.event_name, "Закрытый прогноз")
            self.assertEqual(response.bet.outcome, "П1")
            self.assertIsNone(response.bet.coupon_image_url)
            self.assertIsNone(response.bet.sport_type)

    async def test_edit_can_clear_fair_coefficient_without_disabling_existing_auto_send(self):
        async with self.Session() as session:
            fonbet = Bookmaker(id=1, name="Фонбет", code="fonbet", is_active=True)
            admin = User(telegram_id=900, username="admin", role="admin")
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Team A - Team B",
                coefficient=Decimal("2.10"),
                fair_coefficient=Decimal("1.74"),
                teaser_text="Есть закрытый прогноз под вашу БК.",
                status="pending",
                delivery_mode="sales_private",
                sport_type="Футбол",
                outcome="П1",
                coupon_image_url="/static/coupons/coupon.png",
                bookmaker_links=[{"bookmaker_id": 1, "url": "https://fonbet.ru/match/1"}],
                auto_send_on_interest=True,
            )
            bet.bookmakers = [fonbet]
            session.add_all([fonbet, admin, bet])
            await session.commit()

            fake_request = SimpleNamespace(
                form=AsyncMock(return_value=FakeFormData({
                    "bookmaker_ids": ["1"],
                    "fair_coefficient": "",
                    "bookmaker_links": [],
                }))
            )

            response = await admin_broadcast.prepare_forecast_broadcast_full(
                bet.id,
                fake_request,
                event_name="Team A - Team B",
                outcome="П1",
                coefficient=Decimal("2.10"),
                fair_coefficient="",
                sport_type="Футбол",
                teaser_text="Есть закрытый прогноз под вашу БК.",
                description="Описание",
                match_link=None,
                category=None,
                live_ends_at=None,
                auto_send_interested=False,
                reannounce_new_audience=False,
                coupon_image=None,
                current_admin=admin,
                db=session,
            )

            self.assertIsNone(response.bet.fair_coefficient)
            self.assertTrue(response.bet.auto_send_on_interest)


if __name__ == "__main__":
    unittest.main()
