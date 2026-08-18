from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.config import settings
from src.core.telegram_emoji_catalog import (
    EmojiCatalogSnapshot,
    replace_emoji_catalog,
)
from src.models.models import SystemSetting

SHAMRAI_SETTING_KEY = "TELEGRAM_SHAMRAI_CUSTOM_EMOJI_ID"
WRITE_SETTING_KEY = "TELEGRAM_WRITE_CUSTOM_EMOJI_ID"
BOOKMAKER_SETTING_KEY = "TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS"
SPORT_SETTING_KEY = "TELEGRAM_SPORT_CUSTOM_EMOJI_IDS"
DECOR_SETTING_KEY = "TELEGRAM_DECOR_CUSTOM_EMOJI_IDS"
CATALOG_SETTING_KEYS = (
    SHAMRAI_SETTING_KEY,
    WRITE_SETTING_KEY,
    BOOKMAKER_SETTING_KEY,
    SPORT_SETTING_KEY,
    DECOR_SETTING_KEY,
)
MAX_ASSIGNMENTS_PER_MESSAGE = 64
MAX_ITEMS_PER_SCOPE = 256
CUSTOM_EMOJI_ID_RE = re.compile(r"^\d{5,32}$")
SLOT_KEY_RE = re.compile(r"^[0-9a-zа-яё][0-9a-zа-яё_. -]{0,63}$", re.IGNORECASE)

_SCALAR_ALIASES = {
    "shamrai": "shamrai",
    "шамрай": "shamrai",
    "brand": "shamrai",
    "бренд": "shamrai",
    "write": "write",
    "пишите": "write",
    "ручка": "write",
}
_MAPPING_PREFIXES = {
    "bk": "bookmaker",
    "bookmaker": "bookmaker",
    "бк": "bookmaker",
    "sport": "sport",
    "спорт": "sport",
    "decor": "decor",
    "декор": "decor",
}


@dataclass(frozen=True)
class EmojiAssignment:
    scope: str
    key: str | None
    custom_emoji_id: str


def parse_emoji_slot(raw_slot: str) -> tuple[str, str | None]:
    clean_slot = str(raw_slot or "").strip().lower().strip(" \t:-=—–")
    scalar_scope = _SCALAR_ALIASES.get(clean_slot)
    if scalar_scope:
        return scalar_scope, None
    if ":" not in clean_slot:
        raise ValueError(
            "Укажите слот: write, shamrai, bk:код, sport:название или decor:название."
        )
    raw_prefix, raw_key = clean_slot.split(":", 1)
    scope = _MAPPING_PREFIXES.get(raw_prefix.strip())
    key = " ".join(raw_key.strip().split())
    if not scope or not SLOT_KEY_RE.fullmatch(key):
        raise ValueError(
            "Некорректный слот. Используйте bk:код, sport:название или decor:название."
        )
    return scope, key


def _utf16_offset_to_index(text: str, offset: int) -> int:
    prefix_bytes = text.encode("utf-16-le")[: max(offset, 0) * 2]
    return len(prefix_bytes.decode("utf-16-le", errors="ignore"))


def _command_slot(text: str) -> str:
    parts = str(text or "").strip().split(None, 1)
    if len(parts) < 2:
        return ""
    return parts[1].strip().splitlines()[0].strip()


def _slot_before_entity(text: str, entity: dict) -> str:
    start_index = _utf16_offset_to_index(text, int(entity.get("offset") or 0))
    line_start = text.rfind("\n", 0, start_index) + 1
    prefix = text[line_start:start_index].strip().strip(" \t:-=—–")
    if prefix.lower().startswith("/emoji_add"):
        prefix = prefix.split(None, 1)[1].strip() if len(prefix.split(None, 1)) > 1 else ""
    return prefix


