# Shamrai Mini App Developer Guide

Дата ревизии: 2026-06-23.

Этот документ вводит разработчика в проект с нуля: что делает приложение, где лежит код,
как устроены backend, frontend, база, платежи, доставки, админка и деплой. Отдельный
пошаговый справочник алгоритмов лежит в [functional-algorithms.md](functional-algorithms.md).

## 1. Что Это За Проект

Shamrai Mini App - спортивно-аналитическое приложение для Telegram Mini App и web/PWA.
Пользователь получает персональную ленту прогнозов, покупает пакеты матчей, открывает
закрытые прогнозы, получает сигналы в web-чате, Telegram, VK и Web Push. Администратор
управляет прогнозами, клиентами, рассылками, статистикой, тарифами, шаблонами сообщений
и поддержкой.

Главные пользовательские поверхности:

- Telegram Mini App: авторизация через Telegram `initData`, работа внутри клиента Telegram.
- Browser/PWA: вход через VK ID, Telegram Login Widget или Telegram bot-session.
- Админский кабинет: доступен ролям `owner`, `admin`, `moderator`.
- Внешние webhook-и: Telegram, VK Callback API, YooKassa, Tegro.
- WebSocket-каналы: персональные сигналы и нативный web-чат.

Главные бизнес-домены:

- Профиль клиента: Telegram/VK идентичность, выбранные БК, анкета, баланс матчей.
- Прогнозы: публичная лента, закрытые teaser-прогнозы, платные наборы, купоны, исходы.
- Доступ: пакет матчей, гарантия, бесплатные прогнозы, ручная выдача админом.
- Оплаты: Telegram Stars, YooKassa, Tegro, промокоды и реферальная скидка.
- Доставка: Telegram Bot API, VK messages, Web Push, WebSocket, delivery outbox.
- Статистика: ROI, winrate, таймлайны, выгрузки CSV/XLSX/Google Drive.
- Поддержка: нативный чат `chat_*` и более старый `PersonalSignal`-чат.

## 2. Структура Репозитория

```text
Mini-Web(Shamrai)/
  backend/
    src/
      api/          FastAPI routers: auth, users, bets, payments, admin, chat, webhooks.
      core/         Settings, security, roles, rate limits, templates, formatting.
      models/       SQLAlchemy Base, async sessions, ORM models.
      schemas/      Pydantic response/request contracts.
      services/     Business services: delivery, forecast, stats, chat, VK, Telegram.
      scripts/      Seed, reset, env patch, diagnostics helpers.
    alembic/        Production migrations.
    tests/          unittest backend test suite.
  frontend/
    src/
      App.tsx       Main app shell and tab routing.
      api/          Fetch wrapper, download helpers, error formatting, mock API.
      components/   Shared UI, listeners, auth screens, onboarding, notifications.
      context/      AuthContext and layout context.
      features/     Larger feature modules: chat, performance UI.
      pages/        user/ and admin/ screens.
      utils/        Telegram, VK, Web Push, auth storage, analytics.
    public/         Brand, sport and bookmaker assets, manifest, service worker.
  deploy/nginx/     Public host nginx config for shamra1.pro.
  scripts/          Local verification and deploy/repair scripts.
  docs/             Project documentation.
  docker-compose.yml
  .env.example
```

## 3. Technology Stack

Backend:

- FastAPI `0.138.0`
- SQLAlchemy async `2.0.28`
- Alembic migrations
- PostgreSQL in Docker production, asyncpg driver
- SQLite-compatible dev helpers in some migration paths
- Pydantic v2
- PyJWT auth
- ReportLab, OpenPyXL, Google API client for exports
- Telegram/VK/payment integrations through Python stdlib HTTP helpers and local services

Frontend:

- React 18 + Vite
- TypeScript
- Tailwind CSS
- React Query
- Framer Motion
- lucide-react icons
- VK ID SDK and VK Bridge
- PWA service worker and Web Push support

Runtime:

