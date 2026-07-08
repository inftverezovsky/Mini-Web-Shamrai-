from __future__ import annotations

import re
import hmac
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable
from urllib.parse import urlparse

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from src.core.config import settings
from src.core.redis_cache import cache_delete, cache_delete_pattern, cache_get_json, cache_set_json
from src.models.models import SystemSetting
from src.services.telegram_auth import TELEGRAM_AUTH_SESSION_CACHE_PREFIX
from src.services.vk_auth_flow import VK_AUTH_FLOW_CACHE_PREFIX


SYSTEM_SETTINGS_CACHE_KEY = "admin:system_settings:v1"
SYSTEM_SETTINGS_CACHE_TTL_SECONDS = 60
MAX_SETTING_VALUE_LENGTH = 65536
THEME_PRIMARY_COLOR_KEY = "THEME_PRIMARY_COLOR"
THEME_SECONDARY_COLOR_KEY = "THEME_SECONDARY_COLOR"
GLOBAL_PERFORMANCE_MODE_KEY = "GLOBAL_PERFORMANCE_MODE"
WELCOME_QUIZ_ENABLED_KEY = "WELCOME_QUIZ_ENABLED"
SUBSCRIPTION_PURCHASES_ENABLED_KEY = "SUBSCRIPTION_PURCHASES_ENABLED"
REFERRAL_PROGRAM_ENABLED_KEY = "REFERRAL_PROGRAM_ENABLED"
REFERRAL_DISCOUNT_ENABLED_KEY = "REFERRAL_DISCOUNT_ENABLED"
REFERRAL_DISCOUNT_STEP_PERCENT_KEY = "REFERRAL_DISCOUNT_STEP_PERCENT"
REFERRAL_DISCOUNT_MAX_PERCENT_KEY = "REFERRAL_DISCOUNT_MAX_PERCENT"
REFERRAL_MATCH_REWARD_ENABLED_KEY = "REFERRAL_MATCH_REWARD_ENABLED"
REFERRAL_MATCH_REWARD_COUNT_KEY = "REFERRAL_MATCH_REWARD_COUNT"
BRAND_LOGO_URL_KEY = "BRAND_LOGO_URL"
BRAND_BACKGROUND_URL_KEY = "BRAND_BACKGROUND_URL"
THEME_GLASS_OPACITY_KEY = "THEME_GLASS_OPACITY"
THEME_GLASS_BLUR_PX_KEY = "THEME_GLASS_BLUR_PX"
THEME_RADIUS_SCALE_KEY = "THEME_RADIUS_SCALE"
THEME_FONT_SCALE_KEY = "THEME_FONT_SCALE"
THEME_DENSITY_KEY = "THEME_DENSITY"
THEME_GLOW_STRENGTH_KEY = "THEME_GLOW_STRENGTH"
DEFAULT_THEME_PRIMARY_COLOR = "#00d2ff"
DEFAULT_THEME_SECONDARY_COLOR = "#d946ef"
DEFAULT_THEME_DENSITY = "compact"
INTEGRATION_UNLOCK_CACHE_PREFIX = "admin:integration_unlock:v1"
INTEGRATION_UNLOCK_TTL_SECONDS = 15 * 60
_HEX_COLOR_PATTERN = re.compile(r"^#[0-9a-fA-F]{6}$")
_THEME_DENSITY_VALUES = {"compact", "cozy", "comfortable"}
_IN_MEMORY_UNLOCK_TOKENS: dict[str, datetime] = {}

_THEME_NUMBER_RANGES: dict[str, tuple[float, float]] = {
    THEME_GLASS_OPACITY_KEY: (0.15, 0.9),
    THEME_GLASS_BLUR_PX_KEY: (0, 36),
    THEME_RADIUS_SCALE_KEY: (0.75, 1.5),
    THEME_FONT_SCALE_KEY: (0.85, 1.2),
    THEME_GLOW_STRENGTH_KEY: (0, 1.6),
}
_INTEGER_RANGES: dict[str, tuple[int, int]] = {
    REFERRAL_DISCOUNT_STEP_PERCENT_KEY: (0, 100),
    REFERRAL_DISCOUNT_MAX_PERCENT_KEY: (0, 100),
    REFERRAL_MATCH_REWARD_COUNT_KEY: (0, 1000),
}


