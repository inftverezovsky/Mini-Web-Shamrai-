import inspect
import unittest
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import chat
from src.api.deps import get_current_admin, get_current_admin_read
from src.models.database import Base
from src.models.models import ChatMessage, ChatReadCursor, DeliveryOutbox, PersonalSignal, PersonalSignalReadCursor, User
from src.services.signals import SUPPORT_STAFF_MESSAGE_TYPE


class ChatV2Tests(unittest.IsolatedAsyncioTestCase):
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

    async def _send_client_message(self, session, user: User, text: str = "Здравствуйте"):
        return await chat.create_support_message(
            chat.ChatMessageCreate(client_message_id=uuid4(), text=text),
            current_user=user,
            db=session,
        )

    async def test_user_gets_only_own_support_conversation_and_cannot_read_another_uuid(self):
        async with self.Session() as session:
            user_a = self._user(101)
            user_b = self._user(102)
            session.add_all([user_a, user_b])
            await session.commit()

            sent = await self._send_client_message(session, user_a)
            own_messages = await chat.get_support_messages(current_user=user_a, db=session)
            self.assertEqual([message.id for message in own_messages.items], [sent.id])

            with self.assertRaises(HTTPException) as raised:
                await chat.get_admin_chat_messages(sent.conversation_id, admin=user_b, db=session)
            self.assertEqual(raised.exception.status_code, 403)

            other_messages = await chat.get_support_messages(current_user=user_b, db=session)
            self.assertEqual(other_messages.items, [])

    async def test_staff_lists_replies_and_user_cannot_call_staff_endpoints(self):
        async with self.Session() as session:
            client = self._user(101)
            staff = self._user(901, role="moderator")
            session.add_all([client, staff])
            await session.commit()
            client_message = await self._send_client_message(session, client, "Нужна помощь")

            inbox = await chat.list_admin_chat_conversations(admin=staff, db=session)
            self.assertEqual(len(inbox.items), 1)
            self.assertEqual(inbox.items[0].id, client_message.conversation_id)
            self.assertEqual(inbox.items[0].unread_count, 1)

            staff_message = await chat.create_admin_chat_message(
                client_message.conversation_id,
                chat.ChatMessageCreate(client_message_id=uuid4(), text="Отвечает Shamrai"),
                admin=staff,
                db=session,
            )
            self.assertEqual(staff_message.direction, "staff")

            history = await chat.get_support_messages(current_user=client, db=session)
            self.assertEqual([message.text for message in history.items], ["Нужна помощь", "Отвечает Shamrai"])

            with self.assertRaises(HTTPException) as raised:
                await chat.list_admin_chat_conversations(admin=client, db=session)
            self.assertEqual(raised.exception.status_code, 403)

    async def test_client_message_id_is_idempotent(self):
        async with self.Session() as session:
            client = self._user(101)
            session.add(client)
            await session.commit()
            client_message_id = uuid4()
            payload = chat.ChatMessageCreate(client_message_id=client_message_id, text="Один раз")

            first = await chat.create_support_message(payload, current_user=client, db=session)
            second = await chat.create_support_message(payload, current_user=client, db=session)

            self.assertEqual(first.id, second.id)
            count = (await session.execute(select(ChatMessage))).scalars().all()
            self.assertEqual(len(count), 1)

    async def test_pagination_does_not_skip_or_duplicate_messages(self):
        async with self.Session() as session:
            client = self._user(101)
            session.add(client)
            await session.commit()
            created = [
                await self._send_client_message(session, client, f"Сообщение {index}")
                for index in range(5)
            ]

            first_page = await chat.get_support_messages(limit=3, current_user=client, db=session)
            self.assertEqual([message.id for message in first_page.items], [created[2].id, created[3].id, created[4].id])
            self.assertTrue(first_page.has_more)

            second_page = await chat.get_support_messages(
                before_id=first_page.next_before_id,
                limit=3,
                current_user=client,
                db=session,
            )
            self.assertEqual([message.id for message in second_page.items], [created[0].id, created[1].id])
            self.assertFalse(second_page.has_more)

    async def test_empty_long_and_closed_conversation_rules(self):
        async with self.Session() as session:
            client = self._user(101)
            staff = self._user(901, role="admin")
            session.add_all([client, staff])
            await session.commit()

            with self.assertRaises(HTTPException) as empty_raised:
                await chat.create_support_message(
                    chat.ChatMessageCreate(client_message_id=uuid4(), text="   "),
                    current_user=client,
                    db=session,
                )
            self.assertEqual(empty_raised.exception.status_code, 400)

            with self.assertRaises(HTTPException) as long_raised:
                await chat.create_support_message(
                    chat.ChatMessageCreate(client_message_id=uuid4(), text="x" * 4001),
                    current_user=client,
                    db=session,
                )
            self.assertEqual(long_raised.exception.status_code, 400)

            message = await self._send_client_message(session, client)
            await chat.update_admin_chat_status(
                message.conversation_id,
                chat.ChatStatusUpdate(status="closed"),
                admin=staff,
                db=session,
            )

            with self.assertRaises(HTTPException) as closed_raised:
                await self._send_client_message(session, client, "После закрытия")
            self.assertEqual(closed_raised.exception.status_code, 409)

    async def test_read_cursor_is_monotonic(self):
        async with self.Session() as session:
            client = self._user(101)
            session.add(client)
            await session.commit()
            first = await self._send_client_message(session, client, "Первое")
            second = await self._send_client_message(session, client, "Второе")

            await chat.mark_support_read(
                chat.ChatReadRequest(last_read_message_id=second.id),
                current_user=client,
                db=session,
            )
            await chat.mark_support_read(
                chat.ChatReadRequest(last_read_message_id=first.id),
                current_user=client,
                db=session,
            )

            cursor = (await session.execute(select(ChatReadCursor))).scalars().one()
            self.assertEqual(cursor.last_read_message_id, second.id)

    async def test_signal_read_cursor_requires_existing_owned_non_support_signal(self):
        async with self.Session() as session:
            client = self._user(101)
            other = self._user(102)
            session.add_all([client, other])
            await session.flush()
            own_signal = PersonalSignal(user_id=client.telegram_id, text="Сигнал", type="system", data={})
            support_signal = PersonalSignal(
                user_id=client.telegram_id,
                text="Legacy support",
                type=SUPPORT_STAFF_MESSAGE_TYPE,
                data={},
            )
            other_signal = PersonalSignal(user_id=other.telegram_id, text="Чужой сигнал", type="system", data={})
            session.add_all([own_signal, support_signal, other_signal])
            await session.flush()

            with self.assertRaises(HTTPException) as missing_raised:
                await chat.mark_signal_conversation_read(
                    chat.SignalReadRequest(last_read_signal_id=999_999),
                    current_user=client,
                    db=session,
                )
            self.assertEqual(missing_raised.exception.status_code, 404)

            with self.assertRaises(HTTPException) as support_raised:
                await chat.mark_signal_conversation_read(
                    chat.SignalReadRequest(last_read_signal_id=support_signal.id),
                    current_user=client,
                    db=session,
                )
            self.assertEqual(support_raised.exception.status_code, 404)

            with self.assertRaises(HTTPException) as other_raised:
                await chat.mark_signal_conversation_read(
                    chat.SignalReadRequest(last_read_signal_id=other_signal.id),
                    current_user=client,
                    db=session,
                )
            self.assertEqual(other_raised.exception.status_code, 404)

            response = await chat.mark_signal_conversation_read(
                chat.SignalReadRequest(last_read_signal_id=own_signal.id),
                current_user=client,
                db=session,
            )
            self.assertEqual(response.last_read_signal_id, own_signal.id)
            cursor = (await session.execute(select(PersonalSignalReadCursor))).scalars().one()
            self.assertEqual(cursor.last_read_signal_id, own_signal.id)

    async def test_stream_ticket_is_single_use_and_messages_persist_without_socket(self):
        async with self.Session() as session:
            client = self._user(101)
            session.add(client)
            await session.commit()

            ticket_response = await chat.create_chat_stream_ticket(current_user=client)
            self.assertIsNotNone(chat._consume_chat_stream_ticket(ticket_response.ticket))
            self.assertIsNone(chat._consume_chat_stream_ticket(ticket_response.ticket))

            sent = await self._send_client_message(session, client)
            stored = (await session.execute(select(ChatMessage))).scalars().one()
            self.assertEqual(stored.id, sent.id)
            self.assertEqual((await session.execute(select(DeliveryOutbox))).scalars().all(), [])


class ChatV2DependencyTests(unittest.TestCase):
    def test_admin_routes_use_staff_dependencies(self):
        self.assertIs(
            inspect.signature(chat.list_admin_chat_conversations).parameters["admin"].default.dependency,
            get_current_admin_read,
        )
        self.assertIs(
            inspect.signature(chat.create_admin_chat_message).parameters["admin"].default.dependency,
            get_current_admin,
        )


if __name__ == "__main__":
    unittest.main()
