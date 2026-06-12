import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from src.core.config import settings
from src.services import vk_delivery


class VkDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.previous_values = {
            "VK_GROUP_ID": settings.VK_GROUP_ID,
            "VK_GROUP_ACCESS_TOKEN": settings.VK_GROUP_ACCESS_TOKEN,
        }
        settings.VK_GROUP_ID = "239419819"
        settings.VK_GROUP_ACCESS_TOKEN = "test-token"

    def tearDown(self):
        for key, value in self.previous_values.items():
            setattr(settings, key, value)

    def test_check_vk_group_member_returns_true_for_member_response(self):
        with patch.object(vk_delivery, "_vk_api_request", return_value={"ok": True, "response": 1}):
            self.assertTrue(vk_delivery.check_vk_group_member("123"))

    def test_check_vk_group_member_returns_false_for_non_member_response(self):
        with patch.object(vk_delivery, "_vk_api_request", return_value={"ok": True, "response": 0}):
            self.assertFalse(vk_delivery.check_vk_group_member("123"))

    def test_check_vk_group_member_returns_none_when_not_configured(self):
        settings.VK_GROUP_ACCESS_TOKEN = ""

        self.assertIsNone(vk_delivery.check_vk_group_member("123"))

    def test_user_can_receive_vk_messages_requires_vk_id_and_message_permission(self):
        self.assertFalse(vk_delivery.user_can_receive_vk_messages(SimpleNamespace(vk_user_id=None, vk_messages_allowed=True)))
        self.assertFalse(vk_delivery.user_can_receive_vk_messages(SimpleNamespace(vk_user_id="123", vk_messages_allowed=False)))
        self.assertTrue(vk_delivery.user_can_receive_vk_messages(SimpleNamespace(vk_user_id="123", vk_messages_allowed=True)))

    def test_permission_error_detects_vk_messages_send_denial(self):
        self.assertTrue(vk_delivery.is_vk_message_permission_error({
            "ok": False,
            "error": {"error_code": 901, "error_msg": "Can't send messages for users without permission"},
        }))
        self.assertFalse(vk_delivery.is_vk_message_permission_error({
            "ok": False,
            "error": {"error_code": 5, "error_msg": "Auth failed"},
        }))

    def test_send_vk_message_generates_random_id_with_randint(self):
        with (
            patch.object(vk_delivery.random, "randint", return_value=123456),
            patch.object(vk_delivery, "_vk_api_request", return_value={"ok": True, "response": 1}) as request_mock,
        ):
            result = vk_delivery.send_vk_message(vk_user_id="123", message="Hello")

        self.assertTrue(result["ok"])
        request_mock.assert_called_once()
        method, params = request_mock.call_args.args
        self.assertEqual(method, "messages.send")
        self.assertEqual(params["random_id"], 123456)

    def test_send_vk_message_preserves_explicit_random_id(self):
        with (
            patch.object(vk_delivery.random, "randint", return_value=123456) as randint_mock,
            patch.object(vk_delivery, "_vk_api_request", return_value={"ok": True, "response": 1}) as request_mock,
        ):
            result = vk_delivery.send_vk_message(vk_user_id="123", message="Hello", random_id=777)

        self.assertTrue(result["ok"])
        randint_mock.assert_not_called()
        self.assertEqual(request_mock.call_args.args[1]["random_id"], 777)

    def test_send_vk_message_retries_without_keyboard_when_bot_feature_disabled(self):
        keyboard = {"inline": True, "buttons": []}
        vk_error = {
            "ok": False,
            "description": "This is a chat bot feature, change this status in settings: Chat bot feature",
            "error": {"error_code": 912, "error_msg": "This is a chat bot feature"},
        }
        with (
            patch.object(vk_delivery.random, "randint", side_effect=[111, 222]),
            patch.object(
                vk_delivery,
                "_vk_api_request",
                side_effect=[vk_error, {"ok": True, "response": 1}],
            ) as request_mock,
        ):
            result = vk_delivery.send_vk_message(vk_user_id="123", message="Hello", keyboard=keyboard)

        self.assertTrue(result["ok"])
        self.assertTrue(result["fallback_without_keyboard"])
        self.assertEqual(result["original_error"]["error_code"], 912)
        self.assertEqual(request_mock.call_count, 2)
        self.assertEqual(request_mock.call_args_list[0].args[1]["random_id"], 111)
        self.assertEqual(request_mock.call_args_list[0].args[1]["keyboard"], keyboard)
        self.assertEqual(request_mock.call_args_list[1].args[1]["random_id"], 222)
        self.assertIsNone(request_mock.call_args_list[1].args[1]["keyboard"])

    def test_forecast_keyboard_uses_text_buttons_with_payload(self):
        request_id = UUID("00000000-0000-0000-0000-000000000001")

        keyboard = vk_delivery.build_vk_forecast_keyboard(request_id)

        take_action = keyboard["buttons"][0][0]["action"]
        decline_action = keyboard["buttons"][0][1]["action"]
        self.assertTrue(keyboard["inline"])
        self.assertEqual(take_action["type"], "text")
        self.assertEqual(take_action["label"], "Взять")
        self.assertEqual(
            json.loads(take_action["payload"]),
            {
                "type": "forecast_request",
                "action": "take",
                "request_id": str(request_id),
            },
        )
        self.assertEqual(decline_action["type"], "text")
        self.assertEqual(
            json.loads(decline_action["payload"]),
            {
                "type": "forecast_request",
                "action": "decline",
                "request_id": str(request_id),
            },
        )

    def test_html_to_vk_text_keeps_shamrai_contact_clickable(self):
        text = (
            'Если есть вопросы ✍️ пишите\n'
            '<a href="https://t.me/+OUTzNRDdl9gzNTQy">@Shamrai_Osnova</a>'
        )

        self.assertEqual(
            vk_delivery.html_to_vk_text(text),
            "Если есть вопросы ✍️ пишите\n[https://t.me/+OUTzNRDdl9gzNTQy|@Shamrai_Osnova]",
        )

    def test_mask_secret_hides_middle_of_vk_token(self):
        masked = vk_delivery._mask_secret("abcdef1234567890")

        self.assertEqual(masked, "abcdef**********7890")
        self.assertNotIn("123456", masked)

    def test_probe_vk_api_uses_group_token_safe_membership_probe(self):
        with patch.object(vk_delivery, "_vk_api_request", return_value={"ok": True, "response": 0}) as request_mock:
            self.assertTrue(vk_delivery.probe_vk_api())

        request_mock.assert_called_once_with(
            "groups.isMember",
            {"group_id": 239419819, "user_id": 1},
            timeout=5.0,
        )

    def test_vk_urlopen_bypasses_process_proxy_environment(self):
        request = vk_delivery.urllib.request.Request("https://api.vk.com/method/groups.isMember")
        opener = SimpleNamespace(open=Mock(return_value="response"))

        with (
            patch.object(vk_delivery.urllib.request, "ProxyHandler", return_value="proxy-handler") as proxy_handler,
            patch.object(vk_delivery.urllib.request, "build_opener", return_value=opener) as build_opener,
        ):
            result = vk_delivery._vk_urlopen(request, timeout=7)

        self.assertEqual(result, "response")
        proxy_handler.assert_called_once_with({})
        build_opener.assert_called_once_with("proxy-handler")
        opener.open.assert_called_once_with(request, timeout=7)


class VkDeliveryRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_vk_delivery_status_updates_allowed_from_remote(self):
        user = SimpleNamespace(vk_user_id="123", vk_messages_allowed=False, vk_group_member=False)
        db = SimpleNamespace(commit=AsyncMock())

        with (
            patch.object(vk_delivery, "check_vk_messages_allowed", return_value=True),
            patch.object(vk_delivery, "check_vk_group_member", return_value=True),
        ):
            payload = await vk_delivery.refresh_vk_delivery_status(db, user, commit=True)

        self.assertTrue(user.vk_messages_allowed)
        self.assertTrue(user.vk_group_member)
        self.assertTrue(payload["changed"])
        db.commit.assert_awaited_once()

    async def test_refresh_vk_delivery_status_does_not_downgrade_local_message_grant(self):
        user = SimpleNamespace(vk_user_id="123", vk_messages_allowed=True, vk_group_member=False)
        db = SimpleNamespace(commit=AsyncMock())

        with (
            patch.object(vk_delivery, "check_vk_messages_allowed", return_value=False),
            patch.object(vk_delivery, "check_vk_group_member", return_value=False),
        ):
            payload = await vk_delivery.refresh_vk_delivery_status(db, user, commit=True)

        self.assertTrue(user.vk_messages_allowed)
        self.assertTrue(payload["messages_allowed"])
        self.assertFalse(payload["changed"])
        db.commit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
