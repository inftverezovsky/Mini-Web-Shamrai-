from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.models import SystemSetting

CUSTOM_EMOJI_LIBRARY_SETTING_KEY = "TELEGRAM_CUSTOM_EMOJI_LIBRARY"
CUSTOM_EMOJI_ID_RE = re.compile(r"^\d{5,32}$")
MAX_LIBRARY_ITEMS = 512
MAX_UPLOAD_ITEMS = 64


@dataclass(frozen=True)
class CustomEmojiLibraryItem:
    custom_emoji_id: str
    fallback: str


def _utf16_slice(text: str, offset: int, length: int) -> str:
    encoded = str(text or "").encode("utf-16-le")
    start = max(0, int(offset)) * 2
    end = start + max(0, int(length)) * 2
    return encoded[start:end].decode("utf-16-le", errors="ignore")


def _clean_item(custom_emoji_id: Any, fallback: Any) -> CustomEmojiLibraryItem:
    clean_id = str(custom_emoji_id or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        raise ValueError("Telegram прислал некорректный custom_emoji_id.")
    clean_fallback = str(fallback or "").strip()[:16] or "✨"
    return CustomEmojiLibraryItem(clean_id, clean_fallback)


def extract_custom_emoji_library_items(message: dict[str, Any]) -> tuple[CustomEmojiLibraryItem, ...]:
    text = str(message.get("text") or message.get("caption") or "")
    entities = message.get("entities") or message.get("caption_entities") or []
    items = [
        _clean_item(
            entity.get("custom_emoji_id"),
            _utf16_slice(text, int(entity.get("offset") or 0), int(entity.get("length") or 0)),
        )
        for entity in entities
        if isinstance(entity, dict)
        and entity.get("type") == "custom_emoji"
        and entity.get("custom_emoji_id")
    ]

    sticker = message.get("sticker") or {}
    if sticker.get("custom_emoji_id"):
        items.append(_clean_item(sticker.get("custom_emoji_id"), sticker.get("emoji")))

    unique = {
        item.custom_emoji_id: item
        for item in items
    }
    if not unique:
        raise ValueError("В сообщении не найдено custom emoji.")
    if len(unique) > MAX_UPLOAD_ITEMS:
        raise ValueError(f"За один раз можно добавить не более {MAX_UPLOAD_ITEMS} custom emoji.")
    return tuple(unique.values())


def _parse_library(raw_value: Any) -> tuple[CustomEmojiLibraryItem, ...]:
    try:
        parsed = json.loads(str(raw_value or "[]"))
    except json.JSONDecodeError:
        return ()
    if not isinstance(parsed, list):
        return ()
    items: dict[str, CustomEmojiLibraryItem] = {}
    for raw_item in parsed[:MAX_LIBRARY_ITEMS]:
        if not isinstance(raw_item, dict):
            continue
        try:
            item = _clean_item(
                raw_item.get("custom_emoji_id"),
                raw_item.get("fallback"),
            )
        except ValueError:
            continue
        items[item.custom_emoji_id] = item
    return tuple(items.values())


async def load_custom_emoji_library(
    db: AsyncSession,
) -> tuple[CustomEmojiLibraryItem, ...]:
    result = await db.execute(
        select(SystemSetting).where(
            SystemSetting.key == CUSTOM_EMOJI_LIBRARY_SETTING_KEY
        )
    )
    row = result.scalars().first()
    return _parse_library(row.value if row else None)


async def save_custom_emoji_library_items(
    db: AsyncSession,
    new_items: Iterable[CustomEmojiLibraryItem],
) -> tuple[CustomEmojiLibraryItem, ...]:
    incoming = tuple(new_items)
    if not incoming:
        raise ValueError("Не найдено ни одного custom emoji.")

    current = await load_custom_emoji_library(db)
    merged = {item.custom_emoji_id: item for item in current}
    for item in incoming:
        clean_item = _clean_item(item.custom_emoji_id, item.fallback)
        merged[clean_item.custom_emoji_id] = clean_item
    if len(merged) > MAX_LIBRARY_ITEMS:
        raise ValueError(f"В библиотеке можно хранить не более {MAX_LIBRARY_ITEMS} custom emoji.")

    saved = tuple(merged.values())
    serialized = json.dumps(
        [
            {
                "custom_emoji_id": item.custom_emoji_id,
                "fallback": item.fallback,
            }
            for item in saved
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    result = await db.execute(
        select(SystemSetting).where(
            SystemSetting.key == CUSTOM_EMOJI_LIBRARY_SETTING_KEY
        )
    )
    row = result.scalars().first()
    if row is None:
        db.add(
            SystemSetting(
                key=CUSTOM_EMOJI_LIBRARY_SETTING_KEY,
                value=serialized,
                description="Unlabelled Telegram custom emoji library managed through the bot.",
                is_secret=False,
            )
        )
    else:
        row.value = serialized
        row.description = "Unlabelled Telegram custom emoji library managed through the bot."
        row.is_secret = False
    await db.flush()
    return saved
