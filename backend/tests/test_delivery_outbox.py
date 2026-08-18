import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.models.database import Base
from src.models.models import DeliveryOutbox, User
from src.services import delivery_outbox
from src.services.delivery_outbox import (
    CHANNEL_FORECAST_ADMIN_FULL_COPY,
    CHANNEL_FORECAST_AUTO_DELIVERY,
    CHANNEL_FORECAST_FULL_DELIVERY,
    CHANNEL_CONNECTION_SETUP_REMINDER,
    CHANNEL_TELEGRAM_MESSAGE,
    CHANNEL_VK_MESSAGE,
    CHANNEL_WEB_PUSH_SIGNAL,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RETRY,
    STATUS_SENT,
)


class FakeDb:
    def __init__(self):
        self.added = []

    def add(self, value):
        self.added.append(value)


class DeliveryOutboxTests(unittest.IsolatedAsyncioTestCase):
    async def test_enqueue_delivery_dedupes_with_database_conflict_handling(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with Session() as session:
                first = await delivery_outbox.enqueue_delivery(
                    session,
                    channel=CHANNEL_TELEGRAM_MESSAGE,
                    user_id=12345,
                    dedupe_key="same-delivery",
                    payload={"method": "sendMessage", "payload": {"chat_id": 12345, "text": "one"}},
                )
                second = await delivery_outbox.enqueue_delivery(
                    session,
                    channel=CHANNEL_TELEGRAM_MESSAGE,
                    user_id=12345,
                    dedupe_key="same-delivery",
                    payload={"method": "sendMessage", "payload": {"chat_id": 12345, "text": "two"}},
                )
                await session.commit()

                count = (await session.execute(select(func.count(DeliveryOutbox.id)))).scalar_one()

            self.assertEqual(first.id, second.id)
            self.assertEqual(count, 1)
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    async def test_enqueue_signal_delivery_creates_telegram_and_web_push_items(self):
        db = FakeDb()

        items = await delivery_outbox.enqueue_signal_external_delivery_batch(
            db,
            [
                {
                    "user_id": 12345,
                    "text": "signal text",
                    "signal_payload": {"id": 77, "user_id": 12345, "text": "signal text", "type": "signal"},
                    "web_push_subscription": {"endpoint": "https://push.example", "keys": {}},
                    "send_telegram": True,
                    "send_web_push": True,
                }
            ],
        )

        self.assertEqual(items, db.added)
        self.assertEqual([item.channel for item in items], [CHANNEL_TELEGRAM_MESSAGE, CHANNEL_WEB_PUSH_SIGNAL])
        self.assertEqual(items[0].dedupe_key, "personal_signal:77:telegram")
        self.assertEqual(items[1].dedupe_key, "personal_signal:77:web_push")
        self.assertEqual(items[0].payload["payload"]["chat_id"], 12345)
        self.assertEqual(items[1].payload["signal_payload"]["id"], 77)

    async def test_mark_delivery_failed_retries_before_terminal_failure(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_WEB_PUSH_SIGNAL,
            status=STATUS_PENDING,
            payload={},
            attempt_count=0,
            max_attempts=2,
        )

        await delivery_outbox.mark_delivery_failed(SimpleNamespace(), delivery, {"description": "timeout"})

        self.assertEqual(delivery.status, STATUS_RETRY)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(delivery.last_error, "timeout")
        self.assertIsNotNone(delivery.next_attempt_at)

        await delivery_outbox.mark_delivery_failed(SimpleNamespace(), delivery, {"description": "still down"})

        self.assertEqual(delivery.status, STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 2)
        self.assertEqual(delivery.last_error, "still down")

    async def test_invalid_web_push_subscription_is_categorized_and_not_retried(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_WEB_PUSH_SIGNAL,
            status=STATUS_PENDING,
            payload={},
            attempt_count=0,
            max_attempts=5,
        )

        await delivery_outbox.mark_delivery_failed(
            SimpleNamespace(),
            delivery,
            {"description": "subscription expired", "invalid_subscription": True, "status_code": 410},
        )

        self.assertEqual(delivery.status, STATUS_FAILED)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(
            delivery.payload["terminal_category"],
            delivery_outbox.TERMINAL_CATEGORY_INVALID_WEB_PUSH_SUBSCRIPTION,
        )

    async def test_metrics_report_rolling_channel_windows_and_exclude_expected_web_push_invalidations(self):
        engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        session_factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            now = delivery_outbox._now()
            async with session_factory() as session:
                session.add_all(
                    [
                        DeliveryOutbox(
                            channel=CHANNEL_TELEGRAM_MESSAGE,
                            status=STATUS_SENT,
                            payload={},
                            updated_at=now - timedelta(minutes=10),
                        ),
                        DeliveryOutbox(
                            channel=CHANNEL_TELEGRAM_MESSAGE,
                            status=STATUS_FAILED,
                            payload={},
                            updated_at=now - timedelta(minutes=5),
                        ),
                        DeliveryOutbox(
                            channel=CHANNEL_WEB_PUSH_SIGNAL,
                            status=STATUS_FAILED,
                            payload={
                                "terminal_category": delivery_outbox.TERMINAL_CATEGORY_INVALID_WEB_PUSH_SUBSCRIPTION
                            },
                            updated_at=now - timedelta(minutes=2),
                        ),
                        DeliveryOutbox(
                            channel=CHANNEL_VK_MESSAGE,
                            status=STATUS_SENT,
                            payload={},
                            updated_at=now - timedelta(hours=2),
                        ),
                    ]
                )
                await session.commit()

                with patch.object(delivery_outbox, "_now", return_value=now):
                    metrics = await delivery_outbox.get_delivery_outbox_metrics(session)

            one_hour = metrics["windows"]["1h"]
            twenty_four_hours = metrics["windows"]["24h"]
            self.assertIn(CHANNEL_TELEGRAM_MESSAGE, one_hour["by_channel"])
            self.assertIn(CHANNEL_VK_MESSAGE, one_hour["by_channel"])
            self.assertIn(CHANNEL_WEB_PUSH_SIGNAL, one_hour["by_channel"])
            self.assertEqual(one_hour["by_channel"][CHANNEL_TELEGRAM_MESSAGE]["fail_rate"], 50.0)
            self.assertEqual(
                one_hour["by_channel"][CHANNEL_WEB_PUSH_SIGNAL]["terminal_invalidations"],
                1,
            )
            self.assertEqual(one_hour["by_channel"][CHANNEL_WEB_PUSH_SIGNAL]["fail_rate"], 0.0)
            self.assertEqual(one_hour["fail_rate"], 50.0)
            self.assertEqual(twenty_four_hours["by_channel"][CHANNEL_VK_MESSAGE]["sent"], 1)
            self.assertEqual(metrics["fail_rate"], one_hour["fail_rate"])
        finally:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
            await engine.dispose()

    async def test_dispatch_telegram_message_uses_stored_api_payload(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_TELEGRAM_MESSAGE,
            status=STATUS_PENDING,
            payload={
                "method": "sendMessage",
                "payload": {"chat_id": 12345, "text": "hello"},
            },
        )

        with patch.object(
            delivery_outbox,
            "call_telegram_api_async",
            AsyncMock(return_value={"ok": True, "result": {"message_id": 9}}),
        ) as api_mock:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertEqual(result["ok"], True)
        api_mock.assert_awaited_once_with("sendMessage", {"chat_id": 12345, "text": "hello"})

    async def test_dispatch_telegram_message_falls_back_when_custom_emoji_is_denied(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_TELEGRAM_MESSAGE,
            status=STATUS_PENDING,
            payload={
                "method": "sendMessage",
                "payload": {
                    "chat_id": 12345,
                    "text": "🙂 hello",
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
        )

        with patch.object(
            delivery_outbox,
            "call_telegram_api_async",
            AsyncMock(
                side_effect=[
                    {"ok": False, "description": "Bad Request: can't use custom emoji"},
                    {"ok": True, "result": {"message_id": 10}},
                ]
            ),
        ) as api_mock:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertTrue(result["ok"])
        self.assertEqual(api_mock.await_count, 2)
        self.assertNotIn("entities", api_mock.await_args_list[1].args[1])

    async def test_dispatch_vk_message_uses_stored_user_and_payload(self):
        user = User(telegram_id=-12345, vk_user_id="456", vk_messages_allowed=True)
        delivery = DeliveryOutbox(
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            payload={"message": "hello vk"},
        )
        delivery.user = user

        with patch.object(
            delivery_outbox,
            "send_vk_message_to_user",
            return_value={"ok": True, "response": 1},
        ) as vk_mock:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertEqual(result["ok"], True)
        vk_mock.assert_called_once_with(user, "hello vk", keyboard=None, image_path=None)

    async def test_dispatch_connection_setup_reminder_uses_current_user_state(self):
        user = User(telegram_id=12345, tg_chat_joined=True, vk_user_id="456", vk_messages_allowed=True)
        delivery = DeliveryOutbox(
            channel=CHANNEL_CONNECTION_SETUP_REMINDER,
            user_id=user.telegram_id,
            payload={"kind": "connection_setup_reminder"},
        )
        delivery.user = user

        with patch(
            "src.services.connection_onboarding.dispatch_connection_setup_reminder",
            AsyncMock(return_value={"ok": True, "sent_channels": ["telegram", "vk"]}),
        ) as reminder_mock:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertTrue(result["ok"])
        reminder_mock.assert_awaited_once_with(user)

    async def test_dispatch_forecast_auto_delivery_uses_stored_request_id(self):
        request_id = uuid4()
        delivery = DeliveryOutbox(
            channel=CHANNEL_FORECAST_AUTO_DELIVERY,
            status=STATUS_PENDING,
            payload={"request_id": str(request_id), "delivery_method": "auto"},
        )

        with patch(
            "src.services.forecast_delivery.auto_deliver_forecast_request_for_request",
            AsyncMock(return_value={"ok": True, "already_recorded": False}),
        ) as auto_deliver:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertTrue(result["ok"])
        auto_deliver.assert_awaited_once_with(request_id, delivery_method="auto")

    async def test_dispatch_forecast_full_delivery_uses_stored_request_id(self):
        request_id = uuid4()
        delivery = DeliveryOutbox(
            channel=CHANNEL_FORECAST_FULL_DELIVERY,
            payload={"request_id": str(request_id), "delivery_method": "vk_bot"},
        )

        with patch(
            "src.services.forecast_delivery.dispatch_forecast_full_delivery_from_outbox",
            AsyncMock(return_value={"ok": True, "delivery_method": "vk_bot"}),
        ) as full_deliver:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertTrue(result["ok"])
        full_deliver.assert_awaited_once_with(request_id, delivery_method="vk_bot")

    async def test_dispatch_forecast_admin_full_copy_uses_stored_request_id(self):
        request_id = uuid4()
        delivery = DeliveryOutbox(
            channel=CHANNEL_FORECAST_ADMIN_FULL_COPY,
            payload={"request_id": str(request_id)},
        )

        with patch(
            "src.services.forecast_delivery.dispatch_admin_group_full_forecast_copy_from_outbox",
            AsyncMock(return_value={"ok": True}),
        ) as admin_copy:
            result = await delivery_outbox.dispatch_delivery(delivery)

        self.assertTrue(result["ok"])
        admin_copy.assert_awaited_once_with(request_id)

    async def test_mark_delivery_sent_clears_lock_and_stores_safe_result(self):
        delivery = DeliveryOutbox(
            channel=CHANNEL_TELEGRAM_MESSAGE,
            status=STATUS_PENDING,
            payload={"method": "sendMessage"},
            locked_at=delivery_outbox._now(),
        )

        await delivery_outbox.mark_delivery_sent(
            SimpleNamespace(),
            delivery,
            {"ok": True, "result": {"message_id": 9}},
        )

        self.assertEqual(delivery.status, STATUS_SENT)
        self.assertIsNone(delivery.locked_at)
        self.assertIsNone(delivery.last_error)
        self.assertEqual(delivery.payload["last_result"], {"ok": True})


if __name__ == "__main__":
    unittest.main()
