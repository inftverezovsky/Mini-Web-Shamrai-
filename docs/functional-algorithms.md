# Shamrai Functional Algorithms

Дата ревизии: 2026-06-23.

Формат: каждый алгоритм записан отдельным блоком от старта до финального состояния.
Файл дополняет визуальные схемы из `docs/process-flows.md` и подробный гид из
`docs/developer-guide.md`.

## 1. Startup Backend

```text
Старт: процесс запускает `uvicorn src.main:app`.
1. Загружается `settings` из runtime env.
2. `lifespan()` вызывает `settings.validate_runtime_security()`.
3. Если production-настройки небезопасны, процесс падает до старта API.
4. Логируется безопасная VK-конфигурация без секретов.
5. Если `DEBUG_MODE=true`, создаются/подтягиваются dev-таблицы через narrow dev sync.
6. Если `HTTPS_PROXY` задан, он добавляется в окружение для Telegram.
7. Если Telegram bot token реален, фоном планируется настройка webhook/menu button.
8. Если включен Telegram polling, запускается polling daemon.
9. Если включен VK dialog polling, запускается VK polling daemon.
10. Если `ENABLE_BACKGROUND_TASKS=true`, запускаются фоновые daemons: recovery, VIP,
    delivery outbox, debug tunnel monitor.
11. FastAPI начинает принимать запросы.
Финиш: `/api/health` возвращает `{"status":"ok"}` или запуск прерван до приема трафика.
```

## 2. Frontend Boot

```text
Старт: пользователь открывает `/app`, Telegram Mini App или browser/PWA.
1. `main.tsx` монтирует React-приложение.
2. `AuthProvider` запускает auth boot.
3. `App.tsx` показывает `WelcomeSplash`, пока Telegram SDK/auth не готовы.
4. После auth boot приложение проверяет наличие `userProfile`.
5. Если профиля нет, показывается `BrowserAuthScreen`.
6. Если есть ошибка подключения, показывается экран retry.
7. Если пользователь staff и не включен preview клиента, открывается admin shell.
8. Если обычный пользователь не прошел onboarding, открывается onboarding flow.
9. Если onboarding пройден, выбирается активная user/admin tab.
10. Глобально монтируются notification center, signal listener, admin chat listener,
    VK consent wizard и PWA gate.
Финиш: пользователь видит актуальный раздел, а фоновые realtime/listener-компоненты работают.
```

## 3. Auth Bootstrap On Frontend

```text
Старт: `AuthContext.bootAuth()` вызван при первом рендере.
1. Проверить, вернулся ли пользователь с VK redirect.
2. Если VK redirect есть, отправить payload в `/api/auth/vk/login` или `/api/auth/vk/link`.
3. Если VK redirect обработан успешно, сохранить user/token и завершить boot.
4. Если dev debug включен и сохранен mock token, создать mock user локально.
5. Если есть сохраненный token, запросить `/api/users/me` с Authorization header.
6. Если token устарел, очистить локальное хранилище.
7. Запросить `/api/users/me` с `credentials: include`, чтобы проверить httpOnly cookie.
8. Если cookie валиден, сохранить профиль.
9. Если есть Telegram WebApp `initData`, отправить `/api/auth/login`.
10. Если включен debug auth bypass, отправить mock initData.
11. Если ни один способ не сработал, очистить auth state.
Финиш: `user` заполнен или приложение показывает browser login screen.
```

## 4. Telegram Mini App Login

```text
Старт: frontend получил Telegram `initData`.
1. Frontend отправляет `POST /api/auth/login` с `{initData}`.
2. Backend проверяет подпись через `verify_telegram_init_data()`.
3. Из signed payload извлекаются Telegram ID, username, name, photo, start_param.
4. Backend ищет существующего пользователя по `telegram_id`.
5. Если есть телефон и найден web-only профиль с этим телефоном, профиль переносится
   в Telegram-аккаунт.
6. Если пользователь существует, обновляются identity fields и referral.
7. Если пользователя нет, создается `User` с role `owner` только для `OWNER_TELEGRAM_ID`,
   иначе `user`.
8. Backend коммитит профиль и заново загружает связи bookmakers/badges.
9. Backend создает JWT и ставит httpOnly cookie `shamrai_access_token`.
10. Frontend сохраняет response user и совместимый bearer token.
Финиш: пользователь авторизован, cookie участвует в последующих `/api` запросах.
```

## 5. Browser VK ID Login

```text
Старт: пользователь нажимает вход через VK ID.
1. Frontend генерирует PKCE state, code_verifier и redirect URL.
2. Пользователь проходит VK ID и возвращается на redirect URI.
3. Frontend извлекает `code`, `device_id`, `code_verifier`, `state`.
4. Frontend отправляет `POST /api/auth/vk/login`.
5. Backend валидирует наличие всех полей.
6. Backend обменивает code на VK profile через VK OAuth без глобального proxy.
7. Backend ищет `User` по `vk_user_id`.
8. Если пользователь найден, создает JWT/cookie для него.
9. Если пользователя нет, создает web-only user с отрицательным `telegram_id`.
10. Backend возвращает user и token.
11. Frontend сохраняет auth state.
Финиш: пользователь вошел как web/VK клиент и позже может привязать Telegram.
```

## 6. Link VK To Telegram Profile

```text
Старт: Telegram-пользователь в профиле запускает VK linking.
1. Frontend проходит тот же VK ID redirect/PKCE flow.
2. Frontend отправляет `POST /api/auth/vk/link` с VK code payload.
3. Backend авторизует текущего пользователя через JWT/cookie.
4. Backend получает `vk_user_id` у VK.
5. Если текущий Telegram уже связан с другим VK, возвращается conflict.
6. Backend ищет другой профиль с этим `vk_user_id`.
7. Если найден web-only профиль, его прогресс переносится в текущий Telegram user.
8. Если не найден, `current_user.vk_user_id` заполняется напрямую.
9. Backend коммитит изменения.
10. Frontend перезагружает `/api/users/me`.
Финиш: один клиентский кабинет связан с Telegram и VK без потери баланса/истории.
```

