import base64
import json
import os
from fastapi import APIRouter, Depends, HTTPException, Query, status, UploadFile, File, Form, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import and_, or_, func
from typing import List, Optional
from decimal import Decimal, ROUND_HALF_UP
from uuid import UUID
from datetime import datetime, timezone

from src.models.database import get_db, get_read_db
from src.models.models import User, Bookmaker, Bet, ForecastRequest, PaymentAttempt, Subscription, user_bets
from src.schemas.schemas import (
    AdminAnalytics,
    BetCreate,
    BetHintInvoiceResponse,
    BetHintRequest,
    BetHintResponse,
    BetOddsDropNotifyResponse,
    BetOddsDropUpdate,
    BetResolve,
    BetResponse,
    BetUpdate,
    UserStats,
)
from src.api.deps import (
    get_current_admin,
    get_current_admin_read,
    get_current_privileged_admin,
    get_current_user,
    get_current_user_read,
)
from src.api.payments import (
    PAYMENT_PURCHASE_BET_HINT,
    _create_payment_attempt,
    create_telegram_stars_invoice_link,
)
from src.core.bookmaker_links import normalize_bookmaker_links, normalize_match_url
from src.core.roles import is_staff_role
from src.core.config import settings
from src.core.message_templates import (
    TEMPLATE_BET_LOSS,
    TEMPLATE_BET_LOSS_SUPERCOMPENSATION,
    TEMPLATE_BET_REFUND,
    TEMPLATE_BET_WIN,
    TEMPLATE_ODDS_DROP,
    default_message_template_body,
    load_message_template_body,
    render_message_template_body,
)
from src.core.telegram_delivery import is_personal_telegram_user_id
from src.services.delivery_outbox import CHANNEL_TELEGRAM_MESSAGE, CHANNEL_VK_MESSAGE, enqueue_delivery
from src.services.forecast_delivery import (
    FORECAST_STATUS_ANNOUNCED,
    FORECAST_STATUS_CANCELLED,
    FORECAST_STATUS_DECLINED,
    FORECAST_STATUS_INTERESTED,
    FORECAST_STATUS_REMOVED,
    FORECAST_INACTIVE_MESSAGE,
    PLACEHOLDER_EVENT_NAME,
    count_client_bet_takers,
    enqueue_admin_group_forecast_result_notification,
)
from src.services.match_access import (
    current_match_balance,
    ensure_bet_eligible_for_user,
    load_locked_bet_for_user_access,
    lock_user_balance,
    log_match_balance_event,
    record_user_bet_access,
    record_user_free_bet_access,
)
from src.services.statistics import is_paid_client_access
from src.services.coupon_uploads import store_coupon_image
from src.services.signals import broadcast_live_signal, deliver_personal_signal
from src.services.vk_delivery import html_to_vk_text, user_can_receive_vk_messages

router = APIRouter(prefix="/bets", tags=["Bets"])

STATIC_COUPONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "static",
    "coupons",
)

PUBLICATION_TYPE_FORECAST = "forecast"
PUBLICATION_TYPE_TEXT = "text"
PUBLICATION_TYPES = {PUBLICATION_TYPE_FORECAST, PUBLICATION_TYPE_TEXT}

ODDS_DROP_DELIVERED_FORECAST_STATUSES = {"sent", "manual_sent"}
BET_HINT_PRICE_XTR = 20


def _event_name_or_placeholder(value: Optional[str]) -> str:
    return str(value or "").strip() or PLACEHOLDER_EVENT_NAME


def _encode_feed_cursor(bet: Bet) -> str:
    payload = {
        "created_at": bet.created_at.isoformat() if bet.created_at else "",
        "id": str(bet.id),
    }
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_feed_cursor(cursor: Optional[str]) -> tuple[datetime, UUID] | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * ((4 - len(cursor) % 4) % 4))
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        created_at = datetime.fromisoformat(str(payload["created_at"]))
        bet_id = UUID(str(payload["id"]))
        return created_at, bet_id
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный cursor ленты",
        )


def _feed_response_from_bet(bet: Bet, *, current_user: User, taken_bet_ids: set[UUID]) -> BetResponse:
    if is_staff_role(current_user.role):
        is_unlocked = True
    elif bet.price_stars and bet.price_stars > 0:
        is_unlocked = bet.id in taken_bet_ids
    else:
        is_unlocked = True

    p_bet = BetResponse.model_validate(bet)
    p_bet.is_unlocked = is_unlocked
    p_bet.is_taken = bet.id in taken_bet_ids

    if not is_unlocked:
        p_bet.event_name = "🔒 Прогноз скрыт до покупки"
        p_bet.outcome = "🔒 Скрыто"
        p_bet.description = f"Купите этот прогноз за {bet.price_stars} Stars, чтобы увидеть исход и описание."
        p_bet.match_link = None
        p_bet.coupon_image_url = None
        p_bet.api_match_id = None
        p_bet.brain_score = None

    return p_bet


def _format_decimal(value: Optional[Decimal]) -> str:
    try:
        return f"{Decimal(str(value)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):.2f}"
    except Exception:
        return str(value or "").strip()


def _normalize_publication_type(value: Optional[str]) -> str:
    normalized = (value or PUBLICATION_TYPE_FORECAST).strip().lower()
    if normalized not in PUBLICATION_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="publication_type must be forecast or text",
        )
    return normalized


def _is_forecast_publication(bet: Bet) -> bool:
    return str(getattr(bet, "publication_type", PUBLICATION_TYPE_FORECAST) or PUBLICATION_TYPE_FORECAST) == PUBLICATION_TYPE_FORECAST


def _validate_odds_dropped_to(value: Optional[Decimal]) -> Optional[Decimal]:
    if value is None:
        return None
    try:
        clean_value = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректный коэффициент",
        )
    if clean_value < Decimal("1.00") or clean_value > Decimal("999.99"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Коэффициент должен быть от 1.00 до 999.99",
        )
    return clean_value


def build_odds_drop_message(bet: Bet, *, template_body: Optional[str] = None) -> str:
    dropped_to = _format_decimal(bet.odds_dropped_to)
    original = _format_decimal(bet.coefficient)
    return render_message_template_body(
        template_body or default_message_template_body(TEMPLATE_ODDS_DROP),
        {
            "event_name": str(bet.event_name or "матч").strip(),
            "outcome": str(bet.outcome or "наш исход").strip(),
            "coefficient": original,
            "odds_dropped_to": dropped_to,
        },
    )


