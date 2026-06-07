from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
import re
from src.models.database import get_db
from src.models.models import User
from src.schemas.schemas import UserResponse
from src.core.security import verify_telegram_init_data, create_access_token
from src.core.roles import is_valid_role, normalize_role
from src.core.config import settings

router = APIRouter(prefix="/auth", tags=["Authentication"])

REFERRAL_START_PARAM_RE = re.compile(r"^ref_(\d+)$")

class LoginRequest(BaseModel):
    initData: str

class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse

@router.post("/login", response_model=LoginResponse)
async def login_user(
    request_data: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Verifies Telegram initData.
    Authenticates existing users or registers new ones with default format 'percent'.
    Returns JWT access token and user profile model.
    """
    # Verify signature
    tg_data = verify_telegram_init_data(request_data.initData)
    
    tg_id = tg_data.get("id")
    if not tg_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing telegram ID in credentials"
        )
    
    # Check if user already exists
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == tg_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    user = result.scalars().first()

    referred_by_user_id = None
    start_param = tg_data.get("start_param")
    if isinstance(start_param, str):
        match = REFERRAL_START_PARAM_RE.match(start_param.strip())
        if match:
            candidate_referrer_id = int(match.group(1))
            if candidate_referrer_id != tg_id:
                referrer_res = await db.execute(
                    select(User.telegram_id).filter(User.telegram_id == candidate_referrer_id)
                )
                if referrer_res.scalar_one_or_none() is not None:
                    referred_by_user_id = candidate_referrer_id
    
    is_owner = settings.OWNER_TELEGRAM_ID is not None and tg_id == settings.OWNER_TELEGRAM_ID
    
    if not user:
        # Default role setup
        requested_role = normalize_role(tg_data.get("role"))
        allow_role_from_payload = bool(tg_data.get("_debug_mock")) and settings.allow_debug_auth_bypass
        role = "owner" if is_owner else (requested_role if allow_role_from_payload and is_valid_role(requested_role) else "user")
        import random
        ab_group = random.choice(['A', 'B'])
        
        # Register new user with stats_display_mode set to 'percent'
        user = User(
            telegram_id=tg_id,
            username=tg_data.get("username"),
            first_name=tg_data.get("first_name"),
            last_name=tg_data.get("last_name"),
            role=role,
            stats_display_mode="percent",
            ab_group=ab_group,
            tg_chat_joined=False,
            referred_by_user_id=referred_by_user_id,
        )
        db.add(user)
        await db.flush()
    else:
        # Keep profile details synchronized
        user.username = tg_data.get("username", user.username)
        user.first_name = tg_data.get("first_name", user.first_name)
        user.last_name = tg_data.get("last_name", user.last_name)
        # Ensure existing users have an ab_group assigned
        if not user.ab_group:
            import random
            user.ab_group = random.choice(['A', 'B'])
            
        # Auto-promote to owner if matched with config
        if is_owner:
            user.role = "owner"
        # Allow role overrides in debug environments
        elif bool(tg_data.get("_debug_mock")) and settings.allow_debug_auth_bypass and "role" in tg_data:
            requested_role = normalize_role(tg_data["role"])
            if is_valid_role(requested_role):
                user.role = requested_role
        if user.referred_by_user_id is None and referred_by_user_id is not None:
            user.referred_by_user_id = referred_by_user_id
        
    await db.commit()
    
    # Load refreshed model
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == tg_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    user = result.scalars().first()
    
    # Generate access JWT token
    token_payload = {
        "sub": str(user.telegram_id),
        "role": user.role
    }
    access_token = create_access_token(token_payload)
    
    return LoginResponse(
        access_token=access_token,
        user=user
    )