## 7. Telegram Bot Login Or Link

```text
Старт: browser/web-only клиент нажимает "Привязать Telegram" или "Войти через Telegram".
1. Frontend вызывает `POST /api/auth/telegram/bot-session`.
2. Backend создает короткоживущую auth session и возвращает `bot_url`.
3. Frontend открывает `https://t.me/<bot>?start=<auth-token>`.
4. Пользователь пишет боту через `/start`.
5. Telegram webhook распознает auth start_param и подтверждает session.
6. Frontend polling-ом вызывает `GET /api/auth/telegram/bot-session/{auth_token}`.
7. Когда session `confirmed`, backend upsert-ит Telegram user.
8. Если текущий пользователь web-only, backend переносит его профиль в Telegram user.
9. Session помечается consumed.
10. Backend ставит auth cookie и возвращает user/token.
Финиш: browser-сессия стала Telegram-связанной или вход завершился по timeout.
```

## 8. Logout

```text
Старт: пользователь нажимает logout.
1. Frontend отправляет `POST /api/auth/logout` с `credentials: include`.
2. Backend удаляет cookie `shamrai_access_token`.
3. Frontend очищает сохраненный bearer/debug token.
4. Frontend сбрасывает `user` и `token`.
Финиш: следующая загрузка снова проходит auth bootstrap.
```

## 9. Onboarding

```text
Старт: user существует, но `is_onboarded=false`.
1. `App.tsx` принудительно показывает `Onboarding`.
2. Frontend собирает опыт, банк, риск, БК, валюту, optional VK state.
3. Frontend отправляет `POST /api/users/me/onboard`.
4. Backend валидирует experience, bankroll, risk, bookmakers, currency.
5. Backend проверяет, что переданный `vk_user_id` совпадает с уже связанным профилем.
6. Backend строит рекомендацию: flat stake, monthly profit, missed 24h profit.
7. Backend подтягивает стандартных букмекеров и мапит выбранные code/id.
8. User получает выбранные bookmakers, primary bookmaker, currency, preferred sports.
9. `is_onboarded` становится true.
10. Если настроен admin report chat, в delivery outbox ставится Telegram report.
11. Backend возвращает recommendation и обновленный user.
Финиш: пользователь попадает в основной кабинет, анкета сохранена.
```

## 10. Skip Onboarding

```text
Старт: пользователь пропускает welcome quiz.
1. Frontend вызывает `POST /api/users/me/onboard/skip`.
2. Backend подтягивает стандартных букмекеров.
3. Если у пользователя нет selected bookmakers, назначаются все активные.
4. Если нет preferred sports, назначаются все sports labels.
5. `is_onboarded=true`, `free_bets_available=0`.
6. Backend коммитит и возвращает профиль.
Финиш: пользователь получает широкий доступ к feed без персональной калибровки.
```

## 11. Profile Dashboard

```text
Старт: frontend открывает профиль.
1. Frontend вызывает `GET /api/users/me/profile-dashboard`.
2. Backend загружает актуального пользователя со связями.
3. Если есть `vk_user_id`, backend refresh-ит VK delivery status.
4. Backend загружает стандартных bookmakers.
5. Backend собирает preferences, subscription status, payments, referral stats.
6. Backend добавляет сохраненный VK delivery payload.
7. Frontend отрисовывает профиль, БК, настройки, платежи, referrals и VK status.
Финиш: профиль загружен одним агрегированным payload.
```

## 12. Update Preferences

```text
Старт: пользователь меняет уведомления или отображение статистики.
1. Frontend отправляет `PUT /api/users/me/preferences`.
2. Backend проверяет `alert_min_coef` в диапазоне 1.0-1.6.
3. Backend обновляет флаг уведомлений о падении коэффициента.
4. Backend нормализует night mode start/end в `HH:MM`.
5. Backend запрещает одинаковое начало и конец ночного режима.
6. Backend обновляет preferred sports.
7. Backend проверяет `stats_display_mode` как `percent` или `flat`.
8. Backend коммитит и возвращает обновленные preferences.
Финиш: настройки сохранены и используются в фильтрах доставки/интерфейсе.
```

## 13. VK Delivery Consent Sync

```text
Старт: пользователь с linked VK открывает VK consent wizard/profile status.
1. Frontend вызывает `GET /api/users/me/vk-delivery-status`.
2. Backend вызывает `refresh_vk_delivery_status()`.
3. VK API проверяет membership/messages permission без глобального proxy.
4. Backend сохраняет `vk_group_member` и `vk_messages_allowed`.
5. Frontend показывает готовность доставки.
6. Если пользователь дает согласие через VK Bridge, frontend отправляет
   `PUT /api/users/me/vk-delivery-status`.
