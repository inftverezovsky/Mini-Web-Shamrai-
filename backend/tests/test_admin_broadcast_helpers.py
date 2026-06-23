import unittest
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, status

from src.services.admin_broadcast_helpers import (
    _decode_forecast_request_cursor,
    _encode_forecast_request_cursor,
    _merge_bookmaker_ids,
    _parse_bookmaker_id_values,
    _parse_optional_fair_coefficient,
    _web_push_report_values,
)


@dataclass
class _CursorSource:
    id: object
    created_at: datetime


class AdminBroadcastHelperTests(unittest.TestCase):
    def test_forecast_request_cursor_roundtrip(self):
        source = _CursorSource(id=uuid4(), created_at=datetime(2026, 6, 23, 12, 30, tzinfo=timezone.utc))

        decoded = _decode_forecast_request_cursor(_encode_forecast_request_cursor(source))

        self.assertEqual(decoded, (source.created_at, source.id))

    def test_invalid_cursor_returns_bad_request(self):
        with self.assertRaises(HTTPException) as context:
            _decode_forecast_request_cursor("not-a-valid-cursor")

        self.assertEqual(context.exception.status_code, status.HTTP_400_BAD_REQUEST)

    def test_parse_bookmaker_ids_supports_commas_and_dedupes(self):
        self.assertEqual(_parse_bookmaker_id_values(["1,2", "2", " 3 "]), [1, 2, 3])

    def test_parse_bookmaker_ids_rejects_invalid_values(self):
        with self.assertRaises(HTTPException) as context:
            _parse_bookmaker_id_values(["1,abc"])

        self.assertEqual(context.exception.status_code, status.HTTP_400_BAD_REQUEST)

    def test_merge_bookmaker_ids_keeps_order_and_dedupes(self):
        self.assertEqual(_merge_bookmaker_ids(2, [1, 2, 3]), [2, 1, 3])

    def test_parse_optional_fair_coefficient_validates_range(self):
        self.assertEqual(str(_parse_optional_fair_coefficient("1.75")), "1.75")
        with self.assertRaises(HTTPException):
            _parse_optional_fair_coefficient("0.99")

    def test_web_push_report_values_normalizes_missing_fields(self):
        payload = _web_push_report_values({"web_push_sent": "2", "web_push_errors": ["offline"]})

        self.assertEqual(payload["web_push_audience"], 0)
        self.assertEqual(payload["web_push_sent"], 2)
        self.assertEqual(payload["web_push_errors"], ["offline"])


if __name__ == "__main__":
    unittest.main()
