import json
import unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from src.api import telegram_webhook, vk_callback
from src.main import app


REQUEST_ID = UUID("00000000-0000-0000-0000-000000000001")


class _FakeDb:
    def __init__(self, user=None):
        self.user = user
        self.commits = 0
        self.rollbacks = 0

    async def get(self, *_args):
        return self.user

    async def execute(self, *_args):
        raise AssertionError("The flat lookup is patched in this test")

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1


class _SessionContext:
    def __init__(self, db):
        self.db = db

    async def __aenter__(self):
        return self.db

    async def __aexit__(self, *_args):
        return False


class FlatChannelCallbackTests(unittest.IsolatedAsyncioTestCase):
    def test_flat_take_routes_are_explicit_in_openapi(self):
        paths = app.openapi()["paths"]

        self.assertIn("/api/bets/{bet_id}/take", paths)
        self.assertIn("/api/signals/forecast-requests/{request_id}/take", paths)

    @staticmethod
    def _flat_user(telegram_id: int):
        return SimpleNamespace(
            telegram_id=telegram_id,
            purchased_bets_balance=0,
            matches_remaining=0,
            guarantee_active=False,
        )

    @staticmethod
    def _active_flat():
        return SimpleNamespace(status="active", flat_amount_rub=Decimal("10000.00"))

    async def test_telegram_take_requests_stake_before_accepting_forecast(self):
        user = self._flat_user(111)
        db = _FakeDb(user)
        with (
            patch.object(telegram_webhook, "get_open_flat_subscription", new=AsyncMock(return_value=self._active_flat())),
            patch.object(telegram_webhook, "start_forecast_stake_input", new=AsyncMock()) as start_input,
            patch.object(telegram_webhook, "set_forecast_request_interested", new=AsyncMock()) as set_interested,
            patch.object(telegram_webhook, "call_telegram_api"),
        ):
            response = await telegram_webhook._handle_forecast_callback(
                {
                    "id": "callback-1",
                    "from": {"id": user.telegram_id},
                    "message": {"chat": {"id": user.telegram_id}},
                    "data": f"forecast:take:{REQUEST_ID}",
                },
                db,
            )

        self.assertEqual(response["text"], "Жду сумму ставки")
        start_input.assert_awaited_once_with(
            db,
            channel="telegram",
            user_id=user.telegram_id,
            forecast_request_id=REQUEST_ID,
        )
        set_interested.assert_not_awaited()
        self.assertEqual(db.commits, 1)

    async def test_vk_take_requests_stake_before_accepting_forecast(self):
        user = self._flat_user(-1000000000123)
        db = _FakeDb(user)
        with (
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(vk_callback, "get_open_flat_subscription", new=AsyncMock(return_value=self._active_flat())),
            patch.object(vk_callback, "start_forecast_stake_input", new=AsyncMock()) as start_input,
            patch.object(vk_callback, "set_forecast_request_interested", new=AsyncMock()) as set_interested,
        ):
            response = await vk_callback._handle_forecast_button(
                {
                    "message": {
                        "from_id": 123,
                        "peer_id": 123,
                        "payload": json.dumps({
                            "type": "forecast_request",
                            "action": "take",
                            "request_id": str(REQUEST_ID),
                        }),
                    }
                },
                db,
            )

        self.assertEqual(response["status"], "awaiting_stake")
        start_input.assert_awaited_once_with(
            db,
            channel="vk",
            user_id=user.telegram_id,
            forecast_request_id=REQUEST_ID,
        )
        set_interested.assert_not_awaited()
        self.assertEqual(db.commits, 1)

    async def test_telegram_expired_stake_session_is_explained(self):
        db = _FakeDb()
        with (
            patch.object(telegram_webhook, "AsyncSessionLocal", return_value=_SessionContext(db)),
            patch.object(
                telegram_webhook,
                "lookup_forecast_stake_input",
                new=AsyncMock(return_value=SimpleNamespace(session=None, expired=True)),
            ),
        ):
            response = await telegram_webhook._handle_pending_telegram_stake(
                {"text": "5000", "chat": {"id": 111}},
                111,
            )

        self.assertIn("истекло", response["text"].lower())
        self.assertIn("взять", response["text"].lower())
        self.assertEqual(db.commits, 1)

    async def test_telegram_terminal_stake_error_clears_session(self):
        db = _FakeDb()
        pending = SimpleNamespace(forecast_request_id=REQUEST_ID)
        with (
            patch.object(telegram_webhook, "AsyncSessionLocal", return_value=_SessionContext(db)),
            patch.object(
                telegram_webhook,
                "lookup_forecast_stake_input",
                new=AsyncMock(return_value=SimpleNamespace(session=pending, expired=False)),
            ),
            patch.object(
                telegram_webhook,
                "set_forecast_request_interested",
                new=AsyncMock(side_effect=telegram_webhook.HTTPException(status_code=409, detail="Прогноз уже закрыт")),
            ),
            patch.object(telegram_webhook, "clear_forecast_stake_input", new=AsyncMock()) as clear_input,
        ):
            response = await telegram_webhook._handle_pending_telegram_stake(
                {"text": "5000", "chat": {"id": 111}},
                111,
            )

        clear_input.assert_awaited_once_with(db, channel="telegram", user_id=111)
        self.assertIn("нажмите", response["text"].lower())
        self.assertEqual(db.rollbacks, 1)
        self.assertEqual(db.commits, 1)

    async def test_telegram_non_finite_stake_returns_validation_message(self):
        db = _FakeDb()
        pending = SimpleNamespace(forecast_request_id=REQUEST_ID)
        with (
            patch.object(telegram_webhook, "AsyncSessionLocal", return_value=_SessionContext(db)),
            patch.object(
                telegram_webhook,
                "lookup_forecast_stake_input",
                new=AsyncMock(return_value=SimpleNamespace(session=pending, expired=False)),
            ),
            patch.object(telegram_webhook, "set_forecast_request_interested", new=AsyncMock()) as interested,
        ):
            response = await telegram_webhook._handle_pending_telegram_stake(
                {"text": "NaN", "chat": {"id": 111}},
                111,
            )

        self.assertIn("корректную сумму", response["text"].lower())
        interested.assert_not_awaited()
        self.assertEqual(db.rollbacks, 1)

    async def test_vk_expired_stake_session_is_explained(self):
        user = self._flat_user(-1000000000123)
        db = _FakeDb(user)
        event = {"message": {"from_id": 123, "peer_id": 123, "text": "5000"}}
        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=_SessionContext(db)),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(
                vk_callback,
                "lookup_forecast_stake_input",
                new=AsyncMock(return_value=SimpleNamespace(session=None, expired=True)),
            ),
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            handled = await vk_callback._process_pending_vk_stake_message(event)

        self.assertTrue(handled)
        self.assertIn("истекло", send_message.call_args.args[1].lower())
        self.assertEqual(db.commits, 1)

    async def test_vk_non_finite_stake_returns_validation_message(self):
        user = self._flat_user(-1000000000123)
        db = _FakeDb(user)
        event = {"message": {"from_id": 123, "peer_id": 123, "text": "Infinity"}}
        pending = SimpleNamespace(forecast_request_id=REQUEST_ID)
        with (
            patch.object(vk_callback, "AsyncSessionLocal", return_value=_SessionContext(db)),
            patch.object(vk_callback, "_load_user_by_vk_id", new=AsyncMock(return_value=user)),
            patch.object(
                vk_callback,
                "lookup_forecast_stake_input",
                new=AsyncMock(return_value=SimpleNamespace(session=pending, expired=False)),
            ),
            patch.object(vk_callback, "set_forecast_request_interested", new=AsyncMock()) as interested,
            patch.object(vk_callback, "_send_forecast_button_message") as send_message,
        ):
            handled = await vk_callback._process_pending_vk_stake_message(event)

        self.assertTrue(handled)
        self.assertIn("корректную сумму", send_message.call_args.args[1].lower())
        interested.assert_not_awaited()
        self.assertEqual(db.rollbacks, 1)


if __name__ == "__main__":
    unittest.main()
