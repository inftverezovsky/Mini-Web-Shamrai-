import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.api import users
from src.schemas.schemas import OnboardRequest


def make_onboarding_request() -> OnboardRequest:
    return OnboardRequest(
        anti_capper_pains=["Не успеваю зайти", "Нет дисциплины"],
        onboarding_goal="fast_signals",
        experience_level="amateur",
        bankroll_size="mid",
        risk_tolerance="balanced",
        bookmakers=["fonbet"],
        primary_bookmaker="fonbet",
        favorite_sports=["Футбол", "Теннис"],
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
        self.assertIn("Цель", payload["text"])
        self.assertIn("Быстрые входы по линии", payload["text"])
        self.assertIn("Спорты", payload["text"])
        self.assertIn("Футбол, Теннис", payload["text"])
        self.assertIn("Рекомендованный флэт", payload["text"])
        self.assertIn("FOMO 24ч", payload["text"])
        self.assertIn("Источник расчета", payload["text"])

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


class OnboardingPayloadValidationTests(unittest.TestCase):
    def test_rejects_invalid_onboarding_goal(self):
        request = make_onboarding_request().model_copy(update={"onboarding_goal": "guaranteed_profit"})

        with self.assertRaises(Exception) as context:
            users.validate_onboarding_payload(request)

        self.assertIn("Invalid onboarding_goal", str(context.exception))

    def test_rejects_unknown_favorite_sport(self):
        request = make_onboarding_request().model_copy(update={"favorite_sports": ["Футбол", "Квиддич"]})

        with self.assertRaises(Exception) as context:
            users.validate_onboarding_payload(request)

        self.assertIn("Invalid favorite_sports", str(context.exception))

    def test_requires_other_bookmaker_name_for_other_code(self):
        request = make_onboarding_request().model_copy(update={
            "bookmakers": ["other"],
            "primary_bookmaker": "other",
            "other_bookmaker_name": "",
        })

        with self.assertRaises(Exception) as context:
            users.validate_onboarding_payload(request)

        self.assertIn("other_bookmaker_name is required", str(context.exception))

    def test_derives_crm_segment_from_onboarding_answers(self):
        request = make_onboarding_request().model_copy(update={
            "experience_level": "pro",
            "bankroll_size": "high",
            "risk_tolerance": "aggressive",
            "onboarding_goal": "fast_signals",
        })

        self.assertEqual(users.onboarding_client_group(request), "Новый PRO")
        self.assertEqual(users.onboarding_client_tag(request), "goal: fast_signals")


if __name__ == "__main__":
    unittest.main()
