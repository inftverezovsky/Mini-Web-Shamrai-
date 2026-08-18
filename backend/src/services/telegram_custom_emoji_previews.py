from __future__ import annotations

import os
import tempfile
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from src.services.telegram_bot import call_telegram_api, download_telegram_file
from src.services.telegram_custom_emoji_library import CUSTOM_EMOJI_ID_RE

CUSTOM_EMOJI_PREVIEW_DIR = Path(__file__).resolve().parents[2] / "static" / "custom_emojis"
MAX_PREVIEW_BYTES = 2 * 1024 * 1024
TELEGRAM_CUSTOM_EMOJI_BATCH_SIZE = 100
_warm_lock = threading.Lock()
_CONTENT_TYPE_SUFFIXES = {
    "image/webp": "webp",
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
}


@dataclass(frozen=True)
class CustomEmojiPreviewWarmReport:
    total: int
    ready: int
    cached: int
    failed: int


def _clean_ids(custom_emoji_ids: Iterable[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            clean_id
            for raw_id in custom_emoji_ids
            if CUSTOM_EMOJI_ID_RE.fullmatch(clean_id := str(raw_id or "").strip())
        )
    )


def _preview_path(custom_emoji_id: str, suffix: str) -> Path:
    return CUSTOM_EMOJI_PREVIEW_DIR / f"{custom_emoji_id}.{suffix}"


def _existing_preview_path(custom_emoji_id: str) -> Path | None:
    for suffix in _CONTENT_TYPE_SUFFIXES.values():
        candidate = _preview_path(custom_emoji_id, suffix)
        if candidate.is_file():
            return candidate
    return None


def _detect_image_content_type(contents: bytes) -> str | None:
    if contents.startswith(b"RIFF") and contents[8:12] == b"WEBP":
        return "image/webp"
    if contents.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if contents.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if contents.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return None


def load_cached_custom_emoji_preview(
    custom_emoji_id: str,
) -> tuple[bytes, str] | None:
    clean_id = str(custom_emoji_id or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        return None
    path = _existing_preview_path(clean_id)
    try:
        if path is None or path.stat().st_size > MAX_PREVIEW_BYTES:
            return None
        contents = path.read_bytes()
    except OSError:
        return None
    content_type = _detect_image_content_type(contents)
    if not content_type:
        return None
    return contents, content_type


def custom_emoji_preview_is_ready(custom_emoji_id: str) -> bool:
    return load_cached_custom_emoji_preview(custom_emoji_id) is not None


def cached_custom_emoji_preview_url(custom_emoji_id: str) -> str | None:
    clean_id = str(custom_emoji_id or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        return None
    path = _existing_preview_path(clean_id)
    if path is None:
        return None
    return f"/api/telegram/custom-emojis/{clean_id}/preview"


def save_custom_emoji_preview(custom_emoji_id: str, contents: bytes) -> None:
    clean_id = str(custom_emoji_id or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        raise ValueError("Invalid custom emoji ID")
    if not contents or len(contents) > MAX_PREVIEW_BYTES:
        raise ValueError("Invalid custom emoji preview size")
    content_type = _detect_image_content_type(contents)
    if not content_type:
        raise ValueError("Unsupported custom emoji preview format")

    CUSTOM_EMOJI_PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    target = _preview_path(clean_id, _CONTENT_TYPE_SUFFIXES[content_type])
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=CUSTOM_EMOJI_PREVIEW_DIR,
            prefix=f".{clean_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(contents)
            temporary.flush()
            os.fsync(temporary.fileno())
            temporary_path = temporary.name
        os.replace(temporary_path, target)
        for suffix in _CONTENT_TYPE_SUFFIXES.values():
            stale_path = _preview_path(clean_id, suffix)
            if stale_path != target:
                stale_path.unlink(missing_ok=True)
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass


def _thumbnail_file_ids(custom_emoji_ids: tuple[str, ...]) -> dict[str, str]:
    result: dict[str, str] = {}
    for start in range(0, len(custom_emoji_ids), TELEGRAM_CUSTOM_EMOJI_BATCH_SIZE):
        batch = custom_emoji_ids[start : start + TELEGRAM_CUSTOM_EMOJI_BATCH_SIZE]
        response = call_telegram_api(
            "getCustomEmojiStickers",
            {"custom_emoji_ids": list(batch)},
        )
        stickers = response.get("result") if response.get("ok") else None
        if not isinstance(stickers, list):
            continue
        for index, sticker in enumerate(stickers):
            if not isinstance(sticker, dict):
                continue
            custom_id = str(sticker.get("custom_emoji_id") or "").strip()
            if not custom_id and index < len(batch):
                custom_id = batch[index]
            thumbnail = sticker.get("thumbnail") or sticker.get("thumb") or {}
            file_id = str(thumbnail.get("file_id") or "").strip()
            if custom_id in batch and file_id:
                result[custom_id] = file_id
    return result


def _download_and_save_preview(custom_emoji_id: str, file_id: str) -> bool:
    try:
        contents, _ = download_telegram_file(file_id, max_bytes=MAX_PREVIEW_BYTES)
        save_custom_emoji_preview(custom_emoji_id, contents)
        return True
    except Exception:
        return False


def warm_custom_emoji_previews(
    custom_emoji_ids: Iterable[str],
    *,
    max_workers: int = 4,
) -> CustomEmojiPreviewWarmReport:
    ids = _clean_ids(custom_emoji_ids)
    if not ids:
        return CustomEmojiPreviewWarmReport(total=0, ready=0, cached=0, failed=0)

    with _warm_lock:
        ready_ids = tuple(
            custom_id
            for custom_id in ids
            if custom_emoji_preview_is_ready(custom_id)
        )
        ready_id_set = set(ready_ids)
        missing_ids = tuple(
            custom_id
            for custom_id in ids
            if custom_id not in ready_id_set
        )
        if not missing_ids:
            return CustomEmojiPreviewWarmReport(
                total=len(ids),
                ready=len(ready_ids),
                cached=0,
                failed=0,
            )

        file_ids = _thumbnail_file_ids(missing_ids)
        cached = 0
        worker_count = max(1, min(int(max_workers or 1), 8, len(file_ids) or 1))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(_download_and_save_preview, custom_id, file_id): custom_id
                for custom_id, file_id in file_ids.items()
            }
            for future in as_completed(futures):
                if future.result():
                    cached += 1

        failed = len(missing_ids) - cached
        return CustomEmojiPreviewWarmReport(
            total=len(ids),
            ready=len(ready_ids) + cached,
            cached=cached,
            failed=failed,
        )