def _has_web_push_subscription(user: User) -> bool:
    subscription = getattr(user, "web_push_subscription", None)
    return isinstance(subscription, dict) and bool(subscription.get("endpoint"))


def _bet_event_signal_data(
    bet: Bet,
    *,
    html_message: str,
    plain_message: str,
    signal_type: str,
    push_title: str,
    extra_data: Optional[dict] = None,
) -> dict:
    data = {
        "bet_id": str(bet.id),
        "event_name": str(bet.event_name or "").strip(),
        "outcome": str(bet.outcome or "").strip(),
        "coefficient": _format_decimal(bet.coefficient),
        "sport_type": str(bet.sport_type or "").strip(),
        "message_html": html_message,
        "message_text": plain_message,
        "push_title": push_title,
        "push_body": plain_message,
        "event_type": signal_type,
    }
    if extra_data:
        data.update(extra_data)
    return data


async def _deliver_bet_personal_signal(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    html_message: str,
    signal_type: str,
    push_title: str,
    extra_data: Optional[dict] = None,
) -> str:
    plain_message = html_to_vk_text(html_message)
    await deliver_personal_signal(
        db,
        user=user,
        text=plain_message,
        signal_type=signal_type,
        data=_bet_event_signal_data(
            bet,
            html_message=html_message,
            plain_message=plain_message,
            signal_type=signal_type,
            push_title=push_title,
            extra_data=extra_data,
        ),
        send_telegram=False,
        send_web_push=_has_web_push_subscription(user),
    )
    return plain_message


def _bet_result_label(status_value: str) -> str:
    return {
        "win": "Победа",
        "loss": "Неудача",
        "refund": "Возврат",
    }.get(str(status_value or "").strip().lower(), "Результат")


async def _enqueue_bet_result_message(
    db: AsyncSession,
    *,
    user: User,
    bet: Bet,
    html_message: str,
    status_value: str,
    dedupe_suffix: str,
) -> None:
    plain_message = await _deliver_bet_personal_signal(
        db,
        user=user,
        bet=bet,
        html_message=html_message,
        signal_type="bet_result",
        push_title=f"Результат прогноза: {_bet_result_label(status_value)}",
        extra_data={
            "result_status": status_value,
            "result_label": _bet_result_label(status_value),
        },
    )

    if is_personal_telegram_user_id(user.telegram_id):
        await enqueue_delivery(
            db,
            channel=CHANNEL_TELEGRAM_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"bet_resolution:{bet.id}:{user.telegram_id}:{dedupe_suffix}",
            payload={
                "method": "sendMessage",
                "payload": {
                    "chat_id": user.telegram_id,
                    "text": html_message,
                    "parse_mode": "HTML",
                },
            },
        )

    if user_can_receive_vk_messages(user):
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"bet_resolution:{bet.id}:{user.telegram_id}:vk:{dedupe_suffix}",
            payload={
                "message": plain_message,
            },
        )


async def _load_bet_for_admin(db: AsyncSession, bet_id: UUID) -> Bet:
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден",
        )
    return bet


def _is_legacy_stopped_unresolved_private_bet(bet: Bet) -> bool:
    return (
        bet.status == "deleted"
        and bet.resolved_at is None
        and bet.delivery_mode in {"sales_private", "paid_set"}
    )


async def _load_odds_drop_recipients(db: AsyncSession, bet_id: UUID) -> List[User]:
    user_ids: set[int] = set()
    access_result = await db.execute(
        select(user_bets.c.user_id).filter(user_bets.c.bet_id == bet_id)
    )
    user_ids.update(int(row[0]) for row in access_result.all() if row[0] is not None)

    requests_result = await db.execute(
        select(ForecastRequest.user_id).filter(
            ForecastRequest.bet_id == bet_id,
            ForecastRequest.status.in_(ODDS_DROP_DELIVERED_FORECAST_STATUSES),
        )
    )
    user_ids.update(int(row[0]) for row in requests_result.all() if row[0] is not None)

    if not user_ids:
        return []

    users_result = await db.execute(
        select(User).filter(
            User.telegram_id.in_(user_ids),
            User.odds_drop_notifications_enabled.is_(True),
        )
    )
    users_by_id = {user.telegram_id: user for user in users_result.scalars().all()}
    return [
        users_by_id[user_id]
        for user_id in sorted(user_ids)
        if user_id in users_by_id and not is_staff_role(users_by_id[user_id].role)
    ]


async def _enqueue_odds_drop_message(db: AsyncSession, *, user: User, bet: Bet, html_message: str) -> dict:
    odds_value = _format_decimal(bet.odds_dropped_to)
    plain_message = await _deliver_bet_personal_signal(
        db,
        user=user,
        bet=bet,
        html_message=html_message,
        signal_type="odds_drop",
        push_title="Коэффициент упал",
    )
    queued_channels = ["web_chat"]
    if is_personal_telegram_user_id(user.telegram_id):
        await enqueue_delivery(
            db,
            channel=CHANNEL_TELEGRAM_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"odds_drop:{bet.id}:{user.telegram_id}:telegram:{odds_value}",
            payload={
                "method": "sendMessage",
                "payload": {
                    "chat_id": user.telegram_id,
                    "text": html_message,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            },
        )
        queued_channels.append("telegram")
    if user_can_receive_vk_messages(user):
        await enqueue_delivery(
            db,
            channel=CHANNEL_VK_MESSAGE,
            user_id=user.telegram_id,
            dedupe_key=f"odds_drop:{bet.id}:{user.telegram_id}:vk:{odds_value}",
            payload={
                "message": plain_message,
            },
        )
        queued_channels.append("vk")
    return {"ok": True, "queued": True, "channel": ",".join(queued_channels)}


async def _stop_open_forecast_requests_after_result(
    db: AsyncSession,
    *,
    bet: Bet,
    handled_by: int,
) -> int:
    if bet.delivery_mode not in {"sales_private", "paid_set"}:
        return 0

    bet.auto_send_on_interest = False
    stoppable_statuses = {
        FORECAST_STATUS_ANNOUNCED,
        FORECAST_STATUS_INTERESTED,
        FORECAST_STATUS_DECLINED,
        FORECAST_STATUS_CANCELLED,
    }
    requests_result = await db.execute(
        select(ForecastRequest).filter(
            ForecastRequest.bet_id == bet.id,
            ForecastRequest.status.in_(stoppable_statuses),
        )
    )
    stopped_requests = 0
    for forecast_request in requests_result.scalars().all():
        forecast_request.status = FORECAST_STATUS_REMOVED
        forecast_request.handled_by = handled_by
        stopped_requests += 1
    return stopped_requests


def _parse_bookmaker_id_values(values: Optional[List[str]]) -> List[int]:
    bookmaker_ids: List[int] = []
    for raw_value in values or []:
        if raw_value is None:
            continue
        for part in str(raw_value).split(","):
            clean = part.strip()
            if not clean:
                continue
            try:
                bookmaker_id = int(clean)
            except ValueError:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Некорректный ID букмекера"
                )
            if bookmaker_id not in bookmaker_ids:
                bookmaker_ids.append(bookmaker_id)
    return bookmaker_ids


