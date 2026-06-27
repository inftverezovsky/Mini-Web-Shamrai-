import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.models.database import Base
from src.models.models import (
    Bet,
    CrowdBet,
    CrowdBetParticipant,
    MatchBalanceLog,
    PaymentAttempt,
    Subscription,
    SubscriptionPlan,
    User,
    user_bets,
)
from src.services.payment_reconciliation import (
    ISSUE_LOCAL_SUCCEEDED_ENTITLEMENT_MISMATCH,
    ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT,
    ISSUE_PENDING_STALE,
    ISSUE_PROCESSING_STALE,
    ISSUE_PROVIDER_LOCAL_AMOUNT_MISMATCH,
    ISSUE_PROVIDER_SUCCEEDED_LOCAL_NOT_SUCCEEDED,
    build_payment_reconciliation_report,
    build_payment_reconciliation_summary,
)


class PaymentReconciliationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)

    def _user(self, user_id: int = 101) -> User:
        return User(telegram_id=user_id, username=f"user_{user_id}", role="user")

    def _plan(self, plan_id: int = 1) -> SubscriptionPlan:
        return SubscriptionPlan(
            id=plan_id,
            name=f"Plan {plan_id}",
            duration_days=30,
            match_count=3,
            price=Decimal("900.00"),
            currency="RUB",
            is_active=True,
        )

    def _bet(self) -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Team A - Team B",
            coefficient=Decimal("1.90"),
            status="pending",
            description="Paid analysis",
            price_stars=50,
        )

    def _attempt(
        self,
        *,
        user_id: int = 101,
        plan_id: int | None = 1,
        bet_id=None,
        provider: str = "yookassa",
        provider_payment_id: str | None = None,
        amount: Decimal = Decimal("900.00"),
        currency: str = "RUB",
        status: str = "pending",
        minutes_ago: int = 0,
        metadata_json: dict | None = None,
        processing_minutes_ago: int | None = None,
    ) -> PaymentAttempt:
        now = self._now()
        return PaymentAttempt(
            user_id=user_id,
            plan_id=plan_id,
            bet_id=bet_id,
            provider=provider,
            provider_payment_id=provider_payment_id,
            amount=amount,
            currency=currency,
            status=status,
            metadata_json=metadata_json or {},
            created_at=now - timedelta(minutes=minutes_ago),
            updated_at=now - timedelta(minutes=minutes_ago),
            processing_started_at=(
                now - timedelta(minutes=processing_minutes_ago)
                if processing_minutes_ago is not None
                else None
            ),
            processed_at=now if status == "succeeded" else None,
        )

    async def test_reports_stale_pending_and_processing_attempts(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._plan()
            stale_pending = self._attempt(minutes_ago=20)
            fresh_pending = self._attempt(provider_payment_id="fresh", minutes_ago=3)
            stale_processing = self._attempt(
                provider_payment_id="processing",
                status="processing",
                minutes_ago=8,
                processing_minutes_ago=8,
            )
            session.add_all([user, plan, stale_pending, fresh_pending, stale_processing])
            await session.commit()

            report = await build_payment_reconciliation_report(session, include_provider_checks=False)

        issue_codes = [issue.code for issue in report.issues]
        self.assertIn(ISSUE_PENDING_STALE, issue_codes)
        self.assertIn(ISSUE_PROCESSING_STALE, issue_codes)
        self.assertEqual(issue_codes.count(ISSUE_PENDING_STALE), 1)

    async def test_reports_succeeded_plan_without_subscription_or_ledger(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._plan()
            attempt = self._attempt(
                provider_payment_id="pay-plan",
                status="succeeded",
                minutes_ago=1,
            )
            session.add_all([user, plan, attempt])
            await session.commit()

            report = await build_payment_reconciliation_report(session, include_provider_checks=False)

        self.assertEqual([issue.code for issue in report.issues], [ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT])
        self.assertEqual(report.issues[0].purchase_type, "subscription")

    async def test_reports_succeeded_plan_with_subscription_but_missing_ledger(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._plan()
            attempt = self._attempt(
                provider_payment_id="pay-plan",
                status="succeeded",
                minutes_ago=1,
            )
            subscription = Subscription(
                user_id=user.telegram_id,
                plan_id=plan.id,
                status="active",
                payment_provider="yookassa",
                payment_id="pay-plan",
            )
            session.add_all([user, plan, attempt, subscription])
            await session.commit()

            report = await build_payment_reconciliation_report(session, include_provider_checks=False)

        self.assertEqual([issue.code for issue in report.issues], [ISSUE_LOCAL_SUCCEEDED_ENTITLEMENT_MISMATCH])

    async def test_single_bet_requires_user_bet_access_but_bet_hint_does_not(self):
        async with self.Session() as session:
            user = self._user()
            single_bet = self._bet()
            hint_bet = self._bet()
            single_attempt = self._attempt(
                plan_id=None,
                bet_id=single_bet.id,
                provider="telegram_stars",
                provider_payment_id="single-pay",
                amount=Decimal("50.00"),
                currency="XTR",
                status="succeeded",
                minutes_ago=1,
            )
            hint_attempt = self._attempt(
                plan_id=None,
                bet_id=hint_bet.id,
                provider="telegram_stars",
                provider_payment_id="hint-pay",
                amount=Decimal("1.00"),
                currency="XTR",
                status="succeeded",
                minutes_ago=1,
                metadata_json={"purchase_type": "bet_hint"},
            )
            session.add_all([user, single_bet, hint_bet, single_attempt, hint_attempt])
            await session.commit()

            report = await build_payment_reconciliation_report(session, include_provider_checks=False)

        issues_by_attempt = {issue.attempt_id: issue for issue in report.issues}
        self.assertEqual(len(issues_by_attempt), 1)
        self.assertEqual(next(iter(issues_by_attempt.values())).purchase_type, "single_bet")

    async def test_crowd_bet_requires_participant_contribution(self):
        async with self.Session() as session:
            user = self._user()
            bet = self._bet()
            crowd_bet = CrowdBet(bet=bet, target_amount=100, current_amount=50, status="funding")
            missing_attempt = self._attempt(
                plan_id=None,
                bet_id=bet.id,
                provider="telegram_stars",
                provider_payment_id="crowd-missing",
                amount=Decimal("50.00"),
                currency="XTR",
                status="succeeded",
                minutes_ago=1,
                metadata_json={"purchase_type": "crowd_bet", "crowd_bet_id": 1},
            )
            session.add_all([user, bet, crowd_bet, missing_attempt])
            await session.commit()

            missing_report = await build_payment_reconciliation_report(session, include_provider_checks=False)

            session.add(CrowdBetParticipant(crowd_bet_id=crowd_bet.id, user_id=user.telegram_id, contributed_amount=50))
            await session.commit()
            clean_report = await build_payment_reconciliation_report(session, include_provider_checks=False)

        self.assertEqual([issue.code for issue in missing_report.issues], [ISSUE_LOCAL_SUCCEEDED_MISSING_ENTITLEMENT])
        self.assertEqual(clean_report.issues, [])

    async def test_provider_reconciliation_uses_mocked_yookassa_fetcher(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._plan()
            local_pending = self._attempt(
                provider_payment_id="provider-success",
                status="pending",
                minutes_ago=2,
            )
            amount_mismatch = self._attempt(
                provider_payment_id="provider-amount-mismatch",
                status="pending",
                amount=Decimal("900.00"),
                minutes_ago=2,
            )
            session.add_all([user, plan, local_pending, amount_mismatch])
            await session.commit()

            def fetcher(payment_id: str) -> dict:
                amounts = {
                    "provider-success": "900.00",
                    "provider-amount-mismatch": "1200.00",
                }
                return {
                    "id": payment_id,
                    "status": "succeeded",
                    "amount": {"value": amounts[payment_id], "currency": "RUB"},
                }

            report = await build_payment_reconciliation_report(
                session,
                include_provider_checks=True,
                yookassa_status_fetcher=fetcher,
            )

        issue_codes = [issue.code for issue in report.issues]
        self.assertIn(ISSUE_PROVIDER_SUCCEEDED_LOCAL_NOT_SUCCEEDED, issue_codes)
        self.assertIn(ISSUE_PROVIDER_LOCAL_AMOUNT_MISMATCH, issue_codes)

    async def test_summary_counts_local_issues_without_provider_calls(self):
        async with self.Session() as session:
            user = self._user()
            plan = self._plan()
            stale_pending = self._attempt(minutes_ago=20)
            clean_attempt = self._attempt(
                provider_payment_id="clean-plan",
                status="succeeded",
                minutes_ago=1,
            )
            subscription = Subscription(
                user_id=user.telegram_id,
                plan_id=plan.id,
                status="active",
                payment_provider="yookassa",
                payment_id="clean-plan",
            )
            session.add_all([user, plan, stale_pending, clean_attempt, subscription])
            await session.flush()
            session.add(MatchBalanceLog(
                user_id=user.telegram_id,
                subscription_id=subscription.id,
                delta_matches=plan.match_count,
                event_type="subscription_purchase",
            ))
            await session.commit()

            summary = await build_payment_reconciliation_summary(session)

        self.assertEqual(summary.total_issues, 1)
        self.assertEqual(summary.by_code[ISSUE_PENDING_STALE], 1)
        self.assertFalse(summary.provider_checks_included)


if __name__ == "__main__":
    unittest.main()
