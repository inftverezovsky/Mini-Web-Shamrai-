import asyncio
import hashlib
import json
import logging
import random
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import delete, insert, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from src.api.deps import get_current_user, get_optional_user
from src.core.config import settings
from src.core.roles import is_staff_role, is_valid_role, normalize_role
from src.core.security import (
    ACCESS_TOKEN_EXPIRE_DAYS,
    create_access_token,
    verify_telegram_init_data,
    verify_telegram_login_widget,
)
from src.models.database import get_db
from src.models.models import (
    AdminAuditLog,
    Bet,
    ChatConversation,
    ChatMessage,
    ChatReadCursor,
    CrowdBetParticipant,
    DailyRewardClaim,
    DeliveryOutbox,
    ForecastRequest,
    IdentityDeviceLink,
    MatchBalanceLog,
    MessageTemplate,
    PaymentAttempt,
    PersonalSignal,
    PersonalSignalReadCursor,
    PromoCode,
    PvPBattleVote,
    Subscription,
    User,
    UserBadge,
    UserNote,
    user_bets,
    user_bookmakers,
)
from src.schemas.schemas import UserResponse
from src.services.telegram_auth import (
    consume_telegram_bot_auth_session,
    create_telegram_bot_auth_session,
    get_telegram_bot_auth_session,
    telegram_auth_start_param,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger("uvicorn")

REFERRAL_START_PARAM_RE = re.compile(r"^ref_(\d+)$")
AUTH_COOKIE_NAME = "shamrai_access_token"
IDENTITY_DEVICE_HEADER = "X-Shamrai-Device-Id"
IDENTITY_DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{15,127}$")


class LoginRequest(BaseModel):
    initData: str


class TelegramWidgetLoginRequest(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    phone: Optional[str] = None
    phone_number: Optional[str] = None
    photo_url: Optional[str] = None
    auth_date: int
    hash: str


class TelegramBotAuthStartResponse(BaseModel):
    auth_token: str
    bot_url: str
    expires_at: datetime


class TelegramBotAuthStatusResponse(BaseModel):
    status: str
    access_token: Optional[str] = None
    token_type: str = "bearer"
    user: Optional[UserResponse] = None


class VkOAuthCodeRequest(BaseModel):
    code: str
    device_id: str
    code_verifier: str
    state: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class VkLinkResponse(BaseModel):
    status: str
    vk_user_id: str
    vk_display_name: Optional[str] = None


class VkOAuthError(Exception):
    pass


VK_AUTH_TEMPORARY_ERROR_MESSAGE = (
    "VK ID временно не завершил авторизацию. Подождите немного и попробуйте снова "
    "или войдите через Telegram."
)


def _vk_oauth_client_error(error: VkOAuthError) -> tuple[int, str]:
    raw_message = str(error or "").strip()
    normalized = raw_message.lower()

    if any(marker in normalized for marker in ("too many", "rate", "flood", "limit", "[9]")):
        return (
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Слишком много попыток входа через VK ID. Подождите немного и попробуйте снова или войдите через Telegram.",
        )

    if any(marker in normalized for marker in ("invalid_grant", "code", "expired", "state mismatch")):
        return (
            status.HTTP_400_BAD_REQUEST,
            "Сессия VK ID устарела. Запустите вход еще раз.",
        )

    if "not configured" in normalized:
        return (
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "VK ID временно недоступен. Попробуйте войти через Telegram.",
        )

    return (status.HTTP_502_BAD_GATEWAY, VK_AUTH_TEMPORARY_ERROR_MESSAGE)


def _vk_urlopen(request: urllib.request.Request, *, timeout: float):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    return opener.open(request, timeout=timeout)


def _vk_oauth_request(path: str, query: dict, body: dict) -> dict:
    query_string = urllib.parse.urlencode(query)
    url = f"https://id.vk.ru/oauth2/{path}?{query_string}"
    encoded_body = urllib.parse.urlencode(body).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=encoded_body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        with _vk_urlopen(request, timeout=settings.TELEGRAM_API_TIMEOUT_SECONDS) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        try:
            payload = json.loads(error.read().decode("utf-8"))
            description = payload.get("error_description") or payload.get("error") or f"HTTP {error.code}"
        except Exception:
            description = f"HTTP {error.code}: {error.reason}"
        raise VkOAuthError(description)
    except Exception as error:
        raise VkOAuthError(str(error))

    if payload.get("error"):
        raise VkOAuthError(payload.get("error_description") or payload.get("error"))

    return payload


def _exchange_vk_code(code: str, device_id: str, code_verifier: str, state: str) -> dict:
    vk_app_id = settings.VK_ID_APP_ID.strip()
    if not vk_app_id or not settings.VK_ID_REDIRECT_URI.strip():
        raise VkOAuthError("VK ID is not configured")

    token_payload = _vk_oauth_request(
        "auth",
        {
            "grant_type": "authorization_code",
            "redirect_uri": settings.VK_ID_REDIRECT_URI.strip(),
            "client_id": vk_app_id,
            "code_verifier": code_verifier,
            "state": state,
            "device_id": device_id,
        },
        {"code": code},
    )

    if token_payload.get("state") and token_payload["state"] != state:
        raise VkOAuthError("VK ID state mismatch")

    if not token_payload.get("user_id"):
        raise VkOAuthError("VK ID response has no user_id")

    access_token = token_payload.get("access_token")
    display_name = None
    if access_token:
        try:
            profile_payload = _vk_oauth_request(
                "user_info",
                {"client_id": vk_app_id},
                {"access_token": access_token},
            )
            user_info = profile_payload.get("user") or {}
            display_name = " ".join(
                part for part in [user_info.get("first_name"), user_info.get("last_name")] if part
            ).strip() or None
        except VkOAuthError:
            display_name = None

    return {
        "vk_user_id": str(token_payload["user_id"]),
        "vk_display_name": display_name,
    }


def _normalize_vk_code_payload(payload: VkOAuthCodeRequest | dict[str, Any]) -> dict[str, str]:
    raw_payload = payload.model_dump() if isinstance(payload, VkOAuthCodeRequest) else payload
    normalized = {
        key: str(raw_payload.get(key) or "").strip()
        for key in ("code", "device_id", "code_verifier", "state")
    }
    if not all(normalized.values()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="code, device_id, code_verifier and state are required",
        )
    return normalized


def _current_user_or_none(value: Any) -> Optional[User]:
    return value if isinstance(value, User) else None


def _normalize_identity_device_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    clean_value = value.strip()
    if not IDENTITY_DEVICE_ID_RE.fullmatch(clean_value):
        return None
    return clean_value


def _identity_device_hash(device_id: str) -> str:
    return hashlib.sha256(f"shamrai-identity-device:{device_id}".encode("utf-8")).hexdigest()


async def _load_identity_device_user(db: AsyncSession, identity_device_id: Any) -> Optional[User]:
    clean_device_id = _normalize_identity_device_id(identity_device_id)
    if not clean_device_id:
        return None

    result = await db.execute(
        select(IdentityDeviceLink).filter(
            IdentityDeviceLink.device_key_hash == _identity_device_hash(clean_device_id)
        )
    )
    link = result.scalars().first()
    if not link or link.source_user_id is None:
        return None

    user = await _load_user_with_profile(db, link.source_user_id)
    if user and is_staff_role(user.role):
        return None
    return user


async def _bind_identity_device(db: AsyncSession, identity_device_id: Any, user: Optional[User]) -> None:
    clean_device_id = _normalize_identity_device_id(identity_device_id)
    if not clean_device_id or not user or is_staff_role(user.role):
        return

    device_key_hash = _identity_device_hash(clean_device_id)
    link = await db.get(IdentityDeviceLink, device_key_hash)
    now = datetime.now(timezone.utc)
    if link:
        link.source_user_id = user.telegram_id
        link.last_seen_at = now
        return

    db.add(
        IdentityDeviceLink(
            device_key_hash=device_key_hash,
            source_user_id=user.telegram_id,
            last_seen_at=now,
        )
    )


async def _load_user_with_profile(db: AsyncSession, telegram_id: int) -> Optional[User]:
    result = await db.execute(
        select(User)
        .filter(User.telegram_id == telegram_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    return result.scalars().first()


async def _load_user_by_phone(db: AsyncSession, phone: str) -> Optional[User]:
    result = await db.execute(
        select(User)
        .filter(User.phone == phone)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    return result.scalars().first()


async def _load_user_by_vk_id(db: AsyncSession, vk_user_id: str) -> Optional[User]:
    result = await db.execute(
        select(User)
        .filter(User.vk_user_id == vk_user_id)
        .options(selectinload(User.bookmakers), selectinload(User.badges))
    )
    return result.scalars().first()


def _normalize_phone_number(value: Any) -> Optional[str]:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    starts_with_plus = raw_value.startswith("+")
    digits = re.sub(r"\D", "", raw_value)
    if not digits:
        return None
    if starts_with_plus:
        return f"+{digits}"
    if raw_value.startswith("00") and len(digits) > 2:
        return f"+{digits[2:]}"
    return digits


def _telegram_phone_from_data(tg_data: dict[str, Any]) -> Optional[str]:
    return _normalize_phone_number(tg_data.get("phone") or tg_data.get("phone_number"))


def _set_auth_cookie(response: Response, access_token: str) -> None:
    response.set_cookie(
        AUTH_COOKIE_NAME,
        access_token,
        max_age=ACCESS_TOKEN_EXPIRE_DAYS * 24 * 60 * 60,
        httponly=True,
        secure=settings.is_production or settings.FRONTEND_BASE_URL.startswith("https://"),
        samesite="lax",
        path="/api",
    )


def _clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        AUTH_COOKIE_NAME,
        path="/api",
        secure=settings.is_production or settings.FRONTEND_BASE_URL.startswith("https://"),
        httponly=True,
        samesite="lax",
    )


def _build_login_response(user: User, response: Optional[Response] = None) -> LoginResponse:
    access_token = create_access_token({"sub": str(user.telegram_id), "role": user.role})
    if response is not None:
        _set_auth_cookie(response, access_token)
    return LoginResponse(access_token=access_token, user=user)


@router.post("/logout")
async def logout_user(response: Response):
    _clear_auth_cookie(response)
    return {"status": "ok"}


async def _resolve_referrer_id(db: AsyncSession, tg_id: int, start_param: Optional[str]) -> Optional[int]:
    if not isinstance(start_param, str):
        return None
    match = REFERRAL_START_PARAM_RE.match(start_param.strip())
    if not match:
        return None

    candidate_referrer_id = int(match.group(1))
    if candidate_referrer_id == tg_id:
        return None

    referrer_res = await db.execute(
        select(User.telegram_id).filter(User.telegram_id == candidate_referrer_id)
    )
    return candidate_referrer_id if referrer_res.scalar_one_or_none() is not None else None


def _apply_telegram_identity_fields(
    user: User,
    tg_data: dict[str, Any],
    *,
    is_owner: bool,
    referred_by_user_id: Optional[int],
) -> None:
    phone = _telegram_phone_from_data(tg_data)
    photo_url = str(tg_data.get("photo_url") or "").strip() or None

    user.username = tg_data.get("username") or user.username
    user.first_name = tg_data.get("first_name") or user.first_name
    user.last_name = tg_data.get("last_name") or user.last_name
    if phone and not user.phone:
        user.phone = phone
    if photo_url:
        user.photo_url = photo_url
    if not user.ab_group:
        user.ab_group = random.choice(["A", "B"])
    if is_owner:
        user.role = "owner"
    elif bool(tg_data.get("_debug_mock")) and settings.allow_debug_auth_bypass and "role" in tg_data:
        requested_role = normalize_role(tg_data["role"])
        if is_valid_role(requested_role):
            user.role = requested_role
    if user.referred_by_user_id is None and referred_by_user_id is not None:
        user.referred_by_user_id = referred_by_user_id


async def _upsert_telegram_user(db: AsyncSession, tg_data: dict[str, Any]) -> User:
    tg_id = int(tg_data.get("id") or 0)
    if not tg_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing telegram ID in credentials",
        )

    user = await _load_user_with_profile(db, tg_id)
    referred_by_user_id = await _resolve_referrer_id(db, tg_id, tg_data.get("start_param"))
    is_owner = settings.OWNER_TELEGRAM_ID is not None and tg_id == settings.OWNER_TELEGRAM_ID
    phone = _telegram_phone_from_data(tg_data)

    if user:
        if phone:
            phone_user = await _load_user_by_phone(db, phone)
            if phone_user and phone_user.telegram_id != user.telegram_id:
                if phone_user.telegram_id >= 0:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Этот телефон уже привязан к другому Telegram-профилю",
                    )
                await _merge_web_only_user_into_telegram(db, phone_user, user)
        _apply_telegram_identity_fields(
            user,
            tg_data,
            is_owner=is_owner,
            referred_by_user_id=referred_by_user_id,
        )
        return user

    if phone:
        phone_user = await _load_user_by_phone(db, phone)
        if phone_user:
            if phone_user.telegram_id >= 0:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Этот телефон уже привязан к другому Telegram-профилю",
                )
            return await _promote_web_user_to_telegram(
                db,
                phone_user,
                tg_data,
                is_owner=is_owner,
                referred_by_user_id=referred_by_user_id,
            )

    requested_role = normalize_role(tg_data.get("role"))
    allow_role_from_payload = bool(tg_data.get("_debug_mock")) and settings.allow_debug_auth_bypass
    role = "owner" if is_owner else (
        requested_role if allow_role_from_payload and is_valid_role(requested_role) else "user"
    )
    user = User(
        telegram_id=tg_id,
        username=tg_data.get("username"),
        first_name=tg_data.get("first_name"),
        last_name=tg_data.get("last_name"),
        phone=phone,
        photo_url=str(tg_data.get("photo_url") or "").strip() or None,
        role=role,
        stats_display_mode="percent",
        ab_group=random.choice(["A", "B"]),
        tg_chat_joined=False,
        referred_by_user_id=referred_by_user_id,
        is_onboarded=False,
        purchased_bets_balance=0,
    )
    db.add(user)
    await db.flush()

    return user


def _web_only_telegram_id_for_vk(vk_user_id: str) -> int:
    clean_vk_id = str(vk_user_id or "").strip()
    try:
        return -(1_000_000_000_000 + int(clean_vk_id))
    except ValueError:
        digest = int.from_bytes(hashlib.sha256(clean_vk_id.encode("utf-8")).digest()[:7], "big")
        return -(2_000_000_000_000 + digest)


async def _create_vk_only_user(db: AsyncSession, vk_user_id: str, display_name: Optional[str]) -> User:
    name = (display_name or "VK клиент").strip()
    first_name, _, last_name = name.partition(" ")
    user = User(
        telegram_id=_web_only_telegram_id_for_vk(vk_user_id),
        username=None,
        first_name=first_name or "VK",
        last_name=last_name or None,
        role="user",
        stats_display_mode="percent",
        vk_user_id=vk_user_id,
        ab_group=random.choice(["A", "B"]),
        tg_chat_joined=False,
    )
    db.add(user)
    await db.flush()
    return user


def _is_web_only_user(user: User) -> bool:
    return user.telegram_id < 0


PROFILE_TRANSFER_FIELDS = (
    "role",
    "stats_display_mode",
    "bankroll",
    "is_onboarded",
    "experience_level",
    "bankroll_size",
    "favorite_sports",
    "risk_tolerance",
    "primary_bookmaker",
    "vk_group_member",
    "vk_messages_allowed",
    "vk_notifications_allowed",
    "web_push_subscription",
    "currency_preference",
    "purchased_bets_balance",
    "free_bets_available",
    "matches_remaining",
    "guarantee_active",
    "guarantee_opened_from_bet_id",
    "guarantee_closed_at",
    "onboarding_goal",
    "ab_group",
    "tg_chat_joined",
    "has_used_shield",
    "alert_min_coef",
    "odds_drop_notifications_enabled",
    "is_night_mode",
    "night_mode_start",
    "night_mode_end",
    "preferred_sports",
    "other_bookmaker_name",
    "client_group",
    "client_tag",
)


def _clone_profile_value(value: Any) -> Any:
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _copy_full_profile_fields(target: User, source: User) -> None:
    for field_name in PROFILE_TRANSFER_FIELDS:
        setattr(target, field_name, _clone_profile_value(getattr(source, field_name)))


def _copy_web_profile_fields(target: User, source: User) -> None:
    target.purchased_bets_balance = (target.purchased_bets_balance or 0) + (source.purchased_bets_balance or 0)
    target.free_bets_available = (target.free_bets_available or 0) + (source.free_bets_available or 0)
    target.matches_remaining = (target.matches_remaining or 0) + (source.matches_remaining or 0)

    if not target.bankroll and source.bankroll:
        target.bankroll = source.bankroll
    if not target.referred_by_user_id and source.referred_by_user_id != target.telegram_id:
        target.referred_by_user_id = source.referred_by_user_id

    if source.guarantee_active and not target.guarantee_active:
        target.guarantee_active = True
        target.guarantee_opened_from_bet_id = source.guarantee_opened_from_bet_id
        target.guarantee_closed_at = source.guarantee_closed_at

    if source.is_onboarded and not target.is_onboarded:
        target.is_onboarded = True
        target.experience_level = source.experience_level
        target.bankroll_size = source.bankroll_size
        target.favorite_sports = source.favorite_sports or []
        target.risk_tolerance = source.risk_tolerance
        target.primary_bookmaker = source.primary_bookmaker
        target.currency_preference = source.currency_preference
        target.onboarding_goal = source.onboarding_goal
        target.preferred_sports = source.preferred_sports or []
        target.other_bookmaker_name = source.other_bookmaker_name

    if not target.favorite_sports and source.favorite_sports:
        target.favorite_sports = source.favorite_sports
    if not target.preferred_sports and source.preferred_sports:
        target.preferred_sports = source.preferred_sports
    if not target.client_group and source.client_group:
        target.client_group = source.client_group
    if not target.client_tag and source.client_tag:
        target.client_tag = source.client_tag


def _new_chat_read_cursor_like(cursor: ChatReadCursor, *, conversation_id: Any, user_id: int) -> ChatReadCursor:
    return ChatReadCursor(
        conversation_id=conversation_id,
        user_id=user_id,
        last_read_message_id=cursor.last_read_message_id,
        updated_at=cursor.updated_at,
    )


async def _merge_chat_cursor_row(
    db: AsyncSession,
    cursor: ChatReadCursor,
    *,
    conversation_id: Any,
    user_id: int,
) -> None:
    existing = await db.get(
        ChatReadCursor,
        {"conversation_id": conversation_id, "user_id": user_id},
    )
    if existing:
        if cursor.last_read_message_id and (
            not existing.last_read_message_id
            or cursor.last_read_message_id > existing.last_read_message_id
        ):
            existing.last_read_message_id = cursor.last_read_message_id
        await db.delete(cursor)
        return

    db.add(_new_chat_read_cursor_like(cursor, conversation_id=conversation_id, user_id=user_id))
    await db.delete(cursor)


async def _merge_chat_read_cursors_for_conversation(
    db: AsyncSession,
    *,
    source_conversation_id: Any,
    target_conversation_id: Any,
    source_id: int,
    target_id: int,
) -> None:
    result = await db.execute(
        select(ChatReadCursor).filter(ChatReadCursor.conversation_id == source_conversation_id)
    )
    for cursor in result.scalars().all():
        await _merge_chat_cursor_row(
            db,
            cursor,
            conversation_id=target_conversation_id,
            user_id=target_id if cursor.user_id == source_id else cursor.user_id,
        )


async def _merge_chat_read_cursors_by_user(db: AsyncSession, source_id: int, target_id: int) -> None:
    result = await db.execute(select(ChatReadCursor).filter(ChatReadCursor.user_id == source_id))
    for cursor in result.scalars().all():
        await _merge_chat_cursor_row(
            db,
            cursor,
            conversation_id=cursor.conversation_id,
            user_id=target_id,
        )


async def _merge_support_conversations(db: AsyncSession, source_id: int, target_id: int) -> None:
    result = await db.execute(
        select(ChatConversation).filter(ChatConversation.owner_user_id == source_id)
    )
    source_conversations = result.scalars().all()

    for source_conversation in source_conversations:
        target_result = await db.execute(
            select(ChatConversation).filter(
                ChatConversation.kind == source_conversation.kind,
                ChatConversation.owner_user_id == target_id,
            )
        )
        target_conversation = target_result.scalars().first()

        if not target_conversation:
            source_conversation.owner_user_id = target_id
            continue

        await db.execute(
            update(ChatMessage)
            .where(ChatMessage.conversation_id == source_conversation.id)
            .values(conversation_id=target_conversation.id)
        )
        await _merge_chat_read_cursors_for_conversation(
            db,
            source_conversation_id=source_conversation.id,
            target_conversation_id=target_conversation.id,
            source_id=source_id,
            target_id=target_id,
        )
        if source_conversation.last_message_at and (
            not target_conversation.last_message_at
            or source_conversation.last_message_at > target_conversation.last_message_at
        ):
            target_conversation.last_message_at = source_conversation.last_message_at
        await db.delete(source_conversation)


async def _merge_personal_signal_read_cursor(db: AsyncSession, source_id: int, target_id: int) -> None:
    source_cursor = await db.get(PersonalSignalReadCursor, source_id)
    if not source_cursor:
        return

    target_cursor = await db.get(PersonalSignalReadCursor, target_id)
    if target_cursor:
        if source_cursor.last_read_signal_id and (
            not target_cursor.last_read_signal_id
            or source_cursor.last_read_signal_id > target_cursor.last_read_signal_id
        ):
            target_cursor.last_read_signal_id = source_cursor.last_read_signal_id
        await db.delete(source_cursor)
        return

    db.add(
        PersonalSignalReadCursor(
            user_id=target_id,
            last_read_signal_id=source_cursor.last_read_signal_id,
            updated_at=source_cursor.updated_at,
        )
    )
    await db.delete(source_cursor)


async def _move_user_references(db: AsyncSession, source_id: int, target_id: int) -> None:
    await _merge_user_bookmakers(db, source_id, target_id)
    await _merge_user_bets(db, source_id, target_id)
    await _merge_forecast_requests(db, source_id, target_id)
    await _merge_daily_rewards(db, source_id, target_id)
    await _merge_pvp_votes(db, source_id, target_id)
    await _merge_support_conversations(db, source_id, target_id)
    await _merge_chat_read_cursors_by_user(db, source_id, target_id)
    await _merge_personal_signal_read_cursor(db, source_id, target_id)

    for model in (
        Subscription,
        PaymentAttempt,
        MatchBalanceLog,
        PersonalSignal,
        DeliveryOutbox,
        CrowdBetParticipant,
        PromoCode,
        UserBadge,
        UserNote,
    ):
        await db.execute(update(model).where(model.user_id == source_id).values(user_id=target_id))

    await db.execute(update(User).where(User.referred_by_user_id == source_id).values(referred_by_user_id=target_id))
    await db.execute(update(Bet).where(Bet.author_id == source_id).values(author_id=target_id))
    await db.execute(update(ChatConversation).where(ChatConversation.assigned_staff_id == source_id).values(assigned_staff_id=target_id))
    await db.execute(update(ChatMessage).where(ChatMessage.sender_user_id == source_id).values(sender_user_id=target_id))
    await db.execute(update(ForecastRequest).where(ForecastRequest.handled_by == source_id).values(handled_by=target_id))
    await db.execute(update(AdminAuditLog).where(AdminAuditLog.actor_id == source_id).values(actor_id=target_id))
    await db.execute(update(AdminAuditLog).where(AdminAuditLog.target_user_id == source_id).values(target_user_id=target_id))
    await db.execute(update(MessageTemplate).where(MessageTemplate.updated_by == source_id).values(updated_by=target_id))
    await db.execute(update(IdentityDeviceLink).where(IdentityDeviceLink.source_user_id == source_id).values(source_user_id=target_id))


async def _merge_user_bookmakers(db: AsyncSession, source_id: int, target_id: int) -> None:
    source_result = await db.execute(
        select(user_bookmakers.c.bookmaker_id).where(user_bookmakers.c.user_id == source_id)
    )
    target_result = await db.execute(
        select(user_bookmakers.c.bookmaker_id).where(user_bookmakers.c.user_id == target_id)
    )
    source_bookmakers = set(source_result.scalars().all())
    target_bookmakers = set(target_result.scalars().all())

    for bookmaker_id in source_bookmakers - target_bookmakers:
        await db.execute(
            insert(user_bookmakers).values(user_id=target_id, bookmaker_id=bookmaker_id)
        )

    await db.execute(delete(user_bookmakers).where(user_bookmakers.c.user_id == source_id))


async def _merge_user_bets(db: AsyncSession, source_id: int, target_id: int) -> None:
    source_result = await db.execute(select(user_bets).where(user_bets.c.user_id == source_id))
    source_rows = source_result.mappings().all()
    target_result = await db.execute(select(user_bets.c.bet_id).where(user_bets.c.user_id == target_id))
    target_bet_ids = set(target_result.scalars().all())

    for row in source_rows:
        if row["bet_id"] in target_bet_ids:
            continue
        await db.execute(
            insert(user_bets).values(
                user_id=target_id,
                bet_id=row["bet_id"],
                taken_at=row.get("taken_at"),
                access_type=row.get("access_type") or "paid_match",
                match_charged=row.get("match_charged") if row.get("match_charged") is not None else True,
            )
        )

    await db.execute(delete(user_bets).where(user_bets.c.user_id == source_id))


async def _merge_forecast_requests(db: AsyncSession, source_id: int, target_id: int) -> None:
    result = await db.execute(select(ForecastRequest).filter(ForecastRequest.user_id == source_id))
    requests = result.scalars().all()
    for forecast_request in requests:
        conflict_result = await db.execute(
            select(ForecastRequest.id).filter(
                ForecastRequest.user_id == target_id,
                ForecastRequest.bet_id == forecast_request.bet_id,
            )
        )
        if conflict_result.scalar_one_or_none():
            await db.delete(forecast_request)
        else:
            forecast_request.user_id = target_id


async def _merge_daily_rewards(db: AsyncSession, source_id: int, target_id: int) -> None:
    result = await db.execute(select(DailyRewardClaim).filter(DailyRewardClaim.user_id == source_id))
    claims = result.scalars().all()
    for claim in claims:
        conflict_result = await db.execute(
            select(DailyRewardClaim.id).filter(
                DailyRewardClaim.user_id == target_id,
                DailyRewardClaim.claimed_date == claim.claimed_date,
            )
        )
        if conflict_result.scalar_one_or_none():
            await db.delete(claim)
        else:
            claim.user_id = target_id


async def _merge_pvp_votes(db: AsyncSession, source_id: int, target_id: int) -> None:
    result = await db.execute(select(PvPBattleVote).filter(PvPBattleVote.user_id == source_id))
    votes = result.scalars().all()
    for vote in votes:
        conflict_result = await db.execute(
            select(PvPBattleVote.id).filter(
                PvPBattleVote.user_id == target_id,
                PvPBattleVote.battle_id == vote.battle_id,
            )
        )
        if conflict_result.scalar_one_or_none():
            await db.delete(vote)
        else:
            vote.user_id = target_id


async def _promote_web_user_to_telegram(
    db: AsyncSession,
    source: User,
    tg_data: dict[str, Any],
    *,
    is_owner: bool,
    referred_by_user_id: Optional[int],
) -> User:
    if not _is_web_only_user(source):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот телефон уже привязан к другому Telegram-профилю",
        )

    tg_id = int(tg_data.get("id") or 0)
    source_id = source.telegram_id
    source_vk_user_id = source.vk_user_id
    source_phone = _telegram_phone_from_data(tg_data) or source.phone

    target = User(
        telegram_id=tg_id,
        username=tg_data.get("username") or source.username,
        first_name=tg_data.get("first_name") or source.first_name,
        last_name=tg_data.get("last_name") or source.last_name,
        photo_url=str(tg_data.get("photo_url") or "").strip() or source.photo_url,
        referred_by_user_id=(
            source.referred_by_user_id
            if source.referred_by_user_id not in {source_id, tg_id}
            else referred_by_user_id
        ),
    )
    _copy_full_profile_fields(target, source)
    if is_owner:
        target.role = "owner"
    if not target.role:
        target.role = "user"

    source.vk_user_id = None
    source.phone = None
    await db.flush()

    target.vk_user_id = source_vk_user_id
    target.phone = source_phone
    db.add(target)
    await db.flush()

    _apply_telegram_identity_fields(
        target,
        tg_data,
        is_owner=is_owner,
        referred_by_user_id=referred_by_user_id,
    )
    await _move_user_references(db, source_id, tg_id)
    await db.flush()
    await db.delete(source)
    return target


