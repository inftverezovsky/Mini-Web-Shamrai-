from __future__ import annotations

import contextvars
import json
import logging
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from src.core.config import settings

REQUEST_ID_HEADER = "X-Request-ID"
AUTH_COOKIE_NAME = "shamrai_access_token"

request_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("request_id", default=None)
user_id_context: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("user_id", default=None)

_RESERVED_LOG_RECORD_ATTRS = set(logging.makeLogRecord({}).__dict__)
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(token|secret|password|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|database[_-]?url)",
    re.IGNORECASE,
)
_KEY_VALUE_SECRET_PATTERN = re.compile(
    r"(?i)(token|secret|password|authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|database[_-]?url)=([^\s,;]+)"
)
_DATABASE_URL_PATTERN = re.compile(r"(?i)\b(postgres(?:ql)?|mysql|redis)://[^\s\"']+")
_TELEGRAM_TOKEN_PATTERN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{12,}\b")
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{12,}")


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            redacted[key_text] = "[redacted]" if _SENSITIVE_KEY_PATTERN.search(key_text) else redact_sensitive(item)
        return redacted
    if isinstance(value, (list, tuple, set)):
        return [redact_sensitive(item) for item in value]
    if not isinstance(value, str):
        return value

    redacted = _KEY_VALUE_SECRET_PATTERN.sub(lambda match: f"{match.group(1)}=[redacted]", value)
    redacted = _DATABASE_URL_PATTERN.sub(lambda match: f"{match.group(1)}://[redacted]", redacted)
    redacted = _TELEGRAM_TOKEN_PATTERN.sub("[telegram-token-redacted]", redacted)
    redacted = _BEARER_PATTERN.sub("Bearer [redacted]", redacted)
    return redacted


class JsonLogFormatter(logging.Formatter):
    """Small JSON formatter for Docker/stdout logs without third-party dependencies."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _utc_iso(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "message": redact_sensitive(record.getMessage()),
        }

        context_request_id = request_id_context.get()
        context_user_id = user_id_context.get()
        if context_request_id and not hasattr(record, "request_id"):
            payload["request_id"] = context_request_id
        if context_user_id and not hasattr(record, "user_id"):
            payload["user_id"] = context_user_id

        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_RECORD_ATTRS or key.startswith("_"):
                continue
            payload[str(key)] = redact_sensitive(value)

        if record.exc_info:
            payload["exception"] = redact_sensitive(self.formatException(record.exc_info))

        return json.dumps(payload, ensure_ascii=False, default=str, separators=(",", ":"))


def normalize_request_id(value: Optional[str]) -> str:
    raw_value = str(value or "").strip()
    if raw_value:
        try:
            return str(uuid.UUID(raw_value))
        except ValueError:
            pass
    return str(uuid.uuid4())


def extract_user_id_from_request(request: Request) -> Optional[str]:
    authorization = request.headers.get("authorization") or ""
    token = authorization[7:] if authorization.lower().startswith("bearer ") else authorization
    if not token:
        token = (getattr(request, "cookies", {}) or {}).get(AUTH_COOKIE_NAME) or ""
    if not token:
        return None

    try:
        from src.core.security import verify_access_token

        payload = verify_access_token(token)
    except Exception:
        return None
    if not payload or payload.get("sub") is None:
        return None
    return str(payload["sub"])


def _settings_value(name: str, fallback: Any) -> Any:
    return getattr(settings, name, fallback)


def configure_observability_logging() -> None:
    log_format = str(_settings_value("OBSERVABILITY_LOG_FORMAT", "json") or "json").strip().lower()
    formatter: logging.Formatter
    if log_format == "json":
        formatter = JsonLogFormatter()
    else:
        formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    file_handler: logging.Handler | None = None
    if not any(getattr(handler, "_shamrai_observability_stdout", False) for handler in root_logger.handlers):
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(formatter)
        stream_handler._shamrai_observability_stdout = True  # type: ignore[attr-defined]
        root_logger.addHandler(stream_handler)

    log_path = str(_settings_value("ADMIN_MONITORING_LOG_PATH", "") or "").strip()
    if log_path and not any(getattr(handler, "_shamrai_observability_file", "") == log_path for handler in root_logger.handlers):
        path = Path(log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler._shamrai_observability_file = log_path  # type: ignore[attr-defined]
        root_logger.addHandler(file_handler)
    elif log_path:
        file_handler = next(
            (
                handler
                for handler in root_logger.handlers
                if getattr(handler, "_shamrai_observability_file", "") == log_path
            ),
            None,
        )

    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        named_logger = logging.getLogger(logger_name)
        named_logger.setLevel(logging.INFO)
        for handler in named_logger.handlers:
            handler.setFormatter(formatter)
        if file_handler and not any(
            getattr(handler, "_shamrai_observability_file", "") == log_path
            for handler in named_logger.handlers
        ):
            named_logger.addHandler(file_handler)
        named_logger.propagate = not bool(named_logger.handlers)


class RequestObservabilityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        logger = logging.getLogger("uvicorn")
        request_id = normalize_request_id(request.headers.get(REQUEST_ID_HEADER))
        user_id = extract_user_id_from_request(request)
        request_token = request_id_context.set(request_id)
        user_token = user_id_context.set(user_id)
        started_at = time.perf_counter()
        status_code = 500
        five_xx_recorded = False

        try:
            response = await call_next(request)
            status_code = int(getattr(response, "status_code", 500))
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        except Exception:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            try:
                from src.services.observability_alerts import record_http_5xx

                record_http_5xx(status_code=500)
                five_xx_recorded = True
            except Exception:
                pass
            logger.exception(
                "http_request_failed",
                extra={
                    "event": "http_request",
                    "request_id": request_id,
                    "user_id": user_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "duration_ms": duration_ms,
                },
            )
            raise
        finally:
            duration_ms = round((time.perf_counter() - started_at) * 1000)
            if status_code >= 500 and not five_xx_recorded:
                try:
                    from src.services.observability_alerts import record_http_5xx

                    record_http_5xx(status_code=status_code)
                except Exception:
                    pass
            if "response" in locals():
                logger.log(
                    logging.WARNING if status_code >= 500 else logging.INFO,
                    "http_request",
                    extra={
                        "event": "http_request",
                        "request_id": request_id,
                        "user_id": user_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            request_id_context.reset(request_token)
            user_id_context.reset(user_token)
