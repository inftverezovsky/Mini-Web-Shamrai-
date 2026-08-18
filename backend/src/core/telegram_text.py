import html
import json
from functools import lru_cache
from typing import Optional

from src.core.config import settings
from src.core.telegram_emoji_catalog import current_emoji_catalog, emoji_catalog_is_loaded

SHAMRAI_CONTACT_USERNAME = "@Shamrai_Osnova"
SHAMRAI_CONTACT_URL = "https://t.me/+OUTzNRDdl9gzNTQy"
BOOKMAKER_FALLBACK_EMOJI = "🏦"
BOOKMAKER_FALLBACK_EMOJIS = {
    "fonbet": "💵",
    "betboom": "💥",
    "winline": "🟠",
    "pari": "🔷",
    "ligastavok": "🏆",
    "marathon": "🏁",
    "betcity": "🏙️",
    "bettery": "⚡",
    "melbet": "🟡",
    "leon": "⭐",
    "olimpbet": "🏛️",
    "olimp": "🏛️",
    "zenit": "🔵",
    "other": BOOKMAKER_FALLBACK_EMOJI,
}
BOOKMAKER_EMOJI_KEY_ALIASES = {
    "fonbet": ("fonbet", "фонбет"),
    "betboom": ("betboom", "бетбум"),
    "winline": ("winline", "винлайн"),
    "pari": ("pari", "пари"),
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
SPORT_EMOJI_GROUPS = (
    (("автогонки", "auto racing", "motorsport"), "🏎️"),
    (("ам. футбол", "американский футбол", "american football"), "🏈"),
    (("бадминтон", "badminton"), "🏸"),
    (("баскетбол", "basketball"), "🏀"),
    (("бейсбол", "baseball"), "⚾"),
    (("бильярд", "billiards", "pool"), "🎱"),
    (("бокс", "boxing"), "🥊"),
    (("велоспорт", "cycling"), "🚴"),
    (("вод. поло", "водное поло", "water polo"), "🤽"),
    (("водные виды", "water sports", "swimming"), "🏊"),
    (("волейбол", "volleyball"), "🏐"),
    (("гандбол", "handball"), "🤾"),
    (("гимнастика", "gymnastics"), "🤸"),
    (("гольф", "golf"), "⛳"),
    (("дартс", "darts"), "🎯"),
    (("единоборства", "martial arts", "mma"), "🥋"),
    (("киберспорт", "esports", "e-sports"), "🎮"),
    (("коньки", "skating", "figure skating"), "⛸️"),
    (("крикет", "cricket"), "🏏"),
    (("л/атл", "легкая атлетика", "athletics"), "🏃"),
    (("лыжи/биатлон", "лыжи", "биатлон", "skiing", "biathlon"), "🎿"),
    (("н/т", "настольный теннис", "table tennis"), "🏓"),
    (("пляж. футб", "пляжный футбол", "beach football"), "⚽"),
    (("регби", "rugby"), "🏉"),
    (("сани/бобслей", "сани", "бобслей", "luge", "bobsleigh"), "🛷"),
    (("теннис", "tennis"), "🎾"),
    (("футбол", "football", "soccer"), "⚽"),
    (("футзал", "futsal"), "⚽"),
    (("хоккей", "hockey", "ice hockey"), "🏒"),
)
SPORT_FALLBACK_EMOJI = "🏅"


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
    emoji_map = (
        current_emoji_catalog().mapping("bookmaker")
        if emoji_catalog_is_loaded()
        else _parse_custom_emoji_map(settings.TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS)
    )
    fallback = bookmaker_fallback_emoji(code, name)
    for key in _bookmaker_emoji_keys(code, name):
        emoji = custom_emoji(emoji_map.get(key), fallback)
        if emoji:
            return emoji
    return fallback


def bookmaker_fallback_emoji(code: Optional[str], name: Optional[str] = None) -> str:
    for key in _bookmaker_emoji_keys(code, name):
        fallback = BOOKMAKER_FALLBACK_EMOJIS.get(key)
        if fallback:
            return fallback
    return BOOKMAKER_FALLBACK_EMOJI


def _sport_emoji_keys(label: Optional[str]) -> tuple[str, ...]:
    key = _emoji_key(label)
    if not key:
        return ()
    for aliases, _fallback in SPORT_EMOJI_GROUPS:
        if key in aliases:
            return (key, *(alias for alias in aliases if alias != key))
    return (key,)


def sport_fallback_emoji(label: Optional[str]) -> str:
    keys = _sport_emoji_keys(label)
    for aliases, fallback in SPORT_EMOJI_GROUPS:
        if any(key in aliases for key in keys):
            return fallback
    return SPORT_FALLBACK_EMOJI


def sport_custom_emoji(label: Optional[str]) -> str:
    emoji_map = (
        current_emoji_catalog().mapping("sport")
        if emoji_catalog_is_loaded()
        else _parse_custom_emoji_map(settings.TELEGRAM_SPORT_CUSTOM_EMOJI_IDS)
    )
    fallback = sport_fallback_emoji(label)
    for key in _sport_emoji_keys(label):
        emoji = custom_emoji(emoji_map.get(key), fallback)
        if emoji:
            return emoji
    return fallback


def write_emoji() -> str:
    custom_id = (
        current_emoji_catalog().write_id
        if emoji_catalog_is_loaded()
        else settings.TELEGRAM_WRITE_CUSTOM_EMOJI_ID
    )
    return custom_emoji(custom_id, "✍️") or "✍️"


def shamrai_custom_emoji(fallback: str = "⚔️") -> str:
    custom_id = (
        current_emoji_catalog().shamrai_id
        if emoji_catalog_is_loaded()
        else settings.TELEGRAM_SHAMRAI_CUSTOM_EMOJI_ID
    )
    return custom_emoji(custom_id, fallback) or fallback


def decor_custom_emoji(key: str, fallback: str) -> str:
    emoji_map = (
        current_emoji_catalog().mapping("decor")
        if emoji_catalog_is_loaded()
        else _parse_custom_emoji_map(settings.TELEGRAM_DECOR_CUSTOM_EMOJI_IDS)
    )
    return custom_emoji(emoji_map.get(str(key or "").strip().lower()), fallback) or fallback


_FORECAST_LINE_DECOR = (
    ("<b>Закрытый анонс прогноза</b>", "forecast", "🔒"),
    ("<b>ПРОГНОЗ SHAMRAI</b>", "forecast", "⚔️"),
    ("Матч:", "match", "🏆"),
    ("Исход:", "outcome", "🎯"),
    ("Коэффициент:", "coefficient", "📈"),
    ("КФ:", "coefficient", "📈"),
    ("Спорт:", "sport", SPORT_FALLBACK_EMOJI),
    ("Стоимость:", "price", "💰"),
)


def decorate_forecast_html(message: str) -> str:
    decorated_lines: list[str] = []
    for line in str(message or "").split("\n"):
        decorated_line = line
        for marker, slot, fallback in _FORECAST_LINE_DECOR:
            if line.startswith(marker):
                icon = decor_custom_emoji(slot, fallback)
                if not line.startswith(icon):
                    decorated_line = f"{icon} {line}"
                break
        decorated_lines.append(decorated_line)
    return "\n".join(decorated_lines)


def contact_footer() -> str:
    contact_link = (
        f'<a href="{html.escape(SHAMRAI_CONTACT_URL, quote=True)}">'
        f"{html.escape(SHAMRAI_CONTACT_USERNAME)}</a>"
    )
    return f"Если есть вопросы {write_emoji()} пишите\n{contact_link}"


def append_contact_footer(message: str) -> str:
    return f"{message.rstrip()}\n\n{contact_footer()}"
