import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from src.api import admin_broadcast


class AdminBroadcastVkTests(unittest.IsolatedAsyncioTestCase):
    async def test_refresh_vk_audience_keeps_user_after_remote_allow(self):
        user = SimpleNamespace(
            telegram_id=123,
            vk_user_id="456",
            vk_messages_allowed=False,
        )
        db = SimpleNamespace(commit=AsyncMock())

        async def fake_refresh(_db, refreshed_user, refresh_group=False):
            refreshed_user.vk_messages_allowed = True
            return {"changed": True}

        original_refresh = admin_broadcast.refresh_vk_delivery_status
        try:
            admin_broadcast.refresh_vk_delivery_status = fake_refresh
            audience = await admin_broadcast._refresh_vk_audience(db, [user])
        finally:
            admin_broadcast.refresh_vk_delivery_status = original_refresh

        self.assertEqual(audience, [user])
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
