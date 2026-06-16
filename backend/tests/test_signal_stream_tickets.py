import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
