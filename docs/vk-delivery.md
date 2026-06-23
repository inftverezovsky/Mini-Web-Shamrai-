# VK Synchronization And Delivery

This project has three separate VK layers. Do not collapse them into one flag.

## 1. VK ID Linking

VK ID linking happens through the frontend VK ID flow and backend auth endpoints:

- Frontend helpers: `frontend/src/utils/vkId.ts`
- Backend endpoints: `backend/src/api/auth.py`
- Stored field: `users.vk_user_id`

`users.vk_user_id` means Shamrai knows which VK person belongs to this cabinet. It does not mean the community can send personal VK messages.

## 2. Message Permission Synchronization

VK message delivery needs permission from the Shamrai community. The server stores it in `users.vk_messages_allowed`.

Authoritative and fallback sync paths:

- VK Mini App bridge: `VKWebAppAllowMessagesFromGroup` in `frontend/src/utils/vkDelivery.ts`
- Profile API persist/refresh: `PUT /api/users/me/vk-delivery-status`
- VK callback `message_allow`: set `users.vk_messages_allowed=true`
- VK callback `message_deny`: set `users.vk_messages_allowed=false`
- VK callback `message_new`: fallback for manual dialog flow; if a linked user writes to the community, mark messages as allowed
- VK API refresh: `messages.isMessagesFromGroupAllowed` in `src.services.vk_delivery.refresh_vk_delivery_status`

The profile screen should show VK delivery as ready only when `vk_user_id` exists and `messages_allowed=true`.

## 3. Callback API Contract

VK dashboard callback settings:

- Callback URL: `https://shamra1.pro/api/vk/callback`
- API version: `5.199`
- Event types to enable: `message_new`, `message_allow`, `message_deny`, `message_event`
- Secret key must match runtime `VK_CALLBACK_SECRET`

Confirmation behavior is deliberately special:

- VK sends `{"type":"confirmation","group_id":...}` and often does not include `secret`.
- Shamrai must return exactly the runtime `VK_CALLBACK_CONFIRMATION_CODE` as a plain-text response body.
- Do not reject confirmation because of a stale or missing `VK_GROUP_ID`; log the mismatch and return the code.
- For every non-confirmation event, keep strict `group_id` and `secret` checks.

This prevents the VK dashboard error "Сервер вернул неправильный ответ" when runtime group configuration drifts.

## 4. Delivery Rules

Delivery helpers live in `backend/src/services/vk_delivery.py`.

Before sending to VK, code must satisfy:

- `users.vk_user_id` is a positive VK user id
- `users.vk_messages_allowed` is true
- `VK_GROUP_ID` and `VK_GROUP_ACCESS_TOKEN` are configured at runtime

VK API calls intentionally bypass the process-wide `HTTPS_PROXY`. On the canonical VDS, direct `api.vk.com` connectivity works, while a stale global proxy can return connection refused and break both permission checks and message sends. Keep this bypass in `vk_delivery.py` and the VK ID OAuth bypass in `auth.py`.

Forecast and broadcast delivery paths:

- Admin broadcasts refresh VK audience through `refresh_vk_delivery_status`, then send with `send_vk_message_to_user`.
- Forecast teasers use VK keyboards from `build_vk_forecast_keyboard`.
- Full forecast delivery uses `forecast_delivery.send_full_forecast_to_vk_client`.
- Delivery outbox uses channel `vk_message` and dispatches through `send_vk_message_to_user`.

If VK returns permission errors such as 901 or 902, treat that as a revoked permission and mark `users.vk_messages_allowed=false`.

## 5. Safe Diagnostics

Never print real VK tokens, callback secrets, confirmation codes, database passwords, JWT secrets, Telegram tokens, or YooKassa keys.

Useful checks:

```bash
curl -fsS http://127.0.0.1:8082/api/health
docker compose -p shamrai logs --tail=200 backend
```

`/api/health/vk` and `/api/health/vk/deep` require staff access unless
`DEBUG_MODE=true`. For deploy/repair automation, prefer an internal backend probe
that imports `settings` and `src.services.vk_delivery` inside the container without
printing secret values.

Safe callback shape checks should use placeholders, not real secrets:

```bash
curl -i -X POST http://127.0.0.1:8082/api/vk/callback \
  -H 'Content-Type: application/json' \
  -d '{"type":"confirmation","group_id":239419819}'
```

The response body must be the configured confirmation string, not JSON. Do not paste the real value into docs or chat.

If VK dashboard shows "Сервер вернул неправильный ответ" and the public callback returns a different masked confirmation body than VK currently expects, repair runtime env without storing the code:

```powershell
$env:SHAMRAI_SSH_PASSWORD = '<provide at runtime, do not store>'
$env:SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE = '<current VK dashboard confirmation string>'
powershell -ExecutionPolicy Bypass -File .\scripts\repair-vk-callback-code.ps1
Remove-Item Env:\SHAMRAI_EXPECTED_VK_CALLBACK_CONFIRMATION_CODE
```

Then press "Подтвердить" in the VK dashboard again.

If client messages hit `/api/vk/callback` but backend logs show `403 Forbidden`, the VK dashboard secret and runtime `VK_CALLBACK_SECRET` are out of sync. Repair without storing the secret locally:

```powershell
$env:SHAMRAI_SSH_PASSWORD = '<provide at runtime, do not store>'
$env:SHAMRAI_EXPECTED_VK_CALLBACK_SECRET = '<current VK dashboard secret key>'
powershell -ExecutionPolicy Bypass -File .\scripts\repair-vk-callback-secret.ps1
Remove-Item Env:\SHAMRAI_EXPECTED_VK_CALLBACK_SECRET
```
