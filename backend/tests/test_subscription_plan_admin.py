import inspect
import unittest
from decimal import Decimal
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import auth, subscriptions
from src.api.deps import get_current_admin
from src.models.database import Base
from src.models.models import (
    ABTestConfig,
    AdminAuditLog,
    DeliveryOutbox,
    FlatSubscription,
    PaymentAttempt,
    PersonalSignal,
    Subscription,
    SubscriptionPlan,
    User,
    subscription_plan_checkout_allowlist,
)
from src.schemas.schemas import (
    SubscriptionManualAssign,
    SubscriptionPlanAllowlistUpdate,
    SubscriptionPlanCreate,
    SubscriptionResponse,
)
from src.services.delivery_outbox import (
    CHANNEL_TELEGRAM_MESSAGE,
    CHANNEL_VK_MESSAGE,
    CHANNEL_WEB_PUSH_SIGNAL,
)


class SubscriptionPlanAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _staff_user(self, role: str = "moderator") -> User:
        return User(
            telegram_id=900,
            username=f"{role}_900",
            role=role,
            purchased_bets_balance=0,
            matches_remaining=0,
        )

    def _plan(self, *, name: str, is_active: bool) -> SubscriptionPlan:
        return SubscriptionPlan(
            name=name,
            duration_days=0,
            match_count=5,
            price=Decimal("990.00"),
            price_stars=0,
            currency="RUB",
            is_active=is_active,
        )

    def test_plan_management_endpoints_use_staff_dependency(self):
        for endpoint_name in ("create_plan", "update_plan", "delete_plan"):
            admin_default = inspect.signature(getattr(subscriptions, endpoint_name)).parameters["admin"].default
            self.assertIs(admin_default.dependency, get_current_admin)

    async def test_moderator_can_view_inactive_abonements_for_management(self):
        async with self.Session() as session:
            moderator = self._staff_user("moderator")
            active_plan = self._plan(name="Активный абонемент", is_active=True)
            hidden_plan = self._plan(name="Скрытый абонемент", is_active=False)
            session.add_all([moderator, active_plan, hidden_plan])
            await session.commit()

            plans = await subscriptions.list_plans(include_inactive=True, db=session, current_user=moderator)

            plan_ids = {plan.id for plan in plans}
            self.assertIn(active_plan.id, plan_ids)
            self.assertIn(hidden_plan.id, plan_ids)

    async def test_delete_plan_archives_abonement_and_preserves_purchase_history(self):
        async with self.Session() as session:
            moderator = self._staff_user("moderator")
            client = User(
                telegram_id=101,
                username="client_101",
                role="user",
                purchased_bets_balance=0,
                matches_remaining=0,
            )
            plan = self._plan(name="Удаляемый абонемент", is_active=False)
            session.add_all([moderator, client, plan])
            await session.flush()
            subscription = Subscription(
                user_id=client.telegram_id,
                plan_id=plan.id,
                status="active",
                payment_provider="manual",
                payment_id="manual_101",
            )
            attempt = PaymentAttempt(
                user_id=client.telegram_id,
                plan_id=plan.id,
                provider="debug",
                amount=Decimal("990.00"),
                currency="RUB",
            )
            ab_config = ABTestConfig(plan_id=plan.id, price_group_a=99, price_group_b=109, is_active=True)
            session.add_all([subscription, attempt, ab_config])
            await session.commit()

            response = await subscriptions.delete_plan(plan.id, admin=moderator, db=session)

            self.assertEqual(response["status"], "success")
            saved_plan = await session.get(SubscriptionPlan, plan.id)
            self.assertIsNotNone(saved_plan)
            self.assertFalse(saved_plan.is_active)

            saved_subscription = await session.get(Subscription, subscription.id)
            saved_attempt = await session.get(PaymentAttempt, attempt.id)
            self.assertIsNotNone(saved_subscription)
            self.assertIsNotNone(saved_attempt)
            self.assertEqual(saved_subscription.plan_id, plan.id)
            self.assertEqual(saved_attempt.plan_id, plan.id)
            self.assertEqual(SubscriptionResponse.model_validate(saved_subscription).plan_id, plan.id)

            ab_count = (
                await session.execute(
                    select(func.count()).select_from(ABTestConfig).filter(ABTestConfig.plan_id == plan.id)
                )
            ).scalar_one()
            self.assertEqual(ab_count, 1)

            audit = (await session.execute(select(AdminAuditLog))).scalars().first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.action, "plan_archived")

    async def test_manual_assignment_notifies_client_and_admin_group(self):
        async with self.Session() as session:
            admin = self._staff_user("admin")
            client = User(
                telegram_id=101,
                username="client_101",
                first_name="Ivan",
                last_name="Petrov",
                role="user",
                vk_user_id="123456",
                vk_messages_allowed=True,
                web_push_subscription={"endpoint": "https://push.example/sub", "keys": {"p256dh": "k", "auth": "a"}},
                purchased_bets_balance=2,
                matches_remaining=2,
            )
            plan = self._plan(name="VIP 5", is_active=True)
            session.add_all([admin, client, plan])
            await session.commit()

            websocket_events = []

            async def fake_send_to_user(user_id, payload):
                websocket_events.append((user_id, payload["type"], payload["text"]))

            with (
                patch.object(subscriptions.settings, "TELEGRAM_ADMIN_GROUP_CHAT_ID", -100555),
                patch.object(subscriptions.subscription_notifications.signal_stream_hub, "send_to_user", fake_send_to_user),
            ):
                response = await subscriptions.manually_assign_subscription(
                    SubscriptionManualAssign(user_id=client.telegram_id, plan_id=plan.id),
                    admin=admin,
                    db=session,
                )

            self.assertEqual(response.user_id, client.telegram_id)
            refreshed_client = await session.get(User, client.telegram_id)
            self.assertEqual(refreshed_client.matches_remaining, 7)

            signals = (await session.execute(select(PersonalSignal))).scalars().all()
            self.assertEqual(len(signals), 1)
            self.assertEqual(signals[0].user_id, client.telegram_id)
            self.assertEqual(signals[0].type, "subscription_credit")
            self.assertIn("+5", signals[0].text)
            self.assertEqual(signals[0].data["matches_added"], 5)
            self.assertEqual(signals[0].data["balance_after"], 7)
            self.assertEqual(websocket_events, [(client.telegram_id, "subscription_credit", signals[0].text)])

            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            client_telegram = [
                item for item in outbox_items
                if item.channel == CHANNEL_TELEGRAM_MESSAGE
                and item.payload["payload"]["chat_id"] == client.telegram_id
            ]
            client_vk = [item for item in outbox_items if item.channel == CHANNEL_VK_MESSAGE]
            client_web_push = [item for item in outbox_items if item.channel == CHANNEL_WEB_PUSH_SIGNAL]
            admin_group = [
                item for item in outbox_items
                if item.channel == CHANNEL_TELEGRAM_MESSAGE
                and item.payload["payload"]["chat_id"] == -100555
            ]

            self.assertEqual(len(client_telegram), 1)
            self.assertIn("+5", client_telegram[0].payload["payload"]["text"])
            self.assertIn("Остаток абонемента: 7", client_telegram[0].payload["payload"]["text"])
            self.assertEqual(len(client_vk), 1)
            self.assertIn("+5", client_vk[0].payload["message"])
            self.assertEqual(len(client_web_push), 1)
            self.assertEqual(client_web_push[0].payload["signal_payload"]["type"], "subscription_credit")
            self.assertEqual(len(admin_group), 1)
            self.assertIn("Ivan Petrov", admin_group[0].payload["payload"]["text"])
            self.assertIn("+5", admin_group[0].payload["payload"]["text"])
            self.assertIn("абонемент клиента: 7", admin_group[0].payload["payload"]["text"].lower())

    async def test_new_flat_plan_is_sold_while_legacy_plan_is_hidden(self):
        async with self.Session() as session:
            moderator = self._staff_user("moderator")
            client = User(telegram_id=102, role="user", purchased_bets_balance=0, matches_remaining=0)
            legacy = self._plan(name="Legacy", is_active=True)
            session.add_all([moderator, client, legacy])
            await session.commit()

            flat_plan = await subscriptions.create_plan(
                SubscriptionPlanCreate(
                    name="Цель +3",
                    match_count=1,
                    entitlement_type="flat",
                    target_flats=Decimal("3.00"),
                    price=Decimal("1490.00"),
                    price_stars=0,
                ),
                admin=moderator,
                db=session,
            )
            visible = await subscriptions.list_plans(
                include_inactive=False,
                db=session,
                current_user=client,
            )

            self.assertEqual([plan.id for plan in visible], [flat_plan.id])
            self.assertEqual(flat_plan.target_flats, Decimal("3.00"))

    async def test_hidden_flat_plan_is_visible_only_to_allowlisted_users_and_staff(self):
        async with self.Session() as session:
            moderator = self._staff_user("moderator")
            allowed = User(telegram_id=104, role="user", purchased_bets_balance=0, matches_remaining=0)
            denied = User(telegram_id=105, role="user", purchased_bets_balance=0, matches_remaining=0)
            session.add_all([moderator, allowed, denied])
            await session.commit()

            hidden_plan = await subscriptions.create_plan(
                SubscriptionPlanCreate(
                    name="Скрытая проверка +1",
                    match_count=1,
                    entitlement_type="flat",
                    target_flats=Decimal("1.00"),
                    price=Decimal("100.00"),
                    is_hidden=True,
                    allowed_user_ids=[allowed.telegram_id],
                ),
                admin=moderator,
                db=session,
            )

            anonymous_plans = await subscriptions.list_plans(
                include_inactive=False,
                db=session,
                current_user=None,
            )
            denied_plans = await subscriptions.list_plans(
                include_inactive=False,
                db=session,
                current_user=denied,
            )
            allowed_plans = await subscriptions.list_plans(
                include_inactive=False,
                db=session,
                current_user=allowed,
            )
            staff_plans = await subscriptions.list_plans(
                include_inactive=True,
                db=session,
                current_user=moderator,
            )
            allowlist = await subscriptions.get_plan_allowlist(
                hidden_plan.id,
                admin=moderator,
                db=session,
            )

            self.assertNotIn(hidden_plan.id, {plan.id for plan in anonymous_plans})
            self.assertNotIn(hidden_plan.id, {plan.id for plan in denied_plans})
            self.assertIn(hidden_plan.id, {plan.id for plan in allowed_plans})
            self.assertIn(hidden_plan.id, {plan.id for plan in staff_plans})
            self.assertEqual(allowlist.allowed_user_ids, [allowed.telegram_id])

            updated = await subscriptions.update_plan_allowlist(
                hidden_plan.id,
                SubscriptionPlanAllowlistUpdate(allowed_user_ids=[denied.telegram_id]),
                admin=moderator,
                db=session,
            )
            self.assertEqual(updated.allowed_user_ids, [denied.telegram_id])

    async def test_hidden_plan_allowlist_follows_identity_merge(self):
        async with self.Session() as session:
            source = User(telegram_id=-106, role="user", purchased_bets_balance=0, matches_remaining=0)
            target = User(telegram_id=106, role="user", purchased_bets_balance=0, matches_remaining=0)
            plan = SubscriptionPlan(
                name="Скрытая проверка merge",
                duration_days=0,
                match_count=1,
                entitlement_type="flat",
                target_flats=Decimal("1.00"),
                price=Decimal("100.00"),
                currency="RUB",
                is_active=True,
                is_hidden=True,
            )
            plan.allowed_checkout_users = [source]
            session.add_all([source, target, plan])
            await session.commit()

            await auth._merge_subscription_plan_checkout_allowlist(
                session,
                source.telegram_id,
                target.telegram_id,
            )
            await session.commit()

            rows = list(
                (
                    await session.execute(
                        select(subscription_plan_checkout_allowlist.c.user_id).where(
                            subscription_plan_checkout_allowlist.c.plan_id == plan.id
                        )
                    )
                ).scalars().all()
            )
            self.assertEqual(rows, [target.telegram_id])

    async def test_first_manual_flat_credit_requires_amount_then_extends_same_target(self):
        async with self.Session() as session:
            admin = self._staff_user("admin")
            client = User(telegram_id=103, role="user", purchased_bets_balance=0, matches_remaining=0)
            plan = SubscriptionPlan(
                name="Цель +3",
                duration_days=0,
                match_count=1,
                entitlement_type="flat",
                target_flats=Decimal("3.00"),
                price=Decimal("1490.00"),
                price_stars=0,
                currency="RUB",
                is_active=True,
            )
            session.add_all([admin, client, plan])
            await session.commit()

            with self.assertRaises(HTTPException) as error:
                await subscriptions.manually_assign_subscription(
                    SubscriptionManualAssign(user_id=client.telegram_id, plan_id=plan.id),
                    admin=admin,
                    db=session,
                )
            self.assertEqual(error.exception.status_code, 422)

            await subscriptions.manually_assign_subscription(
                SubscriptionManualAssign(
                    user_id=client.telegram_id,
                    plan_id=plan.id,
                    flat_amount_rub=Decimal("10000.00"),
                ),
                admin=admin,
                db=session,
            )
            await subscriptions.manually_assign_subscription(
                SubscriptionManualAssign(user_id=client.telegram_id, plan_id=plan.id),
                admin=admin,
                db=session,
            )

            flat_subscription = (await session.execute(select(FlatSubscription))).scalars().one()
            signals = list(
                (
                    await session.execute(
                        select(PersonalSignal).filter(PersonalSignal.user_id == client.telegram_id)
                    )
                ).scalars().all()
            )
            self.assertEqual(flat_subscription.status, "active")
            self.assertEqual(flat_subscription.flat_amount_rub, Decimal("10000.00"))
            self.assertEqual(flat_subscription.target_flats, Decimal("6.00"))
            self.assertEqual(len(signals), 2)
            latest_signal = next(signal for signal in signals if signal.data["target_flats"] == "6.00")
            self.assertEqual(latest_signal.data["entitlement_type"], "flat")
            self.assertEqual(latest_signal.data["target_flats_added"], "3.00")


if __name__ == "__main__":
    unittest.main()
