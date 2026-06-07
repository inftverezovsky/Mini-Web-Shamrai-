from typing import Optional
from pydantic_settings import BaseSettings

LOCAL_DEV_JWT_SECRET = "BET_TMA_LOCAL_DEV_SECRET_CHANGE_ME"

class Settings(BaseSettings):
    APP_ENV: str = "local"
    DATABASE_URL: str = "postgresql://postgres:postgres@localhost:5432/shamrai"
    TELEGRAM_BOT_TOKEN: str = ""
    OWNER_TELEGRAM_ID: Optional[int] = None
    DEBUG_MODE: bool = False
    ALLOW_DEBUG_AUTH_BYPASS: bool = False
    JWT_SECRET_KEY: str = LOCAL_DEV_JWT_SECRET
    CORS_ALLOWED_ORIGINS: str = "http://localhost:8082,http://127.0.0.1:8082"
    ENABLE_BACKGROUND_TASKS: bool = False
    TELEGRAM_VIP_CHAT_ID: str = "-100200300400"
    TELEGRAM_WEBHOOK_SECRET_TOKEN: str = ""
    TELEGRAM_SHAMRAI_CUSTOM_EMOJI_ID: str = ""
    TELEGRAM_WRITE_CUSTOM_EMOJI_ID: str = ""
    TELEGRAM_BOOKMAKER_CUSTOM_EMOJI_IDS: str = ""
    TELEGRAM_SPORT_CUSTOM_EMOJI_IDS: str = ""
    TELEGRAM_API_TIMEOUT_SECONDS: float = 3.0
    TELEGRAM_API_RETRIES: int = 2
    TELEGRAM_BROADCAST_CONCURRENCY: int = 12
    TELEGRAM_USE_POLLING: bool = False
    SALES_MANAGER_TELEGRAM_ID: Optional[int] = None
    API_BASE_URL: str = "http://localhost:8000"
    FRONTEND_BASE_URL: str = "http://localhost:8082"
    YOOKASSA_SHOP_ID: str = ""
    YOOKASSA_SECRET_KEY: str = ""
    YOOKASSA_RETURN_URL: str = ""
    HTTPS_PROXY: str = ""

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

        if payment_providers_required and not self.has_yookassa_credentials:
            raise RuntimeError("YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY must be set for production payments")

        if self.is_production and not (self.YOOKASSA_RETURN_URL.strip() or self.FRONTEND_BASE_URL.strip()):
            raise RuntimeError("YOOKASSA_RETURN_URL or FRONTEND_BASE_URL must be set in production")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()
