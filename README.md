# Shamrai Mini App

Full-stack приложение для спортивной аналитики, прогнозов, подписок и клиентского сопровождения. Проект работает как Telegram Mini App, браузерная PWA-версия и админский cockpit для команды.

[GitHub repository](https://github.com/inftverezovsky/Mini-Web-Shamrai-)

## Что умеет проект

- Пользовательский кабинет: onboarding, профиль, тарифы, лента прогнозов, мои ставки, чат поддержки, уведомления и статистика.
- Админка: CRM, прогнозы, подписки, рассылки, web-chat, шаблоны сообщений, экспорт статистики и аудит действий.
- Авторизация: Telegram `initData`, VK ID OAuth, bot-session login, httpOnly cookie, роли `user`, `moderator`, `admin`, `owner`.
- Оплаты и доступы: Telegram Stars, YooKassa, Tegro, промокоды, абонементы на матчи и идемпотентная обработка webhook/update.
- Доставка сигналов: Telegram Bot API, VK Messages API, Web Push, WebSocket и retryable delivery outbox.
- Инфраструктура: FastAPI backend, React/Vite frontend, PostgreSQL, Redis hot cache, Alembic migrations, Docker Compose preview и host-nginx public deploy.

## Текстовое описание проекта

Shamrai Mini App - это рабочая система для спортивно-аналитического продукта: клиент
входит через Telegram или VK, проходит короткую настройку профиля, покупает доступ к
прогнозам, берет ставки в ленте, получает закрытые сигналы и видит собственную
статистику. Команда Shamrai через админский cockpit ведет клиентов, создает прогнозы,
отвечает в чатах, запускает рассылки, управляет тарифами и выгружает отчеты.

Проект состоит из трех пользовательских поверхностей и набора backend-интеграций:

- **Telegram Mini App** - основной клиентский сценарий внутри Telegram.
- **Browser/PWA** - web-вход через VK ID, Telegram bot-session и PWA push.
- **Admin cockpit** - CRM, прогнозы, статистика, чаты, настройки и аудит.
- **Integration layer** - Telegram/VK callbacks, YooKassa/Tegro webhooks, Google Drive export.

Ключевой принцип: деньги, доступы, доставка и статистика не должны зависеть от
повторных кликов или повторных webhook-ов. Поэтому платежи проходят через
`PaymentAttempt`, выдача доступа - через ledger-сервисы, внешние сообщения - через
`delivery_outbox`, а историческая статистика Shamrai хранится отдельно от реальных
клиентских `user_bets`.

Подробный обзор проекта, доменная карта и алгоритмы лежат в
[docs/project-overview.md](docs/project-overview.md).

## Архитектура

```mermaid
flowchart LR
  TG["Telegram Mini App"] --> FE["React + Vite frontend"]
  WEB["Browser / PWA"] --> FE
  ADMIN["Admin cockpit"] --> FE

  FE -->|"HTTPS /api, credentials: include"| API["FastAPI backend"]
  FE -->|"WebSocket tickets"| WS["Signals / Chat WebSockets"]

  API --> DB[("PostgreSQL")]
  API --> REDIS[("Redis")]
  API --> STATIC["/static coupon assets"]
  API --> OUTBOX["Delivery outbox"]

  OUTBOX --> TGBOT["Telegram Bot API"]
  OUTBOX --> VK["VK Messages API"]
  OUTBOX --> PUSH["Web Push"]

  API --> YK["YooKassa"]
  API --> TEGRO["Tegro"]
  API --> GDRIVE["Google Drive export"]

  subgraph Preview["Docker preview"]
    DB
    REDIS
    API
    NGINX["frontend nginx"]
  end

  NGINX -->|"local preview port"| HEALTH["/api/health"]
  PUBLIC["https://shamra1.pro"] -->|"host nginx"| WEBROOT["published frontend assets"]
```

Backend принимает API под `/api`, проверяет cookie/JWT, работает с PostgreSQL через SQLAlchemy async и отдаёт статические coupon-файлы. Внешняя доставка вынесена в outbox, чтобы Telegram, VK и Web Push можно было ретраить без двойной выдачи доступа.

Frontend собирается Vite. В production API вызывается same-origin через `/api`. Важно различать Docker preview и публичный сайт: здоровый контейнер frontend preview не означает, что `https://shamra1.pro/` обновился, потому что публичный домен обслуживается host nginx из отдельного static web root, записанного в private runbook/project registry.

## Ключевые алгоритмы

### Авторизация и embedded cookie

```mermaid
sequenceDiagram
  participant UI as Frontend
  participant API as FastAPI /auth
  participant DB as PostgreSQL

  UI->>API: GET /api/users/me with credentials
  alt cookie valid
    API->>DB: load user from JWT subject
    API-->>UI: UserResponse
  else no session
    UI->>API: POST /api/auth/login or /auth/vk/login
    API->>API: verify Telegram/VK payload
    API->>DB: upsert or merge user
    API-->>UI: httpOnly auth cookie + CSRF cookie
    Note over API,UI: HTTPS/production cookies use SameSite=None; Secure
    UI->>API: GET /api/users/me with credentials
    API-->>UI: authenticated profile
  end
```

### Платеж и выдача доступа

```mermaid
flowchart TD
  A["Клиент выбирает тариф"] --> B["Create PaymentAttempt pending"]
  B --> C["Provider invoice / confirmation URL"]
  C --> D["Provider webhook/update"]
  D --> E["Server-side verification"]
  E --> F{"Already succeeded?"}
  F -- "yes" --> G["Idempotent ok"]
  F -- "no" --> H["Activate subscription / unlock item"]
  H --> I["Write match_balance_logs"]
  I --> J["Mark attempt succeeded"]
  J --> K["Queue confirmation delivery"]
```

### Доставка сигнала

```mermaid
flowchart LR
  Event["Forecast/chat/result event"] --> Queue["delivery_outbox"]
  Queue --> Claim["Daemon claims due rows"]
  Claim --> Dispatch{"Channel"}
  Dispatch --> TG["Telegram"]
  Dispatch --> VK["VK"]
  Dispatch --> Push["Web Push"]
  TG --> Status{"ok / retry / failed"}
  VK --> Status
  Push --> Status
  Status --> Sent["sent"]
  Status --> Retry["retry with backoff"]
  Status --> Failed["failed"]
```

### Общая статистика Shamrai

```mermaid
flowchart TD
  A["/api/stats/global?period=all"] --> B{"Scope"}
  B -- "client" --> C["Only real user_bets"]
  B -- "channel/global" --> D["Historical baseline before 2026-07-01"]
  D --> E["Live DB resolved bets from 2026-07-01 onward"]
  E --> F["Normalize and merge performance items"]
  F --> G["ROI, winrate, profit, monthly chart, breakdowns"]
```

## Стек

| Слой | Технологии |
| --- | --- |
| Backend | Python 3.11, FastAPI, SQLAlchemy async, Pydantic v2, Alembic, Uvicorn |
| Data | PostgreSQL, Redis, asyncpg, optional SQLite for tests/dev |
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, React Query, Framer Motion, lucide-react |
| Integrations | Telegram Bot API, VK ID, VK Callback API, YooKassa, Tegro, Web Push, Google Drive export |
| Ops | Docker Compose, nginx, PowerShell deploy/repair scripts, GitHub Actions |

## Структура репозитория

```text
backend/
  src/
    api/          FastAPI routers: auth, users, bets, payments, admin, chat, webhooks
    core/         settings, security, roles, rate limits, Redis cache, message helpers
    models/       SQLAlchemy database/session and ORM models
    schemas/      Pydantic request/response contracts
    services/     payments, delivery, forecast, stats, chat, VK, Telegram
    scripts/      seed, reset, env patch, diagnostics
  alembic/        database migrations
  tests/          backend unittest suite

frontend/
  src/
    api/          request client, downloads, mock data, API errors
    components/   shared UI, auth screens, listeners, onboarding, notifications
    context/      AuthContext and LayoutModeContext
    features/     chat and performance modules
    pages/        user and admin screens
    utils/        Telegram, VK, Web Push, auth storage, analytics
  public/         brand, sport/bookmaker assets, manifest, service worker
  tests/          Vitest unit tests

deploy/nginx/     public nginx config examples
docs/             developer guide, process flows, VK delivery rules
scripts/          local verification and deploy/repair helpers
docker-compose.yml
.env.example
```

## Локальный запуск

### Docker Compose

```powershell
Copy-Item .env.example .env
Copy-Item backend\.env.example backend\.env
docker compose -p shamrai up -d --build
docker compose -p shamrai ps
curl http://127.0.0.1:<frontend-port>/api/health
```

### Backend

```powershell
cd backend
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m src.scripts.seed_defaults --demo
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Для локальной browser/PWA разработки можно включить debug auth:

```env
DEBUG_MODE=true
ALLOW_DEBUG_AUTH_BYPASS=true
```

В production эти флаги должны быть `false`.

### Frontend

```powershell
cd frontend
npm install
Copy-Item .env.example .env
npm run dev
```

Если backend запущен отдельно на `8000`, используйте:

```env
VITE_API_URL=http://localhost:8000
VITE_ENABLE_DEBUG_AUTH=true
```

Для same-origin production build `VITE_API_URL` оставляют пустым.

## Переменные окружения

Реальные секреты нельзя коммитить, писать в README, shell history или чат. В репозитории хранятся только `.env.example` с безопасными placeholder-ами.

| Файл | Создать, если нет | Git ignored | Назначение |
| --- | --- | --- | --- |
| `.env` | да | да | Docker Compose, Postgres, preview port, frontend build args |
| `backend/.env` | да | да | backend runtime, JWT, Telegram/VK, payments, Google Drive |
| `frontend/.env` | да | да | локальный Vite config и публичные frontend build variables |

Ключевые production-переменные backend:

```env
APP_ENV=production
DEBUG_MODE=false
ALLOW_DEBUG_AUTH_BYPASS=false
DATABASE_URL=postgresql://<user>:<password>@<host>:5432/<database>
JWT_SECRET_KEY=<at-least-32-random-characters>
OWNER_TELEGRAM_ID=<telegram-owner-id>
TELEGRAM_BOT_TOKEN=<telegram-bot-token>
TELEGRAM_WEBHOOK_SECRET_TOKEN=<random-webhook-secret>
CORS_ALLOWED_ORIGINS=https://shamra1.pro,https://www.shamra1.pro
API_BASE_URL=https://shamra1.pro
FRONTEND_BASE_URL=https://shamra1.pro/app
YOOKASSA_SHOP_ID=<shop-id-or-empty>
YOOKASSA_SECRET_KEY=<secret-key-or-empty>
TEGRO_SHOP_ID=<shop-id-or-empty>
TEGRO_API_KEY=<api-key-or-empty>
TEGRO_SECRET_KEY=<secret-key-or-empty>
VK_GROUP_ID=<vk-group-id-or-empty>
VK_GROUP_ACCESS_TOKEN=<vk-group-access-token-or-empty>
VK_CALLBACK_CONFIRMATION_CODE=<vk-confirmation-code-or-empty>
VK_CALLBACK_SECRET=<vk-callback-secret-or-empty>
WEB_PUSH_VAPID_PUBLIC_KEY=<public-vapid-key-or-empty>
WEB_PUSH_VAPID_PRIVATE_KEY=<private-vapid-key-or-empty>
SECURITY_RATE_LIMIT_MODE=enforce
```

Backend intentionally fails fast in production if dangerous runtime combinations are detected: debug auth enabled, weak JWT secret, missing Telegram webhook secret, missing owner id, missing ruble payment provider or disabled rate-limit enforcement.

## Проверки

Полная локальная проверка:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\verify-local.ps1
```

Backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall -q src alembic
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\python.exe -c "import src.main; print('backend_import_ok')"
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\pip-audit.exe -r requirements.txt --strict
```

Frontend:

```powershell
cd frontend
npm run lint
npm run build
npm test
npm run test:e2e
npm audit --audit-level=high
```

Docker and scripts:

```powershell
docker compose config --quiet
```

CI mirrors the same core gates in `.github/workflows/ci.yml`: backend tests/compile/Python dependency audit, frontend npm audit/lint/unit tests/build, Playwright prelaunch smoke, secret scanning, dependency review, Docker image builds, Alembic migration smoke on empty PostgreSQL, Compose config validation and PowerShell script parsing.

## Deployment

Canonical Shamrai preview deployment coordinates are intentionally kept out of tracked docs. Use the private Codex project registry/runbook for the exact server, remote app path, Compose project, preview port, health URL and public web root.

Before any VDS deploy, repair or inspection, inventory Docker containers and listening ports. Keep one canonical preview only: do not solve conflicts by creating another app directory, compose project or frontend port.

Preview Docker deploy shape:

```bash
cp .env.example .env
cp backend/.env.example backend/.env
docker compose -p shamrai build
docker compose -p shamrai up -d
docker compose -p shamrai ps
curl http://127.0.0.1:<frontend-port>/api/health
```

Guarded public frontend deploy helper:

```powershell
$env:SHAMRAI_SSH_PASSWORD = '<provide-at-runtime-do-not-store>'
powershell -ExecutionPolicy Bypass -File .\scripts\deploy-public-shamrai-web.ps1 -RepairShamraiConflicts
Remove-Item Env:\SHAMRAI_SSH_PASSWORD
```

Public deploy is complete only after `frontend/dist` is published to the private public web root and `https://shamra1.pro/` references the newly built `assets/*.js` and `assets/*.css`.

Encrypted database backups and restore drills are documented in [docs/backup-restore.md](docs/backup-restore.md).

Do not stop system nginx, unrelated containers, databases or ports `80/443` unless the task explicitly replaces the public production deployment and ownership is confirmed.

## Security and delivery invariants

- Never commit `.env`, private keys, tokens, passwords, database dumps or generated secret-bearing artifacts.
- Validate all external webhook payloads server-side.
- Activate paid access only after verified provider callback/update.
- Payment, forecast and delivery flows must be idempotent.
- Telegram webhook requires `X-Telegram-Bot-Api-Secret-Token`.
- VK `confirmation` returns plain text `VK_CALLBACK_CONFIRMATION_CODE` and does not require callback secret.
- VK non-confirmation events require strict `group_id` and `VK_CALLBACK_SECRET`.
- VK delivery is allowed only when `users.vk_user_id` exists and `users.vk_messages_allowed=true`.
- VK API calls bypass the global `HTTPS_PROXY`; Telegram may use a proxy separately.
- WebSocket streams use short-lived tickets instead of long-lived JWT in query strings.
- Production must keep `DEBUG_MODE=false`, `ALLOW_DEBUG_AUTH_BYPASS=false`, `SECURITY_RATE_LIMIT_MODE=enforce`.

## Local audit snapshot

Snapshot from local checkout on `2026-07-02`.

Project verification status: green. No local syntax, test, build or dependency-audit blockers were found in the checked surface.

Evidence checked:

- `git status --short --branch`: only the intended auth-cookie/docs changes were present.
- Backend `compileall`: `backend/src` and `backend/alembic` passed.
- Backend import: `import src.main` returned `backend_import_ok`.
- Alembic: one head, `20260702_0036`.
- Backend tests: `423 passed`.
- Frontend lint: passed.
- Frontend unit tests: `97 passed`.
- Frontend production build: passed.
- Playwright smoke/compat E2E: `9 passed`.
- `npm audit --audit-level=high`: 0 frontend vulnerabilities found.
- `pip-audit -r requirements.txt --strict`: no known backend vulnerabilities found.
- `git diff --check`: passed.
- Secret surface checked by tracked files and patterns; real `.env` values were not printed.

High-value follow-ups:

- Keep Playwright coverage focused on launch-critical paths: auth/session, feed, tariff entry, support chat and admin flows.
- Add provider-specific payment webhook regression tests whenever payment logic changes.
- Keep historical stats import/export tests coupled to the Excel baseline totals.
- Run a read-only VDS health audit before any public redeploy.

## Useful docs

- [docs/project-overview.md](docs/project-overview.md) - текстовое описание проекта, доменная карта, схемы и ключевые алгоритмы.
- [docs/developer-guide.md](docs/developer-guide.md) - backend, frontend, database, payments, delivery and deployment map.
- [docs/backup-restore.md](docs/backup-restore.md) - encrypted Postgres backups, retention, restore drill and production restore runbook.
- [docs/process-flows.md](docs/process-flows.md) - Mermaid process diagrams for auth, payments, delivery, forecast, chat and deploy.
- [docs/vk-delivery.md](docs/vk-delivery.md) - required VK ID, Callback API and delivery rules.
- [docs/functional-algorithms.md](docs/functional-algorithms.md) - step-by-step functional algorithms.
- [docs/frontend-infra-optimization.md](docs/frontend-infra-optimization.md) - frontend performance and infra notes.
- [docs/backend-latency-optimization.md](docs/backend-latency-optimization.md) - backend latency and cache notes.

Recommended onboarding order for a new developer:

1. `README.md`
2. `docs/developer-guide.md`
3. `backend/src/models/models.py`
4. `backend/src/api/deps.py` and `backend/src/api/auth.py`
5. `backend/src/api/payments.py`
6. `backend/src/services/forecast_delivery.py`
7. `backend/src/services/delivery_outbox.py`
8. `frontend/src/App.tsx`
9. `frontend/src/context/AuthContext.tsx`