def _merge_bookmaker_ids(
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
) -> List[int]:
    selected_ids: List[int] = []
    if bookmaker_id:
        selected_ids.append(bookmaker_id)
    for selected_id in bookmaker_ids or []:
        if selected_id and selected_id not in selected_ids:
            selected_ids.append(selected_id)
    return selected_ids


def _bookmaker_links_from_form(form_data) -> Optional[List[str]]:
    values = [
        str(value)
        for value in form_data.getlist("bookmaker_links")
        if value is not None
    ]
    values.extend(
        str(value)
        for value in form_data.getlist("bookmaker_links[]")
        if value is not None
    )
    return values if values else None


async def _store_coupon_image(coupon_image: Optional[UploadFile]) -> Optional[str]:
    return await store_coupon_image(coupon_image, target_dir=STATIC_COUPONS_DIR)


def _bet_hint_text(bet: Bet) -> str:
    if bet.description:
        return bet.description
    return (
        f"Shamrai Brain видит value в матче '{bet.event_name}': темп, линия и риск сходятся, "
        "но финальный исход остается закрытым до покупки прогноза."
    )


async def _build_bet_response(
    db: AsyncSession,
    admin: User,
    *,
    event_name: Optional[str],
    coefficient: Decimal,
    bookmaker_id: Optional[int],
    bookmaker_ids: Optional[List[int]],
    description: Optional[str],
    category: str,
    live_ends_at: Optional[datetime],
    price_stars: Optional[int],
    brain_score: Optional[int],
    api_match_id: Optional[str],
    sport_type: Optional[str],
    outcome: Optional[str],
    coupon_image_url: Optional[str],
    match_link: Optional[str],
    bookmaker_links: Optional[object] = None,
    delivery_mode: str = "feed",
    publication_type: str = PUBLICATION_TYPE_FORECAST,
) -> BetResponse:
    normalized_publication_type = _normalize_publication_type(publication_type)
    normalized_match_link = normalize_match_url(match_link)
    if match_link and not normalized_match_link:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Некорректная ссылка на матч",
        )
    selected_bookmaker_ids = _merge_bookmaker_ids(bookmaker_id, bookmaker_ids)
    selected_bookmakers = []
    if selected_bookmaker_ids:
        bk_res = await db.execute(select(Bookmaker).filter(Bookmaker.id.in_(selected_bookmaker_ids)))
        bookmakers_by_id = {bookmaker.id: bookmaker for bookmaker in bk_res.scalars().all()}
        missing_ids = [selected_id for selected_id in selected_bookmaker_ids if selected_id not in bookmakers_by_id]
        if missing_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Selected bookmaker not found: {', '.join(map(str, missing_ids))}"
            )
        selected_bookmakers = [bookmakers_by_id[selected_id] for selected_id in selected_bookmaker_ids]

    primary_bookmaker_id = selected_bookmaker_ids[0] if selected_bookmaker_ids else None

    bet = Bet(
        event_name=_event_name_or_placeholder(event_name),
        coefficient=coefficient,
        bookmaker_id=primary_bookmaker_id,
        description=description,
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=sport_type,
        outcome=outcome,
        coupon_image_url=coupon_image_url,
        match_link=normalized_match_link or None,
        bookmaker_links=normalize_bookmaker_links(
            bookmaker_links,
            allowed_bookmaker_ids=selected_bookmaker_ids,
        ),
        delivery_mode=delivery_mode,
        publication_type=normalized_publication_type,
        status="pending",
        author_id=admin.telegram_id
    )
    bet.bookmakers = selected_bookmakers
    db.add(bet)
    await db.commit()

    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet.id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    return result.scalars().first()


async def has_active_subscription(user: User, db: AsyncSession) -> bool:
    """Compatibility helper: access is now based on match balance or active guarantee."""
    return (
        is_staff_role(user.role)
        or (user.purchased_bets_balance or 0) > 0
        or (user.matches_remaining or 0) > 0
        or bool(user.guarantee_active)
    )

# --- SUBSCRIBER ENDPOINTS ---

