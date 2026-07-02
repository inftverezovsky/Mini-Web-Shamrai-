import unittest
from decimal import Decimal

from pydantic import ValidationError

from src.schemas.schemas import (
    ABTestConfigCreate,
    AdminGrantRequest,
    AnnouncementCreate,
    BetCreate,
    BetOddsDropUpdate,
    BookmakerLink,
    SubscriptionPlanCreate,
    SubscriptionPlanUpdate,
    UserNoteCreate,
    UserUpdateBankroll,
)


class SchemaValidationBoundaryTests(unittest.TestCase):
    def test_regular_bet_payload_still_passes(self):
        bet = BetCreate(
            event_name="Team A - Team B",
            coefficient=Decimal("1.80"),
            bookmaker_ids=[1, 2],
            description="Short analytics",
            sport_type="football",
            outcome="Team A win",
            match_link="https://example.com/match/123",
            bookmaker_links=[BookmakerLink(bookmaker_id=1, url="https://bookmaker.example/match/123")],
        )

        self.assertEqual(bet.event_name, "Team A - Team B")
        self.assertEqual(bet.coefficient, Decimal("1.80"))

    def test_bet_payload_allows_missing_or_blank_event_name(self):
        missing = BetCreate(coefficient=Decimal("1.80"))
        blank = BetCreate(event_name="   ", coefficient=Decimal("1.80"))

        self.assertIsNone(missing.event_name)
        self.assertEqual(blank.event_name, "   ")

    def test_bet_rejects_oversized_text_and_bad_link_scheme(self):
        with self.assertRaises(ValidationError):
            BetCreate(event_name="A" * 201, coefficient=Decimal("1.80"))

        with self.assertRaises(ValidationError):
            BetCreate(
                event_name="Team A - Team B",
                coefficient=Decimal("1.80"),
                match_link="javascript:alert(1)",
            )

        with self.assertRaises(ValidationError):
            BookmakerLink(bookmaker_id=1, url="ftp://bookmaker.example/match/123")

    def test_numeric_bounds_reject_obvious_outliers(self):
        with self.assertRaises(ValidationError):
            BetCreate(event_name="Team A - Team B", coefficient=Decimal("0.99"))

        with self.assertRaises(ValidationError):
            BetOddsDropUpdate(odds_dropped_to=Decimal("1000"))

        with self.assertRaises(ValidationError):
            UserUpdateBankroll(bankroll=-1)

    def test_user_note_bounds(self):
        note = UserNoteCreate(text="Good read", emotion_score=3)

        self.assertEqual(note.emotion_score, 3)

        with self.assertRaises(ValidationError):
            UserNoteCreate(text="x" * 4001, emotion_score=3)

        with self.assertRaises(ValidationError):
            UserNoteCreate(text="Good read", emotion_score=6)

    def test_subscription_and_admin_bounds(self):
        plan = SubscriptionPlanCreate(
            name="VIP",
            match_count=1,
            price=Decimal("100.00"),
            price_stars=100,
            currency="RUB",
        )

        self.assertEqual(plan.name, "VIP")

        with self.assertRaises(ValidationError):
            SubscriptionPlanCreate(name="VIP", match_count=0, price=Decimal("100.00"), price_stars=100)

        with self.assertRaises(ValidationError):
            SubscriptionPlanUpdate(price_stars=-1)

        with self.assertRaises(ValidationError):
            ABTestConfigCreate(plan_id=1, price_group_a=-1, price_group_b=100)

        with self.assertRaises(ValidationError):
            AdminGrantRequest(telegram_id=1, username="u" * 65)

    def test_announcement_bounds(self):
        announcement = AnnouncementCreate(
            title="Line moved",
            body="Short update",
            announcement_type="general",
            min_coef=1.5,
            match_link="https://example.com/match",
        )

        self.assertEqual(announcement.title, "Line moved")

        with self.assertRaises(ValidationError):
            AnnouncementCreate(title="T" * 161)

        with self.assertRaises(ValidationError):
            AnnouncementCreate(title="Line moved", match_link="file:///etc/passwd")


if __name__ == "__main__":
    unittest.main()
