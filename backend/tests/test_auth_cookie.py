import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi import Response

from src.api import auth
from src.core import csrf
from src.models.models import User


def _set_cookie_headers(response: Response) -> list[str]:
    return [
        value.decode("latin-1")
        for key, value in response.raw_headers
        if key.lower() == b"set-cookie"
    ]


class AuthCookieTests(unittest.IsolatedAsyncioTestCase):
    def test_auth_cookie_is_http_only_and_bounded_to_api_path(self):
        response = Response()

        with (
            patch.object(auth.settings, "APP_ENV", "development"),
            patch.object(auth.settings, "FRONTEND_BASE_URL", "http://localhost:5173"),
        ):
            auth._set_auth_cookie(response, "access-token")

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertIn("shamrai_access_token=access-token", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/api", cookie)
        self.assertIn("max-age=2592000", cookie)
        self.assertNotIn("; secure", cookie)

    def test_auth_cookie_uses_cross_site_policy_for_https_app(self):
        response = Response()

        with (
            patch.object(auth.settings, "APP_ENV", "production"),
            patch.object(auth.settings, "FRONTEND_BASE_URL", "https://shamra1.pro/app"),
        ):
            auth._set_auth_cookie(response, "access-token")

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertIn("shamrai_access_token=access-token", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=none", cookie)
        self.assertIn("; secure", cookie)
        self.assertIn("path=/api", cookie)

    def test_login_response_sets_cookie_without_exposing_bearer_token_by_default(self):
        response = Response()
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=12345,
            first_name="Client",
            role="user",
            stats_display_mode="percent",
            bankroll=0.0,
            is_onboarded=False,
            favorite_sports=[],
            vk_group_member=False,
            vk_messages_allowed=False,
            vk_notifications_allowed=False,
            currency_preference="RUB",
            purchased_bets_balance=0,
            free_bets_available=0,
            matches_remaining=0,
            guarantee_active=False,
            tg_chat_joined=False,
            has_used_shield=False,
            alert_min_coef=1.0,
            odds_drop_notifications_enabled=True,
            is_night_mode=False,
            night_mode_start="23:00",
            night_mode_end="08:00",
            preferred_sports=[],
            created_at=now,
            updated_at=now,
        )

        with patch.object(auth.settings, "ENABLE_BEARER_AUTH_COMPAT", False):
            payload = auth._build_login_response(user, response)

        self.assertIsNone(payload.access_token)
        self.assertNotIn("access_token", payload.model_dump(exclude_none=True))
        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertIn("shamrai_access_token=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("shamrai_csrf_token=", cookie)

    def test_login_response_can_expose_bearer_token_only_for_non_production_compat(self):
        response = Response()
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=12345,
            first_name="Client",
            role="user",
            stats_display_mode="percent",
            bankroll=0.0,
            is_onboarded=False,
            favorite_sports=[],
            vk_group_member=False,
            vk_messages_allowed=False,
            vk_notifications_allowed=False,
            currency_preference="RUB",
            purchased_bets_balance=0,
            free_bets_available=0,
            matches_remaining=0,
            guarantee_active=False,
            tg_chat_joined=False,
            has_used_shield=False,
            alert_min_coef=1.0,
            odds_drop_notifications_enabled=True,
            is_night_mode=False,
            night_mode_start="23:00",
            night_mode_end="08:00",
            preferred_sports=[],
            created_at=now,
            updated_at=now,
        )

        with (
            patch.object(auth.settings, "APP_ENV", "development"),
            patch.object(auth.settings, "ENABLE_BEARER_AUTH_COMPAT", True),
        ):
            payload = auth._build_login_response(user, response)

        self.assertIsNotNone(payload.access_token)
        self.assertIn("access_token", payload.model_dump(exclude_none=True))

    def test_production_never_exposes_bearer_token_even_when_compat_is_enabled(self):
        response = Response()
        now = datetime.now(timezone.utc)
        user = User(
            telegram_id=12345,
            first_name="Client",
            role="user",
            stats_display_mode="percent",
            bankroll=0.0,
            is_onboarded=False,
            favorite_sports=[],
            vk_group_member=False,
            vk_messages_allowed=False,
            vk_notifications_allowed=False,
            currency_preference="RUB",
            purchased_bets_balance=0,
            free_bets_available=0,
            matches_remaining=0,
            guarantee_active=False,
            tg_chat_joined=False,
            has_used_shield=False,
            alert_min_coef=1.0,
            odds_drop_notifications_enabled=True,
            is_night_mode=False,
            night_mode_start="23:00",
            night_mode_end="08:00",
            preferred_sports=[],
            created_at=now,
            updated_at=now,
        )

        with (
            patch.object(auth.settings, "APP_ENV", "production"),
            patch.object(auth.settings, "ENABLE_BEARER_AUTH_COMPAT", True),
        ):
            payload = auth._build_login_response(user, response)

        self.assertIsNone(payload.access_token)
        self.assertNotIn("access_token", payload.model_dump(exclude_none=True))

    async def test_logout_clears_auth_cookie(self):
        response = Response()

        payload = await auth.logout_user(response)

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertEqual(payload, {"status": "ok"})
        self.assertIn("shamrai_access_token=", cookie)
        self.assertIn("shamrai_csrf_token=", cookie)
        self.assertIn("max-age=0", cookie)
        self.assertIn("path=/api", cookie)

    async def test_csrf_endpoint_issues_signed_http_only_cookie(self):
        response = Response()

        with (
            patch.object(csrf.settings, "APP_ENV", "development"),
            patch.object(csrf.settings, "FRONTEND_BASE_URL", "http://localhost:5173"),
        ):
            payload = await auth.get_csrf_token(response)

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertTrue(csrf.verify_csrf_token(payload.csrf_token))
        self.assertIn("shamrai_csrf_token=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=lax", cookie)
        self.assertIn("path=/api", cookie)
        self.assertNotIn("; secure", cookie)

    def test_csrf_cookie_uses_cross_site_policy_for_https_app(self):
        response = Response()

        with (
            patch.object(csrf.settings, "APP_ENV", "production"),
            patch.object(csrf.settings, "FRONTEND_BASE_URL", "https://shamra1.pro/app"),
        ):
            token = csrf.set_csrf_cookie(response)

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertTrue(csrf.verify_csrf_token(token))
        self.assertIn("shamrai_csrf_token=", cookie)
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=none", cookie)
        self.assertIn("; secure", cookie)
        self.assertIn("path=/api", cookie)


if __name__ == "__main__":
    unittest.main()