async def _merge_web_only_user_into_telegram(db: AsyncSession, source: User, target: User) -> None:
    if source.telegram_id == target.telegram_id:
        return
    if source.telegram_id >= 0 or target.telegram_id < 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот профиль уже привязан к другому Telegram-профилю",
        )

    source_id = source.telegram_id
    target_id = target.telegram_id
    source_vk_user_id = source.vk_user_id
    source_phone = source.phone

    _copy_web_profile_fields(target, source)
    source.vk_user_id = None
    source.phone = None
    target.vk_group_member = bool(target.vk_group_member or source.vk_group_member)
    target.vk_messages_allowed = bool(target.vk_messages_allowed or source.vk_messages_allowed)
    target.vk_notifications_allowed = bool(target.vk_notifications_allowed or source.vk_notifications_allowed)
    await db.flush()
    if source_vk_user_id and not target.vk_user_id:
        target.vk_user_id = source_vk_user_id
    if source_phone and not target.phone:
        target.phone = source_phone

    await _move_user_references(db, source_id, target_id)

    await db.flush()
    await db.delete(source)


async def _upsert_telegram_user_with_optional_web_profile(
    db: AsyncSession,
    tg_data: dict[str, Any],
    current_user: Optional[User] = None,
    device_user: Optional[User] = None,
) -> User:
    source_user = current_user if current_user and _is_web_only_user(current_user) else None
    if not source_user and device_user and _is_web_only_user(device_user):
        source_user = device_user

    if not source_user:
        return await _upsert_telegram_user(db, tg_data)

    tg_id = int(tg_data.get("id") or 0)
    target = await _load_user_with_profile(db, tg_id) if tg_id else None
    if (
        target
        and target.vk_user_id
        and source_user.vk_user_id
        and target.vk_user_id != source_user.vk_user_id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Этот Telegram уже привязан к другому VK-профилю",
        )

    target = await _upsert_telegram_user(db, tg_data)
    if source_user.telegram_id != target.telegram_id:
        await _merge_web_only_user_into_telegram(db, source_user, target)
    return target