@router.get("/feed", response_model=List[BetResponse])
async def get_bet_feed(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """
    Returns active ('pending') bets.
    Filters bets so that the user only sees those matching bookmakers in their profile.
    Computes is_unlocked status.
    """
    user_bk_ids = [bk.id for bk in current_user.bookmakers]
    
    # Filter: select only pending bets
    query = (
        select(Bet)
        .filter(Bet.status == "pending", Bet.delivery_mode == "feed")
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    
    if not is_staff_role(current_user.role):
        untargeted_filter = and_(Bet.bookmaker_id.is_(None), ~Bet.bookmakers.any())
        if user_bk_ids:
            query = query.filter(or_(
                untargeted_filter,
                Bet.bookmaker_id.in_(user_bk_ids),
                Bet.bookmakers.any(Bookmaker.id.in_(user_bk_ids)),
            ))
        else:
            # If no bookmakers selected, show only bets without a specific bookmaker required
            query = query.filter(untargeted_filter)
            
    query = query.order_by(Bet.created_at.desc())
    result = await db.execute(query)
    bets = result.scalars().all()
    
    has_access = await has_active_subscription(current_user, db)
    
    # Get all bet IDs currently unlocked/taken by the user
    taken_bets_res = await db.execute(
        select(user_bets.c.bet_id).filter(user_bets.c.user_id == current_user.telegram_id)
    )
    taken_bet_ids = {r[0] for r in taken_bets_res.all()}
    
    # Set virtual is_unlocked flag and construct response list
    response_bets = []
    for bet in bets:
        if is_staff_role(current_user.role):
            is_unlocked = True
        else:
            if bet.price_stars and bet.price_stars > 0:
                is_unlocked = bet.id in taken_bet_ids
            else:
                # Free bets/subscription bets are unlocked for everyone (or based on subscription)
                is_unlocked = True
                
        # Validate into Pydantic model
        p_bet = BetResponse.model_validate(bet)
        p_bet.is_unlocked = is_unlocked
        p_bet.is_taken = bet.id in taken_bet_ids
        
        # Mask details if locked
        if not is_unlocked:
            p_bet.event_name = "🔒 Прогноз скрыт до покупки"
            p_bet.outcome = "🔒 Скрыто"
            p_bet.description = f"Купите этот прогноз за {bet.price_stars} Stars, чтобы увидеть исход и описание."
            p_bet.match_link = None
            p_bet.coupon_image_url = None
            p_bet.api_match_id = None
            p_bet.brain_score = None
            
        response_bets.append(p_bet)
        
    return response_bets


@router.get("/feed-page")
async def get_bet_feed_page(
    limit: int = Query(20, ge=1, le=50),
    cursor: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    """Cursor-paginated feed for responsive clients."""
    user_bk_ids = [bk.id for bk in current_user.bookmakers]
    cursor_value = _decode_feed_cursor(cursor)

    query = (
        select(Bet)
        .filter(Bet.status == "pending", Bet.delivery_mode == "feed")
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )

    if not is_staff_role(current_user.role):
        untargeted_filter = and_(Bet.bookmaker_id.is_(None), ~Bet.bookmakers.any())
        if user_bk_ids:
            query = query.filter(or_(
                untargeted_filter,
                Bet.bookmaker_id.in_(user_bk_ids),
                Bet.bookmakers.any(Bookmaker.id.in_(user_bk_ids)),
            ))
        else:
            query = query.filter(untargeted_filter)

    if cursor_value:
        cursor_created_at, cursor_id = cursor_value
        query = query.filter(or_(
            Bet.created_at < cursor_created_at,
            and_(Bet.created_at == cursor_created_at, Bet.id < cursor_id),
        ))

    result = await db.execute(
        query.order_by(Bet.created_at.desc(), Bet.id.desc()).limit(limit + 1)
    )
    fetched_bets = result.scalars().all()
    page_bets = fetched_bets[:limit]
    has_more = len(fetched_bets) > limit

    taken_bet_ids: set[UUID] = set()
    page_bet_ids = [bet.id for bet in page_bets]
    if page_bet_ids:
        taken_bets_res = await db.execute(
            select(user_bets.c.bet_id).filter(
                user_bets.c.user_id == current_user.telegram_id,
                user_bets.c.bet_id.in_(page_bet_ids),
            )
        )
        taken_bet_ids = {row[0] for row in taken_bets_res.all()}

    return {
        "items": [
            _feed_response_from_bet(
                bet,
                current_user=current_user,
                taken_bet_ids=taken_bet_ids,
            ).model_dump(mode="json")
            for bet in page_bets
        ],
        "next_cursor": _encode_feed_cursor(page_bets[-1]) if has_more and page_bets else None,
        "has_more": has_more,
    }


@router.post("/{bet_id}/take", status_code=status.HTTP_200_OK)
async def take_bet(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """Adds a bet to the user's tracking list for stats calculation."""
    bet = await load_locked_bet_for_user_access(db, bet_id)
    ensure_bet_eligible_for_user(bet=bet, user=current_user)

    has_sub = await has_active_subscription(current_user, db)
    is_bet_free = (bet.price_stars is None or bet.price_stars == 0)

    if not has_sub and not is_bet_free:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Пополните абонемент матчами или купите этот прогноз за звезды"
        )

    access_result = await record_user_bet_access(
        db,
        user=current_user,
        bet=bet,
        charge_match=has_sub,
        allow_negative_balance=False,
        free_access_type="free_bet",
        note="Match debited when user added bet to My Bets",
    )
    await db.commit()
    if access_result.already_recorded:
        return {"status": "already_taken", "message": "Bet is already tracked in your profile"}
    return {"status": "success", "message": "Bet added to tracking list"}

@router.post("/{bet_id}/unlock_free", status_code=status.HTTP_200_OK)
async def unlock_free_bet(
    bet_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/bets/{bet_id}/unlock_free
    Unlocks a single bet using the user's free bet balance.
    """
    bet = await load_locked_bet_for_user_access(db, bet_id)
    ensure_bet_eligible_for_user(bet=bet, user=current_user)
    access_result = await record_user_free_bet_access(db, user=current_user, bet=bet)
    await db.commit()
    if access_result.already_recorded:
        return {"status": "already_unlocked", "message": "Прогноз уже открыт"}
    return {"status": "success", "message": "Прогноз успешно разблокирован!"}


@router.post("/{bet_id}/buy-hint", response_model=BetHintInvoiceResponse)
async def buy_bet_hint(
    bet_id: UUID,
    payload: BetHintRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /api/bets/{bet_id}/buy-hint
    Creates a Telegram Stars invoice for the analytical note.
    The note is returned only after a verified successful payment.
    """
    if payload.amount_xtr < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Стоимость подсказки должна быть больше 0 XTR"
        )

    bet = await load_locked_bet_for_user_access(db, bet_id)
    ensure_bet_eligible_for_user(bet=bet, user=current_user)
    await lock_user_balance(db, current_user.telegram_id)

    prior_attempts = await db.execute(
        select(PaymentAttempt).filter(
            PaymentAttempt.user_id == current_user.telegram_id,
            PaymentAttempt.bet_id == bet.id,
            PaymentAttempt.provider == "telegram_stars",
            PaymentAttempt.status.in_(("pending", "processing", "succeeded")),
        )
    )
    has_hint_attempt = any(
        (attempt.metadata_json or {}).get("purchase_type") == PAYMENT_PURCHASE_BET_HINT
        for attempt in prior_attempts.scalars().all()
    )
    if has_hint_attempt:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Оплата подсказки уже создана",
        )

    attempt = await _create_payment_attempt(
        db,
        user=current_user,
        provider="telegram_stars",
        amount=Decimal(BET_HINT_PRICE_XTR),
        currency="XTR",
        bet_id=bet.id,
        metadata={
            "purchase_type": PAYMENT_PURCHASE_BET_HINT,
            "requested_amount_xtr": int(payload.amount_xtr),
            "price_xtr": BET_HINT_PRICE_XTR,
        },
    )
    await db.commit()
    try:
        invoice_url = await create_telegram_stars_invoice_link(
            attempt=attempt,
            title="Подсказка Shamrai",
            description=f"Аналитическая подсказка по матчу: {bet.event_name}.",
            label="Подсказка Shamrai",
        )
    except Exception:
        attempt.status = "failed"
        await db.commit()
        raise

    return BetHintInvoiceResponse(
        bet_id=bet.id,
        attempt_id=attempt.id,
        invoice_url=invoice_url,
        price_xtr=BET_HINT_PRICE_XTR,
    )


@router.get("/{bet_id}/hint", response_model=BetHintResponse)
async def get_paid_bet_hint(
    bet_id: UUID,
    attempt_id: UUID = Query(...),
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db),
):
    result = await db.execute(
        select(PaymentAttempt, Bet)
        .join(Bet, PaymentAttempt.bet_id == Bet.id)
        .filter(
            PaymentAttempt.id == attempt_id,
            PaymentAttempt.user_id == current_user.telegram_id,
            PaymentAttempt.bet_id == bet_id,
            PaymentAttempt.provider == "telegram_stars",
            PaymentAttempt.status == "succeeded",
        )
    )
    row = result.first()
    if not row:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Подсказка доступна только после подтвержденной оплаты",
        )

    attempt, bet = row
    if (attempt.metadata_json or {}).get("purchase_type") != PAYMENT_PURCHASE_BET_HINT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Платеж не относится к подсказке",
        )
    # A verified successful payment is an immutable entitlement. Lifecycle and
    # audience were checked before invoice/pre-checkout; do not strand paid
    # content if the forecast resolves immediately after payment approval.

    return BetHintResponse(
        bet_id=bet.id,
        paid_xtr=int(Decimal(attempt.amount)),
        hint=_bet_hint_text(bet),
    )


