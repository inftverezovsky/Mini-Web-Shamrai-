import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import telegram_webhook
from src.models.database import Base
from src.models.models import Bet, DeliveryOutbox, User
from src.services.telegram_manual_post import (
    TelegramPostDraft,
    TelegramPostPublicationReport,
    extract_replied_post,
    publish_telegram_post,
)


class TelegramManualPostParsingTests(unittest.TestCase):
    def test_extracts_ready_text_and_custom_emoji_from_replied_message(self):
        draft = extract_replied_post(
            {
                "text": "/publish",
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 111},
                "reply_to_message": {
                    "message_id": 55,
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 111},
                    "text": "🔥 Важный пост\nТекст для клиентов",
                    "entities": [
                        {
                            "type": "custom_emoji",
                            "offset": 0,
                            "length": 2,
                            "custom_emoji_id": "1000000000000000001",
                        }
                    ],
                },
            },
            actor_user_id=111,
        )

        self.assertEqual(draft.source_chat_id, 111)
        self.assertEqual(draft.source_message_id, 55)
        self.assertEqual(draft.title, "🔥 Важный пост")
        self.assertEqual(draft.body, "Текст для клиентов")
        self.assertEqual(draft.custom_emoji_count, 1)

    def test_requires_private_reply_to_the_actors_own_message(self):
        with self.assertRaisesRegex(ValueError, "ответ"):
            extract_replied_post(
                {
                    "text": "/publish",
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 111},
                },
                actor_user_id=111,
            )

        with self.assertRaisesRegex(ValueError, "собственное"):
            extract_replied_post(
                {
                    "text": "/publish",
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 111},
                    "reply_to_message": {
                        "message_id": 56,
                        "chat": {"id": 111, "type": "private"},
                        "from": {"id": 999},
                        "text": "Чужое сообщение",
                    },
                },
                actor_user_id=111,
            )

    def test_rejects_oversized_text(self):
        with self.assertRaisesRegex(ValueError, "4096"):
            extract_replied_post(
                {
                    "text": "/publish",
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 111},
                    "reply_to_message": {
                        "message_id": 57,
                        "chat": {"id": 111, "type": "private"},
                        "from": {"id": 111},
                        "text": "x" * 4097,
                    },
                },
                actor_user_id=111,
            )


class TelegramManualPostPublicationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_creates_feed_text_post_and_queues_exact_telegram_copy(self):
        async with self.session_factory() as session:
            session.add_all(
                [
                    User(telegram_id=111, role="owner"),
                    User(telegram_id=222, role="user"),
                    User(telegram_id=333, role="admin"),
                    User(telegram_id=-444, role="user", vk_user_id="444"),
                ]
            )
            await session.commit()

            report = await publish_telegram_post(
                session,
                TelegramPostDraft(
                    source_chat_id=111,
                    source_message_id=55,
                    text="🔥 Важный пост\nТекст для клиентов",
                    title="🔥 Важный пост",
                    body="Текст для клиентов",
                    custom_emoji_count=1,
                ),
                actor_user_id=111,
            )
            await session.commit()

            repeated_report = await publish_telegram_post(
                session,
                TelegramPostDraft(
                    source_chat_id=111,
                    source_message_id=55,
                    text="🔥 Важный пост\nТекст для клиентов",
                    title="🔥 Важный пост",
                    body="Текст для клиентов",
                    custom_emoji_count=1,
                ),
                actor_user_id=111,
            )
            await session.commit()

            feed_post = (await session.execute(select(Bet))).scalars().one()
            deliveries = (await session.execute(select(DeliveryOutbox))).scalars().all()

        self.assertEqual(report.telegram_recipients, 1)
        self.assertEqual(report.custom_emoji_count, 1)
        self.assertTrue(repeated_report.already_published)
        self.assertEqual(repeated_report.feed_post_id, report.feed_post_id)
        self.assertEqual(feed_post.publication_type, "text")
        self.assertEqual(feed_post.delivery_mode, "feed")
        self.assertEqual(feed_post.event_name, "🔥 Важный пост")
        self.assertEqual(feed_post.description, "Текст для клиентов")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].user_id, 222)
        self.assertEqual(deliveries[0].payload["method"], "copyMessage")
        self.assertEqual(
            deliveries[0].payload["payload"],
            {
                "chat_id": 222,
                "from_chat_id": 111,
                "message_id": 55,
            },
        )


class TelegramManualPostWebhookTests(unittest.IsolatedAsyncioTestCase):
    class FakeSession:
        def __init__(self):
            self.committed = False
            self.rolled_back = False

        async def commit(self):
            self.committed = True

        async def rollback(self):
            self.rolled_back = True

    async def test_publish_command_fails_closed_for_regular_users(self):
        session = self.FakeSession()
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(telegram_webhook.settings, "SALES_MANAGER_TELEGRAM_ID", 222),
        ):
            response = await telegram_webhook._build_manual_post_response(
                {"text": "/publish"},
                333,
                session,
            )

        self.assertIn("только владельцу", response["text"])
        self.assertFalse(session.committed)

    async def test_publish_command_commits_for_owner(self):
        session = self.FakeSession()
        message = {
            "text": "/publish",
            "chat": {"id": 111, "type": "private"},
            "from": {"id": 111},
            "reply_to_message": {
                "message_id": 55,
                "chat": {"id": 111, "type": "private"},
                "from": {"id": 111},
                "text": "Готовый пост",
            },
        }
        report = TelegramPostPublicationReport(
            feed_post_id=uuid4(),
            telegram_recipients=7,
            custom_emoji_count=2,
        )
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(
                telegram_webhook,
                "publish_telegram_post",
                new=AsyncMock(return_value=report),
            ) as publish,
        ):
            response = await telegram_webhook._build_manual_post_response(
                message,
                111,
                session,
            )

        self.assertTrue(session.committed)
        self.assertFalse(session.rolled_back)
        self.assertIn("Ленту", response["text"])
        self.assertIn("7", response["text"])
        publish.assert_awaited_once()
