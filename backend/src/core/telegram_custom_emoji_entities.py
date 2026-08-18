from __future__ import annotations

import json
import re
from typing import Any, Iterable

CUSTOM_EMOJI_ID_RE = re.compile(r"^\d{5,32}$")
MAX_CUSTOM_EMOJI_ENTITIES = 100


def utf16_length(value: str) -> int:
    return len(str(value or "").encode("utf-16-le")) // 2


def parse_custom_emoji_entities(
    raw_value: str | None,
    text: str,
    *,
    allowed_custom_emoji_ids: set[str] | None = None,
) -> list[dict[str, int | str]]:
    clean_value = str(raw_value or "").strip()
    if not clean_value:
        return []
    try:
        parsed = json.loads(clean_value)
    except json.JSONDecodeError as exc:
        raise ValueError("Некорректный список custom emoji.") from exc
    if not isinstance(parsed, list):
        raise ValueError("Список custom emoji должен быть массивом.")
    if len(parsed) > MAX_CUSTOM_EMOJI_ENTITIES:
        raise ValueError(
            f"В одном поле можно использовать не более {MAX_CUSTOM_EMOJI_ENTITIES} custom emoji."
        )

    text_length = utf16_length(text)
    result: list[dict[str, int | str]] = []
    previous_end = 0
    for raw_entity in parsed:
        if not isinstance(raw_entity, dict):
            raise ValueError("Некорректная запись custom emoji.")
        try:
            offset = int(raw_entity.get("offset"))
            length = int(raw_entity.get("length"))
        except (TypeError, ValueError) as exc:
            raise ValueError("Некорректная позиция custom emoji.") from exc
        custom_emoji_id = str(raw_entity.get("custom_emoji_id") or "").strip()
        if not CUSTOM_EMOJI_ID_RE.fullmatch(custom_emoji_id):
            raise ValueError("Некорректный custom_emoji_id.")
        if allowed_custom_emoji_ids is not None and custom_emoji_id not in allowed_custom_emoji_ids:
            raise ValueError("Custom emoji отсутствует в загруженной библиотеке.")
        if offset < 0 or length <= 0 or offset + length > text_length:
            raise ValueError("Custom emoji выходит за границы текста.")
        if offset < previous_end:
            raise ValueError("Позиции custom emoji пересекаются.")
        previous_end = offset + length
        result.append(
            {
                "offset": offset,
                "length": length,
                "custom_emoji_id": custom_emoji_id,
            }
        )
    return result


def combine_custom_emoji_entities(
    title: str,
    title_entities: Iterable[dict[str, Any]],
    body: str,
    body_entities: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    clean_title = str(title or "")
    clean_body = str(body or "")
    separator = "\n\n" if clean_title and clean_body else ""
    body_shift = utf16_length(clean_title + separator)

    entities = [
        {
            "type": "custom_emoji",
            "offset": int(entity["offset"]),
            "length": int(entity["length"]),
            "custom_emoji_id": str(entity["custom_emoji_id"]),
        }
        for entity in title_entities
    ]
    entities.extend(
        {
            "type": "custom_emoji",
            "offset": body_shift + int(entity["offset"]),
            "length": int(entity["length"]),
            "custom_emoji_id": str(entity["custom_emoji_id"]),
        }
        for entity in body_entities
    )
    return {
        "text": f"{clean_title}{separator}{clean_body}",
        "entities": entities,
    }
