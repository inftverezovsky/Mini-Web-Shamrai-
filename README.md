# Shamrai Mini App

Telegram Mini App для спортивной аналитики: премиум-лента прогнозов, пакеты матчей, Telegram Stars, YooKassa/Tegro, CRM/админ cockpit и маркетинговые виджеты.

## Структура

- `backend/` - FastAPI, SQLAlchemy, Alembic, Telegram/YooKassa/Tegro webhooks.
- `frontend/` - React + Vite, Telegram WebApp UI, nginx production build.
- `docker-compose.yml` - production-ready VPS контур: Postgres, backend, frontend/nginx.

## Локальный запуск

Backend:

```powershell
cd backend
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\alembic.exe upgrade head
.\.venv\Scripts\python.exe -m src.scripts.seed_defaults --demo
.\.venv\Scripts\python.exe -m uvicorn src.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```powershell
cd frontend
Copy-Item .env.example .env
npm install
npm run dev
```

Для браузерного smoke-теста можно временно включить `VITE_ENABLE_DEBUG_AUTH=true` во `frontend/.env` и `DEBUG_MODE=true`, `ALLOW_DEBUG_AUTH_BYPASS=true` в `backend/.env`. Эти флаги должны быть выключены в production.

## Production Env

Создайте root `.env` из `.env.example`:

```env
POSTGRES_DB=shamrai
POSTGRES_USER=shamrai
POSTGRES_PASSWORD=strong-password
FRONTEND_PORT=8082
VITE_API_URL=
```

Создайте `backend/.env` из `backend/.env.example` и заполните:

```env
APP_ENV=production
DEBUG_MODE=false
ALLOW_DEBUG_AUTH_BYPASS=false
TELEGRAM_BOT_TOKEN=123456789:real-token
TELEGRAM_WEBHOOK_SECRET_TOKEN=strong-random-secret
OWNER_TELEGRAM_ID=123456789
JWT_SECRET_KEY=at-least-32-random-characters
CORS_ALLOWED_ORIGINS=https://your-domain.example
FRONTEND_BASE_URL=https://your-domain.example
API_BASE_URL=https://your-domain.example
YOOKASSA_SHOP_ID=123456
YOOKASSA_SECRET_KEY=live-secret
YOOKASSA_RETURN_URL=https://your-domain.example
TEGRO_SHOP_ID=shop-id
TEGRO_API_KEY=api-key
TEGRO_SECRET_KEY=secret-key
TEGRO_RETURN_URL=https://your-domain.example/app
TELEGRAM_VIP_CHAT_ID=-100...
```

`APP_ENV=production` включает runtime-проверки: без реального Telegram bot token, webhook secret, owner id, сильного JWT secret и хотя бы одного рублевого провайдера оплаты (YooKassa или Tegro) backend не стартует.

## Миграции И Seed

Production schema управляется Alembic:

```powershell
cd backend
.\.venv\Scripts\alembic.exe upgrade head
```

В Docker backend выполняет `alembic upgrade head` перед запуском API.

Базовые справочники:

```powershell
cd backend
.\.venv\Scripts\python.exe -m src.scripts.seed_defaults
```

Демо-данные только для локальной разработки:

```powershell
.\.venv\Scripts\python.exe -m src.scripts.seed_defaults --demo
```

## VPS Deploy

Codex/agent preview deploys must use a single canonical server target:

- path: `/opt/shamrai-mini-app`
- compose project: `shamrai`
- preview port: `8082`
- health: `http://127.0.0.1:8082/api/health`

Do not create new compose projects, app directories, or ports to work around conflicts. First inspect Docker/ports and repair stale Shamrai/sports-betting containers, then deploy the canonical project.

```bash
cp .env.example .env
cp backend/.env.example backend/.env
nano .env
nano backend/.env
docker compose -p shamrai build
docker compose -p shamrai up -d
docker compose -p shamrai ps
curl http://localhost:8082/api/health
```

Логи:

```bash
docker compose -p shamrai logs -f backend
docker compose -p shamrai logs -f frontend
```

Первичная инициализация справочников после старта:

```bash
docker compose -p shamrai exec backend python -m src.scripts.seed_defaults
```

## Webhooks

Telegram webhook:

```bash
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/setWebhook" \
  -H "Content-Type: application/json" \
  -d "{\"url\":\"https://your-domain.example/api/telegram/webhook\",\"secret_token\":\"${TELEGRAM_WEBHOOK_SECRET_TOKEN}\"}"
```

Backend отклоняет Telegram webhook без корректного `X-Telegram-Bot-Api-Secret-Token`.

YooKassa webhook в кабинете YooKassa:

```text
https://your-domain.example/api/payments/yookassa/webhook
event: payment.succeeded
```

Webhook YooKassa повторно запрашивает платеж у YooKassa API и сверяет `status`, `amount`, `currency` и `metadata.attempt_id`. Повторные webhook-и не начисляют доступ повторно.

Tegro webhook в кабинете Tegro.Money:

```text
https://your-domain.example/api/payments/tegro/webhook
```

Webhook Tegro проверяет MD5-подпись уведомления, сверяет `order_id` с внутренним `PaymentAttempt.id`, сумму и валюту. Повторные webhook-и не начисляют доступ повторно.

## Проверки

Frontend:

```powershell
cd frontend
npm run lint
npm run build
```

Backend:

```powershell
cd backend
.\.venv\Scripts\python.exe -m compileall -q src alembic
.\.venv\Scripts\alembic.exe heads
.\.venv\Scripts\python.exe -c "import src.main; print('backend_import_ok')"
```

Docker smoke:

```bash
docker compose -p shamrai up -d --build
docker compose -p shamrai exec backend alembic upgrade head
curl http://localhost:8082/api/health
```

## Production Notes

- `/subscriptions/buy`, debug auth, mock purchases and demo seeds are development-only.
- Public production endpoints do not auto-create demo stats/quiz/swipe/PvP/marathon/crowd data.
- Telegram Stars, YooKassa and Tegro create pending payment attempts; access is activated only by verified webhook processing.
- Active UI uses `Tariffs` for billing and `AdminDashboard` for the admin cockpit.
