from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Any, Dict, List, Optional
from datetime import datetime
from decimal import Decimal
from uuid import UUID
from urllib.parse import urlparse


def _validate_http_or_relative_url(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    url = value.strip()
    if not url:
        return url
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    return url

# --- BOOKMAKER SCHEMAS ---
class BookmakerBase(BaseModel):
    name: str = Field(max_length=120)
    code: str = Field(max_length=80)
    is_active: bool = True

class BookmakerResponse(BookmakerBase):
    id: int

    model_config = ConfigDict(from_attributes=True)

# --- BADGE SCHEMAS ---
class UserBadgeResponse(BaseModel):
    id: int
    user_id: int
    title: str
    icon_type: str
    unlocked_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- USER SCHEMAS ---
class UserBase(BaseModel):
    telegram_id: int
    username: Optional[str] = Field(default=None, max_length=64)
    first_name: Optional[str] = Field(default=None, max_length=128)
    last_name: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    photo_url: Optional[str] = Field(default=None, max_length=2048)
    vk_photo_url: Optional[str] = Field(default=None, max_length=2048)
    is_web_only: bool = False

class UserCreate(UserBase):
    role: str = "user"

class UserResponse(UserBase):
    role: str
    identity_complete: bool = False
    identity_providers: List[str] = Field(default_factory=list)
    missing_identity_providers: List[str] = Field(default_factory=list)
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
    odds_drop_notifications_enabled: bool = True
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

    model_config = ConfigDict(from_attributes=True)

class OnboardRequest(BaseModel):
    anti_capper_pains: List[str] = Field(default_factory=list, max_length=30)
    onboarding_goal: Optional[str] = Field(default=None, max_length=80)
    experience_level: str = Field(max_length=80)
    bankroll_size: str = Field(max_length=80)
    risk_tolerance: str = Field(max_length=80)
    bookmakers: List[str] = Field(default_factory=list, max_length=50)
    primary_bookmaker: Optional[str] = Field(default=None, max_length=120)
    vk_user_id: Optional[str] = Field(default=None, max_length=80)
    other_bookmaker_name: Optional[str] = Field(default=None, max_length=120)
    service_format: Optional[str] = Field(default="auto_fast", max_length=80)
    bookmaker_ids: List[int] = Field(default_factory=list, max_length=50)
    favorite_sports: List[str] = Field(default_factory=list, max_length=30)
    currency_preference: str = Field(default="RUB", max_length=12)


class OnboardRecommendationResponse(BaseModel):
    flat_stake_percent: float
    monthly_profit_percent: float
    missed_profit_percent_24h: float
    missed_profit_amount_24h: float
    currency: str
    source: str
    resolved_bets_24h: int
    service_format_label: str
    vip_verdict_title: str
    vip_verdict_caption: str


class OnboardResponse(BaseModel):
    status: str
    message: str
    recommendation: OnboardRecommendationResponse
    user: UserResponse

class UserUpdateBankroll(BaseModel):
    bankroll: float = Field(ge=0, le=1_000_000_000)

class UserUpdateBookmakers(BaseModel):
    bookmaker_ids: List[int] = Field(max_length=50)
    other_bookmaker_name: Optional[str] = Field(default=None, max_length=120)

class AdminUpdateUserPreferences(BaseModel):
    role: Optional[str] = Field(default=None, max_length=32)  # "owner" | "admin" | "moderator" | "user"
    stats_display_mode: Optional[str] = Field(default=None, max_length=32)  # "percent" | "flat"
    bookmaker_ids: Optional[List[int]] = Field(default=None, max_length=50)
    subscription_end_date: Optional[datetime] = None
    matches_delta: Optional[int] = Field(default=None, ge=-10000, le=10000)
    close_guarantee: Optional[bool] = None
    other_bookmaker_name: Optional[str] = Field(default=None, max_length=120)
    client_group: Optional[str] = Field(default=None, max_length=80)
    client_tag: Optional[str] = Field(default=None, max_length=80)

# --- SUBSCRIPTION PLAN SCHEMAS ---
class SubscriptionPlanBase(BaseModel):
    name: str = Field(max_length=120)
    duration_days: int = Field(default=0, ge=0, le=3650)
    match_count: int = Field(default=1, ge=1, le=10000)
    price: Decimal = Field(ge=0, le=Decimal("10000000"))
    price_stars: int = Field(default=0, ge=0, le=1000000)
    currency: str = Field(default="RUB", max_length=12)
    is_active: bool = True

class SubscriptionPlanCreate(SubscriptionPlanBase):
    pass


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    match_count: Optional[int] = Field(default=None, ge=1, le=10000)
    price: Optional[Decimal] = Field(default=None, ge=0, le=Decimal("10000000"))
    price_stars: Optional[int] = Field(default=None, ge=0, le=1000000)
    currency: Optional[str] = Field(default=None, max_length=12)
    is_active: Optional[bool] = None

class SubscriptionPlanResponse(SubscriptionPlanBase):
    id: int

    model_config = ConfigDict(from_attributes=True)

# --- SUBSCRIPTION SCHEMAS ---
class SubscriptionBase(BaseModel):
    user_id: int
    plan_id: Optional[int] = None
    status: str = Field(default="pending", max_length=32)
    payment_provider: Optional[str] = Field(default=None, max_length=80)
    payment_id: Optional[str] = Field(default=None, max_length=160)

class SubscriptionCreate(BaseModel):
    plan_id: int

class SubscriptionResponse(SubscriptionBase):
    id: UUID
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    created_at: datetime
    plan: Optional[SubscriptionPlanResponse] = None

    model_config = ConfigDict(from_attributes=True)

class SubscriptionManualAssign(BaseModel):
    user_id: int
    plan_id: int
    duration_days: Optional[int] = None  # Override plan duration if specified

# --- BET SCHEMAS ---
class BookmakerLink(BaseModel):
    bookmaker_id: int = Field(ge=1)
    url: str = Field(max_length=2048)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_http_or_relative_url(value) or ""


class BetBase(BaseModel):
    event_name: Optional[str] = Field(default=None, max_length=200)
    coefficient: Decimal = Field(ge=Decimal("1.0"), le=Decimal("999.99"))
    fair_coefficient: Optional[Decimal] = Field(default=None, ge=Decimal("1.0"), le=Decimal("999.99"))
    bookmaker_id: Optional[int] = None
    bookmaker_ids: List[int] = Field(default_factory=list, max_length=50)
    description: Optional[str] = Field(default=None, max_length=4000)
    teaser_text: Optional[str] = Field(default=None, max_length=4000)
    category: str = Field(default="prematch", max_length=40)
    live_ends_at: Optional[datetime] = None
    price_stars: Optional[int] = Field(default=None, ge=0, le=100000)
    brain_score: Optional[int] = Field(default=None, ge=0, le=100)
    api_match_id: Optional[str] = Field(default=None, max_length=160)
    sport_type: Optional[str] = Field(default=None, max_length=120)
    outcome: Optional[str] = Field(default=None, max_length=200)
    coupon_image_url: Optional[str] = Field(default=None, max_length=2048)
    match_link: Optional[str] = Field(default=None, max_length=2048)
    bookmaker_links: List[BookmakerLink] = Field(default_factory=list, max_length=50)
    delivery_mode: str = Field(default="feed", max_length=40)
    auto_send_on_interest: bool = False

    @field_validator("coupon_image_url", "match_link")
    @classmethod
    def validate_url_fields(cls, value: Optional[str]) -> Optional[str]:
        return _validate_http_or_relative_url(value)

class BetCreate(BetBase):
    target_bookmaker_ids: Optional[List[int]] = Field(default=None, max_length=50)  # Specific list of bookmaker IDs for target audience filtering (optional)
    live_alarm: Optional[bool] = None

class BetResolve(BaseModel):
    status: str = Field(max_length=32)  # "win" | "loss" | "refund"


class BetUpdate(BaseModel):
    event_name: Optional[str] = Field(default=None, max_length=200)
    coefficient: Optional[Decimal] = Field(default=None, ge=Decimal("1.0"), le=Decimal("999.99"))
    fair_coefficient: Optional[Decimal] = Field(default=None, ge=Decimal("1.0"), le=Decimal("999.99"))
    bookmaker_id: Optional[int] = None
    bookmaker_ids: Optional[List[int]] = Field(default=None, max_length=50)
    sport_type: Optional[str] = Field(default=None, max_length=120)
    outcome: Optional[str] = Field(default=None, max_length=200)
    description: Optional[str] = Field(default=None, max_length=4000)
    teaser_text: Optional[str] = Field(default=None, max_length=4000)
    match_link: Optional[str] = Field(default=None, max_length=2048)
    bookmaker_links: Optional[List[BookmakerLink]] = Field(default=None, max_length=50)

    @field_validator("match_link")
    @classmethod
    def validate_match_link(cls, value: Optional[str]) -> Optional[str]:
        return _validate_http_or_relative_url(value)


class BetOddsDropUpdate(BaseModel):
    odds_dropped_to: Optional[Decimal] = Field(default=None, ge=Decimal("1.0"), le=Decimal("999.99"))


class BetResponse(BetBase):
    event_name: str = Field(max_length=200)
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

    model_config = ConfigDict(from_attributes=True)


class BetOddsDropNotifyResponse(BaseModel):
    bet: BetResponse
    total: int
    sent: int
    queued: int = 0
    failed: int
    errors: List[str] = Field(default_factory=list)


class ForecastRequestUserResponse(BaseModel):
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None
    vk_user_id: Optional[str] = None
    is_web_only: bool = False
    matches_remaining: int = 0
    guarantee_active: bool = False
    bookmakers: List[BookmakerResponse] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class BetHintRequest(BaseModel):
    amount_xtr: int = Field(default=20, ge=1, le=100000)


class BetHintResponse(BaseModel):
    bet_id: UUID
    paid_xtr: int
    hint: str
    reveal_level: str = "analysis_only"


class BetHintInvoiceResponse(BaseModel):
    bet_id: UUID
    attempt_id: UUID
    invoice_url: str
    price_xtr: int
    status: str = "invoice_created"


class CrowdBetFundRequest(BaseModel):
    amount_xtr: int = Field(ge=1, le=100000)


class CrowdBetResponse(BaseModel):
    id: int
    bet_id: UUID
    target_amount: int
    current_amount: int
    status: str
    progress_percent: float
    is_participant: bool = False

    model_config = ConfigDict(from_attributes=True)


class CrowdBetFundResponse(BaseModel):
    crowd_bet: CrowdBetResponse
    attempt_id: UUID
    invoice_url: str
    amount_xtr: int
    status: str = "invoice_created"


class SwipeCandidateResponse(BaseModel):
    bet_id: UUID
    match_name: str
    bookmaker_name: Optional[str] = None
    coefficient: Decimal
    options: List[str]


class SwipeRequest(BaseModel):
    bet_id: Optional[UUID] = None
    guess: str = Field(max_length=200)


class SwipeResponse(BaseModel):
    match: bool
    discount: int
    promo_code: Optional[str] = Field(default=None, max_length=80)
    message: str


class QuizQuestionResponse(BaseModel):
    id: str = Field(max_length=80)
    question: str = Field(max_length=400)
    options: List[str] = Field(max_length=10)


class QuizActiveResponse(BaseModel):
    id: int
    bet_id: UUID
    discount_reward: int
    questions: List[QuizQuestionResponse]


class QuizSubmitRequest(BaseModel):
    quiz_id: Optional[int] = None
    bet_id: Optional[UUID] = None
    answers: Dict[str, str] = Field(default_factory=dict)


class QuizSubmitResponse(BaseModel):
    passed: bool
    score: int
    total: int
    discount: int
    promo_code: Optional[str] = Field(default=None, max_length=80)
    message: str


class PvPBattleResponse(BaseModel):
    id: int
    match_name: str = Field(max_length=200)
    option_a: str = Field(max_length=200)
    option_b: str = Field(max_length=200)
    votes_a: int
    votes_b: int
    percent_a: float
    percent_b: float

    model_config = ConfigDict(from_attributes=True)


class PvPVoteRequest(BaseModel):
    battle_id: Optional[int] = None
    option: str = Field(max_length=200)


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

    model_config = ConfigDict(from_attributes=True)

class LivePulseLogResponse(BaseModel):
    id: int
    text_message: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)

