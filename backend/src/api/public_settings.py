from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import get_read_db
from src.schemas.schemas import PublicThemeSettingsResponse
from src.services.system_settings import get_public_theme_settings


router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("/theme", response_model=PublicThemeSettingsResponse)
async def public_theme_settings(
    db: AsyncSession = Depends(get_read_db),
):
    """Public, sanitized theme settings for early client-side UI boot."""
    return await get_public_theme_settings(db)
