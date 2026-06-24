import unittest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.models.database import Base
from src.models.models import PersonalSignal, User
from src.services.connection_onboarding import (
    CONNECTION_SETUP_COMPLETE_SIGNAL_TYPE,
    CONNECTION_SETUP_GUIDE_SIGNAL_TYPE,
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
            vk_user_id="741852963",
            vk_messages_allowed=False,
            web_push_subscription={"endpoint": "https://push.example/sub"},
        )

        actions = {action["id"]: action for action in setup_actions_for_user(user)}

        self.assertIn("setup=vk-messages", actions["allow-vk-messages"]["url"])
        self.assertIn("#connect-vk", actions["allow-vk-messages"]["url"])

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
