import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    Integer,
    BigInteger,
    String,
    Boolean,
    DateTime,
    Numeric,
    ForeignKey,
    Text,
    Table,
    Float,
    func,
    JSON,
    Uuid,
    UniqueConstraint,
    Index,
    CheckConstraint,
)
from sqlalchemy.orm import relationship
from src.models.database import Base

# Association Table for User <-> Bookmaker (Many-to-Many)
user_bookmakers = Table(
    "user_bookmakers",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
    Column("bookmaker_id", Integer, ForeignKey("bookmakers.id", ondelete="CASCADE"), primary_key=True)
)

# Association Table for User <-> Bet (Many-to-Many - bets taken by user)
user_bets = Table(
    "user_bets",
    Base.metadata,
    Column("user_id", BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True),
    Column("bet_id", Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), primary_key=True),
    Column("taken_at", DateTime(timezone=True), server_default=func.now()),
    Column("access_type", String, default="paid_match", nullable=False),
    Column("match_charged", Boolean, default=True, nullable=False),
    Index("ix_user_bets_user_taken", "user_id", "taken_at"),
    Index("ix_user_bets_bet_user", "bet_id", "user_id"),
)

# Association Table for Bet <-> Bookmaker (one forecast can target several bookmakers)
bet_bookmakers = Table(
    "bet_bookmakers",
    Base.metadata,
    Column("bet_id", Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), primary_key=True),
    Column("bookmaker_id", Integer, ForeignKey("bookmakers.id", ondelete="CASCADE"), primary_key=True)
)

class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("ix_users_created_at", "created_at"),
    )

    telegram_id = Column(BigInteger, primary_key=True, index=True)
    referred_by_user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    username = Column(String, nullable=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone = Column(String, nullable=True, unique=True, index=True)
    photo_url = Column(Text, nullable=True)
    vk_photo_url = Column(Text, nullable=True)
    role = Column(String, default="user")  # "owner" | "admin" | "moderator" | "user"
    stats_display_mode = Column(String, default="percent")  # "percent" | "flat"
    bankroll = Column(Float, default=0.0)
    is_onboarded = Column(Boolean, default=False, nullable=False)
    experience_level = Column(String, nullable=True)  # "novice" | "amateur" | "pro"
    bankroll_size = Column(String, nullable=True)  # "micro" | "mid" | "high"
    favorite_sports = Column(JSON, default=list, nullable=False)
    risk_tolerance = Column(String, nullable=True)  # "cautious" | "balanced" | "aggressive"
    primary_bookmaker = Column(String, nullable=True)
    vk_user_id = Column(String, nullable=True, unique=True, index=True)
    vk_group_member = Column(Boolean, default=False, nullable=False)
    vk_messages_allowed = Column(Boolean, default=False, nullable=False)
    vk_notifications_allowed = Column(Boolean, default=False, nullable=False)
    web_push_subscription = Column(JSON, nullable=True)
    currency_preference = Column(String, default="RUB", nullable=False)
    purchased_bets_balance = Column(Integer, default=0, nullable=False)
    free_bets_available = Column(Integer, default=0, nullable=False)
    matches_remaining = Column(Integer, default=0, nullable=False)
    guarantee_active = Column(Boolean, default=False, nullable=False)
    guarantee_opened_from_bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="SET NULL"), nullable=True)
    guarantee_closed_at = Column(DateTime(timezone=True), nullable=True)
    onboarding_goal = Column(String, nullable=True)
    ab_group = Column(String, nullable=True)
    tg_chat_joined = Column(Boolean, default=False, nullable=False)
    has_used_shield = Column(Boolean, default=False, nullable=False)
    alert_min_coef = Column(Float, default=1.0, nullable=False)
    odds_drop_notifications_enabled = Column(Boolean, default=True, nullable=False)
    is_night_mode = Column(Boolean, default=False, nullable=False)
    night_mode_start = Column(String(5), default="23:00", nullable=False)
    night_mode_end = Column(String(5), default="08:00", nullable=False)
    preferred_sports = Column(JSON, default=list, nullable=False)
    other_bookmaker_name = Column(String, nullable=True)
    client_group = Column(String, nullable=True)
    client_tag = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    bookmakers = relationship("Bookmaker", secondary=user_bookmakers, back_populates="users")
    subscriptions = relationship("Subscription", back_populates="user", cascade="all, delete-orphan")
    bets_taken = relationship("Bet", secondary=user_bets, back_populates="takers")
    personal_signals = relationship("PersonalSignal", back_populates="user", cascade="all, delete-orphan")
    chat_conversations = relationship(
        "ChatConversation",
        foreign_keys="ChatConversation.owner_user_id",
        back_populates="owner",
        cascade="all, delete-orphan",
    )

    @property
    def is_web_only(self) -> bool:
        return self.telegram_id < 0 and bool(self.vk_user_id)

    @property
    def identity_providers(self) -> list[str]:
        providers: list[str] = []
        if self.telegram_id > 0:
            providers.append("telegram")
        if self.vk_user_id:
            providers.append("vk")
        return providers

    @property
    def missing_identity_providers(self) -> list[str]:
        providers = set(self.identity_providers)
        return [provider for provider in ("telegram", "vk") if provider not in providers]

    @property
    def identity_complete(self) -> bool:
        return not self.missing_identity_providers