class DailyRewardClaimResponse(BaseModel):
    id: int
    user_id: int
    claimed_at: datetime

    model_config = ConfigDict(from_attributes=True)


class UserNoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    emotion_score: int = Field(ge=1, le=5)  # 1-5


class UserNoteResponse(BaseModel):
    id: int
    user_id: int
    bet_id: UUID
    text: str
    emotion_score: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ABTestConfigCreate(BaseModel):
    plan_id: int = Field(ge=1)
    price_group_a: int = Field(ge=0, le=1000000)
    price_group_b: int = Field(ge=0, le=1000000)
    is_active: bool = True


class ABTestConfigResponse(BaseModel):
    id: int
    plan_id: int
    price_group_a: int
    price_group_b: int
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


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
    vk_photo_url: Optional[str] = None
    is_web_only: bool = False
    identity_providers: List[str] = Field(default_factory=list)
    missing_identity_providers: List[str] = Field(default_factory=list)
    telegram_connected: bool = False
    telegram_delivery_enabled: bool = False
    vk_user_id: Optional[str] = None
    vk_group_member: bool = False
    vk_messages_allowed: bool = False
    vk_notifications_allowed: bool = False
    vk_connected: bool = False
    vk_delivery_enabled: bool = False
    web_push_enabled: bool = False
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

    model_config = ConfigDict(from_attributes=True)