- `docker-compose.yml` runs `postgres`, `backend`, `frontend`.
- Backend listens on container port `8000`.
- Frontend nginx listens on container port `8080`.
- Canonical preview exposes `127.0.0.1:8082`.
- Public domain `https://shamra1.pro/` is served by host nginx from `/var/www/shamrai_web/dist`.

## 4. Backend Architecture

### Entry Point

Backend starts from `backend/src/main.py`.

Startup responsibilities:

- validate runtime security with `settings.validate_runtime_security()`;
- log safe VK runtime state;
- run narrow dev schema sync only when `DEBUG_MODE=true`;
- configure optional global HTTPS proxy for Telegram;
- register Telegram webhook/menu button when a real bot token is configured;
- start optional background tasks:
  - abandoned cart recovery;
  - VIP chat expiration checks;
  - delivery outbox daemon;
  - tunnel webhook monitor in debug;
  - VK dialog polling fallback.

The FastAPI app mounts `/static` from `backend/static`, adds CORS, adds
`SecurityRateLimitMiddleware`, and includes all routers under `/api`.

### Settings And Secrets

Backend settings live in `backend/src/core/config.py` and are read from runtime env.
Never put real secrets in committed files.

Local secret files:

- `C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\.env`
- `C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\backend\.env`
- `C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\frontend\.env`

Create each file if it does not exist. These files must stay ignored by git; root
`.gitignore` already ignores `.env` and `.env.*` while allowing `.env.example`.
Committed templates must use safe placeholders only.

Important backend variables:

```env
APP_ENV=production
DEBUG_MODE=false
ALLOW_DEBUG_AUTH_BYPASS=false
DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<db>
JWT_SECRET_KEY=<at-least-32-random-characters>
OWNER_TELEGRAM_ID=<telegram-owner-id>
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_WEBHOOK_SECRET_TOKEN=<random-webhook-secret>
YOOKASSA_SHOP_ID=<shop-id>
YOOKASSA_SECRET_KEY=<secret-key>
TEGRO_SHOP_ID=<shop-id>
TEGRO_API_KEY=<api-key>
TEGRO_SECRET_KEY=<secret-key>
VK_GROUP_ID=<vk-group-id>
VK_GROUP_ACCESS_TOKEN=<vk-group-token>
VK_CALLBACK_CONFIRMATION_CODE=<vk-confirmation-code>
VK_CALLBACK_SECRET=<vk-callback-secret>
WEB_PUSH_VAPID_PUBLIC_KEY=<public-key>
WEB_PUSH_VAPID_PRIVATE_KEY=<private-key>
GOOGLE_SERVICE_ACCOUNT_JSON_B64=<base64-service-account-json>
```

Production hard stops:

- `DEBUG_MODE` cannot be true.
- Real Telegram bot token is required.
- `OWNER_TELEGRAM_ID` is required.
- strong `JWT_SECRET_KEY` is required.
- `TELEGRAM_WEBHOOK_SECRET_TOKEN` is required.
- at least one ruble payment provider is required.
- rate limiting must be in `enforce` mode.
- VK callback/delivery secrets are required when VK is enabled.

### Database Model

ORM models are in `backend/src/models/models.py`.

Core tables:

- `users`: identity, role, onboarding profile, VK state, web push subscription,
  match balances, notification preferences.
- `bookmakers`: canonical bookmaker list.
- `bets`: forecast entity. It can be public feed, private forecast, or paid set.
- `user_bets`: many-to-many access/tracking table with `access_type` and `match_charged`.
- `subscription_plans`, `subscriptions`: match packages and activations.
- `payment_attempts`: provider-agnostic payment state.
- `match_balance_logs`: ledger for package credits/debits/revokes/supercompensation.
- `forecast_requests`: per-user teaser/private forecast request lifecycle.
- `personal_signals`: old signal stream and support-message storage.
- `delivery_outbox`: retryable external delivery queue.
- `chat_conversations`, `chat_messages`, `chat_read_cursors`: native support chat.
- `admin_audit_logs`: sensitive admin actions.
- `message_templates`: editable admin templates.
- `promo_codes`, `crowd_bets`, `quizzes`, `pvp_battles`, `marathons`, `user_badges`.

