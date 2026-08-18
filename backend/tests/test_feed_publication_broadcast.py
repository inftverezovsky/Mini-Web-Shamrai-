import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.datastructures import FormData

from src.api import bets as bets_api
from src.models.database import Base
from src.models.models import Bet, DeliveryOutbox, User
from src.services import delivery_outbox
from src.services.delivery_outbox import (
    CHANNEL_FEED_FORECAST_BROADCAST,
    CHANNEL_TELEGRAM_MESSAGE,
)
from src.services.feed_publication_broadcast import enqueue_feed_publication_broadcast


class FeedPublicationBroadcastTests(unittest.IsolatedAsyncioTestCase):
    class EmptyFormRequest:
        async def form(self):
            return FormData()

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def _seed_users(self, session):
        session.add_all(
            [
                User(telegram_id=111, role="owner"),
                User(
                    telegram_id=222,
                    role="user",
                    matches_remaining=0,
                    is_night_mode=True,
                    night_mode_start="00:00",
                    night_mode_end="23:59",
                ),
                User(telegram_id=555, role="user", matches_remaining=0),
                User(telegram_id=333, role="admin"),
                User(telegram_id=-444, role="user", vk_user_id="444"),
            ]
        )
        await session.commit()

    async def test_text_post_is_queued_for_every_registered_telegram_client(self):
        async with self.session_factory() as session:
            await self._seed_users(session)
            post = Bet(
                event_name="⚡ Новость",
                event_name_entities=[
                    {
                        "offset": 0,
                        "length": 1,
                        "custom_emoji_id": "5436188742756893380",
                    }
                ],
                coefficient=Decimal("1.00"),
                description="Текст публикации",
                publication_type="text",
                author_id=111,
            )
            session.add(post)
            await session.flush()

            queued = await enqueue_feed_publication_broadcast(session, post)
            await session.commit()
            deliveries = (
                await session.execute(
                    select(DeliveryOutbox).order_by(DeliveryOutbox.user_id)
                )
            ).scalars().all()

        self.assertEqual(queued, 2)
        self.assertEqual([item.user_id for item in deliveries], [222, 555])
        self.assertTrue(all(item.channel == CHANNEL_TELEGRAM_MESSAGE for item in deliveries))
        self.assertEqual(deliveries[0].payload["method"], "sendMessage")
        self.assertEqual(deliveries[0].payload["payload"]["text"], "⚡ Новость\n\nТекст публикации")
        self.assertEqual(
            deliveries[0].payload["payload"]["entities"][0]["custom_emoji_id"],
            "5436188742756893380",
        )

    async def test_forecast_is_queued_for_every_registered_telegram_client(self):
        async with self.session_factory() as session:
            await self._seed_users(session)
            forecast = Bet(
                event_name="Куба — Доминикана",
                coefficient=Decimal("1.78"),
                description="Первый сет",
                outcome="Фора +6.5",
                publication_type="forecast",
                author_id=111,
            )
            session.add(forecast)
            await session.flush()

            queued = await enqueue_feed_publication_broadcast(session, forecast)
            await session.commit()
            deliveries = (
                await session.execute(
                    select(DeliveryOutbox).order_by(DeliveryOutbox.user_id)
                )
            ).scalars().all()

        self.assertEqual(queued, 2)
        self.assertEqual([item.user_id for item in deliveries], [222, 555])
        self.assertTrue(
            all(item.channel == CHANNEL_FEED_FORECAST_BROADCAST for item in deliveries)
        )
        self.assertEqual(
            deliveries[0].payload,
            {"bet_id": str(forecast.id), "chat_id": 222},
        )
        self.assertEqual(
            deliveries[0].dedupe_key,
            f"feed_forecast:{forecast.id}:222",
        )

    async def test_forecast_outbox_dispatch_uses_full_forecast_sender(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_FEED_FORECAST_BROADCAST,
            user_id=222,
            payload={"bet_id": "7e4468ae-9f50-47c1-bf63-f839f60bd152", "chat_id": 222},
        )

        with patch(
            "src.services.feed_publication_broadcast.dispatch_feed_forecast_broadcast",
            AsyncMock(return_value={"ok": True, "result": {"message_id": 77}}),
        ) as dispatch:
            result = await delivery_outbox.dispatch_delivery(
                delivery,
                db=AsyncMock(),
            )

        self.assertTrue(result["ok"])
        dispatch.assert_awaited_once()
        self.assertEqual(dispatch.await_args.kwargs["chat_id"], 222)

    async def test_panel_forecast_creation_automatically_enqueues_broadcast(self):
        async with self.session_factory() as session:
            await self._seed_users(session)
            admin = await session.get(User, 111)

            forecast = await bets_api.create_bet_with_coupon(
                request=self.EmptyFormRequest(),
                event_name="Куба — Доминикана",
                coefficient=Decimal("1.78"),
                bookmaker_id=None,
                description="Первый сет",
                category="prematch",
                live_ends_at=None,
                price_stars=None,
                brain_score=5,
                api_match_id=None,
                sport_type="Волейбол",
                outcome="Фора +6.5",
                match_link=None,
                publication_type="forecast",
                event_name_entities=None,
                description_entities=None,
                broadcast_telegram=True,
                live_alarm=False,
                coupon_image=None,
                admin=admin,
                db=session,
            )
            deliveries = (
                await session.execute(
                    select(DeliveryOutbox)
                    .where(DeliveryOutbox.channel == CHANNEL_FEED_FORECAST_BROADCAST)
                    .order_by(DeliveryOutbox.user_id)
                )
            ).scalars().all()

        self.assertEqual(forecast.publication_type, "forecast")
        self.assertEqual([item.user_id for item in deliveries], [222, 555])
