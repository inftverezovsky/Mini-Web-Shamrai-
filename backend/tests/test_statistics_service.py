import unittest
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

from src.services.statistics import (
    build_performance_payload,
    filter_items_by_period,
    is_paid_client_access,
    normalize_period,
    period_start,
    stat_item_from_bet,
    summarize_items,
)


def _bet(
    *,
    status,
    coefficient="2.00",
    created_at=None,
    resolved_at=None,
    delivery_mode="feed",
    publication_type="forecast",
):
    bookmaker = SimpleNamespace(id=1, name="Фонбет", code="fonbet")
    return SimpleNamespace(
        id=uuid4(),
        event_name="Team A - Team B",
        status=status,
        coefficient=Decimal(coefficient),
        created_at=created_at or datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
        resolved_at=resolved_at,
        delivery_mode=delivery_mode,
        publication_type=publication_type,
        sport_type="Футбол",
        outcome="П1",
        description="Detailed forecast",
        match_link="https://example.com/match",
        bookmaker_id=1,
        bookmaker_links=[{"bookmaker_id": 1, "url": "https://fonbet.example/match"}],
        bookmakers=[bookmaker],
        bookmaker=bookmaker,
    )


class StatisticsServiceTests(unittest.TestCase):
    def test_refunds_and_pending_do_not_create_stat_items(self):
        resolved_at = datetime(2026, 6, 10, 18, tzinfo=timezone.utc)

        self.assertIsNone(stat_item_from_bet(_bet(status="refund", resolved_at=resolved_at)))
        self.assertIsNone(stat_item_from_bet(_bet(status="pending", resolved_at=None)))
        self.assertIsNone(stat_item_from_bet(_bet(status="win", resolved_at=resolved_at, publication_type="text")))
        self.assertIsNotNone(stat_item_from_bet(_bet(status="win", resolved_at=resolved_at)))

    def test_stat_item_includes_admin_edit_fields(self):
        item = stat_item_from_bet(_bet(
            status="win",
            coefficient="1.90",
            resolved_at=datetime(2026, 6, 10, 18, tzinfo=timezone.utc),
        ))

        self.assertEqual(item["bookmaker_id"], 1)
        self.assertEqual(item["description"], "Detailed forecast")
        self.assertEqual(item["match_link"], "https://example.com/match")
        self.assertEqual(item["bookmaker_links"], [{"bookmaker_id": 1, "url": "https://fonbet.example/match"}])

    def test_fonbet_is_primary_bookmaker_when_present(self):
        fonbet = SimpleNamespace(id=1, name="Фонбет", code="fonbet")
        betboom = SimpleNamespace(id=2, name="БетБум", code="betboom")
        winline = SimpleNamespace(id=3, name="Винлайн", code="winline")
        bet = _bet(status="win", coefficient="1.90", resolved_at=datetime(2026, 6, 10, 18, tzinfo=timezone.utc))
        bet.bookmakers = [betboom, fonbet, winline]
        bet.bookmaker = betboom

        item = stat_item_from_bet(bet)

        self.assertEqual(item["bookmaker_names"], ["Фонбет"])
        self.assertEqual(item["bookmakers"], [{"id": 1, "name": "Фонбет", "code": "fonbet"}])

    def test_summary_uses_unit_stake_roi_for_win_loss_only(self):
        resolved_at = datetime(2026, 6, 10, 18, tzinfo=timezone.utc)
        items = [
            stat_item_from_bet(_bet(status="win", coefficient="2.50", resolved_at=resolved_at)),
            stat_item_from_bet(_bet(status="loss", coefficient="1.80", resolved_at=resolved_at)),
            stat_item_from_bet(_bet(status="refund", coefficient="2.00", resolved_at=resolved_at)),
        ]
        summary = summarize_items([item for item in items if item])

        self.assertEqual(summary["bets"], 2)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["profit_units"], 0.5)
        self.assertEqual(summary["roi"], 25.0)
        self.assertEqual(summary["winrate"], 50.0)
        self.assertEqual(summary["average_coefficient"], 2.15)

    def test_payload_keeps_author_and_client_aggregate_math_consistent(self):
        resolved_at = datetime(2026, 6, 10, 18, tzinfo=timezone.utc)
        items = [
            stat_item_from_bet(_bet(status="win", coefficient="2.20", resolved_at=resolved_at, delivery_mode="feed")),
            stat_item_from_bet(_bet(status="loss", coefficient="1.80", resolved_at=resolved_at, delivery_mode="sales_private")),
            stat_item_from_bet(_bet(status="win", coefficient="1.50", resolved_at=resolved_at, delivery_mode="paid_set")),
        ]
        payload = build_performance_payload([item for item in items if item], period="all")

        self.assertEqual(payload["summary"]["bets"], 3)
        self.assertEqual(payload["summary"]["wins"], 2)
        self.assertEqual(payload["summary"]["losses"], 1)
        self.assertEqual(payload["summary"]["profit_units"], 0.7)
        self.assertEqual(payload["summary"]["winrate"], 66.67)
        self.assertEqual(payload["summary"]["roi"], 23.33)

        self.assertEqual(payload["source_split"]["feed"]["bets"], 1)
        self.assertEqual(payload["source_split"]["feed"]["profit_units"], 1.2)
        self.assertEqual(payload["source_split"]["feed"]["roi"], 120.0)
        self.assertEqual(payload["source_split"]["private"]["bets"], 1)
        self.assertEqual(payload["source_split"]["private"]["profit_units"], -1.0)
        self.assertEqual(payload["source_split"]["private"]["roi"], -100.0)
        self.assertEqual(payload["source_split"]["paid_set"]["bets"], 1)
        self.assertEqual(payload["source_split"]["paid_set"]["profit_units"], 0.5)
        self.assertEqual(payload["source_split"]["paid_set"]["roi"], 50.0)
        self.assertEqual(items[2]["source_type"], "paid_set")

        month_summary = payload["timeline"][0]["summary"]
        day_summary = payload["timeline"][0]["days"][0]["summary"]
        self.assertEqual(month_summary, payload["summary"])
        self.assertEqual(day_summary, payload["summary"])

    def test_paid_client_access_detection(self):
        self.assertTrue(is_paid_client_access("paid_match", True))
        self.assertTrue(is_paid_client_access("telegram_stars_single_bet", False))
        self.assertTrue(is_paid_client_access("debug_single_bet", False))
        self.assertTrue(is_paid_client_access("unknown", True))
        self.assertFalse(is_paid_client_access("free_bet", False))
        self.assertFalse(is_paid_client_access("guarantee_replacement", False))
        self.assertFalse(is_paid_client_access("admin", True))

    def test_timeline_groups_by_resolved_at_in_moscow_timezone(self):
        item = stat_item_from_bet(_bet(
            status="win",
            coefficient="1.90",
            created_at=datetime(2026, 1, 1, 12, tzinfo=timezone.utc),
            resolved_at=datetime(2026, 6, 9, 22, 30, tzinfo=timezone.utc),
        ))
        payload = build_performance_payload([item])

        self.assertEqual(payload["timeline"][0]["key"], "2026-06")
        self.assertEqual(payload["timeline"][0]["days"][0]["key"], "2026-06-10")
        self.assertEqual(payload["timeline"][0]["days"][0]["bets"][0]["profit_units"], 0.9)

    def test_period_start_uses_moscow_calendar_boundaries(self):
        now = datetime(2026, 6, 10, 9, tzinfo=timezone.utc)

        self.assertEqual(period_start("week", now=now).isoformat(), "2026-06-08T00:00:00+03:00")
        self.assertEqual(period_start("month", now=now).isoformat(), "2026-06-01T00:00:00+03:00")
        self.assertEqual(period_start("quarter", now=now).isoformat(), "2026-04-01T00:00:00+03:00")
        self.assertIsNone(period_start("all", now=now))
        self.assertEqual(normalize_period("bad-value"), "all")

    def test_filter_items_by_period_uses_resolved_at(self):
        now = datetime(2026, 6, 10, 12, tzinfo=timezone.utc)
        items = [
            {"resolved_at": "2026-06-10T14:00:00+03:00", "status": "win"},
            {"resolved_at": "2026-06-02T14:00:00+03:00", "status": "loss"},
            {"resolved_at": "2026-03-20T14:00:00+03:00", "status": "win"},
        ]

        self.assertEqual(len(filter_items_by_period(items, "week", now=now)), 1)
        self.assertEqual(len(filter_items_by_period(items, "month", now=now)), 2)
        self.assertEqual(len(filter_items_by_period(items, "quarter", now=now)), 2)
        self.assertEqual(len(filter_items_by_period(items, "all", now=now)), 3)


if __name__ == "__main__":
    unittest.main()
