import asyncio
import errno
import json
import logging
import mimetypes
import re
import secrets
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from src.core.config import settings

logger = logging.getLogger("uvicorn")

_proxy_state_lock = threading.Lock()
_preferred_telegram_proxy_index = 0
_URL_USERINFO_PATTERN = re.compile(r"([a-z][a-z0-9+.-]*://)[^/@\s]+@", re.IGNORECASE)
_SAFE_PRECONNECT_ERRNOS = {
    errno.ECONNREFUSED,
    errno.ENETUNREACH,
    errno.EHOSTUNREACH,
    errno.EADDRNOTAVAIL,
}


def _redact_url_userinfo(value: str) -> str:
    return _URL_USERINFO_PATTERN.sub(r"\1[credentials-redacted]@", value)


def _safe_network_description(error: BaseException) -> str:
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    error_number = getattr(reason, "errno", None)
    if isinstance(error_number, int):
        return f"Telegram network error (errno {error_number})"
    return f"Telegram network error ({type(reason).__name__})"


def _is_safe_preconnect_failure(error: BaseException) -> bool:
    if isinstance(error, urllib.error.HTTPError):
        return error.code == 407
    reason = error.reason if isinstance(error, urllib.error.URLError) else error
    if isinstance(reason, (ConnectionRefusedError, socket.gaierror)):
        return True
    return isinstance(reason, OSError) and getattr(reason, "errno", None) in _SAFE_PRECONNECT_ERRNOS


def _ordered_proxy_candidates() -> tuple[Optional[str], ...]:
    proxy_urls = settings.telegram_proxy_urls
    if not proxy_urls:
        return (None,)
    with _proxy_state_lock:
        start_index = _preferred_telegram_proxy_index % len(proxy_urls)
    return proxy_urls[start_index:] + proxy_urls[:start_index]


def _mark_proxy_available(proxy_url: Optional[str]) -> None:
    if proxy_url is None:
        return
    proxy_urls = settings.telegram_proxy_urls
    try:
        next_index = proxy_urls.index(proxy_url)
    except ValueError:
        return
    global _preferred_telegram_proxy_index
    with _proxy_state_lock:
        _preferred_telegram_proxy_index = next_index


def _build_telegram_opener(proxy_url: Optional[str]):
    proxy_mapping = {} if proxy_url is None else {"http": proxy_url, "https": proxy_url}
    return urllib.request.build_opener(urllib.request.ProxyHandler(proxy_mapping))


def _telegram_http_error_result(error: urllib.error.HTTPError) -> dict:
    error_payload: dict = {}
    try:
        error_payload = json.loads(error.read().decode("utf-8"))
        if not isinstance(error_payload, dict):
            error_payload = {}
        description = error_payload.get("description") or f"HTTP {error.code}"
    except Exception:
        description = f"HTTP {error.code}"
    result = {
        "ok": False,
        "description": _redact_url_userinfo(str(description)),
        "http_status": int(error.code),
    }
    error_code = error_payload.get("error_code")
    if isinstance(error_code, int) and not isinstance(error_code, bool):
        result["error_code"] = error_code
    return result


