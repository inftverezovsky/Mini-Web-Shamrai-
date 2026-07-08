from __future__ import annotations

import base64
import hmac
import secrets
import time
from hashlib import sha256
from typing import Optional
from urllib.parse import urlparse

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from src.core.config import settings

AUTH_COOKIE_NAME = "shamrai_access_token"
CSRF_COOKIE_NAME = "shamrai_csrf_token"
CSRF_HEADER_NAME = "X-CSRF-Token"
CSRF_TOKEN_TTL_SECONDS = 30 * 24 * 60 * 60
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

CSRF_EXEMPT_PATHS = {
    "/api/auth/login",
    "/api/auth/telegram-widget",
    "/api/payments/telegram-webhook",
    "/api/payments/tegro/webhook",
    "/api/payments/yookassa/webhook",
    "/api/telegram/webhook",
    "/api/vk/callback",
}


def _csrf_cookie_secure() -> bool:
    return settings.is_production or settings.FRONTEND_BASE_URL.startswith("https://")


def _csrf_cookie_samesite() -> str:
    return "none" if _csrf_cookie_secure() else "lax"


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_csrf_payload(payload: str) -> str:
    secret = settings.JWT_SECRET_KEY.encode("utf-8")
    return _base64url(hmac.new(secret, payload.encode("utf-8"), sha256).digest())


def create_csrf_token(*, now: Optional[int] = None) -> str:
    issued_at = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(32)
    payload = f"{nonce}.{issued_at}"
    signature = _sign_csrf_payload(payload)
    return f"{payload}.{signature}"


def verify_csrf_token(token: str | None, *, now: Optional[int] = None) -> bool:
    if not token:
        return False

    try:
        nonce, raw_issued_at, signature = token.split(".", 2)
        issued_at = int(raw_issued_at)
    except (TypeError, ValueError):
        return False

    if not nonce or not signature:
        return False

    current_time = int(time.time() if now is None else now)
    if issued_at > current_time + 60:
        return False
    if current_time - issued_at > CSRF_TOKEN_TTL_SECONDS:
        return False

    expected_signature = _sign_csrf_payload(f"{nonce}.{issued_at}")
    return hmac.compare_digest(signature, expected_signature)


def set_csrf_cookie(response: Response, token: str | None = None) -> str:
    csrf_token = token or create_csrf_token()
    response.set_cookie(
        CSRF_COOKIE_NAME,
        csrf_token,
        max_age=CSRF_TOKEN_TTL_SECONDS,
        httponly=True,
        secure=_csrf_cookie_secure(),
        samesite=_csrf_cookie_samesite(),
        path="/api",
    )
    return csrf_token


def clear_csrf_cookie(response: Response) -> None:
    response.delete_cookie(
        CSRF_COOKIE_NAME,
        path="/api",
        secure=_csrf_cookie_secure(),
        httponly=True,
        samesite=_csrf_cookie_samesite(),
    )


def _header_value(request: Request, name: str) -> str | None:
    headers = getattr(request, "headers", {}) or {}
    value = headers.get(name)
    if value is None:
        value = headers.get(name.lower())
    return value


def _normalize_origin(value: str | None) -> str | None:
    raw_value = (value or "").strip()
    if not raw_value or raw_value == "null":
        return None

    try:
        parsed = urlparse(raw_value)
    except ValueError:
        return None

    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None

    host = parsed.hostname.lower()
    try:
        port = parsed.port
    except ValueError:
        return None
    default_port = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    if port and not default_port:
        host = f"{host}:{port}"
    return f"{scheme}://{host}"


def _allowed_csrf_origins() -> set[str]:
    candidates = [
        *settings.cors_allowed_origins,
        settings.FRONTEND_BASE_URL,
        settings.API_BASE_URL,
    ]
    return {
        origin
        for origin in (_normalize_origin(candidate) for candidate in candidates if candidate and candidate != "*")
        if origin
    }


def _request_origin_allowed(request: Request) -> bool:
    origin_header = _header_value(request, "Origin")
    referer_header = _header_value(request, "Referer")
    allowed_origins = _allowed_csrf_origins()

    if origin_header:
        origin = _normalize_origin(origin_header)
        return bool(origin and origin in allowed_origins)

    if referer_header:
        referer_origin = _normalize_origin(referer_header)
        return bool(referer_origin and referer_origin in allowed_origins)

    # Some same-origin clients and tests cannot provide Origin/Referer. The signed
    # double-submit CSRF token remains mandatory when these headers are absent.
    return True


def should_check_csrf(request: Request) -> bool:
    method = request.method.upper()
    path = request.url.path
    if method == "OPTIONS" or method not in UNSAFE_METHODS:
        return False
    if not path.startswith("/api/") or path in CSRF_EXEMPT_PATHS:
        return False
    return bool((getattr(request, "cookies", {}) or {}).get(AUTH_COOKIE_NAME))


def validate_csrf_request(request: Request) -> bool:
    header_token = request.headers.get(CSRF_HEADER_NAME)
    cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
    if not header_token or not cookie_token:
        return False
    if not hmac.compare_digest(header_token, cookie_token):
        return False
    return verify_csrf_token(header_token) and _request_origin_allowed(request)


class CsrfProtectionMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if should_check_csrf(request) and not validate_csrf_request(request):
            return JSONResponse(
                {"detail": "Недействительный CSRF токен."},
                status_code=403,
            )

        return await call_next(request)
