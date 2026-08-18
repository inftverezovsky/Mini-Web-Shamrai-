import inspect
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.api import admin as admin_api
from src.api import bets as bets_api
from src.models.database import Base
from src.models.models import AdminAuditLog, Bet, FlatSubscriptionCredit, ForecastRequest, ForecastStakeInputSession, PersonalSignal, Subscription, User, user_bets
from src.services.flat_subscriptions import (
    FlatSubscriptionState,
    activate_pending_flat_subscription_if_eligible,
    calculate_flat_result,
    configure_flat_subscription,
    count_pending_flat_bets,
    correct_flat_bet_stake,
    credit_flat_subscription,
    flat_subscription_payload,
    get_forecast_stake_input,
    lookup_forecast_stake_input,
    next_flat_subscription_status,
    parse_stake_amount,
    prepare_forecast_request_flat_stake,
    preview_flat_amount_change,
    preview_flat_bet_stake,
    record_user_flat_bet_access,
    reopen_flat_subscription,
    settle_flat_bet_takers,
    start_forecast_stake_input,
)
from src.schemas.schemas import BetResolve, FlatStakeCorrectionRequest, FlatStakeRequest, FlatSubscriptionCreditRequest
from src.services.forecast_delivery import cancel_forecast_request, load_forecast_request
from src.services import flat_subscriptions as flat_subscription_service
from src.services import forecast_delivery as forecast_delivery_service