async def _resolve_telegram_login_user(
    db: AsyncSession,
    tg_data: dict[str, Any],
    *,
    current_user: Optional[User],
    identity_device_id: Any,
) -> User:
    device_user = None if current_user else await _load_identity_device_user(db, identity_device_id)
    return await _upsert_telegram_user_with_optional_web_profile(
        db,
        tg_data,
        current_user,
        device_user,
    )


async def _resolve_vk_login_user(
    db: AsyncSession,
    vk_profile: dict,
    *,
    current_user: Optional[User],
    identity_device_id: Any,
) -> User:
    vk_user_id = vk_profile["vk_user_id"]
    source_user = current_user or await _load_identity_device_user(db, identity_device_id)
    existing_user = await _load_user_by_vk_id(db, vk_user_id)

    if source_user:
        if source_user.vk_user_id and source_user.vk_user_id != vk_user_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Этот профиль уже привязан к другому VK ID",
            )

        if source_user.telegram_id > 0:
            if existing_user and existing_user.telegram_id != source_user.telegram_id:
                if not _is_web_only_user(existing_user):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Этот VK ID уже привязан к другому Telegram-профилю",
                    )
                await _merge_web_only_user_into_telegram(db, existing_user, source_user)
            source_user.vk_user_id = vk_user_id
            return source_user

        if _is_web_only_user(source_user):
            return existing_user or source_user

    if existing_user:
        return existing_user

    return await _create_vk_only_user(db, vk_user_id, vk_profile.get("vk_display_name"))