7. Backend сохраняет `vk_notifications_allowed` и снова refresh-ит verified permission.
Финиш: VK доставка считается готовой только при linked VK и `messages_allowed=true`.
```

## 14. Public Feed Load

```text
Старт: пользователь открывает ленту прогнозов.
1. Frontend вызывает `GET /api/bets/feed-page`.
2. Backend берет selected bookmakers текущего пользователя.
3. Query выбирает `Bet.status=pending` и `delivery_mode=feed`.
4. Для staff фильтр по БК не применяется.
5. Для user показываются untargeted bets или bets под выбранные БК.
6. Cursor ограничивает следующую страницу по `created_at` и `id`.
7. Backend загружает user_bets для текущей страницы.
8. Каждый bet сериализуется в `BetResponse`.
9. Если paid Stars bet не открыт, чувствительные поля маскируются.
10. Возвращаются `items`, `next_cursor`, `has_more`.
Финиш: frontend показывает ленту с корректным locked/unlocked state.
```

## 15. Take Feed Bet

```text
Старт: пользователь нажимает взять прогноз в feed.
1. Frontend отправляет `POST /api/bets/{bet_id}/take`.
2. Backend загружает bet.
3. Если bet не `feed` и пользователь не staff, возвращается 404.
4. Backend проверяет активный доступ: staff, positive balance или guarantee.
5. Если bet платный и доступа нет, возвращается 403.
6. Backend вызывает `record_user_bet_access(charge_match=has_sub)`.
7. Если запись уже есть в `user_bets`, возвращается `already_taken`.
8. Если нужно списать матч, баланс уменьшается на 1 и пишется ledger event.
9. В `user_bets` вставляется access row.
10. Backend коммитит.
Финиш: прогноз добавлен в My Bets, а баланс списан только при платном доступе.
```

## 16. Unlock Free Bet

```text
Старт: у пользователя есть `free_bets_available > 0`.
1. Frontend вызывает `POST /api/bets/{bet_id}/unlock_free`.
2. Backend проверяет free balance.
3. Backend загружает bet.
4. Если user_bets уже содержит bet, возвращается `already_unlocked`.
5. Backend добавляет row в `user_bets` с `access_type=free_bet`.
6. `free_bets_available` уменьшается на 1.
7. Backend коммитит.
Финиш: конкретный прогноз открыт без списания матча из абонемента.
```

## 17. Buy Bet Hint

```text
Старт: пользователь хочет купить подсказку по прогнозу.
1. Frontend отправляет `POST /api/bets/{bet_id}/buy-hint`.
2. Backend проверяет amount и существование bet.
3. Backend создает `PaymentAttempt` provider `telegram_stars`, currency `XTR`.
4. В metadata пишется `purchase_type=bet_hint`.
5. Backend коммитит attempt.
6. Backend создает Telegram Stars invoice link.
7. Frontend открывает invoice.
8. Telegram successful payment приходит в webhook.
9. `_process_payment_attempt()` помечает attempt succeeded.
10. Frontend вызывает `GET /api/bets/{bet_id}/hint?attempt_id=...`.
11. Backend проверяет succeeded attempt текущего user и возвращает hint.
Финиш: подсказка доступна только после verified Telegram Stars payment.
```

## 18. Admin Create Public Feed Bet

```text
Старт: staff/admin создает новый public forecast.
1. Frontend отправляет `POST /api/bets` или `POST /api/bets/with-coupon`.
2. Backend проверяет staff role.
3. Backend валидирует event, coefficient, bookmaker ids, optional links/coupon.
4. Coupon image сохраняется в `backend/static/coupons`.
5. Создается `Bet` с `delivery_mode=feed`, `status=pending`.
6. Связи `bet.bookmakers` заполняются выбранными БК.
7. Если включен live alarm, создается live signal broadcast.
8. Backend коммитит и возвращает `BetResponse`.
Финиш: прогноз появляется в feed для целевой аудитории.
```

## 19. Admin Update Bet

```text
Старт: staff редактирует forecast.
1. Frontend отправляет `PUT /api/bets/{bet_id}`.
2. Backend загружает bet со связанными bookmakers.
3. Если bet удален и не legacy private case, редактирование запрещено.
4. Backend применяет только переданные поля.
5. Коэффициент валидируется.
6. Текстовые поля чистятся, match link нормализуется.
7. Bookmaker ids проверяются на существование.
8. Bookmaker links нормализуются относительно выбранных БК.
9. Backend коммитит и возвращает обновленный bet.
Финиш: прогноз изменен без изменения unrelated fields.
```

## 20. Admin Resolve Bet

```text
Старт: staff выставляет результат `win`, `loss` или `refund`.
1. Frontend отправляет `PUT /api/bets/{bet_id}/resolve`.
2. Backend проверяет допустимый status.
3. Backend загружает bet и определяет, первое ли это завершение.
4. Bet получает новый status и `resolved_at`.
5. Открытые forecast requests по bet останавливаются.
6. Если это первое завершение, backend загружает всех takers из `user_bets`.
7. Для `loss` и charged `paid_match` клиент получает +2 матча supercompensation.
8. Для win/loss/refund каждому клиенту ставится result message в delivery outbox.
9. В admin group ставится summary notification.
10. Backend коммитит.
11. Если `win`, backend проверяет последние 5 результатов takers и выдает badge
    `sharp_mind` при пяти победах подряд.