def _perform_telegram_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str],
    request_body: bytes,
    timeout: Optional[float],
    retries: Optional[int],
    event_prefix: str,
) -> dict:
    candidates = _ordered_proxy_candidates()
    retry_count = settings.TELEGRAM_API_RETRIES if retries is None else retries
    attempt_count = max(1, int(retry_count or 0) + 1, len(candidates))
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_description = "Telegram network error"

    for attempt_index in range(attempt_count):
        proxy_url = candidates[attempt_index % len(candidates)]
        try:
            request = urllib.request.Request(url, data=request_body, headers=headers, method="POST")
            with _build_telegram_opener(proxy_url).open(request, timeout=request_timeout) as response:
                result = json.loads(response.read().decode("utf-8"))
            _mark_proxy_available(proxy_url)
            if not isinstance(result, dict):
                return {"ok": False, "description": "Telegram API returned an invalid response"}
            return result
        except urllib.error.HTTPError as error:
            error_result = _telegram_http_error_result(error)
            if error.code == 407:
                error_result["description"] = "Telegram proxy authentication failed"
            last_description = error_result["description"]
            can_fail_over = (
                _is_safe_preconnect_failure(error)
                and attempt_index + 1 < attempt_count
            )
            if can_fail_over:
                retry_event = (
                    f"{event_prefix}_proxy_failover"
                    if proxy_url is not None
                    else f"{event_prefix}_retrying"
                )
                logger.warning(
                    retry_event,
                    extra={
                        "event": retry_event,
                        "telegram_method": method,
                        "attempt": attempt_index + 1,
                        "attempts": attempt_count,
                        "proxy_count": len(settings.telegram_proxy_urls),
                        "error_type": type(error).__name__,
                    },
                )
                time.sleep(min(0.2 * (attempt_index + 1), 1.0))
                continue
            if error.code != 407:
                _mark_proxy_available(proxy_url)
            logger.warning(
                f"{event_prefix}_failed",
                extra={
                    "event": f"{event_prefix}_failed",
                    "telegram_method": method,
                    "description": last_description,
                },
            )
            return error_result
        except Exception as error:
            last_description = _safe_network_description(error)
            can_fail_over = (
                _is_safe_preconnect_failure(error)
                and attempt_index + 1 < attempt_count
            )
            if can_fail_over:
                retry_event = (
                    f"{event_prefix}_proxy_failover"
                    if proxy_url is not None
                    else f"{event_prefix}_retrying"
                )
                logger.warning(
                    retry_event,
                    extra={
                        "event": retry_event,
                        "telegram_method": method,
                        "attempt": attempt_index + 1,
                        "attempts": attempt_count,
                        "proxy_count": len(settings.telegram_proxy_urls),
                        "error_type": type(error).__name__,
                    },
                )
                time.sleep(min(0.2 * (attempt_index + 1), 1.0))
                continue
            logger.warning(
                f"{event_prefix}_failed",
                extra={
                    "event": f"{event_prefix}_failed",
                    "telegram_method": method,
                    "error_type": type(error).__name__,
                },
            )
            return {"ok": False, "description": last_description}

    return {"ok": False, "description": last_description}


def call_telegram_api(
    method: str,
    payload: dict,
    timeout: Optional[float] = None,
    retries: Optional[int] = None,
) -> dict:
    """Perform a synchronous JSON call to Telegram with safe proxy failover."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

    return _perform_telegram_request(
        method=method,
        url=f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
        headers={"Content-Type": "application/json"},
        request_body=json.dumps(payload).encode("utf-8"),
        timeout=timeout,
        retries=retries,
        event_prefix="telegram_api_call",
    )


def download_telegram_file(
    file_id: str,
    *,
    max_bytes: int = 2 * 1024 * 1024,
    timeout: Optional[float] = None,
) -> tuple[bytes, str]:
    """Download a Telegram file without exposing the bot token or remote file path."""
    file_response = call_telegram_api(
        "getFile",
        {"file_id": str(file_id or "").strip()},
        timeout=timeout,
    )
    if not file_response.get("ok"):
        raise RuntimeError(str(file_response.get("description") or "Telegram getFile failed"))
    file_path = str((file_response.get("result") or {}).get("file_path") or "").strip()
    if not file_path or ".." in file_path or file_path.startswith(("/", "\\")):
        raise RuntimeError("Telegram returned an invalid file path")

    candidates = _ordered_proxy_candidates()
    attempt_count = max(1, len(candidates))
    request_timeout = timeout or settings.TELEGRAM_API_TIMEOUT_SECONDS
    last_error: BaseException | None = None
    url = f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"

    for attempt_index in range(attempt_count):
        proxy_url = candidates[attempt_index % len(candidates)]
        try:
            request = urllib.request.Request(url, method="GET")
            with _build_telegram_opener(proxy_url).open(request, timeout=request_timeout) as response:
                declared_length = int(response.headers.get("Content-Length") or 0)
                if declared_length > max_bytes:
                    raise RuntimeError("Telegram file is too large")
                contents = response.read(max_bytes + 1)
                if len(contents) > max_bytes:
                    raise RuntimeError("Telegram file is too large")
                content_type = str(
                    response.headers.get_content_type()
                    or mimetypes.guess_type(file_path)[0]
                    or "application/octet-stream"
                )
            _mark_proxy_available(proxy_url)
            return contents, content_type
        except Exception as error:
            last_error = error
            if _is_safe_preconnect_failure(error) and attempt_index + 1 < attempt_count:
                continue
            break
    raise RuntimeError(_safe_network_description(last_error or RuntimeError("download failed")))


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
            logger.exception("[Telegram] Background %s crashed: %s", method, type(exc).__name__)

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
    """Perform a multipart Telegram call with safe proxy failover."""
    if not settings.has_real_telegram_token:
        return {"ok": False, "description": "Telegram bot token is not configured"}

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

    return _perform_telegram_request(
        method=method,
        url=f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}/{method}",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        request_body=b"".join(body_parts),
        timeout=timeout,
        retries=retries,
        event_prefix="telegram_api_multipart_call",
    )
