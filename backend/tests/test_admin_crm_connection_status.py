import unittest

from src.api.admin import build_admin_user_response
from src.models.models import User


class AdminCrmConnectionStatusTests(unittest.TestCase):
    def test_admin_user_response_exposes_connection_status_flags(self):
        user = User(
            telegram_id=123456789,
            username="client",
            first_name="Client",
            last_name="Connected",
            role="user",
            stats_display_mode="percent",
            bankroll=0,
            vk_user_id="vk-123",
            vk_group_member=True,
            vk_messages_allowed=True,
            vk_notifications_allowed=False,
            tg_chat_joined=True,
            web_push_subscription=None,
            purchased_bets_balance=0,
            matches_remaining=0,
            guarantee_active=False,
        )
        user.bookmakers = []
        user.badges = []

        payload = build_admin_user_response(user)

        self.assertTrue(payload["telegram_connected"])
        self.assertTrue(payload["telegram_delivery_enabled"])
        self.assertTrue(payload["vk_connected"])
        self.assertTrue(payload["vk_delivery_enabled"])
        self.assertFalse(payload["web_push_enabled"])
        self.assertEqual(payload["identity_providers"], ["telegram", "vk"])
        self.assertEqual(payload["missing_identity_providers"], [])