@dataclass(frozen=True)
class SystemSettingDefinition:
    key: str
    default_value: str
    description: str
    is_secret: bool = False
    value_kind: str = "text"
    env_key: str | None = None
    integrations_visible: bool = False


SYSTEM_SETTING_DEFINITIONS: tuple[SystemSettingDefinition, ...] = (
    SystemSettingDefinition(
        key="MAINTENANCE_MODE",
        default_value="false",
        description="Режим обслуживания для временного ограничения пользовательских сценариев.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key="DISABLE_REGISTRATIONS",
        default_value="false",
        description="Закрытый клуб: запрет новых регистраций.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key="PAUSE_BROADCASTS",
        default_value="false",
        description="Экстренная пауза исходящих рассылок.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=WELCOME_QUIZ_ENABLED_KEY,
        default_value="false",
        description="Включает подробный приветственный опрос после шага VK-привязки.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=SUBSCRIPTION_PURCHASES_ENABLED_KEY,
        default_value="false",
        description="Показывает клиентам покупку абонементов и переход к оплате матчей.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=REFERRAL_PROGRAM_ENABLED_KEY,
        default_value="false",
        description="Включает реферальную программу и учет квалифицированных покупок.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=REFERRAL_DISCOUNT_ENABLED_KEY,
        default_value="true",
        description="Включает автоматическую скидку пригласившему за оплаченных рефералов.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=REFERRAL_DISCOUNT_STEP_PERCENT_KEY,
        default_value="5",
        description="Процент скидки за одного приглашенного клиента с квалифицированной покупкой.",
        value_kind="integer",
    ),
    SystemSettingDefinition(
        key=REFERRAL_DISCOUNT_MAX_PERCENT_KEY,
        default_value="100",
        description="Максимальная автоматическая реферальная скидка на абонемент.",
        value_kind="integer",
    ),
    SystemSettingDefinition(
        key=REFERRAL_MATCH_REWARD_ENABLED_KEY,
        default_value="false",
        description="Начисляет пригласившему матчи после первой квалифицированной покупки реферала.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=REFERRAL_MATCH_REWARD_COUNT_KEY,
        default_value="0",
        description="Количество матчей, начисляемых пригласившему за первую покупку реферала.",
        value_kind="integer",
    ),
    SystemSettingDefinition(
        key="VK_ACCESS_TOKEN",
        default_value="",
        description="Токен доступа группы VK для внешних интеграций.",
        is_secret=True,
        value_kind="secret",
        env_key="VK_GROUP_ACCESS_TOKEN",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TELEGRAM_BOT_TOKEN",
        default_value="",
        description="Токен Telegram-бота для внешних интеграций.",
        is_secret=True,
        value_kind="secret",
        env_key="TELEGRAM_BOT_TOKEN",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="PAYMENT_GATEWAY_TOKEN",
        default_value="",
        description="Legacy-токен платежного шлюза для обратной совместимости.",
        is_secret=True,
        value_kind="secret",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TELEGRAM_WEBHOOK_SECRET_TOKEN",
        default_value="",
        description="Секрет проверки Telegram webhook.",
        is_secret=True,
        value_kind="secret",
        env_key="TELEGRAM_WEBHOOK_SECRET_TOKEN",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_CALLBACK_CONFIRMATION_CODE",
        default_value="",
        description="Код подтверждения VK Callback API.",
        is_secret=True,
        value_kind="secret",
        env_key="VK_CALLBACK_CONFIRMATION_CODE",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_CALLBACK_SECRET",
        default_value="",
        description="Секретный ключ VK Callback API.",
        is_secret=True,
        value_kind="secret",
        env_key="VK_CALLBACK_SECRET",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_ID_CLIENT_SECRET",
        default_value="",
        description="Client secret для VK ID OAuth.",
        is_secret=True,
        value_kind="secret",
        env_key="VK_ID_CLIENT_SECRET",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="YOOKASSA_SECRET_KEY",
        default_value="",
        description="Секретный ключ YooKassa.",
        is_secret=True,
        value_kind="secret",
        env_key="YOOKASSA_SECRET_KEY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TEGRO_API_KEY",
        default_value="",
        description="API key Tegro.",
        is_secret=True,
        value_kind="secret",
        env_key="TEGRO_API_KEY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TEGRO_SECRET_KEY",
        default_value="",
        description="Secret key Tegro.",
        is_secret=True,
        value_kind="secret",
        env_key="TEGRO_SECRET_KEY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="WEB_PUSH_VAPID_PRIVATE_KEY",
        default_value="",
        description="Private VAPID key для Web Push.",
        is_secret=True,
        value_kind="secret",
        env_key="WEB_PUSH_VAPID_PRIVATE_KEY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_OAUTH_CLIENT_SECRET",
        default_value="",
        description="Google OAuth client secret для Drive export.",
        is_secret=True,
        value_kind="secret",
        env_key="GOOGLE_OAUTH_CLIENT_SECRET",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_OAUTH_REFRESH_TOKEN",
        default_value="",
        description="Google OAuth refresh token для Drive export.",
        is_secret=True,
        value_kind="secret",
        env_key="GOOGLE_OAUTH_REFRESH_TOKEN",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_SERVICE_ACCOUNT_JSON_B64",
        default_value="",
        description="Service account JSON в base64 для Google Drive.",
        is_secret=True,
        value_kind="secret",
        env_key="GOOGLE_SERVICE_ACCOUNT_JSON_B64",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="HTTPS_PROXY",
        default_value="",
        description="Proxy URL для интеграций, где он нужен.",
        is_secret=True,
        value_kind="secret",
        env_key="HTTPS_PROXY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TELEGRAM_BOT_USERNAME",
        default_value="Shamra1_bot",
        description="Username Telegram-бота.",
        env_key="TELEGRAM_BOT_USERNAME",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TELEGRAM_VIP_CHAT_ID",
        default_value="",
        description="ID закрытого Telegram VIP-чата.",
        env_key="TELEGRAM_VIP_CHAT_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TELEGRAM_ADMIN_GROUP_CHAT_ID",
        default_value="",
        description="ID Telegram-группы администраторов.",
        env_key="TELEGRAM_ADMIN_GROUP_CHAT_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="SALES_MANAGER_TELEGRAM_ID",
        default_value="",
        description="Telegram ID менеджера продаж.",
        env_key="SALES_MANAGER_TELEGRAM_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="SHAMRAI_ONBOARDING_REPORT_CHAT_ID",
        default_value="",
        description="Telegram chat ID для onboarding-отчетов.",
        env_key="SHAMRAI_ONBOARDING_REPORT_CHAT_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_ID_APP_ID",
        default_value="",
        description="App ID для VK ID.",
        env_key="VK_ID_APP_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_ID_REDIRECT_URI",
        default_value="",
        description="Redirect URI для VK ID.",
        value_kind="url",
        env_key="VK_ID_REDIRECT_URI",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_GROUP_ID",
        default_value="",
        description="ID группы VK.",
        env_key="VK_GROUP_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VK_API_VERSION",
        default_value="5.199",
        description="Версия VK API.",
        env_key="VK_API_VERSION",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="API_BASE_URL",
        default_value="https://shamra1.pro",
        description="Базовый URL API.",
        value_kind="url",
        env_key="API_BASE_URL",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="FRONTEND_BASE_URL",
        default_value="https://shamra1.pro/app",
        description="Базовый URL mini app.",
        value_kind="url",
        env_key="FRONTEND_BASE_URL",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="YOOKASSA_SHOP_ID",
        default_value="",
        description="Shop ID YooKassa.",
        env_key="YOOKASSA_SHOP_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="YOOKASSA_RETURN_URL",
        default_value="",
        description="Return URL YooKassa.",
        value_kind="url",
        env_key="YOOKASSA_RETURN_URL",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TEGRO_SHOP_ID",
        default_value="",
        description="Shop ID Tegro.",
        env_key="TEGRO_SHOP_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TEGRO_RETURN_URL",
        default_value="",
        description="Return URL Tegro.",
        value_kind="url",
        env_key="TEGRO_RETURN_URL",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="TEGRO_API_BASE_URL",
        default_value="https://tegro.money/api",
        description="Base URL Tegro API.",
        value_kind="url",
        env_key="TEGRO_API_BASE_URL",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="WEB_PUSH_VAPID_PUBLIC_KEY",
        default_value="",
        description="Public VAPID key для Web Push.",
        env_key="WEB_PUSH_VAPID_PUBLIC_KEY",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="WEB_PUSH_VAPID_SUBJECT",
        default_value="mailto:support@shamra1.pro",
        description="Subject для Web Push.",
        env_key="WEB_PUSH_VAPID_SUBJECT",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_DRIVE_STATS_ENABLED",
        default_value="false",
        description="Включение Google Drive stats export.",
        value_kind="boolean",
        env_key="GOOGLE_DRIVE_STATS_ENABLED",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_DRIVE_AUTH_MODE",
        default_value="auto",
        description="Режим авторизации Google Drive.",
        env_key="GOOGLE_DRIVE_AUTH_MODE",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_DRIVE_STATS_FOLDER_ID",
        default_value="",
        description="Folder ID для выгрузки статистики в Google Drive.",
        env_key="GOOGLE_DRIVE_STATS_FOLDER_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_OAUTH_CLIENT_ID",
        default_value="",
        description="Google OAuth client ID.",
        env_key="GOOGLE_OAUTH_CLIENT_ID",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="GOOGLE_OAUTH_TOKEN_URI",
        default_value="https://oauth2.googleapis.com/token",
        description="Google OAuth token URI.",
        value_kind="url",
        env_key="GOOGLE_OAUTH_TOKEN_URI",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="SUPPORT_URL",
        default_value="",
        description="Публичная ссылка на поддержку.",
        value_kind="url",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key="VIP_CHANNEL_URL",
        default_value="",
        description="Ссылка на закрытый канал.",
        value_kind="url",
        integrations_visible=True,
    ),
    SystemSettingDefinition(
        key=THEME_PRIMARY_COLOR_KEY,
        default_value=DEFAULT_THEME_PRIMARY_COLOR,
        description="Основной неоновый цвет интерфейса.",
        value_kind="hex_color",
    ),
    SystemSettingDefinition(
        key=THEME_SECONDARY_COLOR_KEY,
        default_value=DEFAULT_THEME_SECONDARY_COLOR,
        description="Дополнительный неоновый цвет интерфейса.",
        value_kind="hex_color",
    ),
    SystemSettingDefinition(
        key=GLOBAL_PERFORMANCE_MODE_KEY,
        default_value="false",
        description="Глобальный режим сниженной анимации и эффектов.",
        value_kind="boolean",
    ),
    SystemSettingDefinition(
        key=BRAND_LOGO_URL_KEY,
        default_value="",
        description="URL логотипа бренда для интерфейса.",
        value_kind="url",
    ),
    SystemSettingDefinition(
        key=BRAND_BACKGROUND_URL_KEY,
        default_value="",
        description="URL фонового изображения бренда.",
        value_kind="url",
    ),
    SystemSettingDefinition(
        key=THEME_GLASS_OPACITY_KEY,
        default_value="0.42",
        description="Прозрачность glass-панелей.",
        value_kind="number",
    ),
    SystemSettingDefinition(
        key=THEME_GLASS_BLUR_PX_KEY,
        default_value="18",
        description="Интенсивность blur glass-панелей в пикселях.",
        value_kind="number",
    ),
    SystemSettingDefinition(
        key=THEME_RADIUS_SCALE_KEY,
        default_value="1",
        description="Множитель радиусов интерфейса.",
        value_kind="number",
    ),
    SystemSettingDefinition(
        key=THEME_FONT_SCALE_KEY,
        default_value="1",
        description="Множитель размера шрифтов интерфейса.",
        value_kind="number",
    ),
    SystemSettingDefinition(
        key=THEME_DENSITY_KEY,
        default_value=DEFAULT_THEME_DENSITY,
        description="Плотность интерфейса настроек и админ-панели.",
        value_kind="enum:theme_density",
    ),
    SystemSettingDefinition(
        key=THEME_GLOW_STRENGTH_KEY,
        default_value="1",
        description="Интенсивность фирменного неонового свечения.",
        value_kind="number",
    ),
)

