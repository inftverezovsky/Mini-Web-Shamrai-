import html
import json
from functools import lru_cache
from typing import Optional

from src.core.config import settings

SHAMRAI_CONTACT_USERNAME = "@Shamrai_Osnova"


def custom_emoji(custom_emoji_id: Optional[str], fallback: str) -> str:
    clean_id = str(custom_emoji_id or "").strip()
    if not clean_id:
        return ""
    return f'<tg-emoji emoji-id="{html.escape(clean_id, quote=True)}">{html.escape(fallback)}</tg-emoji>'


@lru_cache(maxsize=16)
def _parse_custom_emoji_map(raw_value: str) -> dict[str, str]:
    clean_value = str(raw_value or "").strip()
    if not clean_value:
        return {}
    try:
        parsed = json.loads(clean_value)
    except json.JSONDecodeError:
        parsed = {}
        for part in clean_value.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            clean_key = key.strip().lower()
            clean_custom_id = value.strip()
            if clean_key and clean_custom_id:
                parsed[clean_key] = clean_custom_id
    if not isinstance(parsed, dict):
        return {}
    return {
        str(key).strip().lower(): str(value).strip()
        for key, value in parsed.items()
        if str(key).strip() and str(value).strip()
    }


def bookmaker_custom_emoji(code: Optional[str]) -> str:
    emoji_map = _parse_custom_emoji_map(settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS)
    return custom_emoji(emoji_map.get(str(code or "").strip().lower()), "🏦")


def sport_custom_emoji(label: Optional[str]) -> str:
    emoji_map = _parse_custom_emoji_map(settings.TELEGRAM_SPORT_CUSTOM_EMOJI_IDS)
    return custom_emoji(emoji_map.get(str(label or "").strip().lower()), "🏟")


def write_emoji() -> str:
    return custom_emoji(settings.TELEGRAM_WRITE_CUSTOM_EMOJI_ID, "✍️") or "✍️"


def contact_footer() -> str:
    return f"Если есть вопросы {write_emoji()} пишите\n{SHAMRAI_CONTACT_USERNAME}"


def append_contact_footer(message: str) -> str:
    return f"{message.rstrip()}\n\n{contact_footer()}"
