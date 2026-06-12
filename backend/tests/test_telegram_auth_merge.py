import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import auth
from src.models.database import Base
from src.models.models import User


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