class AdminGrantRequest(BaseModel):
    telegram_id: int
    role: str = Field(default="admin", max_length=32)
    username: Optional[str] = Field(default=None, max_length=64)
    first_name: Optional[str] = Field(default=None, max_length=128)
    last_name: Optional[str] = Field(default=None, max_length=128)


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


class MessageTemplateVariableResponse(BaseModel):
    key: str
    label: str
    example: str = ""


class MessageTemplateResponse(BaseModel):
    key: str
    title: str
    description: str
    body: str
    default_body: str
    variables: List[MessageTemplateVariableResponse] = Field(default_factory=list)
    is_custom: bool = False
    updated_by: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class MessageTemplateUpdate(BaseModel):
    body: str = Field(min_length=1, max_length=4000)


class SystemSettingResponse(BaseModel):
    key: str
    value: str = ""
    description: Optional[str] = None
    is_secret: bool = False
    is_configured: bool = False
    updated_at: Optional[datetime] = None


class SystemSettingUpdate(BaseModel):
    key: str = Field(min_length=1, max_length=120)
    value: str = Field(default="", max_length=65536)


class SystemSettingsResponse(BaseModel):
    settings: List[SystemSettingResponse] = Field(default_factory=list)


class IntegrationSettingsUnlockRequest(BaseModel):
    password: str = Field(min_length=1, max_length=120)


