import inspect
import unittest
import uuid
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api.admin import delete_bet_from_admin, get_pending_bets
from src.api.admin_broadcast import mark_forecast_request_manual, stop_forecast_broadcast_from_admin
from src.api.deps import get_current_admin
from src.models.database import Base
from src.models.models import AdminAuditLog, Bet, DeliveryOutbox, ForecastRequest, MatchBalanceLog, User, user_bets
from src.services import forecast_delivery
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

    def test_delete_bet_endpoint_uses_staff_dependency(self):
        admin_default = inspect.signature(delete_bet_from_admin).parameters["admin"].default

        self.assertIs(admin_default.dependency, get_current_admin)

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
            admin = self._user(900, balance=0, role="moderator")
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

    async def test_paid_set_can_be_stopped_from_broadcast_requests(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            client = self._user(301, balance=5)
            bet = self._bet()
            bet.delivery_mode = "paid_set"
            session.add_all([admin, client, bet])
            await session.flush()
            forecast_request = ForecastRequest(
                bet_id=bet.id,
                user_id=client.telegram_id,
                status="interested",
            )
            session.add(forecast_request)
            await session.commit()

            response = await stop_forecast_broadcast_from_admin(bet.id, current_admin=admin, db=session)

            self.assertEqual(response["status"], "success")
            self.assertEqual(response["stopped_requests"], 1)
            self.assertEqual(bet.status, "pending")
            self.assertFalse(bet.auto_send_on_interest)
            refreshed = await session.get(ForecastRequest, forecast_request.id)
            self.assertEqual(refreshed.status, FORECAST_STATUS_REMOVED)

    async def test_stopping_forecast_broadcast_keeps_match_pending_for_results(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            interested_client = self._user(311, balance=5)
            sent_client = self._user(312, balance=4)
            bet = self._bet()
            bet.delivery_mode = "sales_private"
            bet.auto_send_on_interest = True
            session.add_all([admin, interested_client, sent_client, bet])
            await session.flush()
            request_interested = ForecastRequest(
                bet_id=bet.id,
                user_id=interested_client.telegram_id,
                status="interested",
            )
            request_sent = ForecastRequest(
                bet_id=bet.id,
                user_id=sent_client.telegram_id,
                status="sent",
            )
            session.add_all([request_interested, request_sent])
            await session.commit()

            response = await stop_forecast_broadcast_from_admin(bet.id, current_admin=admin, db=session)

            self.assertEqual(response["status"], "success")
            self.assertFalse(response["already_stopped"])
            self.assertEqual(response["stopped_requests"], 1)
            self.assertEqual(bet.status, "pending")
            self.assertFalse(bet.auto_send_on_interest)
            refreshed_requests = (
                await session.execute(select(ForecastRequest).filter(ForecastRequest.bet_id == bet.id))
            ).scalars().all()
            statuses_by_user_id = {
                forecast_request.user_id: forecast_request.status
                for forecast_request in refreshed_requests
            }
            self.assertEqual(statuses_by_user_id[interested_client.telegram_id], FORECAST_STATUS_REMOVED)
            self.assertEqual(statuses_by_user_id[sent_client.telegram_id], "sent")

            pending_bets = await get_pending_bets(admin=admin, db=session)
            self.assertIn(bet.id, {pending_bet.id for pending_bet in pending_bets})

    async def test_legacy_stopped_unresolved_forecast_still_appears_for_results(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            legacy_stopped_bet = self._bet(status="deleted")
            legacy_stopped_bet.delivery_mode = "sales_private"
            legacy_stopped_bet.resolved_at = None
            truly_deleted_bet = self._bet(status="deleted")
            truly_deleted_bet.delivery_mode = "sales_private"
            session.add_all([admin, legacy_stopped_bet, truly_deleted_bet])
            await session.flush()
            truly_deleted_bet.resolved_at = legacy_stopped_bet.created_at
            await session.commit()

            pending_bets = await get_pending_bets(admin=admin, db=session)
            pending_ids = {pending_bet.id for pending_bet in pending_bets}

            self.assertIn(legacy_stopped_bet.id, pending_ids)
            self.assertNotIn(truly_deleted_bet.id, pending_ids)

    async def test_stop_forecast_broadcast_queues_admin_group_summary_with_client_takers(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            taker_one = self._user(401, balance=3)
            taker_two = self._user(402, balance=4)
            staff_taker = self._user(403, balance=0, role="moderator")
            announced_client = self._user(404, balance=2)
            bet = self._bet()
            bet.delivery_mode = "sales_private"
            session.add_all([admin, taker_one, taker_two, staff_taker, announced_client, bet])
            await session.flush()
            await self._add_access(session, user=taker_one, bet=bet)
            await self._add_access(session, user=taker_two, bet=bet)
            await self._add_access(session, user=staff_taker, bet=bet)
            session.add_all([
                ForecastRequest(bet_id=bet.id, user_id=taker_one.telegram_id, status="sent"),
                ForecastRequest(bet_id=bet.id, user_id=taker_two.telegram_id, status="manual_sent"),
                ForecastRequest(bet_id=bet.id, user_id=staff_taker.telegram_id, status="manual_sent"),
                ForecastRequest(bet_id=bet.id, user_id=announced_client.telegram_id, status="announced"),
            ])
            await session.commit()

            previous_chat_id = forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
            try:
                forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
                response = await stop_forecast_broadcast_from_admin(bet.id, current_admin=admin, db=session)
            finally:
                forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

            self.assertEqual(response["status"], "success")
            self.assertEqual(response["taker_count"], 2)
            self.assertIsNone(bet.resolved_at)
            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            group_items = [
                item
                for item in outbox_items
                if item.payload["payload"]["chat_id"] == -100555
            ]
            self.assertEqual(len(group_items), 1)
            text = group_items[0].payload["payload"]["text"]
            self.assertIn("Раздача остановлена", text)
            self.assertIn("Team A - Team B", text)
            self.assertIn("Взяли: <b>2</b>", text)
            self.assertIn("Формат: <b>прогноз</b>", text)

    async def test_stop_paid_set_broadcast_queues_admin_group_summary_with_paid_set_label(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            taker = self._user(501, balance=6)
            bet = self._bet()
            bet.delivery_mode = "paid_set"
            bet.event_name = "VIP weekend set"
            session.add_all([admin, taker, bet])
            await session.flush()
            await self._add_access(
                session,
                user=taker,
                bet=bet,
                access_type="manual_paid_set",
                match_charged=False,
            )
            session.add(ForecastRequest(bet_id=bet.id, user_id=taker.telegram_id, status="manual_sent"))
            await session.commit()

            previous_chat_id = forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID
            try:
                forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = -100555
                response = await stop_forecast_broadcast_from_admin(bet.id, current_admin=admin, db=session)
            finally:
                forecast_delivery.settings.TELEGRAM_ADMIN_GROUP_CHAT_ID = previous_chat_id

            self.assertEqual(response["taker_count"], 1)
            self.assertIsNone(bet.resolved_at)
            outbox_items = (await session.execute(select(DeliveryOutbox))).scalars().all()
            self.assertEqual(len(outbox_items), 1)
            text = outbox_items[0].payload["payload"]["text"]
            self.assertIn("Раздача остановлена", text)
            self.assertIn("Взяли: <b>1</b>", text)
            self.assertIn("Формат: <b>набор</b>", text)

    async def test_client_taker_count_matches_forecast_and_paid_set_access_rows(self):
        async with self.Session() as session:
            forecast_taker = self._user(601, balance=3)
            paid_set_taker = self._user(602, balance=4)
            staff_taker = self._user(603, balance=0, role="owner")
            forecast_bet = self._bet()
            forecast_bet.delivery_mode = "sales_private"
            paid_set_bet = self._bet()
            paid_set_bet.delivery_mode = "paid_set"
            session.add_all([forecast_taker, paid_set_taker, staff_taker, forecast_bet, paid_set_bet])
            await session.flush()
            await self._add_access(session, user=forecast_taker, bet=forecast_bet)
            await self._add_access(session, user=staff_taker, bet=forecast_bet)
            await self._add_access(
                session,
                user=paid_set_taker,
                bet=paid_set_bet,
                access_type="manual_paid_set",
                match_charged=False,
            )
            await self._add_access(
                session,
                user=staff_taker,
                bet=paid_set_bet,
                access_type="manual_paid_set",
                match_charged=False,
            )
            await session.commit()

            forecast_count = await forecast_delivery.count_client_bet_takers(session, forecast_bet.id)
            paid_set_count = await forecast_delivery.count_client_bet_takers(session, paid_set_bet.id)

            self.assertEqual(forecast_count, 1)
            self.assertEqual(paid_set_count, 1)

    async def test_paid_set_manual_sale_records_client_stats_without_match_debit(self):
        async with self.Session() as session:
            admin = self._user(900, balance=0, role="admin")
            client = self._user(302, balance=4)
            bet = self._bet()
            bet.delivery_mode = "paid_set"
            session.add_all([admin, client, bet])
            await session.flush()
            forecast_request = ForecastRequest(
                bet_id=bet.id,
                user_id=client.telegram_id,
                status="interested",
            )
            session.add(forecast_request)
            await session.commit()

            response = await mark_forecast_request_manual(
                forecast_request.id,
                current_admin=admin,
                db=session,
            )

            self.assertEqual(response.status, "manual_sent")
            self.assertEqual(client.purchased_bets_balance, 4)
            access_rows = (await session.execute(select(user_bets))).all()
            self.assertEqual(len(access_rows), 1)
            access = access_rows[0]._mapping
            self.assertEqual(access["access_type"], "manual_paid_set")
            self.assertFalse(access["match_charged"])
            delivery_rows = (await session.execute(select(DeliveryOutbox))).scalars().all()
            full_delivery_rows = [
                row for row in delivery_rows if row.channel == "forecast_full_delivery"
            ]
            self.assertEqual(len(full_delivery_rows), 1)
            self.assertEqual(full_delivery_rows[0].forecast_request_id, forecast_request.id)
            self.assertEqual(full_delivery_rows[0].payload["delivery_method"], "bot")


if __name__ == "__main__":
    unittest.main()
