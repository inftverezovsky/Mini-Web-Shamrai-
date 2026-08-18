import contextlib
import io
import json
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

from src.core.config import Settings
from src.services import telegram_bot


class _JsonResponse:
    def __init__(self, payload: dict):
        self._body = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return self._body


class _RecordingOpener:
    def __init__(self, proxy_url: str, outcomes: list[object], calls: list[tuple[str, object]]):
        self.proxy_url = proxy_url
        self.outcomes = list(outcomes)
        self.calls = calls

    def open(self, request, timeout=None):
        self.calls.append((self.proxy_url, request))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return _JsonResponse(outcome)


class TelegramProxyConfigurationTests(unittest.TestCase):
    def test_proxy_pool_requires_strict_json(self):
        settings = Settings(
            _env_file=None,
            TELEGRAM_PROXY_URLS_JSON="http://user:pass@proxy.example:8080",
            TELEGRAM_ALLOW_INSECURE_HTTP_PROXY=True,
        )

        with self.assertRaisesRegex(RuntimeError, "JSON array"):
            _ = settings.telegram_proxy_urls

    def test_plain_http_proxy_requires_explicit_opt_in(self):
        settings = Settings(
            _env_file=None,
            TELEGRAM_PROXY_URLS_JSON=json.dumps(["http://user:pass@proxy.example:8080"]),
        )

        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_ALLOW_INSECURE_HTTP_PROXY"):
            _ = settings.telegram_proxy_urls

    def test_valid_proxy_pool_is_immutable_and_preserves_order(self):
        values = [
            "http://user-one:pass-one@8.8.8.8:8080",
            "http://user-two:pass-two@1.1.1.1:8081",
        ]
        settings = Settings(
            _env_file=None,
            TELEGRAM_PROXY_URLS_JSON=json.dumps(values),
            TELEGRAM_ALLOW_INSECURE_HTTP_PROXY=True,
        )

        self.assertEqual(settings.telegram_proxy_urls, tuple(values))

    def test_proxy_pool_is_redacted_from_settings_repr_and_json(self):
        settings = Settings(
            _env_file=None,
            TELEGRAM_PROXY_URLS_JSON=json.dumps(["http://secret-user:secret-pass@8.8.8.8:8080"]),
            TELEGRAM_ALLOW_INSECURE_HTTP_PROXY=True,
        )

        rendered = repr(settings) + settings.model_dump_json()
        self.assertNotIn("secret-user", rendered)
        self.assertNotIn("secret-pass", rendered)

    def test_legacy_global_proxy_requires_explicit_pool_migration(self):
        settings = Settings(
            _env_file=None,
            HTTPS_PROXY="http://legacy-user:legacy-pass@legacy.example:8080",
        )

        with self.assertRaisesRegex(RuntimeError, "TELEGRAM_PROXY_URLS_JSON"):
            settings.validate_runtime_security()