12. Backend возвращает resolved bet с counters.
Финиш: результат сохранен, клиенты уведомлены, компенсации/бейджи начислены.
```

## 21. Odds Drop Notification

```text
Старт: admin фиксирует, что линия просела после выдачи прогноза.
1. Frontend отправляет `PUT /api/bets/{bet_id}/odds-drop`.
2. Backend сохраняет `odds_dropped_to`.
3. Для рассылки frontend вызывает `POST /api/bets/{bet_id}/odds-drop/notify`.
4. Backend требует privileged admin.
5. Backend загружает клиентов, которые уже взяли bet и разрешили такие уведомления.
6. Backend строит сообщение из message template.
7. Для каждого получателя создается доставка.
8. Если хотя бы один queued, ставится `odds_drop_notified_at`.
9. Backend коммитит и возвращает totals/errors.
Финиш: только реальные получатели прогноза получают уведомление о просадке линии.
```

## 22. Package Plan List

```text
Старт: пользователь открывает tariffs.
1. Frontend вызывает `GET /api/subscriptions/plans`.
2. Backend загружает все plans.
3. Не-admin видит только active plans.
4. Admin с `include_inactive=true` может видеть inactive.
5. Для каждого plan backend ищет active A/B config.
6. Если config есть и у user `ab_group=B`, применяются B prices.
7. Иначе применяются A/default prices.
8. Backend возвращает response plans.
Финиш: frontend показывает тарифы с учетом A/B группы.
```

## 23. Match Package Payment With YooKassa

```text
Старт: пользователь выбирает plan и YooKassa.
1. Frontend вызывает `POST /api/payments/yookassa/create`.
2. Backend загружает active plan.
3. Backend валидирует promo code.
4. Если promo нет, backend применяет referral discount.
5. Amount пересчитывается с minimum 1 RUB.
6. Backend создает `PaymentAttempt(provider=yookassa, status=pending)`.
7. Backend коммитит attempt.
8. Backend отправляет create payment request в YooKassa с Idempotence-Key attempt id.
9. Backend сохраняет `provider_payment_id`.
10. Frontend получает `confirmation_url` и отправляет пользователя на оплату.
11. YooKassa присылает webhook `payment.succeeded`.
12. Backend re-fetch-ит payment у YooKassa.
13. Backend сверяет status, amount, currency, attempt_id.
14. `_process_payment_attempt()` активирует абонемент через `activate_match_subscription()`.
15. Backend ставит Telegram confirmation в outbox и коммитит.
Финиш: баланс матчей увеличен ровно один раз.
```

## 24. Match Subscription Payment With Tegro

```text
Старт: пользователь выбирает plan и Tegro.
1. Frontend вызывает `POST /api/payments/tegro/create`.
2. Backend загружает active RUB plan.
3. Backend валидирует promo/referral discount.
4. Backend создает `PaymentAttempt(provider=tegro, status=pending)`.
5. Backend строит Tegro payment form URL.
6. Frontend открывает confirmation URL.
7. Tegro присылает webhook JSON или form-data.
8. Backend проверяет MD5 signature.
9. Backend игнорирует test notification.
10. Backend сверяет shop id и order_id.
11. `_process_payment_attempt()` сверяет provider, amount, currency.
12. `activate_match_subscription()` начисляет matches и ledger.
13. Backend ставит Telegram confirmation в outbox и коммитит.
Финиш: абонемент активирован, duplicate webhook не начисляет повторно.
```

## 25. Telegram Stars Payment

```text
Старт: пользователь покупает Stars invoice: абонемент, прогноз, подсказку или crowd contribution.
1. Backend заранее создает `PaymentAttempt(provider=telegram_stars, currency=XTR)`.
2. Backend вызывает Telegram `createInvoiceLink`.
3. Frontend открывает invoice URL.
4. Telegram присылает `pre_checkout_query`.
5. Backend извлекает attempt id из invoice payload.
6. Backend проверяет pending attempt, provider, currency, total amount.
7. Backend отвечает `answerPreCheckoutQuery(ok=true/false)`.
8. Telegram присылает `successful_payment`.
9. Backend снова извлекает attempt id, amount, currency.
10. `_process_payment_attempt()` активирует нужный purchase type.
11. Confirmation delivery ставится в outbox.
Финиш: Stars purchase обработан только после successful payment update.
```

## 26. Promo Validation

```text
Старт: пользователь вводит промокод.
1. Frontend вызывает `GET /api/payments/promo/validate?code=...`.
2. Backend upper-case нормализует code.
3. Backend ищет active promo с `valid_until > now`.
4. Если promo не найден, возвращает 400.
5. Если promo привязан к другому user, возвращает 400.
6. Backend возвращает code и `discount_percent`.
Финиш: frontend может показать скидку до создания платежа.
```

## 27. Manual Subscription Assignment

```text
Старт: privileged admin выдает абонемент вручную.
1. Frontend отправляет `POST /api/subscriptions/assign`.
2. Backend проверяет target user.
3. Backend проверяет plan.
4. Backend вызывает `activate_match_subscription(payment_provider=admin_manual)`.
5. Backend пишет admin audit log.
6. Backend коммитит.
7. Backend возвращает subscription.
Финиш: клиент получает матчи без внешней оплаты, действие аудируется.
```

## 28. Announcement Broadcast

```text
Старт: privileged admin создает announcement.
1. Frontend отправляет multipart `POST /api/admin/announcements`.
2. Backend парсит filters: sport, bookmaker ids/names, min coefficient, match link.
3. Optional coupon image сохраняется в static.
4. Backend строит целевые аудитории отдельно для Telegram, VK и web.
5. Backend строит HTML message через message template.
6. Для Telegram формируются sendMessage/sendPhoto jobs.
7. Для VK HTML конвертируется в plain VK text, optional image path прикладывается.
8. Для web создаются `PersonalSignal` records и Web Push outbox jobs.
9. Telegram jobs отправляются ограниченно параллельно.
10. VK jobs отправляются ограниченно параллельно.
11. Backend собирает delivery breakdown и errors.
Финиш: admin получает отчет по аудитории, sent/failed/queued/web push.
```

## 29. Private Forecast Teaser Broadcast

```text
Старт: privileged admin создает закрытый forecast teaser.
1. Frontend отправляет multipart `POST /api/admin/forecast-broadcast`.
2. Backend требует хотя бы одну выбранную БК.
3. Backend создает `Bet` с `delivery_mode=sales_private`, placeholder event name,
   коэффициентом, fair coefficient, teaser text и selected bookmakers.
4. Backend находит smart target users по sport, bookmakers, min coefficient.
5. Для каждого target user создается `ForecastRequest(status=announced)`.
6. Backend коммитит bet и requests.
7. Backend отправляет teasers в Telegram/VK/web в зависимости от доступных каналов.
8. В teaser добавляются кнопки "беру" и "не беру".
9. Backend возвращает delivery report.
Финиш: у каждого клиента есть отдельная forecast request, ожидающая реакции.
```

## 30. Paid Set Broadcast

```text
Старт: privileged admin создает платный набор.
1. Frontend отправляет multipart `POST /api/admin/paid-set-broadcast`.
2. Backend валидирует title, event name, outcome, price_rub и selected bookmakers.
3. Backend создает `Bet` с `delivery_mode=paid_set`.
4. Backend подбирает target users.
5. Для каждого target user создается `ForecastRequest(status=announced)`.
6. Backend строит paid-set teaser из template.
7. Web recipients получают `PersonalSignal` и optional Web Push.
8. Telegram recipients получают button teaser.
9. VK recipients проходят refresh permission и получают VK keyboard.
10. Backend возвращает delivery breakdown.
Финиш: платный набор продается через персональный диалог, не через in-app checkout.
```

## 31. Client Takes Forecast Teaser

```text
Старт: клиент нажимает "беру" в Telegram, VK или web-chat signal.
1. Channel handler извлекает `forecast_request_id` и action.
2. Backend вызывает `set_forecast_request_interested()`.
3. Backend проверяет, что request принадлежит этому user.
4. Если bet stopped/deleted, возвращает stopped message.
5. Если status уже final/processing/declined, возвращает соответствующий idempotent message.
6. Если клиенту нужен контакт/оплата и нет full access, возвращается contact-required message.
7. Status атомарно меняется с `announced` на `interested`.
8. В admin group ставится notification о реакции клиента.
9. Если bet `auto_send_on_interest=true`, backend пытается доставить full forecast сразу.
10. Если auto-send невозможен или выключен, заявка идет sales manager/admin.
Финиш: request либо стала `interested`, либо full forecast уже доставлен, либо клиент получил отказ/инструкцию.
```

## 32. Client Declines Forecast Teaser

```text
Старт: клиент нажимает "не беру".
1. Channel handler вызывает `set_forecast_request_declined()`.
2. Backend проверяет ownership.
3. Backend обрабатывает idempotent statuses: delivered, interested, processing, declined,
   cancelled, removed.
