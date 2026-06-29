import argparse
import asyncio

from sqlalchemy import bindparam, text

from src.core.bookmakers import ensure_standard_bookmakers
from src.models.database import AsyncSessionLocal
from src.scripts.seed_defaults import DEFAULT_PLANS, seed_plans


ADMIN_ROLES = ("admin", "owner")

RESET_TABLES = [
    "admin_audit_logs",
    "delivery_outbox",
    "daily_reward_claims",
    "user_badges",
    "user_notes",
    "pvp_battle_votes",
    "crowd_bet_participants",
    "forecast_requests",
    "personal_signals",
    "match_balance_logs",
    "payment_attempts",
    "subscriptions",
    "user_bets",
    "user_bookmakers",
    "bet_bookmakers",
    "quizzes",
    "crowd_bets",
    "pvp_battles",
    "live_pulse_logs",
    "marathons",
    "ab_test_configs",
    "promo_code_redemptions",
    "promo_codes",
    "bets",
]

ADMIN_RESET_COLUMNS = {
    "stats_display_mode": "'percent'",
    "bankroll": "0",
    "is_onboarded": "false",
    "experience_level": "NULL",
    "bankroll_size": "NULL",
    "favorite_sports": "'[]'",
    "risk_tolerance": "NULL",
    "primary_bookmaker": "NULL",
    "currency_preference": "'RUB'",
    "purchased_bets_balance": "0",
    "free_bets_available": "0",
    "matches_remaining": "0",
    "guarantee_active": "false",
    "guarantee_opened_from_bet_id": "NULL",
    "guarantee_closed_at": "NULL",
    "onboarding_goal": "NULL",
    "ab_group": "NULL",
    "tg_chat_joined": "false",
    "has_used_shield": "false",
    "alert_min_coef": "1.0",
    "odds_drop_notifications_enabled": "true",
    "is_night_mode": "false",
    "night_mode_start": "'23:00'",
    "night_mode_end": "'08:00'",
    "preferred_sports": "'[]'",
    "other_bookmaker_name": "NULL",
    "client_group": "NULL",
    "client_tag": "NULL",
}


async def _table_exists(db, table_name: str) -> bool:
    result = await db.execute(
        text(
            """
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
            )
            """
        ),
        {"table_name": table_name},
    )
    return bool(result.scalar())


async def _count(db, table_name: str, where: str = "") -> int:
    result = await db.execute(text(f"SELECT count(*) FROM {table_name} {where}"))
    return int(result.scalar() or 0)


async def collect_counts(db) -> dict[str, int]:
    counts: dict[str, int] = {}
    if not await _table_exists(db, "users"):
        counts["schema_ready"] = 0
        return counts
    counts["schema_ready"] = 1
    counts["users_non_admin"] = await _count(db, "users", "WHERE role NOT IN ('admin', 'owner')")
    counts["users_admin_kept"] = await _count(db, "users", "WHERE role IN ('admin', 'owner')")
    for table_name in ("subscription_plans", "bookmakers"):
        if await _table_exists(db, table_name):
            counts[table_name] = await _count(db, table_name)
    for table_name in RESET_TABLES:
        if await _table_exists(db, table_name):
            counts[table_name] = await _count(db, table_name)
    return counts


async def seed_core_defaults() -> None:
    async with AsyncSessionLocal() as db:
        await ensure_standard_bookmakers(db)
        await seed_plans(db)
        await db.commit()


async def reset_database(*, dry_run: bool, seed: bool) -> dict[str, int | bool]:
    async with AsyncSessionLocal() as db:
        before = await collect_counts(db)
        if dry_run:
            return {"dry_run": True, **before}

        await db.execute(text("UPDATE users SET referred_by_user_id = NULL, guarantee_opened_from_bet_id = NULL"))

        for table_name in RESET_TABLES:
            if await _table_exists(db, table_name):
                await db.execute(text(f"DELETE FROM {table_name}"))

        if await _table_exists(db, "subscription_plans"):
            default_plan_names = [plan["name"] for plan in DEFAULT_PLANS]
            await db.execute(
                text("DELETE FROM subscription_plans WHERE name NOT IN :default_plan_names").bindparams(
                    bindparam("default_plan_names", expanding=True)
                ),
                {"default_plan_names": default_plan_names},
            )

        reset_assignments = ", ".join(f"{column} = {value}" for column, value in ADMIN_RESET_COLUMNS.items())
        await db.execute(
            text(
                f"""
                UPDATE users
                SET {reset_assignments}
                WHERE role IN ('admin', 'owner')
                """
            )
        )
        await db.execute(text("DELETE FROM users WHERE role NOT IN ('admin', 'owner')"))
        await db.commit()

    if seed:
        await seed_core_defaults()

    async with AsyncSessionLocal() as db:
        after = await collect_counts(db)
    return {"dry_run": False, "seeded_defaults": seed, **after}


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset Shamrai client and temporary app data.")
    parser.add_argument("--execute", action="store_true", help="Apply the cleanup. Without this flag only counts are printed.")
    parser.add_argument("--no-seed", action="store_true", help="Do not reseed standard bookmakers and plans.")
    args = parser.parse_args()

    result = asyncio.run(reset_database(dry_run=not args.execute, seed=not args.no_seed))
    for key, value in result.items():
        print(f"{key}: {value}")


if __name__ == "__main__":
    main()
