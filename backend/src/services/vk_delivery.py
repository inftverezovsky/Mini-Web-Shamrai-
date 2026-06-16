import asyncio
import html
import json
import logging
import mimetypes
import os
import random
import re
import secrets
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Optional
from uuid import UUID

from src.core.config import settings
from src.core.telegram_text import SHAMRAI_CONTACT_URL, SHAMRAI_CONTACT_USERNAME

logger = logging.getLogger("uvicorn")

VK_API_BASE = "https://api.vk.com/method"
VK_RANDOM_ID_MIN = 1
VK_RANDOM_ID_MAX = 2_147_483_647
STATIC_ROOT = os.path.abspath(
    os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static")
)


def _vk_urlopen(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def vk_group_id() -> Optional[int]:
    raw_group_id = str(settings.VK_GROUP_ID or "").strip()
    if not raw_group_id:
        return None
    try:
        group_id = abs(int(raw_group_id))
    except ValueError:
        return None
    return group_id or None


def vk_delivery_configured() -> bool:
    return bool(vk_group_id() and settings.VK_GROUP_ACCESS_TOKEN.strip())


def _mask_secret(value: Optional[str]) -> str:
    token = str(value or "").strip()
    if not token:
        return "<empty>"
    if len(token) <= 10:
        return f"{token[:2]}{'*' * max(1, len(token) - 4)}{token[-2:]}"
    return f"{token[:6]}{'*' * 10}{token[-4:]}"


def log_vk_runtime_config() -> None:
    token = settings.VK_GROUP_ACCESS_TOKEN.strip()
    logger.info(
        "[VKDelivery] VK_GROUP_ACCESS_TOKEN initialized=%s masked=%s group_id=%s api_version=%s configured=%s",
        bool(token),
        _mask_secret(token),
        vk_group_id(),
        settings.VK_API_VERSION.strip() or "5.199",
        vk_delivery_configured(),
    )


def generate_vk_random_id() -> int:
    return random.randint(VK_RANDOM_ID_MIN, VK_RANDOM_ID_MAX)


def _vk_broadcast_concurrency() -> int:
    try:
        value = int(settings.VK_BROADCAST_CONCURRENCY or 1)
    except Exception:
        value = 1
    return max(1, min(value, 25))


def _normalize_vk_user_id(value: Any) -> Optional[int]:
    try:
        user_id = int(str(value or "").strip())
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def user_can_receive_vk_messages(user: Any) -> bool:
    return bool(
        _normalize_vk_user_id(getattr(user, "vk_user_id", None))
        and getattr(user, "vk_messages_allowed", False)
    )


def is_vk_message_permission_error(result: dict) -> bool:
    error = result.get("error") or {}
    code = error.get("error_code") or error.get("code")
    description = str(result.get("description") or error.get("error_msg") or "").lower()
    return (
        code in {901, 902}
        or "can't send messages" in description
        or "cannot send messages" in description
        or "messages are denied" in description
        or "no permission" in description
        or ("permission" in description and "message" in description)
        or "нельзя отправлять" in description
        or "сообщения запрещ" in description
        or "запретил сообщения" in description
    )


def _vk_error_code(result: dict) -> Optional[int]:
    error = result.get("error") or {}
    code = error.get("error_code") or error.get("code")
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def is_vk_chat_bot_feature_error(result: dict) -> bool:
    error = result.get("error") or {}
    description = str(result.get("description") or error.get("error_msg") or "").lower()
    return _vk_error_code(result) == 912 or "chat bot feature" in description


async def refresh_vk_delivery_status(
    db: Any,
    user: Any,
    *,
    refresh_group: bool = True,
    commit: bool = False,
) -> dict[str, Any]:
    vk_user_id = getattr(user, "vk_user_id", None)
    messages_allowed = bool(getattr(user, "vk_messages_allowed", False))
    group_member = bool(getattr(user, "vk_group_member", False))
    changed = False

    remote_allowed = await asyncio.to_thread(check_vk_messages_allowed, vk_user_id)
    if remote_allowed is not None and remote_allowed != messages_allowed:
        setattr(user, "vk_messages_allowed", bool(remote_allowed))
        messages_allowed = bool(remote_allowed)
        changed = True

    remote_group_member = None
    if refresh_group:
        remote_group_member = await asyncio.to_thread(check_vk_group_member, vk_user_id)
        if remote_group_member is not None and remote_group_member != group_member:
            setattr(user, "vk_group_member", bool(remote_group_member))
            group_member = bool(remote_group_member)
            changed = True

    if changed and commit and db is not None:
        await db.commit()

    return {
        "changed": changed,
        "configured": vk_delivery_configured(),
        "vk_user_id": vk_user_id,
        "group_id": vk_group_id(),
        "messages_allowed": messages_allowed,
        "group_member": group_member,
        "remote_messages_checked": remote_allowed is not None,
        "remote_group_checked": remote_group_member is not None,
    }


async def mark_vk_messages_denied(db: Any, user: Any, *, commit: bool = False) -> None:
    if getattr(user, "vk_messages_allowed", False):
        setattr(user, "vk_messages_allowed", False)
        if commit and db is not None:
            await db.commit()


def html_to_vk_text(value: str) -> str:
    text = str(value or "")

    def replace_anchor(match: re.Match) -> str:
        href = html.unescape(match.group(1)).strip()
        label = html.unescape(re.sub(r"<[^>]+>", "", match.group(2))).strip()
        if href == SHAMRAI_CONTACT_URL and label == SHAMRAI_CONTACT_USERNAME:
            return f"[{href}|{label}]"
        return f"{label}: {href}"

    text = re.sub(
        r"<a\b[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
        replace_anchor,
        text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _vk_api_request(
    method: str,
    params: dict[str, Any],
    *,
    timeout: float = 8.0,
    log_response: bool = True,
) -> dict:
    if not vk_delivery_configured():
        return {"ok": False, "description": "VK group token is not configured"}

    payload = {
        "access_token": settings.VK_GROUP_ACCESS_TOKEN.strip(),
        "v": settings.VK_API_VERSION.strip() or "5.199",
        **{
            key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
            for key, value in params.items()
            if value is not None
        },
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{VK_API_BASE}/{method}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with _vk_urlopen(request, timeout=timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
            if log_response:
                logger.info("VK Response: %s", result)
            else:
                logger.info(
                    "VK Response: method=%s ok=%s has_error=%s",
                    method,
                    not bool(result.get("error")),
                    bool(result.get("error")),
                )
    except urllib.error.HTTPError as error:
        try:
            result = json.loads(error.read().decode("utf-8"))
            if log_response:
                logger.info("VK Response: %s", result)
            else:
                logger.info(
                    "VK Response: method=%s ok=%s has_error=%s",
                    method,
                    not bool(result.get("error")),
                    bool(result.get("error")),
                )
        except Exception:
            logger.warning("[VKDelivery] VK HTTP %s for %s: %s", error.code, method, error.reason)
            return {"ok": False, "description": f"VK HTTP {error.code}: {error.reason}"}
    except Exception as error:
        logger.exception("[VKDelivery] VK request failed for %s", method)
        return {"ok": False, "description": str(error)}

    if result.get("error"):
        error_info = result["error"]
        description = error_info.get("error_msg") or error_info.get("error_text") or "VK API error"
        logger.warning(
            "[VKDelivery] VK API error method=%s error_code=%s description=%s full_response=%s",
            method,
            error_info.get("error_code") or error_info.get("code"),
            description,
            result,
        )
        return {"ok": False, "description": description, "error": error_info}
    return {"ok": True, "response": result.get("response")}


def get_vk_unread_conversations(count: Optional[int] = None) -> dict:
    try:
        clean_count = int(count or settings.VK_DIALOG_POLLING_BATCH_SIZE or 20)
    except (TypeError, ValueError):
        clean_count = 20
    clean_count = max(1, min(clean_count, 200))
    return _vk_api_request(
        "messages.getConversations",
        {
            "count": clean_count,
            "filter": "unread",
        },
        timeout=8.0,
        log_response=False,
    )


def mark_vk_conversation_read(peer_id: Any) -> dict:
    try:
        clean_peer_id = int(str(peer_id or "").strip())
    except (TypeError, ValueError):
        return {"ok": False, "description": "VK peer_id is invalid"}
    if clean_peer_id <= 0:
        return {"ok": False, "description": "VK peer_id is invalid"}
    return _vk_api_request(
        "messages.markAsRead",
        {"peer_id": clean_peer_id},
        timeout=8.0,
        log_response=False,
    )


def _multipart_request(url: str, field_name: str, file_path: str, *, timeout: float = 20.0) -> dict:
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----shamrai-vk-{secrets.token_hex(16)}"
    with open(file_path, "rb") as file_obj:
        file_bytes = file_obj.read()

    body = b"".join(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            (
                f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode("utf-8"),
            file_bytes,
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with _vk_urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def local_static_asset_path(path: Optional[str]) -> Optional[str]:
    if not path or path.startswith(("http://", "https://")):
        return None

    clean_path = path.split("?", 1)[0].replace("\\", "/").lstrip("/")
    if not clean_path.startswith("static/"):
        return None

    relative_path = clean_path[len("static/"):]
    absolute_path = os.path.abspath(os.path.join(STATIC_ROOT, relative_path))
    if os.path.commonpath([STATIC_ROOT, absolute_path]) != STATIC_ROOT:
        return None
    return absolute_path if os.path.isfile(absolute_path) else None


def _upload_message_photo(vk_user_id: int, file_path: str) -> Optional[str]:
    upload_server = _vk_api_request("photos.getMessagesUploadServer", {"peer_id": vk_user_id})
    if not upload_server.get("ok"):
        logger.warning("[VKDelivery] Upload server failed for %s: %s", vk_user_id, upload_server.get("description"))
        return None

    upload_url = (upload_server.get("response") or {}).get("upload_url")
    if not upload_url:
        logger.warning("[VKDelivery] Upload server has no upload_url for %s", vk_user_id)
        return None

    try:
        uploaded = _multipart_request(upload_url, "photo", file_path)
    except Exception as exc:
        logger.warning("[VKDelivery] Photo upload failed for %s: %s", vk_user_id, exc)
        return None

    saved = _vk_api_request(
        "photos.saveMessagesPhoto",
        {
            "photo": uploaded.get("photo"),
            "server": uploaded.get("server"),
            "hash": uploaded.get("hash"),
        },
    )
    if not saved.get("ok"):
        logger.warning("[VKDelivery] Photo save failed for %s: %s", vk_user_id, saved.get("description"))
        return None

    photos = saved.get("response") or []
    if not photos:
        return None
    photo = photos[0]
    attachment = f"photo{photo.get('owner_id')}_{photo.get('id')}"
    if photo.get("access_key"):
        attachment += f"_{photo['access_key']}"
    return attachment


def check_vk_messages_allowed(vk_user_id: Any) -> Optional[bool]:
    normalized_user_id = _normalize_vk_user_id(vk_user_id)
    group_id = vk_group_id()
    if not normalized_user_id or not group_id or not settings.VK_GROUP_ACCESS_TOKEN.strip():
        return None
    result = _vk_api_request(
        "messages.isMessagesFromGroupAllowed",
        {"group_id": group_id, "user_id": normalized_user_id},
    )
    if not result.get("ok"):
        return None
    response = result.get("response") or {}
    return bool(response.get("is_allowed"))


def check_vk_group_member(vk_user_id: Any) -> Optional[bool]:
    normalized_user_id = _normalize_vk_user_id(vk_user_id)
    group_id = vk_group_id()
    if not normalized_user_id or not group_id or not settings.VK_GROUP_ACCESS_TOKEN.strip():
        return None
    result = _vk_api_request(
        "groups.isMember",
        {"group_id": group_id, "user_id": normalized_user_id},
    )
    if not result.get("ok"):
        return None
    response = result.get("response")
    if isinstance(response, dict):
        response = response.get("member") or response.get("is_member")
    return bool(response)


def probe_vk_api() -> bool:
    group_id = vk_group_id()
    if not group_id or not settings.VK_GROUP_ACCESS_TOKEN.strip():
        return False
    result = _vk_api_request(
        "groups.isMember",
        {"group_id": group_id, "user_id": 1},
        timeout=5.0,
    )
    if result.get("ok"):
        return True
    fallback = _vk_api_request("groups.getById", {"group_ids": str(group_id)}, timeout=5.0)
    return bool(fallback.get("ok"))


def build_vk_forecast_keyboard(request_id: UUID) -> dict:
    def payload(action: str) -> str:
        return json.dumps(
            {
                "type": "forecast_request",
                "action": action,
                "request_id": str(request_id),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    return {
        "inline": True,
        "buttons": [
            [
                {
                    "action": {"type": "text", "label": "Взять", "payload": payload("take")},
                    "color": "positive",
                },
                {
                    "action": {"type": "text", "label": "Не взять", "payload": payload("decline")},
                    "color": "negative",
                },
            ]
        ],
    }


def send_vk_message(
    *,
    vk_user_id: Any,
    message: str,
    keyboard: Optional[dict] = None,
    attachment: Optional[str] = None,
    random_id: Optional[int] = None,
) -> dict:
    normalized_user_id = _normalize_vk_user_id(vk_user_id)
    if not normalized_user_id:
        return {"ok": False, "description": "Client does not have a VK user id"}
    if not vk_delivery_configured():
        return {"ok": False, "description": "VK group token is not configured"}

    effective_random_id = random_id if random_id is not None else generate_vk_random_id()
    logger.info(
        "[VKDelivery] messages.send user_id=%s random_id=%s message_len=%s keyboard=%s attachment=%s",
        normalized_user_id,
        effective_random_id,
        len(str(message or "")),
        bool(keyboard),
        bool(attachment),
    )
    result = _vk_api_request(
        "messages.send",
        {
            "user_id": normalized_user_id,
            "random_id": effective_random_id,
            "message": message[:4096],
            "keyboard": keyboard,
            "attachment": attachment,
        },
    )
    if keyboard and is_vk_chat_bot_feature_error(result):
        fallback_random_id = generate_vk_random_id()
        logger.warning(
            "[VKDelivery] VK rejected inline keyboard because chat bot features are disabled; "
            "retrying messages.send without keyboard user_id=%s random_id=%s",
            normalized_user_id,
            fallback_random_id,
        )
        fallback_result = _vk_api_request(
            "messages.send",
            {
                "user_id": normalized_user_id,
                "random_id": fallback_random_id,
                "message": message[:4096],
                "keyboard": None,
                "attachment": attachment,
            },
        )
        if fallback_result.get("ok"):
            fallback_result["fallback_without_keyboard"] = True
            fallback_result["original_error"] = result.get("error")
        return fallback_result
    return result


def answer_vk_message_event(*, user_id: Any, peer_id: Any, event_id: str, text: str) -> dict:
    normalized_user_id = _normalize_vk_user_id(user_id)
    if not normalized_user_id or not event_id:
        return {"ok": False, "description": "VK message event data is incomplete"}
    return _vk_api_request(
        "messages.sendMessageEventAnswer",
        {
            "user_id": normalized_user_id,
            "peer_id": peer_id or normalized_user_id,
            "event_id": event_id,
            "event_data": {
                "type": "show_snackbar",
                "text": text[:90],
            },
        },
    )


def send_vk_message_to_user(
    user: Any,
    message: str,
    *,
    keyboard: Optional[dict] = None,
    image_path: Optional[str] = None,
) -> dict:
    if not user_can_receive_vk_messages(user):
        return {"ok": False, "description": "Client has not allowed VK messages"}

    vk_user_id = getattr(user, "vk_user_id", None)
    attachment = None
    if image_path:
        normalized_user_id = _normalize_vk_user_id(vk_user_id)
        if normalized_user_id:
            attachment = _upload_message_photo(normalized_user_id, image_path)

    return send_vk_message(
        vk_user_id=vk_user_id,
        message=message,
        keyboard=keyboard,
        attachment=attachment,
    )


__all__ = [
    "_vk_broadcast_concurrency",
    "answer_vk_message_event",
    "build_vk_forecast_keyboard",
    "check_vk_group_member",
    "check_vk_messages_allowed",
    "get_vk_unread_conversations",
    "html_to_vk_text",
    "is_vk_message_permission_error",
    "local_static_asset_path",
    "mark_vk_conversation_read",
    "mark_vk_messages_denied",
    "probe_vk_api",
    "refresh_vk_delivery_status",
    "send_vk_message",
    "send_vk_message_to_user",
    "user_can_receive_vk_messages",
    "vk_delivery_configured",
    "vk_group_id",
]
