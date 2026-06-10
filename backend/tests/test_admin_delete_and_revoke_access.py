import unittest
import uuid
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api.admin import delete_bet_from_admin
from src.models.database import Base
from src.models.models import AdminAuditLog, Bet, ForecastRequest, MatchBalanceLog, User, user_bets
from src.services.forecast_delivery import FORECAST_STATUS_REMOVED
from src.services.match_access import REVOKE_USER_BET_ACCESS_EVENT, revoke_user_bet_access


class AdminDeleteAndRevokeAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    def _user(self, telegram_id: int, balance: int, role: str = "user") -> User:
        return User(
            telegram_id=telegram_id,
            username=f"user_{telegram_id}",
            role=role,
            purchased_bets_balance=balance,
            matches_remaining=balance,
        )

    def _bet(self, *, status: str = "pending") -> Bet:
        return Bet(
            id=uuid.uuid4(),
            event_name="Team A - Team B",
            coefficient=Decimal("1.90"),
            status=status,
            sport_type="Football",
            outcome="Team A win",
        )

    async def _add_access(
        self,
        session,
        *,
        user: User,
        bet: Bet,
        access_type: str = "paid_match",
        match_charged: bool = True,
        ledger_deltas: list[int] | None = None,
    ) -> None:
        await session.execute(
            user_bets.insert().values(
                user_id=user.telegram_id,
                bet_id=bet.id,
                access_type=access_type,
                match_charged=match_charged,
                taken_at=func.now(),
            )
        )
        for index, delta in enumerate(ledger_deltas or []):
            session.add(MatchBalanceLog(
                user_id=user.telegram_id,
                bet_id=bet.id,
                delta_matches=delta,
                event_type="match_debit" if delta < 0 else f"positive_adjustment_{index}",
            ))
        await session.flush()

    async def _access_count(self, session, *, user: User, bet: Bet) -> int:
        result = await session.execute(
            select(func.count()).select_from(user_bets).filter(
                user_bets.c.user_id == user.telegram_id,
                user_bets.c.bet_id == bet.id,
            )
        )
        return int(result.scalar() or 0)

    async def test_revoking_charged_pending_access_returns_one_match(self):
        async with self.Session() as session:
            user = self._user(101, balance=4)
            bet = self._bet()
            session.add_all([user, bet])
            await session.flush()
            await self._add_access(session, user=user, bet=bet, ledger_deltas=[-1])

            result = await revoke_user_bet_access(session, user=user, bet=bet, actor_id=900)
            await session.flush()

            self.assertTrue(result.had_access)
            self.assertEqual(result.delta_matches, 1)
            self.assertEqual(user.purchased_bets_balance, 5)
            self.assertEqual(await self._access_count(session, user=user, bet=bet), 0)

    async def test_revoking_non_charged_or_declined_request_does_not_change_balance(self):
        async with self.Session() as session:
            user = self._user(102, balance=7)
            bet = self._bet()
            session.add_all([user, bet])
            await session.flush()

            result = await revoke_user_bet_access(session, user=user, bet=bet, actor_id=900)

            self.assertFalse(result.had_access)
            self.assertEqual(result.delta_matches, 0)
            self.assertEqual(user.purchased_bets_balance, 7)
            self.assertEqual(user.matches_remaining, 7)

    async def test_revoking_after_loss_supercompensation_reverses_net_ledger(self):
        async with self.Session() as session:
            user = self._user(103, balance=6)
            bet = self._bet(status="loss")
            session.add_all([user, bet])
            await session.flush()
            await self._add_access(session, user=user, bet=bet, ledger_deltas=[-1, 2])

            result = await revoke_user_bet_access(session, user=user, bet=bet, actor_id=900)
            await session.flush()

            self.assertEqual(result.previous_ledger_delta, 1)
            self.assertEqual(result.delta_matches, -1)
            self.assertEqual(user.purchased_bets_balance, 5)
            self.assertEqual(await self._access_count(session, user=user, bet=bet), 0)

    async def test_deleting_forecast_revokes_all_takers_and_marks_requests_removed(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            charged_user = self._user(201, balance=2)
            free_user = self._user(202, balance=3)
            bet = self._bet()
            session.add_all([admin, charged_user, free_user, bet])
            await session.flush()
            await self._add_access(session, user=charged_user, bet=bet, ledger_deltas=[-1])
            await self._add_access(
                session,
                user=free_user,
                bet=bet,
                access_type="guarantee_replacement",
                match_charged=False,
                ledger_deltas=[0],
            )
            request_one = ForecastRequest(
                bet_id=bet.id,
                user_id=charged_user.telegram_id,
                status="sent",
            )
            request_two = ForecastRequest(
                bet_id=bet.id,
                user_id=free_user.telegram_id,
                status="manual_sent",
            )
            session.add_all([request_one, request_two])
            await session.commit()

            response = await delete_bet_from_admin(bet.id, admin=admin, db=session)

            self.assertEqual(response["status"], "success")
            self.assertEqual(response["revoked_count"], 2)
            self.assertEqual(bet.status, "deleted")
            self.assertEqual(charged_user.purchased_bets_balance, 3)
            self.assertEqual(free_user.purchased_bets_balance, 3)
            access_total = await session.execute(select(func.count()).select_from(user_bets))
            self.assertEqual(int(access_total.scalar() or 0), 0)
            requests = (await session.execute(select(ForecastRequest))).scalars().all()
            self.assertEqual({request.status for request in requests}, {FORECAST_STATUS_REMOVED})
            balances_by_user_id = {request.user_id: request.balance_after for request in requests}
            self.assertEqual(balances_by_user_id[charged_user.telegram_id], 3)
            self.assertEqual(balances_by_user_id[free_user.telegram_id], 3)
            revoke_logs = (
                await session.execute(
                    select(MatchBalanceLog).filter(MatchBalanceLog.event_type == REVOKE_USER_BET_ACCESS_EVENT)
                )
            ).scalars().all()
            self.assertEqual(len(revoke_logs), 2)
            audit = (await session.execute(select(AdminAuditLog))).scalars().first()
            self.assertIsNotNone(audit)
            self.assertEqual(audit.action, "bet_deleted")


if __name__ == "__main__":
    unittest.main()
