import inspect
import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import admin_web_chat, signals
from src.api.deps import get_current_admin, get_current_admin_read
from src.models.database import Base
from src.models.models import AdminAuditLog, DeliveryOutbox, PersonalSignal, User
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, CHANNEL_VK_MESSAGE, CHANNEL_WEB_PUSH_SIGNAL
from src.services import signals as signal_services
from src.services.signals import SUPPORT_CLIENT_MESSAGE_TYPE, SUPPORT_STAFF_MESSAGE_TYPE


class SupportWebChatTests(unittest.IsolatedAsyncioTestCase):
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
            purchased_bets_balance=0,
            matches_remaining=0,
        )

    async def test_staff_roles_can_send_message_to_client_as_shamrai(self):
        for offset, role in enumerate(("moderator", "admin", "owner"), start=1):
            async with self.Session() as session:
                staff = self._user(900 + offset, role=role)
                client = self._user(100 + offset)
                client.web_push_subscription = {"endpoint": f"https://push.example/{offset}", "keys": {}}
                session.add_all([staff, client])
                await session.commit()

                response = await admin_web_chat.send_admin_web_chat_message(
                    client.telegram_id,
                    admin_web_chat.SupportMessageCreate(text="Здравствуйте, это Shamrai."),
                    admin=staff,
                    db=session,
                )

                self.assertEqual(response.direction, "staff")
                self.assertEqual(response.author_label, "Shamrai")
                self.assertEqual(response.sender_user_id, staff.telegram_id)
                self.assertEqual(response.sender_role, role)

                signal = (
                    await session.execute(
                        select(PersonalSignal).filter(PersonalSignal.user_id == client.telegram_id)
                    )
                ).scalars().one()
                self.assertEqual(signal.type, SUPPORT_STAFF_MESSAGE_TYPE)
                self.assertEqual(signal.data["sender_user_id"], staff.telegram_id)
                self.assertEqual(signal.data["sender_role"], role)

                outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
                self.assertEqual([item.channel for item in outbox_items], [CHANNEL_WEB_PUSH_SIGNAL])
                self.assertEqual(outbox_items[0].user_id, client.telegram_id)
                self.assertEqual(outbox_items[0].payload["signal_payload"]["type"], SUPPORT_STAFF_MESSAGE_TYPE)

                audit_log = (await session.execute(select(AdminAuditLog))).scalars().one()
                self.assertEqual(audit_log.action, "support_chat_staff_message_sent")
                self.assertEqual(audit_log.target_user_id, client.telegram_id)
                self.assertEqual(audit_log.details["message_id"], signal.id)
                self.assertEqual(audit_log.details["text_length"], len("Здравствуйте, это Shamrai."))
                self.assertNotIn("Здравствуйте", str(audit_log.details))

    async def test_client_message_creates_web_only_signal_and_notifies_staff(self):
        async with self.Session() as session:
            client = self._user(101)
            staff = self._user(901, role="moderator")
            staff.web_push_subscription = {"endpoint": "https://push.example/staff", "keys": {}}
            session.add_all([client, staff])
            await session.commit()

            staff_events = []

            async def fake_staff_send(user_id, payload):
                staff_events.append((user_id, payload["event"], payload["message"]["text"]))

            original_send = signal_services.support_staff_stream_hub.send_to_user
            signal_services.support_staff_stream_hub.send_to_user = fake_staff_send
            try:
                response = await signals.send_support_message_from_web_chat(
                    signals.SupportMessageCreate(text="Нужна помощь по прогнозу."),
                    current_user=client,
                    db=session,
                )
            finally:
                signal_services.support_staff_stream_hub.send_to_user = original_send

            self.assertEqual(response.direction, "client")
            self.assertEqual(response.sender_user_id, client.telegram_id)

            signal = (await session.execute(select(PersonalSignal))).scalars().one()
            self.assertEqual(signal.user_id, client.telegram_id)
            self.assertEqual(signal.type, SUPPORT_CLIENT_MESSAGE_TYPE)

            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            self.assertEqual([item.channel for item in outbox_items], [CHANNEL_WEB_PUSH_SIGNAL])
            self.assertEqual(outbox_items[0].user_id, staff.telegram_id)
            self.assertNotIn(CHANNEL_TELEGRAM_MESSAGE, [item.channel for item in outbox_items])
            self.assertNotIn(CHANNEL_VK_MESSAGE, [item.channel for item in outbox_items])
            self.assertEqual(staff_events, [(staff.telegram_id, "support_message", "Нужна помощь по прогнозу.")])

    async def test_staff_message_is_returned_in_client_signal_history(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            client = self._user(101)
            session.add_all([admin, client])
            await session.commit()

            sent = await admin_web_chat.send_admin_web_chat_message(
                client.telegram_id,
                admin_web_chat.SupportMessageCreate(text="Ответ от Shamrai"),
                admin=admin,
                db=session,
            )

            history = await signals.get_signal_history(limit=60, current_user=client, db=session)

            self.assertEqual(len(history), 1)
            self.assertEqual(history[0].id, sent.id)
            self.assertEqual(history[0].type, SUPPORT_STAFF_MESSAGE_TYPE)
            self.assertEqual(history[0].direction, "staff")
            self.assertEqual(history[0].author_label, "Shamrai")

    async def test_thread_list_sorts_by_latest_message_and_marks_needs_reply(self):
        async with self.Session() as session:
            admin = self._user(900, role="admin")
            client_waiting = self._user(101)
            client_answered = self._user(102)
            session.add_all([admin, client_waiting, client_answered])
            await session.flush()

            now = datetime.now(timezone.utc)
            session.add_all([
                PersonalSignal(
                    user_id=client_answered.telegram_id,
                    text="Клиент 102",
                    type=SUPPORT_CLIENT_MESSAGE_TYPE,
                    data={"direction": "client"},
                    created_at=now - timedelta(minutes=5),
                ),
                PersonalSignal(
                    user_id=client_answered.telegram_id,
                    text="Ответ 102",
                    type=SUPPORT_STAFF_MESSAGE_TYPE,
                    data={"direction": "staff", "author_label": "Shamrai"},
                    created_at=now - timedelta(minutes=4),
                ),
                PersonalSignal(
                    user_id=client_waiting.telegram_id,
                    text="Клиент 101 ждет",
                    type=SUPPORT_CLIENT_MESSAGE_TYPE,
                    data={"direction": "client"},
                    created_at=now,
                ),
            ])
            await session.commit()

            response = await admin_web_chat.list_admin_web_chat_threads(admin=admin, db=session)

            self.assertEqual([thread.user.telegram_id for thread in response.items], [101, 102])
            self.assertTrue(response.items[0].needs_reply)
            self.assertFalse(response.items[1].needs_reply)


class SupportWebChatDependencyTests(unittest.TestCase):
    def test_admin_web_chat_routes_use_staff_dependencies(self):
        self.assertIs(
            inspect.signature(admin_web_chat.list_admin_web_chat_threads).parameters["admin"].default.dependency,
            get_current_admin_read,
        )
        self.assertIs(
            inspect.signature(admin_web_chat.get_admin_web_chat_messages).parameters["admin"].default.dependency,
            get_current_admin_read,
        )
        self.assertIs(
            inspect.signature(admin_web_chat.send_admin_web_chat_message).parameters["admin"].default.dependency,
            get_current_admin,
        )
        self.assertIs(
            inspect.signature(admin_web_chat.create_admin_web_chat_stream_ticket).parameters["admin"].default.dependency,
            get_current_admin,
        )


if __name__ == "__main__":
    unittest.main()
