import asyncio
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import BackgroundTasks

from src.api import signals as signals_api
from src.models.models import DeliveryOutbox, PersonalSignal, User
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, CHANNEL_WEB_PUSH_SIGNAL
from src.services import forecast_delivery, signals


class FakeDb:
    def __init__(self):
        self.added = []
        self.next_id = 42
        self.commit_count = 0
        self.events = []

    def add(self, value):
        self.added.append(value)

    def add_all(self, values):
        self.added.extend(values)

    async def flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = self.next_id
                self.next_id += 1

    async def commit(self):
        self.commit_count += 1
        self.events.append(("commit", self.commit_count))


class SignalDeliveryTests(unittest.TestCase):
    def test_websocket_delivery_runs_before_external_background_delivery(self):
        events = []
        db = FakeDb()
        user = User(
            telegram_id=12345,
            web_push_subscription={"endpoint": "https://push.example", "keys": {}},
        )

        async def fake_send_to_user(user_id, payload):
            events.append(("websocket", user_id, payload["text"]))

        with patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user):
            signal = asyncio.run(
                signals.deliver_personal_signal(
                    db,
                    user=user,
                    text="fast signal",
                )
            )

        self.assertEqual(signal.id, 42)
        self.assertEqual(events[0], ("websocket", 12345, "fast signal"))
        outbox_items = [item for item in db.added if isinstance(item, DeliveryOutbox)]
        self.assertEqual([item.channel for item in outbox_items], [CHANNEL_TELEGRAM_MESSAGE, CHANNEL_WEB_PUSH_SIGNAL])
        self.assertEqual(outbox_items[0].payload["payload"]["text"], "fast signal")
        self.assertEqual(outbox_items[1].payload["signal_payload"]["text"], "fast signal")

    def test_broadcast_personal_signals_fans_out_to_web_clients(self):
        events = []
        db = FakeDb()
        users = [
            User(telegram_id=111, web_push_subscription={"endpoint": "https://push.example/1", "keys": {}}),
            User(telegram_id=222, web_push_subscription={"endpoint": "https://push.example/2", "keys": {}}),
        ]

        async def fake_send_to_user(user_id, payload):
            events.append(("websocket", user_id, payload["id"], payload["type"], payload["text"]))

        with patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user):
            sent = asyncio.run(
                signals.broadcast_personal_signals(
                    db,
                    users=users,
                    text="Закрытый анонс прогноза",
                    signal_type="forecast_teaser",
                    send_telegram=False,
                )
            )

        self.assertEqual(sent, 2)
        signal_rows = [item for item in db.added if isinstance(item, PersonalSignal)]
        outbox_items = [item for item in db.added if isinstance(item, DeliveryOutbox)]
        self.assertEqual([item.user_id for item in signal_rows], [111, 222])
        self.assertEqual([item.user_id for item in outbox_items], [111, 222])
        self.assertEqual([item.channel for item in outbox_items], [CHANNEL_WEB_PUSH_SIGNAL, CHANNEL_WEB_PUSH_SIGNAL])
        self.assertEqual(events[0], ("websocket", 111, 42, "forecast_teaser", "Закрытый анонс прогноза"))
        self.assertEqual(events[1], ("websocket", 222, 43, "forecast_teaser", "Закрытый анонс прогноза"))

    def test_web_push_payload_uses_root_static_image_url_and_signal_metadata(self):
        signal_payload = {
            "id": 77,
            "type": "forecast_full",
            "text": "Матч: Team A - Team B\nКэф 2.10",
            "data": {
                "forecast_request_id": "request-77",
                "coupon_image_url": "/static/coupons/coupon.png",
                "message_text": "Прогноз готов\nМатч: Team A - Team B",
            },
        }

        with (
            patch.object(signals.settings, "API_BASE_URL", "https://shamra1.pro"),
            patch.object(signals.settings, "FRONTEND_BASE_URL", "https://shamra1.pro/app"),
        ):
            payload = json.loads(signals._web_push_notification_payload(signal_payload))

        self.assertEqual(payload["title"], "Прогноз готов")
        self.assertEqual(payload["url"], "https://shamra1.pro/app?open=web-bot-chat")
        self.assertEqual(payload["image"], "https://shamra1.pro/static/coupons/coupon.png")
        self.assertEqual(payload["signal_id"], 77)
        self.assertEqual(payload["type"], "forecast_full")
        self.assertEqual(payload["forecast_request_id"], "request-77")
        self.assertEqual(payload["data"]["forecast_request_id"], "request-77")

    def test_report_mode_can_commit_web_signals_before_external_push(self):
        db = FakeDb()
        dispatch_events = []
        users = [
            User(telegram_id=-111, web_push_subscription={"endpoint": "https://push.example/1", "keys": {}}),
        ]

        async def fake_send_to_user(user_id, payload):
            dispatch_events.append(("websocket", db.commit_count, user_id, payload["id"]))

        async def fake_external_delivery(deliveries):
            signal_count = len([item for item in db.added if isinstance(item, PersonalSignal)])
            dispatch_events.append(("external", db.commit_count, signal_count, len(deliveries)))
            return [{"web_push": {"ok": True}} for _ in deliveries]

        with (
            patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user),
            patch.object(signals, "dispatch_signal_external_delivery_batch", fake_external_delivery),
        ):
            report = asyncio.run(
                signals.broadcast_personal_signals(
                    db,
                    users=users,
                    text="Важное сообщение",
                    signal_type="announcement",
                    send_telegram=False,
                    send_web_push=True,
                    return_report=True,
                    commit_before_external_delivery=True,
                )
            )

        self.assertEqual(report["created"], 1)
        self.assertEqual(report["web_push_sent"], 1)
        self.assertEqual(db.commit_count, 1)
        self.assertEqual(dispatch_events[0], ("websocket", 0, -111, 42))
        self.assertEqual(dispatch_events[1], ("external", 1, 1, 1))

    def test_report_mode_queues_retry_for_failed_web_push(self):
        db = FakeDb()
        users = [
            User(telegram_id=-111, web_push_subscription={"endpoint": "https://push.example/1", "keys": {}}),
        ]

        async def fake_send_to_user(user_id, payload):
            return None

        async def fake_external_delivery(deliveries):
            return [{"web_push": {"ok": False, "description": "temporary timeout", "status_code": 503}}]

        with (
            patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user),
            patch.object(signals, "dispatch_signal_external_delivery_batch", fake_external_delivery),
        ):
            report = asyncio.run(
                signals.broadcast_personal_signals(
                    db,
                    users=users,
                    text="Повторить push",
                    signal_type="announcement",
                    send_telegram=False,
                    send_web_push=True,
                    return_report=True,
                    commit_before_external_delivery=True,
                )
            )

        retry_items = [item for item in db.added if isinstance(item, DeliveryOutbox)]
        self.assertEqual(report["web_push_failed"], 1)
        self.assertEqual(report["web_push_retry_queued"], 1)
        self.assertEqual(db.commit_count, 2)
        self.assertEqual(len(retry_items), 1)
        self.assertEqual(retry_items[0].channel, CHANNEL_WEB_PUSH_SIGNAL)
        self.assertEqual(retry_items[0].dedupe_key, "personal_signal:42:web_push")

    def test_history_serializer_enriches_old_forecast_signal_data(self):
        request_id = uuid4()
        bet_id = uuid4()
        bookmaker = SimpleNamespace(id=1, name="Фонбет", code="fonbet")
        bet = SimpleNamespace(
            id=bet_id,
            event_name="валера-вася",
            outcome="П1",
            coefficient=Decimal("4.00"),
            description=None,
            sport_type="Футбол",
            coupon_image_url="/static/coupons/coupon.png",
            match_link=None,
            bookmaker=bookmaker,
            bookmaker_id=bookmaker.id,
            bookmakers=[bookmaker],
            bookmaker_links=[
                {"bookmaker_id": bookmaker.id, "url": "fonbet.ru/sports/football/12313"},
            ],
        )
        forecast_request = SimpleNamespace(
            id=request_id,
            status="sent",
            bet_id=bet_id,
            bet=bet,
        )
        signal = SimpleNamespace(
            id=77,
            user_id=12345,
            text="Матч: валера-вася\n\nИсход: П1\n\nКоэффициент: 4.00",
            type="forecast_full",
            data={"forecast_request_id": str(request_id)},
            created_at=datetime(2026, 6, 9, tzinfo=timezone.utc),
        )

        payload = signals_api._serialize_signal_with_forecast_data(
            signal,
            {str(request_id): forecast_request},
        )

        self.assertEqual(payload.data["forecast_status"], "sent")
        self.assertEqual(payload.data["coupon_image_url"], "/static/coupons/coupon.png")
        self.assertEqual(payload.data["bookmakers"][0]["name"], "Фонбет")
        self.assertEqual(payload.data["bookmakers"][0]["url"], "https://fonbet.ru/sports/football/12313")
        self.assertEqual(payload.data["event_name"], "валера-вася")

    def test_web_take_without_full_access_returns_contact_required_payload(self):
        async def run_check():
            request_id = uuid4()
            forecast_request = SimpleNamespace(
                id=request_id,
                status=forecast_delivery.FORECAST_STATUS_ANNOUNCED,
            )

            class FakeDb:
                async def commit(self):
                    return None

            with patch.object(
                signals_api,
                "set_forecast_request_interested",
                new=AsyncMock(return_value=(
                    forecast_request,
                    f"Чтобы получить ставку, напишите: {forecast_delivery.FORECAST_CONTACT_DRAFT_TEXT}",
                    False,
                )),
            ):
                return await signals_api.answer_forecast_request_from_web_chat(
                    request_id,
                    "take",
                    BackgroundTasks(),
                    current_user=SimpleNamespace(telegram_id=12345),
                    db=FakeDb(),
                )

        response = asyncio.run(run_check())

        self.assertEqual(response.action, "contact_required")
        self.assertEqual(response.status, forecast_delivery.FORECAST_STATUS_ANNOUNCED)
        self.assertEqual(response.contact["channel"], "web")
        self.assertEqual(response.contact["draft_text"], forecast_delivery.FORECAST_CONTACT_DRAFT_TEXT)


if __name__ == "__main__":
    unittest.main()