def _validated_custom_emoji_id(raw_value: object) -> str:
    clean_id = str(raw_value or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        raise ValueError("Telegram прислал некорректный custom_emoji_id.")
    return clean_id


def _custom_entities(message: dict) -> list[dict]:
    return [
        entity
        for entity in list(message.get("entities") or message.get("caption_entities") or [])
        if entity.get("type") == "custom_emoji" and entity.get("custom_emoji_id")
    ]


def extract_emoji_assignments(message: dict) -> tuple[EmojiAssignment, ...]:
    text = str(message.get("text") or message.get("caption") or "")
    entities = _custom_entities(message)
    assignments: list[EmojiAssignment] = []

    if entities:
        if len(entities) > MAX_ASSIGNMENTS_PER_MESSAGE:
            raise ValueError(f"За один раз можно добавить не более {MAX_ASSIGNMENTS_PER_MESSAGE} эмодзи.")
        for entity in entities:
            scope, key = parse_emoji_slot(_slot_before_entity(text, entity))
            assignments.append(
                EmojiAssignment(
                    scope=scope,
                    key=key,
                    custom_emoji_id=_validated_custom_emoji_id(entity.get("custom_emoji_id")),
                )
            )
    else:
        reply = message.get("reply_to_message") or {}
        reply_entities = _custom_entities(reply)
        sticker_custom_id = (reply.get("sticker") or {}).get("custom_emoji_id")
        reply_ids = [
            _validated_custom_emoji_id(entity.get("custom_emoji_id"))
            for entity in reply_entities
        ]
        if sticker_custom_id:
            reply_ids.append(_validated_custom_emoji_id(sticker_custom_id))
        if len(reply_ids) != 1:
            raise ValueError(
                "Укажите слот после /emoji_add и ответьте на одно кастомное эмодзи или custom-emoji sticker."
            )
        scope, key = parse_emoji_slot(_command_slot(text))
        assignments.append(
            EmojiAssignment(
                scope=scope,
                key=key,
                custom_emoji_id=reply_ids[0],
            )
        )

    unique_slots = {(item.scope, item.key) for item in assignments}
    if len(unique_slots) != len(assignments):
        raise ValueError("Один и тот же слот указан несколько раз.")
    return tuple(assignments)


def _parse_mapping(raw_value: object) -> dict[str, str]:
    clean_value = str(raw_value or "").strip()
    if not clean_value:
        return {}
    try:
        parsed = json.loads(clean_value)
    except json.JSONDecodeError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    return {
        " ".join(str(key).strip().lower().split()): str(value).strip()
        for key, value in parsed.items()
        if SLOT_KEY_RE.fullmatch(str(key).strip()) and CUSTOM_EMOJI_ID_RE.fullmatch(str(value).strip())
    }


def _environment_snapshot() -> EmojiCatalogSnapshot:
    return EmojiCatalogSnapshot.from_values(
        shamrai_id=settings.TELEGRAM_SHAMRAI_CUSTOM_EMOJI_ID,
        write_id=settings.TELEGRAM_WRITE_CUSTOM_EMOJI_ID,
        bookmakers=_parse_mapping(settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS),
        sports=_parse_mapping(settings.TELEGRAM_SPORT_CUSTOM_EMOJI_IDS),
        decor=_parse_mapping(settings.TELEGRAM_DECOR_CUSTOM_EMOJI_IDS),
    )


async def _stored_catalog_values(db: AsyncSession) -> dict[str, str]:
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key.in_(CATALOG_SETTING_KEYS))
    )
    return {
        row.key: str(row.value or "")
        for row in result.scalars().all()
    }


async def load_telegram_emoji_catalog(db: AsyncSession) -> EmojiCatalogSnapshot:
    defaults = _environment_snapshot()
    stored = await _stored_catalog_values(db)
    snapshot = EmojiCatalogSnapshot.from_values(
        shamrai_id=stored.get(SHAMRAI_SETTING_KEY, defaults.shamrai_id),
        write_id=stored.get(WRITE_SETTING_KEY, defaults.write_id),
        bookmakers=_parse_mapping(
            stored.get(BOOKMAKER_SETTING_KEY, json.dumps(defaults.mapping("bookmaker")))
        ),
        sports=_parse_mapping(
            stored.get(SPORT_SETTING_KEY, json.dumps(defaults.mapping("sport")))
        ),
        decor=_parse_mapping(
            stored.get(DECOR_SETTING_KEY, json.dumps(defaults.mapping("decor")))
        ),
    )
    replace_emoji_catalog(snapshot)
    return snapshot