class TelegramProxyFailoverTests(unittest.TestCase):
    TOKEN = "987654:unit-test-token-not-a-secret"
    PROXY_ONE = "http://proxy-user-one:proxy-pass-one@8.8.8.8:8080"
    PROXY_TWO = "http://proxy-user-two:proxy-pass-two@1.1.1.1:8080"

    def setUp(self):
        self.preferred_patch = patch.object(
            telegram_bot,
            "_preferred_telegram_proxy_index",
            0,
            create=True,
        )
        self.preferred_patch.start()

    def tearDown(self):
        self.preferred_patch.stop()

    def _patch_openers(self, outcomes_by_proxy: dict[str, list[object]]):
        calls: list[tuple[str, object]] = []
        openers = {
            proxy: _RecordingOpener(proxy, outcomes, calls)
            for proxy, outcomes in outcomes_by_proxy.items()
        }
        openers["direct"] = _RecordingOpener(
            "direct",
            outcomes_by_proxy.get("direct", []),
            calls,
        )

        def proxy_handler(mapping):
            if not mapping:
                return "direct"
            self.assertEqual(mapping["http"], mapping["https"])
            return mapping["https"]

        def build_opener(handler):
            return openers[handler]

        return calls, (
            patch.object(telegram_bot.urllib.request, "ProxyHandler", side_effect=proxy_handler),
            patch.object(telegram_bot.urllib.request, "build_opener", side_effect=build_opener),
        )

    def _settings_patch(self):
        fake_settings = SimpleNamespace(
            TELEGRAM_BOT_TOKEN=self.TOKEN,
            TELEGRAM_API_RETRIES=2,
            TELEGRAM_API_TIMEOUT_SECONDS=3.0,
            has_real_telegram_token=True,
            telegram_proxy_urls=(self.PROXY_ONE, self.PROXY_TWO),
        )
        return patch.object(telegram_bot, "settings", fake_settings)

    def test_direct_mode_retries_only_safe_preconnect_failure(self):
        calls, opener_patches = self._patch_openers({
            "direct": [
                urllib.error.URLError(ConnectionRefusedError(111, "refused")),
                {"ok": True, "result": {"message_id": 1}},
            ],
        })
        fake_settings = SimpleNamespace(
            TELEGRAM_BOT_TOKEN=self.TOKEN,
            TELEGRAM_API_RETRIES=1,
            TELEGRAM_API_TIMEOUT_SECONDS=3.0,
            has_real_telegram_token=True,
            telegram_proxy_urls=(),
        )

        with contextlib.ExitStack() as stack:
            stack.enter_context(patch.object(telegram_bot, "settings", fake_settings))
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api("getMe", {}, retries=1)

        self.assertTrue(result["ok"])
        self.assertEqual([proxy for proxy, _ in calls], ["direct", "direct"])

    def test_connection_refused_fails_over_and_success_is_sticky(self):
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [urllib.error.URLError(ConnectionRefusedError(111, "refused"))],
            self.PROXY_TWO: [
                {"ok": True, "result": {"message_id": 1}},
                {"ok": True, "result": {"message_id": 2}},
            ],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            first = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "one"}, retries=0)
            second = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "two"}, retries=0)

        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE, self.PROXY_TWO, self.PROXY_TWO])

    def test_ambiguous_timeout_does_not_resend_mutating_request(self):
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [TimeoutError("read timed out")],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 2}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "hello"}, retries=0)

        self.assertFalse(result["ok"])
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE])

    def test_proxy_auth_407_fails_over(self):
        proxy_auth_error = urllib.error.HTTPError(
            url="https://api.telegram.org/redacted",
            code=407,
            msg="Proxy Authentication Required",
            hdrs=None,
            fp=io.BytesIO(b""),
        )
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [proxy_auth_error],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 2}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "hello"}, retries=0)

        self.assertTrue(result["ok"])
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE, self.PROXY_TWO])

    def test_telegram_429_does_not_rotate_or_resend(self):
        rate_limit_error = urllib.error.HTTPError(
            url="https://api.telegram.org/redacted",
            code=429,
            msg="Too Many Requests",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"description":"Too Many Requests: retry later"}'),
        )
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [rate_limit_error],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 2}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "hello"}, retries=0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["description"], "Too Many Requests: retry later")
        self.assertEqual(result["http_status"], 429)
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE])

    def test_telegram_http_error_preserves_structured_numeric_code(self):
        bad_request = urllib.error.HTTPError(
            url="https://api.telegram.org/redacted",
            code=400,
            msg="Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"ok":false,"error_code":400,"description":"Bad Request"}'),
        )
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [bad_request],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 2}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "hello"}, retries=0)

        self.assertFalse(result["ok"])
        self.assertEqual(result["error_code"], 400)
        self.assertEqual(result["http_status"], 400)
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE])

    def test_terminal_error_and_logs_never_expose_proxy_credentials(self):
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [TimeoutError(f"timed out via {self.PROXY_ONE}")],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 2}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            warning = stack.enter_context(patch.object(telegram_bot.logger, "warning"))
            result = telegram_bot.call_telegram_api("sendMessage", {"chat_id": 1, "text": "hello"}, retries=0)

        rendered = json.dumps(result) + repr(warning.call_args_list)
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE])
        self.assertNotIn("proxy-user-one", rendered)
        self.assertNotIn("proxy-pass-one", rendered)

    def test_multipart_fails_over_only_after_preconnect_failure(self):
        calls, opener_patches = self._patch_openers({
            self.PROXY_ONE: [urllib.error.URLError(ConnectionRefusedError(111, "refused"))],
            self.PROXY_TWO: [{"ok": True, "result": {"message_id": 7}}],
        })

        with contextlib.ExitStack() as stack:
            stack.enter_context(self._settings_patch())
            for active_patch in opener_patches:
                stack.enter_context(active_patch)
            result = telegram_bot.call_telegram_api_multipart(
                "sendDocument",
                {"chat_id": 1},
                {"document": ("report.txt", b"report-bytes", "text/plain")},
                retries=0,
            )

        self.assertTrue(result["ok"])
        self.assertEqual([proxy for proxy, _ in calls], [self.PROXY_ONE, self.PROXY_TWO])
        self.assertEqual(calls[0][1].data, calls[1][1].data)


if __name__ == "__main__":
    unittest.main()