4. Если request еще `announced`, status становится `declined`.
5. `responded_at` заполняется текущим временем.
6. В admin group ставится notification о decline.
7. Клиент получает короткий ответ.
Финиш: request больше не участвует в delivery и видна admin как отказ.
```

## 33. Admin Sends Full Forecast

```text
Старт: admin видит interested forecast request.
1. Frontend вызывает `POST /api/admin/forecast-requests/{request_id}/send` или `send-saved`.
2. Backend загружает request, user и bet.
3. Если это paid set, full auto-send запрещен.
4. Если переданы полные поля прогноза, backend обновляет bet.
5. Backend вызывает `deliver_forecast_request(send_to_client=true)`.
6. Delivery service проверяет status и запрещает send до client "беру".
7. Если нет access, возвращается 403.
8. Delivery method выбирается: bot, VK, VK+bot или web.
9. Status атомарно меняется `interested -> processing`.
10. `record_user_bet_access(charge_match=true)` списывает один матч.
11. Forecast request получает final status `sent` или method-specific status.
12. Full forecast delivery ставится в outbox.
13. Backend коммитит.
Финиш: клиент получает full forecast по выбранному каналу, баланс и request state обновлены.
```

## 34. Auto Full Forecast Delivery From Outbox

```text
Старт: outbox получил channel `forecast_auto_delivery` или `forecast_full_delivery`.
1. Delivery daemon claims due row.
2. Dispatcher вызывает forecast delivery service по `request_id`.
3. Service загружает request, user, bet.
4. Service проверяет status, access, readiness full forecast fields and links.
5. Service выбирает delivery method или использует заданный.
6. Если Telegram delivery, отправляется bot message/coupon/links.
7. Если VK delivery, refresh-ится permission и отправляется VK message/keyboard/image.
8. Если web delivery, создается personal signal.
9. При успехе outbox row становится `sent`.
10. При transient fail row становится `retry` с backoff.
11. При исчерпании attempts row становится `failed`.
Финиш: доставка завершена или останется в очереди с понятным retry/failure state.
```

## 35. Admin Marks Manual Sale

```text
Старт: admin обработал клиента вне приложения.
1. Frontend вызывает `POST /api/admin/forecast-requests/{request_id}/mark-manual`.
2. Backend загружает forecast request.
3. `deliver_forecast_request(send_to_client=false, delivery_method=manual)` проверяет status.
4. Для paid set access записывается как `manual_paid_set` без списания матча.
5. Для private forecast access записывается как manual marker.
6. Request получает `manual_sent`, `handled_by`, `delivered_at`.
7. Backend коммитит.
Финиш: продажа/выдача отражена в статистике без автоматической отправки сообщения.
```

## 36. Stop Forecast Broadcast

```text
Старт: admin останавливает private forecast или paid set.
1. Frontend вызывает `POST /api/admin/forecast-broadcast/{bet_id}/stop`.
2. Backend загружает private/paid-set bet.
3. Backend считает takers.
4. Backend загружает all forecast requests по bet.
5. Requests в statuses announced/interested/declined/cancelled переводятся в `removed`.
6. Processing requests пропускаются.
7. `auto_send_on_interest` выключается.
8. В admin group ставится stopped notification.
9. Пишется admin audit log.
10. Backend коммитит.
Финиш: новые реакции по stopped teaser не приводят к доставке.
```

## 37. Remove Client From Forecast Request

```text
Старт: admin удаляет клиента из request.
1. Frontend вызывает `POST /api/admin/forecast-requests/{request_id}/remove-client`.
2. Backend загружает request.
3. Если request `processing`, удаление запрещено.
4. Если еще не `removed`, backend вызывает `revoke_user_bet_access()`.
5. Revoke удаляет `user_bets` row и обратным ledger event корректирует баланс.
6. Request получает `removed`, `handled_by`, balance fields.
7. Пишется admin audit log.
8. Backend коммитит.
Финиш: клиент исключен из forecast request, доступ и баланс восстановлены при необходимости.
```

## 38. Personal Signal Broadcast

```text
Старт: backend должен доставить live signal/support/system message группе пользователей.
1. Service вызывает `broadcast_personal_signals()` или `deliver_personal_signal()`.
2. Для каждого user создается `PersonalSignal`.
3. Payload строится через `signal_to_payload()`.
4. WebSocket hub отправляет payload всем активным соединениям user.
5. Если нужно Telegram/Web Push, outbox jobs создаются с dedupe keys.
6. При commit данные становятся видны в history.
7. Frontend `WebSignalListener` принимает realtime event.
8. Если WebSocket был offline, frontend догоняет историю через `/api/signals/history`
   или chat signal messages.
