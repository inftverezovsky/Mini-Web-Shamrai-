from typing import Optional
from pydantic_settings import BaseSettings

LOCAL_DEV_JWT_SECRET = "BET_TMA_LOCAL_DEV_SECRET_CHANGE_ME"

class Settings(BaseSettings):
    APP_ENV: str = "local"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/shamrai"
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_RECYCLE_SECONDS: int = 1800
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = "Shamra1_bot"
    OWNER_TELEGRAM_ID: Optional[int] = None
    DEBUG_MODE: bool = False
    ALLOW_DEBUG_AUTH_BYPASS: bool = False
    JWT_SECRET_KEY: str = LOCAL_DEV_JWT_SECRET
    CORS_ALLOWED_ORIGINS: str = "https://shamra1.pro,https://www.shamra1.pro,http://localhost:8082,http://127.0.0.1:8082"
    ENABLE_BACKGROUND_TASKS: bool = False
    DELIVERY_OUTBOX_BATCH_SIZE: int = 50
    DELIVERY_OUTBOX_IDLE_SECONDS: float = 5.0
    DELIVERY_OUTBOX_DEFAULT_CONCURRENCY: int = 2
    DELIVERY_OUTBOX_CHANNEL_CONCURRENCY: str = "telegram_message=3,vk_message=2,web_push_signal=5,forecast_auto_delivery=1,forecast_full_delivery=1"
    DELIVERY_OUTBOX_RETRY_BASE_SECONDS: int = 30
    DELIVERY_OUTBOX_STALE_LOCK_SECONDS: int = 300
    TELEGRAM_VIP_CHAT_ID: str = "-100200300400"
    TELEGRAM_WEBHOOK_SECRET_TOKEN: str = ""
    TELEGRAM_SHAMRAI_CUSTOM_EMOJI_ID: str = ""
    TELEGRAM_WRITE_CUSTOM_EMOJI_ID: str = ""
    TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS: str = ""
    TELEGRAM_SPORT_CUSTOM_EMOJI_IDS: str = ""
    TELEGRAM_API_TIMEOUT_SECONDS: float = 3.0
    TELEGRAM_API_RETRIES: int = 2
    TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS: float = 4.0
    TELEGRAM_BROADCAST_CONCURRENCY: int = 12
    TELEGRAM_USE_POLLING: bool = False
    TELEGRAM_WEBHOOK_IP_ADDRESS: str = ""
    SALES_MANAGER_TELEGRAM_ID: Optional[int] = None
    SHAMRAI_ONBOARDING_REPORT_CHAT_ID: Optional[int] = None
    NOTIFICATION_TIMEZONE: str = "Europe/Moscow"
    VK_ID_APP_ID: str = ""
    VK_ID_REDIRECT_URI: str = ""
    VK_GROUP_ID: str = ""
    VK_GROUP_ACCESS_TOKEN: str = ""
    VK_CALLBACK_CONFIRMATION_CODE: str = ""
    VK_CALLBACK_SECRET: str = ""
    VK_API_VERSION: str = "5.199"
    VK_BROADCAST_CONCURRENCY: int = 8
    VK_DIALOG_POLLING_ENABLED: bool = False
    VK_DIALOG_POLLING_INTERVAL_SECONDS: float = 4.0
    VK_DIALOG_POLLING_BATCH_SIZE: int = 20
    WEB_PUSH_VAPID_PUBLIC_KEY: str = ""
    WEB_PUSH_VAPID_PRIVATE_KEY: str = ""
    WEB_PUSH_VAPID_SUBJECT: str = "mailto:support@shamra1.pro"
    API_BASE_URL: str = "https://shamra1.pro"
    FRONTEND_BASE_URL: str = "https://shamra1.pro/app"
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = ""
    TEGRO_SHOP_ID: str = ""
    TEGRO_API_KEY: str = ""
    TEGRO_SECRET_KEY: str = ""
    TEGRO_RETURN_URL: str = ""
    TEGRO_API_BASE_URL: str = "https://tegro.money/api"
    HTTPS_PROXY: str = ""
    SECURITY_RATE_LIMIT_MODE: str = "enforce"
    SECURITY_RATE_LIMIT_WINDOW_SECONDS: int = 60
    SECURITY_RATE_LIMIT_GROUP_RULES: str = (
        "public_read=120:40,auth=20:10,payment=30:10,webhook=120:60,"
        "admin=60:20,upload=20:5,default=180:60"
    )
    SECURITY_RATE_LIMIT_MAX_TRACKED_KEYS: int = 10000
    SECURITY_RATE_LIMIT_CLEANUP_INTERVAL_SECONDS: int = 60
    SECURITY_JSON_BODY_MAX_BYTES: int = 1048576
    SECURITY_UPLOAD_BODY_MAX_BYTES: int = 10485760
    SECURITY_TRUSTED_PROXY_CIDRS: str = "127.0.0.1/32,::1/128,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"
    STATS_EXPORT_UNIT_STAKE_RUB: int = 10000
    GOOGLE_DRIVE_STATS_ENABLED: bool = False
    GOOGLE_DRIVE_STATS_FOLDER_ID: str = ""
    GOOGLE_SERVICE_ACCOUNT_JSON_B64: str = ""

    @property
    def sales_manager_telegram_id(self) -> Optional[int]:
        return self.SALES_MANAGER_TELEGRAM_ID or self.OWNER_TELEGRAM_ID

    @property
    def async_database_url(self) -> str:
        # Async SQLAlchemy requires async drivers for request handlers.
        if self.DATABASE_URL.startswith("postgresql://"):
            return self.DATABASE_URL.replace("postgresql://", "postgresql+asyncpg://", 1)
        if self.DATABASE_URL.startswith("sqlite:///"):
            return self.DATABASE_URL.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        return self.DATABASE_URL

    @property
    def cors_allowed_origins(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.CORS_ALLOWED_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def has_real_telegram_token(self) -> bool:
        token = self.TELEGRAM_BOT_TOKEN.strip()
        return (
            bool(token)
            and ":" in token
            and not token.startswith("123456789:")
            and "CHANGE_ME" not in token.upper()
        )

    @property
    def has_yookassa_credentials(self) -> bool:
        return bool(self.YOOKASSA_SHOP_ID.strip() and self.YOOKASSA_SECRET_KEY.strip())

    @property
    def has_tegro_credentials(self) -> bool:
        return bool(self.TEGRO_SHOP_ID.strip() and self.TEGRO_SECRET_KEY.strip())

    @property
    def has_tegro_api_credentials(self) -> bool:
        return bool(
            self.TEGRO_SHOP_ID.strip()
            and self.TEGRO_API_KEY.strip()
            and self.TEGRO_SECRET_KEY.strip()
        )

    @property
    def has_ruble_payment_provider(self) -> bool:
        return self.has_yookassa_credentials or self.has_tegro_credentials

    @property
    def is_production(self) -> bool:
        return self.APP_ENV.strip().lower() in {"production", "prod"}

    @property
    def allow_debug_auth_bypass(self) -> bool:
        return (
            self.DEBUG_MODE
            and self.ALLOW_DEBUG_AUTH_BYPASS
            and not self.has_real_telegram_token
        )

    def validate_runtime_security(self) -> None:
        if self.is_production and self.DEBUG_MODE:
            raise RuntimeError("DEBUG_MODE must be false when APP_ENV=production")

        if self.has_real_telegram_token and self.DEBUG_MODE:
            raise RuntimeError("DEBUG_MODE must be false when a real Telegram bot token is configured")

        payment_providers_required = self.is_production

        if self.is_production and not self.has_real_telegram_token:
            raise RuntimeError("TELEGRAM_BOT_TOKEN must be set to a real bot token in production")

        if self.is_production and self.OWNER_TELEGRAM_ID is None:
            raise RuntimeError("OWNER_TELEGRAM_ID must be set in production")

        if self.is_production or self.has_real_telegram_token:
            secret = self.JWT_SECRET_KEY.strip()
            if secret == LOCAL_DEV_JWT_SECRET or len(secret) < 32:
                raise RuntimeError("JWT_SECRET_KEY must be set to a strong production secret")
            if not self.TELEGRAM_WEBHOOK_SECRET_TOKEN.strip():
                raise RuntimeError("TELEGRAM_WEBHOOK_SECRET_TOKEN must be set for Telegram webhook verification")

        if payment_providers_required and not self.has_ruble_payment_provider:
            raise RuntimeError("YooKassa or Tegro credentials must be set for production payments")

        if self.is_production and not (self.YOOKASSA_RETURN_URL.strip() or self.FRONTEND_BASE_URL.strip()):
            raise RuntimeError("YOOKASSA_RETURN_URL or FRONTEND_BASE_URL must be set in production")

        if self.is_production and self.SECURITY_RATE_LIMIT_MODE.strip().lower() != "enforce":
            raise RuntimeError("SECURITY_RATE_LIMIT_MODE must be enforce in production")

        if self.is_production and self.VK_GROUP_ID.strip():
            if not self.VK_CALLBACK_CONFIRMATION_CODE.strip():
                raise RuntimeError("VK_CALLBACK_CONFIRMATION_CODE must be set when VK callbacks are enabled in production")
            if not self.VK_CALLBACK_SECRET.strip():
                raise RuntimeError("VK_CALLBACK_SECRET must be set when VK callbacks are enabled in production")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
