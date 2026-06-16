import unittest
from types import SimpleNamespace

from src.core.security_limits import (
    RateLimitRule,
    SecurityRateLimiter,
    classify_rate_limit_group,
    parse_rate_limit_rules,
    request_subjects,
)


class SecurityRateLimitConfigTests(unittest.TestCase):
    def test_route_classifier_assigns_high_risk_groups(self):
        cases = [
            ("/api/auth/login", "POST", "auth"),
            ("/api/payments/yookassa/webhook", "POST", "webhook"),
            ("/api/payments/yookassa/create", "POST", "payment"),
            ("/api/admin/users-page", "GET", "admin"),
            ("/api/admin/announcements", "POST", "upload"),
            ("/api/bets/with-coupon", "POST", "upload"),
            ("/api/bets/feed-page", "GET", "public_read"),
            ("/api/stats/global", "GET", "public_read"),
            ("/api/users/me", "GET", "default"),
        ]

        for path, method, expected_group in cases:
            with self.subTest(path=path):
                self.assertEqual(classify_rate_limit_group(path, method), expected_group)

    def test_parse_rate_limit_rules_overrides_known_groups_only(self):
        rules = parse_rate_limit_rules("auth=2:3,unknown=1:1,admin=5", 30)

        self.assertEqual(rules["auth"].limit, 2)
        self.assertEqual(rules["auth"].burst, 3)
        self.assertEqual(rules["auth"].window_seconds, 30)
        self.assertEqual(rules["admin"].limit, 5)
        self.assertNotIn("unknown", rules)

    def test_client_ip_ignores_spoofed_proxy_headers_from_untrusted_peer(self):
        request = SimpleNamespace(
            headers={"x-real-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"},
            client=SimpleNamespace(host="203.0.113.10"),
        )

        self.assertEqual(request_subjects(request)[0], "ip:203.0.113.10")

    def test_client_ip_uses_forwarded_header_from_trusted_proxy(self):
        request = SimpleNamespace(
            headers={"x-real-ip": "1.2.3.4", "x-forwarded-for": "5.6.7.8"},
            client=SimpleNamespace(host="127.0.0.1"),
        )

        self.assertEqual(request_subjects(request)[0], "ip:1.2.3.4")


class SecurityRateLimiterTests(unittest.TestCase):
    def _limiter(self, mode: str) -> SecurityRateLimiter:
        return SecurityRateLimiter(
            mode=mode,
            rules={"auth": RateLimitRule(limit=1, burst=0, window_seconds=60)},
            max_tracked_keys=100,
            cleanup_interval_seconds=60,
        )

    def test_monitor_mode_records_exceeded_without_blocking(self):
        limiter = self._limiter("monitor")

        first = limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)

        self.assertFalse(first.exceeded)
        self.assertFalse(first.blocked)
        self.assertTrue(second.exceeded)
        self.assertFalse(second.blocked)
        self.assertEqual(limiter.snapshot()["groups"]["auth"]["exceeded"], 1)

    def test_enforce_mode_blocks_after_threshold(self):
        limiter = self._limiter("enforce")

        limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)

        self.assertTrue(second.exceeded)
        self.assertTrue(second.blocked)
        self.assertGreater(second.retry_after, 0)
        self.assertEqual(limiter.snapshot()["groups"]["auth"]["blocked"], 1)

    def test_off_mode_never_tracks_or_blocks(self):
        limiter = self._limiter("off")

        for offset in range(5):
            decision = limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0 + offset)

        self.assertFalse(decision.exceeded)
        self.assertFalse(decision.blocked)
        self.assertEqual(limiter.snapshot()["tracked_keys"], 0)


if __name__ == "__main__":
    unittest.main()
