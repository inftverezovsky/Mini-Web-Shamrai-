import asyncio
import json
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from src.api import signals as signals_api
from src.models.models import User
from src.services import signals


class FakeDb:
    def __init__(self):
        self.added = []
        self.next_id = 42

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for item in self.added:
            if getattr(item, "id", None) is None:
                item.id = self.next_id
                self.next_id += 1


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

        def fake_run_background(coro):
            events.append(("background",))
            coro.close()

        with (
            patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user),
            patch.object(signals, "_run_background_delivery", fake_run_background),
        ):
            signal = asyncio.run(
                signals.deliver_personal_signal(
                    db,
                    user=user,
                    text="fast signal",
                )
            )

        self.assertEqual(signal.id, 42)
        self.assertEqual(events[0], ("websocket", 12345, "fast signal"))
        self.assertEqual(events[1], ("background",))

    def test_broadcast_personal_signals_fans_out_to_web_clients(self):
        events = []
        db = FakeDb()
        users = [
            User(telegram_id=111, web_push_subscription={"endpoint": "https://push.example/1", "keys": {}}),
            User(telegram_id=222, web_push_subscription={"endpoint": "https://push.example/2", "keys": {}}),
        ]

        async def fake_send_to_user(user_id, payload):
            events.append(("websocket", user_id, payload["id"], payload["type"], payload["text"]))

        def fake_run_background(coro):
            events.append(("background",))
            coro.close()

        with (
            patch.object(signals.signal_stream_hub, "send_to_user", fake_send_to_user),
            patch.object(signals, "_run_background_delivery", fake_run_background),
        ):
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
        self.assertEqual([item.user_id for item in db.added], [111, 222])
        self.assertEqual(events[0], ("websocket", 111, 42, "forecast_teaser", "Закрытый анонс прогноза"))
        self.assertEqual(events[1], ("websocket", 222, 43, "forecast_teaser", "Закрытый анонс прогноза"))
        self.assertEqual(events[2], ("background",))

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

    def test_history_serializer_enriches_old_forecast_signal_data(self):
        request_id = uuid4()
        bet_id = uuid4()
        bookmaker = SimpleNamespace(id=1, name="Fonbet", code="fonbet")
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
        self.assertEqual(payload.data["bookmakers"][0]["name"], "Fonbet")
        self.assertEqual(payload.data["bookmakers"][0]["url"], "https://fonbet.ru/sports/football/12313")
        self.assertEqual(payload.data["event_name"], "валера-вася")


if __name__ == "__main__":
    unittest.main()