class IdentityDeviceLink(Base):
    __tablename__ = "identity_device_links"

    device_key_hash = Column(String(64), primary_key=True)
    source_user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    source_user = relationship("User")

class Bookmaker(Base):
    __tablename__ = "bookmakers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, unique=True, index=True)
    code = Column(String, nullable=False, unique=True, index=True)  # unique string code, e.g. "fonbet", "betboom"
    is_active = Column(Boolean, default=True)

    # Relationships
    users = relationship("User", secondary=user_bookmakers, back_populates="bookmakers")
    bets = relationship("Bet", secondary=bet_bookmakers, back_populates="bookmakers")

class SubscriptionPlan(Base):
    __tablename__ = "subscription_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False, index=True)
    duration_days = Column(Integer, nullable=False)
    match_count = Column(Integer, default=1, nullable=False)
    price = Column(Numeric(10, 2), nullable=False)
    price_stars = Column(Integer, default=0)
    currency = Column(String, default="RUB")
    is_active = Column(Boolean, default=True)

class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("payment_provider", "payment_id", name="uq_subscriptions_provider_payment"),
        Index("ix_subscriptions_user_created", "user_id", "created_at"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True)
    status = Column(String, default="pending")  # "active" | "expired" | "pending"
    start_date = Column(DateTime(timezone=True), nullable=True)
    end_date = Column(DateTime(timezone=True), nullable=True)
    payment_provider = Column(String, nullable=True)  # e.g., "stars", "yookassa"
    payment_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    user = relationship("User", back_populates="subscriptions")
    plan = relationship("SubscriptionPlan")


class PaymentAttempt(Base):
    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint("provider", "provider_payment_id", name="uq_payment_attempts_provider_payment"),
        Index("ix_payment_attempts_user_status", "user_id", "status"),
        Index("ix_payment_attempts_user_created", "user_id", "created_at"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="SET NULL"), nullable=True, index=True)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String, nullable=False, index=True)  # "telegram_stars" | "yookassa" | "debug"
    provider_payment_id = Column(String, nullable=True)
    status = Column(String, default="pending", nullable=False, index=True)
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String, default="XTR", nullable=False)
    promo_code = Column(String, nullable=True)
    metadata_json = Column(JSON, default=dict, nullable=False)
    processing_started_at = Column(DateTime(timezone=True), nullable=True)
    processed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    user = relationship("User")
    plan = relationship("SubscriptionPlan")
    bet = relationship("Bet")


