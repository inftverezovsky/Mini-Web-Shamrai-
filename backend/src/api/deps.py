from fastapi import Depends, HTTPException, status, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from typing import Optional
from src.models.database import get_db
from src.models.models import User
from src.core.security import verify_access_token
from src.core.roles import is_admin_role, is_owner_role, is_staff_role

async def get_current_user(
    authorization: str = Header(None),
    db: AsyncSession = Depends(get_db)
) -> User:
    """
    Decodes the JWT access token from the Authorization header 
    to retrieve the active database user record.
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing"
        )
        
    token = authorization
    if authorization.startswith("Bearer "):
        token = authorization[7:]
        
    payload = verify_access_token(token)
    if not payload or "sub" not in payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalid or token signature expired"
        )
        
    tg_id = int(payload["sub"])
    
    # Fetch user details along with bookmakers configuration and badges
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == tg_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    user = result.scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User associated with this token not found"
        )
    return user

async def get_current_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Enforces staff/admin area validation checks."""
    if not is_staff_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Staff permissions required"
        )
    return current_user


async def get_current_privileged_admin(
    current_user: User = Depends(get_current_user)
) -> User:
    """Allows only owner/admin roles for sensitive operations."""
    if not is_admin_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Admin permissions required"
        )
    return current_user


async def get_current_owner(
    current_user: User = Depends(get_current_user)
) -> User:
    """Allows only the owner role."""
    if not is_owner_role(current_user.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: Owner permissions required"
        )
    return current_user

async def get_optional_user(
    authorization: Optional[str] = Header(None),
    db: AsyncSession = Depends(get_db)
) -> Optional[User]:
    """Optionally retrieves the current user if authorization header is provided."""
    if not authorization:
        return None
    try:
        token = authorization
        if authorization.startswith("Bearer "):
            token = authorization[7:]
        payload = verify_access_token(token)
        if not payload or "sub" not in payload:
            return None
        tg_id = int(payload["sub"])
        result = await db.execute(
            select(User)
            .filter(User.telegram_id == tg_id)
            .options(selectinload(User.bookmakers), selectinload(User.badges))
        )
        return result.scalars().first()
    except Exception:
        return None