async def _exchange_vk_or_502(payload: dict[str, str]) -> dict:
    try:
        return await asyncio.to_thread(
            _exchange_vk_code,
            payload["code"],
            payload["device_id"],
            payload["code_verifier"],
            payload["state"],
        )
    except VkOAuthError as error:
        status_code, detail = _vk_oauth_client_error(error)
        logger.warning("[Auth] vk_oauth_failed status=%s reason=%s", status_code, type(error).__name__)
        raise HTTPException(
            status_code=status_code,
            detail=detail,
        )


@router.post("/login", response_model=LoginResponse)
async def login_user(
    request_data: LoginRequest,
    response: Response,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies Telegram Mini App initData and returns a JWT for the Telegram profile.
    """
    tg_data = verify_telegram_init_data(request_data.initData)
    source_user = _current_user_or_none(current_user)
    user = await _resolve_telegram_login_user(
        db,
        tg_data,
        current_user=source_user,
        identity_device_id=identity_device_id,
    )
    await _bind_identity_device(db, identity_device_id, user)
    telegram_id = user.telegram_id
    await db.commit()

    hydrated_user = await _load_user_with_profile(db, telegram_id)
    return _build_login_response(hydrated_user, response)


@router.post("/telegram-widget", response_model=LoginResponse)
async def login_telegram_widget(
    request_data: TelegramWidgetLoginRequest,
    response: Response,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Verifies Telegram Login Widget payload for browser login and returns the same JWT type.
    """
    tg_data = verify_telegram_login_widget(request_data.model_dump(exclude_none=True))
    source_user = _current_user_or_none(current_user)
    user = await _resolve_telegram_login_user(
        db,
        tg_data,
        current_user=source_user,
        identity_device_id=identity_device_id,
    )
    await _bind_identity_device(db, identity_device_id, user)
    telegram_id = user.telegram_id
    await db.commit()

    hydrated_user = await _load_user_with_profile(db, telegram_id)
    return _build_login_response(hydrated_user, response)


@router.get("/telegram/callback", response_model=LoginResponse)
async def telegram_callback(
    request: Request,
    response: Response,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Browser Telegram callback endpoint.

    It accepts the same signed payload as the Telegram Login Widget redirect flow.
    If a signed phone/phone_number is present, a matching web-only profile is
    promoted into the verified Telegram account instead of creating a duplicate.
    """
    tg_data = verify_telegram_login_widget(dict(request.query_params))
    source_user = _current_user_or_none(current_user)
    user = await _resolve_telegram_login_user(
        db,
        tg_data,
        current_user=source_user,
        identity_device_id=identity_device_id,
    )
    await _bind_identity_device(db, identity_device_id, user)
    telegram_id = user.telegram_id
    await db.commit()

    hydrated_user = await _load_user_with_profile(db, telegram_id)
    return _build_login_response(hydrated_user, response)


@router.post("/telegram/bot-session", response_model=TelegramBotAuthStartResponse)
async def start_telegram_bot_auth_session(
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    clean_bot_username = settings.TELEGRAM_BOT_USERNAME.strip().lstrip("@")
    if not clean_bot_username:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Telegram bot username is not configured",
        )

    source_user = _current_user_or_none(current_user) or await _load_identity_device_user(db, identity_device_id)
    session = await create_telegram_bot_auth_session(
        source_user_id=source_user.telegram_id if source_user else None,
    )
    start_param = telegram_auth_start_param(session.auth_token)
    return TelegramBotAuthStartResponse(
        auth_token=session.auth_token,
        bot_url=f"https://t.me/{clean_bot_username}?start={start_param}",
        expires_at=session.expires_at,
    )


@router.get("/telegram/bot-session/{auth_token}", response_model=TelegramBotAuthStatusResponse)
async def poll_telegram_bot_auth_session(
    auth_token: str,
    response: Response,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    session = await get_telegram_bot_auth_session(auth_token)
    if not session:
        return TelegramBotAuthStatusResponse(status="expired")
    if session.status != "confirmed" or not session.telegram_user:
        return TelegramBotAuthStatusResponse(status=session.status)

    source_user = _current_user_or_none(current_user)
    if not source_user and session.source_user_id is not None:
        source_user = await _load_user_with_profile(db, session.source_user_id)
    if not source_user:
        source_user = await _load_identity_device_user(db, identity_device_id)

    user = await _upsert_telegram_user_with_optional_web_profile(db, session.telegram_user, source_user)
    await _bind_identity_device(db, identity_device_id, user)
    telegram_id = user.telegram_id
    await db.commit()
    await consume_telegram_bot_auth_session(auth_token)

    hydrated_user = await _load_user_with_profile(db, telegram_id)
    login_response = _build_login_response(hydrated_user, response)
    return TelegramBotAuthStatusResponse(
        status="confirmed",
        access_token=login_response.access_token,
        token_type=login_response.token_type,
        user=login_response.user,
    )


@router.post("/vk/login", response_model=LoginResponse)
async def vk_id_login(
    request_data: VkOAuthCodeRequest,
    response: Response,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: Optional[User] = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Browser login via VK ID. A new VK profile creates a web-only cabinet;
    Telegram can be linked later from the profile without losing progress.
    """
    payload = _normalize_vk_code_payload(request_data)
    vk_profile = await _exchange_vk_or_502(payload)
    source_user = _current_user_or_none(current_user)
    user = await _resolve_vk_login_user(
        db,
        vk_profile,
        current_user=source_user,
        identity_device_id=identity_device_id,
    )
    await _bind_identity_device(db, identity_device_id, user)
    telegram_id = user.telegram_id
    await db.commit()
    user = await _load_user_with_profile(db, telegram_id)

    return _build_login_response(user, response)


@router.post("/vk/link", response_model=VkLinkResponse)
async def vk_id_link(
    request_data: VkOAuthCodeRequest,
    identity_device_id: Optional[str] = Header(None, alias=IDENTITY_DEVICE_HEADER),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Exchanges a VK ID authorization code and links the resulting VK user ID
    to the currently authenticated Telegram profile.
    """
    payload = _normalize_vk_code_payload(request_data)
    vk_profile = await _exchange_vk_or_502(payload)
    vk_user_id = vk_profile["vk_user_id"]
    current_profile = _current_user_or_none(current_user)
    if not current_profile:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    if current_profile.vk_user_id and current_profile.vk_user_id != vk_user_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This Telegram profile is already linked to another VK profile",
        )

    existing_result = await db.execute(
        select(User)
        .filter(
            User.vk_user_id == vk_user_id,
            User.telegram_id != current_profile.telegram_id,
        )
    )
    existing_user = existing_result.scalars().first()

    if existing_user:
        if not _is_web_only_user(existing_user):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Этот VK ID уже привязан к другому Telegram-профилю",
            )
        await _merge_web_only_user_into_telegram(db, existing_user, current_profile)
    else:
        current_profile.vk_user_id = vk_user_id

    await _bind_identity_device(db, identity_device_id, current_profile)
    await db.commit()

    return VkLinkResponse(
        status="success",
        vk_user_id=vk_user_id,
        vk_display_name=vk_profile.get("vk_display_name"),
    )
