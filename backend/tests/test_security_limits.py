import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

import jwt

from src.core import security
from src.core.security_limits import (
    RateLimitRule,
    SecurityRateLimiter,
    classify_rate_limit_group,
    parse_rate_limit_rules,
    request_subjects,
)


class SecurityRateLimitConfigTests(unittest.TestCase):
    def test_access_token_lifetime_uses_bounded_browser_session_window(self):
        token = security.create_access_token({"sub": "12345", "role": "user"})
        payload = jwt.decode(
            token,
            security.settings.JWT_SECRET_KEY,
            algorithms=[security.JWT_ALGORITHM],
        )
        expires_at = datetime.fromtimestamp(payload["exp"], timezone.utc)

        self.assertEqual(security.ACCESS_TOKEN_EXPIRE_DAYS, 30)
        self.assertGreaterEqual((expires_at - datetime.now(timezone.utc)).days, 29)
        self.assertLessEqual((expires_at - datetime.now(timezone.utc)).days, 30)

    def test_route_classifier_assigns_high_risk_groups(self):
        cases = [
            ("/api/auth/login", "POST", "auth"),
            ("/api/auth/telegram/bot-session", "POST", "auth"),
            ("/api/auth/telegram/bot-session/token-123", "GET", "auth_poll"),
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

    def test_request_subjects_can_prefer_authenticated_cookie_user(self):
        token = security.create_access_token({"sub": "12345", "role": "user"})
        request = SimpleNamespace(
            headers={},
            cookies={"shamrai_access_token": token},
            client=SimpleNamespace(host="127.0.0.1"),
        )

        self.assertEqual(
            request_subjects(request, prefer_authenticated_user=True),
            ("user:12345",),
        )

    def test_auth_subjects_prefer_hashed_identity_device_when_available(self):
        request = SimpleNamespace(
            headers={"x-shamrai-device-id": "device-12345678"},
            client=SimpleNamespace(host="203.0.113.10"),
        )

        subjects = request_subjects(request, prefer_identity_device=True)

        self.assertEqual(len(subjects), 1)
        self.assertTrue(subjects[0].startswith("device:"))
        self.assertNotIn("device-12345678", subjects[0])

    def test_auth_subjects_fall_back_to_ip_without_identity_device(self):
        request = SimpleNamespace(
            headers={},
            client=SimpleNamespace(host="203.0.113.10"),
        )

        self.assertEqual(
            request_subjects(request, prefer_identity_device=True),
            ("ip:203.0.113.10",),
        )


class FakeRedis:
    def __init__(self) -> None:
        self.sorted_sets = {}
        self.expires = {}

    async def eval(self, script, numkeys, *keys_and_args):
        key = keys_and_args[0]
        cutoff = float(keys_and_args[1])
        now = float(keys_and_args[2])
        member = str(keys_and_args[3])
        ttl = int(keys_and_args[5])
        values = {
            item_member: score
            for item_member, score in self.sorted_sets.get(key, {}).items()
            if score > cutoff
        }
        values[member] = now
        self.sorted_sets[key] = values
        self.expires[key] = ttl
        oldest = min(values.values()) if values else now
        return [len(values), oldest]


class FailingRedis:
    async def eval(self, script, numkeys, *keys_and_args):
        raise ConnectionError("redis unavailable")


class SecurityRateLimiterTests(unittest.IsolatedAsyncioTestCase):
    def _limiter(self, mode: str, **kwargs) -> SecurityRateLimiter:
        return SecurityRateLimiter(
            mode=mode,
            rules={"auth": RateLimitRule(limit=1, burst=0, window_seconds=60)},
            max_tracked_keys=100,
            cleanup_interval_seconds=60,
            **kwargs,
        )

    async def test_monitor_mode_records_exceeded_without_blocking(self):
        limiter = self._limiter("monitor")

        first = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)

        self.assertFalse(first.exceeded)
        self.assertFalse(first.blocked)
        self.assertTrue(second.exceeded)
        self.assertFalse(second.blocked)
        self.assertEqual(limiter.snapshot()["groups"]["auth"]["exceeded"], 1)

    async def test_enforce_mode_blocks_after_threshold(self):
        limiter = self._limiter("enforce")

        await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)

        self.assertTrue(second.exceeded)
        self.assertTrue(second.blocked)
        self.assertGreater(second.retry_after, 0)
        self.assertEqual(limiter.snapshot()["groups"]["auth"]["blocked"], 1)

    async def test_off_mode_never_tracks_or_blocks(self):
        limiter = self._limiter("off")

        for offset in range(5):
            decision = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0 + offset)

        self.assertFalse(decision.exceeded)
        self.assertFalse(decision.blocked)
        self.assertEqual(limiter.snapshot()["tracked_keys"], 0)

    async def test_redis_storage_shares_limits_between_limiter_instances(self):
        redis = FakeRedis()
        first_limiter = self._limiter("enforce", storage="redis", redis_client=redis)
        second_limiter = self._limiter("enforce", storage="redis", redis_client=redis)

        first = await first_limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = await second_limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)

        self.assertFalse(first.blocked)
        self.assertTrue(second.exceeded)
        self.assertTrue(second.blocked)
        self.assertEqual(first_limiter.snapshot()["storage"], "redis")
        self.assertEqual(second_limiter.snapshot()["groups"]["auth"]["blocked"], 1)

    async def test_redis_failure_falls_back_to_local_memory(self):
        limiter = self._limiter("enforce", storage="redis", redis_client=FailingRedis())

        first = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=100.0)
        second = await limiter.check(group="auth", subjects=("ip:127.0.0.1",), now=101.0)
        snapshot = limiter.snapshot()

        self.assertFalse(first.blocked)
        self.assertTrue(second.blocked)
        self.assertEqual(snapshot["storage"], "memory")
        self.assertTrue(snapshot["redis_fallback_active"])
        self.assertEqual(snapshot["fallback_checks"], 2)
        self.assertGreaterEqual(snapshot["redis_failures"], 1)
        self.assertEqual(snapshot["tracked_keys"], 1)


if __name__ == "__main__":
    unittest.main()