@router.get("/stats", response_model=UserStats)
async def get_user_stats(
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """Calculates statistics for dashboard display."""
    query = (
        select(Bet, user_bets.c.access_type, user_bets.c.match_charged)
        .join(user_bets, user_bets.c.bet_id == Bet.id)
        .filter(
            user_bets.c.user_id == current_user.telegram_id,
            Bet.publication_type == PUBLICATION_TYPE_FORECAST,
            Bet.status.in_(["win", "loss", "refund"]),
            Bet.resolved_at.isnot(None),
        )
    )
    result = await db.execute(query)
    bets = [
        bet
        for bet, access_type, match_charged in result.all()
        if is_paid_client_access(access_type, match_charged)
    ]
    
    total = len(bets)
    won = 0
    lost = 0
    refunded = 0
    profit = Decimal("0.00")
    coefficient_sum = Decimal("0.00")
    
    for bet in bets:
        if bet.status == "win":
            won += 1
            profit += (bet.coefficient - Decimal("1.00"))
            coefficient_sum += bet.coefficient
        elif bet.status == "loss":
            lost += 1
            profit -= Decimal("1.00")
            coefficient_sum += bet.coefficient
        elif bet.status == "refund":
            refunded += 1
            
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / resolved * 100) if resolved > 0 else 0.0
    average_coefficient = float(coefficient_sum / Decimal(resolved)) if resolved > 0 else 0.0
    
    return UserStats(
        total_bets_taken=total,
        won_bets=won,
        lost_bets=lost,
        refund_bets=refunded,
        net_profit=profit,
        winrate=round(winrate, 2),
        roi=round(roi, 2),
        average_coefficient=round(average_coefficient, 2)
    )

# --- ADMIN ENDPOINTS ---

