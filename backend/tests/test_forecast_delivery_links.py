import asyncio
import json
import unittest
from uuid import uuid4
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException

from src.api import go
from src.api import payments
from src.core import telegram_text
from src.core.bookmaker_links import normalize_bookmaker_links, normalize_match_url
from src.core.bookmakers import STANDARD_BOOKMAKERS
from src.models.models import DeliveryOutbox
from src.services import forecast_delivery as delivery
from src.services import telegram_bot
from src.services.delivery_outbox import (
    CHANNEL_FORECAST_ADMIN_FULL_COPY,
    CHANNEL_FORECAST_AUTO_DELIVERY,
    CHANNEL_FORECAST_FULL_DELIVERY,
    CHANNEL_TELEGRAM_MESSAGE,
)
from src.services.match_access import user_has_full_forecast_access


class ForecastDeliveryLinkTests(unittest.IsolatedAsyncioTestCase):
    def _bookmaker(self, bookmaker_id=1, name="Фонбет", code="fonbet"):
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

    def test_standard_bookmaker_names_are_canonical_russian_labels(self):
        names_by_code = {bookmaker["code"]: bookmaker["name"] for bookmaker in STANDARD_BOOKMAKERS}

        self.assertEqual(names_by_code["fonbet"], "Фонбет")
        self.assertEqual(names_by_code["betboom"], "БетБум")
        self.assertEqual(names_by_code["winline"], "Винлайн")
        self.assertEqual(names_by_code["pari"], "Пари")
        self.assertFalse(any("(" in name or ")" in name for name in names_by_code.values()))

    def test_invalid_match_urls_are_rejected(self):
        invalid_values = [
            "",
            "   ",
            "🔗",
            "<a href='https://fonbet.ru'>Фонбет</a>",
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

        self.assertIn("<b>Фонбет</b>", message)
        self.assertNotIn(expected_url, message)
        self.assertIn("Нажмите кнопку ниже, чтобы открыть матч", message)
        self.assertIn("💵", message)
        self.assertNotIn("<a href", message)
        self.assertEqual(
            reply_markup,
            {
                "inline_keyboard": [
                    [{"text": "Фонбет", "url": expected_button_url}],
                ],
            },
        )

    def test_multipart_reply_markup_is_valid_json_for_telegram(self):
        reply_markup = {
            "inline_keyboard": [
                [{"text": "Фонбет", "url": "https://fonbet.ru/sports/football/12313"}],
            ],
        }

        serialized = telegram_bot._telegram_multipart_field_value(reply_markup)

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
        self.assertNotIn(f'<a href="{expected_url}"', payload["text"])
        self.assertIn('href="https://t.me/+OUTzNRDdl9gzNTQy"', payload["text"])
        self.assertEqual(payload["reply_markup"]["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_single_unknown_bookmaker_link_is_not_labeled_as_selected_bookmaker(self):
        bet = self._bet(
            bookmaker_links=[{"bookmaker_id": 2, "url": "winline.ru/match/1"}],
        )

        message = delivery._bookmaker_links_message(bet)
        reply_markup = delivery._bookmaker_link_reply_markup(bet)

        self.assertNotIn("Фонбет", message)
        self.assertIn("<b>Ссылка на матч</b>", message)
        self.assertIsNone(reply_markup)

    def test_same_url_for_multiple_selected_bookmakers_keeps_all_buttons(self):
        bookmakers = [
            self._bookmaker(1, "Фонбет", "fonbet"),
            self._bookmaker(2, "БетБум", "betboom"),
            self._bookmaker(3, "Винлайн", "winline"),
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
            ["Фонбет", "БетБум", "Винлайн"],
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

        self.assertIn("💵", message)
        self.assertIn("<b>Фонбет</b>", message)
        self.assertNotIn(expected_url, message)
        self.assertEqual(reply_markup["inline_keyboard"][0][0]["url"], expected_button_url)

    def test_teaser_contact_footer_links_to_shamrai_telegram(self):
        forecast_request = SimpleNamespace(user_id=123456789, bet=self._bet())

        message = delivery.build_teaser_message(forecast_request, None)
        vk_text = delivery.html_to_vk_text(message)

        self.assertIn('href="https://t.me/+OUTzNRDdl9gzNTQy"', message)
        self.assertIn(">@Shamrai_Osnova</a>", message)
        self.assertIn("[https://t.me/+OUTzNRDdl9gzNTQy|@Shamrai_Osnova]", vk_text)

    def test_teaser_includes_optional_fair_coefficient(self):
        bet = self._bet()
        bet.fair_coefficient = 1.74
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)

        message = delivery.build_teaser_message(forecast_request, None)

        self.assertIn("Верный: <b>1.74</b>", message)

    def test_teaser_omits_fair_coefficient_line_when_empty(self):
        bet = self._bet()
        bet.fair_coefficient = None
        forecast_request = SimpleNamespace(user_id=123456789, bet=bet)

        message = delivery.build_teaser_message(forecast_request, None)

        self.assertNotIn("Верный:", message)

    def test_custom_bookmaker_emoji_can_be_found_by_display_name_alias(self):
        original_value = delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS
        try:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = '{"фонбет":"123456"}'
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
        fonbet = self._bookmaker(1, "Фонбет", "fonbet")
        pari = self._bookmaker(4, "Пари", "pari")
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
        self.assertIn("<b>Фонбет</b>", message)
        self.assertIn("<b>Пари</b>", message)

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
        self.assertEqual(calls[0][1]["reply_markup"]["inline_keyboard"][0][0]["text"], "Фонбет")
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

        self.assertIn(f"Фонбет: {expected_url}", message)
        self.assertEqual(message.count(expected_url), 1)

    def test_full_forecast_ready_allows_missing_bookmaker_link_targets(self):
        forecast_request = SimpleNamespace(bet=self._bet(bookmaker_links=[]))

        delivery._ensure_full_forecast_ready(forecast_request)

    def test_full_forecast_ready_allows_missing_match_and_coupon(self):
        forecast_request = SimpleNamespace(
            bet=self._bet(
                event_name=delivery.PLACEHOLDER_EVENT_NAME,
                coupon_image_url=None,
            )
        )

        delivery._ensure_full_forecast_ready(forecast_request)

    def test_full_forecast_ready_still_rejects_missing_outcome(self):
        bet = self._bet(coupon_image_url=None)
        bet.outcome = ""
        forecast_request = SimpleNamespace(bet=bet)

        with self.assertRaises(HTTPException) as raised:
            delivery._ensure_full_forecast_ready(forecast_request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Заполните исход перед отправкой прогноза")

    def test_full_forecast_ready_rejects_text_too_long_for_telegram_message(self):
        forecast_request = SimpleNamespace(bet=self._bet(description="A" * 5000))

        with self.assertRaises(HTTPException) as raised:
            delivery._ensure_full_forecast_ready(forecast_request)

        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual(raised.exception.detail, "Сократите текст полного прогноза до 4096 символов")


class ForecastDeliveryMethodTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.previous_admin_group_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555

    def tearDown(self):
        delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = self.previous_admin_group_chat_id

    def _bookmaker(self):
        return SimpleNamespace(id=1, name="Фонбет", code="fonbet")

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
            delivery_mode=delivery.DELIVERY_MODE_SALES_PRIVATE,
            price_stars=10_000,
        )

    def _user(
        self,
        *,
        telegram_id=123456789,
        vk_user_id=None,
        vk_messages_allowed=False,
        balance=3,
        guarantee_active=False,
        role="user",
    ):
        return SimpleNamespace(
            telegram_id=telegram_id,
            username="client",
            first_name="Client",
            last_name=None,
            is_web_only=telegram_id < 0,
            vk_user_id=vk_user_id,
            vk_messages_allowed=vk_messages_allowed,
            purchased_bets_balance=balance,
            matches_remaining=balance,
            guarantee_active=guarantee_active,
            role=role,
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

    def test_full_forecast_access_helper_allows_balance_guarantee_or_staff_only(self):
        self.assertTrue(user_has_full_forecast_access(self._user(balance=1)))
        self.assertTrue(user_has_full_forecast_access(self._user(balance=0, guarantee_active=True)))
        self.assertTrue(user_has_full_forecast_access(self._user(balance=0, role="moderator")))
        self.assertFalse(user_has_full_forecast_access(self._user(balance=0)))

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

    async def test_sales_manager_notification_is_enqueued(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_sales_manager = delivery.settings.SALES_MANAGER_TELEGRAM_ID
        db = FakeDb()
        try:
            delivery.settings.SALES_MANAGER_TELEGRAM_ID = 987654321
            forecast_request = self._forecast_request(self._user())
            result = await delivery.enqueue_sales_manager_notification(db, forecast_request)
        finally:
            delivery.settings.SALES_MANAGER_TELEGRAM_ID = previous_sales_manager

        self.assertTrue(result["ok"])
        self.assertEqual(len(db.added), 1)
        outbox_item = db.added[0]
        self.assertIsInstance(outbox_item, DeliveryOutbox)
        self.assertEqual(outbox_item.channel, CHANNEL_TELEGRAM_MESSAGE)
        self.assertEqual(outbox_item.forecast_request_id, forecast_request.id)
        self.assertIn(str(forecast_request.id), outbox_item.dedupe_key)
        self.assertEqual(outbox_item.payload["method"], "sendMessage")
        self.assertEqual(outbox_item.payload["payload"]["chat_id"], 987654321)

    async def test_admin_group_take_notification_is_enqueued(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        db = FakeDb()
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            forecast_request = self._forecast_request(self._user())
            result = await delivery.enqueue_admin_group_forecast_response_notification(
                db,
                forecast_request,
                action="take",
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

        self.assertTrue(result["ok"])
        self.assertEqual(len(db.added), 1)
        outbox_item = db.added[0]
        self.assertIsInstance(outbox_item, DeliveryOutbox)
        self.assertEqual(outbox_item.channel, CHANNEL_TELEGRAM_MESSAGE)
        self.assertEqual(outbox_item.forecast_request_id, forecast_request.id)
        payload = outbox_item.payload["payload"]
        self.assertEqual(payload["chat_id"], -100555)
        self.assertIn("хочет взять", payload["text"])
        self.assertIn("France - Northern Ireland", payload["text"])
        self.assertIn("Client (@client)", payload["text"])
        self.assertEqual(
            payload["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
            f"forecast:sales_send:{forecast_request.id}",
        )

    def test_admin_group_take_zero_balance_blocks_send_button(self):
        forecast_request = self._forecast_request(self._user(balance=0))

        delivery_payload = delivery.build_admin_group_forecast_response_delivery(
            forecast_request,
            action="take",
        )

        self.assertIsNotNone(delivery_payload)
        payload = delivery_payload["payload"]
        self.assertIn("отправка прогноза недоступна до оплаты", payload["text"].lower())
        self.assertNotIn("баланс уйдет в минус", payload["text"])
        keyboard_json = json.dumps(payload["reply_markup"], ensure_ascii=False)
        self.assertNotIn("Отправить прогноз", keyboard_json)
        self.assertNotIn("sales_send", keyboard_json)

    def test_paid_set_admin_notification_opens_dialog_and_has_separate_taken_button(self):
        user = self._user(vk_user_id="456")
        forecast_request = self._forecast_request(user)
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET
        forecast_request.bet.price_stars = 10_000

        delivery_payload = delivery.build_admin_group_forecast_response_delivery(
            forecast_request,
            action="take",
        )

        self.assertIsNotNone(delivery_payload)
        keyboard = delivery_payload["payload"]["reply_markup"]["inline_keyboard"]
        flat_buttons = [button for row in keyboard for button in row]
        dialog_button = next(button for button in flat_buttons if button["text"] == "Продажа в диалоге")
        taken_button = next(button for button in flat_buttons if button["text"] == "Взял")
        self.assertEqual(dialog_button["url"], "https://t.me/client")
        self.assertNotIn("callback_data", dialog_button)
        self.assertEqual(taken_button["callback_data"], f"forecast:sales_manual:{forecast_request.id}")
        self.assertTrue(any(button.get("url") == "https://vk.com/im?sel=456" for button in flat_buttons))

    def test_paid_set_sale_message_does_not_require_coupon(self):
        forecast_request = self._forecast_request(self._user())
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET
        forecast_request.bet.price_stars = 10_000
        forecast_request.bet.coupon_image_url = None

        message = delivery.build_paid_set_sale_message(forecast_request)

        self.assertIn("Набор оформлен", message)
        self.assertIn("France - Northern Ireland", message)
        self.assertIn("Total over 3.5", message)
        self.assertIn("10 000 ₽", message)

    def test_paid_set_sale_message_lists_bookmaker_with_custom_emoji(self):
        forecast_request = self._forecast_request(self._user())
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET
        original_value = delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS
        try:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = '{"fonbet":"111"}'
            telegram_text._parse_custom_emoji_map.cache_clear()
            message = delivery.build_paid_set_sale_message(forecast_request)
        finally:
            delivery.settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS = original_value
            telegram_text._parse_custom_emoji_map.cache_clear()

        self.assertIn('emoji-id="111"', message)
        self.assertIn('</tg-emoji> <b>', message)

    async def test_admin_group_decline_notification_is_enqueued_without_buttons(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        db = FakeDb()
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            forecast_request = self._forecast_request(self._user())
            result = await delivery.enqueue_admin_group_forecast_response_notification(
                db,
                forecast_request,
                action="decline",
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

        self.assertTrue(result["ok"])
        self.assertEqual(len(db.added), 1)
        payload = db.added[0].payload["payload"]
        self.assertEqual(payload["chat_id"], -100555)
        self.assertIn("отказался", payload["text"])
        self.assertIn("France - Northern Ireland", payload["text"])
        self.assertNotIn("reply_markup", payload)

    async def test_admin_group_result_notification_includes_forecast_and_taker_count(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        db = FakeDb()
        bet = self._bet()
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            result = await delivery.enqueue_admin_group_forecast_result_notification(
                db,
                bet=bet,
                status_value="win",
                taker_count=7,
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

        self.assertTrue(result["ok"])
        self.assertEqual(len(db.added), 1)
        outbox_item = db.added[0]
        self.assertEqual(outbox_item.channel, CHANNEL_TELEGRAM_MESSAGE)
        self.assertEqual(outbox_item.payload["payload"]["chat_id"], -100555)
        text = outbox_item.payload["payload"]["text"]
        self.assertIn("Результат прогноза", text)
        self.assertIn("Выигрыш", text)
        self.assertIn("Взяли: <b>7</b>", text)
        self.assertIn("France - Northern Ireland", text)
        self.assertIn("Total over 3.5", text)

    async def test_sales_callback_authorizes_registered_admin_user(self):
        admin_user = SimpleNamespace(role="admin")

        class FakeResult:
            def scalars(self):
                return self

            def first(self):
                return admin_user

        class FakeDb:
            async def execute(self, _query):
                return FakeResult()

        previous_sales_manager = delivery.settings.SALES_MANAGER_TELEGRAM_ID
        previous_owner = delivery.settings.OWNER_TELEGRAM_ID
        try:
            delivery.settings.SALES_MANAGER_TELEGRAM_ID = None
            delivery.settings.OWNER_TELEGRAM_ID = None
            self.assertTrue(await delivery._actor_can_handle_sales_callback(FakeDb(), 900))
        finally:
            delivery.settings.SALES_MANAGER_TELEGRAM_ID = previous_sales_manager
            delivery.settings.OWNER_TELEGRAM_ID = previous_owner

    async def test_forecast_auto_delivery_is_enqueued(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        db = FakeDb()
        forecast_request = self._forecast_request(self._user())

        result = await delivery.enqueue_forecast_auto_delivery(
            db,
            forecast_request,
            delivery_method="vk_bot",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(db.added), 1)
        outbox_item = db.added[0]
        self.assertIsInstance(outbox_item, DeliveryOutbox)
        self.assertEqual(outbox_item.channel, CHANNEL_FORECAST_AUTO_DELIVERY)
        self.assertEqual(outbox_item.forecast_request_id, forecast_request.id)
        self.assertEqual(outbox_item.payload["request_id"], str(forecast_request.id))
        self.assertEqual(outbox_item.payload["delivery_method"], "vk_bot")

    async def test_forecast_auto_delivery_is_not_enqueued_without_full_access(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        user = self._user(balance=0)
        forecast_request = self._forecast_request(user)
        db = FakeDb()

        with self.assertRaises(HTTPException) as raised:
            await delivery.enqueue_forecast_auto_delivery(
                db,
                forecast_request,
                delivery_method="bot",
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(db.added, [])

    async def test_full_forecast_delivery_queues_admin_group_copy(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        db = FakeDb()
        forecast_request = self._forecast_request(self._user())
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            await delivery.enqueue_forecast_full_delivery(
                db,
                forecast_request,
                delivery_method="bot",
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

        self.assertEqual([item.channel for item in db.added], [
            CHANNEL_FORECAST_FULL_DELIVERY,
            CHANNEL_FORECAST_ADMIN_FULL_COPY,
        ])
        admin_item = db.added[1]
        self.assertEqual(admin_item.forecast_request_id, forecast_request.id)
        self.assertEqual(admin_item.payload["request_id"], str(forecast_request.id))
        self.assertIn(str(forecast_request.bet.id), admin_item.dedupe_key)

    async def test_paid_set_full_delivery_queues_admin_group_copy(self):
        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        db = FakeDb()
        forecast_request = self._forecast_request(self._user())
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            await delivery.enqueue_forecast_full_delivery(
                db,
                forecast_request,
                delivery_method="bot",
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

        self.assertEqual([item.channel for item in db.added], [
            CHANNEL_FORECAST_FULL_DELIVERY,
            CHANNEL_FORECAST_ADMIN_FULL_COPY,
        ])

    def test_admin_group_full_copy_uses_client_telegram_forecast_format(self):
        forecast_request = self._forecast_request(self._user())
        forecast_request.bet.coupon_image_url = "https://example.com/coupon.jpg"
        calls = []
        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_admin_group_full_forecast_copy(forecast_request)
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual([method for method, _ in calls], ["sendPhoto"])
        payload = calls[0][1]
        self.assertEqual(payload["chat_id"], -100555)
        self.assertEqual(payload["photo"], "https://example.com/coupon.jpg")
        self.assertIn("France - Northern Ireland", payload["caption"])
        self.assertIn("Total over 3.5", payload["caption"])
        self.assertIn("Коэффициент:", payload["caption"])
        self.assertEqual(
            payload["reply_markup"]["inline_keyboard"][0][0]["url"],
            f"https://shamra1.pro/api/go/bets/{forecast_request.bet.id}/bookmakers/1",
        )

    def test_admin_group_full_copy_uses_paid_set_sale_format(self):
        forecast_request = self._forecast_request(self._user())
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET
        forecast_request.bet.price_stars = 10_000
        forecast_request.bet.coupon_image_url = None
        calls = []
        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        original_call_telegram_api = delivery.call_telegram_api

        def fake_call_telegram_api(method, payload):
            calls.append((method, payload))
            return {"ok": True}

        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            delivery.call_telegram_api = fake_call_telegram_api
            result = delivery.send_admin_group_full_forecast_copy(forecast_request)
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id
            delivery.call_telegram_api = original_call_telegram_api

        self.assertEqual(result, {"ok": True})
        self.assertEqual([method for method, _ in calls], ["sendMessage"])
        payload = calls[0][1]
        self.assertEqual(payload["chat_id"], -100555)
        self.assertIn("Набор оформлен", payload["text"])
        self.assertIn("France - Northern Ireland", payload["text"])
        self.assertIn("10 000 ₽", payload["text"])

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
        self.assertIsNone(teaser_data["coupon_image_url"])
        self.assertEqual(full_data["event_name"], "France - Northern Ireland")
        self.assertEqual(full_data["outcome"], "Total over 3.5")
        self.assertEqual(full_data["forecast_status"], delivery.FORECAST_STATUS_INTERESTED)
        self.assertEqual(full_data["bookmakers"][0]["name"], "Фонбет")
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

    async def test_vk_bot_delivery_records_access_once_and_queues_full_delivery(self):
        user = self._user(vk_user_id="456", vk_messages_allowed=True)
        forecast_request = self._forecast_request(user)
        record_calls = []

        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

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

        async def fake_refresh_vk_delivery_status(_db, refreshed_user, refresh_group=False):
            refreshed_user.vk_messages_allowed = True
            return {"changed": False, "messages_allowed": True}

        original_record = delivery.record_user_bet_access
        original_refresh = delivery.refresh_vk_delivery_status
        db = FakeDb()
        try:
            delivery.record_user_bet_access = fake_record_user_bet_access
            delivery.refresh_vk_delivery_status = fake_refresh_vk_delivery_status

            await delivery.deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=111,
                delivery_method="vk_bot",
                send_to_client=True,
                commit=True,
            )
        finally:
            delivery.record_user_bet_access = original_record
            delivery.refresh_vk_delivery_status = original_refresh

        self.assertEqual(len(record_calls), 1)
        self.assertFalse(record_calls[0][1]["allow_negative_balance"])
        full_delivery_items = [
            item for item in db.added if item.channel == CHANNEL_FORECAST_FULL_DELIVERY
        ]
        self.assertEqual(len(full_delivery_items), 1)
        outbox_item = full_delivery_items[0]
        self.assertIsInstance(outbox_item, DeliveryOutbox)
        self.assertEqual(outbox_item.channel, CHANNEL_FORECAST_FULL_DELIVERY)
        self.assertEqual(outbox_item.forecast_request_id, forecast_request.id)
        self.assertEqual(outbox_item.payload["request_id"], str(forecast_request.id))
        self.assertEqual(outbox_item.payload["delivery_method"], "vk_bot")
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_SENT)
        self.assertEqual(forecast_request.delivery_method, "vk_bot")

    async def test_manual_forecast_delivery_queues_admin_group_copy_only(self):
        user = self._user()
        forecast_request = self._forecast_request(user)

        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

            async def execute(self, _query):
                return SimpleNamespace(rowcount=1)

            async def commit(self):
                return None

            async def rollback(self):
                return None

        async def fake_record_user_bet_access(*_args, **_kwargs):
            return SimpleNamespace(
                already_recorded=False,
                balance_before=3,
                balance_after=2,
                no_balance_warning=False,
            )

        previous_chat_id = delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
        original_record = delivery.record_user_bet_access
        db = FakeDb()
        try:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
            delivery.record_user_bet_access = fake_record_user_bet_access
            await delivery.deliver_forecast_request(
                db,
                forecast_request=forecast_request,
                handled_by=111,
                delivery_method="manual",
                send_to_client=False,
                commit=True,
            )
        finally:
            delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id
            delivery.record_user_bet_access = original_record

        self.assertEqual([item.channel for item in db.added], [CHANNEL_FORECAST_ADMIN_FULL_COPY])
        self.assertEqual(db.added[0].forecast_request_id, forecast_request.id)
        self.assertEqual(db.added[0].payload["request_id"], str(forecast_request.id))
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_MANUAL_SENT)
        self.assertEqual(forecast_request.delivery_method, "manual")

    async def test_take_without_full_access_returns_contact_required_without_status_change(self):
        user = self._user(balance=0)
        forecast_request = self._forecast_request(user)
        forecast_request.status = delivery.FORECAST_STATUS_ANNOUNCED
        forecast_request.bet.auto_send_on_interest = True

        class FakeDb:
            async def execute(self, _query):
                raise AssertionError("zero-balance take must not update the request")

        original_loader = delivery.load_forecast_request
        try:
            delivery.load_forecast_request = AsyncMock(return_value=forecast_request)
            result_request, message, should_notify_sales = await delivery.set_forecast_request_interested(
                FakeDb(),
                request_id=forecast_request.id,
                actor_user_id=user.telegram_id,
                notify_sales_manager_now=False,
                auto_delivery_now=False,
            )
        finally:
            delivery.load_forecast_request = original_loader

        self.assertIs(result_request, forecast_request)
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_ANNOUNCED)
        self.assertIn(delivery.FORECAST_CONTACT_DRAFT_TEXT, message)
        self.assertFalse(should_notify_sales)

    async def test_take_removed_request_returns_inactive_message_without_status_change(self):
        user = self._user(balance=3)
        forecast_request = self._forecast_request(user)
        forecast_request.status = delivery.FORECAST_STATUS_REMOVED

        class FakeDb:
            async def execute(self, _query):
                raise AssertionError("inactive request must not be updated")

        original_loader = delivery.load_forecast_request
        try:
            delivery.load_forecast_request = AsyncMock(return_value=forecast_request)
            result_request, message, should_notify_sales = await delivery.set_forecast_request_interested(
                FakeDb(),
                request_id=forecast_request.id,
                actor_user_id=user.telegram_id,
                notify_sales_manager_now=False,
                auto_delivery_now=False,
            )
        finally:
            delivery.load_forecast_request = original_loader

        self.assertIs(result_request, forecast_request)
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_REMOVED)
        self.assertEqual(message, "Прогноз уже не активен. Реагировать не нужно.")
        self.assertFalse(should_notify_sales)

    async def test_take_deleted_paid_set_returns_paid_set_inactive_message(self):
        user = self._user(balance=3)
        forecast_request = self._forecast_request(user)
        forecast_request.status = delivery.FORECAST_STATUS_ANNOUNCED
        forecast_request.bet.status = "deleted"
        forecast_request.bet.delivery_mode = delivery.DELIVERY_MODE_PAID_SET

        class FakeDb:
            async def execute(self, _query):
                raise AssertionError("deleted paid set must not be updated")

        original_loader = delivery.load_forecast_request
        try:
            delivery.load_forecast_request = AsyncMock(return_value=forecast_request)
            result_request, message, should_notify_sales = await delivery.set_forecast_request_interested(
                FakeDb(),
                request_id=forecast_request.id,
                actor_user_id=user.telegram_id,
                notify_sales_manager_now=False,
                auto_delivery_now=False,
            )
        finally:
            delivery.load_forecast_request = original_loader

        self.assertIs(result_request, forecast_request)
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_ANNOUNCED)
        self.assertEqual(message, "Набор уже не активен. Реагировать не нужно.")
        self.assertFalse(should_notify_sales)

    async def test_deliver_without_full_access_rejects_before_processing_or_debit(self):
        user = self._user(balance=0)
        forecast_request = self._forecast_request(user)

        class FakeDb:
            async def execute(self, _query):
                raise AssertionError("zero-balance delivery must not lock the request")

            async def rollback(self):
                return None

        async def fail_record_user_bet_access(*_args, **_kwargs):
            raise AssertionError("zero-balance delivery must not debit matches")

        original_record = delivery.record_user_bet_access
        try:
            delivery.record_user_bet_access = fail_record_user_bet_access
            with self.assertRaises(HTTPException) as raised:
                await delivery.deliver_forecast_request(
                    FakeDb(),
                    forecast_request=forecast_request,
                    handled_by=111,
                    delivery_method="bot",
                    send_to_client=True,
                    commit=True,
                )
        finally:
            delivery.record_user_bet_access = original_record

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(forecast_request.status, delivery.FORECAST_STATUS_INTERESTED)
        self.assertEqual(user.purchased_bets_balance, 0)
        self.assertEqual(user.matches_remaining, 0)

    async def test_guarantee_allows_delivery_without_positive_balance(self):
        user = self._user(balance=0, guarantee_active=True)
        forecast_request = self._forecast_request(user)
        record_calls = []

        class FakeDb:
            def __init__(self):
                self.added = []

            def add(self, value):
                self.added.append(value)

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
                balance_before=0,
                balance_after=0,
                no_balance_warning=False,
            )

        original_record = delivery.record_user_bet_access
        try:
            delivery.record_user_bet_access = fake_record_user_bet_access
            await delivery.deliver_forecast_request(
                FakeDb(),
                forecast_request=forecast_request,
                handled_by=111,
                delivery_method="bot",
                send_to_client=True,
                commit=True,
            )
        finally:
            delivery.record_user_bet_access = original_record

        self.assertEqual(len(record_calls), 1)
        self.assertFalse(record_calls[0][1]["allow_negative_balance"])
        self.assertEqual(user.purchased_bets_balance, 0)
        self.assertEqual(user.matches_remaining, 0)

    async def test_vk_permission_error_is_reported_by_full_delivery_sender(self):
        user = self._user(telegram_id=-456, vk_user_id="456", vk_messages_allowed=True)
        forecast_request = self._forecast_request(user)

        original_vk = delivery.send_full_forecast_to_vk_client
        try:
            delivery.send_full_forecast_to_vk_client = lambda _request, **_kwargs: {
                "ok": False,
                "error": {"error_code": 901, "error_msg": "Can't send messages for users without permission"},
            }

            result = await delivery.send_full_forecast_to_external_channels(
                forecast_request,
                delivery_method="vk",
            )
        finally:
            delivery.send_full_forecast_to_vk_client = original_vk

        self.assertFalse(result["ok"])
        self.assertTrue(delivery.is_vk_message_permission_error(result["result"]))


if __name__ == "__main__":
    unittest.main()
