from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.deps import get_current_admin_read
from src.core.background_tasks import create_logged_task
from src.models.database import get_read_db
from src.models.models import User
from src.services.telegram_custom_emoji_library import (
    CUSTOM_EMOJI_ID_RE,
    load_custom_emoji_library,
)
from src.services.telegram_custom_emoji_previews import (
    cached_custom_emoji_preview_url,
    load_cached_custom_emoji_preview,
    warm_custom_emoji_previews,
)

router = APIRouter(tags=["Telegram Custom Emoji"])
logger = logging.getLogger("uvicorn")
_preview_warm_task: asyncio.Task | None = None


class CustomEmojiLibraryResponse(BaseModel):
    custom_emoji_id: str
    fallback: str
    preview_url: str
    preview_ready: bool


class CustomEmojiPreviewRefreshResponse(BaseModel):
    total: int
    ready: int
    pending: int


def _start_preview_warm(custom_emoji_ids: list[str]) -> None:
    global _preview_warm_task
    if not custom_emoji_ids:
        return
    if _preview_warm_task is not None and not _preview_warm_task.done():
        return
    _preview_warm_task = create_logged_task(
        asyncio.to_thread(warm_custom_emoji_previews, custom_emoji_ids),
        logger=logger,
        failure_message="telegram_custom_emoji_preview_warm_failed",
        task_name="telegram-custom-emoji-preview-warm",
    )


@router.get(
    "/admin/custom-emojis",
    response_model=list[CustomEmojiLibraryResponse],
)
async def get_admin_custom_emoji_library(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    del admin
    items = await load_custom_emoji_library(db)
    response: list[CustomEmojiLibraryResponse] = []
    for item in items:
        cached_url = cached_custom_emoji_preview_url(item.custom_emoji_id)
        response.append(
            CustomEmojiLibraryResponse(
                custom_emoji_id=item.custom_emoji_id,
                fallback=item.fallback,
                preview_url=(
                    cached_url
                    or f"/api/telegram/custom-emojis/{item.custom_emoji_id}/preview"
                ),
                preview_ready=bool(cached_url),
            )
        )
    _start_preview_warm([
        item.custom_emoji_id
        for item, response_item in zip(items, response)
        if not response_item.preview_ready
    ])
    return response


@router.post(
    "/admin/custom-emojis/refresh",
    response_model=CustomEmojiPreviewRefreshResponse,
)
async def refresh_admin_custom_emoji_previews(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db),
):
    del admin
    items = await load_custom_emoji_library(db)
    ids = [item.custom_emoji_id for item in items]
    ready = sum(
        1
        for custom_id in ids
        if cached_custom_emoji_preview_url(custom_id)
    )
    _start_preview_warm(ids)
    return CustomEmojiPreviewRefreshResponse(
        total=len(ids),
        ready=ready,
        pending=max(0, len(ids) - ready),
    )


@router.get("/telegram/custom-emojis/{custom_emoji_id}/preview")
async def get_custom_emoji_preview(
    custom_emoji_id: str,
    db: AsyncSession = Depends(get_read_db),
):
    clean_id = str(custom_emoji_id or "").strip()
    if not CUSTOM_EMOJI_ID_RE.fullmatch(clean_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emoji not found")
    allowed_ids = {
        item.custom_emoji_id
        for item in await load_custom_emoji_library(db)
    }
    if clean_id not in allowed_ids:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Emoji not found")
    cached_preview = load_cached_custom_emoji_preview(clean_id)
    if cached_preview is None:
        _start_preview_warm([clean_id])
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Emoji preview is unavailable",
            headers={"Retry-After": "2"},
        )
    contents, content_type = cached_preview
    return Response(
        content=contents,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
