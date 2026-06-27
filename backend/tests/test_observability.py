import asyncio
import io
import json
import logging
import time
import unittest
from unittest.mock import patch

from starlette.requests import Request
from starlette.responses import Response

from src.core.security import create_access_token


class StructuredLoggingTests(unittest.TestCase):
    def test_json_formatter_redacts_known_secret_shapes(self):
        from src.core.observability import JsonLogFormatter

        record = logging.LogRecord(
            name="uvicorn",
            level=logging.WARNING,
            pathname=__file__,
            lineno=12,
            msg=(
                "provider failed token=123456:ABCdef_ghi-jklmnop "
                "database_url=postgresql://user:pass@db/app"
            ),
            args=(),
            exc_info=None,
        )
        record.request_id = "request-1"
        record.user_id = 12345
        record.payload = {
            "VK_CALLBACK_SECRET": "vk-secret-value",
            "Authorization": "Bearer abcdefghijklmnopqrstuvwxyz",
            "safe": "kept",
        }

        payload = json.loads(JsonLogFormatter().format(record))
        rendered = json.dumps(payload, ensure_ascii=False)

        self.assertEqual(payload["request_id"], "request-1")
        self.assertEqual(payload["user_id"], 12345)
        self.assertEqual(payload["payload"]["safe"], "kept")
        self.assertNotIn("ABCdef_ghi", rendered)
        self.assertNotIn("user:pass", rendered)
        self.assertNotIn("vk-secret-value", rendered)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz", rendered)

    def test_request_id_normalization_preserves_uuid_and_replaces_invalid_value(self):
        from src.core.observability import normalize_request_id

        valid = "11111111-1111-4111-8111-111111111111"
        generated = normalize_request_id("not-a-request-id")

        self.assertEqual(normalize_request_id(valid), valid)
        self.assertNotEqual(generated, "not-a-request-id")
        self.assertEqual(len(generated), 36)


class RequestObservabilityMiddlewareTests(unittest.TestCase):
    def setUp(self):
        from src.services import observability_alerts

        observability_alerts.reset_observability_state()

    def tearDown(self):
        from src.services import observability_alerts

        observability_alerts.reset_observability_state()

    def test_request_id_is_echoed_user_id_is_logged_and_5xx_bucket_is_recorded(self):
        from src.core.observability import JsonLogFormatter, RequestObservabilityMiddleware
        from src.services.observability_alerts import get_http_5xx_bucket_snapshot

        token = create_access_token({"sub": "12345", "role": "admin"})
        log_stream = io.StringIO()
        handler = logging.StreamHandler(log_stream)
        handler.setFormatter(JsonLogFormatter())
        logger = logging.getLogger("uvicorn")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            middleware = RequestObservabilityMiddleware(app=lambda scope, receive, send: None)
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/service-unavailable",
                "raw_path": b"/service-unavailable",
                "query_string": b"",
                "scheme": "http",
                "server": ("testserver", 80),
                "client": ("127.0.0.1", 50100),
                "headers": [
                    (b"x-request-id", b"11111111-1111-4111-8111-111111111111"),
                    (b"authorization", f"Bearer {token}".encode("utf-8")),
                ],
            }
            request = Request(scope, receive=lambda: None)

            async def call_next(_request):
                return Response(status_code=503)

            response = asyncio.run(middleware.dispatch(request, call_next))
        finally:
            logger.removeHandler(handler)

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["X-Request-ID"], "11111111-1111-4111-8111-111111111111")
        self.assertTrue(get_http_5xx_bucket_snapshot())

        log_payloads = [
            json.loads(line)
            for line in log_stream.getvalue().splitlines()
            if line.strip()
        ]
        request_log = next(item for item in log_payloads if item.get("event") == "http_request")
        rendered = json.dumps(request_log, ensure_ascii=False)
        self.assertEqual(request_log["request_id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(request_log["user_id"], "12345")
        self.assertEqual(request_log["status_code"], 503)
        self.assertNotIn(token, rendered)


class ObservabilityAlertTests(unittest.TestCase):
    def setUp(self):
        from src.services import observability_alerts

        observability_alerts.reset_observability_state()

    def tearDown(self):
        from src.services import observability_alerts

        observability_alerts.reset_observability_state()

    def test_delivery_outbox_thresholds_and_growth_alerts(self):
        from src.services.observability_alerts import build_observability_alert_payload

        metrics_template = {
            "oldest_pending_age_seconds": 1200,
            "retry_rate": 25.0,
            "fail_rate": 6.0,
            "by_channel": {
                "telegram_message": {"pending": 55, "retry": 1},
                "vk_message": {"pending": 3},
            },
        }

        with (
            patch("src.services.observability_alerts.settings.OBSERVABILITY_OUTBOX_QUEUE_WARNING", 50),
            patch("src.services.observability_alerts.settings.OBSERVABILITY_OUTBOX_QUEUE_CRITICAL", 100),
        ):
            build_observability_alert_payload(delivery_metrics={**metrics_template, "queue_depth": 50})
            build_observability_alert_payload(delivery_metrics={**metrics_template, "queue_depth": 60})
            payload = build_observability_alert_payload(delivery_metrics={**metrics_template, "queue_depth": 70})

        keys = {alert["key"] for alert in payload["alerts"]}
        self.assertEqual(payload["overall_status"], "warning")
        self.assertIn("delivery_outbox.queue_depth", keys)
        self.assertIn("delivery_outbox.oldest_pending_age", keys)
        self.assertIn("delivery_outbox.retry_rate", keys)
        self.assertIn("delivery_outbox.fail_rate", keys)
        self.assertIn("delivery_outbox.channel_queue_depth", keys)
        self.assertIn("delivery_outbox.queue_growth", keys)

    def test_integration_payment_and_5xx_alerts(self):
        from src.services import observability_alerts

        for _ in range(3):
            observability_alerts.record_integration_probe("telegram.webhook", ok=False)
            observability_alerts.record_integration_probe("vk.api", ok=False)

        observability_alerts.record_payment_mismatch("tegro", "bad_signature", attempt_id="attempt-1")
        base_minute = int(time.time() // 60) - 2
        observability_alerts.record_http_5xx(status_code=503, bucket_minute=base_minute)
        observability_alerts.record_http_5xx(status_code=502, bucket_minute=base_minute + 1)
        observability_alerts.record_http_5xx(status_code=500, bucket_minute=base_minute + 2)

        payload = observability_alerts.build_observability_alert_payload()
        keys = {alert["key"] for alert in payload["alerts"]}

        self.assertIn("integration.telegram.webhook", keys)
        self.assertIn("integration.vk.api", keys)
        self.assertIn("payments.webhook_mismatch", keys)
        self.assertIn("http.5xx", keys)
        self.assertNotIn("bad-secret", json.dumps(payload, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
