import hmac
import hashlib
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from typing import Optional
import jwt
from fastapi import HTTPException, status
from src.core.config import settings

# JWT configuration settings
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_DAYS = 30
TELEGRAM_LOGIN_WIDGET_SIGNED_FIELDS = {
    "id",
    "first_name",
    "last_name",
    "username",
    "phone",
    "phone_number",
    "photo_url",
    "auth_date",
}

def create_access_token(data: dict) -> str:
    """Generates a secure JWT token for authenticated requests."""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(days=ACCESS_TOKEN_EXPIRE_DAYS)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def verify_access_token(token: str) -> Optional[dict]:
    """Decodes and validates a JWT token. Returns payload or None."""
    try:
        payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def verify_telegram_webhook_secret(secret_token: Optional[str]) -> None:
    """Validate Telegram's X-Telegram-Bot-Api-Secret-Token header when configured."""
    expected = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN.strip()
    if not expected:
        return

    if not secret_token or not hmac.compare_digest(secret_token, expected):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram webhook secret validation failed",
        )

def verify_telegram_init_data(init_data: str) -> dict:
    """
    Utility function to verify Telegram WebApp initData.
    Accepts raw query string and returns parsed user dict.
    """
    # Debug bypass for local desktop browser testing
    if settings.allow_debug_auth_bypass:
        if init_data == "mock_debug_user":
            return {
                "id": 123456789,
                "username": "debug_user",
                "first_name": "Иван",
                "last_name": "Подписчик",
                "role": "user",
                "_debug_mock": True,
            }
        elif init_data == "mock_debug_admin":
            return {
                "id": 987654321,
                "username": "debug_admin",
                "first_name": "Алексей",
                "last_name": "Админ",
                "role": "admin",
                "_debug_mock": True,
            }
        elif init_data.startswith("mock_debug_user_ref_"):
            try:
                referrer_id = int(init_data.removeprefix("mock_debug_user_ref_"))
            except ValueError:
                referrer_id = None
            return {
                "id": 123456780,
                "username": "debug_referral_user",
                "first_name": "Иван",
                "last_name": "Реферал",
                "role": "user",
                "start_param": f"ref_{referrer_id}" if referrer_id else None,
                "_debug_mock": True,
            }

    try:
        parsed_data = dict(urllib.parse.parse_qsl(init_data))
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid initialization data format"
        )

    if "hash" not in parsed_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Security hash missing"
        )

    received_hash = parsed_data.pop("hash")
    
    # Sort keys alphabetically and compile data check string
    sorted_items = sorted(parsed_data.items())
    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted_items)

    # WebAppData HMAC-SHA256 signature chain validation
    secret_key = hmac.new(
        b"WebAppData",
        settings.TELEGRAM_BOT_TOKEN.encode(),
        hashlib.sha256
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram credentials validation failed"
        )

    # Verify expiration (24 hours standard window)
    try:
        auth_date = int(parsed_data.get("auth_date", 0))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization timestamp is invalid"
        )
    now = datetime.now(timezone.utc).timestamp()
    if now - auth_date > 86400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization session expired"
        )

    user_str = parsed_data.get("user")
    if not user_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User details missing in Telegram payload"
        )

    try:
        user_data = json.loads(user_str)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Failed to parse user JSON payload"
        )

    if parsed_data.get("start_param"):
        user_data["start_param"] = parsed_data["start_param"]

    return user_data


def verify_telegram_login_widget(payload: dict) -> dict:
    """
    Validate Telegram Login Widget payload and return normalized user data.

    The browser Login Widget uses a different HMAC key from Mini App initData:
    sha256(bot_token) is used directly as the secret key.
    """
    if settings.allow_debug_auth_bypass and payload.get("_debug_mock"):
        return {
            "id": int(payload.get("id") or 123456789),
            "username": payload.get("username") or "debug_user",
            "first_name": payload.get("first_name") or "Иван",
            "last_name": payload.get("last_name") or "Подписчик",
            "role": payload.get("role") or "user",
            "_debug_mock": True,
        }

    received_hash = str(payload.get("hash") or "")
    if not received_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram Login Widget security hash missing",
        )

    cleaned_payload = {
        key: value
        for key, value in payload.items()
        if key in TELEGRAM_LOGIN_WIDGET_SIGNED_FIELDS and value is not None and value != ""
    }
    data_check_string = "\n".join(
        f"{key}={cleaned_payload[key]}" for key in sorted(cleaned_payload.keys())
    )

    secret_key = hashlib.sha256(settings.TELEGRAM_BOT_TOKEN.encode()).digest()
    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram Login Widget credentials validation failed",
        )

    try:
        auth_date = int(cleaned_payload.get("auth_date", 0))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization timestamp is invalid",
        )

    now = datetime.now(timezone.utc).timestamp()
    if auth_date - now > 300:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization timestamp is invalid",
        )

    if now - auth_date > 86400:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram authorization session expired",
        )

    try:
        telegram_id = int(cleaned_payload["id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Telegram ID in Login Widget payload",
        )

    return {
        "id": telegram_id,
        "username": cleaned_payload.get("username"),
        "first_name": cleaned_payload.get("first_name"),
        "last_name": cleaned_payload.get("last_name"),
        "phone": cleaned_payload.get("phone") or cleaned_payload.get("phone_number"),
        "photo_url": cleaned_payload.get("photo_url"),
    }