Production schema changes go through Alembic in `backend/alembic/versions`.
`run_dev_schema_migrations()` in `main.py` is only a local/debug compatibility helper.

### API Routers

All routers are mounted under `/api`.

| Router | File | Main responsibility |
| --- | --- | --- |
| `/auth/*` | `backend/src/api/auth.py` | Telegram/VK login, bot-session login, auth cookie, profile merge |
| `/users/*`, `/bookmakers` | `backend/src/api/users.py` | profile, onboarding, preferences, VK delivery status, history |
| `/bets/*` | `backend/src/api/bets.py` | feed, bet taking, hints, admin create/update/resolve, notes |
| `/subscriptions/*` | `backend/src/api/subscriptions.py` | package plans, debug buy, manual assignment, invite links |
| `/payments/*` | `backend/src/api/payments.py` | Telegram Stars, YooKassa, Tegro, promo validation, webhooks |
| `/signals/*` | `backend/src/api/signals.py` | personal signal history, web push, WebSocket stream |
| `/chat/*` | `backend/src/api/chat.py` | native client/staff support chat and WebSocket stream |
| `/admin/*` | `backend/src/api/admin.py` | dashboard, CRM, stats, users, templates, exports, audit |
| `/admin/*` | `backend/src/api/admin_broadcast.py` | announcements, forecast broadcasts, paid sets, forecast requests |
| `/admin/web-chat/*` | `backend/src/api/admin_web_chat.py` | older admin support-chat stream |
| `/marketing/*` | `backend/src/api/marketing.py` | pulse, daily spin, swipe, quiz, PvP, marathon |
| `/crowd-bets/*` | `backend/src/api/crowd_bets.py` | active crowd bet and funding |
| `/telegram/webhook` | `backend/src/api/telegram_webhook.py` | bot commands, callback buttons, Stars payment updates |
| `/vk/callback` | `backend/src/api/vk_callback.py` | VK confirmation, message permissions, forecast buttons |
| `/stats/*` | `backend/src/api/stats.py` | global stats and bookmaker logo export |
| `/go/*` | `backend/src/api/go.py` | safe bookmaker redirect URLs |

### Auth And Roles

Auth uses JWT in two places:

- httpOnly cookie `shamrai_access_token` scoped to `/api`;
- optional `Authorization: Bearer ...` header for compatibility/debug.

Dependency rules in `backend/src/api/deps.py`:

- `get_current_user`: any authenticated user.
- `get_current_user_read`: read-only session variant.
- `get_current_admin`: staff roles: `owner`, `admin`, `moderator`.
- `get_current_privileged_admin`: `owner` and `admin`.
- `get_current_owner`: owner only.

Role helpers live in `backend/src/core/roles.py`.

### Payments

All real payments create a `PaymentAttempt` first. Successful access is granted only after
the provider is verified:

- Telegram Stars: validates pre-checkout and successful payment payload.
- YooKassa: webhook re-fetches payment from YooKassa and verifies status, amount,
  currency and `metadata.attempt_id`.
- Tegro: webhook verifies signature, order id, amount, currency and shop id.

`_process_payment_attempt()` is the central activation path. It:

- locks the attempt;
- checks provider, provider payment id, amount and currency;
- avoids double processing;
- activates a match package with `activate_match_package()`, or unlocks a single bet,
  or marks a hint/crowd contribution as paid;
- marks the attempt `succeeded`;
- queues a Telegram confirmation when possible.

Debug completion endpoints exist only when `DEBUG_MODE=true`.

### Match Access

Access logic lives in `backend/src/services/match_access.py`.

Key ideas:

