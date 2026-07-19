from __future__ import annotations

import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from typing import Optional
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError


CHAT_IMAGE_MAX_BYTES = 5 * 1024 * 1024
CHAT_VOICE_MAX_BYTES = 10 * 1024 * 1024
CHAT_FILE_MAX_BYTES = 25 * 1024 * 1024
CHAT_VOICE_MAX_DURATION_MS = 120_000
CHAT_ATTACHMENT_CHUNK_BYTES = 256 * 1024

CHAT_MESSAGE_TYPE_IMAGE = "image"
CHAT_MESSAGE_TYPE_VOICE = "voice"
CHAT_MESSAGE_TYPE_FILE = "file"
CHAT_ATTACHMENT_STORAGE_PATH_PATTERN = re.compile(
    r"^(?P<conversation_id>[0-9a-fA-F-]{36})/(?P<filename>[0-9a-f]{32}\.[a-z0-9][a-z0-9._-]{0,15})$"
)


@dataclass(frozen=True)
class StoredChatAttachment:
    message_type: str
    payload: dict[str, object]
    absolute_path: str


def _safe_original_filename(filename: Optional[str]) -> str:
    clean = os.path.basename(filename or "").strip()
    clean = re.sub(r"[\x00-\x1f\x7f]+", "", clean)
    clean = clean.replace('"', "").replace("\\", "").replace("/", "")
    return clean[:180] or "attachment"


def _safe_storage_extension(filename: Optional[str]) -> str:
    extension = os.path.splitext(_safe_original_filename(filename))[1].lower()
    if not extension or len(extension) > 16:
        return ".bin"
    if not re.fullmatch(r"\.[a-z0-9][a-z0-9._-]*", extension):
        return ".bin"
    return extension


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
    if message_type == CHAT_MESSAGE_TYPE_FILE:
        return CHAT_FILE_MAX_BYTES
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
                        detail="Файл слишком большой. Максимум 5 МБ для скринов, 10 МБ для голосовых и 25 МБ для файлов.",
                    )
                if len(header) < 32:
                    header = (header + chunk)[:32]
                temp_file.write(chunk)

        if total_size <= 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Файл пустой")

        detected = _detect_attachment(message_type, header)
        if message_type in {CHAT_MESSAGE_TYPE_IMAGE, CHAT_MESSAGE_TYPE_VOICE}:
            if not detected:
                detail = (
                    "Недопустимый формат скрина. Разрешены: .jpg, .png, .webp"
                    if message_type == CHAT_MESSAGE_TYPE_IMAGE
                    else "Недопустимый формат голосового. Разрешены: .webm, .ogg, .m4a"
                )
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
            extension, mime_type = detected
        else:
            extension = _safe_storage_extension(upload.filename)
            mime_type = (upload.content_type or "application/octet-stream").strip() or "application/octet-stream"

        filename = f"{uuid.uuid4().hex}{extension}"
        final_path = os.path.join(target_dir, filename)
        os.replace(temp_path, final_path)
        os.chmod(final_path, 0o644)

        payload: dict[str, object] = {
            "mime_type": mime_type,
            "size_bytes": total_size,
            "original_filename": _safe_original_filename(upload.filename),
            "storage_path": f"{conversation_id}/{filename}",
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


def _attachment_relative_path(
    payload: dict[str, object],
    *,
    expected_conversation_id: UUID | str | None = None,
) -> str:
    raw_url = payload.get("url") if isinstance(payload, dict) else None
    raw_storage_path = payload.get("storage_path") if isinstance(payload, dict) else None
    if isinstance(raw_storage_path, str) and raw_storage_path:
        candidate = raw_storage_path
    elif isinstance(raw_url, str) and raw_url.startswith("/static/chat/"):
        candidate = raw_url.removeprefix("/static/chat/")
    else:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено")

    match = CHAT_ATTACHMENT_STORAGE_PATH_PATTERN.fullmatch(candidate)
    if not match:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено")
    try:
        conversation_id = UUID(match.group("conversation_id"))
        expected_id = UUID(str(expected_conversation_id)) if expected_conversation_id is not None else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено")
    if expected_id is not None and conversation_id != expected_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено")
    return os.path.join(str(conversation_id), match.group("filename"))


def remove_chat_attachment(payload: dict[str, object], *, target_root: str) -> None:
    try:
        relative_path = _attachment_relative_path(payload)
    except HTTPException:
        return
    root = os.path.realpath(target_root)
    absolute_path = os.path.realpath(os.path.join(root, relative_path))
    try:
        if os.path.commonpath([root, absolute_path]) != root:
            return
    except ValueError:
        return
    if os.path.exists(absolute_path):
        os.remove(absolute_path)


def resolve_chat_attachment_path(
    payload: dict[str, object],
    *,
    target_root: str,
    expected_conversation_id: UUID | str | None = None,
) -> str:
    relative_path = _attachment_relative_path(
        payload,
        expected_conversation_id=expected_conversation_id,
    )
    root = os.path.realpath(target_root)
    absolute_path = os.path.realpath(os.path.join(root, relative_path))
    try:
        is_inside_root = os.path.commonpath([root, absolute_path]) == root
    except ValueError:
        is_inside_root = False
    if not is_inside_root or not os.path.isfile(absolute_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Вложение не найдено")
    return absolute_path
