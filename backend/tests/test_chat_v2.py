import base64
import inspect
import io
import os
import stat
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import chat
from src.api.deps import get_current_admin, get_current_admin_read
from src.models.database import Base
from src.models.models import ChatMessage, ChatReadCursor, DeliveryOutbox, PersonalSignal, PersonalSignalReadCursor, User
from src.services.signals import SUPPORT_STAFF_MESSAGE_TYPE


PNG_1X1_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4//8/AAX+Av4N70a4AAAAAElFTkSuQmCC"
)
WEBM_BYTES = b"\x1a\x45\xdf\xa3" + (b"\x00" * 128)


def _upload(filename: str, data: bytes, content_type: str) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(data), headers={"content-type": content_type})


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

    async def test_staff_reply_closes_shared_conversation_and_client_reply_reopens_same_history(self):
        async with self.Session() as session:
            client = self._user(101)
            admin = self._user(901, role="admin")
            moderator = self._user(902, role="moderator")
            session.add_all([client, admin, moderator])
            await session.commit()

            client_message = await self._send_client_message(session, client, "Нужна помощь")
            open_before = await chat.list_admin_chat_conversations(status="open", admin=admin, db=session)
            self.assertEqual([item.id for item in open_before.items], [client_message.conversation_id])

            staff_message = await chat.create_admin_chat_message(
                client_message.conversation_id,
                chat.ChatMessageCreate(client_message_id=uuid4(), text="Разобрали вопрос"),
                admin=admin,
                db=session,
            )
            self.assertEqual(staff_message.direction, "staff")

            open_after_reply = await chat.list_admin_chat_conversations(status="open", admin=admin, db=session)
            self.assertEqual(open_after_reply.items, [])

            closed_for_moderator = await chat.list_admin_chat_conversations(status="closed", admin=moderator, db=session)
            self.assertEqual(len(closed_for_moderator.items), 1)
            self.assertEqual(closed_for_moderator.items[0].id, client_message.conversation_id)
            self.assertEqual(closed_for_moderator.items[0].status, "closed")
            self.assertEqual(closed_for_moderator.items[0].last_message_text, "Разобрали вопрос")

            shared_history = await chat.get_admin_chat_messages(
                client_message.conversation_id,
                admin=moderator,
                db=session,
            )
            self.assertEqual(
                [message.text for message in shared_history.items],
                ["Нужна помощь", "Разобрали вопрос"],
            )

            follow_up = await self._send_client_message(session, client, "Есть уточнение")
            self.assertEqual(follow_up.conversation_id, client_message.conversation_id)

            reopened = await chat.list_admin_chat_conversations(status="open", admin=moderator, db=session)
            self.assertEqual([item.id for item in reopened.items], [client_message.conversation_id])
            self.assertEqual(reopened.items[0].status, "open")

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

    async def test_client_image_and_admin_voice_are_shared_and_update_status(self):
        async with self.Session() as session:
            with tempfile.TemporaryDirectory() as temp_dir, patch.object(chat, "CHAT_STATIC_DIR", temp_dir):
                client = self._user(101)
                admin = self._user(901, role="admin")
                moderator = self._user(902, role="moderator")
                session.add_all([client, admin, moderator])
                await session.commit()

                image_message = await chat.create_support_attachment(
                    client_message_id=uuid4(),
                    message_type="image",
                    text="Вот скрин",
                    reply_to_id=None,
                    duration_ms=None,
                    file=_upload("screen.png", PNG_1X1_BYTES, "image/png"),
                    current_user=client,
                    db=session,
                )
                self.assertEqual(image_message.type, "image")
                self.assertEqual(image_message.text, "Вот скрин")
                self.assertEqual(image_message.payload["mime_type"], "image/png")
                self.assertEqual(image_message.payload["width"], 1)
                self.assertEqual(image_message.payload["height"], 1)
                image_path = os.path.join(temp_dir, *image_message.payload["url"].split("/static/chat/", 1)[1].split("/"))
                self.assertTrue(os.path.exists(image_path))
                if os.name != "nt":
                    self.assertEqual(stat.S_IMODE(os.stat(image_path).st_mode), 0o644)

                open_before = await chat.list_admin_chat_conversations(status="open", admin=admin, db=session)
                self.assertEqual([item.id for item in open_before.items], [image_message.conversation_id])
                self.assertEqual(open_before.items[0].last_message_text, "Вот скрин")

                voice_message = await chat.create_admin_chat_attachment(
                    image_message.conversation_id,
                    client_message_id=uuid4(),
                    message_type="voice",
                    text=None,
                    reply_to_id=None,
                    duration_ms=1500,
                    file=_upload("voice.webm", WEBM_BYTES, "audio/webm"),
                    admin=admin,
                    db=session,
                )
                self.assertEqual(voice_message.type, "voice")
                self.assertIsNone(voice_message.text)
                self.assertEqual(voice_message.payload["mime_type"], "audio/webm")
                self.assertEqual(voice_message.payload["duration_ms"], 1500)

            open_after = await chat.list_admin_chat_conversations(status="open", admin=moderator, db=session)
            self.assertEqual(open_after.items, [])

            closed = await chat.list_admin_chat_conversations(status="closed", admin=moderator, db=session)
            self.assertEqual(len(closed.items), 1)
            self.assertEqual(closed.items[0].id, image_message.conversation_id)
            self.assertEqual(closed.items[0].status, "closed")
            self.assertEqual(closed.items[0].last_message_text, "Голосовое сообщение")

            shared_history = await chat.get_admin_chat_messages(
                image_message.conversation_id,
                admin=moderator,
                db=session,
            )
            self.assertEqual([message.type for message in shared_history.items], ["image", "voice"])

            client_history = await chat.get_support_messages(current_user=client, db=session)
            self.assertEqual([message.type for message in client_history.items], ["image", "voice"])

    async def test_attachment_rejects_invalid_file_and_duplicate_upload_is_cleaned_up(self):
        async with self.Session() as session:
            with tempfile.TemporaryDirectory() as temp_dir, patch.object(chat, "CHAT_STATIC_DIR", temp_dir):
                client = self._user(101)
                session.add(client)
                await session.commit()

                with self.assertRaises(HTTPException) as invalid_raised:
                    await chat.create_support_attachment(
                        client_message_id=uuid4(),
                        message_type="image",
                        text=None,
                        reply_to_id=None,
                        duration_ms=None,
                        file=_upload("bad.png", b"not an image", "image/png"),
                        current_user=client,
                        db=session,
                    )
                self.assertEqual(invalid_raised.exception.status_code, 400)

                client_message_id = uuid4()
                first = await chat.create_support_attachment(
                    client_message_id=client_message_id,
                    message_type="image",
                    text=None,
                    reply_to_id=None,
                    duration_ms=None,
                    file=_upload("first.png", PNG_1X1_BYTES, "image/png"),
                    current_user=client,
                    db=session,
                )
                second = await chat.create_support_attachment(
                    client_message_id=client_message_id,
                    message_type="image",
                    text=None,
                    reply_to_id=None,
                    duration_ms=None,
                    file=_upload("second.png", PNG_1X1_BYTES, "image/png"),
                    current_user=client,
                    db=session,
                )
                stored_files = [
                    name
                    for _, _, names in os.walk(temp_dir)
                    for name in names
                ]
                self.assertEqual(len(stored_files), 1)

            self.assertEqual(first.id, second.id)
            stored_messages = (await session.execute(select(ChatMessage))).scalars().all()
            self.assertEqual(len(stored_messages), 1)

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

    async def test_empty_long_and_client_reopen_rules(self):
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

            reopened_message = await self._send_client_message(session, client, "После закрытия")
            self.assertEqual(reopened_message.conversation_id, message.conversation_id)

            reopened = await chat.list_admin_chat_conversations(status="open", admin=staff, db=session)
            self.assertEqual([item.id for item in reopened.items], [message.conversation_id])

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
        self.assertIs(
            inspect.signature(chat.create_admin_chat_attachment).parameters["admin"].default.dependency,
            get_current_admin,
        )


if __name__ == "__main__":
    unittest.main()
