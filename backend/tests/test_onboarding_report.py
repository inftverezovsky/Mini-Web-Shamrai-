import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api import users
from src.schemas.schemas import OnboardRequest


def make_onboarding_request() -> OnboardRequest:
    return OnboardRequest(
        anti_capper_pains=["Не успеваю зайти", "Нет дисциплины"],
        experience_level="amateur",
        bankroll_size="mid",
        risk_tolerance="balanced",
        bookmakers=["fonbet"],
        primary_bookmaker="fonbet",
        vk_user_id="741852963",
        currency_preference="RUB",
    )


class OnboardingReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_onboarding_report_falls_back_to_admin_group_chat(self):
        db = SimpleNamespace()
        user = SimpleNamespace(
            telegram_id=123456789,
            username="client_user",
            first_name="Ivan",
            last_name="Petrov",
            phone="+79990001122",
            vk_user_id="741852963",
            is_web_only=False,
        )
        bookmaker = SimpleNamespace(name="Fonbet")
        recommendation = {
            "flat_stake_percent": 2.5,
            "monthly_profit_percent": 24.0,
            "missed_profit_percent_24h": 7.4,
        }

        with (
            patch.object(users.settings, "SHAMRAI_ONBOARDING_REPORT_CHAT_ID", None),
            patch.object(users.settings, "TELEGRAM_ADMIN_GROUP_CHAT_ID", -100555),
            patch.object(users, "enqueue_delivery", new=AsyncMock()) as enqueue_delivery,
        ):
            await users.enqueue_onboarding_report(
                db,
                user,
                make_onboarding_request(),
                [bookmaker],
                recommendation,
            )

        enqueue_delivery.assert_awaited_once()
        kwargs = enqueue_delivery.await_args.kwargs
        self.assertEqual(kwargs["channel"], users.CHANNEL_TELEGRAM_MESSAGE)
        self.assertEqual(kwargs["user_id"], 123456789)
        payload = kwargs["payload"]["payload"]
        self.assertEqual(payload["chat_id"], -100555)
        self.assertEqual(payload["parse_mode"], "HTML")
        self.assertIn("Новая анкета приветственного опроса", payload["text"])
        self.assertIn("Telegram ID", payload["text"])
        self.assertIn("123456789", payload["text"])
        self.assertIn("@client_user", payload["text"])
        self.assertIn("+79990001122", payload["text"])
        self.assertIn("VK ID", payload["text"])
        self.assertIn("741852963", payload["text"])
        self.assertIn("Тип профиля", payload["text"])

    async def test_onboarding_report_dedicated_chat_has_priority(self):
        db = SimpleNamespace()
        user = SimpleNamespace(
            telegram_id=123456789,
            username=None,
            first_name="VK",
            last_name=None,
            phone=None,
            vk_user_id="741852963",
            is_web_only=True,
        )
        recommendation = {
            "flat_stake_percent": 2.5,
            "monthly_profit_percent": 24.0,
            "missed_profit_percent_24h": 7.4,
        }

        with (
            patch.object(users.settings, "SHAMRAI_ONBOARDING_REPORT_CHAT_ID", -100777),
            patch.object(users.settings, "TELEGRAM_ADMIN_GROUP_CHAT_ID", -100555),
            patch.object(users, "enqueue_delivery", new=AsyncMock()) as enqueue_delivery,
        ):
            await users.enqueue_onboarding_report(
                db,
                user,
                make_onboarding_request(),
                [SimpleNamespace(name="Fonbet")],
                recommendation,
            )

        payload = enqueue_delivery.await_args.kwargs["payload"]["payload"]
        self.assertEqual(payload["chat_id"], -100777)


if __name__ == "__main__":
    unittest.main()
