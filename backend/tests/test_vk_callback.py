import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from src.api import vk_callback
from src.core.config import settings


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class VkCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_values = {
            "VK_GROUP_ID": settings.VK_GROUP_ID,
            "VK_CALLBACK_CONFIRMATION_CODE": settings.VK_CALLBACK_CONFIRMATION_CODE,
            "VK_CALLBACK_SECRET": settings.VK_CALLBACK_SECRET,
        }
        settings.VK_GROUP_ID = "239419819"
        settings.VK_CALLBACK_CONFIRMATION_CODE = "confirmation-code"
        settings.VK_CALLBACK_SECRET = "expected-secret"

    def tearDown(self):
        for key, value in self.previous_values.items():
            setattr(settings, key, value)

    async def test_confirmation_returns_plain_text_code(self):
        response = await vk_callback.vk_callback(
            FakeRequest(
                {
                    "type": "confirmation",
                    "group_id": 239419819,
                    "secret": "expected-secret",
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.body.decode("utf-8"), "confirmation-code")

    async def test_confirmation_accepts_vk_dashboard_payload_without_secret(self):
        response = await vk_callback.vk_callback(
            FakeRequest(
                {
                    "type": "confirmation",
                    "group_id": 239419819,
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.body.decode("utf-8"), "confirmation-code")

    async def test_message_events_reject_bad_secret(self):
        with self.assertRaises(HTTPException) as exc:
            await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 239419819,
                        "secret": "wrong-secret",
                        "object": {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}},
                    }
                )
            )

        self.assertEqual(exc.exception.status_code, 403)

    async def test_rejects_bad_group_id(self):
        with self.assertRaises(HTTPException) as exc:
            await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "confirmation",
                        "group_id": 1,
                        "secret": "expected-secret",
                    }
                )
            )

        self.assertEqual(exc.exception.status_code, 403)

    async def test_message_new_logs_and_returns_ok_plain_text(self):
        with (
            patch.object(vk_callback.logger, "info") as log_info,
            patch.object(vk_callback, "_refresh_message_permission_from_message_new", new=AsyncMock()),
        ):
            response = await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 239419819,
                        "secret": "expected-secret",
                        "object": {
                            "message": {
                                "from_id": 123,
                                "peer_id": 123,
                                "text": "Привет",
                            }
                        },
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.body.decode("utf-8"), "ok")
        log_info.assert_called_once()

    async def test_message_new_refreshes_linked_user_message_permission(self):
        user = SimpleNamespace(vk_user_id="123", vk_messages_allowed=False)

        class FakeSession:
            commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()

        async def fake_refresh(_db, refreshed_user, refresh_group=False):
            refreshed_user.vk_messages_allowed = True
            return {"remote_messages_checked": True, "messages_allowed": True}

        with (
            patch.object(vk_callback.logger, "info"),
            patch.object(vk_callback, "AsyncSessionLocal", return_value=fake_session),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(vk_callback, "refresh_vk_delivery_status", new=AsyncMock(side_effect=fake_refresh)),
        ):
            response = await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 239419819,
                        "secret": "expected-secret",
                        "object": {
                            "message": {
                                "from_id": 123,
                                "peer_id": 123,
                                "text": "Привет",
                            }
                        },
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(user.vk_messages_allowed)
        self.assertEqual(fake_session.commits, 1)


if __name__ == "__main__":
    unittest.main()
