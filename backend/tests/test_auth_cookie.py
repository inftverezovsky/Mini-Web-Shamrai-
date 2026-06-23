import unittest
from unittest.mock import patch

from fastapi import Response

from src.api import auth


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

    async def test_logout_clears_auth_cookie(self):
        response = Response()

        payload = await auth.logout_user(response)

        cookie = "; ".join(_set_cookie_headers(response)).lower()
        self.assertEqual(payload, {"status": "ok"})
        self.assertIn("shamrai_access_token=", cookie)
        self.assertIn("max-age=0", cookie)
        self.assertIn("path=/api", cookie)


if __name__ == "__main__":
    unittest.main()