async def _upsert_setting(
    db: AsyncSession,
    *,
    key: str,
    value: str,
    description: str,
) -> None:
    result = await db.execute(select(SystemSetting).where(SystemSetting.key == key))
    row = result.scalars().first()
    if row is None:
        db.add(
            SystemSetting(
                key=key,
                value=value,
                description=description,
                is_secret=False,
            )
        )
        return
    row.value = value
    row.description = description
    row.is_secret = False


async def save_emoji_assignments(
    db: AsyncSession,
    assignments: Iterable[EmojiAssignment],
) -> EmojiCatalogSnapshot:
    clean_assignments = tuple(assignments)
    if not clean_assignments:
        raise ValueError("Не найдено ни одного кастомного эмодзи.")

    current = await load_telegram_emoji_catalog(db)
    shamrai_id = current.shamrai_id
    write_id = current.write_id
    bookmakers = current.mapping("bookmaker")
    sports = current.mapping("sport")
    decor = current.mapping("decor")
    changed_scopes: set[str] = set()

    for assignment in clean_assignments:
        changed_scopes = {*changed_scopes, assignment.scope}
        if assignment.scope == "shamrai":
            shamrai_id = assignment.custom_emoji_id
        elif assignment.scope == "write":
            write_id = assignment.custom_emoji_id
        elif assignment.scope == "bookmaker" and assignment.key:
            bookmakers = {**bookmakers, assignment.key: assignment.custom_emoji_id}
        elif assignment.scope == "sport" and assignment.key:
            sports = {**sports, assignment.key: assignment.custom_emoji_id}
        elif assignment.scope == "decor" and assignment.key:
            decor = {**decor, assignment.key: assignment.custom_emoji_id}
        else:
            raise ValueError("Неизвестная категория кастомного эмодзи.")

    snapshot = EmojiCatalogSnapshot.from_values(
        shamrai_id=shamrai_id,
        write_id=write_id,
        bookmakers=bookmakers,
        sports=sports,
        decor=decor,
    )
    if any(
        len(snapshot.mapping(scope)) > MAX_ITEMS_PER_SCOPE
        for scope in ("bookmaker", "sport", "decor")
    ):
        raise ValueError(f"В одной категории можно хранить не более {MAX_ITEMS_PER_SCOPE} эмодзи.")
    writes = {
        "shamrai": (
            SHAMRAI_SETTING_KEY,
            snapshot.shamrai_id,
            "Custom emoji бренда Shamrai, добавленный владельцем через Telegram-бота.",
        ),
        "write": (
            WRITE_SETTING_KEY,
            snapshot.write_id,
            "Custom emoji для контактного блока, добавленный владельцем через Telegram-бота.",
        ),
        "bookmaker": (
            BOOKMAKER_SETTING_KEY,
            json.dumps(snapshot.mapping("bookmaker"), ensure_ascii=False, separators=(",", ":")),
            "Карта custom emoji букмекеров, управляемая через Telegram-бота.",
        ),
        "sport": (
            SPORT_SETTING_KEY,
            json.dumps(snapshot.mapping("sport"), ensure_ascii=False, separators=(",", ":")),
            "Карта custom emoji видов спорта, управляемая через Telegram-бота.",
        ),
        "decor": (
            DECOR_SETTING_KEY,
            json.dumps(snapshot.mapping("decor"), ensure_ascii=False, separators=(",", ":")),
            "Карта декоративных custom emoji прогнозов и анонсов, управляемая через Telegram-бота.",
        ),
    }
    for scope in sorted(changed_scopes):
        key, value, description = writes[scope]
        await _upsert_setting(db, key=key, value=value, description=description)
    await db.flush()
    return snapshot
