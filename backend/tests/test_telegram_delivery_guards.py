import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.routing import APIRoute

from src import main
from src.api import telegram_webhook
from src.services import forecast_delivery


class TelegramDeliveryGuardTests(unittest.TestCase):
    def test_callback_answer_failure_does_not_block_polling(self):
        response = {
            "method": "answerCallbackQuery",
            "callback_query_id": "callback-1",
            "text": "Accepted",
        }

        with patch(
            "src.main.call_telegram_api",
            return_value={"ok": False, "description": "Bad Request: query is too old"},
        ):
            self.assertTrue(main._dispatch_polling_response(response))

    def test_send_message_failure_can_still_retry_polling_update(self):
        response = {
            "method": "sendMessage",
            "chat_id": 123,
            "text": "Hello",
        }

        with patch(
            "src.main.call_telegram_api",
            return_value={"ok": False, "description": "network error"},
        ):
            self.assertFalse(main._dispatch_polling_response(response))

    def test_allowed_updates_need_repair_when_callback_updates_are_missing(self):
        self.assertTrue(main._telegram_allowed_updates_need_repair(["message"]))

    def test_allowed_updates_are_current_when_callbacks_are_allowed_or_unspecified(self):
        self.assertFalse(
            main._telegram_allowed_updates_need_repair(
                ["message", "callback_query", "pre_checkout_query"]
            )
        )
        self.assertFalse(main._telegram_allowed_updates_need_repair([]))
        self.assertFalse(main._telegram_allowed_updates_need_repair(None))

    def test_webhook_base_ignores_tunnel_logs_outside_debug(self):
        with (
            patch.object(main.settings, "DEBUG_MODE", False),
            patch.object(main.settings, "API_BASE_URL", "https://shamra1.pro"),
            patch.object(main.settings, "FRONTEND_BASE_URL", "https://shamra1.pro/app"),
            patch.object(main, "_active_tunnel_url_from_logs", return_value="https://stale-tunnel.example"),
        ):
            self.assertEqual(main._telegram_webhook_url(), "https://shamra1.pro/api/telegram/webhook")

    def test_webhook_registration_payload_can_pin_public_ip(self):
        with (
            patch.object(main.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "secret"),
            patch.object(main.settings, "TELEGRAM_WEBHOOK_IP_ADDRESS", "82.147.67.245"),
        ):
            payload = main._telegram_webhook_registration_payload()

        self.assertEqual(payload["ip_address"], "82.147.67.245")
        self.assertEqual(payload["secret_token"], "secret")
        self.assertIn("callback_query", payload["allowed_updates"])

    def test_emoji_ids_command_defaults_deny_when_allowlist_is_empty(self):
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", None),
            patch.object(telegram_webhook.settings, "SALES_MANAGER_TELEGRAM_ID", None),
        ):
            self.assertFalse(telegram_webhook._can_use_emoji_id_command(12345))

    def test_emoji_ids_command_allows_owner_or_sales_manager_only(self):
        with (
            patch.object(telegram_webhook.settings, "OWNER_TELEGRAM_ID", 111),
            patch.object(telegram_webhook.settings, "SALES_MANAGER_TELEGRAM_ID", 222),
        ):
            self.assertTrue(telegram_webhook._can_use_emoji_id_command(111))
            self.assertTrue(telegram_webhook._can_use_emoji_id_command(222))
            self.assertFalse(telegram_webhook._can_use_emoji_id_command(333))


class HealthDiagnosticsAccessTests(unittest.TestCase):
    def test_public_health_remains_public(self):
        routes = [route for route in main.app.routes if isinstance(route, APIRoute) and route.path == "/api/health"]

        self.assertEqual(len(routes), 1)
        self.assertFalse(routes[0].dependant.dependencies)

    def test_deep_health_endpoints_reject_anonymous_outside_debug(self):
        async def run_check():
            await main.require_health_diagnostics_access(None)

        with patch.object(main.settings, "DEBUG_MODE", False):
            with self.assertRaises(HTTPException) as exc:
                self.async_run(run_check())

        self.assertEqual(exc.exception.status_code, 403)

    def test_diagnostics_routes_are_guarded(self):
        diagnostic_paths = {
            "/api/health/payments",
            "/api/health/telegram",
            "/api/health/vk",
            "/api/health/vk/deep",
        }
        guarded_paths = set()
        for route in main.app.routes:
            if not isinstance(route, APIRoute):
                continue
            if any(dependency.call is main.require_health_diagnostics_access for dependency in route.dependant.dependencies):
                guarded_paths.add(route.path)

        self.assertEqual(diagnostic_paths, guarded_paths & diagnostic_paths)

    def test_diagnostics_allow_debug_mode_without_user(self):
        async def run_check():
            await main.require_health_diagnostics_access(None)

        with patch.object(main.settings, "DEBUG_MODE", True):
            self.async_run(run_check())

    def test_diagnostics_allow_staff_user_outside_debug(self):
        async def run_check():
            await main.require_health_diagnostics_access(SimpleNamespace(role="admin"))

        with patch.object(main.settings, "DEBUG_MODE", False):
            self.async_run(run_check())

    def async_run(self, coroutine):
        import asyncio

        return asyncio.run(coroutine)


