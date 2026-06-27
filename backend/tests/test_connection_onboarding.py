import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.database import Base
from src.models.models import DeliveryOutbox, PersonalSignal, User
from src.services.delivery_outbox import CHANNEL_CONNECTION_SETUP_REMINDER, STATUS_CANCELLED, STATUS_PENDING
from src.services.connection_onboarding import (
    CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE,
    CONNECTION_SETUP_GUIDE_SIGNAL_TYPE,
    build_connection_setup_reminder_text,
    dispatch_connection_setup_reminder,
    build_connection_setup_guide_text,
    setup_actions_for_user,
    sync_connection_onboarding,
)


class ConnectionOnboardingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def _signals(self, session):
        return list((await session.execute(select(PersonalSignal).order_by(PersonalSignal.id))).scalars().all())

    async def _outbox(self, session):
        return list((await session.execute(select(DeliveryOutbox).order_by(DeliveryOutbox.created_at))).scalars().all())

    def _user(self, **overrides) -> User:
        fields = {
            "telegram_id": -741852963,
            "first_name": "Web",
            "role": "user",
            "vk_user_id": "741852963",
            "vk_messages_allowed": False,
            "web_push_subscription": None,
        }
        fields.update(overrides)
        return User(**fields)

    async def test_changed_missing_actions_supersede_old_pending_reminder(self):
        async with self.Session() as session:
            user = self._user(
                telegram_id=323456789,
                tg_chat_joined=False,
                vk_user_id="741852963",
                vk_messages_allowed=False,
                web_push_subscription=None,
            )
            session.add(user)
            await session.flush()

            await sync_connection_onboarding(session, user)
            user.tg_chat_joined = True
            await sync_connection_onboarding(session, user)

            deliveries = await self._outbox(session)
            self.assertEqual(len(deliveries), 2)
            old_delivery = next(delivery for delivery in deliveries if "confirm-telegram-chat" in delivery.dedupe_key)
            new_delivery = next(delivery for delivery in deliveries if "confirm-telegram-chat" not in delivery.dedupe_key)
            self.assertEqual(old_delivery.status, STATUS_CANCELLED)
            self.assertEqual(new_delivery.status, STATUS_PENDING)

            user.tg_chat_joined = False
            await sync_connection_onboarding(session, user)

            deliveries = await self._outbox(session)
            self.assertEqual(len(deliveries), 2)
            old_delivery = next(delivery for delivery in deliveries if "confirm-telegram-chat" in delivery.dedupe_key)
            new_delivery = next(delivery for delivery in deliveries if "confirm-telegram-chat" not in delivery.dedupe_key)
            self.assertEqual(old_delivery.status, STATUS_PENDING)
            self.assertEqual(new_delivery.status, STATUS_CANCELLED)

    async def test_first_web_sync_creates_one_guide_with_quick_setup_actions(self):
        async with self.Session() as session:
            user = self._user()
            session.add(user)
            await session.flush()

            first = await sync_connection_onboarding(session, user)
            second = await sync_connection_onboarding(session, user)

            self.assertEqual(first["created_signal_types"], [CONNECTION_SETUP_GUIDE_SIGNAL_TYPE])
            self.assertEqual(second["created_signal_types"], [])

            signals = await self._signals(session)
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0].type, CONNECTION_SETUP_GUIDE_SIGNAL_TYPE)

            actions = signals[0].data["setup_actions"]
            labels = [action["label"] for action in actions]
            self.assertIn("Подключить Telegram", labels)
            self.assertIn("Разрешить сообщения VK", labels)
            self.assertIn("Включить Web Push", labels)
            self.assertTrue(all(action["url"] for action in actions))

            deliveries = await self._outbox(session)
            self.assertEqual(len(deliveries), 1)
            self.assertEqual(deliveries[0].channel, CHANNEL_CONNECTION_SETUP_REMINDER)
            self.assertEqual(deliveries[0].user_id, user.telegram_id)
            self.assertIn("connection_setup_reminder", deliveries[0].dedupe_key)
            self.assertGreater(deliveries[0].next_attempt_at, deliveries[0].created_at + timedelta(hours=23))

    def test_missing_identity_actions_deep_link_to_exact_profile_flows(self):
        user = self._user(
            telegram_id=-741852963,
            vk_user_id=None,
            vk_messages_allowed=False,
            web_push_subscription=None,
        )

        actions = {action["id"]: action for action in setup_actions_for_user(user)}

        self.assertIn("setup=telegram", actions["connect-telegram"]["url"])
        self.assertIn("#connect-telegram", actions["connect-telegram"]["url"])
        self.assertIn("setup=vk", actions["connect-vk"]["url"])
        self.assertIn("#connect-vk", actions["connect-vk"]["url"])
        self.assertIn("setup=notifications", actions["enable-web-push"]["url"])
        self.assertIn("#web-push", actions["enable-web-push"]["url"])

    def test_vk_message_action_opens_vk_delivery_flow(self):
        user = self._user(
            telegram_id=323456789,
            tg_chat_joined=True,
            vk_user_id="741852963",
            vk_messages_allowed=False,
            web_push_subscription={"endpoint": "https://push.example/sub"},
        )

        actions = {action["id"]: action for action in setup_actions_for_user(user)}

        self.assertIn("setup=vk-messages", actions["allow-vk-messages"]["url"])
        self.assertIn("#connect-vk", actions["allow-vk-messages"]["url"])

    def test_linked_telegram_without_chat_gets_chat_confirmation_action(self):
        user = self._user(
            telegram_id=323456789,
            tg_chat_joined=False,
            vk_user_id="741852963",
            vk_messages_allowed=True,
            web_push_subscription={"endpoint": "https://push.example/sub"},
        )

        actions = {action["id"]: action for action in setup_actions_for_user(user)}

        self.assertNotIn("connect-telegram", actions)
        self.assertIn("confirm-telegram-chat", actions)
        self.assertEqual(actions["confirm-telegram-chat"]["label"], "Подтвердить Telegram чат")
        self.assertIn("setup=telegram", actions["confirm-telegram-chat"]["url"])

    def test_guide_text_promises_guided_one_by_one_setup(self):
        text = build_connection_setup_guide_text(self._user(vk_user_id=None))

        self.assertIn("Нажимайте кнопки ниже по очереди", text)
        self.assertIn("я все включу сам", text)
        self.assertIn("Разрешить", text)
        self.assertIn("https://shamra1.pro/app?open=profile", text)

    def test_reminder_text_lists_only_missing_actions_with_direct_links(self):
        text = build_connection_setup_reminder_text(self._user(
            telegram_id=323456789,
            tg_chat_joined=True,
            vk_user_id="741852963",
            vk_messages_allowed=True,
            web_push_subscription=None,
        ))

        self.assertIn("давно не завершено", text)
        self.assertIn("Включить Web Push", text)
        self.assertIn("https://shamra1.pro/app?open=profile", text)
        self.assertNotIn("Подключить Telegram", text)
        self.assertNotIn("Разрешить сообщения VK", text)

    async def test_connection_reminder_dispatches_to_available_telegram_and_vk(self):
        user = self._user(
            telegram_id=323456789,
            vk_user_id="741852963",
            vk_messages_allowed=True,
            web_push_subscription=None,
        )

        with (
            patch(
                "src.services.connection_onboarding.call_telegram_api_async",
                AsyncMock(return_value={"ok": True, "result": {"message_id": 7}}),
            ) as telegram_mock,
            patch(
                "src.services.connection_onboarding.send_vk_message_to_user",
                return_value={"ok": True, "response": 5},
            ) as vk_mock,
        ):
            result = await dispatch_connection_setup_reminder(user)

        self.assertTrue(result["ok"])
        self.assertEqual(result["sent_channels"], ["telegram", "vk"])
        telegram_mock.assert_awaited_once()
        vk_mock.assert_called_once()

    async def test_connection_reminder_skips_completed_client(self):
        user = self._user(
            telegram_id=323456789,
            tg_chat_joined=True,
            vk_user_id="741852963",
            vk_messages_allowed=True,
            web_push_subscription={
                "endpoint": "https://push.example/sub",
                "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
            },
        )

        with (
            patch("src.services.connection_onboarding.call_telegram_api_async", AsyncMock()) as telegram_mock,
            patch("src.services.connection_onboarding.send_vk_message_to_user") as vk_mock,
        ):
            result = await dispatch_connection_setup_reminder(user)

        self.assertTrue(result["ok"])
        self.assertTrue(result["skipped"])
        telegram_mock.assert_not_awaited()
        vk_mock.assert_not_called()

    async def test_completion_message_is_sent_once_after_all_channels_are_ready(self):
        async with self.Session() as session:
            user = self._user(
                telegram_id=323456789,
                vk_user_id=None,
                vk_messages_allowed=False,
                web_push_subscription=None,
            )
            session.add(user)
            await session.flush()

            await sync_connection_onboarding(session, user)
            user.vk_user_id = "741852963"
            user.vk_messages_allowed = True
            user.tg_chat_joined = True
            user.web_push_subscription = {
                "endpoint": "https://push.example/sub",
                "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
            }

            completed = await sync_connection_onboarding(session, user)
            repeated = await sync_connection_onboarding(session, user)

            self.assertEqual(completed["created_signal_types"], [CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE])
            self.assertEqual(repeated["created_signal_types"], [])
            self.assertTrue(completed["checklist"]["complete"])

            signals = await self._signals(session)
            self.assertEqual([signal.type for signal in signals], [
                CONNECTION_SETUP_GUIDE_SIGNAL_TYPE,
                CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE,
            ])
            self.assertIn("всегда на связи", signals[-1].text)

    async def test_already_connected_client_gets_only_completion_message(self):
        async with self.Session() as session:
            user = self._user(
                telegram_id=323456789,
                tg_chat_joined=True,
                vk_user_id="741852963",
                vk_messages_allowed=True,
                web_push_subscription={
                    "endpoint": "https://push.example/sub",
                    "keys": {"p256dh": "p256dh-value", "auth": "auth-value"},
                },
            )
            session.add(user)
            await session.flush()

            result = await sync_connection_onboarding(session, user)

            self.assertEqual(result["created_signal_types"], [CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE])
            signals = await self._signals(session)
            self.assertEqual([signal.type for signal in signals], [CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE])


if __name__ == "__main__":
    unittest.main()
