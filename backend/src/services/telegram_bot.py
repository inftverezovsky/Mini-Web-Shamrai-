import asyncio
import json
import logging
import mimetypes
import secrets
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from src.core.config import settings

logger = logging.getLogger("uvicorn")


def call_telegram_api(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    """Perform a synchronous POST call to the Telegram Bot API."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    headers = {"Content-Type": "application/json"}
    req_body = json.dumps(payload).encode("utf-8")
    opener = urllib.request.build_opener()
    if settings.HTTPS_PROXY.strip():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({
                "http": settings.HTTPS_PROXY.strip(),
                "https": settings.HTTPS_PROXY.strip(),
            })
        )

    retry_count = settings.TELEGRAM_API_RETRIES if retries is None else retries
    attempts = max(1, int(retry_count or 0) + 1)
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_description = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
            with opener.open(req, timeout=request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                error_payload = json.loads(e.read().decode("utf-8"))
                description = error_payload.get("description") or f"HTTP {e.code}"
            except Exception:
                description = f"HTTP {e.code}: {e.reason}"
            logger.warning(
                "telegram_api_call_failed",
                extra={
                    "event": "telegram_api_call_failed",
                    "telegram_method": method,
                    "description": description,
                },
            )
            return {"ok": False, "description": description}
        except Exception as e:
            last_description = str(e)
            if attempt < attempts:
                logger.warning(
                    "telegram_api_call_retrying",
                    extra={
                        "event": "telegram_api_call_retrying",
                        "telegram_method": method,
                        "attempt": attempt,
                        "attempts": attempts,
                        "error_type": type(e).__name__,
                    },
                )
                time.sleep(min(0.8 * attempt, 2.0))
                continue
            logger.warning(
                "telegram_api_call_failed",
                extra={
                    "event": "telegram_api_call_failed",
                    "telegram_method": method,
                    "error_type": type(e).__name__,
                },
            )
            return {"ok": False, "description": last_description}

    return {"ok": False, "description": last_description}


async def call_telegram_api_async(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    return await asyncio.to_thread(call_telegram_api, method, payload, timeout, retries)


def run_telegram_api_background(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> None:
    task = asyncio.create_task(call_telegram_api_async(method, payload, timeout, retries))

    def _log_failure(done_task: asyncio.Task) -> None:
        try:
            result = done_task.result()
            if not result.get("ok"):
                logger.info(
                    "[Telegram] Background %s failed: %s",
                    method,
                    result.get("description", "unknown error"),
                )
        except Exception as exc:
            logger.exception("[Telegram] Background %s crashed: %s", method, exc)

    task.add_done_callback(_log_failure)


def _telegram_multipart_field_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def call_telegram_api_multipart(
    method: str,
    payload: dict,
    files: dict[str, tuple[str, bytes, str]],
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    """Perform a synchronous multipart/form-data call to the Telegram Bot API."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

    url = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}"
    boundary = f"----shamrai-telegram-{secrets.token_hex(16)}"
    body_parts: list[bytes] = []

    for key, value in payload.items():
        if value is None:
            continue
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("utf-8"),
            _telegram_multipart_field_value(value).encode("utf-8"),
            b"\r\n",
        ])

    for field_name, (filename, contents, content_type) in files.items():
        clean_filename = filename or field_name
        guessed_type = content_type or mimetypes.guess_type(clean_filename)[0] or "application/octet-stream"
        body_parts.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{field_name}"; '
                f'filename="{clean_filename}"\r\n'
            ).encode("utf-8"),
            f"Content-Type: {guessed_type}\r\n\r\n".encode("utf-8"),
            contents,
            b"\r\n",
        ])

    body_parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    req_body = b"".join(body_parts)
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}

    opener = urllib.request.build_opener()
    if settings.HTTPS_PROXY.strip():
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({
                "http": settings.HTTPS_PROXY.strip(),
                "https": settings.HTTPS_PROXY.strip(),
            })
        )

    retry_count = settings.TELEGRAM_API_RETRIES if retries is None else retries
    attempts = max(1, int(retry_count or 0) + 1)
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_description = "unknown error"

    for attempt in range(1, attempts + 1):
        try:
            req = urllib.request.Request(url, data=req_body, headers=headers, method="POST")
            with opener.open(req, timeout=request_timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                error_payload = json.loads(e.read().decode("utf-8"))
                description = error_payload.get("description") or f"HTTP {e.code}"
            except Exception:
                description = f"HTTP {e.code}: {e.reason}"
            logger.warning(
                "telegram_api_multipart_call_failed",
                extra={
                    "event": "telegram_api_multipart_call_failed",
                    "telegram_method": method,
                    "description": description,
                },
            )
            return {"ok": False, "description": description}
        except Exception as e:
            last_description = str(e)
            if attempt < attempts:
                logger.warning(
                    "telegram_api_multipart_call_retrying",
                    extra={
                        "event": "telegram_api_multipart_call_retrying",
                        "telegram_method": method,
                        "attempt": attempt,
                        "attempts": attempts,
                        "error_type": type(e).__name__,
                    },
                )
                time.sleep(min(0.8 * attempt, 2.0))
                continue
            logger.warning(
                "telegram_api_multipart_call_failed",
                extra={
                    "event": "telegram_api_multipart_call_failed",
                    "telegram_method": method,
                    "error_type": type(e).__name__,
                },
            )
            return {"ok": False, "description": last_description}

    return {"ok": False, "description": last_description}
