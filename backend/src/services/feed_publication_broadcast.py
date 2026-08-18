from __future__ import annotations

import asyncio
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.core.roles import STAFF_ROLES
from src.core.telegram_custom_emoji_entities import combine_custom_emoji_entities
from src.core.telegram_delivery import user_can_receive_personal_telegram
from src.models.models import Bet, User
from src.services.delivery_outbox import (
    enqueue_feed_forecast_broadcast_batch,
    enqueue_telegram_message_batch,
)
from src.services.forecast_delivery import send_feed_forecast_to_telegram_chat


async def load_all_registered_telegram_clients(db: AsyncSession) -> list[User]:
    result = await db.execute(
        select(User).filter(
            User.role.notin_(list(STAFF_ROLES)),
            User.telegram_id > 0,
        )
    )
    return [
        user
        for user in result.scalars().all()
        if user_can_receive_personal_telegram(user)
    ]


async def enqueue_feed_publication_broadcast(
    db: AsyncSession,
    bet: Bet,
) -> int:
    recipients = await load_all_registered_telegram_clients(db)
    user_ids = [user.telegram_id for user in recipients]
    publication_type = str(bet.publication_type or "forecast").strip().lower()

    if publication_type == "text":
        telegram_post = combine_custom_emoji_entities(
            bet.event_name,
            bet.event_name_entities or [],
            bet.description or "",
            bet.description_entities or [],
        )
        outbox_items = await enqueue_telegram_message_batch(
            db,
            text=telegram_post["text"],
            entities=telegram_post["entities"],
            user_ids=user_ids,
            dedupe_prefix=f"feed_text:{bet.id}",
        )
        return len(outbox_items)

    outbox_items = await enqueue_feed_forecast_broadcast_batch(
        db,
        bet_id=bet.id,
        user_ids=user_ids,
    )
    return len(outbox_items)


async def dispatch_feed_forecast_broadcast(
    db: AsyncSession,
    *,
    bet_id: UUID,
    chat_id: int,
) -> dict:
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(
            selectinload(Bet.bookmaker),
            selectinload(Bet.bookmakers),
        )
    )
    bet = result.scalars().first()
    if bet is None:
        return {"ok": False, "description": "Feed forecast was not found"}
    if str(bet.publication_type or "forecast").strip().lower() != "forecast":
        return {"ok": False, "description": "Feed publication is not a forecast"}

    return await asyncio.to_thread(
        send_feed_forecast_to_telegram_chat,
        bet,
        chat_id=chat_id,
    )