- package purchase adds `plan.match_count` to `purchased_bets_balance` and `matches_remaining`;
- taking a paid match debits exactly one match through `record_user_bet_access()`;
- staff access does not consume balance;
- guarantee replacement can grant a no-debit access;
- removing access reverses ledger impact through `revoke_user_bet_access()`;
- losses on charged paid matches add supercompensation in `resolve_bet()`.

### Forecast Lifecycle

Private forecast logic is split between:

- admin broadcast endpoints in `backend/src/api/admin_broadcast.py`;
- delivery state machine in `backend/src/services/forecast_delivery.py`.

Important statuses:

- `announced`: teaser was sent.
- `interested`: client pressed "take".
- `declined`: client refused.
- `processing`: server/admin is delivering.
- `sent`: full forecast delivered automatically.
- `manual_sent`: admin marked manual sale/send.
- `cancelled`: admin cancelled request.
- `removed`: admin removed client or stopped broadcast.

Important delivery modes:

- `feed`: public feed bet.
- `sales_private`: teaser first, full forecast later.
- `paid_set`: paid set sold manually.

### Delivery Outbox

`backend/src/services/delivery_outbox.py` provides retryable delivery for Telegram,
VK, Web Push and forecast delivery jobs.

Outbox guarantees:

- optional dedupe key prevents duplicate queued jobs;
- due items are claimed with `skip_locked` on PostgreSQL;
- stale `processing` locks are retried;
- failed attempts use exponential backoff;
- channel-specific concurrency is configured through
  `DELIVERY_OUTBOX_CHANNEL_CONCURRENCY`.

Channels:

- `telegram_message`
- `vk_message`
- `web_push_signal`
- `forecast_auto_delivery`
- `forecast_full_delivery`

### VK Delivery

Read `docs/vk-delivery.md` before changing VK behavior.

Do not merge the three VK concepts:

- `users.vk_user_id`: profile is linked to a VK account.
- `users.vk_messages_allowed`: community can send personal messages.
- `users.vk_notifications_allowed`: user intent for notifications.

VK callback rules:

- `confirmation` returns `VK_CALLBACK_CONFIRMATION_CODE` as plain text.
- `confirmation` must not require callback secret.
- non-confirmation events require strict `group_id` and `VK_CALLBACK_SECRET`.
- `message_allow`, `message_deny`, `message_new` update delivery permission.
- VK API calls intentionally bypass global `HTTPS_PROXY`.

### Telegram Delivery

Telegram routes live in `backend/src/api/telegram_webhook.py`.

Responsibilities:

- `/start` response and web app button;
- Telegram bot auth session confirmation;
- forecast action callbacks;
- sales manager callbacks;
- payment pre-checkout and successful payment handling via `payments.py`;
- safe webhook secret validation through `X-Telegram-Bot-Api-Secret-Token`.

### Statistics And Exports

Runtime stats use:

- `backend/src/services/statistics.py`: normalized items, summaries, timelines,
  source splits, ROI/winrate and client situation.
- `backend/src/services/stats_export.py`: XLSX workbooks, bookmaker logos,
  client exports and performance export artifacts.
- `backend/src/services/google_drive_export.py`: optional Google Drive export jobs.

The frontend performance UI helpers live in `frontend/src/features/performance/performanceUi.tsx`.

## 5. Frontend Architecture

### App Shell

The frontend does not use route files as the primary navigation model. `frontend/src/App.tsx`
is the main shell and chooses pages by tab state:

User tabs:

- `feed`: `pages/user/BetFeed.tsx`
- `chat`: `components/WebBotChat.tsx`
- `stats` / `my_bets`: `pages/user/MyBets.tsx`
- `billing`: `pages/user/Tariffs.tsx`
- `profile`: `pages/user/Profile.tsx`

Admin tabs:

- `manage_bets`: `pages/admin/AdminDashboard.tsx`
- `stats`: `pages/admin/AdminStats.tsx`
- `clients`: `pages/admin/AdminCRM.tsx`
- `chats`: `pages/admin/AdminWebChat.tsx`
- `settings`: `pages/admin/AdminSettings.tsx`
- `profile`: `pages/user/Profile.tsx`