Финиш: сигнал виден в интерфейсе и доставлен внешними каналами где возможно.
```

## 39. Signal WebSocket Connect

```text
Старт: frontend хочет realtime personal signals.
1. Frontend вызывает `POST /api/signals/stream-ticket`.
2. Backend выдает short-lived ticket.
3. Frontend открывает WebSocket `/api/signals/stream?ticket=...`.
4. Backend consumes ticket, загружает user.
5. Если ticket/user невалидны, socket закрывается policy violation.
6. Hub регистрирует socket за `user.telegram_id`.
7. Frontend отправляет `ping`, backend отвечает `pong`.
8. На новые signals hub отправляет JSON payload.
9. При disconnect socket удаляется из hub.
Финиш: realtime канал активен без JWT в URL.
```

## 40. Native Chat Load

```text
Старт: пользователь открывает web messenger.
1. Frontend вызывает `GET /api/chat/conversations`.
2. Backend строит две conversation entries: `signals` и `support`.
3. Для signals берется последний `PersonalSignal` не support-типа.
4. Для support берется `ChatConversation(kind=support)` если существует.
5. Backend считает unread counters по read cursors.
6. Frontend вызывает messages endpoints для выбранной conversation.
7. Signals читаются из `personal_signals`, support messages из `chat_messages`.
8. Frontend отображает историю и pagination cursors.
Финиш: пользователь видит сигналы и поддержку в едином messenger UI.
```

## 41. Native Client Sends Support Message

```text
Старт: обычный user отправляет текст в support chat.
1. Frontend создает `client_message_id` и optimistic message.
2. Frontend отправляет `POST /api/chat/conversations/support/messages`.
3. Backend запрещает staff использовать client endpoint.
4. Backend создает support conversation, если ее еще нет.
5. Если conversation closed, возвращает conflict.
6. Backend чистит текст и проверяет длину до 4000 символов.
7. Backend проверяет idempotency по `(sender_user_id, client_message_id)`.
8. Backend создает `ChatMessage(sender_role=user)`.
9. `notify_chat_message()` отправляет WebSocket owner/staff updates.
10. Staff пользователям ставится Web Push outbox job.
11. Backend возвращает server message.
12. Frontend заменяет optimistic message на server message.
Финиш: сообщение сохранено, staff получил realtime/push notification.
```

## 42. Native Staff Replies In Chat

```text
Старт: staff открывает admin web chat и отвечает клиенту.
1. Frontend вызывает `GET /api/chat/admin/conversations`.
2. Staff выбирает conversation и загружает messages.
3. Frontend отправляет `POST /api/chat/admin/conversations/{id}/messages`.
4. Backend проверяет staff role.
5. Backend загружает support conversation.
6. Если conversation closed, backend reopens it.
7. Backend создает idempotent `ChatMessage(sender_role=admin/moderator/owner)`.
8. `notify_chat_message()` отправляет WebSocket owner/staff updates.
9. Клиенту ставится Web Push outbox job при наличии subscription.
10. Backend возвращает message.
Финиш: клиент видит ответ в realtime или получит push.
```

## 43. Chat Read Cursor

```text
Старт: пользователь или staff прочитал сообщения.
1. Frontend отправляет read endpoint с last read id.
2. Backend проверяет, что message существует в conversation.
3. Если cursor есть и новый id больше старого, cursor обновляется.
4. Если cursor отсутствует, создается `ChatReadCursor`.
5. Backend отправляет realtime `chat.read.updated` этому user.
6. Response содержит last read id.
Финиш: unread counters уменьшаются на следующих conversation payloads.
```

## 44. Web Push Subscription

```text
Старт: PWA/browser поддерживает push и пользователь разрешил уведомления.
1. Frontend регистрирует service worker.
2. Frontend запрашивает VAPID public key через `/api/signals/web-push/public-key`.
3. Browser создает PushSubscription.
4. Frontend отправляет `PUT /api/signals/web-push/subscription`.
5. Backend сохраняет subscription JSON в `users.web_push_subscription`.
6. При сигнале/чате backend создает `web_push_signal` delivery outbox item.
7. Outbox dispatcher вызывает Web Push delivery service.
8. Если push endpoint invalid/expired, backend очищает subscription.
Финиш: пользователь получает browser push или subscription безопасно удаляется при ошибке.
```

## 45. VK Callback Confirmation

```text
Старт: VK dashboard отправляет `type=confirmation` на `/api/vk/callback`.
1. Backend читает JSON payload.
2. Backend видит `type=confirmation`.
3. Backend не требует `secret`.
4. Backend не блокирует из-за group_id mismatch, только логирует mismatch.
5. Backend возвращает `VK_CALLBACK_CONFIRMATION_CODE` как plain text.
Финиш: VK dashboard подтверждает callback server.
```

## 46. VK Message Permission Events

```text
Старт: VK отправляет non-confirmation callback.
1. Backend проверяет `group_id`.
2. Backend проверяет `VK_CALLBACK_SECRET`.
3. Если `message_allow`, пользователь по VK id получает `vk_messages_allowed=true`.
4. Если `message_deny`, пользователь получает `vk_messages_allowed=false`.
5. Если `message_new`, linked user fallback-ом получает `vk_messages_allowed=true`.
6. Если message содержит forecast button/text, запускается forecast action handler.
7. Backend возвращает `ok`.
Финиш: локальная permission state синхронизирована с VK event.
```

## 47. VK Message Delivery

```text
Старт: сервис хочет отправить VK message пользователю.
1. Проверяется `user.vk_user_id`.
2. Проверяется `user.vk_messages_allowed=true`.
3. Проверяется runtime config `VK_GROUP_ID` и `VK_GROUP_ACCESS_TOKEN`.
4. VK API request выполняется без process-wide HTTPS proxy.
5. Если VK вернул success, delivery считается ok.
6. Если VK вернул permission error 901/902, user помечается denied.
7. Delivery не ретраит permission error бесконечно.
8. Другие ошибки возвращаются dispatch caller/outbox.
Финиш: сообщение доставлено или permission state безопасно обновлен.
```

## 48. Telegram Webhook

```text
Старт: Telegram отправляет update на `/api/telegram/webhook`.
1. Backend проверяет `X-Telegram-Bot-Api-Secret-Token`.
2. Backend парсит JSON update.
3. Если это `/start`, строится welcome/web app response.
4. Если start_param относится к bot auth session, session подтверждается.
5. Если это forecast callback button, вызывается forecast action handler.
6. Если это sales manager callback, вызывается admin/sales action handler.
7. Если это payment update, управление передается payment processor.
8. Backend возвращает Telegram-compatible response/status.
Финиш: update обработан идемпотентно или безопасно проигнорирован.
```

## 49. Delivery Outbox Daemon

```text
Старт: `ENABLE_BACKGROUND_TASKS=true`, daemon запущен.
1. Daemon вызывает `process_delivery_outbox_batch()`.
2. Batch открывает DB session и claims due deliveries.
3. Due statuses: pending, retry, stale processing.
4. Claimed rows получают `processing` и `locked_at`.
5. Batch запускает dispatch tasks с semaphore по channel.
6. Каждый task загружает delivery by id.
7. Dispatcher вызывает Telegram, VK, Web Push или forecast delivery.
8. При `ok=true` row становится `sent`.
9. При ошибке attempt_count увеличивается.
10. Если attempts остались, row становится `retry` с exponential backoff.
11. Если attempts закончились, row становится `failed`.
12. Daemon спит коротко при claimed rows или `DELIVERY_OUTBOX_IDLE_SECONDS` без работы.
Финиш: очередь постепенно доставляет pending jobs и сохраняет видимый статус ошибок.
```

## 50. User Stats Timeline

```text
Старт: пользователь открывает статистику/My Bets timeline.
1. Frontend вызывает `GET /api/users/me/bets/timeline?period=...`.
2. Backend нормализует period: week, month, quarter, all.
3. Backend выбирает resolved win/loss bets из `user_bets`.
4. Каждая запись превращается в stat item через `stat_item_from_bet()`.
5. Paid client access идет в main items.
6. Free/admin/other access идет в excluded items.
7. `build_performance_payload()` считает summary, timeline, breakdowns.
8. Excluded summary добавляется отдельно.
9. Frontend рисует KPI, график, breakdown и timeline rows.
Финиш: пользователь видит performance только по платным клиентским прогнозам отдельно от freebies.
```

## 51. Admin Stats Export

```text
Старт: admin запускает export статистики.
1. Frontend вызывает admin stats/export или drive-export endpoint.
2. Backend проверяет staff/admin access.
3. Backend загружает items через `stats_export` loaders.
4. Items нормализуются по period, author/client/source.
5. Workbook builder создает XLSX sheets, KPI, breakdowns, details и логотипы БК.
6. Для download backend возвращает файл streaming response.
7. Для Google Drive backend создает async job.
8. Drive service публикует файл как XLSX или Google Sheet при включенной конфигурации.
9. Frontend polling-ом проверяет job status.
Финиш: admin получает downloadable/exported statistics artifact.
```

## 52. Global Stats

```text
Старт: пользователь открывает общий performance screen.
1. Frontend вызывает `GET /api/stats/global`.
2. Backend собирает resolved bets.
3. Считаются win/loss/refund, winrate, ROI, average coefficient, profit.
4. Response возвращается без пользовательских секретов.
5. Frontend отображает summary.
Финиш: пользователь видит глобальную статистику проекта.
```

## 53. User Export Timeline

```text
Старт: пользователь скачивает свою историю.
1. Frontend вызывает `/api/users/me/bets/timeline/export`.
2. Backend строит тот же timeline payload.
3. Items превращаются в плоские rows.
4. Для CSV создается streaming text response.
5. Для XLSX создается workbook response.
6. Filename формируется по period/export type.
Финиш: пользователь получает файл со своей историей прогнозов.
```

## 54. Admin CRM User Update

```text
Старт: admin меняет карточку клиента.
1. Frontend вызывает `PUT /api/admin/users/{user_id}`.
2. Backend проверяет staff/privileged access according to operation.
3. Backend загружает target user.
4. Backend применяет разрешенные поля: роль, баланс, теги, настройки и прочее.
5. Sensitive role changes ограничиваются правами owner/admin.
6. Backend пишет admin audit log с changed fields.
7. Backend коммитит и возвращает `UserResponse`.
Финиш: CRM-данные обновлены и действие попало в audit trail.
```

## 55. Admin Delete User

```text
Старт: admin удаляет пользователя.
1. Frontend вызывает `DELETE /api/admin/users/{user_id}`.
2. Backend проверяет права и target user.
3. Backend не позволяет удалить protected/owner cases без нужного уровня.
4. Связанные записи удаляются cascade или вручную по модели.
5. Пишется admin audit log.
6. Backend коммитит.
Финиш: профиль и зависимые пользовательские данные удалены согласно ORM/cascade правилам.
```

## 56. Message Template Edit

```text
Старт: admin открывает настройки шаблонов.
1. Frontend вызывает `GET /api/admin/message-templates`.
2. Backend возвращает templates из DB или defaults.
3. Admin редактирует body.
4. Frontend вызывает `PUT /api/admin/message-templates/{key}`.
5. Backend проверяет key и сохраняет body/variables/updated_by.
6. Для reset frontend вызывает `POST /reset`.
7. Backend восстанавливает default body.
Финиш: будущие рассылки/результаты/прогнозы используют обновленный шаблон.
```

## 57. Marketing Daily Spin

```text
Старт: пользователь нажимает daily spin.
1. Frontend вызывает `POST /api/marketing/daily-spin`.
2. Backend ищет последний `DailyRewardClaim`.
3. Если прошло меньше 24 часов и не debug, возвращает cooldown error.
4. Backend случайно выбирает `free_bet` или `promo_code`.
5. Для free bet увеличивает `free_bets_available`.
6. Для promo берет active generic promo или создает bound promo.
7. Backend пишет `DailyRewardClaim`.
8. Backend пишет `LivePulseLog`.
9. Backend коммитит и возвращает reward detail.
Финиш: пользователь получил бонус, повтор ограничен 24 часами.
```

## 58. Marketing Swipe

```text
Старт: пользователь открывает Shamrai Swipe.
1. Frontend вызывает `GET /api/marketing/swipe-candidate`.
2. Backend берет последний pending conversion bet.
3. Если production и bet нет, возвращает 404.
4. В debug backend может seed-ить demo bet.
5. Frontend показывает варианты П1/Х/П2.
6. Пользователь отправляет guess через `POST /api/marketing/swipe`.
7. Backend нормализует guess и сравнивает с `bet.outcome`.
8. При совпадении создается персональный promo `MIND...` на 50%.
9. Backend пишет LivePulseLog и коммитит.
Финиш: пользователь получает результат и, при совпадении, временный промокод.
```

## 59. Marketing Quiz

```text
Старт: пользователь открывает analytical quiz.
1. Frontend вызывает `GET /api/marketing/quiz-active`.
2. Backend ищет active quiz для conversion bet.
3. Answers не возвращаются клиенту.
4. Пользователь отправляет answers через `POST /api/marketing/quiz-submit`.
5. Backend загружает quiz by id/bet id или active fallback.
6. Backend сравнивает answers с `question.correct`.
7. Если все ответы верны и вопросов минимум 3, quiz passed.
8. При passed создается bound promo `LOGIC...` на `discount_reward`.
9. Backend пишет LivePulseLog и коммитит.
Финиш: клиент получает score, total, discount и optional promo code.
```

## 60. Marketing PvP Battle

```text
Старт: пользователь открывает Battle of Minds.
1. Frontend вызывает `GET /api/marketing/pvp-active`.
2. Backend возвращает последний PvP battle и распределение голосов.
3. Пользователь голосует через `POST /api/marketing/pvp-vote`.
4. Backend нормализует option как A или B.
5. Backend ищет существующий vote по `(battle_id, user_id)`.
6. Если vote существует и option изменился, старый счетчик уменьшается, новый растет.
7. Если vote отсутствует, создается `PvPBattleVote`, счетчик растет.
8. Backend коммитит и возвращает новое распределение.
Финиш: один пользователь имеет один актуальный голос в battle.
```

## 61. Marketing Marathon And Pulse

```text
Старт: frontend запрашивает marketing widgets.
1. Для pulse вызывается `GET /api/marketing/pulse`.
2. Backend возвращает 5 последних `LivePulseLog`.
3. В debug при пустой таблице backend может создать demo pulse messages.
4. Для marathon вызывается `GET /api/marketing/marathon`.
5. Backend ищет active marathon.
6. В debug при отсутствии marathon создается demo record.
7. В production при отсутствии данных возвращается 404.
Финиш: widgets показывают реальные production данные или debug seed только локально.
```

## 62. Crowd Bet Funding

```text
Старт: пользователь открывает crowd bet widget.
1. Frontend вызывает `GET /api/crowd-bets/active`.
2. Backend возвращает active/funding crowd bet.
3. Пользователь выбирает contribution и вызывает `POST /api/crowd-bets/{id}/fund`.
4. Backend создает payment attempt или verified contribution path according to payment flow.
5. После verified payment `apply_verified_crowd_contribution()` добавляет participant amount.
6. `current_amount` увеличивается.
7. Если target reached, status может перейти в opened.
Финиш: contribution засчитан только после verified payment processing.
```

## 63. Bookmaker Redirect

```text
Старт: пользователь нажимает bookmaker link в прогнозе.
1. Frontend/Telegram получает safe URL `/api/go/bets/{bet_id}/bookmakers/{bookmaker_id}`.
2. Backend загружает bet.
3. Backend ищет URL для конкретной БК в `bookmaker_links` или fallback `match_link`.
4. Если URL отсутствует или некорректен, возвращается unavailable HTML response.
5. Если URL валиден, backend возвращает redirect.
Финиш: внешний bookmaker URL не раскрывается как произвольная неподтвержденная ссылка без проверки.
```

## 64. Coupon Upload And Serving

```text
Старт: admin прикладывает coupon image к forecast/announcement.
1. Frontend отправляет multipart form with `coupon_image`.
2. Backend проверяет upload endpoint и сохраняет файл в `backend/static/coupons`.
3. В DB сохраняется public path `/static/coupons/...`.
4. Frontend и delivery messages используют этот path.
5. Docker preview nginx монтирует backend static volume в frontend nginx.
6. Public host nginx alias/proxy обслуживает `/static/coupons`.
7. Telegram/VK delivery может использовать public URL или локальный файл path для upload.
Финиш: купон доступен клиенту и внешним каналам без хранения в frontend bundle.
```

## 65. Health Diagnostics

```text
Старт: developer/admin проверяет состояние приложения.
1. Public smoke вызывает `/api/health`.
2. Backend возвращает lightweight status без auth.
3. Для `/api/health/payments`, `/api/health/telegram`, `/api/health/vk`,
   `/api/health/vk/deep` требуется staff access, если не debug.