class FlatSubscriptionMathTests(unittest.TestCase):
    def test_half_flat_win_uses_net_profit(self):
        result = calculate_flat_result(
            stake_rub=Decimal("5000"),
            flat_amount_rub=Decimal("10000"),
            coefficient=Decimal("1.70"),
            result_status="win",
        )

        self.assertEqual(result.stake_flats, Decimal("0.500000"))
        self.assertEqual(result.profit_rub, Decimal("3500.00"))
        self.assertEqual(result.profit_flats, Decimal("0.350000"))

    def test_stake_can_be_greater_than_one_flat(self):
        result = calculate_flat_result(
            stake_rub=Decimal("15000"),
            flat_amount_rub=Decimal("10000"),
            coefficient=Decimal("2.20"),
            result_status="loss",
        )

        self.assertEqual(result.stake_flats, Decimal("1.500000"))
        self.assertEqual(result.profit_rub, Decimal("-15000.00"))
        self.assertEqual(result.profit_flats, Decimal("-1.500000"))

    def test_refund_has_zero_profit(self):
        result = calculate_flat_result(
            stake_rub=Decimal("12500"),
            flat_amount_rub=Decimal("10000"),
            coefficient=Decimal("3.10"),
            result_status="refund",
        )

        self.assertEqual(result.stake_flats, Decimal("1.250000"))
        self.assertEqual(result.profit_rub, Decimal("0.00"))
        self.assertEqual(result.profit_flats, Decimal("0.000000"))

    def test_money_rounding_uses_half_up_at_the_cent_boundary(self):
        result = calculate_flat_result(
            stake_rub=Decimal("1.00"),
            flat_amount_rub=Decimal("1.00"),
            coefficient=Decimal("1.005"),
            result_status="win",
        )

        self.assertEqual(result.profit_rub, Decimal("0.01"))
        self.assertEqual(result.profit_flats, Decimal("0.010000"))

    def test_non_finite_stakes_are_rejected_as_validation_errors(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_stake_amount(value)

    def test_common_russian_stake_formats(self):
        self.assertEqual(parse_stake_amount("5 000"), Decimal("5000.00"))
        self.assertEqual(parse_stake_amount("5000,50"), Decimal("5000.50"))
        self.assertEqual(parse_stake_amount("5к"), Decimal("5000.00"))
        self.assertEqual(parse_stake_amount("5 тыс"), Decimal("5000.00"))

    def test_invalid_stake_amount_is_rejected(self):
        for value in ("", "0", "-10", "много", "100000001"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_stake_amount(value)


class FlatSubscriptionLifecycleTests(unittest.TestCase):
    def test_terminal_request_lock_order_is_guarded_before_flush(self):
        financial_lock_source = inspect.getsource(
            flat_subscription_service.lock_flat_subscription_financial_rows
        )
        terminal_lock_source = inspect.getsource(
            forecast_delivery_service._lock_forecast_terminal_scope
        )
        interest_lock_source = inspect.getsource(
            forecast_delivery_service._lock_forecast_interest_scope
        )
        interest_source = inspect.getsource(
            forecast_delivery_service.set_forecast_request_interested
        )
        delivery_source = inspect.getsource(
            forecast_delivery_service.deliver_forecast_request
        )

        self.assertIn("with db.no_autoflush", financial_lock_source)
        self.assertNotIn("await db.flush()", financial_lock_source)
        self.assertLess(
            terminal_lock_source.index("load_locked_bet_for_user_access"),
            terminal_lock_source.index("lock_user_balance"),
        )
        self.assertLess(
            terminal_lock_source.index("lock_user_balance"),
            terminal_lock_source.index("lock_flat_subscription_financial_rows"),
        )
        self.assertLess(
            terminal_lock_source.index("lock_flat_subscription_financial_rows"),
            terminal_lock_source.index("select(ForecastRequest)"),
        )
        self.assertLess(
            interest_lock_source.index("load_locked_bet_for_user_access"),
            interest_lock_source.index("lock_user_balance"),
        )
        self.assertLess(
            interest_lock_source.index("lock_user_balance"),
            interest_lock_source.index("lock_flat_subscription_financial_rows"),
        )
        self.assertLess(
            interest_lock_source.index("lock_flat_subscription_financial_rows"),
            interest_lock_source.index("select(ForecastRequest)"),
        )
        self.assertLess(
            interest_source.index("_lock_forecast_interest_scope"),
            interest_source.index("prepare_forecast_request_flat_stake"),
        )
        self.assertLess(
            delivery_source.index("_lock_forecast_interest_scope"),
            delivery_source.index("record_user_flat_bet_access"),
        )

        credit_source = inspect.getsource(flat_subscription_service.credit_flat_subscription)
        record_source = inspect.getsource(flat_subscription_service.record_user_flat_bet_access)
        self.assertLess(
            credit_source.index("get_open_flat_subscription"),
            credit_source.index("lock_flat_subscription_financial_rows"),
        )
        self.assertLess(
            credit_source.index("lock_flat_subscription_financial_rows"),
            credit_source.index("refresh_flat_subscription_totals"),
        )
        self.assertLess(
            record_source.index("get_open_flat_subscription"),
            record_source.index("lock_flat_subscription_financial_rows"),
        )
        self.assertLess(
            record_source.index("lock_flat_subscription_financial_rows"),
            record_source.index("select(user_bets)"),
        )

    def test_target_with_open_bets_enters_closing(self):
        self.assertEqual(
            next_flat_subscription_status(
                current_status=FlatSubscriptionState.ACTIVE,
                profit_flats=Decimal("3.00"),
                target_flats=Decimal("3.00"),
                pending_bets=1,
            ),
            FlatSubscriptionState.CLOSING,
        )

    def test_closing_returns_active_when_final_result_drops_below_target(self):
        self.assertEqual(
            next_flat_subscription_status(
                current_status=FlatSubscriptionState.CLOSING,
                profit_flats=Decimal("2.40"),
                target_flats=Decimal("3.00"),
                pending_bets=0,
            ),
            FlatSubscriptionState.ACTIVE,
        )

    def test_closing_completes_after_last_pending_bet(self):
        self.assertEqual(
            next_flat_subscription_status(
                current_status=FlatSubscriptionState.CLOSING,
                profit_flats=Decimal("3.25"),
                target_flats=Decimal("3.00"),
                pending_bets=0,
            ),
            FlatSubscriptionState.COMPLETED,
        )

    def test_completed_subscription_never_reopens_automatically(self):
        self.assertEqual(
            next_flat_subscription_status(
                current_status=FlatSubscriptionState.COMPLETED,
                profit_flats=Decimal("1.00"),
                target_flats=Decimal("3.00"),
                pending_bets=0,
            ),
            FlatSubscriptionState.COMPLETED,
        )


class FlatSubscriptionPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    @staticmethod
    def _bet(*, coefficient: str = "2.00") -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Команда А — Команда Б",
            outcome="Победа команды А",
            coefficient=Decimal(coefficient),
            status="pending",
            delivery_mode="feed",
            publication_type="forecast",
        )

    async def test_persisted_lifecycle_closing_then_active_after_open_loss(self):
        async with self.Session() as session:
            user = User(telegram_id=301, purchased_bets_balance=0, matches_remaining=0)
            first_bet = self._bet()
            second_bet = self._bet()
            session.add_all([user, first_bet, second_bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
                actor_id=900,
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=first_bet,
                stake_rub=Decimal("5000"),
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=second_bet,
                stake_rub=Decimal("5000"),
            )

            await settle_flat_bet_takers(session, bet=first_bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.CLOSING.value)
            self.assertEqual(subscription.profit_flats, Decimal("0.500000"))

            await settle_flat_bet_takers(session, bet=second_bet, result_status="loss")
            self.assertEqual(subscription.status, FlatSubscriptionState.ACTIVE.value)
            self.assertEqual(subscription.profit_flats, Decimal("0.000000"))

    async def test_persisted_lifecycle_closing_then_completed_after_open_refund(self):
        async with self.Session() as session:
            user = User(telegram_id=310, purchased_bets_balance=0, matches_remaining=0)
            first_bet = self._bet()
            second_bet = self._bet()
            session.add_all([user, first_bet, second_bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=first_bet, stake_rub=Decimal("5000"))
            await record_user_flat_bet_access(session, user=user, bet=second_bet, stake_rub=Decimal("5000"))

            await settle_flat_bet_takers(session, bet=first_bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.CLOSING.value)
            with self.assertRaises(HTTPException):
                await record_user_flat_bet_access(
                    session,
                    user=user,
                    bet=self._bet(),
                    stake_rub=Decimal("5000"),
                )

            await settle_flat_bet_takers(session, bet=second_bet, result_status="refund")
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)

    async def test_accepted_delivery_reservation_keeps_subscription_closing_and_can_finish(self):
        async with self.Session() as session:
            user = User(telegram_id=312, purchased_bets_balance=0, matches_remaining=0)
            winning_bet = self._bet()
            reserved_bet = self._bet()
            reserved_request = ForecastRequest(
                bet=reserved_bet,
                user=user,
                status="announced",
            )
            session.add_all([user, winning_bet, reserved_bet, reserved_request])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=winning_bet,
                stake_rub=Decimal("5000"),
            )
            await prepare_forecast_request_flat_stake(
                session,
                forecast_request=reserved_request,
                flat_subscription=subscription,
                stake_rub=Decimal("5000"),
                input_channel="telegram",
            )
            reserved_request.status = "interested"

            await settle_flat_bet_takers(session, bet=winning_bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.CLOSING.value)

            access = await record_user_flat_bet_access(
                session,
                user=user,
                bet=reserved_bet,
                stake_rub=Decimal("5000"),
                forecast_request=reserved_request,
                input_channel="telegram",
            )
            pending_bets = await count_pending_flat_bets(session, subscription.id)

            self.assertFalse(access.already_recorded)
            self.assertEqual(pending_bets, 1)

    async def test_cancelling_reserved_request_releases_closing_subscription_idempotently(self):
        async with self.Session() as session:
            user = User(telegram_id=313, purchased_bets_balance=0, matches_remaining=0)
            winning_bet = self._bet()
            reserved_bet = self._bet()
            reserved_bet.delivery_mode = "sales_private"
            reserved_request = ForecastRequest(
                bet=reserved_bet,
                user=user,
                status="announced",
            )
            session.add_all([user, winning_bet, reserved_bet, reserved_request])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=winning_bet,
                stake_rub=Decimal("5000"),
            )
            await prepare_forecast_request_flat_stake(
                session,
                forecast_request=reserved_request,
                flat_subscription=subscription,
                stake_rub=Decimal("5000"),
                input_channel="telegram",
            )
            reserved_request.status = "interested"

            await settle_flat_bet_takers(session, bet=winning_bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.CLOSING.value)
            revision_before_cancel = subscription.revision

            locked_request = await load_forecast_request(session, reserved_request.id)
            cancelled, _ = await cancel_forecast_request(
                session,
                forecast_request=locked_request,
                handled_by=900,
            )
            await session.flush()

            self.assertEqual(cancelled.status, "cancelled")
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)
            self.assertEqual(await count_pending_flat_bets(session, subscription.id), 0)
            self.assertEqual(subscription.revision, revision_before_cancel + 1)

            cancelled_again, _ = await cancel_forecast_request(
                session,
                forecast_request=cancelled,
                handled_by=900,
            )
            await session.flush()

            self.assertEqual(cancelled_again.status, "cancelled")
            self.assertEqual(subscription.revision, revision_before_cancel + 1)

    async def test_stake_preparation_cannot_overwrite_terminal_request(self):
        async with self.Session() as session:
            user = User(telegram_id=314, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet()
            bet.delivery_mode = "sales_private"
            forecast_request = ForecastRequest(
                bet=bet,
                user=user,
                status="cancelled",
            )
            session.add_all([user, bet, forecast_request])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("1.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            revision_before = subscription.revision

            with self.assertRaises(HTTPException) as raised:
                await prepare_forecast_request_flat_stake(
                    session,
                    forecast_request=forecast_request,
                    flat_subscription=subscription,
                    stake_rub=Decimal("5000"),
                    input_channel="telegram",
                )

            self.assertEqual(raised.exception.status_code, 409)
            self.assertIsNone(forecast_request.flat_subscription_id)
            self.assertIsNone(forecast_request.stake_rub)
            self.assertEqual(subscription.revision, revision_before)

    async def test_result_and_stake_corrections_are_idempotent(self):
        async with self.Session() as session:
            user = User(telegram_id=302, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.35"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
                actor_id=900,
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=bet,
                stake_rub=Decimal("5000"),
            )

            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            first_settled_at = (await session.execute(select(user_bets.c.settled_at))).scalar_one()
            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            second_settled_at = (await session.execute(select(user_bets.c.settled_at))).scalar_one()
            self.assertEqual(subscription.profit_rub, Decimal("3500.00"))
            self.assertEqual(subscription.profit_flats, Decimal("0.350000"))
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)
            self.assertEqual(second_settled_at, first_settled_at)
            settlement_events = list(
                (
                    await session.execute(
                        select(FlatSubscriptionCredit).filter(
                            FlatSubscriptionCredit.event_type == "bet_result_settled"
                        )
                    )
                ).scalars().all()
            )
            self.assertEqual(len(settlement_events), 1)

            await correct_flat_bet_stake(
                session,
                user_id=user.telegram_id,
                bet_id=bet.id,
                stake_rub=Decimal("10000"),
                actor_id=900,
            )
            self.assertEqual(subscription.profit_rub, Decimal("7000.00"))
            self.assertEqual(subscription.profit_flats, Decimal("0.700000"))
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)

    async def test_result_correction_is_audited_and_does_not_auto_reopen_completed(self):
        async with self.Session() as session:
            user = User(telegram_id=307, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="2.00")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            billing_subscription = Subscription(
                user_id=user.telegram_id,
                status=subscription.status,
                flat_subscription_id=subscription.id,
            )
            session.add(billing_subscription)
            await session.flush()
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            await settle_flat_bet_takers(session, bet=bet, result_status="win", actor_id=900)
            await settle_flat_bet_takers(session, bet=bet, result_status="loss", actor_id=900)

            correction = (
                await session.execute(
                    select(FlatSubscriptionCredit).filter(
                        FlatSubscriptionCredit.event_type == "bet_result_corrected"
                    )
                )
            ).scalars().one()
            self.assertEqual(subscription.profit_rub, Decimal("-5000.00"))
            self.assertEqual(subscription.profit_flats, Decimal("-0.500000"))
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)
            self.assertEqual(correction.actor_id, 900)

            reopened = await reopen_flat_subscription(
                session,
                flat_subscription_id=subscription.id,
                actor_id=900,
                note="Reopen after audited correction",
            )
            self.assertEqual(reopened.status, FlatSubscriptionState.ACTIVE.value)
            self.assertEqual(billing_subscription.status, FlatSubscriptionState.ACTIVE.value)

    async def test_repeated_credit_extends_open_target_without_resetting_progress(self):
        async with self.Session() as session:
            user = User(telegram_id=308, purchased_bets_balance=0, matches_remaining=0)
            winning_bet = self._bet(coefficient="1.50")
            session.add_all([user, winning_bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=winning_bet,
                stake_rub=Decimal("5000"),
            )
            await settle_flat_bet_takers(session, bet=winning_bet, result_status="win")

            extended = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("1.50"),
                event_type="subscription_purchase",
            )

            self.assertEqual(extended.id, subscription.id)
            self.assertEqual(extended.target_flats, Decimal("4.50"))
            self.assertEqual(extended.profit_flats, Decimal("0.250000"))
            credits = list(
                (
                    await session.execute(
                        select(FlatSubscriptionCredit).filter(
                            FlatSubscriptionCredit.event_type == "subscription_purchase"
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(credits), 2)

    async def test_repeated_credit_keeps_closing_when_expanded_target_is_still_reached(self):
        async with self.Session() as session:
            user = User(telegram_id=318, purchased_bets_balance=0, matches_remaining=0)
            winning_bet = self._bet(coefficient="3.00")
            pending_bet = self._bet(coefficient="2.00")
            session.add_all([user, winning_bet, pending_bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=winning_bet,
                stake_rub=Decimal("5000"),
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=pending_bet,
                stake_rub=Decimal("5000"),
            )
            await settle_flat_bet_takers(session, bet=winning_bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.CLOSING.value)

            extended = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.25"),
                event_type="subscription_purchase",
            )

            self.assertEqual(extended.target_flats, Decimal("0.75"))
            self.assertEqual(extended.profit_flats, Decimal("1.000000"))
            self.assertEqual(extended.status, FlatSubscriptionState.CLOSING.value)

    async def test_settlement_revision_changes_once_for_same_result(self):
        async with self.Session() as session:
            user = User(telegram_id=319, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            revision_before = subscription.revision

            first_updates = await settle_flat_bet_takers(session, bet=bet, result_status="win")
            revision_after_first = subscription.revision
            second_updates = await settle_flat_bet_takers(session, bet=bet, result_status="win")

            self.assertGreater(revision_after_first, revision_before)
            self.assertEqual(subscription.revision, revision_after_first)
            self.assertEqual(len(first_updates), 1)
            self.assertTrue(first_updates[0].changed)
            self.assertEqual(len(second_updates), 0)

    async def test_refund_settlement_increments_revision_even_when_profit_is_unchanged(self):
        async with self.Session() as session:
            user = User(telegram_id=325, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            revision_before = subscription.revision

            first_updates = await settle_flat_bet_takers(session, bet=bet, result_status="refund")
            revision_after = subscription.revision
            second_updates = await settle_flat_bet_takers(session, bet=bet, result_status="refund")

            self.assertEqual(len(first_updates), 1)
            self.assertGreater(revision_after, revision_before)
            self.assertEqual(len(second_updates), 0)
            self.assertEqual(subscription.revision, revision_after)

    async def test_stake_correction_rejects_stale_revision_and_audits_before_after(self):
        async with self.Session() as session:
            user = User(telegram_id=320, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="2.00")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))

            with self.assertRaises(HTTPException) as stale:
                await correct_flat_bet_stake(
                    session,
                    user_id=user.telegram_id,
                    bet_id=bet.id,
                    stake_rub=Decimal("7500"),
                    actor_id=900,
                    expected_revision=subscription.revision + 1,
                    note="Исправление суммы по сообщению клиента",
                )
            self.assertEqual(stale.exception.status_code, 409)

            await correct_flat_bet_stake(
                session,
                user_id=user.telegram_id,
                bet_id=bet.id,
                stake_rub=Decimal("7500"),
                actor_id=900,
                expected_revision=subscription.revision,
                note="Исправление суммы по сообщению клиента",
            )
            correction = (
                await session.execute(
                    select(FlatSubscriptionCredit).filter(
                        FlatSubscriptionCredit.event_type == "stake_corrected"
                    )
                )
            ).scalars().one()
            self.assertIn("5000", correction.note)
            self.assertIn("7500", correction.note)
            self.assertIn("Исправление суммы", correction.note)

    async def test_flat_amount_change_recalculates_fraction_and_keeps_completed_closed(self):
        async with self.Session() as session:
            user = User(telegram_id=303, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet()
            session.add_all([user, bet])
            await session.flush()
            forecast_request = ForecastRequest(
                bet_id=bet.id,
                user_id=user.telegram_id,
                status="processing",
            )
            session.add(forecast_request)
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("0.50"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
                actor_id=900,
            )
            await record_user_flat_bet_access(
                session,
                user=user,
                bet=bet,
                stake_rub=Decimal("5000"),
                forecast_request=forecast_request,
            )
            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)

            await configure_flat_subscription(
                session,
                user_id=user.telegram_id,
                flat_subscription_id=subscription.id,
                flat_amount_rub=Decimal("20000"),
                actor_id=900,
            )
            await session.flush()
            row = (await session.execute(select(user_bets))).first()._mapping
            audit_events = list(
                (await session.execute(select(FlatSubscriptionCredit.event_type))).scalars().all()
            )

            self.assertEqual(row["stake_flats"], Decimal("0.250000"))
            self.assertEqual(forecast_request.stake_flats, Decimal("0.250000"))
            self.assertEqual(row["profit_flats"], Decimal("0.250000"))
            self.assertEqual(subscription.profit_flats, Decimal("0.250000"))
            self.assertEqual(subscription.status, FlatSubscriptionState.COMPLETED.value)
            self.assertIn("flat_amount_changed", audit_events)

    async def test_admin_previews_show_financial_before_after_without_mutation(self):
        async with self.Session() as session:
            user = User(telegram_id=321, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="2.00")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            revision = subscription.revision

            flat_preview = await preview_flat_amount_change(
                session,
                user_id=user.telegram_id,
                flat_subscription_id=subscription.id,
                flat_amount_rub=Decimal("20000"),
                expected_revision=revision,
            )
            stake_preview = await preview_flat_bet_stake(
                session,
                user_id=user.telegram_id,
                bet_id=bet.id,
                stake_rub=Decimal("7500"),
                expected_revision=revision,
            )

            self.assertEqual(flat_preview["before"]["profit_flats"], Decimal("0.500000"))
            self.assertEqual(flat_preview["after"]["profit_flats"], Decimal("0.250000"))
            self.assertEqual(stake_preview["before"]["stake_rub"], Decimal("5000.00"))
            self.assertEqual(stake_preview["after"]["stake_rub"], Decimal("7500.00"))
            self.assertEqual(subscription.flat_amount_rub, Decimal("10000.00"))
            self.assertEqual(subscription.revision, revision)

    async def test_payload_contains_actual_stake_and_progress(self):
        async with self.Session() as session:
            user = User(telegram_id=304, purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="2.20")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("15000"))
            response = await flat_subscription_payload(session, subscription)

            self.assertEqual(response["remaining_flats"], Decimal("3.00"))
            self.assertEqual(response["pending_bets"], 1)
            self.assertEqual(response["bets"][0]["stake_rub"], Decimal("15000.00"))
            self.assertEqual(response["bets"][0]["stake_flats"], Decimal("1.500000"))

    async def test_legacy_balance_keeps_configured_flat_subscription_pending(self):
        async with self.Session() as session:
            user = User(telegram_id=305, purchased_bets_balance=1, matches_remaining=1)
            session.add(user)
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )

            self.assertEqual(subscription.status, FlatSubscriptionState.PENDING_SETUP.value)
            self.assertIsNone(subscription.activated_at)

            user.purchased_bets_balance = 0
            user.matches_remaining = 0
            await activate_pending_flat_subscription_if_eligible(session, user=user)

            self.assertEqual(subscription.status, FlatSubscriptionState.ACTIVE.value)
            self.assertIsNotNone(subscription.activated_at)

    async def test_fresh_guarantee_state_blocks_activation_when_tracked_user_is_stale(self):
        async with self.Session() as session:
            user = User(
                telegram_id=324,
                purchased_bets_balance=0,
                matches_remaining=0,
                guarantee_active=False,
            )
            session.add(user)
            await session.commit()

            await session.execute(
                update(User)
                .where(User.telegram_id == user.telegram_id)
                .values(guarantee_active=True)
                .execution_options(synchronize_session=False)
            )
            self.assertFalse(user.guarantee_active)

            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )

            self.assertEqual(subscription.status, FlatSubscriptionState.PENDING_SETUP.value)
            self.assertIsNone(subscription.activated_at)

    async def test_unresolved_legacy_bet_keeps_configured_flat_subscription_pending(self):
        async with self.Session() as session:
            user = User(telegram_id=311, purchased_bets_balance=1, matches_remaining=1)
            legacy_bet = self._bet()
            session.add_all([user, legacy_bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )
            await session.execute(
                user_bets.insert().values(
                    user_id=user.telegram_id,
                    bet_id=legacy_bet.id,
                    access_type="paid_match",
                    match_charged=True,
                )
            )
            user.purchased_bets_balance = 0
            user.matches_remaining = 0

            await activate_pending_flat_subscription_if_eligible(session, user=user)
            self.assertEqual(subscription.status, FlatSubscriptionState.PENDING_SETUP.value)

            legacy_bet.status = "win"
            await session.flush()
            await activate_pending_flat_subscription_if_eligible(session, user=user)
            self.assertEqual(subscription.status, FlatSubscriptionState.ACTIVE.value)

    async def test_unresolved_legacy_bet_blocks_initial_flat_activation_after_balance_is_spent(self):
        async with self.Session() as session:
            user = User(telegram_id=323, purchased_bets_balance=0, matches_remaining=0)
            legacy_bet = self._bet()
            session.add_all([user, legacy_bet])
            await session.flush()
            await session.execute(
                user_bets.insert().values(
                    user_id=user.telegram_id,
                    bet_id=legacy_bet.id,
                    access_type="paid_match",
                    match_charged=True,
                )
            )

            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3"),
                flat_amount_rub=Decimal("10000"),
                event_type="subscription_purchase",
            )

            self.assertEqual(subscription.status, FlatSubscriptionState.PENDING_SETUP.value)
            self.assertIsNone(subscription.activated_at)

    async def test_channel_stake_session_replaces_previous_request_and_expires(self):
        async with self.Session() as session:
            user = User(telegram_id=306, purchased_bets_balance=0, matches_remaining=0)
            first_bet = self._bet()
            second_bet = self._bet()
            session.add_all([user, first_bet, second_bet])
            await session.flush()
            first_request = ForecastRequest(bet_id=first_bet.id, user_id=user.telegram_id)
            second_request = ForecastRequest(bet_id=second_bet.id, user_id=user.telegram_id)
            session.add_all([first_request, second_request])
            await session.flush()

            await start_forecast_stake_input(
                session,
                channel="telegram",
                user_id=user.telegram_id,
                forecast_request_id=first_request.id,
            )
            await start_forecast_stake_input(
                session,
                channel="telegram",
                user_id=user.telegram_id,
                forecast_request_id=second_request.id,
            )
            current = await get_forecast_stake_input(
                session,
                channel="telegram",
                user_id=user.telegram_id,
            )
            sessions = list((await session.execute(select(ForecastStakeInputSession))).scalars().all())

            self.assertEqual(len(sessions), 1)
            self.assertEqual(current.forecast_request_id, second_request.id)

            current.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            lookup = await lookup_forecast_stake_input(
                session,
                channel="telegram",
                user_id=user.telegram_id,
            )
            self.assertIsNone(lookup.session)
            self.assertTrue(lookup.expired)

    async def test_feed_take_requires_actual_stake_is_idempotent_and_keeps_odds_snapshot(self):
        async with self.Session() as session:
            user = User(telegram_id=309, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000.00"),
                event_type="manual_credit",
            )
            await session.commit()

            with self.assertRaises(HTTPException) as missing_stake:
                await bets_api.take_bet(
                    bet.id,
                    current_user=user,
                    db=session,
                )
            self.assertEqual(missing_stake.exception.status_code, 422)

            first = await bets_api.take_bet(
                bet.id,
                payload=FlatStakeRequest(stake_rub=Decimal("5000.00")),
                current_user=user,
                db=session,
            )
            second = await bets_api.take_bet(
                bet.id,
                payload=FlatStakeRequest(stake_rub=Decimal("9000.00")),
                current_user=user,
                db=session,
            )

            self.assertEqual(first["status"], "success")
            self.assertEqual(second["status"], "already_taken")
            self.assertEqual(second["stake_rub"], Decimal("5000.00"))
            forecast_request = (await session.execute(select(ForecastRequest))).scalars().one()
            self.assertEqual(forecast_request.stake_rub, Decimal("5000.00"))
            self.assertEqual(forecast_request.stake_input_channel, "web")

            bet.coefficient = Decimal("5.00")
            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            self.assertEqual(subscription.profit_rub, Decimal("3500.00"))
            self.assertEqual(subscription.profit_flats, Decimal("0.350000"))

    async def test_admin_target_credit_notifies_client(self):
        async with self.Session() as session:
            admin = User(telegram_id=901, role="admin")
            user = User(telegram_id=313, role="user", purchased_bets_balance=0, matches_remaining=0)
            session.add_all([admin, user])
            await session.commit()

            response = await admin_api.admin_credit_user_flat_subscription(
                user.telegram_id,
                FlatSubscriptionCreditRequest(
                    target_flats=Decimal("1.25"),
                    flat_amount_rub=Decimal("10000.00"),
                ),
                admin=admin,
                db=session,
            )
            signals = list(
                (
                    await session.execute(
                        select(PersonalSignal).filter(PersonalSignal.user_id == user.telegram_id)
                    )
                ).scalars().all()
            )

            self.assertEqual(response["target_flats"], Decimal("1.25"))
            self.assertEqual(len(signals), 1)
            self.assertIn("+1.25 флета", signals[0].text)

    async def test_resolve_sends_personal_flat_progress_and_idempotent_correction(self):
        async with self.Session() as session:
            admin = User(telegram_id=902, role="admin")
            user = User(telegram_id=322, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([admin, user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            await session.commit()

            await bets_api.resolve_bet(bet.id, BetResolve(status="win"), admin=admin, db=session)
            await bets_api.resolve_bet(bet.id, BetResolve(status="win"), admin=admin, db=session)
            first_signals = list(
                (
                    await session.execute(
                        select(PersonalSignal).filter(PersonalSignal.user_id == user.telegram_id)
                    )
                ).scalars().all()
            )
            self.assertEqual(len(first_signals), 1)
            self.assertEqual(first_signals[0].type, "flat_bet_result")
            self.assertIn("3 500.00 ₽", first_signals[0].text)
            self.assertIn("0.350000", first_signals[0].text)
            self.assertEqual(first_signals[0].data["flat_subscription_status"], "active")

            await bets_api.resolve_bet(bet.id, BetResolve(status="loss"), admin=admin, db=session)
            corrected_signals = list(
                (
                    await session.execute(
                        select(PersonalSignal)
                        .filter(PersonalSignal.user_id == user.telegram_id)
                        .order_by(PersonalSignal.id)
                    )
                ).scalars().all()
            )
            self.assertEqual(len(corrected_signals), 2)
            self.assertTrue(corrected_signals[-1].data["corrected"])
            self.assertEqual(subscription.status, FlatSubscriptionState.ACTIVE.value)

    async def test_admin_stake_correction_is_audited_notified_and_retry_safe(self):
        async with self.Session() as session:
            admin = User(telegram_id=903, role="admin")
            user = User(telegram_id=324, role="user", purchased_bets_balance=0, matches_remaining=0)
            bet = self._bet(coefficient="1.70")
            session.add_all([admin, user, bet])
            await session.flush()
            subscription = await credit_flat_subscription(
                session,
                user=user,
                target_flats=Decimal("3.00"),
                flat_amount_rub=Decimal("10000"),
                event_type="manual_credit",
            )
            await record_user_flat_bet_access(session, user=user, bet=bet, stake_rub=Decimal("5000"))
            await settle_flat_bet_takers(session, bet=bet, result_status="win")
            await session.commit()
            expected_revision = subscription.revision

            response = await admin_api.admin_correct_user_flat_stake(
                user.telegram_id,
                bet.id,
                FlatStakeCorrectionRequest(
                    stake_rub=Decimal("7500"),
                    expected_revision=expected_revision,
                    note="Клиент подтвердил фактическую сумму",
                ),
                admin=admin,
                db=session,
            )
            with self.assertRaises(HTTPException) as retry:
                await admin_api.admin_correct_user_flat_stake(
                    user.telegram_id,
                    bet.id,
                    FlatStakeCorrectionRequest(
                        stake_rub=Decimal("7500"),
                        expected_revision=expected_revision,
                        note="Клиент подтвердил фактическую сумму",
                    ),
                    admin=admin,
                    db=session,
                )

            signals = list(
                (
                    await session.execute(
                        select(PersonalSignal).filter(
                            PersonalSignal.user_id == user.telegram_id,
                            PersonalSignal.type == "flat_subscription_adjustment",
                        )
                    )
                ).scalars().all()
            )
            audit = (
                await session.execute(
                    select(AdminAuditLog).filter(AdminAuditLog.action == "flat_stake_corrected")
                )
            ).scalars().one()

            self.assertEqual(response["bets"][0]["stake_rub"], Decimal("7500.00"))
            self.assertEqual(retry.exception.status_code, 409)
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0].data["revision"], response["revision"])
            self.assertEqual(audit.details["reason"], "Клиент подтвердил фактическую сумму")
            self.assertEqual(
                Decimal(str(audit.details["preview"]["before"]["stake_rub"])),
                Decimal("5000.00"),
            )
            self.assertEqual(
                Decimal(str(audit.details["preview"]["after"]["stake_rub"])),
                Decimal("7500.00"),
            )


if __name__ == "__main__":
    unittest.main()