`App.tsx` also mounts global listeners:

- `NotificationCenter`
- `WebSignalListener`
- `AdminWebChatListener`
- `VkConsentWizard`
- `PwaPushGate`

### AuthContext

`frontend/src/context/AuthContext.tsx` owns auth boot:

1. consume VK redirect result if present;
2. use mock debug token when enabled in dev;
3. try stored token;
4. try cookie session via `/api/users/me`;
5. try Telegram Mini App `initData`;
6. try debug auth bypass in dev;
7. show `BrowserAuthScreen` when no authenticated user exists.

API calls use `credentials: include`, so the httpOnly cookie participates automatically.
Stored bearer tokens remain for compatibility/debug and are cleared on 401.

### API Helpers

- `frontend/src/api/client.ts`: `requestApi()`, JSON parsing, credentials, 401 event.
- `frontend/src/utils/api.ts`: `apiFetch()` and debug mock dispatch.
- `frontend/src/config/api.ts`: base URL and WebSocket URL builders.
- `frontend/src/api/downloads.ts`: file download helper.
- `frontend/src/api/errors.ts`: user-friendly API error formatting.

### Realtime

- Personal signals: `/api/signals/stream-ticket` then WebSocket `/api/signals/stream`.
- Native chat: `/api/chat/stream-ticket` then WebSocket `/api/chat/stream`.
- Admin chat listener uses the same native stream for staff notifications.
- Older support-web-chat surfaces still exist through `admin_web_chat.py` and
  `support_web_chat.py`.

WebSocket connections use short-lived stream tickets instead of putting JWTs directly
into URLs.

### PWA And Push

PWA helpers live in `frontend/src/utils/webPush.ts`.

The app registers the service worker outside Telegram runtime and saves push subscription
through `/api/signals/web-push/subscription`. Push notifications are delivered by the
backend through `CHANNEL_WEB_PUSH_SIGNAL` outbox jobs.

### VK ID And Consent

- `frontend/src/utils/vkId.ts`: PKCE redirect/auth flow, cooldown handling, login/link.
- `frontend/src/utils/vkDelivery.ts`: VK Bridge group/message/notification consent.
- `frontend/src/components/VkConsentWizard.tsx`: guided permission flow.

## 6. Local Development

Backend setup:

```powershell
cd C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m src.scripts.seed_defaults --demo
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend setup:

```powershell
cd C:\Users\Sa1z1ngr0z\Desktop\Mini-Web(Shamrai)\frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Useful debug flags:

```env
# backend/.env
DEBUG_MODE=true
ALLOW_DEBUG_AUTH_BYPASS=true

# frontend/.env
VITE_ENABLE_DEBUG_AUTH=true
```

Do not enable these flags in production.

Seed data:

- `python -m src.scripts.seed_defaults`: standard reference data.
- `python -m src.scripts.seed_defaults --demo`: local demo data.
- marketing demo seed happens only in `DEBUG_MODE` and only for some endpoints.

## 7. Verification

Full local check:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-local.ps1
```

Backend-only checks from `scripts/verify-local.ps1`:

- Python version check;
- dependency import check;
- `compileall` for `src` and `alembic`;
- `alembic heads`;
- `import src.main`;
- `unittest discover -s tests -p 'test_*.py'`.

Frontend checks:

- `npm run lint`;
- `npm run build`.

For focused work, run the nearest test file in `backend/tests` and the relevant frontend
build/lint step. Before deploy or payment/delivery changes, run the full script.

## 8. Deployment Model

Canonical Shamrai preview deployment:

- server: `root@82.147.67.245`
- app path: `/opt/shamrai-mini-app`
- Docker Compose project: `shamrai`
- preview frontend port: `8082`
- health URL: `http://127.0.0.1:8082/api/health`

Do not create alternate app directories, compose projects or ports to avoid conflicts.
Before touching the server, inventory Docker containers and listening ports.

Public `https://shamra1.pro/` is not the same as the Docker preview frontend. Public
frontend is served by host nginx from:

