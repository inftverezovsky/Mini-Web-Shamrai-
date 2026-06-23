from sqlalchemy.future import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import Bookmaker


STANDARD_BOOKMAKERS = [
    {"name": "Фонбет", "code": "fonbet"},
    {"name": "БетБум", "code": "betboom"},
    {"name": "Винлайн", "code": "winline"},
    {"name": "Пари", "code": "pari"},
    {"name": "Лига Ставок", "code": "ligastavok"},
    {"name": "Марафонбет", "code": "marathon"},
    {"name": "Бетсити", "code": "betcity"},
    {"name": "Мелбет", "code": "melbet"},
    {"name": "Леон", "code": "leon"},
    {"name": "Олимпбет", "code": "olimpbet"},
    {"name": "Зенит", "code": "zenit"},
    {"name": "Другие", "code": "other"},
]

STANDARD_BOOKMAKER_ORDER = {
    bookmaker["code"]: index for index, bookmaker in enumerate(STANDARD_BOOKMAKERS)
}


async def ensure_standard_bookmakers(db: AsyncSession) -> list[Bookmaker]:
    result = await db.execute(select(Bookmaker))
    existing = result.scalars().all()
    by_code = {bookmaker.code: bookmaker for bookmaker in existing}
    changed = False

    for seed in STANDARD_BOOKMAKERS:
        bookmaker = by_code.get(seed["code"])
        if bookmaker is None:
            db.add(Bookmaker(name=seed["name"], code=seed["code"], is_active=True))
            changed = True
            continue

        if bookmaker.name != seed["name"]:
            bookmaker.name = seed["name"]
            changed = True
        if not bookmaker.is_active:
            bookmaker.is_active = True
            changed = True

    if changed:
        await db.flush()

    result = await db.execute(select(Bookmaker).filter(Bookmaker.is_active == True))
    bookmakers = result.scalars().all()
    return sorted(
        bookmakers,
        key=lambda bookmaker: STANDARD_BOOKMAKER_ORDER.get(bookmaker.code, 999),
    )
