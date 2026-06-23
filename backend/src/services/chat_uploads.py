from __future__ import annotations

import os
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError


CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_VOICE_MAX_BYTES = 10 * 1024 * 1024
CHAT_VOICE_MAX_DURATION_MS = 120_000
CHAT_ATTACHMENT_CHUNK_BYTES = 256 * 1024

CHAT_MESSAGE_TYPE_IMAGE = "image"
CHAT_MESSAGE_TYPE_VOICE = "voice"


@dataclass(frozen=True)
class StoredChatAttachment:
    message_type: str
    payload: dict[str, object]
    absolute_path: str


def _safe_original_filename(filename: Optional[str]) -> str:
    clean = os.path.basename(filename or "").strip()
    return clean[:180] or "attachment"


def _detect_image(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\xff\xd8\xff"):
        return ".jpg", "image/jpeg"
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png", "image/png"
    if len(header) >= 12 and header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return ".webp", "image/webp"
    return None


def _detect_voice(header: bytes) -> tuple[str, str] | None:
    if header.startswith(b"\x1a\x45\xdf\xa3"):
        return ".webm", "audio/webm"
    if header.startswith(b"OggS"):
        return ".ogg", "audio/ogg"
    if len(header) >= 12 and header[4:8] == b"ftyp":
        return ".m4a", "audio/mp4"
    return None


def _detect_attachment(message_type: str, header: bytes) -> tuple[str, str] | None:
    if message_type == CHAT_MESSAGE_TYPE_IMAGE:
        return _detect_image(header)
    if message_type == CHAT_MESSAGE_TYPE_VOICE:
        return _detect_voice(header)
    return None


def _max_size_for_type(message_type: str) -> int:
    if message_type == CHAT_MESSAGE_TYPE_IMAGE:
        return CHAT_IMAGE_MAX_BYTES
    if message_type == CHAT_MESSAGE_TYPE_VOICE:
        return CHAT_VOICE_MAX_BYTES
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Неизвестный тип вложения")


def _validate_duration_ms(duration_ms: Optional[int]) -> Optional[int]:
    if duration_ms is None:
        return None
    try:
        clean_duration = int(duration_ms)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректная длительность голосового")
    if clean_duration < 0 or clean_duration > CHAT_VOICE_MAX_DURATION_MS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Голосовое сообщение не может быть длиннее 2 минут")
    return clean_duration


def _ensure_target_dir(target_root: str, conversation_id: UUID) -> str:
    root = os.path.abspath(target_root)
    target_dir = os.path.abspath(os.path.join(root, str(conversation_id)))
    if os.path.commonpath([root, target_dir]) != root:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Некорректный путь вложения")
    os.makedirs(target_dir, exist_ok=True)
    return target_dir


def _read_image_size(path: str) -> tuple[int, int]:
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            return int(image.width), int(image.height)
    except (OSError, SyntaxError, UnidentifiedImageError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Не удалось прочитать изображение")


async def store_chat_attachment(
    upload: UploadFile,
    *,
    target_root: str,
    conversation_id: UUID,
    message_type: str,
    duration_ms: Optional[int] = None,
) -> StoredChatAttachment:
    if not upload or not upload.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл не выбран")

    target_dir = _ensure_target_dir(target_root, conversation_id)
    max_size = _max_size_for_type(message_type)
    fd, temp_path = tempfile.mkstemp(prefix="chat-upload-", suffix=".tmp", dir=target_dir)
    total_size = 0
    header = b""

    try:
        with os.fdopen(fd, "wb") as temp_file:
            while True:
                chunk = await upload.read(CHAT_ATTACHMENT_CHUNK_BYTES)
                if not chunk:
                    break
                total_size += len(chunk)
                if total_size > max_size:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Файл слишком большой. Максимум 5 МБ для скринов и 10 МБ для голосовых.",
                    )
                if len(header) < 32:
                    header = (header + chunk)[:32]
                temp_file.write(chunk)

        if total_size <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл пустой")

        detected = _detect_attachment(message_type, header)
        if not detected:
            detail = (
                "Недопустимый формат скрина. Разрешены: .jpg, .png, .webp"
                if message_type == CHAT_MESSAGE_TYPE_IMAGE
                else "Недопустимый формат голосового. Разрешены: .webm, .ogg, .m4a"
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
        extension, mime_type = detected

        filename = f"{uuid.uuid4().hex}{extension}"
        final_path = os.path.join(target_dir, filename)
        os.replace(temp_path, final_path)
        os.chmod(final_path, 0o644)

        relative_url = f"/static/chat/{conversation_id}/{filename}"
        payload: dict[str, object] = {
            "url": relative_url,
            "mime_type": mime_type,
            "size_bytes": total_size,
            "original_filename": _safe_original_filename(upload.filename),
        }

        if message_type == CHAT_MESSAGE_TYPE_IMAGE:
            width, height = _read_image_size(final_path)
            payload.update({"width": width, "height": height})
        elif message_type == CHAT_MESSAGE_TYPE_VOICE:
            clean_duration = _validate_duration_ms(duration_ms)
            if clean_duration is not None:
                payload["duration_ms"] = clean_duration

        return StoredChatAttachment(message_type=message_type, payload=payload, absolute_path=final_path)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    finally:
        await upload.close()


def remove_chat_attachment(payload: dict[str, object], *, target_root: str) -> None:
    raw_url = payload.get("url") if isinstance(payload, dict) else None
    if not isinstance(raw_url, str) or not raw_url.startswith("/static/chat/"):
        return
    relative_path = raw_url.removeprefix("/static/chat/").replace("/", os.sep)
    root = os.path.abspath(target_root)
    absolute_path = os.path.abspath(os.path.join(root, relative_path))
    if os.path.commonpath([root, absolute_path]) != root:
        return
    if os.path.exists(absolute_path):
        os.remove(absolute_path)
