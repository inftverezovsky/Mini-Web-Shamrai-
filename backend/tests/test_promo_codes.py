import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import admin as admin_api
from src.api import payments
from src.models.database import Base
from src.models.models import FlatSubscription, MatchBalanceLog, PromoCode, PromoCodeRedemption, User


class PromoCodeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _admin_user(self) -> User:
        return User(
            telegram_id=900,
            username="admin_900",
            role="admin",
            purchased_bets_balance=0,
            matches_remaining=0,
        )

    def _client_user(self) -> User:
        return User(
            telegram_id=101,
            username="client_101",
            role="user",
            purchased_bets_balance=1,
            matches_remaining=1,
        )

    async def test_admin_creates_new_credit_promos_in_flats(self):
        async with self.Session() as session:
            admin = self._admin_user()
            session.add(admin)
            await session.commit()

            promo = await admin_api.create_promo(
                admin_api.PromoCreate(
                    code="FLAT3",
                    reward_type="flats",
                    target_flats=3,
                    valid_until=datetime.now(timezone.utc) + timedelta(days=7),
                ),
                admin=admin,
                db=session,
            )

            self.assertEqual(promo.code, "FLAT3")
            self.assertEqual(promo.reward_type, "flats")
            self.assertEqual(promo.discount_percent, 0)
            self.assertEqual(promo.target_flats, 3)

    async def test_user_can_redeem_match_credit_promo_once(self):
        async with self.Session() as session:
            client = self._client_user()
            promo = PromoCode(
                code="MATCH3",
                reward_type="matches",
                discount_percent=0,
                matches_count=3,
                valid_until=datetime.now(timezone.utc) + timedelta(days=7),
                is_active=True,
            )
            session.add_all([client, promo])
            await session.commit()

            response = await payments.redeem_promo_code(
                payments.PromoRedeemRequest(code="match3"),
                current_user=client,
                db=session,
            )

            self.assertEqual(response["code"], "MATCH3")
            self.assertEqual(response["reward_type"], "matches")
            self.assertEqual(response["matches_added"], 3)
            self.assertEqual(response["balance_after"], 4)

            refreshed_user = await session.get(User, client.telegram_id)
            self.assertEqual(refreshed_user.purchased_bets_balance, 4)
            self.assertEqual(refreshed_user.matches_remaining, 4)

            redemption = (await session.execute(select(PromoCodeRedemption))).scalars().one()
            self.assertEqual(redemption.user_id, client.telegram_id)
            self.assertEqual(redemption.matches_added, 3)

            balance_log = (await session.execute(select(MatchBalanceLog))).scalars().one()
            self.assertEqual(balance_log.user_id, client.telegram_id)
            self.assertEqual(balance_log.delta_matches, 3)
            self.assertEqual(balance_log.event_type, "promo_match_credit")

            with self.assertRaises(HTTPException) as error:
                await payments.redeem_promo_code(
                    payments.PromoRedeemRequest(code="MATCH3"),
                    current_user=client,
                    db=session,
                )

            self.assertEqual(error.exception.status_code, 400)
            refreshed_after_retry = await session.get(User, client.telegram_id)
            self.assertEqual(refreshed_after_retry.matches_remaining, 4)

    async def test_user_redeems_new_flat_credit_promo_into_pending_subscription(self):
        async with self.Session() as session:
            client = self._client_user()
            promo = PromoCode(
                code="FLAT150",
                reward_type="flats",
                discount_percent=0,
                matches_count=0,
                target_flats=1.5,
                valid_until=datetime.now(timezone.utc) + timedelta(days=7),
                is_active=True,
            )
            session.add_all([client, promo])
            await session.commit()

            response = await payments.redeem_promo_code(
                payments.PromoRedeemRequest(code="flat150"),
                current_user=client,
                db=session,
            )

            self.assertEqual(response["reward_type"], "flats")
            self.assertEqual(response["target_flats_added"], "1.50")
            subscription = (await session.execute(select(FlatSubscription))).scalars().one()
            self.assertEqual(str(subscription.target_flats), "1.50")
            self.assertEqual(subscription.status, "pending_setup")
            redemption = (await session.execute(select(PromoCodeRedemption))).scalars().one()
            self.assertEqual(str(redemption.target_flats_added), "1.50")

    async def test_discount_promo_validation_keeps_discount_contract(self):
        async with self.Session() as session:
            client = self._client_user()
            promo = PromoCode(
                code="SALE25",
                reward_type="discount",
                discount_percent=25,
                matches_count=0,
                valid_until=datetime.now(timezone.utc) + timedelta(days=7),
                is_active=True,
            )
            session.add_all([client, promo])
            await session.commit()

            response = await payments.validate_promo_code("sale25", current_user=client, db=session)

            self.assertEqual(response["code"], "SALE25")
            self.assertEqual(response["reward_type"], "discount")
            self.assertEqual(response["discount_percent"], 25)
            self.assertEqual(response["matches_count"], 0)


if __name__ == "__main__":
    unittest.main()