4. Payments health показывает configured flags без секретов.
5. Telegram health проверяет getMe/getWebhookInfo и expected URL.
6. VK health показывает configured flags без токенов/secret values.
7. Deep VK health делает safe live API probe.
Финиш: diagnostics помогают найти misconfiguration без раскрытия секретов.
```

## 66. Local Verification

```text
Старт: developer готов проверить изменения.
1. Запускается `powershell -ExecutionPolicy Bypass -File .\scripts\verify-local.ps1`.
2. Script проверяет наличие backend venv.
3. Backend: Python version, dependency import, compileall, alembic heads, `import src.main`.
4. Если не передан `-SkipBackendTests`, запускается `unittest discover`.
5. Frontend: запускаются `npm run lint` и `npm run build`.
6. При любом non-zero exit script падает.
7. При успехе печатает `verify_local_ok`.
Финиш: локальная проверка прошла или developer получил точку отказа.
```

## 67. Preview Deploy Verification

```text
Старт: пользователь явно попросил deploy/redeploy/check Shamrai VDS.
1. Перед изменениями инвентаризируются Docker containers, compose projects,
   listening ports, disk и target path.
2. Проверяется canonical target: `/opt/shamrai-mini-app`, project `shamrai`, port `8082`.
3. Если stale Shamrai-like deployment владеет port 8082, сначала чинится конфликт.
4. Не создаются новые app directories, compose projects или frontend ports.
5. Docker compose build/up выполняется только в canonical project.
6. Проверяется `http://127.0.0.1:8082/api/health`.
7. Если нужен public frontend, `frontend/dist` публикуется в `/var/www/shamrai_web/dist`.
8. Public HTML проверяется на новые hashed `assets/*.js` и `assets/*.css`.
Финиш: preview/public state проверен по правильной поверхности, без затрагивания unrelated nginx/apps.
```