class IntegrationSettingsUnlockResponse(SystemSettingsResponse):
    unlock_token: str
    expires_at: datetime


class IntegrationDiagnosticsRequest(BaseModel):
    unlock_token: str = Field(min_length=16, max_length=256)
    group: Optional[str] = Field(default=None, max_length=40)


class ResetSessionsResponse(BaseModel):
    status: str
    deleted: int = 0


class PublicThemeSettingsResponse(BaseModel):
    primary_color: str
    secondary_color: str
    global_performance_mode: bool = False
    welcome_quiz_enabled: bool = False
    subscription_purchases_enabled: bool = False
    brand_logo_url: str = ""
    brand_background_url: str = ""
    glass_opacity: float = 0.42
    glass_blur_px: int = 18
    radius_scale: float = 1.0
    font_scale: float = 1.0
    theme_density: str = "compact"
    glow_strength: float = 1.0


class PresenceHeartbeatResponse(BaseModel):
    status: str = "ok"


class OnlineUsersResponse(BaseModel):
    online_users: int = 0


class MonitoringLogsResponse(BaseModel):
    logs: List[str] = Field(default_factory=list)


class ParserStatusResponse(BaseModel):
    status: str
    last_sync: datetime


# --- USER PREFERENCES SCHEMAS ---
class UserPreferencesUpdate(BaseModel):
    alert_min_coef: Optional[float] = Field(default=None, ge=1.0, le=1.6)
    odds_drop_notifications_enabled: Optional[bool] = None
    is_night_mode: Optional[bool] = None
    night_mode_start: Optional[str] = None
    night_mode_end: Optional[str] = None
    preferred_sports: Optional[List[str]] = None
    stats_display_mode: Optional[str] = None  # "percent" | "flat"


class UserPreferencesResponse(BaseModel):
    alert_min_coef: float
    odds_drop_notifications_enabled: bool
    is_night_mode: bool
    night_mode_start: str
    night_mode_end: str
    preferred_sports: List[str]
    stats_display_mode: str

    model_config = ConfigDict(from_attributes=True)


# --- PAYMENT TRANSACTION SCHEMAS ---
class PaymentTransactionResponse(BaseModel):
    id: UUID
    plan_name: Optional[str] = None
    amount: Optional[str] = None
    payment_provider: Optional[str] = None
    status: str
    created_at: datetime
    end_date: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PaymentReconciliationIssue(BaseModel):
    code: str
    severity: str
    message: str
    attempt_id: UUID
    user_id: int
    provider: str
    provider_payment_id: Optional[str] = None
    status: str
    purchase_type: str
    amount: str
    currency: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    processing_started_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class PaymentReconciliationSummary(BaseModel):
    generated_at: datetime
    window_hours: int
    total_attempts_scanned: int
    total_issues: int
    provider_checks_included: bool
    by_code: Dict[str, int] = Field(default_factory=dict)
    by_severity: Dict[str, int] = Field(default_factory=dict)


class PaymentReconciliationReport(BaseModel):
    summary: PaymentReconciliationSummary
    issues: List[PaymentReconciliationIssue] = Field(default_factory=list)


# --- ANNOUNCEMENT SCHEMAS ---
class AnnouncementCreate(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    body: Optional[str] = Field(default=None, max_length=4000)
    announcement_type: str = Field(default="general", max_length=40)
    sport_filter: Optional[str] = Field(default=None, max_length=80)
    min_coef: Optional[float] = Field(default=None, ge=1.0, le=999.99)
    match_link: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("match_link")
    @classmethod
    def validate_match_link(cls, value: Optional[str]) -> Optional[str]:
        return _validate_http_or_relative_url(value)


class AnnouncementDeliveryResponse(BaseModel):
    total_audience: int
    sent: int
    failed: int