@router.post("/", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
@router.post("", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
async def create_bet(
    bet_data: BetCreate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Publish a new bet recommendation."""
    bet = await _build_bet_response(
        db,
        admin,
        event_name=bet_data.event_name,
        coefficient=bet_data.coefficient,
        bookmaker_id=bet_data.bookmaker_id,
        bookmaker_ids=bet_data.bookmaker_ids or bet_data.target_bookmaker_ids,
        description=bet_data.description,
        category=bet_data.category,
        live_ends_at=bet_data.live_ends_at,
        price_stars=bet_data.price_stars,
        brain_score=bet_data.brain_score,
        api_match_id=bet_data.api_match_id,
        sport_type=bet_data.sport_type,
        outcome=bet_data.outcome,
        coupon_image_url=bet_data.coupon_image_url,
        match_link=bet_data.match_link,
        bookmaker_links=bet_data.bookmaker_links,
        publication_type=bet_data.publication_type,
    )

    # Process Live Alarm
    if bet_data.live_alarm and _is_forecast_publication(bet):
        res_users = await db.execute(select(User))
        await broadcast_live_signal(
            db,
            users=res_users.scalars().all(),
            event_name=_event_name_or_placeholder(bet_data.event_name),
            coefficient=bet_data.coefficient,
            brain_score=bet_data.brain_score,
        )

    return bet


@router.post("/with-coupon", response_model=BetResponse, status_code=status.HTTP_201_CREATED)
async def create_bet_with_coupon(
    request: Request,
    event_name: Optional[str] = Form(None, max_length=200),
    coefficient: Decimal = Form(..., ge=Decimal("1.0"), le=Decimal("999.99")),
    bookmaker_id: Optional[int] = Form(None),
    description: Optional[str] = Form(None, max_length=4000),
    category: str = Form("prematch", max_length=40),
    live_ends_at: Optional[datetime] = Form(None),
    price_stars: Optional[int] = Form(None, ge=0, le=100000),
    brain_score: Optional[int] = Form(5, ge=0, le=100),
    api_match_id: Optional[str] = Form(None, max_length=160),
    sport_type: Optional[str] = Form(None, max_length=120),
    outcome: Optional[str] = Form(None, max_length=200),
    match_link: Optional[str] = Form(None, max_length=2048),
    publication_type: str = Form(PUBLICATION_TYPE_FORECAST, max_length=40),
    live_alarm: Optional[bool] = Form(False),
    coupon_image: Optional[UploadFile] = File(None),
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Admin-only: Publish a bet recommendation with an uploaded coupon screenshot."""
    coupon_image_url = await _store_coupon_image(coupon_image)
    form = await request.form()
    selected_bookmaker_ids = _parse_bookmaker_id_values(form.getlist("bookmaker_ids"))
    selected_bookmaker_ids.extend(
        selected_id
        for selected_id in _parse_bookmaker_id_values(form.getlist("target_bookmaker_ids"))
        if selected_id not in selected_bookmaker_ids
    )

    bet = await _build_bet_response(
        db,
        admin,
        event_name=event_name,
        coefficient=coefficient,
        bookmaker_id=bookmaker_id,
        bookmaker_ids=selected_bookmaker_ids,
        description=description,
        category=category,
        live_ends_at=live_ends_at,
        price_stars=price_stars,
        brain_score=brain_score,
        api_match_id=api_match_id,
        sport_type=sport_type,
        outcome=outcome,
        coupon_image_url=coupon_image_url,
        match_link=match_link,
        bookmaker_links=_bookmaker_links_from_form(form),
        publication_type=publication_type,
    )

    if live_alarm and _is_forecast_publication(bet):
        res_users = await db.execute(select(User))
        await broadcast_live_signal(
            db,
            users=res_users.scalars().all(),
            event_name=_event_name_or_placeholder(event_name),
            coefficient=coefficient,
            brain_score=brain_score,
        )

    return bet


@router.put("/{bet_id}", response_model=BetResponse)
async def update_bet(
    bet_id: UUID,
    bet_data: BetUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Staff-only: Edit forecast details for active and already settled forecasts."""
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Прогноз не найден",
        )
    if bet.status == "deleted" and not _is_legacy_stopped_unresolved_private_bet(bet):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нельзя редактировать удаленный прогноз",
        )

    update_payload = bet_data.model_dump(exclude_unset=True)

    if "event_name" in update_payload:
        bet.event_name = _event_name_or_placeholder(bet_data.event_name)

    if "coefficient" in update_payload:
        if bet_data.coefficient is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите коэффициент",
            )
        coefficient = _validate_odds_dropped_to(bet_data.coefficient)
        if coefficient is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Укажите коэффициент",
            )
        bet.coefficient = coefficient

    text_fields = ("sport_type", "outcome", "description", "match_link")
    for field_name in text_fields:
        if field_name in update_payload:
            value = getattr(bet_data, field_name)
            clean_value = str(value).strip() if value is not None else ""
            if field_name == "match_link" and clean_value:
                clean_value = normalize_match_url(clean_value)
                if not clean_value:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="Некорректная ссылка на матч",
                    )
            setattr(bet, field_name, clean_value or None)

    if "publication_type" in update_payload:
        bet.publication_type = _normalize_publication_type(bet_data.publication_type)

    selected_bookmaker_ids: Optional[List[int]] = None
    if "bookmaker_id" in update_payload or "bookmaker_ids" in update_payload:
        selected_bookmaker_ids = _merge_bookmaker_ids(
            bet_data.bookmaker_id,
            bet_data.bookmaker_ids or [],
        )
        selected_bookmakers: List[Bookmaker] = []
        if selected_bookmaker_ids:
            bookmakers_result = await db.execute(
                select(Bookmaker).filter(Bookmaker.id.in_(selected_bookmaker_ids))
            )
            bookmakers_by_id = {bookmaker.id: bookmaker for bookmaker in bookmakers_result.scalars().all()}
            missing_ids = [
                selected_id
                for selected_id in selected_bookmaker_ids
                if selected_id not in bookmakers_by_id
            ]
            if missing_ids:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Букмекер не найден: {', '.join(map(str, missing_ids))}",
                )
            selected_bookmakers = [bookmakers_by_id[selected_id] for selected_id in selected_bookmaker_ids]
        bet.bookmaker_id = selected_bookmaker_ids[0] if selected_bookmaker_ids else None
        bet.bookmakers = selected_bookmakers

    allowed_link_bookmaker_ids = (
        selected_bookmaker_ids
        if selected_bookmaker_ids is not None
        else [bookmaker.id for bookmaker in bet.bookmakers]
    )
    if "bookmaker_links" in update_payload:
        bet.bookmaker_links = normalize_bookmaker_links(
            bet_data.bookmaker_links or [],
            allowed_bookmaker_ids=allowed_link_bookmaker_ids,
        )
    elif "bookmaker_id" in update_payload or "bookmaker_ids" in update_payload:
        bet.bookmaker_links = normalize_bookmaker_links(
            bet.bookmaker_links or [],
            allowed_bookmaker_ids=allowed_link_bookmaker_ids,
        )

    await db.commit()
    return await _load_bet_for_admin(db, bet_id)


@router.put("/{bet_id}/odds-drop", response_model=BetResponse)
async def update_bet_odds_drop(
    bet_id: UUID,
    payload: BetOddsDropUpdate,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: Save the coefficient that the line dropped to after clients took the forecast."""
    bet = await _load_bet_for_admin(db, bet_id)
    bet.odds_dropped_to = _validate_odds_dropped_to(payload.odds_dropped_to)
    await db.commit()
    return await _load_bet_for_admin(db, bet_id)


@router.post("/{bet_id}/odds-drop/notify", response_model=BetOddsDropNotifyResponse)
async def notify_bet_odds_drop(
    bet_id: UUID,
    payload: BetOddsDropUpdate,
    admin: User = Depends(get_current_privileged_admin),
    db: AsyncSession = Depends(get_db),
):
    """Admin-only: Tell clients who already took this forecast that the line has dropped."""
    bet = await _load_bet_for_admin(db, bet_id)
    if payload.odds_dropped_to is not None:
        bet.odds_dropped_to = _validate_odds_dropped_to(payload.odds_dropped_to)
        await db.flush()
    if bet.odds_dropped_to is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Сначала укажите коэффициент в поле «Упал до»",
        )

    recipients = await _load_odds_drop_recipients(db, bet.id)
    if not recipients:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Нет клиентов, которым уже был выдан этот прогноз и разрешены уведомления о падении коэффициента",
        )

    odds_drop_template_body = await load_message_template_body(db, TEMPLATE_ODDS_DROP)
    html_message = build_odds_drop_message(bet, template_body=odds_drop_template_body)
    queued = 0
    failed = 0
    errors: List[str] = []
    for user in recipients:
        result = await _enqueue_odds_drop_message(db, user=user, bet=bet, html_message=html_message)
        if result.get("ok"):
            queued += 1
            continue
        failed += 1
        user_label = user.username or user.first_name or str(user.telegram_id)
        errors.append(f"{user_label}: {result.get('description', 'unknown error')}")

    if queued:
        bet.odds_drop_notified_at = datetime.now(timezone.utc)
    await db.commit()
    refreshed_bet = await _load_bet_for_admin(db, bet_id)
    return BetOddsDropNotifyResponse(
        bet=BetResponse.model_validate(refreshed_bet),
        total=len(recipients),
        sent=0,
        queued=queued,
        failed=failed,
        errors=errors[:10],
    )

@router.put("/{bet_id}/resolve", response_model=BetResponse)
async def resolve_bet(
    bet_id: UUID,
    resolution: BetResolve,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """Staff-only: Settle or correct bet result as win, loss, or refund."""
    if resolution.status not in ["win", "loss", "refund"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resolution status must be one of: win, loss, refund"
        )
        
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .with_for_update()
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    bet = result.scalars().first()
    if not bet:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Bet not found"
        )
    if not _is_forecast_publication(bet):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Текстовую публикацию нельзя рассчитать как ставку",
        )
        
    previous_status = bet.status
    previous_resolved_at = bet.resolved_at
    is_first_resolution = previous_status == "pending" or (
        previous_status == "deleted"
        and previous_resolved_at is None
        and bet.delivery_mode in {"sales_private", "paid_set"}
    )
    bet.status = resolution.status
    bet.resolved_at = datetime.now(timezone.utc)
    await _stop_open_forecast_requests_after_result(
        db,
        bet=bet,
        handled_by=admin.telegram_id,
    )

    supercompensation_count = 0
    refund_count = 0
    if is_first_resolution:
        result_template_body = None
        if resolution.status == "win":
            result_template_body = await load_message_template_body(db, TEMPLATE_BET_WIN)
        elif resolution.status == "loss":
            result_template_body = await load_message_template_body(db, TEMPLATE_BET_LOSS)
            supercompensation_template_body = await load_message_template_body(db, TEMPLATE_BET_LOSS_SUPERCOMPENSATION)
        elif resolution.status == "refund":
            result_template_body = await load_message_template_body(db, TEMPLATE_BET_REFUND)

        takers_res = await db.execute(
            select(
                user_bets.c.user_id,
                user_bets.c.match_charged,
                user_bets.c.access_type,
            ).filter(user_bets.c.bet_id == bet_id)
        )
        takers = takers_res.all()
        taker_ids = [row[0] for row in takers]
        users_by_id = {}
        if taker_ids:
            users_res = await db.execute(select(User).filter(User.telegram_id.in_(taker_ids)))
            users_by_id = {u.telegram_id: u for u in users_res.scalars().all()}

        for user_id, match_charged, access_type in sorted(takers, key=lambda row: int(row[0])):
            user = users_by_id.get(user_id)
            if not user or is_staff_role(user.role):
                continue

            if resolution.status == "loss" and match_charged and access_type == "paid_match":
                locked_balance = await lock_user_balance(db, user.telegram_id)
                current_balance = current_match_balance(locked_balance)
                next_balance = current_balance + 2
                user.purchased_bets_balance = next_balance
                user.matches_remaining = next_balance
                supercompensation_count += 1
                db.add(log_match_balance_event(
                    user_id=user_id,
                    bet_id=bet_id,
                    event_type="supercompensation_loss",
                    delta_matches=2,
                    note="Loss supercompensation: charged stake returned and +1 bonus stake added",
                ))
                message_text = render_message_template_body(
                    supercompensation_template_body,
                    {"event_name": bet.event_name},
                )
                await _enqueue_bet_result_message(
                    db,
                    user=user,
                    bet=bet,
                    html_message=message_text,
                    status_value="loss",
                    dedupe_suffix="loss_supercompensation",
                )
            elif resolution.status == "loss":
                message_text = render_message_template_body(
                    result_template_body,
                    {"event_name": bet.event_name},
                )
                await _enqueue_bet_result_message(
                    db,
                    user=user,
                    bet=bet,
                    html_message=message_text,
                    status_value="loss",
                    dedupe_suffix="loss",
                )
            elif resolution.status == "win":
                message_text = render_message_template_body(
                    result_template_body,
                    {"event_name": bet.event_name},
                )
                await _enqueue_bet_result_message(
                    db,
                    user=user,
                    bet=bet,
                    html_message=message_text,
                    status_value="win",
                    dedupe_suffix="win",
                )
            elif resolution.status == "refund":
                refund_count += 1
                message_text = render_message_template_body(
                    result_template_body,
                    {"event_name": bet.event_name},
                )
                await _enqueue_bet_result_message(
                    db,
                    user=user,
                    bet=bet,
                    html_message=message_text,
                    status_value="refund",
                    dedupe_suffix="refund",
                )

        client_taker_count = await count_client_bet_takers(db, bet_id)
        await enqueue_admin_group_forecast_result_notification(
            db,
            bet=bet,
            status_value=resolution.status,
            taker_count=client_taker_count,
        )

    await db.commit()

    # Trigger badges achievements calculation for each user who took this bet
    if resolution.status == "win":
        # Fetch user IDs of all takers
        takers_res = await db.execute(
            select(user_bets.c.user_id).filter(user_bets.c.bet_id == bet_id)
        )
        taker_ids = [r[0] for r in takers_res.all()]
        
        from src.models.models import UserBadge
        for u_id in taker_ids:
            # Query user's last 5 resolved taken bets
            last_bets_query = (
                select(Bet.status)
                .join(user_bets)
                .filter(
                    user_bets.c.user_id == u_id,
                    Bet.publication_type == PUBLICATION_TYPE_FORECAST,
                    Bet.status.in_(["win", "loss", "refund"]),
                )
                .order_by(user_bets.c.taken_at.desc())
                .limit(5)
            )
            last_bets_res = await db.execute(last_bets_query)
            statuses = last_bets_res.scalars().all()
            
            # Check if they have 5 consecutive wins
            if len(statuses) >= 5 and all(s == "win" for s in statuses):
                # Check if badge already exists
                badge_check = await db.execute(
                    select(UserBadge).filter(UserBadge.user_id == u_id, UserBadge.icon_type == "sharp_mind")
                )
                if not badge_check.scalars().first():
                    # Award the badge
                    new_badge = UserBadge(
                        user_id=u_id,
                        title="Острый Ум",
                        icon_type="sharp_mind"
                    )
                    db.add(new_badge)
                    
        await db.commit()
    
    result = await db.execute(
        select(Bet)
        .filter(Bet.id == bet_id)
        .options(selectinload(Bet.bookmaker), selectinload(Bet.bookmakers))
    )
    resolved_bet = result.scalars().first()
    resolved_bet.guarantee_count = supercompensation_count
    resolved_bet.supercompensation_count = supercompensation_count
    resolved_bet.refund_count = refund_count
    return resolved_bet

@router.get("/analytics", response_model=AdminAnalytics)
async def get_admin_analytics(
    admin: User = Depends(get_current_admin_read),
    db: AsyncSession = Depends(get_read_db)
):
    """Admin-only: Fetch platform-wide analytics."""
    user_count_res = await db.execute(select(func.count(User.telegram_id)))
    total_users = user_count_res.scalar() or 0
    
    sub_count_res = await db.execute(
        select(func.count(User.telegram_id)).filter(
            (User.purchased_bets_balance > 0) | (User.matches_remaining > 0) | (User.guarantee_active == True)
        )
    )
    active_subs = sub_count_res.scalar() or 0
    
    bets_res = await db.execute(select(Bet).filter(Bet.publication_type == PUBLICATION_TYPE_FORECAST))
    bets = bets_res.scalars().all()
    
    total_bets = len(bets)
    won = 0
    lost = 0
    profit = Decimal("0.00")
    coefficient_sum = Decimal("0.00")
    
    for bet in bets:
        if bet.status == "win":
            won += 1
            profit += (bet.coefficient - Decimal("1.00"))
            coefficient_sum += bet.coefficient
        elif bet.status == "loss":
            lost += 1
            profit -= Decimal("1.00")
            coefficient_sum += bet.coefficient
            
    resolved = won + lost
    winrate = (won / resolved * 100) if resolved > 0 else 0.0
    roi = (float(profit) / resolved * 100) if resolved > 0 else 0.0
    average_coefficient = float(coefficient_sum / Decimal(resolved)) if resolved > 0 else 0.0
    
    return AdminAnalytics(
        total_subscribers=total_users,
        active_subscriptions=active_subs,
        total_bets_issued=total_bets,
        winrate=round(winrate, 2),
        roi=round(roi, 2),
        net_profit=profit,
        average_coefficient=round(average_coefficient, 2)
    )


from src.models.models import UserNote
from src.schemas.schemas import UserNoteCreate, UserNoteResponse
from typing import Optional

@router.get("/{bet_id}/notes", response_model=Optional[UserNoteResponse])
async def get_bet_note(
    bet_id: UUID,
    current_user: User = Depends(get_current_user_read),
    db: AsyncSession = Depends(get_read_db)
):
    """GET /api/bets/{bet_id}/notes — Retrieve capper diary note for this prediction."""
    query = select(UserNote).filter(UserNote.user_id == current_user.telegram_id, UserNote.bet_id == bet_id)
    res = await db.execute(query)
    return res.scalars().first()


@router.post("/{bet_id}/notes", response_model=UserNoteResponse)
async def save_bet_note(
    bet_id: UUID,
    note_data: UserNoteCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """POST /api/bets/{bet_id}/notes — Create or update capper diary note for this prediction."""
    query = select(UserNote).filter(UserNote.user_id == current_user.telegram_id, UserNote.bet_id == bet_id)
    res = await db.execute(query)
    note = res.scalars().first()
    
    if note:
        note.text = note_data.text
        note.emotion_score = note_data.emotion_score
    else:
        note = UserNote(
            user_id=current_user.telegram_id,
            bet_id=bet_id,
            text=note_data.text,
            emotion_score=note_data.emotion_score
        )
        db.add(note)
        
    await db.commit()
    await db.refresh(note)
    return note


@router.get("/match/{api_match_id}")
async def get_live_match_score(api_match_id: str):
    """
    GET /api/bets/match/{api_match_id}
    Debug-only live score simulator. Production must use a real live-score provider.
    """
    if not settings.DEBUG_MODE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live score provider is not configured",
        )

    import random
    import time
    
    seed_val = sum(ord(c) for c in api_match_id)
    random.seed(seed_val)
    
    teams = [
        ("Реал Мадрид", "Бавария"),
        ("Ливерпуль", "Манчестер Сити"),
        ("Барселона", "ПСЖ"),
        ("Арсенал", "Челси"),
        ("Ювентус", "Милан")
    ]
    
    team_a, team_b = random.choice(teams)
    
    # Minute increases based on time
    sec = int(time.time())
    minute = 65 + (sec % 25) # simulates min 65 to 90
    
    score_a = (seed_val + (sec // 60)) % 3
    score_b = (seed_val // 2 + (sec // 90)) % 3
    
    return {
        "api_match_id": api_match_id,
        "team_a": team_a,
        "team_b": team_b,
        "score_a": score_a,
        "score_b": score_b,
        "minute": minute,
        "status": "Live" if minute < 90 else "Finished"
    }