```text
/var/www/shamrai_web/dist
```

When deploying a public frontend change, build `frontend/dist`, publish it to that root,
and verify that public HTML references the new `assets/*.js` and `assets/*.css` files.

Protected production boundaries:

- do not stop system nginx unless explicitly replacing public production deployment;
- do not stop unrelated containers or ports `80/443`;
- do not write SSH passwords, Telegram tokens, VK secrets, payment keys, JWT secrets or
  database passwords into files, shell history, docs or chat.

## 9. How To Add Or Change Features

Backend endpoint:

1. Add or update Pydantic schema in `backend/src/schemas/schemas.py` or local router models.
2. Put HTTP boundary logic in the relevant `backend/src/api/*.py` router.
3. Put reusable business logic in `backend/src/services/*.py`.
4. Add Alembic migration for schema changes.
5. Add tests in `backend/tests`.
6. Run focused tests, then `scripts/verify-local.ps1` when risk is meaningful.

Frontend screen:

1. Put page-level UI in `frontend/src/pages/user` or `frontend/src/pages/admin`.
2. Put reusable UI in `frontend/src/components` or feature modules.
3. Use `apiFetch()` for API calls.
4. Add the page to `App.tsx` tab loading only if it is a first-class tab.
5. Check compact/mobile and desktop layouts.
6. Run `npm run lint` and `npm run build`.

New delivery action:

1. Prefer `delivery_outbox` when the external call can fail or be retried.
2. Choose a channel or add one in `backend/src/services/delivery_outbox.py`.
3. Use a stable `dedupe_key` for idempotency.
4. Keep secrets out of logs and payloads.
5. Add tests around duplicate delivery and retry behavior.

New payment provider:

1. Create `PaymentAttempt` before redirecting the user.
2. Persist only non-secret metadata needed for verification.
3. Verify the provider server-side on webhook.
4. Call `_process_payment_attempt()` only after verification.
5. Make duplicate webhook processing idempotent.

New DB table/field:

1. Update ORM model.
2. Generate/write Alembic migration.
3. Add serialization fields if API response needs them.
4. Add tests for migration-sensitive behavior.
5. Do not rely on `run_dev_schema_migrations()` for production.

## 10. Common Pitfalls

- Healthy `shamrai-frontend` on port `8082` does not prove that `https://shamra1.pro/`
  changed. Public frontend must be published to `/var/www/shamrai_web/dist`.
- VK linked profile is not the same as VK message permission.
- VK confirmation callback must return plain text and must not require secret.
- Global `HTTPS_PROXY` may be needed for Telegram but must not break VK API calls.
- `DEBUG_MODE` unlocks routes and seed behavior that must not exist in production.
- `PaymentAttempt.status=succeeded` is the only safe source of truth for paid access.
- `user_bets` means both "tracked in My Bets" and "access/unlocked"; inspect
  `access_type` and `match_charged`.
- `matches_remaining` and `purchased_bets_balance` are kept in sync by access services.
- Private forecast requests are per-user; changing a `Bet` does not automatically change
  every request status.
- Some support/chat code exists in two generations. Prefer native `chat.py` for new work.

## 11. Fast Mental Model

Read the project in this order:

1. `README.md` for quick commands.
2. `backend/src/models/models.py` for data vocabulary.
3. `backend/src/api/deps.py` and `backend/src/api/auth.py` for auth.
4. `backend/src/services/match_access.py` for balances.
5. `backend/src/api/payments.py` for paid activation.
6. `backend/src/services/forecast_delivery.py` and `backend/src/api/admin_broadcast.py`
   for private forecasts.
7. `backend/src/services/delivery_outbox.py` for external delivery reliability.
8. `frontend/src/App.tsx` and `frontend/src/context/AuthContext.tsx` for UI flow.
9. `frontend/src/pages/user` and `frontend/src/pages/admin` for screens.
10. `docs/functional-algorithms.md` for end-to-end scenarios.