_DEFINITIONS_BY_KEY = {definition.key: definition for definition in SYSTEM_SETTING_DEFINITIONS}
_INTEGRATION_SETTING_KEYS = tuple(
    definition.key for definition in SYSTEM_SETTING_DEFINITIONS if definition.integrations_visible
)
_SESSION_CACHE_PATTERNS = (
    f"{TELEGRAM_AUTH_SESSION_CACHE_PREFIX}:*",
    f"{VK_AUTH_FLOW_CACHE_PREFIX}:*",
)
_PUBLIC_THEME_KEYS = (
    THEME_PRIMARY_COLOR_KEY,
    THEME_SECONDARY_COLOR_KEY,
    GLOBAL_PERFORMANCE_MODE_KEY,
    WELCOME_QUIZ_ENABLED_KEY,
    SUBSCRIPTION_PURCHASES_ENABLED_KEY,
    BRAND_LOGO_URL_KEY,
    BRAND_BACKGROUND_URL_KEY,
    THEME_GLASS_OPACITY_KEY,
    THEME_GLASS_BLUR_PX_KEY,
    THEME_RADIUS_SCALE_KEY,
    THEME_FONT_SCALE_KEY,
    THEME_DENSITY_KEY,
    THEME_GLOW_STRENGTH_KEY,
)
_REFERRAL_SETTING_KEYS = (
    REFERRAL_PROGRAM_ENABLED_KEY,
    REFERRAL_DISCOUNT_ENABLED_KEY,
    REFERRAL_DISCOUNT_STEP_PERCENT_KEY,
    REFERRAL_DISCOUNT_MAX_PERCENT_KEY,
    REFERRAL_MATCH_REWARD_ENABLED_KEY,
    REFERRAL_MATCH_REWARD_COUNT_KEY,
)
_INTEGRATION_GROUP_REQUIRED_KEYS: dict[str, tuple[str, ...]] = {
    "telegram": ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET_TOKEN", "TELEGRAM_BOT_USERNAME"),
    "vk": ("VK_ACCESS_TOKEN", "VK_CALLBACK_CONFIRMATION_CODE", "VK_CALLBACK_SECRET", "VK_GROUP_ID"),
    "payments": ("YOOKASSA_SHOP_ID", "YOOKASSA_SECRET_KEY", "TEGRO_SHOP_ID", "TEGRO_SECRET_KEY"),
    "webpush": ("WEB_PUSH_VAPID_PUBLIC_KEY", "WEB_PUSH_VAPID_PRIVATE_KEY", "WEB_PUSH_VAPID_SUBJECT"),
    "google": ("GOOGLE_DRIVE_STATS_ENABLED", "GOOGLE_DRIVE_STATS_FOLDER_ID", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_SERVICE_ACCOUNT_JSON_B64"),
    "urls": ("API_BASE_URL", "FRONTEND_BASE_URL", "SUPPORT_URL", "VIP_CHANNEL_URL"),
}


def _coerce_boolean_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    clean_value = str(value or "").strip().lower()
    return "true" if clean_value in {"1", "true", "yes", "y", "on", "да"} else "false"


def _normalize_setting_value(definition: SystemSettingDefinition, value: Any) -> str:
    clean_value = str(value or "").strip()
    if len(clean_value) > MAX_SETTING_VALUE_LENGTH:
        raise ValueError(f"Значение {definition.key} слишком длинное")
    if definition.value_kind == "boolean":
        return _coerce_boolean_value(value)
    if definition.value_kind == "hex_color":
        if not _HEX_COLOR_PATTERN.fullmatch(clean_value):
            raise ValueError(f"Значение {definition.key} должно быть HEX-цветом формата #RRGGBB")
        return clean_value.lower()
    if definition.value_kind == "url":
        if not clean_value:
            return ""
        parsed = urlparse(clean_value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.password:
            raise ValueError(f"Значение {definition.key} должно быть безопасной http(s)-ссылкой без логина и пароля")
        return clean_value
    if definition.value_kind == "number":
        try:
            numeric_value = float(clean_value)
        except ValueError as exc:
            raise ValueError(f"Значение {definition.key} должно быть числом") from exc
        min_value, max_value = _THEME_NUMBER_RANGES.get(definition.key, (0.0, 1_000_000.0))
        if numeric_value < min_value or numeric_value > max_value:
            raise ValueError(f"Значение {definition.key} должно быть в диапазоне {min_value:g}-{max_value:g}")
        normalized = f"{numeric_value:.2f}".rstrip("0").rstrip(".")
        return normalized or "0"
    if definition.value_kind == "integer":
        try:
            numeric_value = int(clean_value)
        except ValueError as exc:
            raise ValueError(f"Значение {definition.key} должно быть целым числом") from exc
        min_value, max_value = _INTEGER_RANGES.get(definition.key, (0, 1_000_000))
        if numeric_value < min_value or numeric_value > max_value:
            raise ValueError(f"Значение {definition.key} должно быть в диапазоне {min_value}-{max_value}")
        return str(numeric_value)
    if definition.value_kind == "enum:theme_density":
        normalized = clean_value.lower() or definition.default_value
        if normalized not in _THEME_DENSITY_VALUES:
            raise ValueError(f"Значение {definition.key} должно быть одним из: {', '.join(sorted(_THEME_DENSITY_VALUES))}")
        return normalized
    return clean_value


def _runtime_default_value(definition: SystemSettingDefinition) -> str:
    if not definition.env_key:
        return definition.default_value
    value = getattr(settings, definition.env_key, None)
    if value is None:
        return definition.default_value
    return str(value)


def _setting_value_for_definition(
    definition: SystemSettingDefinition,
    stored_setting: SystemSetting | None,
) -> str:
    return stored_setting.value if stored_setting else _runtime_default_value(definition)


def _serialize_setting(
    definition: SystemSettingDefinition,
    *,
    value: str,
    updated_at: Any = None,
    reveal_secret: bool = False,
) -> dict[str, Any]:
    is_configured = bool(str(value or "").strip())
    return {
        "key": definition.key,
        "value": "" if definition.is_secret and not reveal_secret else str(value or ""),
        "description": definition.description,
        "is_secret": definition.is_secret,
        "is_configured": is_configured,
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at,
    }


async def _load_settings_by_key(db: AsyncSession, keys: Iterable[str] | None = None) -> dict[str, SystemSetting]:
    statement = select(SystemSetting)
    clean_keys = [key for key in (keys or []) if key]
    if clean_keys:
        statement = statement.where(SystemSetting.key.in_(clean_keys))
    result = await db.execute(statement)
    return {setting.key: setting for setting in result.scalars().all()}


async def _build_admin_settings_payload(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    stored_settings = await _load_settings_by_key(db)
    serialized_settings: list[dict[str, Any]] = []
    for definition in SYSTEM_SETTING_DEFINITIONS:
        stored_setting = stored_settings.get(definition.key)
        serialized_settings.append(
            _serialize_setting(
                definition,
                value=_setting_value_for_definition(definition, stored_setting),
                updated_at=stored_setting.updated_at if stored_setting else None,
            )
        )
    return {"settings": serialized_settings}


async def get_admin_system_settings(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    cached_payload = await cache_get_json(SYSTEM_SETTINGS_CACHE_KEY)
    if isinstance(cached_payload, dict) and isinstance(cached_payload.get("settings"), list):
        return cached_payload

    payload = await _build_admin_settings_payload(db)
    await cache_set_json(SYSTEM_SETTINGS_CACHE_KEY, payload, ttl_seconds=SYSTEM_SETTINGS_CACHE_TTL_SECONDS)
    return payload


def verify_integrations_password(password: str) -> bool:
    expected_password = settings.ADMIN_INTEGRATIONS_PASSWORD.strip()
    if not expected_password:
        return False
    return hmac.compare_digest(password.strip(), expected_password)


def _unlock_cache_key(token: str) -> str:
    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return f"{INTEGRATION_UNLOCK_CACHE_PREFIX}:{token_digest}"


async def create_integration_unlock_token(admin_id: int | None = None) -> dict[str, str]:
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=INTEGRATION_UNLOCK_TTL_SECONDS)
    token_payload = {
        "token_type": "integration_unlock",
        "admin_id": admin_id,
        "expires_at": expires_at.isoformat(),
    }
    await cache_set_json(_unlock_cache_key(token), token_payload, ttl_seconds=INTEGRATION_UNLOCK_TTL_SECONDS)
    _IN_MEMORY_UNLOCK_TOKENS[_unlock_cache_key(token)] = expires_at
    return {
        "token_type": "integration_unlock",
        "unlock_token": token,
        "expires_at": expires_at.isoformat(),
    }


async def validate_integration_unlock_token(unlock_token: str) -> bool:
    token = str(unlock_token or "").strip()
    if not token:
        return False
    cache_key = _unlock_cache_key(token)
    payload = await cache_get_json(cache_key)
    if isinstance(payload, dict):
        raw_expires_at = payload.get("expires_at")
        try:
            expires_at = datetime.fromisoformat(str(raw_expires_at))
        except ValueError:
            return False
        return expires_at > datetime.now(timezone.utc)

    expires_at = _IN_MEMORY_UNLOCK_TOKENS.get(cache_key)
    if expires_at is None:
        return False
    if expires_at <= datetime.now(timezone.utc):
        _IN_MEMORY_UNLOCK_TOKENS.pop(cache_key, None)
        return False
    return True


async def get_unlocked_integration_settings(db: AsyncSession) -> dict[str, list[dict[str, Any]]]:
    stored_settings = await _load_settings_by_key(db, _INTEGRATION_SETTING_KEYS)
    serialized_settings: list[dict[str, Any]] = []
    for key in _INTEGRATION_SETTING_KEYS:
        definition = _DEFINITIONS_BY_KEY[key]
        stored_setting = stored_settings.get(key)
        serialized_settings.append(
            _serialize_setting(
                definition,
                value=_setting_value_for_definition(definition, stored_setting),
                updated_at=stored_setting.updated_at if stored_setting else None,
                reveal_secret=True,
            )
        )
    return {"settings": serialized_settings}


async def run_integration_diagnostics(
    db: AsyncSession,
    *,
    unlock_token: str,
    group: str | None = None,
) -> dict[str, Any]:
    if not await validate_integration_unlock_token(unlock_token):
        raise ValueError("Неверный или истекший unlock token интеграций")

    requested_groups = [group] if group else list(_INTEGRATION_GROUP_REQUIRED_KEYS)
    unknown_groups = [item for item in requested_groups if item not in _INTEGRATION_GROUP_REQUIRED_KEYS]
    if unknown_groups:
        raise ValueError(f"Неизвестная группа интеграций: {', '.join(unknown_groups)}")

    stored_settings = await _load_settings_by_key(db, _INTEGRATION_SETTING_KEYS)
    group_payloads: list[dict[str, Any]] = []
    for group_id in requested_groups:
        checks: list[dict[str, Any]] = []
        required_keys = _INTEGRATION_GROUP_REQUIRED_KEYS[group_id]
        for key in required_keys:
            definition = _DEFINITIONS_BY_KEY[key]
            value = _setting_value_for_definition(definition, stored_settings.get(key))
            configured = bool(str(value or "").strip())
            status_value = "ok" if configured else "missing"
            if definition.value_kind == "url" and configured:
                try:
                    _normalize_setting_value(definition, value)
                except ValueError:
                    status_value = "error"
            checks.append({
                "key": key,
                "label": definition.description,
                "status": status_value,
                "configured": configured,
                "is_secret": definition.is_secret,
                "message": "задано" if configured else "не задано",
            })

        statuses = {check["status"] for check in checks}
        group_status = "error" if "error" in statuses else "missing" if statuses == {"missing"} else "warning" if "missing" in statuses else "ok"
        group_payloads.append({
            "group": group_id,
            "status": group_status,
            "checks": checks,
        })

    group_statuses = {item["status"] for item in group_payloads}
    overall_status = "error" if "error" in group_statuses else "missing" if group_statuses == {"missing"} else "warning" if "warning" in group_statuses or "missing" in group_statuses else "ok"
    return {
        "overall_status": overall_status,
        "groups": group_payloads,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


async def update_admin_system_settings(
    db: AsyncSession,
    updates: Iterable[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    pending_updates = list(updates)
    requested_keys = [str(item.get("key") or "").strip() for item in pending_updates]
    unknown_keys = [key for key in requested_keys if key not in _DEFINITIONS_BY_KEY]
    if unknown_keys:
        raise ValueError(f"Неизвестные настройки: {', '.join(unknown_keys)}")

    existing_settings = await _load_settings_by_key(db, requested_keys)
    for item in pending_updates:
        key = str(item.get("key") or "").strip()
        if not key:
            raise ValueError("Ключ настройки не может быть пустым")
        definition = _DEFINITIONS_BY_KEY[key]
        value = _normalize_setting_value(definition, item.get("value"))
        if definition.is_secret and not value:
            continue

        stored_setting = existing_settings.get(key)
        if stored_setting is None:
            stored_setting = SystemSetting(
                key=key,
                value=value,
                description=definition.description,
                is_secret=definition.is_secret,
            )
            db.add(stored_setting)
            existing_settings[key] = stored_setting
        else:
            stored_setting.value = value
            stored_setting.description = definition.description
            stored_setting.is_secret = definition.is_secret

    await db.flush()
    await cache_delete(SYSTEM_SETTINGS_CACHE_KEY)
    return await _build_admin_settings_payload(db)


def _theme_value(stored_settings: dict[str, SystemSetting], key: str) -> str:
    definition = _DEFINITIONS_BY_KEY[key]
    stored_setting = stored_settings.get(key)
    raw_value = stored_setting.value if stored_setting else definition.default_value
    try:
        return _normalize_setting_value(definition, raw_value)
    except ValueError:
        return definition.default_value


def _theme_number_value(stored_settings: dict[str, SystemSetting], key: str) -> float:
    value = _theme_value(stored_settings, key)
    try:
        return float(value)
    except ValueError:
        return float(_DEFINITIONS_BY_KEY[key].default_value)


async def get_public_theme_settings(db: AsyncSession) -> dict[str, str | bool | float | int]:
    stored_settings = await _load_settings_by_key(db, _PUBLIC_THEME_KEYS)
    return {
        "primary_color": _theme_value(stored_settings, THEME_PRIMARY_COLOR_KEY),
        "secondary_color": _theme_value(stored_settings, THEME_SECONDARY_COLOR_KEY),
        "global_performance_mode": _coerce_boolean_value(
            _theme_value(stored_settings, GLOBAL_PERFORMANCE_MODE_KEY)
        ) == "true",
        "welcome_quiz_enabled": _coerce_boolean_value(
            _theme_value(stored_settings, WELCOME_QUIZ_ENABLED_KEY)
        ) == "true",
        "subscription_purchases_enabled": _coerce_boolean_value(
            _theme_value(stored_settings, SUBSCRIPTION_PURCHASES_ENABLED_KEY)
        ) == "true",
        "brand_logo_url": _theme_value(stored_settings, BRAND_LOGO_URL_KEY),
        "brand_background_url": _theme_value(stored_settings, BRAND_BACKGROUND_URL_KEY),
        "glass_opacity": _theme_number_value(stored_settings, THEME_GLASS_OPACITY_KEY),
        "glass_blur_px": int(round(_theme_number_value(stored_settings, THEME_GLASS_BLUR_PX_KEY))),
        "radius_scale": _theme_number_value(stored_settings, THEME_RADIUS_SCALE_KEY),
        "font_scale": _theme_number_value(stored_settings, THEME_FONT_SCALE_KEY),
        "theme_density": _theme_value(stored_settings, THEME_DENSITY_KEY),
        "glow_strength": _theme_number_value(stored_settings, THEME_GLOW_STRENGTH_KEY),
    }


def _integer_value(stored_settings: dict[str, SystemSetting], key: str) -> int:
    definition = _DEFINITIONS_BY_KEY[key]
    stored_setting = stored_settings.get(key)
    raw_value = stored_setting.value if stored_setting else definition.default_value
    try:
        return int(_normalize_setting_value(definition, raw_value))
    except ValueError:
        return int(definition.default_value)


async def get_referral_program_settings(db: AsyncSession) -> dict[str, bool | int]:
    stored_settings = await _load_settings_by_key(db, _REFERRAL_SETTING_KEYS)

    def bool_value(key: str) -> bool:
        definition = _DEFINITIONS_BY_KEY[key]
        stored_setting = stored_settings.get(key)
        raw_value = stored_setting.value if stored_setting else definition.default_value
        return _coerce_boolean_value(raw_value) == "true"

    return {
        "program_enabled": bool_value(REFERRAL_PROGRAM_ENABLED_KEY),
        "discount_enabled": bool_value(REFERRAL_DISCOUNT_ENABLED_KEY),
        "discount_step_percent": _integer_value(stored_settings, REFERRAL_DISCOUNT_STEP_PERCENT_KEY),
        "discount_max_percent": _integer_value(stored_settings, REFERRAL_DISCOUNT_MAX_PERCENT_KEY),
        "match_reward_enabled": bool_value(REFERRAL_MATCH_REWARD_ENABLED_KEY),
        "match_reward_count": _integer_value(stored_settings, REFERRAL_MATCH_REWARD_COUNT_KEY),
    }


async def is_system_setting_enabled(db: AsyncSession, key: str) -> bool:
    definition = _DEFINITIONS_BY_KEY.get(key)
    if definition is None or definition.value_kind != "boolean":
        return False
    if not hasattr(db, "execute"):
        return False
    try:
        stored_settings = await _load_settings_by_key(db, [key])
    except AttributeError:
        return False
    return _coerce_boolean_value(_setting_value_for_definition(definition, stored_settings.get(key))) == "true"


async def reset_user_session_cache() -> dict[str, int | str]:
    deleted = 0
    for pattern in _SESSION_CACHE_PATTERNS:
        deleted += await cache_delete_pattern(pattern)
    return {"status": "success", "deleted": deleted}
