import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.future import select

from src.core.bookmakers import ensure_standard_bookmakers
from src.models.database import AsyncSessionLocal
from src.models.models import Bet, CrowdBet, LivePulseLog, Marathon, PromoCode, PvPBattle, Quiz, SubscriptionPlan


DEFAULT_PLANS = [
    {
        "name": "Стартовый абонемент (5 матчей)",
        "duration_days": 0,
        "match_count": 5,
        "price": Decimal("990.00"),
        "price_stars": 0,
    },
    {
        "name": "Профи абонемент (10 матчей)",
        "duration_days": 0,
        "match_count": 10,
        "price": Decimal("1790.00"),
        "price_stars": 0,
    },
    {
        "name": "Максимум абонемент (20 матчей)",
        "duration_days": 0,
        "match_count": 20,
        "price": Decimal("2990.00"),
        "price_stars": 0,
    },
]

DEFAULT_PROMOS = [
    {"code": "WELCOME20", "discount_percent": 20, "days": 365},
    {"code": "HALF50", "discount_percent": 50, "days": 365},
]


async def seed_plans(db) -> int:
    created = 0
    for seed in DEFAULT_PLANS:
        result = await db.execute(select(SubscriptionPlan).filter(SubscriptionPlan.name == seed["name"]))
        plan = result.scalars().first()
        if plan:
            plan.duration_days = seed["duration_days"]
            plan.match_count = seed["match_count"]
            plan.price = seed["price"]
            plan.price_stars = seed["price_stars"]
            plan.currency = "RUB"
            plan.is_active = True
            continue

        db.add(SubscriptionPlan(**seed, currency="RUB", is_active=True))
        created += 1
    return created


async def seed_promos(db) -> int:
    created = 0
    now = datetime.now(timezone.utc)
    for seed in DEFAULT_PROMOS:
        code = seed["code"]
        result = await db.execute(select(PromoCode).filter(PromoCode.code == code))
        promo = result.scalars().first()
        if promo:
            promo.discount_percent = seed["discount_percent"]
            promo.valid_until = now + timedelta(days=seed["days"])
            promo.is_active = True
            continue

        db.add(PromoCode(
            code=code,
            discount_percent=seed["discount_percent"],
            valid_until=now + timedelta(days=seed["days"]),
            is_active=True,
        ))
        created += 1
    return created


async def seed_demo(db) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    created = {"bets": 0, "pulse": 0, "quiz": 0, "pvp": 0, "marathon": 0, "crowd": 0}

    result = await db.execute(select(Bet).filter(Bet.event_name == "Реал - Барселона"))
    conversion_bet = result.scalars().first()
    if not conversion_bet:
        conversion_bet = Bet(
            event_name="Реал - Барселона",
            coefficient=Decimal("2.00"),
            description="Темп Реала выше после 60-й минуты, фланги Барселоны проседают под быстрыми переводами.",
            status="pending",
            outcome="П1",
            price_stars=50,
            brain_score=8,
            sport_type="football",
        )
        db.add(conversion_bet)
        await db.flush()
        created["bets"] += 1

    resolved_seed = [
        ("Реал Мадрид vs Барселона", Decimal("1.85"), "win", 90),
        ("Ливерпуль vs Манчестер Сити", Decimal("2.10"), "loss", 85),
        ("Бавария vs Боруссия Д", Decimal("1.65"), "win", 60),
        ("ПСЖ vs Марсель", Decimal("1.90"), "win", 55),
        ("Интер vs Милан", Decimal("2.25"), "win", 30),
        ("Ювентус vs Наполи", Decimal("1.95"), "loss", 25),
        ("Манчестер Юнайтед vs Ньюкасл", Decimal("2.05"), "win", 5),
    ]
    for event_name, coefficient, status, days_ago in resolved_seed:
        exists = await db.execute(select(Bet).filter(Bet.event_name == event_name))
        if exists.scalars().first():
            continue
        db.add(Bet(
            event_name=event_name,
            coefficient=coefficient,
            status=status,
            created_at=now - timedelta(days=days_ago),
            resolved_at=now - timedelta(days=days_ago),
            outcome="П1",
            sport_type="football",
        ))
        created["bets"] += 1

    quiz_exists = await db.execute(select(Quiz).filter(Quiz.bet_id == conversion_bet.id))
    if not quiz_exists.scalars().first():
        db.add(Quiz(
            bet_id=conversion_bet.id,
            discount_reward=30,
            questions=[
                {
                    "id": "q1",
                    "question": "Что сильнее всего влияет на value в этом матче?",
                    "options": ["Форма атаки", "Цвет формы", "Название стадиона"],
                    "correct": "Форма атаки",
                },
                {
                    "id": "q2",
                    "question": "Какой риск-подход ближе к Bankroll Shield?",
                    "options": ["Флэт 5-10%", "Ва-банк", "Догон после минуса"],
                    "correct": "Флэт 5-10%",
                },
                {
                    "id": "q3",
                    "question": "Что делает коэффициент привлекательным?",
                    "options": ["Разница вероятности и линии", "Просто высокий кэф", "Совет из чата"],
                    "correct": "Разница вероятности и линии",
                },
            ],
        ))
        created["quiz"] += 1

    battle_exists = await db.execute(select(PvPBattle).filter(PvPBattle.match_name == conversion_bet.event_name))
    if not battle_exists.scalars().first():
        db.add(PvPBattle(
            match_name=conversion_bet.event_name,
            option_a=conversion_bet.outcome or "П1",
            option_b="П2",
            votes_a=12,
            votes_b=8,
        ))
        created["pvp"] += 1

    marathon_exists = await db.execute(select(Marathon).filter(Marathon.is_active == True))
    if not marathon_exists.scalars().first():
        db.add(Marathon(
            title="Марафон: Путь к х10 от банка",
            target_multiplier=10.0,
            current_step=5,
            total_steps=10,
            is_active=True,
        ))
        created["marathon"] += 1

    crowd_exists = await db.execute(select(CrowdBet).filter(CrowdBet.status == "funding"))
    if not crowd_exists.scalars().first():
        db.add(CrowdBet(
            bet_id=conversion_bet.id,
            target_amount=1000,
            current_amount=350,
            status="funding",
        ))
        created["crowd"] += 1

    pulse_messages = [
        "@alex*** разблокировал VIP прогноз за 50 Stars",
        "@dmit*** оформил абонемент на 10 матчей",
        "@mari*** выиграл бесплатный прогноз в бонусе",
        "@vla*** разблокировал Live-прогноз с кф 2.45",
        "@serg*** применил промокод WELCOME20",
    ]
    pulse_count = await db.execute(select(LivePulseLog.id).limit(1))
    if not pulse_count.first():
        for index, message in enumerate(pulse_messages):
            db.add(LivePulseLog(text_message=message, created_at=now - timedelta(minutes=index * 3 + 2)))
            created["pulse"] += 1

    return created


async def run(*, demo: bool) -> None:
    async with AsyncSessionLocal() as db:
        await ensure_standard_bookmakers(db)
        plans_created = await seed_plans(db)
        promos_created = await seed_promos(db)
        demo_created = await seed_demo(db) if demo else {}
        await db.commit()

    print({
        "bookmakers": "ensured",
        "plans_created": plans_created,
        "promos_created": promos_created,
        "demo": demo_created,
    })


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Shamrai production defaults.")
    parser.add_argument("--demo", action="store_true", help="Also seed demo bets, marketing widgets, pulse and stats data.")
    args = parser.parse_args()
    asyncio.run(run(demo=args.demo))


if __name__ == "__main__":
    main()
