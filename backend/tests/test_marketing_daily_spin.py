import asyncio
import os
import re
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.future import select

from src.api import marketing as marketing_api
from src.models.database import Base
from src.models.models import (
    Bet,
    Bookmaker,
    DailyRewardClaim,
    MarketingRewardEvent,
    MarketingWidgetConfig,
    PromoCode,
    User,
)
from src.services import marketing_widgets


class DailySpinAtomicityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await self.engine.dispose()

    async def test_repeated_daily_spin_claim_cannot_award_twice(self):
        async with self.Session() as session:
            user = User(telegram_id=6101, username="daily", free_bets_available=0)
            session.add(user)
            await session.commit()

            widget_config = {
                "reward_type": "free_bet",
                "reward_value": 1,
                "promo_valid_hours": 24,
            }
            with (
                patch.object(
                    marketing_api,
                    "ensure_widget_reward_allowed",
                    new=AsyncMock(return_value=widget_config),
                ),
                patch.object(
                    marketing_api,
                    "record_marketing_reward_event",
                    new=AsyncMock(),
                ),
            ):
                first = await marketing_api.claim_daily_bonus(current_user=user, db=session)
                with self.assertRaises(HTTPException) as raised:
                    await marketing_api.claim_daily_bonus(current_user=user, db=session)

            await session.refresh(user)
            claim_count = int(
                (
                    await session.execute(
                        select(func.count(DailyRewardClaim.id)).filter(
                            DailyRewardClaim.user_id == user.telegram_id
                        )
                    )
                ).scalar()
                or 0
            )

            self.assertEqual(first["type"], "free_bet")
            self.assertEqual(raised.exception.status_code, 429)
            self.assertEqual(user.free_bets_available, 1)
            self.assertEqual(claim_count, 1)


_POSTGRES_URL = os.environ.get("SHAMRAI_TEST_POSTGRES_URL", "").strip()
_RUN_POSTGRES = os.environ.get("SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS") == "1"


def _postgres_async_url(value: str) -> str:
    if value.startswith("postgresql://"):
        return value.replace("postgresql://", "postgresql+asyncpg://", 1)
    return value


@unittest.skipUnless(
    _POSTGRES_URL and _RUN_POSTGRES,
    "Set SHAMRAI_TEST_POSTGRES_URL and SHAMRAI_RUN_POSTGRES_CONCURRENCY_TESTS=1 for PostgreSQL row-lock tests",
)
class MarketingRewardPostgresConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    """Real PostgreSQL proof that a final global reward slot cannot be overspent."""

    async def asyncSetUp(self):
        self.schema = f"shamrai_marketing_test_{uuid.uuid4().hex}"
        if not re.fullmatch(r"shamrai_marketing_test_[0-9a-f]{32}", self.schema):
            raise RuntimeError("Unsafe PostgreSQL test schema name")

        postgres_url = _postgres_async_url(_POSTGRES_URL)
        self.admin_engine = create_async_engine(postgres_url, future=True)
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{self.schema}"'))

        self.engine = create_async_engine(
            postgres_url,
            future=True,
            connect_args={"server_settings": {"search_path": f'"{self.schema}",public'}},
        )
        tables = [
            Bookmaker.__table__,
            User.__table__,
            Bet.__table__,
            MarketingWidgetConfig.__table__,
            PromoCode.__table__,
            MarketingRewardEvent.__table__,
        ]
        async with self.engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: Base.metadata.create_all(sync_connection, tables=tables)
            )
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False, autoflush=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin_engine.dispose()

    async def test_two_users_cannot_consume_one_global_daily_reward_slot(self):
        async with self.Session() as session:
            session.add_all([
                User(telegram_id=6201, username="first"),
                User(telegram_id=6202, username="second"),
                MarketingWidgetConfig(
                    key="daily_spin",
                    is_enabled=True,
                    audience="all",
                    cooldown_hours=0,
                    per_user_limit=0,
                    global_daily_limit=1,
                    reward_type="free_bet",
                    reward_value=1,
                    promo_valid_hours=24,
                    settings_json={},
                ),
            ])
            await session.commit()

        barrier = asyncio.Barrier(2)

        async def claim(user_id: int) -> bool:
            async with self.Session() as session:
                user = await session.get(User, user_id)
                await barrier.wait()
                try:
                    await marketing_widgets.ensure_widget_reward_allowed(
                        session,
                        "daily_spin",
                        user,
                    )
                    session.add(MarketingRewardEvent(
                        user_id=user_id,
                        widget_key="daily_spin",
                        reward_type="free_bet",
                        reward_value=1,
                        risk_status="approved",
                        risk_reasons=[],
                    ))
                    await session.commit()
                    return True
                except marketing_widgets.MarketingWidgetLimitError:
                    await session.rollback()
                    return False

        results = await asyncio.gather(claim(6201), claim(6202))

        async with self.Session() as session:
            event_count = int(
                (await session.execute(select(func.count(MarketingRewardEvent.id)))).scalar()
                or 0
            )

        self.assertEqual(sum(results), 1)
        self.assertEqual(event_count, 1)


if __name__ == "__main__":
    unittest.main()
