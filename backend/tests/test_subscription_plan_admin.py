import inspect
import unittest
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import subscriptions
from src.api.deps import get_current_admin
from src.models.database import Base
from src.models.models import ABTestConfig, AdminAuditLog, PaymentAttempt, Subscription, SubscriptionPlan, User
from src.schemas.schemas import SubscriptionResponse


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

    async def test_delete_plan_removes_abonement_and_preserves_purchase_history(self):
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
            self.assertEqual(await session.get(SubscriptionPlan, plan.id), None)

            saved_subscription = await session.get(Subscription, subscription.id)
            saved_attempt = await session.get(PaymentAttempt, attempt.id)
            self.assertIsNotNone(saved_subscription)
            self.assertIsNotNone(saved_attempt)
            self.assertIsNone(saved_subscription.plan_id)
            self.assertIsNone(saved_attempt.plan_id)
            self.assertIsNone(SubscriptionResponse.model_validate(saved_subscription).plan_id)

            ab_count = (
                await session.execute(
                    select(func.count()).select_from(ABTestConfig).filter(ABTestConfig.plan_id == plan.id)
                )
            ).scalar_one()
            self.assertEqual(ab_count, 0)

            audit = (await session.execute(select(AdminAuditLog))).scalars().first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.action, "plan_deleted")


if __name__ == "__main__":
    unittest.main()
