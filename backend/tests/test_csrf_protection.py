import unittest
from types import SimpleNamespace

from src.core.csrf import (
    AUTH_COOKIE_NAME,
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    create_csrf_token,
    should_check_csrf,
    validate_csrf_request,
)


def _request(
    *,
    path: str = "/api/protected",
    method: str = "POST",
    cookies: dict[str, str] | None = None,
    headers: dict[str, str] | None = None,
):
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        cookies=cookies or {},
        headers=headers or {},
    )


class CsrfProtectionTests(unittest.TestCase):
    def test_cookie_auth_rejects_missing_csrf_token(self):
        request = _request(cookies={AUTH_COOKIE_NAME: "access-token"})

        self.assertTrue(should_check_csrf(request))
        self.assertFalse(validate_csrf_request(request))

    def test_cookie_auth_rejects_mismatched_csrf_token(self):
        token = create_csrf_token()
        request = _request(
            cookies={AUTH_COOKIE_NAME: "access-token", CSRF_COOKIE_NAME: token},
            headers={CSRF_HEADER_NAME: create_csrf_token()},
        )

        self.assertTrue(should_check_csrf(request))
        self.assertFalse(validate_csrf_request(request))

    def test_cookie_auth_accepts_valid_csrf_token(self):
        token = create_csrf_token()
        request = _request(
            cookies={AUTH_COOKIE_NAME: "access-token", CSRF_COOKIE_NAME: token},
            headers={CSRF_HEADER_NAME: token},
        )

        self.assertTrue(should_check_csrf(request))
        self.assertTrue(validate_csrf_request(request))

    def test_webhook_exemptions_do_not_require_csrf(self):
        request = _request(
            path="/api/payments/yookassa/webhook",
            cookies={AUTH_COOKIE_NAME: "access-token"},
        )

        self.assertFalse(should_check_csrf(request))

    def test_signed_auth_login_endpoints_do_not_require_csrf(self):
        for path in ("/api/auth/login", "/api/auth/telegram-widget"):
            request = _request(
                path=path,
                cookies={AUTH_COOKIE_NAME: "stale-access-token"},
            )

            with self.subTest(path=path):
                self.assertFalse(should_check_csrf(request))

    def test_bearer_only_compat_request_is_not_csrf_blocked(self):
        request = _request(headers={"Authorization": "Bearer access-token"})

        self.assertFalse(should_check_csrf(request))


if __name__ == "__main__":
    unittest.main()
