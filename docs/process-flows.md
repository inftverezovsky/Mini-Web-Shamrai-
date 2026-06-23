# Process Flows

Карта основных процессов Shamrai. Файл нужен для быстрого онбординга разработчика: где начинается сценарий, какие сервисы участвуют и где процесс завершается.

## Auth

```mermaid
flowchart TD
  A["Пользователь открывает Telegram Mini App или web"] --> B["Frontend AuthContext boot"]
  B --> C{"Есть httpOnly cookie session?"}
  C -- "да" --> D["GET /api/users/me с credentials: include"]
  D --> E["Backend deps читает cookie shamrai_access_token"]
  E --> F["JWT проверен, user найден"]
  F --> G["Frontend получает профиль без записи JWT в localStorage"]
  C -- "нет" --> H{"Есть Telegram initData / VK login / debug mock?"}
  H -- "Telegram" --> I["POST /api/auth/login"]
  H -- "VK" --> J["POST /api/auth/vk/login"]
  H -- "debug mock" --> K["mock_debug_access_token только для local mock API"]
  I --> L["Backend создает JWT и ставит httpOnly cookie"]
  J --> L
  L --> G
  G --> M["apiFetch всегда credentials: include"]
  M --> N["Logout: POST /api/auth/logout очищает cookie и debug storage"]
```

## Payments

```mermaid
flowchart TD
  A["Пользователь открывает Оплата / Tariffs"] --> B["GET /api/subscriptions/plans и /api/users/me/referral"]
  B --> C{"API доступен?"}
  C -- "нет" --> D["Frontend показывает retry card, список не становится пустой без объяснения"]
  C -- "да" --> E["Пользователь выбирает Tegro или YooKassa"]
  E --> F["POST /api/payments/{provider}/create"]
  F --> G["Backend создает PaymentAttempt pending"]
  G --> H{"DEBUG_MODE?"}
  H -- "да" --> I["Debug complete endpoint начисляет абонемент локально"]
  H -- "нет" --> J["Backend вызывает provider API"]
  J --> K["Клиент получает только payment/confirmation URL"]
  K --> L["Provider webhook приходит в backend"]
  L --> M["Backend проверяет подпись/status/amount/currency/attempt_id"]
  M --> N["activate_match_subscription начисляет доступ один раз"]
  J -- "provider error" --> O["Клиент получает generic error; детали остаются в server logs без provider body"]
```

## VK And Telegram Delivery

```mermaid
flowchart TD
  A["Forecast / broadcast / support event"] --> B["Delivery service выбирает канал"]
  B --> C{"Telegram доступен?"}
  C -- "да" --> D["Telegram delivery через bot API"]
  C -- "нет" --> E{"VK ID linked и messages_allowed=true?"}
  E -- "нет" --> F["Событие остается без VK delivery / требует consent"]
  E -- "да" --> G["send_vk_message_to_user"]
  G --> H["VK API request без глобального HTTPS_PROXY"]
  H --> I{"VK вернул permission error 901/902?"}
  I -- "да" --> J["Mark vk_messages_allowed=false, не ретраить бесконечно"]
  I -- "нет" --> K["Delivery success / logged safely"]
  L["VK Callback"] --> M{"type=confirmation?"}
  M -- "да" --> N["Return VK_CALLBACK_CONFIRMATION_CODE plain text"]
  M -- "нет" --> O["Strict group_id + VK_CALLBACK_SECRET validation"]
  O --> P["message_allow/message_deny/message_new обновляют permission state"]
```

## Forecast Lifecycle

```mermaid
flowchart TD
  A["Admin создает прогноз или заявку"] --> B["Backend сохраняет Bet / ForecastRequest"]
  B --> C["Проверка доступа: абонемент на матчи, гарантия, аудитория"]
  C --> D["Delivery outbox / signals service формирует сообщения"]
  D --> E["Telegram, VK, Web Signal stream"]
  E --> F["Пользователь видит сигнал в ленте или чате"]
  F --> G{"Forecast teaser action?"}
  G -- "Взять" --> H["POST /api/signals/forecast-requests/{id}/take"]
  G -- "Отказ" --> I["POST /api/signals/forecast-requests/{id}/decline"]
  H --> J["Status interested/processing/sent"]
  I --> K["Status declined"]
  J --> L["Admin/Support завершает ручной сценарий при необходимости"]
```

## Chat

```mermaid
flowchart TD
  A["WebMessenger mount"] --> B["GET /api/chat/conversations"]
  B --> C["GET signals/support messages"]
  C --> D{"Transient API error?"}
  D -- "да" --> E["Старые сообщения остаются, показывается retry/offline state"]
  D -- "нет" --> F["MessageList renders history"]
  F --> G["POST /api/chat/stream-ticket по cookie session"]
  G --> H["WebSocket /api/chat/stream?ticket=..."]
  H --> I["chat.message.created / chat.conversation.updated"]
  I --> J["UI merge без дублей по id/client_message_id"]
  J --> K["mark read endpoint обновляет unread_count"]
  F --> L["User sends support message"]
  L --> M["Optimistic message sending"]
  M --> N{"POST success?"}
  N -- "да" --> O["Replace optimistic with server message"]
  N -- "нет" --> P["Mark failed, retry button keeps text"]
```

## Deploy And Verification

```mermaid
flowchart TD
  A["Local change"] --> B["No commit/push/deploy by default"]
  B --> C["Run backend compile/tests"]
  C --> D["Run frontend lint/build"]
  D --> E["Run scripts/verify-local.ps1"]
  E --> F{"Deployment requested explicitly?"}
  F -- "нет" --> G["Stop with local report"]
  F -- "preview Shamrai VDS" --> H["Inventory Docker containers and ports first"]
  H --> I["Use canonical /opt/shamrai-mini-app, compose shamrai, port 8082"]
  I --> J["Health check http://127.0.0.1:8082/api/health"]
  F -- "public shamra1.pro" --> K["Build frontend/dist and publish to /var/www/shamrai_web/dist"]
  K --> L["Verify public HTML references new assets"]
```
