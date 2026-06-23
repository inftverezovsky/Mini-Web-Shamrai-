import asyncio
import mimetypes
from typing import Optional
import socket
import time
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from contextlib import suppress
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.future import select

from src.models.database import Base, engine, AsyncSessionLocal
from src.models.models import Subscription, User
from src.api.deps import get_optional_user_read
from src.core.config import settings
from src.core.roles import is_staff_role
from src.core.security_limits import SecurityRateLimitMiddleware, security_rate_limiter
from src.core.telegram_delivery import is_personal_telegram_user_id, user_can_receive_personal_telegram
from src.api import auth, users, bets, subscriptions, payments, stats, marketing, admin, admin_web_chat, admin_broadcast, crowd_bets, telegram_webhook, vk_callback, signals, chat, go
from src.services.delivery_outbox import delivery_outbox_daemon
from src.services.telegram_bot import call_telegram_api, call_telegram_api_async, run_telegram_api_background
from src.services.vk_delivery import (
    get_vk_unread_conversations,
    log_vk_runtime_config,
    mark_vk_conversation_read,
    probe_vk_api,
    vk_delivery_configured,
    vk_group_id,
)


async def run_dev_schema_migrations(conn):
    """
    Keeps the local/dev database compatible with model changes.
    This is intentionally narrow; production should use Alembic migrations.
    """
    dialect = conn.dialect.name

    if dialect == "postgresql":
        statements = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS alert_min_coef DOUBLE PRECISION NOT NULL DEFAULT 1.0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS odds_drop_notifications_enabled BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_user_id BIGINT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_night_mode BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS night_mode_start VARCHAR(5) NOT NULL DEFAULT '23:00'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS night_mode_end VARCHAR(5) NOT NULL DEFAULT '08:00'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS experience_level VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS bankroll_size VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS favorite_sports JSON NOT NULL DEFAULT '[]'::json",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS risk_tolerance VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_bookmaker VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_user_id VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_group_member BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_messages_allowed BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_notifications_allowed BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS web_push_subscription JSON",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS photo_url TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS vk_photo_url TEXT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS currency_preference VARCHAR NOT NULL DEFAULT 'RUB'",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS purchased_bets_balance INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS preferred_sports JSON NOT NULL DEFAULT '[]'::json",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS has_used_shield BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS other_bookmaker_name VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS client_group VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS client_tag VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS matches_remaining INTEGER NOT NULL DEFAULT 0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS guarantee_active BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS guarantee_opened_from_bet_id UUID",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS guarantee_closed_at TIMESTAMP WITH TIME ZONE",
            "ALTER TABLE subscription_plans ADD COLUMN IF NOT EXISTS match_count INTEGER NOT NULL DEFAULT 1",
            "ALTER TABLE user_bets ADD COLUMN IF NOT EXISTS access_type VARCHAR NOT NULL DEFAULT 'paid_match'",
            "ALTER TABLE user_bets ADD COLUMN IF NOT EXISTS match_charged BOOLEAN NOT NULL DEFAULT TRUE",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR NOT NULL DEFAULT 'feed'",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS fair_coefficient NUMERIC(5, 2)",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS teaser_text TEXT",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS sport_type VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS outcome VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS coupon_image_url VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS match_link TEXT",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS bookmaker_links JSON NOT NULL DEFAULT '[]'::json",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS auto_send_on_interest BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE promo_codes ADD COLUMN IF NOT EXISTS user_id BIGINT",
            """
            CREATE TABLE IF NOT EXISTS bet_bookmakers (
                bet_id UUID NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
                bookmaker_id INTEGER NOT NULL REFERENCES bookmakers(id) ON DELETE CASCADE,
                PRIMARY KEY (bet_id, bookmaker_id)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS forecast_requests (
                id UUID PRIMARY KEY,
                bet_id UUID NOT NULL REFERENCES bets(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                status VARCHAR NOT NULL DEFAULT 'announced',
                delivery_method VARCHAR,
                handled_by BIGINT,
                responded_at TIMESTAMP WITH TIME ZONE,
                delivered_at TIMESTAMP WITH TIME ZONE,
                balance_before INTEGER,
                balance_after INTEGER,
                no_balance_warning BOOLEAN NOT NULL DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
                CONSTRAINT uq_forecast_requests_bet_user UNIQUE (bet_id, user_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_bet_id ON forecast_requests (bet_id)",
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_user_id ON forecast_requests (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_handled_by ON forecast_requests (handled_by)",
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_status_created ON forecast_requests (status, created_at)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_user_id ON users (vk_user_id)",
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)",
            """
            CREATE TABLE IF NOT EXISTS personal_signals (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                text TEXT NOT NULL,
                type VARCHAR NOT NULL DEFAULT 'signal',
                data JSON,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            "ALTER TABLE personal_signals ADD COLUMN IF NOT EXISTS data JSON",
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_id ON personal_signals (id)",
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_user_id ON personal_signals (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_type ON personal_signals (type)",
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_user_created ON personal_signals (user_id, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_type_created ON personal_signals (type, created_at)",
            """
            CREATE TABLE IF NOT EXISTS delivery_outbox (
                id UUID PRIMARY KEY,
                channel VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                user_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
                personal_signal_id INTEGER REFERENCES personal_signals(id) ON DELETE SET NULL,
                forecast_request_id UUID REFERENCES forecast_requests(id) ON DELETE SET NULL,
                payload JSON NOT NULL DEFAULT '{}',
                dedupe_key VARCHAR,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                locked_at TIMESTAMP WITH TIME ZONE,
                sent_at TIMESTAMP WITH TIME ZONE,
                last_error TEXT,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_outbox_dedupe_key ON delivery_outbox (dedupe_key)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_channel ON delivery_outbox (channel)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_status ON delivery_outbox (status)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_user_id ON delivery_outbox (user_id)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_personal_signal_id ON delivery_outbox (personal_signal_id)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_forecast_request_id ON delivery_outbox (forecast_request_id)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_status_next_attempt ON delivery_outbox (status, next_attempt_at)",
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_channel_status ON delivery_outbox (channel, status)",
            """
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id UUID PRIMARY KEY,
                kind VARCHAR(32) NOT NULL DEFAULT 'support',
                owner_user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                assigned_staff_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'open',
                title VARCHAR(200),
                last_message_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                CONSTRAINT uq_chat_conversation_kind_owner UNIQUE (kind, owner_user_id),
                CONSTRAINT ck_chat_conversation_kind CHECK (kind IN ('support')),
                CONSTRAINT ck_chat_conversation_status CHECK (status IN ('open', 'closed'))
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_chat_conversations_status_last_message ON chat_conversations (status, last_message_at)",
            "CREATE INDEX IF NOT EXISTS ix_chat_conversations_owner ON chat_conversations (owner_user_id)",
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id BIGSERIAL PRIMARY KEY,
                conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                sender_user_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
                sender_role VARCHAR(32) NOT NULL,
                type VARCHAR(32) NOT NULL DEFAULT 'text',
                text TEXT,
                payload JSON NOT NULL DEFAULT '{}',
                client_message_id UUID NOT NULL,
                reply_to_id BIGINT REFERENCES chat_messages(id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                edited_at TIMESTAMP WITH TIME ZONE,
                deleted_at TIMESTAMP WITH TIME ZONE,
                CONSTRAINT uq_chat_message_sender_client UNIQUE (sender_user_id, client_message_id),
                CONSTRAINT ck_chat_message_type CHECK (type IN ('text', 'image', 'voice')),
                CONSTRAINT ck_chat_message_text_length CHECK (text IS NULL OR length(text) BETWEEN 1 AND 4000)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id_id ON chat_messages (conversation_id, id)",
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_created ON chat_messages (conversation_id, created_at)",
            """
            CREATE TABLE IF NOT EXISTS chat_read_cursors (
                conversation_id UUID NOT NULL REFERENCES chat_conversations(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                last_read_message_id BIGINT REFERENCES chat_messages(id) ON DELETE SET NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                PRIMARY KEY (conversation_id, user_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_chat_read_cursors_user ON chat_read_cursors (user_id)",
            """
            CREATE TABLE IF NOT EXISTS personal_signal_read_cursors (
                user_id BIGINT PRIMARY KEY REFERENCES users(telegram_id) ON DELETE CASCADE,
                last_read_signal_id INTEGER REFERENCES personal_signals(id) ON DELETE SET NULL,
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS message_templates (
                key VARCHAR PRIMARY KEY,
                title VARCHAR NOT NULL,
                description TEXT,
                body TEXT NOT NULL,
                variables JSON NOT NULL DEFAULT '[]'::json,
                updated_by BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_message_templates_updated_by ON message_templates (updated_by)",
            "CREATE INDEX IF NOT EXISTS ix_bets_status_delivery_created ON bets (status, delivery_mode, created_at)",
            "CREATE INDEX IF NOT EXISTS ix_bets_status_resolved ON bets (status, resolved_at)",
            "CREATE INDEX IF NOT EXISTS ix_bets_author_status_resolved ON bets (author_id, status, resolved_at)",
            "CREATE INDEX IF NOT EXISTS ix_user_bets_user_taken ON user_bets (user_id, taken_at)",
            "CREATE INDEX IF NOT EXISTS ix_user_bets_bet_user ON user_bets (bet_id, user_id)",
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_bet_status ON forecast_requests (bet_id, status)",
            """
            CREATE TABLE IF NOT EXISTS identity_device_links (
                device_key_hash VARCHAR(64) PRIMARY KEY,
                source_user_id BIGINT REFERENCES users(telegram_id) ON DELETE SET NULL,
                created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
                last_seen_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now()
            )
            """,
            "CREATE INDEX IF NOT EXISTS ix_identity_device_links_source_user_id ON identity_device_links (source_user_id)",
        ]
        for statement in statements:
            await conn.execute(text(statement))
        return

    if dialect == "sqlite":
        table_columns = {}
        for table in ("users", "bets", "promo_codes", "subscription_plans", "user_bets"):
            result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
            table_columns[table] = {row[1] for row in result.fetchall()}

        sqlite_columns = {
            "users": [
                ("alert_min_coef", "REAL NOT NULL DEFAULT 1.0"),
                ("odds_drop_notifications_enabled", "BOOLEAN NOT NULL DEFAULT 1"),
                ("referred_by_user_id", "BIGINT"),
                ("is_night_mode", "BOOLEAN NOT NULL DEFAULT 0"),
                ("night_mode_start", "VARCHAR(5) NOT NULL DEFAULT '23:00'"),
                ("night_mode_end", "VARCHAR(5) NOT NULL DEFAULT '08:00'"),
                ("experience_level", "VARCHAR"),
                ("bankroll_size", "VARCHAR"),
                ("favorite_sports", "JSON NOT NULL DEFAULT '[]'"),
                ("risk_tolerance", "VARCHAR"),
                ("primary_bookmaker", "VARCHAR"),
                ("vk_user_id", "VARCHAR"),
                ("phone", "VARCHAR"),
                ("vk_group_member", "BOOLEAN NOT NULL DEFAULT 0"),
                ("vk_messages_allowed", "BOOLEAN NOT NULL DEFAULT 0"),
                ("vk_notifications_allowed", "BOOLEAN NOT NULL DEFAULT 0"),
                ("web_push_subscription", "JSON"),
                ("photo_url", "TEXT"),
                ("vk_photo_url", "TEXT"),
                ("currency_preference", "VARCHAR NOT NULL DEFAULT 'RUB'"),
                ("purchased_bets_balance", "INTEGER NOT NULL DEFAULT 0"),
                ("preferred_sports", "JSON NOT NULL DEFAULT '[]'"),
                ("has_used_shield", "BOOLEAN NOT NULL DEFAULT 0"),
                ("other_bookmaker_name", "VARCHAR"),
                ("client_group", "VARCHAR"),
                ("client_tag", "VARCHAR"),
                ("matches_remaining", "INTEGER NOT NULL DEFAULT 0"),
                ("guarantee_active", "BOOLEAN NOT NULL DEFAULT 0"),
                ("guarantee_opened_from_bet_id", "CHAR(32)"),
                ("guarantee_closed_at", "DATETIME"),
            ],
            "subscription_plans": [
                ("match_count", "INTEGER NOT NULL DEFAULT 1"),
            ],
            "user_bets": [
                ("access_type", "VARCHAR NOT NULL DEFAULT 'paid_match'"),
                ("match_charged", "BOOLEAN NOT NULL DEFAULT 1"),
            ],
            "bets": [
                ("delivery_mode", "VARCHAR NOT NULL DEFAULT 'feed'"),
                ("fair_coefficient", "NUMERIC(5, 2)"),
                ("teaser_text", "TEXT"),
                ("sport_type", "VARCHAR"),
                ("outcome", "VARCHAR"),
                ("coupon_image_url", "VARCHAR"),
                ("match_link", "TEXT"),
                ("bookmaker_links", "JSON NOT NULL DEFAULT '[]'"),
                ("auto_send_on_interest", "BOOLEAN NOT NULL DEFAULT 0"),
            ],
            "promo_codes": [
                ("user_id", "BIGINT"),
            ],
        }
        for table, columns in sqlite_columns.items():
            for column_name, column_definition in columns:
                if column_name not in table_columns[table]:
                    await conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {column_name} {column_definition}"
                    )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS bet_bookmakers (
                bet_id CHAR(32) NOT NULL,
                bookmaker_id INTEGER NOT NULL,
                PRIMARY KEY (bet_id, bookmaker_id),
                FOREIGN KEY (bet_id) REFERENCES bets(id) ON DELETE CASCADE,
            FOREIGN KEY (bookmaker_id) REFERENCES bookmakers(id) ON DELETE CASCADE
            )
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS forecast_requests (
                id CHAR(32) NOT NULL,
                bet_id CHAR(32) NOT NULL,
                user_id BIGINT NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'announced',
                delivery_method VARCHAR,
                handled_by BIGINT,
                responded_at DATETIME,
                delivered_at DATETIME,
                balance_before INTEGER,
                balance_after INTEGER,
                no_balance_warning BOOLEAN NOT NULL DEFAULT 0,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                CONSTRAINT uq_forecast_requests_bet_user UNIQUE (bet_id, user_id),
                FOREIGN KEY (bet_id) REFERENCES bets(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_bet_id ON forecast_requests (bet_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_user_id ON forecast_requests (user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_handled_by ON forecast_requests (handled_by)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_status_created ON forecast_requests (status, created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS identity_device_links (
                device_key_hash VARCHAR(64) NOT NULL,
                source_user_id BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                last_seen_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (device_key_hash),
                FOREIGN KEY (source_user_id) REFERENCES users(telegram_id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_identity_device_links_source_user_id ON identity_device_links (source_user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_vk_user_id ON users (vk_user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS ix_users_phone ON users (phone)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS personal_signals (
                id INTEGER NOT NULL,
                user_id BIGINT NOT NULL,
                text TEXT NOT NULL,
                type VARCHAR NOT NULL DEFAULT 'signal',
                data JSON,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE
            )
            """
        )
        personal_signal_columns_result = await conn.exec_driver_sql("PRAGMA table_info(personal_signals)")
        personal_signal_columns = {row[1] for row in personal_signal_columns_result.fetchall()}
        if "data" not in personal_signal_columns:
            await conn.exec_driver_sql("ALTER TABLE personal_signals ADD COLUMN data JSON")
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_id ON personal_signals (id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_user_id ON personal_signals (user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_type ON personal_signals (type)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_user_created ON personal_signals (user_id, created_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_personal_signals_type_created ON personal_signals (type, created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS delivery_outbox (
                id CHAR(32) NOT NULL,
                channel VARCHAR NOT NULL,
                status VARCHAR NOT NULL DEFAULT 'pending',
                user_id BIGINT,
                personal_signal_id INTEGER,
                forecast_request_id CHAR(32),
                payload JSON NOT NULL DEFAULT '{}',
                dedupe_key VARCHAR,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                locked_at DATETIME,
                sent_at DATETIME,
                last_error TEXT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE SET NULL,
                FOREIGN KEY (personal_signal_id) REFERENCES personal_signals(id) ON DELETE SET NULL,
                FOREIGN KEY (forecast_request_id) REFERENCES forecast_requests(id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_delivery_outbox_dedupe_key ON delivery_outbox (dedupe_key)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_channel ON delivery_outbox (channel)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_status ON delivery_outbox (status)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_user_id ON delivery_outbox (user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_personal_signal_id ON delivery_outbox (personal_signal_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_forecast_request_id ON delivery_outbox (forecast_request_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_status_next_attempt ON delivery_outbox (status, next_attempt_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_delivery_outbox_channel_status ON delivery_outbox (channel, status)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS chat_conversations (
                id CHAR(32) NOT NULL,
                kind VARCHAR(32) NOT NULL DEFAULT 'support',
                owner_user_id BIGINT NOT NULL,
                assigned_staff_id BIGINT,
                status VARCHAR(16) NOT NULL DEFAULT 'open',
                title VARCHAR(200),
                last_message_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (id),
                CONSTRAINT uq_chat_conversation_kind_owner UNIQUE (kind, owner_user_id),
                CONSTRAINT ck_chat_conversation_kind CHECK (kind IN ('support')),
                CONSTRAINT ck_chat_conversation_status CHECK (status IN ('open', 'closed')),
                FOREIGN KEY (owner_user_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
                FOREIGN KEY (assigned_staff_id) REFERENCES users(telegram_id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_conversations_status_last_message ON chat_conversations (status, last_message_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_conversations_owner ON chat_conversations (owner_user_id)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS chat_messages (
                id INTEGER NOT NULL,
                conversation_id CHAR(32) NOT NULL,
                sender_user_id BIGINT,
                sender_role VARCHAR(32) NOT NULL,
                type VARCHAR(32) NOT NULL DEFAULT 'text',
                text TEXT,
                payload JSON NOT NULL DEFAULT '{}',
                client_message_id CHAR(32) NOT NULL,
                reply_to_id BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                edited_at DATETIME,
                deleted_at DATETIME,
                PRIMARY KEY (id),
                CONSTRAINT uq_chat_message_sender_client UNIQUE (sender_user_id, client_message_id),
                CONSTRAINT ck_chat_message_type CHECK (type IN ('text', 'image', 'voice')),
                CONSTRAINT ck_chat_message_text_length CHECK (text IS NULL OR length(text) BETWEEN 1 AND 4000),
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (sender_user_id) REFERENCES users(telegram_id) ON DELETE SET NULL,
                FOREIGN KEY (reply_to_id) REFERENCES chat_messages(id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_id_id ON chat_messages (conversation_id, id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_messages_conversation_created ON chat_messages (conversation_id, created_at)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS chat_read_cursors (
                conversation_id CHAR(32) NOT NULL,
                user_id BIGINT NOT NULL,
                last_read_message_id BIGINT,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (conversation_id, user_id),
                FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id) ON DELETE CASCADE,
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
                FOREIGN KEY (last_read_message_id) REFERENCES chat_messages(id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_chat_read_cursors_user ON chat_read_cursors (user_id)"
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS personal_signal_read_cursors (
                user_id BIGINT NOT NULL,
                last_read_signal_id INTEGER,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id),
                FOREIGN KEY (user_id) REFERENCES users(telegram_id) ON DELETE CASCADE,
                FOREIGN KEY (last_read_signal_id) REFERENCES personal_signals(id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            """
            CREATE TABLE IF NOT EXISTS message_templates (
                key VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                description TEXT,
                body TEXT NOT NULL,
                variables JSON NOT NULL DEFAULT '[]',
                updated_by BIGINT,
                created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (key),
                FOREIGN KEY (updated_by) REFERENCES users(telegram_id) ON DELETE SET NULL
            )
            """
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_message_templates_updated_by ON message_templates (updated_by)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_bets_status_delivery_created ON bets (status, delivery_mode, created_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_bets_status_resolved ON bets (status, resolved_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_bets_author_status_resolved ON bets (author_id, status, resolved_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_user_bets_user_taken ON user_bets (user_id, taken_at)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_user_bets_bet_user ON user_bets (bet_id, user_id)"
        )
        await conn.exec_driver_sql(
            "CREATE INDEX IF NOT EXISTS ix_forecast_requests_bet_status ON forecast_requests (bet_id, status)"
        )

async def check_abandoned_invoices(db):
    """Checks for pending payments older than threshold, updates status and pushes promo codes."""
    now = datetime.now(timezone.utc)
    # Debug mode checks older than 2 minutes; Production checks older than 20 minutes
    threshold = now - timedelta(minutes=2 if settings.DEBUG_MODE else 20)
    
    query = (
        select(Subscription)
        .filter(
            Subscription.status == "pending",
            Subscription.created_at < threshold
        )
    )
    res = await db.execute(query)
    abandoned_subs = res.scalars().all()
    
    for sub in abandoned_subs:
        sub.status = "abandoned"
        if not is_personal_telegram_user_id(sub.user_id):
            continue
        
        # Dispatch nudge message with discount coupon
        push_text = (
            "⏳ Заметили, что вы интересовались тарифами, но не завершили покупку.\n\n"
            "Дарим вам секретный промокод HALF50 на скидку 50%! 🎁\n"
            "Вернитесь в приложение и примените его перед оплатой."
        )
        run_telegram_api_background("sendMessage", {
            "chat_id": sub.user_id,
            "text": push_text
        })
        
    if abandoned_subs:
        await db.commit()
        print(f"[Daemon] Cart Recovery: marked {len(abandoned_subs)} pending invoices as abandoned and sent push coupons.")

async def abandoned_cart_recovery_daemon():
    """Background execution loop."""
    print("[Daemon] Abandoned Cart Recovery daemon initialized.")
    while True:
        # Check every 60s in debug/dev, every 15 mins in production
        sleep_time = 60 if settings.DEBUG_MODE else 900
        await asyncio.sleep(sleep_time)
        
        async with AsyncSessionLocal() as db:
            try:
                await check_abandoned_invoices(db)
            except Exception as e:
                print(f"[Daemon] Error in cart recovery daemon tick: {e}")

async def check_expired_vip_subscriptions(db):
    """
    Checks for active user subscriptions that have expired.
    Kicks (bans and immediately unbans) the user from the configured VIP Telegram Chat/Channel.
    Updates the User's tg_chat_joined status to False.
    """
    now = datetime.now(timezone.utc)
    from src.models.models import User
    
    # Select all users currently marked as tg_chat_joined
    query_users = select(User).filter(User.tg_chat_joined == True, User.telegram_id > 0)
    res_users = await db.execute(query_users)
    users = res_users.scalars().all()
    
    chat_id = settings.TELEGRAM_VIP_CHAT_ID
    
    kicked_count = 0
    for user in users:
        has_sub = (
            (user.purchased_bets_balance or 0) > 0
            or (user.matches_remaining or 0) > 0
            or bool(user.guarantee_active)
        )
        
        # If no active sub (and user is not admin), they must be removed
        if not has_sub and not is_staff_role(user.role) and user_can_receive_personal_telegram(user):
            # Invoke Telegram kick API
            await call_telegram_api_async("banChatMember", {
                "chat_id": chat_id,
                "user_id": user.telegram_id
            })
            await call_telegram_api_async("unbanChatMember", {
                "chat_id": chat_id,
                "user_id": user.telegram_id
            })
            
            user.tg_chat_joined = False
            kicked_count += 1
            
            # Send notification
            run_telegram_api_background("sendMessage", {
                "chat_id": user.telegram_id,
                "text": "🔴 Срок вашей VIP-подписки истек, вы были автоматически исключены из закрытого канала Shamrai Analytics Hub."
            })
            
    if kicked_count > 0:
        await db.commit()
        print(f"[Daemon] VIP Auto-pilot: Kicked {kicked_count} expired members from chat.")

async def vip_chat_expirations_daemon():
    """VIP autopilot ban background loop."""
    print("[Daemon] VIP Chat Expirations autopilot initialized.")
    while True:
        # Check every 60s in debug/dev, every hour in production
        sleep_time = 60 if settings.DEBUG_MODE else 3600
        await asyncio.sleep(sleep_time)
        
        async with AsyncSessionLocal() as db:
            try:
                await check_expired_vip_subscriptions(db)
            except Exception as e:
                print(f"[Daemon] Error in VIP autopilot tick: {e}")


TUNNEL_LOG_PATTERNS = [
    ("/app/localhost_run.log", r"https://[a-zA-Z0-9-]+\.lhr\.life"),
    ("/app/serveo_subdomain.log", r"https://[a-zA-Z0-9-]+\.serveousercontent\.com"),
    ("/app/serveo.log", r"https://[a-zA-Z0-9-]+\.serveousercontent\.com"),
]
TELEGRAM_ALLOWED_UPDATES = ["message", "callback_query", "pre_checkout_query"]


def _active_tunnel_url_from_logs() -> str | None:
    import os
    import re
    import urllib.request

    tunnel_urls: list[str] = []
    for log_path, pattern in TUNNEL_LOG_PATTERNS:
        try:
            if not os.path.exists(log_path):
                continue
            with open(log_path, "r", encoding="utf-8", errors="ignore") as log_file:
                tunnel_urls.extend(re.findall(pattern, log_file.read()))
        except Exception as e:
            print(f"[Webhook] Error reading tunnel log {log_path}: {e}")

    latest_tunnel_url = tunnel_urls[-1] if tunnel_urls else None

    for tunnel_url in reversed(tunnel_urls):
        try:
            direct_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with direct_opener.open(f"{tunnel_url.rstrip('/')}/api/health", timeout=4) as response:
                if response.status < 500:
                    return tunnel_url
        except Exception as e:
            print(f"[Webhook] Tunnel health check failed for {tunnel_url}: {e}")

    return latest_tunnel_url


def _telegram_webhook_base() -> str:
    return (
        (_active_tunnel_url_from_logs() if settings.DEBUG_MODE else None)
        or settings.API_BASE_URL.strip()
        or settings.FRONTEND_BASE_URL.strip()
    )


def _telegram_webhook_url() -> str:
    webhook_base = _telegram_webhook_base().strip()
    if not webhook_base:
        return ""
    return f"{webhook_base.rstrip('/')}/api/telegram/webhook"


def _telegram_allowed_updates_need_repair(actual_updates: object) -> bool:
    if not isinstance(actual_updates, list) or not actual_updates:
        return False
    actual = {str(update) for update in actual_updates}
    return not set(TELEGRAM_ALLOWED_UPDATES).issubset(actual)


def _telegram_webhook_registration_payload() -> dict:
    payload = {
        "max_connections": 100,
        "allowed_updates": TELEGRAM_ALLOWED_UPDATES,
    }
    if settings.TELEGRAM_WEBHOOK_SECRET_TOKEN:
        payload["secret_token"] = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN
    webhook_ip_address = settings.TELEGRAM_WEBHOOK_IP_ADDRESS.strip()
    if webhook_ip_address:
        payload["ip_address"] = webhook_ip_address
    return payload


async def tunnel_webhook_monitor_daemon():
    """
    Periodically keeps Telegram pointed at the latest live tunnel URL.
    """
    print("[Daemon] Tunnel Webhook Monitor daemon initialized.")
    webhook_payload_extra = _telegram_webhook_registration_payload()

    while True:
        # Check every 60 seconds
        await asyncio.sleep(60)

        if not settings.has_real_telegram_token:
            continue

        webhook_url = _telegram_webhook_url()
        if webhook_url:
            try:
                # Retrieve current webhook configuration from Telegram API
                webhook_info = await call_telegram_api_async("getWebhookInfo", {})
                current_webhook = webhook_info.get("result", {}) if webhook_info.get("ok") else {}
                current_url = current_webhook.get("url", "")
                current_allowed_updates = current_webhook.get("allowed_updates")
                allowed_updates_need_repair = _telegram_allowed_updates_need_repair(current_allowed_updates)

                if current_url != webhook_url or allowed_updates_need_repair:
                    reason = "URL mismatch" if current_url != webhook_url else "allowed_updates missing callback_query"
                    print(f"[Daemon] Webhook {reason}. Telegram has: '{current_url}', expected: '{webhook_url}'. Re-registering...")
                    res = await call_telegram_api_async("setWebhook", {"url": webhook_url, **webhook_payload_extra})
                    print(f"[Daemon] Webhook re-registration result: {res}")
            except Exception as e:
                print(f"[Daemon] Error in webhook monitor tick: {e}")


def _dispatch_polling_response(response: dict) -> bool:
    if not isinstance(response, dict):
        return True

    method = response.get("method")
    if not method:
        return True

    payload = {key: value for key, value in response.items() if key != "method"}
    text = str(payload.get("text") or "")
    is_start_response = (
        method == "sendMessage"
        and text.startswith("👋")
        and "Открыть Shamrai" in text
    )
    timeout = settings.TELEGRAM_START_RESPONSE_TIMEOUT_SECONDS if is_start_response else 6
    retries = 0 if is_start_response else 2
    result = call_telegram_api(method, payload, timeout, retries)
    if not result.get("ok"):
        description = result.get("description", "unknown error")
        if _is_web_app_button_type_error(method, payload, description):
            fallback_payload = _telegram_url_button_fallback_payload(payload)
            if fallback_payload:
                fallback_result = call_telegram_api(method, fallback_payload, timeout, retries)
                if fallback_result.get("ok"):
                    print("[Daemon] Telegram web_app button fallback delivered with url button")
                    return True
                description = fallback_result.get("description", description)
        print(f"[Daemon] Telegram response dispatch failed: {method}: {description}")
        if method == "answerCallbackQuery":
            return True
        return False
    return True


def _is_web_app_button_type_error(method: str, payload: dict, description: str) -> bool:
    if method != "sendMessage":
        return False
    if "BUTTON_TYPE_INVALID" not in description:
        return False
    return bool(_telegram_url_button_fallback_payload(payload))


def _telegram_url_button_fallback_payload(payload: dict) -> dict | None:
    reply_markup = payload.get("reply_markup")
    if not isinstance(reply_markup, dict):
        return None
    inline_keyboard = reply_markup.get("inline_keyboard")
    if not isinstance(inline_keyboard, list):
        return None

    changed = False
    fallback_keyboard = []
    for row in inline_keyboard:
        if not isinstance(row, list):
            return None
        fallback_row = []
        for button in row:
            if not isinstance(button, dict):
                return None
            fallback_button = dict(button)
            web_app = fallback_button.get("web_app")
            if isinstance(web_app, dict) and web_app.get("url"):
                fallback_button.pop("web_app", None)
                fallback_button["url"] = str(web_app["url"])
                changed = True
            fallback_row.append(fallback_button)
        fallback_keyboard.append(fallback_row)

    if not changed:
        return None
    return {
        **payload,
        "reply_markup": {
            **reply_markup,
            "inline_keyboard": fallback_keyboard,
        },
    }


async def telegram_polling_daemon():
    """
    Fallback update loop for servers that Telegram cannot reach reliably by webhook.
    """
    print("[Daemon] Telegram polling daemon initialized.")
    offset = None
    while True:
        if not settings.has_real_telegram_token:
            await asyncio.sleep(5)
            continue

        payload = {
            "timeout": 1,
            "limit": 50,
            "allowed_updates": TELEGRAM_ALLOWED_UPDATES,
        }
        if offset is not None:
            payload["offset"] = offset

        try:
            updates_result = await asyncio.to_thread(
                call_telegram_api,
                "getUpdates",
                payload,
                6,
                0,
            )
            if not updates_result.get("ok"):
                print(f"[Daemon] Telegram getUpdates failed: {updates_result.get('description', 'unknown error')}")
                await asyncio.sleep(1)
                continue

            updates = updates_result.get("result") or []
            for update in updates:
                update_id = update.get("update_id")

                try:
                    response = await telegram_webhook.handle_telegram_update(
                        update,
                        request_base_url=settings.API_BASE_URL.rstrip("/"),
                    )
                    dispatched = await asyncio.to_thread(_dispatch_polling_response, response)
                    if not dispatched:
                        print(f"[Daemon] Telegram polling response not delivered for update_id={update_id}; retrying later")
                        break
                    if update_id is not None:
                        offset = int(update_id) + 1
                except Exception as exc:
                    print(f"[Daemon] Telegram polling update failed: {exc}")
                    break
        except Exception as exc:
            print(f"[Daemon] Telegram polling tick failed: {exc}")
            await asyncio.sleep(3)


def _vk_event_object_from_conversation_item(item: dict) -> dict | None:
    if not isinstance(item, dict):
        return None
    message = dict(item.get("last_message") or {})
    if not message:
        return None
    if str(message.get("out") or "").strip() in {"1", "true", "True"}:
        return None

    conversation = item.get("conversation") or {}
    peer = conversation.get("peer") or {}
    peer_id = message.get("peer_id") or peer.get("id")
    from_id = message.get("from_id")
    if not peer_id or not from_id:
        return None
    try:
        if int(from_id) <= 0:
            return None
    except (TypeError, ValueError):
        return None

    message["peer_id"] = peer_id
    return {
        "message": message,
        "user_id": from_id,
        "peer_id": peer_id,
    }


async def vk_dialog_polling_daemon():
    """
    Fallback for VK communities where Callback API message_new events are not delivered.
    It reads unread dialogs with the group token and runs the same handler as callbacks.
    """
    print("[Daemon] VK dialog polling daemon initialized.")

    while True:
        interval = max(1.0, float(settings.VK_DIALOG_POLLING_INTERVAL_SECONDS or 4.0))
        if not settings.VK_DIALOG_POLLING_ENABLED:
            await asyncio.sleep(interval)
            continue
        if not vk_delivery_configured():
            await asyncio.sleep(interval)
            continue

        try:
            result = await asyncio.to_thread(
                get_vk_unread_conversations,
                settings.VK_DIALOG_POLLING_BATCH_SIZE,
            )
            if not result.get("ok"):
                print(f"[Daemon] VK dialog polling failed: {result.get('description', 'unknown error')}")
                await asyncio.sleep(interval)
                continue

            response = result.get("response") or {}
            items = response.get("items") or []
            for item in items:
                event_object = _vk_event_object_from_conversation_item(item)
                if not event_object:
                    continue
                await vk_callback.handle_vk_message_new_event(event_object, source="polling")
                mark_result = await asyncio.to_thread(
                    mark_vk_conversation_read,
                    event_object.get("peer_id"),
                )
                if not mark_result.get("ok"):
                    print(
                        "[Daemon] VK dialog markAsRead failed: "
                        f"{mark_result.get('description', 'unknown error')}"
                    )
        except Exception as exc:
            print(f"[Daemon] VK dialog polling tick failed: {exc}")

        await asyncio.sleep(interval)


def _telegram_api_host_entries() -> list[str]:
    entries: list[str] = []
    hosts_paths = ["/etc/hosts", r"C:\Windows\System32\drivers\etc\hosts"]

    for hosts_path in hosts_paths:
        if not os.path.exists(hosts_path):
            continue
        try:
            with open(hosts_path, "r", encoding="utf-8", errors="ignore") as hosts_file:
                for line in hosts_file:
                    stripped = line.strip()
                    if stripped and not stripped.startswith("#") and "api.telegram.org" in stripped:
                        entries.append(stripped)
        except Exception as exc:
            entries.append(f"{hosts_path}: unreadable: {exc}")

    return entries


def _telegram_api_resolved_addresses() -> list[str]:
    try:
        address_infos = socket.getaddrinfo("api.telegram.org", 443, proto=socket.IPPROTO_TCP)
    except Exception as exc:
        return [f"unresolved: {exc}"]

    return sorted({item[4][0] for item in address_infos})


def _telegram_api_probe(method: str, payload: dict | None = None) -> dict:
    started_at = time.perf_counter()
    result = call_telegram_api(
        method,
        payload or {},
        settings.TELEGRAM_API_TIMEOUT_SECONDS,
        0,
    )
    duration_ms = round((time.perf_counter() - started_at) * 1000)

    probe = {
        "ok": bool(result.get("ok")),
        "duration_ms": duration_ms,
    }
    if not result.get("ok"):
        probe["description"] = result.get("description", "unknown error")

    if method == "getWebhookInfo" and result.get("ok"):
        webhook = result.get("result") or {}
        actual_url = webhook.get("url") or ""
        probe["url_set"] = bool(actual_url)
        probe["actual_url"] = actual_url
        probe["pending_update_count"] = webhook.get("pending_update_count", 0)
        if webhook.get("max_connections") is not None:
            probe["max_connections"] = webhook.get("max_connections")
        if webhook.get("allowed_updates") is not None:
            probe["allowed_updates"] = webhook.get("allowed_updates")
        probe["allowed_updates_current"] = not _telegram_allowed_updates_need_repair(
            webhook.get("allowed_updates")
        )
        if webhook.get("last_error_message"):
            probe["last_error_message"] = webhook.get("last_error_message")
        if webhook.get("last_error_date"):
            probe["last_error_date"] = webhook.get("last_error_date")

    if method == "getMe" and result.get("ok"):
        bot = result.get("result") or {}
        probe["username"] = bot.get("username")

    return probe


async def configure_telegram_delivery_on_startup():
    if not settings.has_real_telegram_token:
        return

    try:
        webhook_base = _telegram_webhook_base()
        webhook_url = _telegram_webhook_url()

        if settings.TELEGRAM_USE_POLLING:
            print("[Lifespan] Telegram polling enabled. Scheduling webhook deletion without blocking startup.")
            result = await asyncio.to_thread(
                call_telegram_api,
                "deleteWebhook",
                {"drop_pending_updates": False},
                settings.TELEGRAM_API_TIMEOUT_SECONDS,
                1,
            )
            if not result.get("ok"):
                print(f"[Lifespan] Telegram deleteWebhook failed: {result.get('description', 'unknown error')}")
        else:
            print(f"[Lifespan] Scheduling Telegram Webhook registration to {webhook_url}")
            webhook_payload = {
                "url": webhook_url,
                **_telegram_webhook_registration_payload(),
            }
            result = await asyncio.to_thread(
                call_telegram_api,
                "setWebhook",
                webhook_payload,
                settings.TELEGRAM_API_TIMEOUT_SECONDS,
                1,
            )
            if not result.get("ok"):
                print(f"[Lifespan] Telegram setWebhook failed: {result.get('description', 'unknown error')}")

        menu_base = settings.FRONTEND_BASE_URL.rstrip("/") if settings.FRONTEND_BASE_URL else webhook_base
        print(f"[Lifespan] Scheduling Telegram Menu Button registration to {menu_base}")
        menu_result = await asyncio.to_thread(
            call_telegram_api,
            "setChatMenuButton",
            {
                "menu_button": {
                    "type": "web_app",
                    "text": "Открыть Shamrai",
                    "web_app": {
                        "url": menu_base
                    }
                }
            },
            settings.TELEGRAM_API_TIMEOUT_SECONDS,
            1,
        )
        if not menu_result.get("ok"):
            print(f"[Lifespan] Telegram setChatMenuButton failed: {menu_result.get('description', 'unknown error')}")
    except Exception as exc:
        print(f"[Lifespan] Telegram startup configuration failed: {exc}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.validate_runtime_security()
    log_vk_runtime_config()

    if settings.DEBUG_MODE:
        # Local/dev convenience only. Production schema changes should go through Alembic.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await run_dev_schema_migrations(conn)
        
    # Set proxy if configured
    if settings.HTTPS_PROXY:
        print("[Lifespan] Setting global HTTPS proxy: configured")
        os.environ["HTTPS_PROXY"] = settings.HTTPS_PROXY
        os.environ["https_proxy"] = settings.HTTPS_PROXY
 
    polling_task = None
    telegram_startup_task = None
    if settings.has_real_telegram_token:
        telegram_startup_task = asyncio.create_task(configure_telegram_delivery_on_startup())
        if settings.TELEGRAM_USE_POLLING:
            polling_task = asyncio.create_task(telegram_polling_daemon())

    daemon_task = None
    vip_daemon_task = None
    delivery_outbox_task = None
    webhook_monitor_task = None
    vk_dialog_polling_task = None
    if settings.VK_DIALOG_POLLING_ENABLED:
        vk_dialog_polling_task = asyncio.create_task(vk_dialog_polling_daemon())

    if settings.ENABLE_BACKGROUND_TASKS:
        daemon_task = asyncio.create_task(abandoned_cart_recovery_daemon())
        vip_daemon_task = asyncio.create_task(vip_chat_expirations_daemon())
        delivery_outbox_task = asyncio.create_task(delivery_outbox_daemon())
        if settings.DEBUG_MODE:
            webhook_monitor_task = asyncio.create_task(tunnel_webhook_monitor_daemon())
    
    yield
    
    # Cancel tasks on shutdown
    for task in (
        daemon_task,
        vip_daemon_task,
        delivery_outbox_task,
        webhook_monitor_task,
        vk_dialog_polling_task,
        polling_task,
        telegram_startup_task,
    ):
        if task:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(
    title="Telegram Mini App Sports Betting API",
    version="1.0.0",
    lifespan=lifespan
)

# CORS configuration for development environment.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SecurityRateLimitMiddleware, limiter=security_rate_limiter)

# Mount static files directory for serving uploaded coupon images
import os
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
mimetypes.add_type("audio/webm", ".webm")
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include endpoint routers under /api prefix
app.include_router(auth.router, prefix="/api")
app.include_router(users.router, prefix="/api")
app.include_router(bets.router, prefix="/api")
app.include_router(subscriptions.router, prefix="/api")
app.include_router(payments.router, prefix="/api")
app.include_router(stats.router, prefix="/api")
app.include_router(marketing.router, prefix="/api")
app.include_router(admin.router, prefix="/api")
app.include_router(admin_web_chat.router, prefix="/api")
app.include_router(admin_broadcast.router, prefix="/api")
app.include_router(crowd_bets.router, prefix="/api")
app.include_router(telegram_webhook.router, prefix="/api")
app.include_router(vk_callback.router, prefix="/api")
app.include_router(signals.router, prefix="/api")
app.include_router(chat.router, prefix="/api")
app.include_router(go.router, prefix="/api")


async def require_health_diagnostics_access(
    current_user: Optional[User] = Depends(get_optional_user_read),
) -> None:
    if settings.DEBUG_MODE:
        return
    if current_user and is_staff_role(current_user.role):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Health diagnostics require staff access",
    )


@app.get("/api/health")
async def health_check():
    """Simple container sanity health-check."""
    return {"status": "ok", "message": "Betting TMA service active"}


@app.get("/api/health/payments", dependencies=[Depends(require_health_diagnostics_access)])
async def payments_health_check():
    """Safe payment diagnostics without exposing provider credentials."""
    yookassa_return_url = settings.YOOKASSA_RETURN_URL.strip() or settings.FRONTEND_BASE_URL.strip()
    tegro_return_url = settings.TEGRO_RETURN_URL.strip() or settings.FRONTEND_BASE_URL.strip()
    return {
        "ok": bool(
            settings.has_real_telegram_token
            or settings.has_yookassa_credentials
            or settings.has_tegro_credentials
            or settings.DEBUG_MODE
        ),
        "app_env": settings.APP_ENV,
        "debug_checkout_enabled": settings.DEBUG_MODE,
        "telegram_stars": {
            "configured": settings.has_real_telegram_token,
            "mode": "live" if settings.has_real_telegram_token else "not_configured",
        },
        "yookassa": {
            "configured": settings.has_yookassa_credentials,
            "return_url_configured": bool(yookassa_return_url),
        },
        "tegro": {
            "configured": settings.has_tegro_credentials,
            "api_configured": settings.has_tegro_api_credentials,
            "return_url_configured": bool(tegro_return_url),
        },
        "production_requirements_met": (
            not settings.is_production
            or (settings.has_real_telegram_token and settings.has_ruble_payment_provider)
        ),
    }


@app.get("/api/health/telegram", dependencies=[Depends(require_health_diagnostics_access)])
async def telegram_health_check():
    """Safe Telegram diagnostics without exposing tokens or secrets."""
    expected_webhook_url = _telegram_webhook_url()
    if not settings.has_real_telegram_token:
        return {
            "ok": False,
            "status": "not_configured",
            "polling_enabled": settings.TELEGRAM_USE_POLLING,
            "proxy_set": bool(settings.HTTPS_PROXY.strip()),
            "expected_webhook_url": expected_webhook_url,
            "resolved_addresses": _telegram_api_resolved_addresses(),
            "hosts_entries": _telegram_api_host_entries(),
        }

    started_at = time.perf_counter()
    get_me = await asyncio.to_thread(_telegram_api_probe, "getMe", {})
    webhook_info = await asyncio.to_thread(_telegram_api_probe, "getWebhookInfo", {})
    actual_webhook_url = webhook_info.get("actual_url", "")
    webhook_matches_expected = bool(expected_webhook_url and actual_webhook_url == expected_webhook_url)
    allowed_updates_current = bool(webhook_info.get("allowed_updates_current"))
    webhook_delivery_ok = (not settings.TELEGRAM_USE_POLLING) and webhook_matches_expected and allowed_updates_current
    polling_delivery_ok = settings.TELEGRAM_USE_POLLING and get_me.get("ok") and webhook_info.get("ok")
    delivery_ok = bool(webhook_delivery_ok or polling_delivery_ok)
    status_value = "ok" if get_me.get("ok") and webhook_info.get("ok") and delivery_ok else "misconfigured"

    return {
        "ok": bool(get_me.get("ok") and webhook_info.get("ok") and delivery_ok),
        "status": status_value,
        "delivery_mode": "polling" if settings.TELEGRAM_USE_POLLING else "webhook",
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "polling_enabled": settings.TELEGRAM_USE_POLLING,
        "proxy_set": bool(settings.HTTPS_PROXY.strip()),
        "api_timeout_seconds": settings.TELEGRAM_API_TIMEOUT_SECONDS,
        "api_retries": settings.TELEGRAM_API_RETRIES,
        "webhook_ip_forced": bool(settings.TELEGRAM_WEBHOOK_IP_ADDRESS.strip()),
        "expected_webhook_url": expected_webhook_url,
        "actual_webhook_url": actual_webhook_url,
        "webhook_matches_expected": webhook_matches_expected,
        "allowed_updates_current": allowed_updates_current,
        "pending_update_count": webhook_info.get("pending_update_count", 0),
        "last_error_message": webhook_info.get("last_error_message"),
        "last_error_date": webhook_info.get("last_error_date"),
        "resolved_addresses": _telegram_api_resolved_addresses(),
        "hosts_entries": _telegram_api_host_entries(),
        "checks": {
            "getMe": get_me,
            "getWebhookInfo": webhook_info,
        },
    }


@app.get("/api/health/vk", dependencies=[Depends(require_health_diagnostics_access)])
async def vk_health_check():
    """Fast VK configuration diagnostics without exposing tokens or callback secrets."""
    group_id = vk_group_id()
    token_configured = bool(settings.VK_GROUP_ACCESS_TOKEN.strip())
    confirmation_configured = bool(settings.VK_CALLBACK_CONFIRMATION_CODE.strip())
    configured = vk_delivery_configured()

    return {
        "ok": configured,
        "configured": configured,
        "group_id_set": bool(group_id),
        "token_configured": token_configured,
        "callback_secret_configured": bool(settings.VK_CALLBACK_SECRET.strip()),
        "confirmation_code_configured": confirmation_configured,
        "callback_endpoint_configured": True,
        "dialog_polling_enabled": settings.VK_DIALOG_POLLING_ENABLED,
        "dialog_polling_interval_seconds": settings.VK_DIALOG_POLLING_INTERVAL_SECONDS,
        "confirmation_self_check": "configured" if confirmation_configured else "missing",
    }


@app.get("/api/health/vk/deep", dependencies=[Depends(require_health_diagnostics_access)])
async def vk_deep_health_check():
    """Safe live VK API diagnostics without exposing tokens or callback secrets."""
    group_id = vk_group_id()
    token_configured = bool(settings.VK_GROUP_ACCESS_TOKEN.strip())
    confirmation_configured = bool(settings.VK_CALLBACK_CONFIRMATION_CODE.strip())
    configured = vk_delivery_configured()
    started_at = time.perf_counter()
    api_probe_ok = await asyncio.to_thread(probe_vk_api) if configured else False

    return {
        "ok": bool(configured and api_probe_ok),
        "configured": configured,
        "group_id_set": bool(group_id),
        "token_configured": token_configured,
        "callback_secret_configured": bool(settings.VK_CALLBACK_SECRET.strip()),
        "confirmation_code_configured": confirmation_configured,
        "callback_endpoint_configured": True,
        "dialog_polling_enabled": settings.VK_DIALOG_POLLING_ENABLED,
        "dialog_polling_interval_seconds": settings.VK_DIALOG_POLLING_INTERVAL_SECONDS,
        "confirmation_self_check": "configured" if confirmation_configured else "missing",
        "api_probe_ok": api_probe_ok,
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
    }
