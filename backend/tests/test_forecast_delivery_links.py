import asyncio
import json
import unittest
from uuid import uuid4
from types import SimpleNamespace

from fastapi import HTTPException

from src.api import go
from src.api import payments
from src.core import telegram_text
from src.core.bookmaker_links import normalize_bookmaker_links, normalize_match_url
from src.services import forecast_delivery as delivery


class ForecastDeliveryLinkTests(unittest.IsolatedAsyncioTestCase):
    def _bookmaker(self, bookmaker_id=1, name="Fonbet", code="fonbet"):
        return SimpleNamespace(id=bookmaker_id, name=name, code=code)

    def _bet(
        self,
        *,
        bet_id=None,
        bookmaker_links=None,
        bookmakers=None,
        coupon_image_url="/static/coupons/coupon.png",
        description=None,
        event_name="France - Northern Ireland",
        match_link=None,
    ):
        bookmaker = self._bookmaker()
        return SimpleNamespace(
            id=bet_id or uuid4(),
            event_name=event_name,
            outcome="Total over 3.5",
            coefficient=2.0,
            description=description,
            sport_type="Football",
            coupon_image_url=coupon_image_url,
            match_link=match_link,
            bookmaker=bookmaker,
            bookmaker_id=bookmaker.id,
            bookmakers=bookmakers if bookmakers is not None else [bookmaker],
            bookmaker_links=bookmaker_links if bookmaker_links is not None else [
                {"bookmaker_id": 1, "url": "fonbet.ru/sports/football/12313"},
            ],
        )

    def _redirect_url(self, bet, bookmaker_id=1):
        return f"https://shamra1.pro/api/go/bets/{bet.id}/bookmakers/{bookmaker_id}"

    def test_normalizes_bookmaker_url(self):
        self.assertEqual(
            delivery._normalize_match_url("fonbet.ru/sports/football/12313"),
            "https://fonbet.ru/sports/football/12313",
        )

    def test_invalid_match_urls_are_rejected(self):
        invalid_values = [
            "",
            "   ",
            "🔗",
            "<a href='https://fonbet.ru'>Fonbet</a>",
            "javascript:alert(1)",
            "https://",
            "https://example",
            "https://example.com:bad/path",
            "https://example.com:99999/path",
            "https://.com/path",
            "https://foo..bar/path",
            "https://user:pass@example.com/match",
        ]

        for value in invalid_values:
            with self.subTest(value=value):
                self.assertEqual(normalize_match_url(value), "")

    def test_normalize_bookmaker_links_drops_invalid_and_normalizes_valid(self):
        self.assertEqual(
            normalize_bookmaker_links(
                [
                    {"bookmaker_id": 1, "url": "fonbet.ru/sports/football/12313"},
                    {"bookmaker_id": 2, "url": "🔗"},
                    {"bookmaker_id": 3, "url": "<a href='https://bad.example'>Bad</a>"},
                ],
                allowed_bookmaker_ids=[1, 2, 3],
            ),
            [
                {
                    "bookmaker_id": 1,
                    "url": "https://fonbet.ru/sports/football/12313",
                }
            ],
        )

    def test_telegram_message_has_visible_url_and_button_without_anchor_dependency(self):
        bet = self._bet()
        expected_url = "https://fonbet.ru/sports/football/12313"
        expected_button_url = self._redirect_url(bet)

        message = delivery._bookmaker_links_message(bet)
        reply_markup = delivery._bookmaker_link_reply_markup(bet)

        self.assertIn("<b>Fonbet</b>", message)
        self.assertNotIn(expected_url, message)
        self.assertIn("Нажмите кнопку ниже, чтобы открыть матч", message)
        self.assertIn("🏦", message)
        self.assertNotIn("<a href", message)
        self.assertEqual(
            reply_markup,
            {
                "inline_keyboard": [
                    [{"text": "Fonbet", "url": expected_button_url}],
                ],
            },
        )

    def test_multipart_reply_markup_is_valid_json_for_telegram(self):
        reply_markup = {
            "inline_keyboard": [
                [{"text": "Fonbet", "url": "https://fonbet.ru/sports/football/12313"}],
            ],
        }

        serialized = payments._telegram_multipart_field_value(reply_markup)

        self.assertEqual(json.loads(serialized), reply_markup)
        self.assertIn('"inline_keyboard"', serialized)
        self.assertNotIn("'inline_keyboard'", serialized)

    def test_full_forecast_text_inlines_visible_url_and_button_when_it_fits(self):
        bet = self._bet(coupon_image_url=None)
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)
        calls = []
        expected_url = "https://fonbet.ru/sports/football/12313"
        expected_button_url = self._redirect_url(bet)
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_full_forecast_to_client(forecast_request)
        finally:
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(calls), 1)
        method, payload = calls[0]
        self.assertEqual(method, "sendMessage")
        self.assertNotIn(expected_url, payload["text"])
        self.assertIn("Нажмите кнопку ниже, чтобы открыть матч", payload["text"])
        self.assertNotIn("<a href", payload["text"])
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_single_unknown_bookmaker_link_is_not_labeled_as_selected_bookmaker(self):
        bet = self._bet(
            bookmaker_links=[{"bookmaker_id": 2, "url": "winline.ru/match/1"}],
        )

        message = delivery._bookmaker_links_message(bet)
        reply_markup = delivery._bookmaker_link_reply_markup(bet)

        self.assertNotIn("Fonbet", message)
        self.assertIn("<b>Ссылка на матч</b>", message)
        self.assertIsNone(reply_markup)

    def test_same_url_for_multiple_selected_bookmakers_keeps_all_buttons(self):
        bookmakers = [
            self._bookmaker(1, "Fonbet", "fonbet"),
            self._bookmaker(2, "BetBoom", "betboom"),
            self._bookmaker(3, "Winline", "winline"),
        ]
        shared_url = "https://winline.ru/stavki/sport/tennis/15964236"
        bet = self._bet(
            bookmakers=bookmakers,
            bookmaker_links=[
                {"bookmaker_id": bookmaker.id, "url": shared_url}
                for bookmaker in bookmakers
            ],
        )

        targets = delivery._bookmaker_link_targets(bet)
        reply_markup = delivery._bookmaker_link_reply_markup(bet)

        self.assertEqual(
            [target["bookmaker"].id for target in targets],
            [1, 2, 3],
        )
        self.assertEqual(len(reply_markup["inline_keyboard"]), 3)
        self.assertEqual(
            [row[0]["text"] for row in reply_markup["inline_keyboard"]],
            ["Fonbet", "BetBoom", "Winline"],
        )
        self.assertEqual(
            [row[0]["url"] for row in reply_markup["inline_keyboard"]],
            [
                self._redirect_url(bet, 1),
                self._redirect_url(bet, 2),
                self._redirect_url(bet, 3),
            ],
        )

    def test_match_link_fallback_is_delivered_for_selected_bookmaker(self):
        bet = self._bet(
            bookmaker_links=[],
            match_link="fonbet.ru/sports/football/999",
        )
        expected_url = "https://fonbet.ru/sports/football/999"
        expected_button_url = self._redirect_url(bet)

        message = delivery._bookmaker_links_message(bet)
        reply_markup = delivery._bookmaker_link_reply_markup(bet)

        self.assertIn("🏦 <b>Fonbet</b>", message)
        self.assertNotIn(expected_url, message)
        self.assertEqual(reply_markup["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_custom_bookmaker_emoji_can_be_found_by_display_name_alias(self):
        original_value = delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS
        try:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = '{"фонбет (fonbet)":"123456"}'
            telegram_text._parse_custom_emoji_map.cache_clear()
            message = delivery._bookmaker_links_message(self._bet())
        finally:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = original_value
            telegram_text._parse_custom_emoji_map.cache_clear()

        self.assertIn('emoji-id="123456"', message)

    def test_custom_bookmaker_emoji_can_be_found_by_logo_file_alias(self):
        olimp = self._bookmaker(10, "Олимпбет", "olimpbet")
        bettery = self._bookmaker(11, "Bettery", "bettery")
        bet = self._bet(
            bookmakers=[olimp, bettery],
            bookmaker_links=[
                {"bookmaker_id": olimp.id, "url": "https://example.com/olimp"},
                {"bookmaker_id": bettery.id, "url": "https://example.com/bettery"},
            ],
        )
        original_value = delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS
        try:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = '{"olimp":"777","bettery":"888"}'
            telegram_text._parse_custom_emoji_map.cache_clear()
            message = delivery._bookmaker_links_message(bet)
        finally:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = original_value
            telegram_text._parse_custom_emoji_map.cache_clear()

        self.assertIn('emoji-id="777"', message)
        self.assertIn('emoji-id="888"', message)

    def test_full_forecast_message_lists_bookmakers_with_custom_emojis(self):
        fonbet = self._bookmaker(1, "Фонбет (Fonbet)", "fonbet")
        pari = self._bookmaker(4, "Пари (Pari)", "pari")
        forecast_request = SimpleNamespace(
            user_id=123456789,
            bet=self._bet(
                bookmakers=[fonbet, pari],
                bookmaker_links=[
                    {"bookmaker_id": fonbet.id, "url": "https://example.com/fonbet"},
                    {"bookmaker_id": pari.id, "url": "https://example.com/pari"},
                ],
            ),
        )
        original_value = delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS
        try:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = '{"fonbet":"111","pari":"222"}'
            telegram_text._parse_custom_emoji_map.cache_clear()
            message = delivery._build_full_forecast_message(forecast_request)
        finally:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = original_value
            telegram_text._parse_custom_emoji_map.cache_clear()

        self.assertIn("БК:", message)
        self.assertIn('emoji-id="111"', message)
        self.assertIn('emoji-id="222"', message)
        self.assertIn("<b>Фонбет (Fonbet)</b>", message)
        self.assertIn("<b>Пари (Pari)</b>", message)

    def test_coupon_delivery_keeps_bookmaker_button_on_coupon_when_caption_fits(self):
        bet = self._bet(coupon_image_url="https://example.com/coupon.jpg", description="")
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)
        for size in range(1, 1200):
            bet.description = "A" * size
            base_message = delivery._build_full_forecast_message(forecast_request)
            message_with_links = delivery._build_full_forecast_message(
                forecast_request,
                include_bookmaker_links=True,
            )
            if delivery._fits_photo_caption(base_message) and not delivery._fits_photo_caption(message_with_links):
                break
        else:
            self.fail("Could not build caption boundary fixture")

        calls = []
        expected_url = "https://fonbet.ru/sports/football/12313"
        expected_button_url = self._redirect_url(bet)
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_full_forecast_to_client(forecast_request)
        finally:
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual([method for method, _ in calls], ["sendPhoto"])
        self.assertNotIn(expected_url, calls[0][1]["caption"])
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_coupon_delivery_does_not_duplicate_bookmaker_links_message(self):
        bet = self._bet(coupon_image_url="https://example.com/coupon.jpg")
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)
        calls = []
        expected_url = "https://fonbet.ru/sports/football/12313"
        expected_button_url = self._redirect_url(bet)
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_full_forecast_to_client(forecast_request)
        finally:
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual([method for method, _ in calls], ["sendPhoto"])
        self.assertNotIn(expected_url, calls[0][1]["caption"])
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"], "Fonbet")
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_redirect_endpoint_helpers_find_url_and_return_unavailable_page(self):
        bet = self._bet()
        expected_url = "https://fonbet.ru/sports/football/12313"

        self.assertEqual(go.bookmaker_match_url_for_bet(bet, 1), expected_url)
        self.assertEqual(go.bookmaker_match_url_for_bet(bet, 2), "")

        response = go.unavailable_link_response(reason="Ссылка на матч недоступна")
        self.assertEqual(response.status_code, 404)
        self.assertIn("Ссылка на матч недоступна".encode("utf-8"), response.body)

    def test_redirect_endpoint_returns_302_for_saved_bookmaker_url(self):
        bet = self._bet()
        expected_url = "https://fonbet.ru/sports/football/12313"

        class FakeScalars:
            def first(self):
                return bet

        class FakeResult:
            def scalars(self):
                return FakeScalars()

        class FakeDb:
            async def execute(self, _query):
                return FakeResult()

        response = asyncio.run(go.redirect_to_bookmaker_match(bet.id, 1, FakeDb()))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["location"], expected_url)

    def test_long_coupon_fallback_caption_is_capped_before_full_text_and_links(self):
        bet = self._bet(
            coupon_image_url="https://example.com/coupon.jpg",
            description="A" * 1200,
            event_name="E" * 2000,
        )
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)
        calls = []
        expected_button_url = self._redirect_url(bet)
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_full_forecast_to_client(forecast_request)
        finally:
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual([method for method, _ in calls], ["sendPhoto", "sendMessage"])
        self.assertLessEqual(len(calls[0][1]["caption"]), delivery.TELEGRAM_PHOTO_CAPTION_LIMIT)
        self.assertTrue(calls[0][1]["caption"].endswith("..."))
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)
        self.assertEqual(calls[1][1]["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_vk_plain_text_contains_bookmaker_url_once(self):
        bet = self._bet()
        expected_url = "https://fonbet.ru/sports/football/12313"

        message = delivery._bookmaker_links_plain_text(bet)

        self.assertIn(f"Fonbet: {expected_url}", message)
        self.assertEqual(message.count(expected_url), 1)

    def test_full_forecast_ready_rejects_missing_bookmaker_link_targets(self):
        forecast_request = SimpleNamespace(bet=self._bet(bookmaker_links=[]))

        with self.assertRaises(HTTPException) as raised:
            delivery._ensure_full_forecast_ready(forecast_request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(
            raised.exception.detail,
            delivery.FULL_FORECAST_LINKS_REQUIRED_MESSAGE,
        )

    def test_full_forecast_ready_rejects_text_too_long_for_telegram_message(self):
        forecast_request = SimpleNamespace(bet=self._bet(description="A" * 5000))

        with self.assertRaises(HTTPException) as raised:
            delivery._ensure_full_forecast_ready(forecast_request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Сократите текст полного прогноза до 4096 символов")


class ForecastDeliveryMethodTests(unittest.IsolatedAsyncioTestCase):
    def _bookmaker(self):
        return SimpleNamespace(id=1, name="Fonbet", code="fonbet")

    def _bet(self):
        bookmaker = self._bookmaker()
        return SimpleNamespace(
            id=uuid4(),
            event_name="France - Northern Ireland",
            outcome="Total over 3.5",
            coefficient=2.0,
            description="Full forecast",
            sport_type="Football",
            coupon_image_url="/static/coupons/coupon.png",
            match_link=None,
            bookmaker=bookmaker,
            bookmaker_id=bookmaker.id,
            bookmakers=[bookmaker],
            bookmaker_links=[
                {"bookmaker_id": 1, "url": "https://fonbet.ru/sports/football/12313"},
            ],
        )

    def _user(self, *, telegram_id=123456789, vk_user_id=None, vk_messages_allowed=False):
        return SimpleNamespace(
            telegram_id=telegram_id,
            is_web_only=telegram_id < 0,
            vk_user_id=vk_user_id,
            vk_messages_allowed=vk_messages_allowed,
            purchased_bets_balance=3,
            matches_remaining=3,
            guarantee_active=False,
        )

    def _forecast_request(self, user):
        return SimpleNamespace(
            id=uuid4(),
            user_id=user.telegram_id,
            user=user,
            bet=self._bet(),
            status=delivery.FORECAST_STATUS_INTERESTED,
            handled_by=None,
            delivered_at=None,
            balance_before=None,
            balance_after=None,
            no_balance_warning=False,
            delivery_method=None,
        )

    def test_client_delivery_method_prefers_duplicate_when_both_channels_exist(self):
        self.assertEqual(
            delivery.client_delivery_method(self._user(vk_user_id="456", vk_messages_allowed=True)),
            "vk_bot",
        )
        self.assertEqual(
            delivery.client_delivery_method(self._user(telegram_id=-456, vk_user_id="456", vk_messages_allowed=True)),
            "vk",
        )
        self.assertEqual(delivery.client_delivery_method(self._user()), "bot")
        self.assertEqual(delivery.client_delivery_method(self._user(telegram_id=-456)), "web")

    async def test_refreshed_client_delivery_method_promotes_vk_after_remote_permission(self):
        user = self._user(vk_user_id="456", vk_messages_allowed=False)

        async def fake_refresh(_db, refreshed_user, refresh_group=False):
            refreshed_user.vk_messages_allowed = True
            return {"changed": True}

        original_refresh = delivery.refresh_vk_delivery_status
        try:
            delivery.refresh_vk_delivery_status = fake_refresh
            self.assertEqual(
                await delivery.refreshed_client_delivery_method(SimpleNamespace(), user),
                "vk_bot",
            )
        finally:
            delivery.refresh_vk_delivery_status = original_refresh

    def test_status_for_vk_delivery_methods_is_sent(self):
        self.assertEqual(delivery._status_for_delivery_method("bot"), delivery.FORECAST_STATUS_SENT)
        self.assertEqual(delivery._status_for_delivery_method("vk"), delivery.FORECAST_STATUS_SENT)
        self.assertEqual(delivery._status_for_delivery_method("vk_bot"), delivery.FORECAST_STATUS_SENT)
        self.assertEqual(delivery._status_for_delivery_method("web"), delivery.FORECAST_STATUS_SENT)
        self.assertEqual(delivery._status_for_delivery_method("manual"), delivery.FORECAST_STATUS_MANUAL_SENT)

    def test_web_forecast_signal_data_keeps_link_only_in_bookmaker_button_payload(self):
        user = self._user(telegram_id=-456)
        forecast_request = self._forecast_request(user)

        full_data = delivery.build_web_forecast_signal_data(forecast_request)
        teaser_data = delivery.build_web_teaser_signal_data(forecast_request, "Закрытый анонс")

        self.assertEqual(full_data["coupon_image_url"], "/static/coupons/coupon.png")
        self.assertEqual(teaser_data["coupon_image_url"], "/static/coupons/coupon.png")
        self.assertEqual(full_data["event_name"], "France - Northern Ireland")
        self.assertEqual(full_data["outcome"], "Total over 3.5")
        self.assertEqual(full_data["forecast_status"], delivery.FORECAST_STATUS_INTERESTED)
        self.assertEqual(full_data["bookmakers"][0]["name"], "Fonbet")
        self.assertEqual(full_data["bookmakers"][0]["url"], "https://fonbet.ru/sports/football/12313")
        self.assertEqual(full_data["bookmakers"][0]["logo_url"], "/bookmakers/transparent/fonbet.png")
        self.assertIn("Матч:", full_data["message_html"])
        self.assertIn("France - Northern Ireland", full_data["message_text"])
        self.assertNotIn("БК:", full_data["message_text"])
        self.assertNotIn("Ссылки на матч", full_data["message_text"])
        self.assertNotIn("Купон:", full_data["message_text"])
        self.assertNotIn("/static/coupons/coupon.png", full_data["message_text"])
        self.assertNotIn("https://fonbet.ru", full_data["message_text"])
        self.assertNotIn("Спорт:", teaser_data["message_text"])
        self.assertNotIn("Football", teaser_data["message_text"])
        self.assertEqual(teaser_data["actions"], ["take", "decline"])

    async def test_vk_bot_delivery_records_access_once_after_sending_both_channels(self):
        user = self._user(vk_user_id="456", vk_messages_allowed=True)
        forecast_request = self._forecast_request(user)
        record_calls = []
        send_calls = []
        web_signal_calls = []

        class FakeDb:
            async def execute(self, _query):
                return SimpleNamespace(rowcount=1)

            async def commit(self):
                return None

            async def rollback(self):
                return None

        async def fake_record_user_bet_access(*args, **kwargs):
            record_calls.append((args, kwargs))
            return SimpleNamespace(
                already_recorded=False,
                balance_before=3,
                balance_after=2,
                no_balance_warning=False,
            )

        async def fake_deliver_full_forecast_to_web_chat(_db, request):
            web_signal_calls.append(request.id)

        original_vk = delivery.send_full_forecast_to_vk_client
        original_telegram = delivery.send_full_forecast_to_client
        original_record = delivery.record_user_bet_access
        original_web_signal = delivery.deliver_full_forecast_to_web_chat
        try:
            delivery.send_full_forecast_to_vk_client = lambda _request: send_calls.append("vk") or {"ok": True}
            delivery.send_full_forecast_to_client = lambda _request: send_calls.append("bot") or {"ok": True}
            delivery.record_user_bet_access = fake_record_user_bet_access
            delivery.deliver_full_forecast_to_web_chat = fake_deliver_full_forecast_to_web_chat

            await delivery.deliver_forecast_request(
                FakeDb(),
                forecast_request=forecast_request,
                handled_by=111,
                delivery_method="vk_bot",
                send_to_client=True,
            )
        finally:
            delivery.send_full_forecast_to_vk_client = original_vk
            delivery.send_full_forecast_to_client = original_telegram
            delivery.record_user_bet_access = original_record
            delivery.deliver_full_forecast_to_web_chat = original_web_signal

        self.assertEqual(send_calls, ["vk", "bot"])
        self.assertEqual(web_signal_calls, [forecast_request.id])
        self.assertEqual(len(record_calls), 1)
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_SENT)
        self.assertEqual(forecast_request.delivery_method, "vk_bot")

    async def test_vk_permission_error_marks_messages_denied(self):
        user = self._user(telegram_id=-456, vk_user_id="456", vk_messages_allowed=True)
        forecast_request = self._forecast_request(user)

        class FakeDb:
            async def execute(self, _query):
                return SimpleNamespace(rowcount=1)

            async def commit(self):
                return None

            async def rollback(self):
                return None

        async def fake_refresh(_db, _user, refresh_group=False):
            return {"changed": False}

        original_vk = delivery.send_full_forecast_to_vk_client
        original_refresh = delivery.refresh_vk_delivery_status
        try:
            delivery.send_full_forecast_to_vk_client = lambda _request: {
                "ok": False,
                "error": {"error_code": 901, "error_msg": "Can't send messages for users without permission"},
            }
            delivery.refresh_vk_delivery_status = fake_refresh

            with self.assertRaises(HTTPException) as exc:
                await delivery.deliver_forecast_request(
                    FakeDb(),
                    forecast_request=forecast_request,
                    handled_by=111,
                    delivery_method="vk",
                    send_to_client=True,
                )
        finally:
            delivery.send_full_forecast_to_vk_client = original_vk
            delivery.refresh_vk_delivery_status = original_refresh

        self.assertEqual(exc.exception.status_code, 502)
        self.assertFalse(user.vk_messages_allowed)


if __name__ == "__main__":
    unittest.main()