class MatchBalanceLog(Base):
    __tablename__ = "match_balance_logs"
    __table_args__ = (
        Index("ix_match_balance_logs_user_created", "user_id", "created_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="SET NULL"), nullable=True, index=True)
    subscription_id = Column(Uuid(as_uuid=True), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True)
    delta_matches = Column(Integer, default=0, nullable=False)
    event_type = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User")
    bet = relationship("Bet")
    subscription = relationship("Subscription")


class PersonalSignal(Base):
    __tablename__ = "personal_signals"
    __table_args__ = (
        Index("ix_personal_signals_user_created", "user_id", "created_at"),
        Index("ix_personal_signals_type_created", "type", "created_at"),
        Index("ix_personal_signals_user_id_lookup", "user_id", "id"),
        Index("ix_personal_signals_user_type_id", "user_id", "type", "id"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    type = Column(String, default="signal", nullable=False, index=True)
    data = Column(JSON, default=dict, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", back_populates="personal_signals")


class ChatConversation(Base):
    __tablename__ = "chat_conversations"
    __table_args__ = (
        UniqueConstraint("kind", "owner_user_id", name="uq_chat_conversation_kind_owner"),
        CheckConstraint("kind IN ('support')", name="ck_chat_conversation_kind"),
        CheckConstraint("status IN ('open', 'closed')", name="ck_chat_conversation_status"),
        Index("ix_chat_conversations_status_last_message", "status", "last_message_at"),
        Index("ix_chat_conversations_owner", "owner_user_id"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind = Column(String(32), default="support", nullable=False)
    owner_user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False)
    assigned_staff_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True)
    status = Column(String(16), default="open", nullable=False)
    title = Column(String(200), nullable=True)
    last_message_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now(), nullable=False)

    owner = relationship("User", foreign_keys=[owner_user_id], back_populates="chat_conversations")
    assigned_staff = relationship("User", foreign_keys=[assigned_staff_id])
    messages = relationship("ChatMessage", back_populates="conversation", cascade="all, delete-orphan")
    read_cursors = relationship("ChatReadCursor", back_populates="conversation", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("sender_user_id", "client_message_id", name="uq_chat_message_sender_client"),
        CheckConstraint("type IN ('text', 'image', 'voice', 'file')", name="ck_chat_message_type"),
        CheckConstraint("text IS NULL OR length(text) BETWEEN 1 AND 4000", name="ck_chat_message_text_length"),
        Index("ix_chat_messages_conversation_id_id", "conversation_id", "id"),
        Index("ix_chat_messages_conversation_created", "conversation_id", "created_at"),
    )

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    conversation_id = Column(Uuid(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False)
    sender_user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True)
    sender_role = Column(String(32), nullable=False)
    type = Column(String(32), default="text", nullable=False)
    text = Column(Text, nullable=True)
    payload = Column(JSON, default=dict, nullable=False)
    client_message_id = Column(Uuid(as_uuid=True), nullable=False)
    reply_to_id = Column(BigInteger, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    edited_at = Column(DateTime(timezone=True), nullable=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True)

    conversation = relationship("ChatConversation", back_populates="messages")
    sender = relationship("User", foreign_keys=[sender_user_id])
    reply_to = relationship("ChatMessage", remote_side=[id])


class ChatReadCursor(Base):
    __tablename__ = "chat_read_cursors"
    __table_args__ = (
        Index("ix_chat_read_cursors_user", "user_id"),
    )

    conversation_id = Column(Uuid(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True)
    last_read_message_id = Column(BigInteger, ForeignKey("chat_messages.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    conversation = relationship("ChatConversation", back_populates="read_cursors")
    user = relationship("User", foreign_keys=[user_id])
    last_read_message = relationship("ChatMessage", foreign_keys=[last_read_message_id])


class PersonalSignalReadCursor(Base):
    __tablename__ = "personal_signal_read_cursors"

    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), primary_key=True)
    last_read_signal_id = Column(Integer, ForeignKey("personal_signals.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    user = relationship("User", foreign_keys=[user_id])
    last_read_signal = relationship("PersonalSignal", foreign_keys=[last_read_signal_id])


class DeliveryOutbox(Base):
    __tablename__ = "delivery_outbox"
    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_delivery_outbox_dedupe_key"),
        Index("ix_delivery_outbox_status_next_attempt", "status", "next_attempt_at"),
        Index("ix_delivery_outbox_channel_status", "channel", "status"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    channel = Column(String, nullable=False, index=True)
    status = Column(String, default="pending", nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    personal_signal_id = Column(Integer, ForeignKey("personal_signals.id", ondelete="SET NULL"), nullable=True, index=True)
    forecast_request_id = Column(Uuid(as_uuid=True), ForeignKey("forecast_requests.id", ondelete="SET NULL"), nullable=True, index=True)
    payload = Column(JSON, default=dict, nullable=False)
    dedupe_key = Column(String, nullable=True)
    attempt_count = Column(Integer, default=0, nullable=False)
    max_attempts = Column(Integer, default=5, nullable=False)
    next_attempt_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    locked_at = Column(DateTime(timezone=True), nullable=True)
    sent_at = Column(DateTime(timezone=True), nullable=True)
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now(), nullable=False)

    user = relationship("User")
    personal_signal = relationship("PersonalSignal")
    forecast_request = relationship("ForecastRequest")


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    target_user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    action = Column(String, nullable=False, index=True)
    details = Column(JSON, default=dict, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    actor = relationship("User", foreign_keys=[actor_id])
    target_user = relationship("User", foreign_keys=[target_user_id])


class MessageTemplate(Base):
    __tablename__ = "message_templates"

    key = Column(String, primary_key=True, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    body = Column(Text, nullable=False)
    variables = Column(JSON, default=list, nullable=False)
    updated_by = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now(), nullable=False)

    editor = relationship("User", foreign_keys=[updated_by])


class SystemSetting(Base):
    __tablename__ = "system_settings"

    key = Column(String(120), primary_key=True, index=True)
    value = Column(Text, nullable=False, default="")
    description = Column(Text, nullable=True)
    is_secret = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now(), nullable=False)


class Bet(Base):
    __tablename__ = "bets"
    __table_args__ = (
        Index("ix_bets_status_delivery_created", "status", "delivery_mode", "created_at"),
        Index("ix_bets_status_resolved", "status", "resolved_at"),
        Index("ix_bets_author_status_resolved", "author_id", "status", "resolved_at"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_name = Column(String, nullable=False)
    coefficient = Column(Numeric(5, 2), nullable=False)
    fair_coefficient = Column(Numeric(5, 2), nullable=True)
    bookmaker_id = Column(Integer, ForeignKey("bookmakers.id", ondelete="SET NULL"), nullable=True)
    description = Column(Text, nullable=True)
    teaser_text = Column(Text, nullable=True)
    status = Column(String, default="pending")  # "pending" | "win" | "loss" | "refund"
    delivery_mode = Column(String, default="feed", nullable=False)  # "feed" | "sales_private"
    author_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    price_stars = Column(Integer, nullable=True)
    category = Column(String, default="prematch", nullable=False)  # "prematch" | "live"
    live_ends_at = Column(DateTime(timezone=True), nullable=True)
    brain_score = Column(Integer, nullable=True)
    api_match_id = Column(String, nullable=True)
    sport_type = Column(String, nullable=True)
    outcome = Column(String, nullable=True)
    coupon_image_url = Column(String, nullable=True)
    match_link = Column(Text, nullable=True)
    bookmaker_links = Column(JSON, default=list, nullable=False)
    auto_send_on_interest = Column(Boolean, default=False, nullable=False)
    odds_dropped_to = Column(Numeric(5, 2), nullable=True)
    odds_drop_notified_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    bookmaker = relationship("Bookmaker")
    bookmakers = relationship("Bookmaker", secondary=bet_bookmakers, back_populates="bets")
    takers = relationship("User", secondary=user_bets, back_populates="bets_taken")

    @property
    def bookmaker_ids(self):
        ids = [bookmaker.id for bookmaker in self.bookmakers]
        if not ids and self.bookmaker_id:
            ids.append(self.bookmaker_id)
        return ids


class ForecastRequest(Base):
    __tablename__ = "forecast_requests"
    __table_args__ = (
        UniqueConstraint("bet_id", "user_id", name="uq_forecast_requests_bet_user"),
        Index("ix_forecast_requests_status_created", "status", "created_at"),
        Index("ix_forecast_requests_bet_status", "bet_id", "status"),
    )

    id = Column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String, default="announced", nullable=False)
    delivery_method = Column(String, nullable=True)  # "bot" | "manual"
    handled_by = Column(BigInteger, nullable=True, index=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    delivered_at = Column(DateTime(timezone=True), nullable=True)
    balance_before = Column(Integer, nullable=True)
    balance_after = Column(Integer, nullable=True)
    no_balance_warning = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    bet = relationship("Bet")
    user = relationship("User")

class CrowdBet(Base):
    __tablename__ = "crowd_bets"

    id = Column(Integer, primary_key=True, index=True)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), nullable=False, index=True)
    target_amount = Column(Integer, nullable=False, default=1000)
    current_amount = Column(Integer, nullable=False, default=0)
    status = Column(String, default="funding", nullable=False)  # "funding" | "opened"

    bet = relationship("Bet")
    participants = relationship("CrowdBetParticipant", back_populates="crowd_bet", cascade="all, delete-orphan")


class CrowdBetParticipant(Base):
    __tablename__ = "crowd_bet_participants"

    id = Column(Integer, primary_key=True, index=True)
    crowd_bet_id = Column(Integer, ForeignKey("crowd_bets.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    contributed_amount = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    crowd_bet = relationship("CrowdBet", back_populates="participants")
    user = relationship("User")


class Quiz(Base):
    __tablename__ = "quizzes"

    id = Column(Integer, primary_key=True, index=True)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), nullable=False, index=True)
    questions = Column(JSON, nullable=False, default=list)
    discount_reward = Column(Integer, nullable=False, default=30)

    bet = relationship("Bet")


class PvPBattle(Base):
    __tablename__ = "pvp_battles"

    id = Column(Integer, primary_key=True, index=True)
    match_name = Column(String, nullable=False)
    option_a = Column(String, nullable=False)
    option_b = Column(String, nullable=False)
    votes_a = Column(Integer, nullable=False, default=0)
    votes_b = Column(Integer, nullable=False, default=0)


class PvPBattleVote(Base):
    __tablename__ = "pvp_battle_votes"
    __table_args__ = (
        UniqueConstraint("battle_id", "user_id", name="uq_pvp_battle_votes_battle_user"),
    )

    id = Column(Integer, primary_key=True, index=True)
    battle_id = Column(Integer, ForeignKey("pvp_battles.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    option = Column(String, nullable=False)
    voted_at = Column(DateTime(timezone=True), server_default=func.now())

    battle = relationship("PvPBattle")
    user = relationship("User")

class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=True, index=True)
    reward_type = Column(String, default="discount", nullable=False)
    discount_percent = Column(Integer, default=0, nullable=False)
    matches_count = Column(Integer, default=0, nullable=False)
    valid_until = Column(DateTime(timezone=True), nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)


class PromoCodeRedemption(Base):
    __tablename__ = "promo_code_redemptions"
    __table_args__ = (
        UniqueConstraint("promo_code_id", "user_id", name="uq_promo_code_redemptions_code_user"),
        Index("ix_promo_code_redemptions_user_redeemed", "user_id", "redeemed_at"),
    )

    id = Column(Integer, primary_key=True, index=True)
    promo_code_id = Column(Integer, ForeignKey("promo_codes.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    matches_added = Column(Integer, default=0, nullable=False)
    redeemed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    promo_code = relationship("PromoCode")
    user = relationship("User")


class Marathon(Base):
    __tablename__ = "marathons"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    target_multiplier = Column(Float, nullable=False)
    current_step = Column(Integer, default=1, nullable=False)
    total_steps = Column(Integer, default=10, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)

class LivePulseLog(Base):
    __tablename__ = "live_pulse_logs"

    id = Column(Integer, primary_key=True, index=True)
    text_message = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

class DailyRewardClaim(Base):
    __tablename__ = "daily_reward_claims"
    __table_args__ = (
        UniqueConstraint("user_id", "claimed_date", name="uq_daily_reward_claims_user_date"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    claimed_date = Column(String, nullable=False, index=True)
    claimed_at = Column(DateTime(timezone=True), server_default=func.now())


class UserBadge(Base):
    __tablename__ = "user_badges"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String, nullable=False)
    icon_type = Column(String, nullable=False)  # e.g., "sharp_mind"
    unlocked_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", backref="badges")


class UserNote(Base):
    __tablename__ = "user_notes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(BigInteger, ForeignKey("users.telegram_id", ondelete="CASCADE"), nullable=False, index=True)
    bet_id = Column(Uuid(as_uuid=True), ForeignKey("bets.id", ondelete="CASCADE"), nullable=False, index=True)
    text = Column(Text, nullable=False)
    emotion_score = Column(Integer, default=5)  # 1-5
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    user = relationship("User", backref="notes")
    bet = relationship("Bet")


class ABTestConfig(Base):
    __tablename__ = "ab_test_configs"

    id = Column(Integer, primary_key=True, index=True)
    plan_id = Column(Integer, ForeignKey("subscription_plans.id", ondelete="CASCADE"), nullable=False)
    price_group_a = Column(Integer, nullable=False)  # Stars price
    price_group_b = Column(Integer, nullable=False)  # Stars price
    is_active = Column(Boolean, default=True, nullable=False)

    # Relationships
    plan = relationship("SubscriptionPlan")
