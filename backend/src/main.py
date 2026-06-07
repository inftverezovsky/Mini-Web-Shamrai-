import asyncio
import socket
import time
from datetime import datetime, timezone, timedelta
from contextlib import asynccontextmanager
from contextlib import suppress
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.future import select

from src.models.database import Base, engine, AsyncSessionLocal
from src.models.models import Subscription
from src.core.config import settings
from src.core.roles import is_staff_role
from src.api.payments import call_telegram_api
from src.api import auth, users, bets, subscriptions, payments, stats, marketing, admin, admin_broadcast, crowd_bets, telegram_webhook


async def run_dev_schema_migrations(conn):
    """
    Keeps the local/dev database compatible with model changes.
    This is intentionally narrow; production should use Alembic migrations.
    """
    dialect = conn.dialect.name

    if dialect == "postgresql":
        statements = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS alert_min_coef DOUBLE PRECISION NOT NULL DEFAULT 1.0",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS referred_by_user_id BIGINT",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_night_mode BOOLEAN NOT NULL DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS experience_level VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS bankroll_size VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS favorite_sports JSON NOT NULL DEFAULT '[]'::json",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS risk_tolerance VARCHAR",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS primary_bookmaker VARCHAR",
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
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS sport_type VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS outcome VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS coupon_image_url VARCHAR",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS match_link TEXT",
            "ALTER TABLE bets ADD COLUMN IF NOT EXISTS bookmaker_links JSON NOT NULL DEFAULT '[]'::json",
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
                ("referred_by_user_id", "BIGINT"),
                ("is_night_mode", "BOOLEAN NOT NULL DEFAULT 0"),
                ("experience_level", "VARCHAR"),
                ("bankroll_size", "VARCHAR"),
                ("favorite_sports", "JSON NOT NULL DEFAULT '[]'"),
                ("risk_tolerance", "VARCHAR"),
                ("primary_bookmaker", "VARCHAR"),
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
                ("sport_type", "VARCHAR"),
                ("outcome", "VARCHAR"),
                ("coupon_image_url", "VARCHAR"),
                ("match_link", "TEXT"),
                ("bookmaker_links", "JSON NOT NULL DEFAULT '[]'"),
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
        
        # Dispatch nudge message with discount coupon
        push_text = (
            "⏳ Заметили, что вы интересовались тарифами, но не завершили покупку.\n\n"
            "Дарим вам секретный промокод HALF50 на скидку 50%! 🎁\n"
            "Вернитесь в приложение и примените его перед оплатой."
        )
        call_telegram_api("sendMessage", {
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
    query_users = select(User).filter(User.tg_chat_joined == True)
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
        if not has_sub and not is_staff_role(user.role):
            # Invoke Telegram kick API
            call_telegram_api("banChatMember", {
                "chat_id": chat_id,
                "user_id": user.telegram_id
            })
            call_telegram_api("unbanChatMember", {
                "chat_id": chat_id,
                "user_id": user.telegram_id
            })
            
            user.tg_chat_joined = False
            kicked_count += 1
            
            # Send notification
            call_telegram_api("sendMessage", {
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
    return _active_tunnel_url_from_logs() or settings.FRONTEND_BASE_URL.strip()


async def tunnel_webhook_monitor_daemon():
    """
    Periodically keeps Telegram pointed at the latest live tunnel URL.
    """
    from src.api.payments import call_telegram_api

    print("[Daemon] Tunnel Webhook Monitor daemon initialized.")
    webhook_payload_extra = {"max_connections": 100}
    if settings.TELEGRAM_WEBHOOK_SECRET_TOKEN:
        webhook_payload_extra["secret_token"] = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN

    while True:
        # Check every 60 seconds
        await asyncio.sleep(60)

        if not settings.has_real_telegram_token:
            continue

        webhook_base = _telegram_webhook_base()
        if webhook_base:
            webhook_url = f"{webhook_base.rstrip('/')}/api/telegram/webhook"
            try:
                # Retrieve current webhook configuration from Telegram API
                webhook_info = call_telegram_api("getWebhookInfo", {})
                current_url = webhook_info.get("result", {}).get("url", "")
                
                if current_url != webhook_url:
                    print(f"[Daemon] Webhook mismatch! Telegram has: '{current_url}', expected: '{webhook_url}'. Re-registering...")
                    res = call_telegram_api("setWebhook", {"url": webhook_url, **webhook_payload_extra})
                    print(f"[Daemon] Webhook re-registration result: {res}")
            except Exception as e:
                print(f"[Daemon] Error in webhook monitor tick: {e}")


def _dispatch_polling_response(response: dict) -> bool:
    method = response.get("method")
    if not method:
        return True

    payload = {key: value for key, value in response.items() if key != "method"}
    result = call_telegram_api(method, payload, 6, 2)
    if not result.get("ok"):
        print(f"[Daemon] Telegram response dispatch failed: {method}: {result.get('description', 'unknown error')}")
        return False
    return True


async def telegram_polling_daemon():
    """
    Fallback update loop for servers that Telegram cannot reach reliably by webhook.
    """
    print("[Daemon] Telegram polling daemon initialized.")
    offset = None
    allowed_updates = ["message", "callback_query", "pre_checkout_query"]

    while True:
        if not settings.has_real_telegram_token:
            await asyncio.sleep(5)
            continue

        payload = {
            "timeout": 2,
            "limit": 50,
            "allowed_updates": allowed_updates,
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
        probe["url_set"] = bool(webhook.get("url"))
        probe["pending_update_count"] = webhook.get("pending_update_count", 0)
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
        webhook_url = f"{webhook_base.rstrip('/')}/api/telegram/webhook"

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
            webhook_payload = {"url": webhook_url, "max_connections": 100}
            if settings.TELEGRAM_WEBHOOK_SECRET_TOKEN:
                webhook_payload["secret_token"] = settings.TELEGRAM_WEBHOOK_SECRET_TOKEN
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

    if settings.DEBUG_MODE:
        # Local/dev convenience only. Production schema changes should go through Alembic.
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await run_dev_schema_migrations(conn)
        
    # Set proxy if configured
    if settings.HTTPS_PROXY:
        print(f"[Lifespan] Setting global HTTPS proxy: {settings.HTTPS_PROXY}")
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
    webhook_monitor_task = None
    if settings.ENABLE_BACKGROUND_TASKS:
        daemon_task = asyncio.create_task(abandoned_cart_recovery_daemon())
        vip_daemon_task = asyncio.create_task(vip_chat_expirations_daemon())
        if settings.DEBUG_MODE:
            webhook_monitor_task = asyncio.create_task(tunnel_webhook_monitor_daemon())
    
    yield
    
    # Cancel tasks on shutdown
    for task in (daemon_task, vip_daemon_task, webhook_monitor_task, polling_task, telegram_startup_task):
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

# Mount static files directory for serving uploaded coupon images
import os
static_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")
os.makedirs(static_dir, exist_ok=True)
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
app.include_router(admin_broadcast.router, prefix="/api")
app.include_router(crowd_bets.router, prefix="/api")
app.include_router(telegram_webhook.router, prefix="/api")

@app.get("/api/health")
async def health_check():
    """Simple container sanity health-check."""
    return {"status": "ok", "message": "Betting TMA service active"}


@app.get("/api/health/telegram")
async def telegram_health_check():
    """Safe Telegram diagnostics without exposing tokens or secrets."""
    if not settings.has_real_telegram_token:
        return {
            "ok": False,
            "status": "not_configured",
            "polling_enabled": settings.TELEGRAM_USE_POLLING,
            "proxy_set": bool(settings.HTTPS_PROXY.strip()),
            "resolved_addresses": _telegram_api_resolved_addresses(),
            "hosts_entries": _telegram_api_host_entries(),
        }

    started_at = time.perf_counter()
    get_me = await asyncio.to_thread(_telegram_api_probe, "getMe", {})
    webhook_info = await asyncio.to_thread(_telegram_api_probe, "getWebhookInfo", {})

    return {
        "ok": bool(get_me.get("ok") and webhook_info.get("ok")),
        "duration_ms": round((time.perf_counter() - started_at) * 1000),
        "polling_enabled": settings.TELEGRAM_USE_POLLING,
        "proxy_set": bool(settings.HTTPS_PROXY.strip()),
        "api_timeout_seconds": settings.TELEGRAM_API_TIMEOUT_SECONDS,
        "api_retries": settings.TELEGRAM_API_RETRIES,
        "resolved_addresses": _telegram_api_resolved_addresses(),
        "hosts_entries": _telegram_api_host_entries(),
        "checks": {
            "getMe": get_me,
            "getWebhookInfo": webhook_info,
        },
    }
