import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

from fastapi import HTTPException

from src.api import vk_callback
from src.core.config import settings
from src.services import forecast_delivery


class FakeRequest:
    def __init__(self, payload):
        self.payload = payload

    async def json(self):
        return self.payload


class VkCallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_values = {
            "APP_ENV": settings.APP_ENV,
            "VK_GROUP_ID": settings.VK_GROUP_ID,
            "VK_CALLBACK_CONFIRMATION_CODE": settings.VK_CALLBACK_CONFIRMATION_CODE,
            "VK_CALLBACK_SECRET": settings.VK_CALLBACK_SECRET,
        }
        settings.APP_ENV = "local"
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

    async def test_message_events_reject_missing_server_secret(self):
        settings.VK_CALLBACK_SECRET = ""

        with self.assertRaises(HTTPException) as exc:
            await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 239419819,
                        "object": {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}},
                    }
                )
            )

        self.assertEqual(exc.exception.status_code, 503)

    async def test_message_events_reject_missing_server_secret_in_production(self):
        settings.APP_ENV = "production"
        settings.VK_CALLBACK_SECRET = ""

        with self.assertRaises(HTTPException) as exc:
            await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 239419819,
                        "object": {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}},
                    }
                )
            )

        self.assertEqual(exc.exception.status_code, 503)

    async def test_confirmation_accepts_group_mismatch_to_keep_vk_dashboard_confirmable(self):
        response = await vk_callback.vk_callback(
            FakeRequest(
                {
                    "type": "confirmation",
                    "group_id": 1,
                    "secret": "expected-secret",
                }
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.media_type, "text/plain")
        self.assertEqual(response.body.decode("utf-8"), "confirmation-code")

    async def test_message_events_reject_bad_group_id(self):
        with self.assertRaises(HTTPException) as exc:
            await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_new",
                        "group_id": 1,
                        "secret": "expected-secret",
                        "object": {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}},
                    }
                )
            )

        self.assertEqual(exc.exception.status_code, 403)

    async def test_message_allow_returns_ok_and_schedules_permission_update(self):
        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)
            coro.close()

        with patch.object(vk_callback, "_run_background", side_effect=fake_run_background) as run_background:
            response = await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_allow",
                        "group_id": 239419819,
                        "secret": "expected-secret",
                        "object": {"user_id": 123},
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body.decode("utf-8"), "ok")
        run_background.assert_called_once()
        self.assertEqual(len(created_tasks), 1)

    async def test_message_permission_callback_marks_user_allowed(self):
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

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=fake_session),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)) as load_user,
        ):
            await vk_callback._set_message_permission_from_callback({"user_id": 123}, allowed=True)

        load_user.assert_awaited_once_with(fake_session, 123)
        self.assertTrue(user.vk_messages_allowed)
        self.assertEqual(fake_session.commits, 1)

    async def test_message_permission_callback_marks_user_denied(self):
        user = SimpleNamespace(vk_user_id="123", vk_messages_allowed=True)

        class FakeSession:
            commits = 0

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

            async def commit(self):
                self.commits += 1

        fake_session = FakeSession()

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=fake_session),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
        ):
            await vk_callback._set_message_permission_from_callback({"user_id": 123}, allowed=False)

        self.assertFalse(user.vk_messages_allowed)
        self.assertEqual(fake_session.commits, 1)

    async def test_message_new_logs_and_returns_ok_plain_text(self):
        with (
            patch.object(vk_callback.logger, "info") as log_info,
            patch.object(vk_callback, "_run_background") as run_background,
            patch.object(vk_callback, "_refresh_message_permission_from_message_new", new=Mock(return_value=object())) as refresh_permission,
            patch.object(vk_callback, "_process_plain_text_status_message", new=Mock(return_value=object())) as process_status,
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
        refresh_permission.assert_called_once()
        process_status.assert_called_once()
        self.assertEqual(run_background.call_count, 2)

    async def test_message_new_forecast_payload_is_processed(self):
        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)

        request_id = "00000000-0000-0000-0000-000000000001"
        with (
            patch.object(vk_callback, "_run_background", side_effect=fake_run_background) as run_background,
            patch.object(vk_callback, "_refresh_message_permission_from_message_new", new=Mock(return_value=object())) as refresh_permission,
            patch.object(vk_callback, "_process_forecast_button_event", new=Mock(return_value=object())) as process_event,
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
                                "text": "Взять",
                                "payload": json.dumps(
                                    {
                                        "type": "forecast_request",
                                        "action": "take",
                                        "request_id": request_id,
                                    }
                                ),
                            }
                        },
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body.decode("utf-8"), "ok")
        refresh_permission.assert_called_once()
        process_event.assert_called_once()
        self.assertEqual(run_background.call_count, 2)
        self.assertEqual(len(created_tasks), 2)

    async def test_message_new_plain_text_forecast_is_processed(self):
        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)

        with (
            patch.object(vk_callback, "_run_background", side_effect=fake_run_background) as run_background,
            patch.object(vk_callback, "_refresh_message_permission_from_message_new", new=Mock(return_value=object())) as refresh_permission,
            patch.object(vk_callback, "_process_plain_text_forecast_message", new=Mock(return_value=object())) as process_text,
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
                                "text": "Взять",
                            }
                        },
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body.decode("utf-8"), "ok")
        refresh_permission.assert_called_once()
        process_text.assert_called_once()
        self.assertEqual(run_background.call_count, 2)
        self.assertEqual(len(created_tasks), 2)

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
            patch.object(vk_callback, "AsyncSessionLocal", return_value=fake_session),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(vk_callback, "refresh_vk_delivery_status", new=AsyncMock(side_effect=fake_refresh)),
        ):
            await vk_callback._refresh_message_permission_from_message_new(
                {
                    "message": {
                        "from_id": 123,
                        "peer_id": 123,
                        "text": "Привет",
                    }
                }
            )

        self.assertTrue(user.vk_messages_allowed)
        self.assertEqual(fake_session.commits, 1)

    async def test_message_new_marks_allowed_even_when_remote_check_still_denies(self):
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

        async def fake_refresh(_db, _refreshed_user, refresh_group=False):
            return {"remote_messages_checked": True, "messages_allowed": False}

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=fake_session),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(vk_callback, "refresh_vk_delivery_status", new=AsyncMock(side_effect=fake_refresh)),
        ):
            await vk_callback._refresh_message_permission_from_message_new(
                {
                    "message": {
                        "from_id": 123,
                        "peer_id": 123,
                        "text": "Проверка",
                    }
                }
            )

        self.assertTrue(user.vk_messages_allowed)
        self.assertEqual(fake_session.commits, 1)

    async def test_message_event_returns_ok_before_processing_forecast_button(self):
        created_tasks = []

        def fake_run_background(coro):
            created_tasks.append(coro)

        with (
            patch.object(vk_callback, "_run_background", side_effect=fake_run_background) as run_background,
            patch.object(vk_callback, "_answer_forecast_button_event") as answer_event,
            patch.object(vk_callback, "_process_forecast_button_event", new=Mock(return_value=object())) as process_event,
        ):
            response = await vk_callback.vk_callback(
                FakeRequest(
                    {
                        "type": "message_event",
                        "group_id": 239419819,
                        "secret": "expected-secret",
                        "object": {
                            "user_id": 123,
                            "peer_id": 123,
                            "event_id": "event-1",
                            "payload": "{\"type\":\"forecast_request\",\"action\":\"take\",\"request_id\":\"00000000-0000-0000-0000-000000000001\"}",
                        },
                    }
                )
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body.decode("utf-8"), "ok")
        answer_event.assert_called_once()
        process_event.assert_called_once()
        run_background.assert_called_once()
        self.assertEqual(len(created_tasks), 1)

    def test_decode_button_payload_accepts_nested_and_text_payloads(self):
        request_id = "00000000-0000-0000-0000-000000000001"

        nested_payload = json.dumps(
            {
                "payload": json.dumps(
                    {
                        "type": "forecast_request",
                        "action": "take",
                        "request_id": request_id,
                    }
                )
            }
        )

        self.assertEqual(
            vk_callback._decode_button_payload(nested_payload),
            {
                "type": "forecast_request",
                "action": "take",
                "request_id": request_id,
            },
        )
        self.assertEqual(
            vk_callback._decode_button_payload(f"forecast:decline:{request_id}"),
            {
                "type": "forecast_request",
                "action": "decline",
                "request_id": request_id,
            },
        )

    async def test_plain_text_take_single_active_forecast_is_processed(self):
        request_id = UUID("00000000-0000-0000-0000-000000000001")
        user = SimpleNamespace(telegram_id=-1000000000123)
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Взять"}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)) as load_user,
            patch.object(
                vk_callback,
                "_find_announced_forecast_request_ids_for_user",
                new=AsyncMock(return_value=[request_id]),
            ) as find_requests,
            patch.object(
                vk_callback,
                "_handle_forecast_button",
                new=AsyncMock(return_value={"status": "ok", "message": "Принято. Готовим прогноз."}),
            ) as handle_button,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_forecast_message(event_object)

        load_user.assert_awaited_once()
        find_requests.assert_awaited_once()
        handle_button.assert_awaited_once()
        synthetic_event = handle_button.await_args.args[0]
        self.assertEqual(handle_button.await_args.args[1].__class__.__name__, "FakeSession")
        self.assertEqual(synthetic_event["message"]["from_id"], 123)
        payload = json.loads(synthetic_event["message"]["payload"])
        self.assertEqual(payload["type"], "forecast_request")
        self.assertEqual(payload["action"], "take")
        self.assertEqual(payload["request_id"], str(request_id))
        send_message.assert_called_once_with(event_object, "Принято. Готовим прогноз.")

    async def test_plain_text_decline_single_active_forecast_is_processed(self):
        request_id = UUID("00000000-0000-0000-0000-000000000002")
        user = SimpleNamespace(telegram_id=-1000000000123)
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Не взять"}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(
                vk_callback,
                "_find_announced_forecast_request_ids_for_user",
                new=AsyncMock(return_value=[request_id]),
            ),
            patch.object(
                vk_callback,
                "_handle_forecast_button",
                new=AsyncMock(return_value={"status": "ok", "message": "Ок, не берем."}),
            ) as handle_button,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_forecast_message(event_object)

        handle_button.assert_awaited_once()
        payload = json.loads(handle_button.await_args.args[0]["message"]["payload"])
        self.assertEqual(payload["action"], "decline")
        self.assertEqual(payload["request_id"], str(request_id))
        send_message.assert_called_once_with(event_object, "Ок, не берем.")

    async def test_plain_text_forecast_with_no_active_requests_sends_explanation(self):
        user = SimpleNamespace(telegram_id=-1000000000123)
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Беру"}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(
                vk_callback,
                "_find_announced_forecast_request_ids_for_user",
                new=AsyncMock(return_value=[]),
            ),
            patch.object(vk_callback, "_handle_forecast_button", new=AsyncMock()) as handle_button,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_forecast_message(event_object)

        handle_button.assert_not_awaited()
        send_message.assert_called_once_with(event_object, vk_callback.NO_ACTIVE_FORECAST_MESSAGE)

    async def test_plain_text_forecast_with_multiple_active_requests_does_not_choose_randomly(self):
        user = SimpleNamespace(telegram_id=-1000000000123)
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "take"}}
        request_ids = [
            UUID("00000000-0000-0000-0000-000000000001"),
            UUID("00000000-0000-0000-0000-000000000002"),
        ]

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(
                vk_callback,
                "_find_announced_forecast_request_ids_for_user",
                new=AsyncMock(return_value=request_ids),
            ),
            patch.object(vk_callback, "_handle_forecast_button", new=AsyncMock()) as handle_button,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_forecast_message(event_object)

        handle_button.assert_not_awaited()
        send_message.assert_called_once_with(event_object, vk_callback.MULTIPLE_ACTIVE_FORECASTS_MESSAGE)

    async def test_plain_text_status_message_replies_for_linked_user(self):
        user = SimpleNamespace(telegram_id=-1000000000123)
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)) as load_user,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_status_message(event_object)

        load_user.assert_awaited_once()
        send_message.assert_called_once_with(event_object, vk_callback.VK_DEFAULT_REPLY_MESSAGE)

    async def test_plain_text_status_message_replies_for_unlinked_user(self):
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Привет"}}

        class FakeSession:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=None)) as load_user,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_plain_text_status_message(event_object)

        load_user.assert_awaited_once()
        send_message.assert_called_once_with(event_object, vk_callback.VK_PROFILE_NOT_LINKED_MESSAGE)

    async def test_plain_text_status_message_ignores_forecast_text(self):
        event_object = {"message": {"from_id": 123, "peer_id": 123, "text": "Взять"}}

        with patch.object(vk_callback, "_send_forecast_button_message") as send_message:
            await vk_callback._process_plain_text_status_message(event_object)

        send_message.assert_not_called()

    async def test_handle_forecast_button_returns_contact_required_with_vk_dialog_link(self):
        request_id = UUID("00000000-0000-0000-0000-000000000001")
        fake_user = SimpleNamespace(telegram_id=-1000000000123)
        fake_forecast_request = SimpleNamespace(
            id=request_id,
            status=forecast_delivery.FORECAST_STATUS_ANNOUNCED,
        )

        class FakeDb:
            commits = 0
            rollbacks = 0

            async def commit(self):
                self.commits += 1

            async def rollback(self):
                self.rollbacks += 1

        fake_db = FakeDb()

        with (
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=fake_user)),
            patch.object(
                vk_callback,
                "set_forecast_request_interested",
                new=AsyncMock(return_value=(
                    fake_forecast_request,
                    f"Чтобы получить ставку, напишите: {forecast_delivery.FORECAST_CONTACT_DRAFT_TEXT}",
                    False,
                )),
            ),
        ):
            result = await vk_callback._handle_forecast_button(
                {
                    "message": {
                        "from_id": 123,
                        "peer_id": 123,
                        "payload": json.dumps(
                            {
                                "type": "forecast_request",
                                "action": "take",
                                "request_id": str(request_id),
                            }
                        ),
                    }
                },
                fake_db,
            )

        self.assertEqual(result["status"], "contact_required")
        self.assertIn(forecast_delivery.FORECAST_CONTACT_DRAFT_TEXT, result["message"])
        self.assertIn("https://vk.me/club239419819", result["message"])
        self.assertEqual(fake_db.commits, 1)
        self.assertEqual(fake_db.rollbacks, 0)

    async def test_process_inactive_forecast_button_answers_without_chat_duplicate(self):
        expected_message = "Прогноз уже не активен. Реагировать не нужно."
        event_object = {
            "event_id": "event-1",
            "user_id": 123,
            "peer_id": 123,
            "payload": json.dumps(
                {
                    "type": "forecast_request",
                    "action": "take",
                    "request_id": "00000000-0000-0000-0000-000000000001",
                }
            ),
        }

        class FakeSession:
            async def __aenter__(self):
                return object()

            async def __aexit__(self, *_args):
                return False

        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=FakeSession()),
            patch.object(
                vk_callback,
                "_handle_forecast_button",
                new=AsyncMock(return_value={"status": "inactive", "message": expected_message}),
            ),
            patch.object(vk_callback, "_answer_forecast_button_event") as answer_event,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            await vk_callback._process_forecast_button_event(event_object)

        answer_event.assert_called_once_with(event_object, expected_message)
        send_message.assert_not_called()

    async def test_handle_forecast_button_does_not_schedule_inline_auto_delivery_after_take(self):
        request_id = UUID("00000000-0000-0000-0000-000000000001")
        fake_user = SimpleNamespace(telegram_id=-1000000000123)
        fake_forecast_request = SimpleNamespace(id=request_id)
        created_tasks = []

        class FakeDb:
            commits = 0
            rollbacks = 0

            async def commit(self):
                self.commits += 1

            async def rollback(self):
                self.rollbacks += 1

        def fake_run_background(coro):
            created_tasks.append(coro)
            coro.close()

        fake_db = FakeDb()

        with (
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=fake_user)) as load_user,
            patch.object(
                vk_callback,
                "set_forecast_request_interested",
                new=AsyncMock(return_value=(fake_forecast_request, "Принято. Готовим прогноз.", False)),
            ) as set_interested,
            patch.object(vk_callback, "_run_background", side_effect=fake_run_background) as run_background,
            patch.object(vk_callback, "notify_sales_manager_for_request") as notify_sales,
        ):
            result = await vk_callback._handle_forecast_button(
                {
                    "message": {
                        "from_id": 123,
                        "peer_id": 123,
                        "payload": json.dumps(
                            {
                                "payload": json.dumps(
                                    {
                                        "type": "forecast_request",
                                        "action": "take",
                                        "request_id": str(request_id),
                                    }
                                )
                            }
                        ),
                    }
                },
                fake_db,
            )

        self.assertEqual(result, {"status": "ok", "message": "Принято. Готовим прогноз."})
        load_user.assert_awaited_once_with(fake_db, 123)
        set_interested.assert_awaited_once()
        self.assertEqual(set_interested.call_args.kwargs["request_id"], request_id)
        self.assertEqual(set_interested.call_args.kwargs["actor_user_id"], fake_user.telegram_id)
        self.assertFalse(hasattr(vk_callback, "auto_deliver_forecast_request_for_request"))
        run_background.assert_not_called()
        notify_sales.assert_not_called()
        self.assertEqual(created_tasks, [])
        self.assertEqual(fake_db.commits, 1)
        self.assertEqual(fake_db.rollbacks, 0)


if __name__ == "__main__":
    unittest.main()
