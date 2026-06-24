import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.database import Base
from src.models.models import PersonalSignal, User
from src.core import redis_cache
from src.services import chat as chat_service
from src.services import signals as signal_service


class SignalHotCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _user(self, telegram_id: int) -> User:
        return User(
            telegram_id=telegram_id,
            username=f"user_{telegram_id}",
            first_name=f"User {telegram_id}",
            role="user",
            purchased_bets_balance=0,
            matches_remaining=0,
        )

    async def test_signal_page_cache_hit_skips_database(self):
        cached_page = {
            "items": [{"id": 1, "text": "Cached", "type": "signal", "data": {}, "created_at": "2026-06-24T00:00:00+00:00"}],
            "next_before_id": None,
            "has_more": False,
        }

        class ExplodingSession:
            async def execute(self, *_args, **_kwargs):
                raise AssertionError("database should not be touched on Redis cache hit")

        with (
            patch.object(chat_service, "cache_get_json", new=AsyncMock(return_value=cached_page)),
            patch.object(chat_service, "cache_set_json", new=AsyncMock()) as cache_set,
        ):
            page = await chat_service.paginated_signal_messages(
                ExplodingSession(),
                user=self._user(101),
                before_id=None,
                limit=50,
            )

        self.assertEqual(page, cached_page)
        cache_set.assert_not_awaited()

    async def test_signal_page_cache_miss_persists_hot_page(self):
        async with self.Session() as session:
            user = self._user(101)
            session.add(user)
            await session.flush()
            session.add(PersonalSignal(user_id=user.telegram_id, text="Fresh signal", type="signal", data={}))
            await session.flush()

            with (
                patch.object(chat_service, "cache_get_json", new=AsyncMock(return_value=None)),
                patch.object(chat_service, "cache_set_json", new=AsyncMock()) as cache_set,
            ):
                page = await chat_service.paginated_signal_messages(
                    session,
                    user=user,
                    before_id=None,
                    limit=50,
                )

        self.assertEqual([item["text"] for item in page["items"]], ["Fresh signal"])
        cache_set.assert_awaited_once()
        cached_payload = cache_set.await_args.args[1]
        self.assertEqual(cached_payload["items"][0]["text"], "Fresh signal")

    async def test_deliver_personal_signal_flushes_cache_after_commit_queue(self):
        async with self.Session() as session:
            user = self._user(101)
            session.add(user)
            await session.flush()

            with (
                patch.object(redis_cache, "invalidate_signal_page_cache_for_users", new=AsyncMock()) as invalidate,
                patch.object(signal_service.signal_stream_hub, "send_to_user", new=AsyncMock()),
                patch.object(signal_service, "enqueue_signal_external_delivery_batch", new=AsyncMock()),
            ):
                await signal_service.deliver_personal_signal(
                    session,
                    user=user,
                    text="New signal",
                    send_telegram=False,
                    send_web_push=False,
                )
                await redis_cache.flush_signal_page_cache_invalidations(session)

        invalidate.assert_awaited_once()
        self.assertEqual(invalidate.await_args.args[0], {user.telegram_id})


if __name__ == "__main__":
    unittest.main()
