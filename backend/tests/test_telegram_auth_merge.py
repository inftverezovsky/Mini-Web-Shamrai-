import unittest
import urllib.parse
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
    FlatSubscription,
    IdentityDeviceLink,
    MarketingRewardEvent,
    MatchBalanceLog,
    MessageTemplate,
    PaymentAttempt,
    PersonalSignal,
    PersonalSignalReadCursor,
    ReferralRewardEvent,
    Subscription,
    User,
    user_bets,
)
from src.services import telegram_auth
from src.services import vk_auth_flow
from src.services.telegram_auth import confirm_telegram_bot_auth_session, create_telegram_bot_auth_session


class TelegramAuthMergeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        telegram_auth._sessions.clear()
        vk_auth_flow._flows.clear()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        telegram_auth._sessions.clear()
        vk_auth_flow._flows.clear()
        await self.engine.dispose()

    async def test_identity_merge_combines_open_flat_targets_and_preserves_stake_snapshots(self):
        async with self.Session() as db:
            source = User(telegram_id=-501, matches_remaining=0, purchased_bets_balance=0)
            target = User(telegram_id=501, matches_remaining=0, purchased_bets_balance=0)
            bet = Bet(
                id=uuid.uuid4(),
                event_name="А — Б",
                outcome="П1",
                coefficient=Decimal("1.70"),
                status="pending",
            )
            source_subscription = FlatSubscription(
                user=source,
                status="active",
                flat_amount_rub=Decimal("5000.00"),
                target_flats=Decimal("2.00"),
            )
            target_subscription = FlatSubscription(
                user=target,
                status="active",
                flat_amount_rub=Decimal("10000.00"),
                target_flats=Decimal("1.00"),
            )
            db.add_all([source, target, bet, source_subscription, target_subscription])
            await db.flush()
            await db.execute(
                user_bets.insert().values(
                    user_id=source.telegram_id,
                    bet_id=bet.id,
                    access_type="flat_subscription",
                    match_charged=False,
                    flat_subscription_id=source_subscription.id,
                    stake_rub=Decimal("2500.00"),
                    flat_amount_rub_snapshot=Decimal("5000.00"),
                    stake_flats=Decimal("0.500000"),
                    coefficient_snapshot=Decimal("1.700"),
                )
            )

            await auth._merge_flat_subscriptions(db, source.telegram_id, target.telegram_id)
            await auth._merge_user_bets(db, source.telegram_id, target.telegram_id)
            await db.flush()

            subscriptions = list((await db.execute(select(FlatSubscription))).scalars().all())
            stake = (await db.execute(select(user_bets))).mappings().one()

            self.assertEqual(len(subscriptions), 1)
            self.assertEqual(subscriptions[0].user_id, target.telegram_id)
            self.assertEqual(subscriptions[0].target_flats, Decimal("3.00"))
            self.assertEqual(stake["user_id"], target.telegram_id)
            self.assertEqual(stake["flat_subscription_id"], subscriptions[0].id)
            self.assertEqual(stake["stake_rub"], Decimal("2500.00"))
            self.assertEqual(stake["coefficient_snapshot"], Decimal("1.700"))

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

    def test_vk_photo_url_is_extracted_from_user_info(self):
        self.assertEqual(
            auth._extract_vk_photo_url({"avatar": {"url": "https://vk.example/avatar.jpg"}}),
            "https://vk.example/avatar.jpg",
        )
        self.assertEqual(
            auth._extract_vk_photo_url({"photo_200": "https://vk.example/photo-200.jpg"}),
            "https://vk.example/photo-200.jpg",
        )
        self.assertIsNone(auth._extract_vk_photo_url({"avatar": {"url": "javascript:alert(1)"}}))

    async def test_vk_start_sets_http_only_flow_cookie_and_authorize_url(self):
        response = Response()

        with (
            patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
            patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
        ):
            result = await auth.vk_id_start(
                auth.VkAuthStartRequest(action="login"),
                response=response,
            )

        parsed_url = urllib.parse.urlparse(result.authorize_url)
        query = urllib.parse.parse_qs(parsed_url.query)
        self.assertEqual(parsed_url.scheme, "https")
        self.assertEqual(parsed_url.netloc, "id.vk.ru")
        self.assertEqual(parsed_url.path, "/authorize")
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["client_id"], ["54626979"])
        self.assertEqual(query["redirect_uri"], ["https://shamra1.pro"])
        self.assertEqual(query["scope"], ["vkid.personal_info"])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scheme"], ["dark"])
        self.assertIn("code_challenge", query)
        self.assertEqual(query["state"], [result.state])
        self.assertNotIn("prompt", query)
        self.assertNotIn("code_verifier", query)

        set_cookie = response.headers.get("set-cookie", "")
        self.assertIn(auth.VK_FLOW_COOKIE_NAME, set_cookie)
        self.assertIn("HttpOnly", set_cookie)
        self.assertIn("SameSite=none", set_cookie)
        self.assertIn("Secure", set_cookie)

    async def test_vk_complete_rejects_missing_flow_cookie(self):
        async with self.Session() as db:
            with self.assertRaises(HTTPException) as exc:
                await auth.vk_id_complete(
                    auth.VkAuthCompleteRequest(
                        code="code",
                        device_id="device",
                        state="state",
                    ),
                    request=SimpleNamespace(cookies={}),
                    response=Response(),
                    db=db,
                )

            self.assertEqual(exc.exception.status_code, 400)
            self.assertIn("Сессия VK ID", exc.exception.detail)

    async def test_vk_complete_login_creates_web_only_profile_and_clears_flow_cookie(self):
        start_response = Response()
        with (
            patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
            patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
        ):
            start = await auth.vk_id_start(
                auth.VkAuthStartRequest(action="login"),
                response=start_response,
            )
        cookie_value = start_response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

        async with self.Session() as db:
            complete_response = Response()
            with (
                patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
                patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
                patch.object(
                    auth,
                    "_exchange_vk_or_502",
                    new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
                ),
            ):
                result = await auth.vk_id_complete(
                    auth.VkAuthCompleteRequest(
                        code="code",
                        device_id="device",
                        state=start.state,
                    ),
                    request=SimpleNamespace(cookies={auth.VK_FLOW_COOKIE_NAME: cookie_value}),
                    response=complete_response,
                    db=db,
                )

            self.assertLess(result.user.telegram_id, 0)
            self.assertTrue(result.user.is_web_only)
            self.assertEqual(result.user.vk_user_id, "741852963")
            set_cookie = "\n".join(
                value.decode("latin-1")
                for key, value in complete_response.raw_headers
                if key.lower() == b"set-cookie"
            )
            self.assertIn(auth.VK_FLOW_COOKIE_NAME, set_cookie)
            self.assertIn("Max-Age=0", set_cookie)
            self.assertIn(auth.AUTH_COOKIE_NAME, set_cookie)

    async def test_vk_complete_link_attaches_vk_to_current_telegram_profile(self):
        start_response = Response()
        with (
            patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
            patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
        ):
            start = await auth.vk_id_start(
                auth.VkAuthStartRequest(action="link"),
                response=start_response,
            )
        cookie_value = start_response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]

        async with self.Session() as db:
            telegram_user = User(
                telegram_id=123456789,
                first_name="Telegram",
                role="user",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            db.add(telegram_user)
            await db.commit()

            with (
                patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
                patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
                patch.object(
                    auth,
                    "_exchange_vk_or_502",
                    new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
                ),
            ):
                result = await auth.vk_id_complete(
                    auth.VkAuthCompleteRequest(
                        code="code",
                        device_id="device",
                        state=start.state,
                    ),
                    request=SimpleNamespace(cookies={auth.VK_FLOW_COOKIE_NAME: cookie_value}),
                    response=Response(),
                    current_user=telegram_user,
                    db=db,
                )

            self.assertEqual(result.status, "success")
            refreshed = await db.get(User, telegram_user.telegram_id)
            self.assertEqual(refreshed.vk_user_id, "741852963")

    async def test_vk_complete_link_uses_server_flow_when_external_browser_has_no_cookie(self):
        async with self.Session() as db:
            telegram_user = User(
                telegram_id=123456789,
                first_name="Telegram",
                role="user",
                stats_display_mode="percent",
                tg_chat_joined=False,
            )
            db.add(telegram_user)
            await db.commit()

            start_response = Response()
            with (
                patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
                patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
            ):
                start = await auth.vk_id_start(
                    auth.VkAuthStartRequest(action="link"),
                    response=start_response,
                    current_user=telegram_user,
                    db=db,
                )

            with (
                patch.object(auth.settings, "VK_ID_APP_ID", "54626979"),
                patch.object(auth.settings, "VK_ID_REDIRECT_URI", "https://shamra1.pro"),
                patch.object(
                    auth,
                    "_exchange_vk_or_502",
                    new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
                ),
            ):
                result = await auth.vk_id_complete(
                    auth.VkAuthCompleteRequest(
                        code="code",
                        device_id="device",
                        state=start.state,
                    ),
                    request=SimpleNamespace(cookies={}),
                    response=Response(),
                    current_user=None,
                    db=db,
                )

            self.assertEqual(result.status, "success")
            refreshed = await db.get(User, telegram_user.telegram_id)
            self.assertEqual(refreshed.vk_user_id, "741852963")

    async def test_vk_login_creates_web_only_profile_without_debug_bypass(self):
        async with self.Session() as db:
            with patch.object(
                auth,
                "_exchange_vk_or_502",
                new=AsyncMock(return_value={
                    "vk_user_id": "741852963",
                    "vk_display_name": "VK Client",
                    "vk_photo_url": "https://vk.example/avatar.jpg",
                }),
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
            self.assertEqual(response.user.vk_photo_url, "https://vk.example/avatar.jpg")

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].vk_user_id, "741852963")
            self.assertEqual(users[0].vk_photo_url, "https://vk.example/avatar.jpg")

    async def test_first_vk_login_enqueues_registration_report_without_onboarding(self):
        async with self.Session() as db:
            with (
                patch.object(
                    auth,
                    "_exchange_vk_or_502",
                    new=AsyncMock(return_value={"vk_user_id": "741852963", "vk_display_name": "VK Client"}),
                ),
                patch.object(auth.settings, "SHAMRAI_ONBOARDING_REPORT_CHAT_ID", -100777),
                patch.object(auth.settings, "TELEGRAM_ADMIN_GROUP_CHAT_ID", -100555),
            ):
                response = await auth.vk_id_login(
                    auth.VkOAuthCodeRequest(
                        code="code",
                        device_id="device",
                        code_verifier="verifier",
                        state="state",
                    ),
                    response=Response(),
                    current_user=None,
                    db=db,
                )

            result = await db.execute(select(DeliveryOutbox))
            outbox_items = result.scalars().all()
            self.assertEqual(len(outbox_items), 1)
            outbox_item = outbox_items[0]
            self.assertEqual(outbox_item.channel, "telegram_message")
            self.assertEqual(outbox_item.user_id, response.user.telegram_id)
            self.assertEqual(outbox_item.dedupe_key, f"user_registration:{response.user.telegram_id}")

            payload = outbox_item.payload["payload"]
            self.assertEqual(payload["chat_id"], -100777)
            self.assertIn("Новая регистрация клиента", payload["text"])
            self.assertIn("Источник регистрации", payload["text"])
            self.assertIn("VK ID", payload["text"])
            self.assertIn("Web/VK клиент", payload["text"])
            self.assertIn("Telegram ID", payload["text"])
            self.assertIn("не привязан", payload["text"])
            self.assertNotIn(str(response.user.telegram_id), payload["text"])

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

            with patch.object(auth.settings, "OWNER_TELEGRAM_ID", None):
                response = await auth.poll_telegram_bot_auth_session(
                    session.auth_token,
                    response=Response(),
                    current_user=web_user,
                    db=db,
            )

            self.assertEqual(response.status, "confirmed")
            self.assertIsNone(response.access_token)
            self.assertNotIn("access_token", response.model_dump(exclude_none=True))
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
            self.assertIsNone(response.access_token)
            self.assertNotIn("access_token", response.model_dump(exclude_none=True))
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

    async def test_bot_auth_session_restores_from_cache_after_memory_reset(self):
        cached_sessions = {}

        async def fake_cache_set_json(key, value, ttl_seconds=None):
            cached_sessions[key] = value

        async def fake_cache_get_json(key):
            return cached_sessions.get(key)

        async def fake_cache_delete(*keys):
            for key in keys:
                cached_sessions.pop(key, None)

        with (
            patch.object(telegram_auth, "cache_set_json", new=AsyncMock(side_effect=fake_cache_set_json)),
            patch.object(telegram_auth, "cache_get_json", new=AsyncMock(side_effect=fake_cache_get_json)),
            patch.object(telegram_auth, "cache_delete", new=AsyncMock(side_effect=fake_cache_delete)),
        ):
            session = await telegram_auth.create_telegram_bot_auth_session(source_user_id=-1001)
            telegram_auth._sessions.clear()

            restored = await telegram_auth.get_telegram_bot_auth_session(session.auth_token)
            self.assertIsNotNone(restored)
            self.assertEqual(restored.source_user_id, -1001)
            self.assertEqual(restored.status, "pending")

            confirmed = await telegram_auth.confirm_telegram_bot_auth_session(
                session.auth_token,
                {
                    "id": 123456789,
                    "first_name": "Telegram",
                    "username": "tg_user",
                },
            )
            self.assertTrue(confirmed)

            telegram_auth._sessions.clear()
            restored_confirmed = await telegram_auth.get_telegram_bot_auth_session(session.auth_token)
            self.assertIsNotNone(restored_confirmed)
            self.assertEqual(restored_confirmed.status, "confirmed")
            self.assertEqual(restored_confirmed.telegram_user["id"], 123456789)

            await telegram_auth.consume_telegram_bot_auth_session(session.auth_token)
            self.assertEqual(cached_sessions, {})

    async def test_vk_first_then_telegram_same_device_does_not_merge_without_current_cookie(self):
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
            self.assertIsNone(tg_response.user.vk_user_id)
            self.assertFalse(tg_response.user.identity_complete)
            self.assertEqual(tg_response.user.identity_providers, ["telegram"])

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 2)
            self.assertEqual(sorted(user.telegram_id for user in users), [vk_response.user.telegram_id, 223456789])

    async def test_vk_first_then_telegram_current_cookie_merges_into_one_user(self):
        async with self.Session() as db:
            web_user = await auth._create_vk_only_user(db, "741852963", "VK Client")
            web_user.matches_remaining = 3
            await db.commit()
            web_user = await auth._load_user_with_profile(db, web_user.telegram_id)

            with patch.object(
                auth,
                "verify_telegram_init_data",
                return_value={"id": 223456789, "first_name": "Telegram", "username": "tg_user"},
            ):
                tg_response = await auth.login_user(
                    auth.LoginRequest(initData="signed"),
                    response=Response(),
                    current_user=web_user,
                    db=db,
                )

            self.assertEqual(tg_response.user.telegram_id, 223456789)
            self.assertEqual(tg_response.user.vk_user_id, "741852963")
            self.assertTrue(tg_response.user.identity_complete)
            self.assertEqual(tg_response.user.matches_remaining, 3)

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 1)
            self.assertEqual(users[0].telegram_id, 223456789)
            self.assertEqual(users[0].vk_user_id, "741852963")

    async def test_telegram_first_then_vk_same_device_does_not_attach_without_current_cookie(self):
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

            self.assertLess(vk_response.user.telegram_id, 0)
            self.assertEqual(vk_response.user.vk_user_id, "741852963")
            self.assertFalse(vk_response.user.identity_complete)

            result = await db.execute(select(User))
            users = result.scalars().all()
            self.assertEqual(len(users), 2)
            self.assertEqual(sorted(user.telegram_id for user in users), [vk_response.user.telegram_id, 323456789])

    async def test_first_telegram_login_enqueues_registration_report_without_onboarding(self):
        async with self.Session() as db:
            with (
                patch.object(
                    auth,
                    "verify_telegram_init_data",
                    return_value={
                        "id": 423456789,
                        "first_name": "New",
                        "last_name": "Client",
                        "username": "new_client",
                    },
                ),
                patch.object(auth.settings, "SHAMRAI_ONBOARDING_REPORT_CHAT_ID", None),
                patch.object(auth.settings, "TELEGRAM_ADMIN_GROUP_CHAT_ID", -100555),
            ):
                await auth.login_user(
                    auth.LoginRequest(initData="signed"),
                    response=Response(),
                    current_user=None,
                    db=db,
                )

            result = await db.execute(select(DeliveryOutbox))
            outbox_items = result.scalars().all()
            self.assertEqual(len(outbox_items), 1)
            outbox_item = outbox_items[0]
            self.assertEqual(outbox_item.channel, "telegram_message")
            self.assertEqual(outbox_item.user_id, 423456789)
            self.assertEqual(outbox_item.dedupe_key, "user_registration:423456789")

            payload = outbox_item.payload["payload"]
            self.assertEqual(payload["chat_id"], -100555)
            self.assertEqual(payload["parse_mode"], "HTML")
            self.assertIn("Новая регистрация клиента", payload["text"])
            self.assertIn("Источник регистрации", payload["text"])
            self.assertIn("Telegram Mini App", payload["text"])
            self.assertIn("New Client / @new_client", payload["text"])
            self.assertIn("Telegram ID", payload["text"])
            self.assertIn("423456789", payload["text"])
            self.assertIn("Анкета", payload["text"])
            self.assertIn("не пройдена", payload["text"])

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

    async def test_vk_login_ignores_stolen_device_link_when_vk_belongs_to_real_telegram(self):
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
                response = await auth.vk_id_login(
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

            self.assertEqual(response.user.telegram_id, existing_vk_owner.telegram_id)
            refreshed_device_user = await db.get(User, device_user.telegram_id)
            self.assertIsNone(refreshed_device_user.vk_user_id)

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

    async def test_identity_merge_preserves_and_deduplicates_referral_reward_history(self):
        async with self.Session() as db:
            source = User(
                telegram_id=-5001,
                first_name="Web duplicate",
                role="user",
                stats_display_mode="percent",
            )
            target = User(
                telegram_id=5001,
                first_name="Telegram target",
                role="user",
                stats_display_mode="percent",
            )
            invited = User(telegram_id=5002, first_name="Invited", role="user")
            other_referrer = User(telegram_id=5003, first_name="Other", role="user")
            db.add_all([source, target, invited, other_referrer])
            await db.flush()

            source_event = ReferralRewardEvent(
                referrer_user_id=source.telegram_id,
                referred_user_id=invited.telegram_id,
                source_type="single_bet",
                status="held",
                risk_score=40,
                risk_reasons=["source-risk"],
                matches_awarded=1,
                reviewed_by=source.telegram_id,
            )
            target_event = ReferralRewardEvent(
                referrer_user_id=target.telegram_id,
                referred_user_id=invited.telegram_id,
                source_type="subscription",
                status="approved",
                risk_score=0,
                risk_reasons=[],
                matches_awarded=2,
            )
            referred_event = ReferralRewardEvent(
                referrer_user_id=other_referrer.telegram_id,
                referred_user_id=source.telegram_id,
                source_type="subscription",
                status="rejected",
                risk_score=100,
                risk_reasons=["identity-risk"],
                matches_awarded=0,
            )
            reciprocal_source_event = ReferralRewardEvent(
                referrer_user_id=source.telegram_id,
                referred_user_id=target.telegram_id,
                source_type="identity_overlap",
                status="held",
                risk_score=50,
                risk_reasons=["source-target-overlap"],
                matches_awarded=1,
            )
            reciprocal_target_event = ReferralRewardEvent(
                referrer_user_id=target.telegram_id,
                referred_user_id=source.telegram_id,
                source_type="identity_overlap",
                status="approved",
                risk_score=0,
                risk_reasons=[],
                matches_awarded=2,
            )
            marketing_event = MarketingRewardEvent(
                user_id=source.telegram_id,
                widget_key="daily_spin",
                reward_type="free_bet",
                reward_value=1,
                risk_status="approved",
                risk_reasons=[],
            )
            db.add_all([
                source_event,
                target_event,
                referred_event,
                reciprocal_source_event,
                reciprocal_target_event,
                marketing_event,
            ])
            await db.commit()

            await auth._merge_web_only_user_into_telegram(db, source, target)
            await db.commit()

            events = (
                await db.execute(select(ReferralRewardEvent).order_by(ReferralRewardEvent.id))
            ).scalars().all()
            merged_outbound = next(
                event
                for event in events
                if event.referrer_user_id == target.telegram_id
                and event.referred_user_id == invited.telegram_id
            )
            merged_inbound = next(
                event
                for event in events
                if event.referrer_user_id == other_referrer.telegram_id
                and event.referred_user_id == target.telegram_id
            )
            merged_reciprocal = next(
                event
                for event in events
                if event.referrer_user_id == target.telegram_id
                and event.referred_user_id == target.telegram_id
            )
            persisted_marketing_event = await db.get(MarketingRewardEvent, marketing_event.id)

            self.assertIsNone(await db.get(User, source.telegram_id))
            self.assertEqual(len(events), 3)
            self.assertEqual(merged_outbound.status, "approved")
            self.assertEqual(merged_outbound.matches_awarded, 3)
            self.assertEqual(merged_outbound.reviewed_by, target.telegram_id)
            self.assertIn("source-risk", merged_outbound.risk_reasons)
            self.assertEqual(merged_inbound.status, "rejected")
            self.assertEqual(merged_reciprocal.status, "rejected")
            self.assertEqual(merged_reciprocal.matches_awarded, 3)
            self.assertIn("identity_merge_self_referral", merged_reciprocal.risk_reasons)
            self.assertEqual(persisted_marketing_event.user_id, target.telegram_id)

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

    async def test_web_only_admin_role_is_not_transferred_to_telegram_profile(self):
        async with self.Session() as db:
            web_user = User(
                telegram_id=-1002,
                phone="+79990001123",
                first_name="Web",
                role="admin",
                stats_display_mode="percent",
                purchased_bets_balance=1,
                matches_remaining=1,
                tg_chat_joined=False,
            )
            db.add(web_user)
            await db.commit()

            user = await auth._upsert_telegram_user(
                db,
                {
                    "id": 123456790,
                    "phone_number": "+7 (999) 000-11-23",
                    "first_name": "Telegram",
                },
            )
            await db.commit()

            self.assertEqual(user.telegram_id, 123456790)
            self.assertEqual(user.role, "user")

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
