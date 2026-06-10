import html
import json
from functools import lru_cache
from typing import Optional

from src.core.config import settings

SHAMRAI_CONTACT_USERNAME = "@Shamrai_Osnova"
BOOKMAKER_FALLBACK_EMOJI = "🏦"
BOOKMAKER_EMOJI_KEY_ALIASES = {
    "fonbet": ("fonbet", "фонбет", "фонбет (fonbet)"),
    "betboom": ("betboom", "бетбум", "бетбум (betboom)"),
    "winline": ("winline", "винлайн", "винлайн (winline)"),
    "pari": ("pari", "пари", "пари (pari)", "pari (pari)"),
    "ligastavok": ("ligastavok", "liga stavok", "лига ставок", "лига ставок (ligastavok)"),
    "marathon": ("marathon", "марафон", "марафонбет"),
    "betcity": ("betcity", "бетсити", "бетсити (betcity)"),
    "bettery": ("bettery", "беттери", "беттери (bettery)"),
    "melbet": ("melbet", "мелбет", "мелбет (melbet)"),
    "leon": ("leon", "леон", "леон (leon)"),
    "olimpbet": ("olimpbet", "olimp", "олимп", "олимпбет", "олимпбет (olimpbet)"),
    "zenit": ("zenit", "зенит", "зенит (zenit)"),
    "other": ("other", "другие"),
}


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


def _emoji_key(value: Optional[str]) -> str:
    return str(value or "").strip().lower()


def _bookmaker_emoji_keys(code: Optional[str], name: Optional[str] = None) -> list[str]:
    keys: list[str] = []

    def add(value: Optional[str]) -> None:
        key = _emoji_key(value)
        if key and key not in keys:
            keys.append(key)

    add(code)
    add(name)

    name_key = _emoji_key(name)
    if "(" in name_key and ")" in name_key:
        add(name_key.split("(", 1)[0])
        add(name_key.split("(", 1)[1].split(")", 1)[0])

    code_key = _emoji_key(code)
    aliases = BOOKMAKER_EMOJI_KEY_ALIASES.get(code_key, ())
    for alias in aliases:
        add(alias)

    if name_key:
        for alias_code, alias_values in BOOKMAKER_EMOJI_KEY_ALIASES.items():
            if name_key in alias_values:
                add(alias_code)
                for alias in alias_values:
                    add(alias)

    return keys


def bookmaker_custom_emoji(code: Optional[str], name: Optional[str] = None) -> str:
    emoji_map = _parse_custom_emoji_map(settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS)
    for key in _bookmaker_emoji_keys(code, name):
        emoji = custom_emoji(emoji_map.get(key), BOOKMAKER_FALLBACK_EMOJI)
        if emoji:
            return emoji
    return BOOKMAKER_FALLBACK_EMOJI


def sport_custom_emoji(label: Optional[str]) -> str:
    emoji_map = _parse_custom_emoji_map(settings.TELEGRAM_SPORT_CUSTOM_EMOJI_IDS)
    return custom_emoji(emoji_map.get(str(label or "").strip().lower()), "🏟")


def write_emoji() -> str:
    return custom_emoji(settings.TELEGRAM_WRITE_CUSTOM_EMOJI_ID, "✍️") or "✍️"


def contact_footer() -> str:
    return f"Если есть вопросы {write_emoji()} пишите\n{SHAMRAI_CONTACT_USERNAME}"


def append_contact_footer(message: str) -> str:
    return f"{message.rstrip()}\n\n{contact_footer()}"
