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
        service_format="logic_review",
        favorite_sports=["Футбол", "Теннис"],
        vk_user_id="741852963",
        currency_preference="RUB",
    )


class OnboardingReportTests(unittest.IsolatedAsyncioTestCase):
    async def test_onboarding_recommendation_flat_never_drops_below_seven_percent(self):
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [])
                )
            )
        )
        request = make_onboarding_request().model_copy(update={
            "experience_level": "novice",
            "risk_tolerance": "cautious",
        })

        recommendation = await users.build_onboarding_recommendation(db, request)

        self.assertGreaterEqual(recommendation["flat_stake_percent"], 7.0)

    async def test_onboarding_recommendation_includes_vip_verdict(self):
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=SimpleNamespace(
                    scalars=lambda: SimpleNamespace(all=lambda: [])
                )
            )
        )
        request = make_onboarding_request().model_copy(update={"service_format": "vip_support"})

        recommendation = await users.build_onboarding_recommendation(db, request)

        self.assertEqual(recommendation["service_format_label"], "VIP-сопровождение")
        self.assertEqual(recommendation["vip_verdict_title"], "VIP-контур")
        self.assertIn("личное сопровождение", recommendation["vip_verdict_caption"])

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
            "service_format_label": "С объяснением",
            "vip_verdict_title": "Проверочный контур",
            "vip_verdict_caption": "Клиенту важны логика входа и прозрачный разбор.",
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
        self.assertIn("Формат сервиса", payload["text"])
        self.assertIn("С объяснением", payload["text"])
        self.assertIn("VIP-вердикт", payload["text"])
        self.assertIn("Проверочный контур", payload["text"])
        self.assertIn("Следующий шаг для админа", payload["text"])
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

    def test_allows_empty_favorite_sports(self):
        request = make_onboarding_request().model_copy(update={"favorite_sports": []})

        users.validate_onboarding_payload(request)

    def test_rejects_unknown_service_format(self):
        request = make_onboarding_request().model_copy(update={"service_format": "phone_spam"})

        with self.assertRaises(Exception) as context:
            users.validate_onboarding_payload(request)

        self.assertIn("Invalid service_format", str(context.exception))

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
        self.assertEqual(users.onboarding_client_tag(request), "Цель: быстрые входы")

    def test_client_tag_for_trust_check_is_display_ready_russian(self):
        request = make_onboarding_request().model_copy(update={"onboarding_goal": "trust_check"})

        self.assertEqual(users.onboarding_client_tag(request), "Цель: проверить честность")


if __name__ == "__main__":
    unittest.main()
