"""Frozen PostgreSQL baseline schema as deployed on 2026-06-05.

This revision is deliberately independent of the live SQLAlchemy models so a
fresh database always starts from the same historical schema.
"""

from alembic import op


revision = "20260605_0001"
down_revision = None
branch_labels = None
depends_on = None


BASELINE_DDL = (
    r"""
CREATE TABLE users (
	telegram_id BIGSERIAL NOT NULL,
	referred_by_user_id BIGINT,
	username VARCHAR,
	first_name VARCHAR,
	last_name VARCHAR,
	role VARCHAR,
	stats_display_mode VARCHAR,
	bankroll FLOAT,
	is_onboarded BOOLEAN NOT NULL,
	experience_level VARCHAR,
	bankroll_size VARCHAR,
	favorite_sports JSON NOT NULL,
	risk_tolerance VARCHAR,
	primary_bookmaker VARCHAR,
	currency_preference VARCHAR NOT NULL,
	purchased_bets_balance INTEGER NOT NULL,
	free_bets_available INTEGER NOT NULL,
	matches_remaining INTEGER NOT NULL,
	guarantee_active BOOLEAN NOT NULL,
	guarantee_opened_from_bet_id UUID,
	guarantee_closed_at TIMESTAMP WITH TIME ZONE,
	onboarding_goal VARCHAR,
	ab_group VARCHAR,
	tg_chat_joined BOOLEAN NOT NULL,
	has_used_shield BOOLEAN NOT NULL,
	alert_min_coef FLOAT NOT NULL,
	is_night_mode BOOLEAN NOT NULL,
	preferred_sports JSON NOT NULL,
	other_bookmaker_name VARCHAR,
	client_group VARCHAR,
	client_tag VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (telegram_id)
)
""",
    r"""
CREATE INDEX ix_users_telegram_id ON users (telegram_id)
""",
    r"""
CREATE INDEX ix_users_username ON users (username)
""",
    r"""
CREATE INDEX ix_users_referred_by_user_id ON users (referred_by_user_id)
""",
    r"""
CREATE TABLE bookmakers (
	id SERIAL NOT NULL,
	name VARCHAR NOT NULL,
	code VARCHAR NOT NULL,
	is_active BOOLEAN,
	PRIMARY KEY (id)
)
""",
    r"""
CREATE UNIQUE INDEX ix_bookmakers_code ON bookmakers (code)
""",
    r"""
CREATE UNIQUE INDEX ix_bookmakers_name ON bookmakers (name)
""",
    r"""
CREATE INDEX ix_bookmakers_id ON bookmakers (id)
""",
    r"""
CREATE TABLE subscription_plans (
	id SERIAL NOT NULL,
	name VARCHAR NOT NULL,
	duration_days INTEGER NOT NULL,
	match_count INTEGER NOT NULL,
	price NUMERIC(10, 2) NOT NULL,
	price_stars INTEGER,
	currency VARCHAR,
	is_active BOOLEAN,
	PRIMARY KEY (id)
)
""",
    r"""
CREATE INDEX ix_subscription_plans_id ON subscription_plans (id)
""",
    r"""
CREATE INDEX ix_subscription_plans_name ON subscription_plans (name)
""",
    r"""
CREATE TABLE bets (
	id UUID NOT NULL,
	event_name VARCHAR NOT NULL,
	coefficient NUMERIC(5, 2) NOT NULL,
	bookmaker_id INTEGER,
	description TEXT,
	status VARCHAR,
	delivery_mode VARCHAR NOT NULL,
	author_id BIGINT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	resolved_at TIMESTAMP WITH TIME ZONE,
	price_stars INTEGER,
	category VARCHAR NOT NULL,
	live_ends_at TIMESTAMP WITH TIME ZONE,
	brain_score INTEGER,
	api_match_id VARCHAR,
	sport_type VARCHAR,
	outcome VARCHAR,
	coupon_image_url VARCHAR,
	match_link TEXT,
	bookmaker_links JSON NOT NULL,
	PRIMARY KEY (id)
)
""",
    r"""
CREATE TABLE pvp_battles (
	id SERIAL NOT NULL,
	match_name VARCHAR NOT NULL,
	option_a VARCHAR NOT NULL,
	option_b VARCHAR NOT NULL,
	votes_a INTEGER NOT NULL,
	votes_b INTEGER NOT NULL,
	PRIMARY KEY (id)
)
""",
    r"""
CREATE INDEX ix_pvp_battles_id ON pvp_battles (id)
""",
    r"""
CREATE TABLE marathons (
	id SERIAL NOT NULL,
	title VARCHAR NOT NULL,
	target_multiplier FLOAT NOT NULL,
	current_step INTEGER NOT NULL,
	total_steps INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (id)
)
""",
    r"""
CREATE INDEX ix_marathons_id ON marathons (id)
""",
    r"""
CREATE TABLE live_pulse_logs (
	id SERIAL NOT NULL,
	text_message VARCHAR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id)
)
""",
    r"""
CREATE INDEX ix_live_pulse_logs_id ON live_pulse_logs (id)
""",
    r"""
CREATE TABLE user_bookmakers (
	user_id BIGINT NOT NULL,
	bookmaker_id INTEGER NOT NULL,
	PRIMARY KEY (user_id, bookmaker_id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id) ON DELETE CASCADE
)
""",
    r"""
CREATE TABLE user_bets (
	user_id BIGINT NOT NULL,
	bet_id UUID NOT NULL,
	taken_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	access_type VARCHAR NOT NULL,
	match_charged BOOLEAN NOT NULL,
	PRIMARY KEY (user_id, bet_id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE
)
""",
    r"""
CREATE TABLE bet_bookmakers (
	bet_id UUID NOT NULL,
	bookmaker_id INTEGER NOT NULL,
	PRIMARY KEY (bet_id, bookmaker_id),
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE,
	FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id) ON DELETE CASCADE
)
""",
    r"""
CREATE TABLE subscriptions (
	id UUID NOT NULL,
	user_id BIGINT NOT NULL,
	plan_id INTEGER,
	status VARCHAR,
	start_date TIMESTAMP WITH TIME ZONE,
	end_date TIMESTAMP WITH TIME ZONE,
	payment_provider VARCHAR,
	payment_id VARCHAR,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT uq_subscriptions_provider_payment UNIQUE (payment_provider, payment_id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(plan_id) REFERENCES subscription_plans (id) ON DELETE SET NULL
)
""",
    r"""
CREATE INDEX ix_subscriptions_user_id ON subscriptions (user_id)
""",
    r"""
CREATE TABLE payment_attempts (
	id UUID NOT NULL,
	user_id BIGINT NOT NULL,
	plan_id INTEGER,
	bet_id UUID,
	provider VARCHAR NOT NULL,
	provider_payment_id VARCHAR,
	status VARCHAR NOT NULL,
	amount NUMERIC(10, 2) NOT NULL,
	currency VARCHAR NOT NULL,
	promo_code VARCHAR,
	metadata_json JSON NOT NULL,
	processed_at TIMESTAMP WITH TIME ZONE,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT uq_payment_attempts_provider_payment UNIQUE (provider, provider_payment_id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(plan_id) REFERENCES subscription_plans (id) ON DELETE SET NULL,
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE SET NULL
)
""",
    r"""
CREATE INDEX ix_payment_attempts_user_id ON payment_attempts (user_id)
""",
    r"""
CREATE INDEX ix_payment_attempts_status ON payment_attempts (status)
""",
    r"""
CREATE INDEX ix_payment_attempts_plan_id ON payment_attempts (plan_id)
""",
    r"""
CREATE INDEX ix_payment_attempts_bet_id ON payment_attempts (bet_id)
""",
    r"""
CREATE INDEX ix_payment_attempts_provider ON payment_attempts (provider)
""",
    r"""
CREATE INDEX ix_payment_attempts_user_status ON payment_attempts (user_id, status)
""",
    r"""
CREATE TABLE admin_audit_logs (
	id SERIAL NOT NULL,
	actor_id BIGINT,
	target_user_id BIGINT,
	action VARCHAR NOT NULL,
	details JSON NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(actor_id) REFERENCES users (telegram_id) ON DELETE SET NULL,
	FOREIGN KEY(target_user_id) REFERENCES users (telegram_id) ON DELETE SET NULL
)
""",
    r"""
CREATE INDEX ix_admin_audit_logs_action ON admin_audit_logs (action)
""",
    r"""
CREATE INDEX ix_admin_audit_logs_actor_id ON admin_audit_logs (actor_id)
""",
    r"""
CREATE INDEX ix_admin_audit_logs_id ON admin_audit_logs (id)
""",
    r"""
CREATE INDEX ix_admin_audit_logs_target_user_id ON admin_audit_logs (target_user_id)
""",
    r"""
CREATE TABLE forecast_requests (
	id UUID NOT NULL,
	bet_id UUID NOT NULL,
	user_id BIGINT NOT NULL,
	status VARCHAR NOT NULL,
	delivery_method VARCHAR,
	handled_by BIGINT,
	responded_at TIMESTAMP WITH TIME ZONE,
	delivered_at TIMESTAMP WITH TIME ZONE,
	balance_before INTEGER,
	balance_after INTEGER,
	no_balance_warning BOOLEAN NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT uq_forecast_requests_bet_user UNIQUE (bet_id, user_id),
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_forecast_requests_user_id ON forecast_requests (user_id)
""",
    r"""
CREATE INDEX ix_forecast_requests_status_created ON forecast_requests (status, created_at)
""",
    r"""
CREATE INDEX ix_forecast_requests_bet_id ON forecast_requests (bet_id)
""",
    r"""
CREATE INDEX ix_forecast_requests_handled_by ON forecast_requests (handled_by)
""",
    r"""
CREATE TABLE crowd_bets (
	id SERIAL NOT NULL,
	bet_id UUID NOT NULL,
	target_amount INTEGER NOT NULL,
	current_amount INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_crowd_bets_id ON crowd_bets (id)
""",
    r"""
CREATE INDEX ix_crowd_bets_bet_id ON crowd_bets (bet_id)
""",
    r"""
CREATE TABLE quizzes (
	id SERIAL NOT NULL,
	bet_id UUID NOT NULL,
	questions JSON NOT NULL,
	discount_reward INTEGER NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_quizzes_bet_id ON quizzes (bet_id)
""",
    r"""
CREATE INDEX ix_quizzes_id ON quizzes (id)
""",
    r"""
CREATE TABLE pvp_battle_votes (
	id SERIAL NOT NULL,
	battle_id INTEGER NOT NULL,
	user_id BIGINT NOT NULL,
	option VARCHAR NOT NULL,
	voted_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT uq_pvp_battle_votes_battle_user UNIQUE (battle_id, user_id),
	FOREIGN KEY(battle_id) REFERENCES pvp_battles (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_pvp_battle_votes_battle_id ON pvp_battle_votes (battle_id)
""",
    r"""
CREATE INDEX ix_pvp_battle_votes_id ON pvp_battle_votes (id)
""",
    r"""
CREATE INDEX ix_pvp_battle_votes_user_id ON pvp_battle_votes (user_id)
""",
    r"""
CREATE TABLE promo_codes (
	id SERIAL NOT NULL,
	code VARCHAR NOT NULL,
	user_id BIGINT,
	discount_percent INTEGER NOT NULL,
	valid_until TIMESTAMP WITH TIME ZONE NOT NULL,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_promo_codes_user_id ON promo_codes (user_id)
""",
    r"""
CREATE INDEX ix_promo_codes_id ON promo_codes (id)
""",
    r"""
CREATE UNIQUE INDEX ix_promo_codes_code ON promo_codes (code)
""",
    r"""
CREATE TABLE daily_reward_claims (
	id SERIAL NOT NULL,
	user_id BIGINT NOT NULL,
	claimed_date VARCHAR NOT NULL,
	claimed_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	CONSTRAINT uq_daily_reward_claims_user_date UNIQUE (user_id, claimed_date),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_daily_reward_claims_id ON daily_reward_claims (id)
""",
    r"""
CREATE INDEX ix_daily_reward_claims_claimed_date ON daily_reward_claims (claimed_date)
""",
    r"""
CREATE INDEX ix_daily_reward_claims_user_id ON daily_reward_claims (user_id)
""",
    r"""
CREATE TABLE user_badges (
	id SERIAL NOT NULL,
	user_id BIGINT NOT NULL,
	title VARCHAR NOT NULL,
	icon_type VARCHAR NOT NULL,
	unlocked_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_user_badges_id ON user_badges (id)
""",
    r"""
CREATE INDEX ix_user_badges_user_id ON user_badges (user_id)
""",
    r"""
CREATE TABLE user_notes (
	id SERIAL NOT NULL,
	user_id BIGINT NOT NULL,
	bet_id UUID NOT NULL,
	text TEXT NOT NULL,
	emotion_score INTEGER,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_user_notes_user_id ON user_notes (user_id)
""",
    r"""
CREATE INDEX ix_user_notes_id ON user_notes (id)
""",
    r"""
CREATE INDEX ix_user_notes_bet_id ON user_notes (bet_id)
""",
    r"""
CREATE TABLE ab_test_configs (
	id SERIAL NOT NULL,
	plan_id INTEGER NOT NULL,
	price_group_a INTEGER NOT NULL,
	price_group_b INTEGER NOT NULL,
	is_active BOOLEAN NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(plan_id) REFERENCES subscription_plans (id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_ab_test_configs_id ON ab_test_configs (id)
""",
    r"""
CREATE TABLE match_balance_logs (
	id SERIAL NOT NULL,
	user_id BIGINT NOT NULL,
	bet_id UUID,
	subscription_id UUID,
	delta_matches INTEGER NOT NULL,
	event_type VARCHAR NOT NULL,
	note TEXT,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE,
	FOREIGN KEY(bet_id) REFERENCES bets (id) ON DELETE SET NULL,
	FOREIGN KEY(subscription_id) REFERENCES subscriptions (id) ON DELETE SET NULL
)
""",
    r"""
CREATE INDEX ix_match_balance_logs_bet_id ON match_balance_logs (bet_id)
""",
    r"""
CREATE INDEX ix_match_balance_logs_subscription_id ON match_balance_logs (subscription_id)
""",
    r"""
CREATE INDEX ix_match_balance_logs_id ON match_balance_logs (id)
""",
    r"""
CREATE INDEX ix_match_balance_logs_user_id ON match_balance_logs (user_id)
""",
    r"""
CREATE TABLE crowd_bet_participants (
	id SERIAL NOT NULL,
	crowd_bet_id INTEGER NOT NULL,
	user_id BIGINT NOT NULL,
	contributed_amount INTEGER NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (id),
	FOREIGN KEY(crowd_bet_id) REFERENCES crowd_bets (id) ON DELETE CASCADE,
	FOREIGN KEY(user_id) REFERENCES users (telegram_id) ON DELETE CASCADE
)
""",
    r"""
CREATE INDEX ix_crowd_bet_participants_id ON crowd_bet_participants (id)
""",
    r"""
CREATE INDEX ix_crowd_bet_participants_crowd_bet_id ON crowd_bet_participants (crowd_bet_id)
""",
    r"""
CREATE INDEX ix_crowd_bet_participants_user_id ON crowd_bet_participants (user_id)
""",
    r"""
ALTER TABLE users ADD FOREIGN KEY(guarantee_opened_from_bet_id) REFERENCES bets (id) ON DELETE SET NULL
""",
    r"""
ALTER TABLE bets ADD FOREIGN KEY(author_id) REFERENCES users (telegram_id) ON DELETE SET NULL
""",
    r"""
ALTER TABLE users ADD FOREIGN KEY(referred_by_user_id) REFERENCES users (telegram_id) ON DELETE SET NULL
""",
    r"""
ALTER TABLE bets ADD FOREIGN KEY(bookmaker_id) REFERENCES bookmakers (id) ON DELETE SET NULL
""",
)


BASELINE_TABLES_REVERSE = (
    "crowd_bet_participants",
    "match_balance_logs",
    "ab_test_configs",
    "user_notes",
    "user_badges",
    "daily_reward_claims",
    "promo_codes",
    "pvp_battle_votes",
    "quizzes",
    "crowd_bets",
    "forecast_requests",
    "admin_audit_logs",
    "payment_attempts",
    "subscriptions",
    "bet_bookmakers",
    "user_bets",
    "user_bookmakers",
    "live_pulse_logs",
    "marathons",
    "pvp_battles",
    "bets",
    "subscription_plans",
    "bookmakers",
    "users",
)


def upgrade() -> None:
    for statement in BASELINE_DDL:
        op.execute(statement)


def downgrade() -> None:
    for table_name in BASELINE_TABLES_REVERSE:
        op.execute(f'DROP TABLE "{table_name}" CASCADE')
