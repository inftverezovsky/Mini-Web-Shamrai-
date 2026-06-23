import unittest
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import admin as admin_api
from src.api import auth
from src.models.database import Base
from src.models.models import (
    AdminAuditLog,
    Bet,
    ChatConversation,
    ChatMessage,
    ChatReadCursor,
    DeliveryOutbox,
    IdentityDeviceLink,
    MatchBalanceLog,
    MessageTemplate,
    PaymentAttempt,
    PersonalSignal,
    PersonalSignalReadCursor,
    Subscription,
    User,
)
from src.services.telegram_auth import confirm_telegram_bot_auth_session, create_telegram_bot_auth_session


class TelegramAuthMergeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def test_vk_oauth_urlopen_bypasses_process_proxy_environment(self):
        request = auth.urllib.request.Request("https://id.vk.ru/oauth2/auth")
        opener = SimpleNamespace(open=Mock(return_value="response"))

        with (
            patch.object(auth.urllib.request, "ProxyHandler", return_value="proxy-handler") as proxy_handler,
            patch.object(auth.urllib.request, "build_opener", return_value=opener) as build_opener,
        ):
            result = auth._vk_urlopen(request, timeout=7)

        self.assertEqual(result, "response")
        proxy_handler.assert_called_once_with({})
        build_opener.assert_called_once_with("proxy-handler")
        opener.open.assert_called_once_with(request, timeout=7)

    def test_vk_oauth_rate_limit_error_returns_client_safe_message(self):
        status_code, message = auth._vk_oauth_client_error(
            auth.VkOAuthError("Too many attempts. Try later. [9]")
        )

        self.assertEqual(status_code, 429)
        self.assertIn("Слишком много попыток", message)
        self.assertNotIn("Too many attempts", message)

    def test_vk_oauth_unknown_error_hides_internal_detail(self):
        status_code, message = auth._vk_oauth_client_error(
            auth.VkOAuthError("HTTP 500: upstream stack detail")
        )

        self.assertEqual(status_code, 502)
        self.assertIn("VK ID временно", message)
        self.assertNotIn("upstream stack detail", message)

    async def test_vk_login_creates_web_only_profile_without_debug_bypass(self):
        async with self.Session() as db:
            with patch.object(
                auth,
                "_exchange_vk_or_502",
                new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
            ):
                response = await auth.vk_id_login(
                    auth.VkOAuthCodeRequest(
                        code="code",
                        device_id="device",
                        code_verifier="verifier",
                        state="state",
                    ),
                    response=Response(),
                    db=db,
                )

            self.assertLess(response.user.telegram_id, 0)
            self.assertTrue(response.user.is_web_only)
            self.assertEqual(response.user.vk_user_id, "741852963")

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_telegram_bot_login_merges_current_vk_only_profile(self):
        async with self.Session() as db:
            web_user = await auth._create_vk_only_user(db, "741852963", "VK Client")
            web_user.purchased_bets_balance = 6
            web_user.matches_remaining = 6
            web_user.is_onboarded = True
            web_user.experience_level = "amateur"
            web_user.bankroll_size = "mid"
            web_user.risk_tolerance = "balanced"
            web_user.currency_preference = "RUB"
            await db.commit()

            web_user = await auth._load_user_with_profile(db, web_user.telegram_id)
            user = await auth._upsert_telegram_user_with_optional_web_profile(
                db,
                {
                    "id": 123456789,
                    "first_name": "Telegram",
                    "username": "tg_user",
                },
                web_user,
            )
            await db.commit()

            self.assertEqual(user.telegram_id, 123456789)
            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            merged_user = users[0]
            self.assertEqual(merged_user.telegram_id, 123456789)
            self.assertEqual(merged_user.vk_user_id, "741852963")
            self.assertEqual(merged_user.purchased_bets_balance, 6)
            self.assertEqual(merged_user.matches_remaining, 6)
            self.assertTrue(merged_user.is_onboarded)
            self.assertEqual(merged_user.username, "tg_user")

    async def test_bot_session_poll_links_telegram_to_current_vk_only_profile(self):
        async with self.Session() as db:
            web_user = await auth._create_vk_only_user(db, "741852963", "VK Client")
            web_user.matches_remaining = 4
            web_user.purchased_bets_balance = 4
            await db.commit()
            web_user = await auth._load_user_with_profile(db, web_user.telegram_id)

            session = await create_telegram_bot_auth_session()
            confirmed = await confirm_telegram_bot_auth_session(
                session.auth_token,
                {
                    "id": 123456789,
                    "first_name": "Telegram",
                    "username": "tg_user",
                },
            )
            self.assertTrue(confirmed)

            response = await auth.poll_telegram_bot_auth_session(
                session.auth_token,
                response=Response(),
                current_user=web_user,
                db=db,
            )

            self.assertEqual(response.status, "confirmed")
            self.assertIsNotNone(response.access_token)
            self.assertIsNotNone(response.user)
            self.assertEqual(response.user.telegram_id, 123456789)
            self.assertFalse(response.user.is_web_only)
            self.assertEqual(response.user.vk_user_id, "741852963")
            self.assertEqual(response.user.matches_remaining, 4)

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].telegram_id, 123456789)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_bot_session_poll_links_source_vk_profile_without_current_cookie(self):
        async with self.Session() as db:
            web_user = await auth._create_vk_only_user(db, "741852963", "VK Client")
            web_user.matches_remaining = 5
            web_user.purchased_bets_balance = 5
            await db.commit()
            web_user = await auth._load_user_with_profile(db, web_user.telegram_id)

            session = await create_telegram_bot_auth_session(source_user_id=web_user.telegram_id)
            confirmed = await confirm_telegram_bot_auth_session(
                session.auth_token,
                {
                    "id": 123456789,
                    "first_name": "Telegram",
                    "username": "tg_user",
                },
            )
            self.assertTrue(confirmed)

            response = await auth.poll_telegram_bot_auth_session(
                session.auth_token,
                response=Response(),
                current_user=None,
                db=db,
            )

            self.assertEqual(response.status, "confirmed")
            self.assertIsNotNone(response.user)
            self.assertEqual(response.user.telegram_id, 123456789)
            self.assertFalse(response.user.is_web_only)
            self.assertEqual(response.user.vk_user_id, "741852963")
            self.assertEqual(response.user.matches_remaining, 5)

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].telegram_id, 123456789)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_vk_first_then_telegram_same_device_merges_into_one_user(self):
        device_id = "550e8400-e29b-41d4-a716-446655440000"
        async with self.Session() as db:
            with patch.object(
                auth,
                "_exchange_vk_or_502",
                new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
            ):
                vk_response = await auth.vk_id_login(
                    auth.VkOAuthCodeRequest(
                        code="code",
                        device_id="device",
                        code_verifier="verifier",
                        state="state",
                    ),
                    response=Response(),
                    identity_device_id=device_id,
                    current_user=None,
                    db=db,
                )

            self.assertLess(vk_response.user.telegram_id, 0)
            self.assertEqual(vk_response.user.identity_providers, ["vk"])
            self.assertEqual(vk_response.user.missing_identity_providers, ["telegram"])

            with patch.object(
                auth,
                "verify_telegram_init_data",
                return_value={"id": 223456789, "first_name": "Telegram", "username": "tg_user"},
            ):
                tg_response = await auth.login_user(
                    auth.LoginRequest(initData="signed"),
                    response=Response(),
                    identity_device_id=device_id,
                    current_user=None,
                    db=db,
                )

            self.assertEqual(tg_response.user.telegram_id, 223456789)
            self.assertEqual(tg_response.user.vk_user_id, "741852963")
            self.assertTrue(tg_response.user.identity_complete)
            self.assertEqual(tg_response.user.identity_providers, ["telegram", "vk"])

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].telegram_id, 223456789)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_telegram_first_then_vk_same_device_attaches_vk_to_telegram_user(self):
        device_id = "550e8400-e29b-41d4-a716-446655440001"
        async with self.Session() as db:
            with patch.object(
                auth,
                "verify_telegram_init_data",
                return_value={"id": 323456789, "first_name": "Telegram", "username": "tg_user"},
            ):
                tg_response = await auth.login_user(
                    auth.LoginRequest(initData="signed"),
                    response=Response(),
                    identity_device_id=device_id,
                    current_user=None,
                    db=db,
                )

            self.assertEqual(tg_response.user.telegram_id, 323456789)
            self.assertFalse(tg_response.user.identity_complete)
            self.assertEqual(tg_response.user.missing_identity_providers, ["vk"])

            with patch.object(
                auth,
                "_exchange_vk_or_502",
                new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
            ):
                vk_response = await auth.vk_id_login(
                    auth.VkOAuthCodeRequest(
                        code="code",
                        device_id="device",
                        code_verifier="verifier",
                        state="state",
                    ),
                    response=Response(),
                    identity_device_id=device_id,
                    current_user=None,
                    db=db,
                )

            self.assertEqual(vk_response.user.telegram_id, 323456789)
            self.assertEqual(vk_response.user.vk_user_id, "741852963")
            self.assertTrue(vk_response.user.identity_complete)

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].telegram_id, 323456789)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_device_binding_does_not_auto_merge_different_real_telegram_profiles(self):
        device_id = "550e8400-e29b-41d4-a716-446655440002"
        async with self.Session() as db:
            first_user = User(
                telegram_id=111111111,
                first_name="First",
                role="user",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            db.add(first_user)
            await db.flush()
            await auth._bind_identity_device(db, device_id, first_user)
            await db.commit()

            with patch.object(
                auth,
                "verify_telegram_init_data",
                return_value={"id": 222222222, "first_name": "Second"},
            ):
                response = await auth.login_user(
                    auth.LoginRequest(initData="signed"),
                    response=Response(),
                    identity_device_id=device_id,
                    current_user=None,
                    db=db,
                )

            self.assertEqual(response.user.telegram_id, 222222222)
            result = await db.execute(select(User).order_by(User.telegram_id))
            users = result.scalars().all()
            self.assertEqual([user.telegram_id for user in users], [111111111, 222222222])

    async def test_vk_login_conflicts_when_vk_belongs_to_another_real_telegram(self):
        device_id = "550e8400-e29b-41d4-a716-446655440003"
        async with self.Session() as db:
            existing_vk_owner = User(
                telegram_id=111111111,
                first_name="VK Owner",
                role="user",
                stats_display_mode="percent",
                vk_user_id="741852963",
                tg_chat_joined=False,
            )
            device_user = User(
                telegram_id=222222222,
                first_name="Device User",
                role="user",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            db.add_all([existing_vk_owner, device_user])
            await db.flush()
            await auth._bind_identity_device(db, device_id, device_user)
            await db.commit()

            with patch.object(
                auth,
                "_exchange_vk_or_502",
                new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
            ):
                with self.assertRaises(HTTPException) as exc:
                    await auth.vk_id_login(
                        auth.VkOAuthCodeRequest(
                            code="code",
                            device_id="device",
                            code_verifier="verifier",
                            state="state",
                        ),
                        response=Response(),
                        identity_device_id=device_id,
                        current_user=None,
                        db=db,
                    )

            self.assertEqual(exc.exception.status_code, 409)

    async def test_admin_merge_moves_extended_user_related_records_and_deletes_source(self):
        async with self.Session() as db:
            admin_user = User(
                telegram_id=999999999,
                first_name="Admin",
                role="admin",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            source = await auth._create_vk_only_user(db, "741852963", "VK Client")
            target = User(
                telegram_id=123456789,
                first_name="Telegram",
                role="user",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            bet = Bet(
                id=uuid.uuid4(),
                event_name="Test match",
                coefficient=Decimal("1.90"),
                author_id=source.telegram_id,
            )
            db.add_all([admin_user, target, bet])
            await db.flush()

            subscription = Subscription(user_id=source.telegram_id, status="active")
            payment = PaymentAttempt(
                user_id=source.telegram_id,
                provider="debug",
                status="pending",
                amount=Decimal("10.00"),
            )
            balance_log = MatchBalanceLog(user_id=source.telegram_id, event_type="test", delta_matches=3)
            signal = PersonalSignal(user_id=source.telegram_id, text="signal")
            conversation = ChatConversation(
                id=uuid.uuid4(),
                owner_user_id=source.telegram_id,
                kind="support",
                status="open",
            )
            message = ChatMessage(
                conversation_id=conversation.id,
                sender_user_id=source.telegram_id,
                sender_role="client",
                text="hello",
                client_message_id=uuid.uuid4(),
            )
            read_cursor = ChatReadCursor(
                conversation_id=conversation.id,
                user_id=source.telegram_id,
            )
            outbox = DeliveryOutbox(
                id=uuid.uuid4(),
                channel="vk",
                user_id=source.telegram_id,
                payload={},
            )
            audit_log = AdminAuditLog(
                actor_id=source.telegram_id,
                target_user_id=source.telegram_id,
                action="legacy",
                details={},
            )
            template = MessageTemplate(
                key="identity_merge_test",
                title="Test",
                body="Body",
                variables=[],
                updated_by=source.telegram_id,
            )
            device_link = IdentityDeviceLink(
                device_key_hash="a" * 64,
                source_user_id=source.telegram_id,
            )
            db.add_all([
                subscription,
                payment,
                balance_log,
                signal,
                conversation,
                message,
                read_cursor,
                outbox,
                audit_log,
                template,
                device_link,
            ])
            await db.flush()
            signal_cursor = PersonalSignalReadCursor(
                user_id=source.telegram_id,
                last_read_signal_id=signal.id,
            )
            db.add(signal_cursor)
            await db.commit()

            response = await admin_api.admin_merge_user(
                source.telegram_id,
                admin_api.AdminUserMergeRequest(target_user_id=target.telegram_id, reason="duplicate"),
                admin=admin_user,
                db=db,
            )

            self.assertEqual(response.telegram_id, target.telegram_id)
            self.assertEqual(response.vk_user_id, "741852963")
            self.assertIsNone(await db.get(User, source.telegram_id))
            self.assertEqual((await db.get(Subscription, subscription.id)).user_id, target.telegram_id)
            self.assertEqual((await db.get(PaymentAttempt, payment.id)).user_id, target.telegram_id)
            self.assertEqual((await db.get(MatchBalanceLog, balance_log.id)).user_id, target.telegram_id)
            self.assertEqual((await db.get(PersonalSignal, signal.id)).user_id, target.telegram_id)
            self.assertEqual((await db.get(ChatConversation, conversation.id)).owner_user_id, target.telegram_id)
            self.assertEqual((await db.get(ChatMessage, message.id)).sender_user_id, target.telegram_id)
            self.assertIsNotNone(await db.get(ChatReadCursor, {"conversation_id": conversation.id, "user_id": target.telegram_id}))
            self.assertEqual((await db.get(PersonalSignalReadCursor, target.telegram_id)).last_read_signal_id, signal.id)
            self.assertEqual((await db.get(DeliveryOutbox, outbox.id)).user_id, target.telegram_id)
            self.assertEqual((await db.get(AdminAuditLog, audit_log.id)).actor_id, target.telegram_id)
            self.assertEqual((await db.get(AdminAuditLog, audit_log.id)).target_user_id, target.telegram_id)
            self.assertEqual((await db.get(MessageTemplate, template.key)).updated_by, target.telegram_id)
            self.assertEqual((await db.get(IdentityDeviceLink, device_link.device_key_hash)).source_user_id, target.telegram_id)

    async def test_promotes_web_only_phone_profile_without_losing_balance(self):
        async with self.Session() as db:
            web_user = User(
                telegram_id=-1001,
                phone="+79990001122",
                first_name="Web",
                role="user",
                stats_display_mode="percent",
                purchased_bets_balance=7,
                matches_remaining=7,
                is_onboarded=True,
                experience_level="amateur",
                bankroll_size="mid",
                risk_tolerance="balanced",
                currency_preference="RUB",
                tg_chat_joined=False,
            )
            db.add(web_user)
            await db.commit()

            user = await auth._upsert_telegram_user(
                db,
                {
                    "id": 123456789,
                    "phone_number": "+7 (999) 000-11-22",
                    "first_name": "Telegram",
                    "username": "tg_user",
                },
            )
            await db.commit()

            self.assertEqual(user.telegram_id, 123456789)
            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            merged_user = users[0]
            self.assertEqual(merged_user.telegram_id, 123456789)
            self.assertEqual(merged_user.phone, "+79990001122")
            self.assertEqual(merged_user.purchased_bets_balance, 7)
            self.assertEqual(merged_user.matches_remaining, 7)
            self.assertTrue(merged_user.is_onboarded)
            self.assertEqual(merged_user.username, "tg_user")

    async def test_rejects_phone_bound_to_another_real_telegram_profile(self):
        async with self.Session() as db:
            db.add(
                User(
                    telegram_id=987654321,
                    phone="+79990001122",
                    first_name="Existing",
                    role="user",
                    stats_display_mode="percent",
                    tg_chat_joined=False,
                )
            )
            await db.commit()

            with self.assertRaises(HTTPException) as exc:
                await auth._upsert_telegram_user(
                    db,
                    {
                        "id": 123456789,
                        "phone_number": "+7 (999) 000-11-22",
                        "first_name": "Telegram",
                    },
                )

            self.assertEqual(exc.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