class TelegramForecastCallbackTests(unittest.IsolatedAsyncioTestCase):
    async def test_sales_send_callback_is_scheduled_in_background(self):
        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)

        with (
            patch.object(telegram_webhook, "_run_background", side_effect=fake_run_background) as run_background,
            patch.object(
                telegram_webhook,
                "_process_sales_send_callback",
                new=Mock(return_value=object()),
            ) as process_sales_send,
        ):
            response = await telegram_webhook._handle_forecast_callback(
                {
                    "id": "callback-1",
                    "from": {"id": 111},
                    "message": {"chat": {"id": 111}},
                    "data": "forecast:sales_send:00000000-0000-0000-0000-000000000001",
                },
                object(),
            )

        self.assertEqual(response["method"], "answerCallbackQuery")
        self.assertEqual(response["callback_query_id"], "callback-1")
        self.assertIn("отправляем", response["text"])
        process_sales_send.assert_called_once()
        run_background.assert_called_once()
        self.assertEqual(len(created_tasks), 1)

    async def test_inactive_take_callback_shows_alert_and_clears_client_message(self):
        request_id = "00000000-0000-0000-0000-000000000001"
        forecast_request = SimpleNamespace(
            id=request_id,
            status=forecast_delivery.FORECAST_STATUS_REMOVED,
        )
        expected_message = "Прогноз уже не активен. Реагировать не нужно."

        class FakeDb:
            async def commit(self):
                return None

            async def rollback(self):
                return None

        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)
            if hasattr(coro, "close"):
                coro.close()

        with (
            patch.object(
                telegram_webhook,
                "set_forecast_request_interested",
                new=AsyncMock(return_value=(forecast_request, expected_message, False)),
            ),
            patch.object(telegram_webhook, "_run_background", side_effect=fake_run_background) as run_background,
        ):
            response = await telegram_webhook._handle_forecast_callback(
                {
                    "id": "callback-1",
                    "from": {"id": 111},
                    "message": {"chat": {"id": 111}},
                    "data": f"forecast:take:{request_id}",
                },
                FakeDb(),
            )

        self.assertEqual(response["method"], "answerCallbackQuery")
        self.assertEqual(response["callback_query_id"], "callback-1")
        self.assertEqual(response["text"], expected_message)
        self.assertTrue(response["show_alert"])
        run_background.assert_called_once()
        self.assertEqual(len(created_tasks), 1)


class TelegramChatJoinedTests(unittest.IsolatedAsyncioTestCase):
    class _Scalars:
        def __init__(self, user):
            self.user = user

        def first(self):
            return self.user

    class _Result:
        def __init__(self, user):
            self.user = user

        def scalars(self):
            return TelegramChatJoinedTests._Scalars(self.user)

    class _Session:
        def __init__(self, user):
            self.user = user
            self.committed = False

        async def execute(self, _statement):
            return TelegramChatJoinedTests._Result(self.user)

        async def commit(self):
            self.committed = True

    async def test_private_bot_message_marks_existing_telegram_user_ready(self):
        user = SimpleNamespace(telegram_id=123456789, tg_chat_joined=False)
        session = self._Session(user)

        changed = await telegram_webhook._mark_private_telegram_chat_joined(
            session,
            {
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 123456789},
                "text": "/start",
            },
        )

        self.assertTrue(changed)
        self.assertTrue(user.tg_chat_joined)
        self.assertTrue(session.committed)

    async def test_group_message_does_not_mark_telegram_chat_ready(self):
        user = SimpleNamespace(telegram_id=123456789, tg_chat_joined=False)
        session = self._Session(user)

        changed = await telegram_webhook._mark_private_telegram_chat_joined(
            session,
            {
                "chat": {"id": -100123, "type": "supergroup"},
                "from": {"id": 123456789},
                "text": "hello",
            },
        )

        self.assertFalse(changed)
        self.assertFalse(user.tg_chat_joined)
        self.assertFalse(session.committed)

    async def test_already_ready_user_is_not_committed_again(self):
        user = SimpleNamespace(telegram_id=123456789, tg_chat_joined=True)
        session = self._Session(user)

        changed = await telegram_webhook._mark_private_telegram_chat_joined(
            session,
            {
                "chat": {"id": 123456789, "type": "private"},
                "from": {"id": 123456789},
                "text": "hello",
            },
        )

        self.assertFalse(changed)
        self.assertTrue(user.tg_chat_joined)
        self.assertFalse(session.committed)


class VkHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_vk_health_is_fast_config_only(self):
        with (
            patch.object(main, "vk_group_id", return_value=239419819),
            patch.object(main, "vk_delivery_configured", return_value=True),
            patch.object(main, "probe_vk_api") as probe_vk_api,
            patch.object(main.settings, "VK_GROUP_ACCESS_TOKEN", "token"),
            patch.object(main.settings, "VK_CALLBACK_SECRET", "secret"),
            patch.object(main.settings, "VK_CALLBACK_CONFIRMATION_CODE", "confirmation"),
        ):
            response = await main.vk_health_check()

        self.assertTrue(response["ok"])
        self.assertTrue(response["configured"])
        probe_vk_api.assert_not_called()

    async def test_vk_deep_health_runs_live_probe(self):
        with (
            patch.object(main, "vk_group_id", return_value=239419819),
            patch.object(main, "vk_delivery_configured", return_value=True),
            patch.object(main, "probe_vk_api", return_value=True) as probe_vk_api,
            patch.object(main.settings, "VK_GROUP_ACCESS_TOKEN", "token"),
            patch.object(main.settings, "VK_CALLBACK_SECRET", "secret"),
            patch.object(main.settings, "VK_CALLBACK_CONFIRMATION_CODE", "confirmation"),
        ):
            response = await main.vk_deep_health_check()

        self.assertTrue(response["ok"])
        self.assertTrue(response["api_probe_ok"])
        self.assertIn("duration_ms", response)
        probe_vk_api.assert_called_once()


class PaymentsHealthTests(unittest.IsolatedAsyncioTestCase):
    async def test_payment_health_reports_configuration_without_secrets(self):
        with (
            patch.object(main.settings, "APP_ENV", "production"),
            patch.object(main.settings, "DEBUG_MODE", False),
            patch.object(main.settings, "TELEGRAM_BOT_TOKEN", "123456:realistic"),
            patch.object(main.settings, "YOOKASSA_SHOP_ID", "shop-id"),
            patch.object(main.settings, "YOOKASSA_SECRET_KEY", "super-secret"),
            patch.object(main.settings, "YOOKASSA_RETURN_URL", "https://shamra1.pro/app"),
        ):
            response = await main.payments_health_check()

        self.assertTrue(response["ok"])
        self.assertTrue(response["telegram_stars"]["configured"])
        self.assertTrue(response["yookassa"]["configured"])
        self.assertTrue(response["yookassa"]["return_url_configured"])
        self.assertTrue(response["production_requirements_met"])
        self.assertNotIn("super-secret", str(response))
        self.assertNotIn("shop-id", str(response))


class RuntimeSecurityTests(unittest.TestCase):
    def test_production_vk_callback_secret_is_required_when_vk_group_is_enabled(self):
        with (
            patch.object(main.settings, "APP_ENV", "production"),
            patch.object(main.settings, "DEBUG_MODE", False),
            patch.object(main.settings, "TELEGRAM_BOT_TOKEN", "123456:realistic"),
            patch.object(main.settings, "OWNER_TELEGRAM_ID", 1),
            patch.object(main.settings, "JWT_SECRET_KEY", "x" * 32),
            patch.object(main.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "telegram-secret"),
            patch.object(main.settings, "YOOKASSA_SHOP_ID", "shop-id"),
            patch.object(main.settings, "YOOKASSA_SECRET_KEY", "yookassa-secret"),
            patch.object(main.settings, "YOOKASSA_RETURN_URL", "https://shamra1.pro/app"),
            patch.object(main.settings, "VK_GROUP_ID", "239419819"),
            patch.object(main.settings, "VK_CALLBACK_CONFIRMATION_CODE", "confirmation-code"),
            patch.object(main.settings, "VK_CALLBACK_SECRET", ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "VK_CALLBACK_SECRET"):
                main.settings.validate_runtime_security()

    def test_production_vk_group_token_is_required_when_vk_group_is_enabled(self):
        with (
            patch.object(main.settings, "APP_ENV", "production"),
            patch.object(main.settings, "DEBUG_MODE", False),
            patch.object(main.settings, "TELEGRAM_BOT_TOKEN", "123456:realistic"),
            patch.object(main.settings, "OWNER_TELEGRAM_ID", 1),
            patch.object(main.settings, "JWT_SECRET_KEY", "x" * 32),
            patch.object(main.settings, "TELEGRAM_WEBHOOK_SECRET_TOKEN", "telegram-secret"),
            patch.object(main.settings, "YOOKASSA_SHOP_ID", "shop-id"),
            patch.object(main.settings, "YOOKASSA_SECRET_KEY", "yookassa-secret"),
            patch.object(main.settings, "YOOKASSA_RETURN_URL", "https://shamra1.pro/app"),
            patch.object(main.settings, "VK_GROUP_ID", "239419819"),
            patch.object(main.settings, "VK_CALLBACK_CONFIRMATION_CODE", "confirmation-code"),
            patch.object(main.settings, "VK_CALLBACK_SECRET", "callback-secret"),
            patch.object(main.settings, "VK_GROUP_ACCESS_TOKEN", ""),
        ):
            with self.assertRaisesRegex(RuntimeError, "VK_GROUP_ACCESS_TOKEN"):
                main.settings.validate_runtime_security()


if __name__ == "__main__":
    unittest.main()
