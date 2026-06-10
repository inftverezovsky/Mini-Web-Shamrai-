from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID

# --- BOOKMAKER SCHEMAS ---
class BookmakerBase(BaseModel):
    name: str
    code: str
    is_active: bool = True

class BookmakerResponse(BookmakerBase):
    id: int

    class Config:
        from_attributes = True

# --- BADGE SCHEMAS ---
class UserBadgeResponse(BaseModel):
    id: int
    user_id: int
    title: str
    icon_type: str
    unlocked_at: datetime

    class Config:
        from_attributes = True

# --- USER SCHEMAS ---
class UserBase(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    photo_url: Optional[str] = None
    is_web_only: bool = False

class UserCreate(UserBase):
    role: str = "user"

class UserResponse(UserBase):
    role: str
    stats_display_mode: str
    bankroll: float
    is_onboarded: bool = False
    experience_level: Optional[str] = None
    bankroll_size: Optional[str] = None
    favorite_sports: List[str] = Field(default_factory=list)
    risk_tolerance: Optional[str] = None
    primary_bookmaker: Optional[str] = None
    vk_user_id: Optional[str] = None
    vk_group_member: bool = False
    vk_messages_allowed: bool = False
    vk_notifications_allowed: bool = False
    currency_preference: str = "RUB"
    purchased_bets_balance: int = 0
    free_bets_available: int = 0
    matches_remaining: int = 0
    guarantee_active: bool = False
    guarantee_opened_from_bet_id: Optional[UUID] = None
    guarantee_closed_at: Optional[datetime] = None
    onboarding_goal: Optional[str] = None
    ab_group: Optional[str] = None
    tg_chat_joined: bool = False
    has_used_shield: bool = False
    alert_min_coef: float = 1.0
    is_night_mode: bool = False
    night_mode_start: str = "23:00"
    night_mode_end: str = "08:00"
    preferred_sports: List[str] = Field(default_factory=list)
    other_bookmaker_name: Optional[str] = None
    client_group: Optional[str] = None
    client_tag: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    bookmakers: List[BookmakerResponse] = Field(default_factory=list)
    badges: List[UserBadgeResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True

class OnboardRequest(BaseModel):
    anti_capper_pains: List[str] = Field(default_factory=list)
    experience_level: str
    bankroll_size: str
    risk_tolerance: str
    bookmakers: List[str] = Field(default_factory=list)
    primary_bookmaker: Optional[str] = None
    vk_user_id: Optional[str] = None
    other_bookmaker_name: Optional[str] = None
    bookmaker_ids: List[int] = Field(default_factory=list)
    currency_preference: str = "RUB"


class OnboardRecommendationResponse(BaseModel):
    flat_stake_percent: float
    monthly_profit_percent: float
    missed_profit_percent_24h: float
    missed_profit_amount_24h: float
    currency: str
    source: str
    resolved_bets_24h: int


class OnboardResponse(BaseModel):
    status: str
    message: str
    recommendation: OnboardRecommendationResponse
    user: UserResponse

class UserUpdateBankroll(BaseModel):
    bankroll: float

class UserUpdateBookmakers(BaseModel):
    bookmaker_ids: List[int]
    other_bookmaker_name: Optional[str] = None

class AdminUpdateUserPreferences(BaseModel):
    role: Optional[str] = None  # "owner" | "admin" | "moderator" | "user"
    stats_display_mode: Optional[str] = None  # "percent" | "flat"
    bookmaker_ids: Optional[List[int]] = None
    subscription_end_date: Optional[datetime] = None
    matches_delta: Optional[int] = None
    close_guarantee: Optional[bool] = None
    other_bookmaker_name: Optional[str] = None
    client_group: Optional[str] = None
    client_tag: Optional[str] = None

# --- SUBSCRIPTION PLAN SCHEMAS ---
class SubscriptionPlanBase(BaseModel):
    name: str
    duration_days: int = 0
    match_count: int = 1
    price: Decimal
    price_stars: int = 0
    currency: str = "RUB"
    is_active: bool = True

class SubscriptionPlanCreate(SubscriptionPlanBase):
    pass


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    match_count: Optional[int] = None
    price: Optional[Decimal] = None
    price_stars: Optional[int] = None
    currency: Optional[str] = None
    is_active: Optional[bool] = None

class SubscriptionPlanResponse(SubscriptionPlanBase):
    id: int

    class Config:
        from_attributes = True

# --- SUBSCRIPTION SCHEMAS ---
class SubscriptionBase(BaseModel):
    user_id: int
    plan_id: int
    status: str = "pending"
    payment_provider: Optional[str] = None
    payment_id: Optional[str] = None

class SubscriptionCreate(BaseModel):
    plan_id: int

class SubscriptionResponse(SubscriptionBase):
    id: UUID
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    created_at: datetime
    plan: Optional[SubscriptionPlanResponse] = None

    class Config:
        from_attributes = True

class SubscriptionManualAssign(BaseModel):
    user_id: int
    plan_id: int
    duration_days: Optional[int] = None  # Override plan duration if specified

# --- BET SCHEMAS ---
class BookmakerLink(BaseModel):
    bookmaker_id: int
    url: str


class BetBase(BaseModel):
    event_name: str
    coefficient: Decimal
    bookmaker_id: Optional[int] = None
    bookmaker_ids: List[int] = Field(default_factory=list)
    description: Optional[str] = None
    category: str = "prematch"
    live_ends_at: Optional[datetime] = None
    price_stars: Optional[int] = None
    brain_score: Optional[int] = None
    api_match_id: Optional[str] = None
    sport_type: Optional[str] = None
    outcome: Optional[str] = None
    coupon_image_url: Optional[str] = None
    match_link: Optional[str] = None
    bookmaker_links: List[BookmakerLink] = Field(default_factory=list)
    delivery_mode: str = "feed"
    auto_send_on_interest: bool = False

class BetCreate(BetBase):
    target_bookmaker_ids: Optional[List[int]] = None  # Specific list of bookmaker IDs for target audience filtering (optional)
    live_alarm: Optional[bool] = None

class BetResolve(BaseModel):
    status: str  # "win" | "loss" | "refund"


class BetOddsDropUpdate(BaseModel):
    odds_dropped_to: Optional[Decimal] = None


class BetResponse(BetBase):
    id: UUID
    status: str
    created_at: datetime
    resolved_at: Optional[datetime] = None
    author_id: Optional[int] = None
    bookmaker: Optional[BookmakerResponse] = None
    bookmakers: List[BookmakerResponse] = Field(default_factory=list)
    price_stars: Optional[int] = None
    is_unlocked: bool = False
    is_taken: bool = False
    guarantee_count: int = 0
    supercompensation_count: int = 0
    refund_count: int = 0
    odds_dropped_to: Optional[Decimal] = None
    odds_drop_notified_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class BetOddsDropNotifyResponse(BaseModel):
    bet: BetResponse
    total: int
    sent: int
    failed: int
    errors: List[str] = Field(default_factory=list)


class ForecastRequestUserResponse(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None
    is_web_only: bool = False
    matches_remaining: int = 0
    guarantee_active: bool = False
    bookmakers: List[BookmakerResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class ForecastRequestResponse(BaseModel):
    id: UUID
    bet_id: UUID
    user_id: int
    status: str
    delivery_method: Optional[str] = None
    handled_by: Optional[int] = None
    responded_at: Optional[datetime] = None
    delivered_at: Optional[datetime] = None
    balance_before: Optional[int] = None
    balance_after: Optional[int] = None
    no_balance_warning: bool = False
    created_at: datetime
    updated_at: datetime
    bet: BetResponse
    user: ForecastRequestUserResponse

    class Config:
        from_attributes = True


class BetHintRequest(BaseModel):
    amount_xtr: int = 20


class BetHintResponse(BaseModel):
    bet_id: UUID
    paid_xtr: int
    hint: str
    reveal_level: str = "analysis_only"


class CrowdBetFundRequest(BaseModel):
    amount_xtr: int


class CrowdBetResponse(BaseModel):
    id: int
    bet_id: UUID
    target_amount: int
    current_amount: int
    status: str
    progress_percent: float
    is_participant: bool = False

    class Config:
        from_attributes = True


class SwipeCandidateResponse(BaseModel):
    bet_id: UUID
    match_name: str
    bookmaker_name: Optional[str] = None
    coefficient: Decimal
    options: List[str]


class SwipeRequest(BaseModel):
    bet_id: Optional[UUID] = None
    guess: str


class SwipeResponse(BaseModel):
    match: bool
    discount: int
    promo_code: Optional[str] = None
    message: str


class QuizQuestionResponse(BaseModel):
    id: str
    question: str
    options: List[str]


class QuizActiveResponse(BaseModel):
    id: int
    bet_id: UUID
    discount_reward: int
    questions: List[QuizQuestionResponse]


class QuizSubmitRequest(BaseModel):
    quiz_id: Optional[int] = None
    bet_id: Optional[UUID] = None
    answers: Dict[str, str]


class QuizSubmitResponse(BaseModel):
    passed: bool
    score: int
    total: int
    discount: int
    promo_code: Optional[str] = None
    message: str


class PvPBattleResponse(BaseModel):
    id: int
    match_name: str
    option_a: str
    option_b: str
    votes_a: int
    votes_b: int
    percent_a: float
    percent_b: float

    class Config:
        from_attributes = True


class PvPVoteRequest(BaseModel):
    battle_id: Optional[int] = None
    option: str


class PvPVoteResponse(PvPBattleResponse):
    selected_option: str
    message: str

# --- STATS AND ANALYTICS SCHEMAS ---
class UserStats(BaseModel):
    total_bets_taken: int
    won_bets: int
    lost_bets: int
    refund_bets: int
    net_profit: Decimal  # calculated assuming unit stakes
    winrate: float
    roi: float
    average_coefficient: float

class AdminAnalytics(BaseModel):
    total_subscribers: int
    active_subscriptions: int
    total_bets_issued: int
    winrate: float
    roi: float
    net_profit: Decimal
    average_coefficient: float

# --- MARKETING HUB SCHEMAS ---
class MarathonResponse(BaseModel):
    id: int
    title: str
    target_multiplier: float
    current_step: int
    total_steps: int
    is_active: bool

    class Config:
        from_attributes = True

class LivePulseLogResponse(BaseModel):
    id: int
    text_message: str
    created_at: datetime

    class Config:
        from_attributes = True

class DailyRewardClaimResponse(BaseModel):
    id: int
    user_id: int
    claimed_at: datetime

    class Config:
        from_attributes = True


class UserNoteCreate(BaseModel):
    text: str
    emotion_score: int  # 1-5


class UserNoteResponse(BaseModel):
    id: int
    user_id: int
    bet_id: UUID
    text: str
    emotion_score: int
    created_at: datetime

    class Config:
        from_attributes = True


class ABTestConfigCreate(BaseModel):
    plan_id: int
    price_group_a: int
    price_group_b: int
    is_active: bool = True


class ABTestConfigResponse(BaseModel):
    id: int
    plan_id: int
    price_group_a: int
    price_group_b: int
    is_active: bool

    class Config:
        from_attributes = True


class ClientRecentMatchResult(BaseModel):
    bet_id: UUID
    status: str
    taken_at: Optional[datetime] = None


class AdminUserListResponse(BaseModel):
    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    photo_url: Optional[str] = None
    is_web_only: bool = False
    role: str
    stats_display_mode: str
    has_active_subscription: bool
    subscription_end_date: Optional[datetime]
    purchased_bets_balance: int = 0
    matches_remaining: int = 0
    guarantee_active: bool = False
    guarantee_opened_from_bet_id: Optional[UUID] = None
    guarantee_closed_at: Optional[datetime] = None
    bookmakers: List[BookmakerResponse]
    other_bookmaker_name: Optional[str] = None
    client_group: Optional[str] = None
    client_tag: Optional[str] = None
    ab_group: Optional[str] = None
    tg_chat_joined: bool = False
    badges: List[UserBadgeResponse] = Field(default_factory=list)
    recent_match_results: List[ClientRecentMatchResult] = Field(default_factory=list)

    class Config:
        from_attributes = True


class AdminGrantRequest(BaseModel):
    telegram_id: int
    role: str = "admin"
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class AdminAuditLogResponse(BaseModel):
    id: int
    actor_id: Optional[int] = None
    actor_username: Optional[str] = None
    actor_first_name: Optional[str] = None
    target_user_id: Optional[int] = None
    target_username: Optional[str] = None
    target_first_name: Optional[str] = None
    action: str
    details: Dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# --- USER PREFERENCES SCHEMAS ---
class UserPreferencesUpdate(BaseModel):
    alert_min_coef: Optional[float] = Field(default=None, ge=1.0, le=1.6)
    is_night_mode: Optional[bool] = None
    night_mode_start: Optional[str] = None
    night_mode_end: Optional[str] = None
    preferred_sports: Optional[List[str]] = None
    stats_display_mode: Optional[str] = None  # "percent" | "flat"


class UserPreferencesResponse(BaseModel):
    alert_min_coef: float
    is_night_mode: bool
    night_mode_start: str
    night_mode_end: str
    preferred_sports: List[str]
    stats_display_mode: str

    class Config:
        from_attributes = True


# --- PAYMENT TRANSACTION SCHEMAS ---
class PaymentTransactionResponse(BaseModel):
    id: UUID
    plan_name: Optional[str] = None
    amount: Optional[str] = None
    payment_provider: Optional[str] = None
    status: str
    created_at: datetime
    end_date: Optional[datetime] = None

    class Config:
        from_attributes = True


# --- ANNOUNCEMENT SCHEMAS ---
class AnnouncementCreate(BaseModel):
    title: str
    body: Optional[str] = None
    announcement_type: str = "general"
    sport_filter: Optional[str] = None
    min_coef: Optional[float] = None
    match_link: Optional[str] = None


class AnnouncementDeliveryResponse(BaseModel):
    total_audience: int
    sent: int
    failed: int
