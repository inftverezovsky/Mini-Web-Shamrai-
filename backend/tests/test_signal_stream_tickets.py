import unittest
from unittest.mock import patch

from pydantic import ValidationError

from src.api import signals


class SignalStreamTicketTests(unittest.TestCase):
    def tearDown(self):
        signals._signal_stream_tickets.clear()

    def test_signal_stream_ticket_is_single_use(self):
        ticket = signals._issue_signal_stream_ticket(123)

        self.assertEqual(signals._consume_signal_stream_ticket(ticket), 123)
        self.assertIsNone(signals._consume_signal_stream_ticket(ticket))

    def test_signal_stream_ticket_expires(self):
        with patch.object(signals.time, "monotonic", return_value=100.0):
            ticket = signals._issue_signal_stream_ticket(123)

        with patch.object(signals.time, "monotonic", return_value=200.0):
            self.assertIsNone(signals._consume_signal_stream_ticket(ticket))


class WebPushSubscriptionValidationTests(unittest.TestCase):
    def _valid_payload(self, **overrides):
        payload = {
            "endpoint": "https://updates.push.services.mozilla.com/wpush/v2/abcdef",
            "keys": {
                "p256dh": "a" * 88,
                "auth": "b" * 24,
            },
        }
        payload.update(overrides)
        return payload

    def test_web_push_https_endpoint_passes(self):
        subscription = signals.WebPushSubscriptionPayload(**self._valid_payload())

        self.assertEqual(subscription.endpoint, "https://updates.push.services.mozilla.com/wpush/v2/abcdef")

    def test_web_push_rejects_bad_schemes(self):
        for endpoint in (
            "http://updates.push.services.mozilla.com/wpush/v2/abcdef",
            "javascript:alert(1)",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValidationError):
                    signals.WebPushSubscriptionPayload(**self._valid_payload(endpoint=endpoint))

    def test_web_push_rejects_local_private_and_reserved_hosts(self):
        for endpoint in (
            "https://localhost/push",
            "https://127.0.0.1/push",
            "https://10.1.2.3/push",
            "https://[::1]/push",
            "https://example.invalid/push",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(ValidationError):
                    signals.WebPushSubscriptionPayload(**self._valid_payload(endpoint=endpoint))

    def test_web_push_rejects_oversized_payload_parts(self):
        with self.assertRaises(ValidationError):
            signals.WebPushSubscriptionPayload(**self._valid_payload(endpoint=f"https://example.com/{'a' * 2050}"))

        with self.assertRaises(ValidationError):
            signals.WebPushSubscriptionPayload(
                **self._valid_payload(keys={"p256dh": "a" * 513, "auth": "b" * 24})
            )


if __name__ == "__main__":
    unittest.main()
