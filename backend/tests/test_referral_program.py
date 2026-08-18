import unittest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import admin as admin_api
from src.api import payments
from src.models.database import Base
from src.models.models import (
    Bet,
    FlatSubscription,
    MarketingWidgetConfig,
    MarketingRewardEvent,
    MatchBalanceLog,
    PaymentAttempt,
    ReferralRewardEvent,
    SubscriptionPlan,
    SystemSetting,
    User,
    user_bets,
)
from src.services import referrals, system_settings
from src.services import marketing_widgets


class ReferralProgramTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_referral_settings_are_admin_editable_and_validated(self):
        async with self.Session() as session:
            with (
                patch.object(system_settings, "cache_get_json", new=AsyncMock(return_value=None)),
                patch.object(system_settings, "cache_set_json", new=AsyncMock()),
            ):
                payload = await system_settings.get_admin_system_settings(session)

            settings_by_key = {item["key"]: item for item in payload["settings"]}
            self.assertEqual(settings_by_key["REFERRAL_PROGRAM_ENABLED"]["value"], "false")
            self.assertEqual(settings_by_key["REFERRAL_DISCOUNT_STEP_PERCENT"]["value"], "5")
            self.assertEqual(settings_by_key["REFERRAL_DISCOUNT_MAX_PERCENT"]["value"], "100")
            self.assertFalse(settings_by_key["REFERRAL_PROGRAM_ENABLED"]["is_secret"])

            with patch.object(system_settings, "cache_delete", new=AsyncMock()):
                await system_settings.update_admin_system_settings(
                    session,
                    [
                        {"key": "REFERRAL_DISCOUNT_STEP_PERCENT", "value": "7"},
                        {"key": "REFERRAL_DISCOUNT_MAX_PERCENT", "value": "21"},
                    ],
                )

                with self.assertRaises(ValueError):
                    await system_settings.update_admin_system_settings(
                        session,
                        [{"key": "REFERRAL_MATCH_REWARD_COUNT", "value": "10000.01"}],
                    )

    async def test_custom_referral_discount_settings_drive_referral_stats(self):
        async with self.Session() as session:
            referrer = User(telegram_id=100, username="referrer")
            invited_a = User(telegram_id=201, referred_by_user_id=referrer.telegram_id)
            invited_b = User(telegram_id=202, referred_by_user_id=referrer.telegram_id)
            session.add_all([
                referrer,
                invited_a,
                invited_b,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_DISCOUNT_STEP_PERCENT", value="7"),
                SystemSetting(key="REFERRAL_DISCOUNT_MAX_PERCENT", value="10"),
            ])
            await session.flush()
            session.add_all([
                ReferralRewardEvent(
                    referrer_user_id=referrer.telegram_id,
                    referred_user_id=invited_a.telegram_id,
                    source_type="single_bet",
                    status="approved",
                ),
                ReferralRewardEvent(
                    referrer_user_id=referrer.telegram_id,
                    referred_user_id=invited_b.telegram_id,
                    source_type="single_bet",
                    status="approved",
                ),
            ])
            await session.commit()

            stats = await referrals.get_referral_stats(session, referrer.telegram_id)

        self.assertEqual(stats["invited_count"], 2)
        self.assertEqual(stats["purchased_invited_count"], 2)
        self.assertEqual(stats["discount_step_percent"], 7)
        self.assertEqual(stats["referral_discount_percent"], 10)
        self.assertTrue(stats["program_enabled"])

    async def test_disabled_referral_program_returns_zero_discount_and_no_match_reward(self):
        async with self.Session() as session:
            referrer = User(telegram_id=100, username="referrer", purchased_bets_balance=0, matches_remaining=0)
            invited = User(telegram_id=201, referred_by_user_id=referrer.telegram_id)
            plan = SubscriptionPlan(
                id=1,
                name="Test plan",
                duration_days=30,
                match_count=3,
                price=Decimal("100.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=invited.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("100.00"),
                currency="RUB",
                status="pending",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="legacy_match",
                match_count_snapshot=plan.match_count,
                metadata_json={},
            )
            session.add_all([
                referrer,
                invited,
                plan,
                attempt,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="false"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2"),
            ])
            await session.commit()

            result = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="yookassa",
                provider_payment_id="payment-1",
                amount=Decimal("100.00"),
                currency="RUB",
            )
            await session.commit()

            stats = await referrals.get_referral_stats(session, referrer.telegram_id)
            refreshed_referrer = await session.get(User, referrer.telegram_id)
            reward_count = (await session.execute(select(func.count(ReferralRewardEvent.id)))).scalar_one()

        self.assertEqual(result["status"], "success")
        self.assertEqual(stats["referral_discount_percent"], 0)
        self.assertEqual(refreshed_referrer.matches_remaining, 0)
        self.assertEqual(reward_count, 0)

    async def test_referral_match_reward_is_awarded_to_referrer_once(self):
        async with self.Session() as session:
            referrer = User(telegram_id=100, username="referrer", purchased_bets_balance=1, matches_remaining=1)
            invited = User(telegram_id=201, referred_by_user_id=referrer.telegram_id)
            plan = SubscriptionPlan(
                id=1,
                name="Test plan",
                duration_days=30,
                match_count=3,
                price=Decimal("100.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=invited.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("100.00"),
                currency="RUB",
                status="pending",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="legacy_match",
                match_count_snapshot=plan.match_count,
                metadata_json={},
            )
            session.add_all([
                referrer,
                invited,
                plan,
                attempt,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2"),
            ])
            await session.commit()

            first = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="yookassa",
                provider_payment_id="payment-1",
                amount=Decimal("100.00"),
                currency="RUB",
            )
            await session.commit()
            second = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="yookassa",
                provider_payment_id="payment-1",
                amount=Decimal("100.00"),
                currency="RUB",
            )
            await session.commit()

            refreshed_referrer = await session.get(User, referrer.telegram_id)
            event = (await session.execute(select(ReferralRewardEvent))).scalars().one()
            balance_log = (
                await session.execute(
                    select(MatchBalanceLog).filter(MatchBalanceLog.event_type == "referral_match_reward")
                )
            ).scalars().one()

            summary = await admin_api.admin_referrals_summary(admin=referrer, db=session)

        self.assertEqual(first["status"], "success")
        self.assertEqual(second["status"], "already_processed")
        self.assertEqual(refreshed_referrer.matches_remaining, 3)
        self.assertEqual(event.referrer_user_id, referrer.telegram_id)
        self.assertEqual(event.referred_user_id, invited.telegram_id)
        self.assertEqual(event.status, "approved")
        self.assertEqual(event.matches_awarded, 2)
        self.assertEqual(balance_log.user_id, referrer.telegram_id)
        self.assertEqual(balance_log.delta_matches, 2)
        self.assertEqual(summary["matches_awarded_total"], 2)
        self.assertEqual(summary["recent_events"][0]["matches_awarded"], 2)

    async def test_new_flat_purchase_awards_referrer_target_flats(self):
        async with self.Session() as session:
            referrer = User(telegram_id=110, username="flat_referrer", purchased_bets_balance=0, matches_remaining=0)
            invited = User(telegram_id=211, referred_by_user_id=referrer.telegram_id)
            plan = SubscriptionPlan(
                id=11,
                name="Flat target",
                duration_days=0,
                match_count=0,
                entitlement_type="flat",
                target_flats=Decimal("3.00"),
                price=Decimal("100.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=invited.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("100.00"),
                currency="RUB",
                status="pending",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="flat",
                target_flats_snapshot=Decimal("3.00"),
                match_count_snapshot=0,
                metadata_json={},
            )
            session.add_all([
                referrer,
                invited,
                plan,
                attempt,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2.25"),
            ])
            await session.commit()

            # A later tariff edit must not alter either the purchased
            # entitlement or the associated referral reward.
            plan.entitlement_type = "legacy_match"
            plan.target_flats = None
            plan.match_count = 99
            await session.commit()

            result = await payments._process_payment_attempt(
                session,
                attempt_id=attempt.id,
                provider="yookassa",
                provider_payment_id="flat-payment-1",
                amount=Decimal("100.00"),
                currency="RUB",
            )
            await session.commit()

            event = (await session.execute(select(ReferralRewardEvent))).scalars().one()
            subscriptions = list((await session.execute(select(FlatSubscription))).scalars().all())
            referrer_subscription = next(item for item in subscriptions if item.user_id == referrer.telegram_id)
            summary = await admin_api.admin_referrals_summary(admin=referrer, db=session)

        self.assertEqual(result["status"], "success")
        self.assertEqual(event.matches_awarded, 0)
        self.assertEqual(event.target_flats_awarded, Decimal("2.25"))
        self.assertEqual(referrer_subscription.target_flats, Decimal("2.25"))
        self.assertEqual(referrer_subscription.status, "pending_setup")
        self.assertEqual(summary["target_flats_awarded_total"], Decimal("2.25"))

    async def test_same_device_referral_is_held_and_does_not_increase_discount(self):
        async with self.Session() as session:
            referrer = User(telegram_id=100, username="referrer", purchased_bets_balance=0, matches_remaining=0)
            invited = User(telegram_id=201, referred_by_user_id=referrer.telegram_id)
            plan = SubscriptionPlan(
                id=1,
                name="Test plan",
                duration_days=30,
                match_count=3,
                price=Decimal("100.00"),
                currency="RUB",
                is_active=True,
            )
            attempt = PaymentAttempt(
                user_id=invited.telegram_id,
                plan_id=plan.id,
                provider="yookassa",
                amount=Decimal("100.00"),
                currency="RUB",
                status="pending",
                plan_name_snapshot=plan.name,
                entitlement_type_snapshot="legacy_match",
                match_count_snapshot=plan.match_count,
                metadata_json={},
            )
            session.add_all([
                referrer,
                invited,
                plan,
                attempt,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2"),
            ])
            await session.commit()

            with patch("src.services.marketing_risk.users_share_identity_device", new=AsyncMock(return_value=True)):
                result = await payments._process_payment_attempt(
                    session,
                    attempt_id=attempt.id,
                    provider="yookassa",
                    provider_payment_id="payment-held",
                    amount=Decimal("100.00"),
                    currency="RUB",
                )
            await session.commit()

            stats = await referrals.get_referral_stats(session, referrer.telegram_id)
            refreshed_referrer = await session.get(User, referrer.telegram_id)
            event = (await session.execute(select(ReferralRewardEvent))).scalars().one()
            balance_logs = (
                await session.execute(
                    select(MatchBalanceLog).filter(MatchBalanceLog.event_type == "referral_match_reward")
                )
            ).scalars().all()

        self.assertEqual(result["status"], "success")
        self.assertEqual(event.status, "held")
        self.assertIn("same_identity_device", event.risk_reasons)
        self.assertEqual(event.matches_awarded, 0)
        self.assertEqual(stats["purchased_invited_count"], 0)
        self.assertEqual(stats["referral_discount_percent"], 0)
        self.assertEqual(refreshed_referrer.matches_remaining, 0)
        self.assertEqual(balance_logs, [])

    async def test_admin_approve_held_referral_awards_flat_target_once(self):
        async with self.Session() as session:
            admin = User(telegram_id=1, username="admin", role="admin")
            referrer = User(telegram_id=100, username="referrer", purchased_bets_balance=1, matches_remaining=1)
            invited = User(telegram_id=201, referred_by_user_id=referrer.telegram_id)
            event = ReferralRewardEvent(
                referrer_user_id=referrer.telegram_id,
                referred_user_id=invited.telegram_id,
                source_type="subscription",
                status="held",
                risk_score=50,
                risk_reasons=["same_identity_device"],
            )
            session.add_all([
                admin,
                referrer,
                invited,
                event,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_DISCOUNT_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_DISCOUNT_STEP_PERCENT", value="7"),
                SystemSetting(key="REFERRAL_DISCOUNT_MAX_PERCENT", value="30"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2"),
            ])
            await session.commit()

            first = await admin_api.admin_approve_marketing_risk_event(f"referral:{event.id}", admin=admin, db=session)
            second = await admin_api.admin_approve_marketing_risk_event(f"referral:{event.id}", admin=admin, db=session)
            await session.commit()

            refreshed_referrer = await session.get(User, referrer.telegram_id)
            refreshed_event = await session.get(ReferralRewardEvent, event.id)
            flat_subscriptions = list(
                (await session.execute(select(FlatSubscription))).scalars().all()
            )
            balance_logs = (
                await session.execute(
                    select(MatchBalanceLog).filter(MatchBalanceLog.event_type == "referral_match_reward")
                )
            ).scalars().all()

        self.assertEqual(first["status"], "approved")
        self.assertEqual(second["status"], "approved")
        self.assertEqual(refreshed_event.status, "approved")
        self.assertEqual(refreshed_event.matches_awarded, 0)
        self.assertEqual(refreshed_event.target_flats_awarded, Decimal("2.00"))
        self.assertEqual(refreshed_event.discount_percent_snapshot, 7)
        self.assertEqual(refreshed_referrer.matches_remaining, 1)
        self.assertEqual(len(balance_logs), 0)
        self.assertEqual(len(flat_subscriptions), 1)
        self.assertEqual(flat_subscriptions[0].target_flats, Decimal("2.00"))

    async def test_referral_approval_refreshes_stale_event_before_awarding(self):
        async with self.Session() as setup_session:
            referrer = User(
                telegram_id=110,
                username="stale-referrer",
                purchased_bets_balance=1,
                matches_remaining=1,
            )
            invited = User(telegram_id=211, referred_by_user_id=referrer.telegram_id)
            event = ReferralRewardEvent(
                referrer_user_id=referrer.telegram_id,
                referred_user_id=invited.telegram_id,
                source_type="subscription",
                status="held",
                risk_score=50,
                risk_reasons=["same_identity_device"],
            )
            setup_session.add_all([
                referrer,
                invited,
                event,
                SystemSetting(key="REFERRAL_PROGRAM_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_ENABLED", value="true"),
                SystemSetting(key="REFERRAL_MATCH_REWARD_COUNT", value="2"),
            ])
            await setup_session.commit()
            event_id = event.id

        async with self.Session() as stale_session:
            stale_event = await stale_session.get(ReferralRewardEvent, event_id)

            async with self.Session() as winning_session:
                winning_event = await winning_session.get(ReferralRewardEvent, event_id)
                winning_referrer = await winning_session.get(User, referrer.telegram_id)
                winning_event.status = "approved"
                winning_event.matches_awarded = 2
                winning_referrer.purchased_bets_balance = 3
                winning_referrer.matches_remaining = 3
                winning_session.add(MatchBalanceLog(
                    user_id=referrer.telegram_id,
                    delta_matches=2,
                    event_type="referral_match_reward",
                ))
                await winning_session.commit()

            await referrals.approve_referral_reward_event(
                stale_session,
                event=stale_event,
                reviewer_user_id=1,
            )
            await stale_session.commit()

        async with self.Session() as verify_session:
            refreshed_referrer = await verify_session.get(User, referrer.telegram_id)
            refreshed_event = await verify_session.get(ReferralRewardEvent, event_id)
            balance_log_count = int(
                (
                    await verify_session.execute(
                        select(func.count(MatchBalanceLog.id)).filter(
                            MatchBalanceLog.user_id == referrer.telegram_id,
                            MatchBalanceLog.event_type == "referral_match_reward",
                        )
                    )
                ).scalar()
                or 0
            )

        self.assertEqual(refreshed_event.matches_awarded, 2)
        self.assertEqual(refreshed_referrer.matches_remaining, 3)
        self.assertEqual(balance_log_count, 1)

    async def test_marketing_widget_config_disables_widget_and_limits_rewards(self):
        async with self.Session() as session:
            user = User(telegram_id=300, username="promo-user")
            disabled = MarketingWidgetConfig(
                key="daily_spin",
                is_enabled=False,
                cooldown_hours=24,
                per_user_limit=0,
                global_daily_limit=0,
                reward_type="discount",
                reward_value=25,
                promo_valid_hours=24,
                settings_json={},
            )
            limited = MarketingWidgetConfig(
                key="swipe",
                is_enabled=True,
                cooldown_hours=0,
                per_user_limit=1,
                global_daily_limit=0,
                reward_type="discount",
                reward_value=50,
                promo_valid_hours=24,
                settings_json={},
            )
            session.add_all([user, disabled, limited])
            await session.commit()

            self.assertFalse(await marketing_widgets.is_widget_available_for_user(session, "daily_spin", user))
            await marketing_widgets.record_marketing_reward_event(
                session,
                user=user,
                widget_key="swipe",
                reward_type="discount",
                reward_value=50,
                promo_code_id=None,
            )
            with self.assertRaises(marketing_widgets.MarketingWidgetLimitError):
                await marketing_widgets.ensure_widget_reward_allowed(session, "swipe", user)

            events_count = (await session.execute(select(func.count(MarketingRewardEvent.id)))).scalar_one()

        self.assertEqual(events_count, 1)


if __name__ == "__main__":
    unittest.main()
