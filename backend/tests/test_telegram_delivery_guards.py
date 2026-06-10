import unittest
from unittest.mock import patch

from src import main


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


if __name__ == "__main__":
    unittest.main()
